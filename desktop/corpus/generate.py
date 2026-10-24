"""
Build the golden corpus: what the Python implementation answers today, for the
Rust port to be held to. See docs/RUST_DESKTOP_PLAN.md, Phase 0 and section 4.

    cd backend && python ../desktop/corpus/generate.py

Writes desktop/corpus/data/*.json and desktop/corpus/manifest.json. Run it with
the backend's own pinned dependencies (backend/requirements-dev.txt) and commit
what it writes. Regenerate only when the Python behaviour changes on purpose;
the diff of the corpus is then the record of what changed.

Two kinds of case, kept apart because they are compared differently:

  units/   one service function, one input, one output. Deterministic, so the
           Rust port is held to these to floating-point tolerance. These are the
           Phase 1 targets: f14c, cell edges, curve interpolation (linear and
           cubic), likelihoods, calibrated posteriors, HPD ranges, point
           estimates, curve mixing, expected resolution, same-event tests,
           reservoir combination, date formatting, NumPy's seeded generator,
           the KDE summary, the curve browser's windows and comparisons, and
           the measured half of the curve registry. Then the Phase 2 targets:
           model trees planned and validated, contexts and relationships
           migrated, durations and differences from joint draws, the exact
           chain and parallel solvers piece by piece and whole, agreement
           indices and wiggle matches. A solve's cases are smaller than an
           API run (fewer samples and joint draws); the arithmetic is the
           same.

  api/     a whole request through the HTTP API, as the frontend sends it, and
           the JSON that came back. Every .oxcal fixture in backend/tests is
           imported and run through both solvers. These are the targets for
           the solvers (Phases 2-3) and the contract for the Rust server
           (Phase 5). Sampled outputs are compared statistically, not bit for
           bit -- see the plan, section 4.3.

Format. Every file is {"schema": 2, "family": ..., "cases": [{"name", "inputs",
"outputs"}]}. Floats are written with Python's repr, which round-trips exactly.
JSON has no NaN or infinity, so a non-finite float is written as the string
"NaN", "Infinity" or "-Infinity". A posterior over a whole curve is mostly
zeros; it is stored as the window where it carries mass (see _window), with the
mass left outside so a comparison can check that too.

Two encodings keep the API cases small. Both are lossless, and both are checked
as they are written; radical-corpus expands them back to the JSON the API sent.

  {"$series": {"x": X, "y": [...]}}
      stands for a list of [x, y] pairs -- every distribution the API returns.
      X is the list of x values, or {"linspace": [start, stop, n]} when they
      are exactly numpy.linspace(start, stop, n): x_i = i * ((stop - start) /
      (n - 1)) + start, with the last forced to stop.

  {"$from_analysis": {"ids": [[key, sample_id], ...], "fields": [...]}}
      stands for a saved run's `results` in the POST response, an object
      keyed by the request's date ids: under each key, the named fields of
      the entry in the same case's GET analysis `results` with that
      sample_id. The two are the same numbers, so only the GET copy is kept.

Arrays of plain numbers or strings are written on one line, everything else
indented, so a diff still lands on the series that changed.

Volatile fields -- ids, timestamps, timings -- are replaced or dropped so that
two runs of this script on one commit write identical files.
"""
from __future__ import annotations

import base64
import datetime
from collections import Counter
import hashlib
import io
import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
import zipfile
from typing import Any, Callable, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
BACKEND = os.path.join(ROOT, 'backend')
CURVES_DIR = os.path.join(ROOT, 'calibration_data')
FIXTURES = os.path.join(BACKEND, 'tests', 'fixtures')
OUT = os.path.join(HERE, 'data')

SCHEMA = 2
CURVE_FILES = {'intcal20': 'intcal20.14c', 'marine20': 'marine20.14c', 'shcal20': 'shcal20.14c'}
SEED = 42

# The environment has to be in place before anything under app/ is imported:
# app/database.py builds its engine from DATABASE_URL at import time.
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix='.db', prefix='radical_corpus_')
os.close(_DB_FD)
os.environ['DATABASE_URL'] = f'sqlite:///{_DB_PATH}'
os.environ.setdefault('RADICAL_SECRET_KEY', 'corpus-generation-only-' + 'x' * 32)
for _curve_id, _filename in CURVE_FILES.items():
    os.environ[f'{_curve_id[:-2].upper()}_CURVE_PATH'] = os.path.join(CURVES_DIR, _filename)
sys.path.insert(0, BACKEND)

import numpy as np  # noqa: E402
import scipy  # noqa: E402


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def plain(value: Any) -> Any:
    """`value` as JSON-ready Python: numpy unwrapped, non-finite floats named."""
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, np.ndarray):
        return [plain(v) for v in value.tolist()]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        value = float(value)
        if math.isnan(value):
            return 'NaN'
        if math.isinf(value):
            return 'Infinity' if value > 0 else '-Infinity'
        return value
    if hasattr(value, 'as_dict'):
        return plain(value.as_dict())
    return value


# Probability below this fraction of the peak is left out of a stored window.
WINDOW_FLOOR = 1e-12


def _window(cal_ages, probs) -> Dict[str, Any]:
    """The stretch of a curve-length distribution that carries its mass."""
    cal_ages = np.asarray(cal_ages, dtype=float)
    probs = np.asarray(probs, dtype=float)
    keep = np.nonzero(probs >= probs.max() * WINDOW_FLOOR)[0]
    lo, hi = int(keep[0]), int(keep[-1]) + 1
    return {
        'length': len(probs),
        'index_from': lo,
        'cal_ages': cal_ages[lo:hi],
        'values': probs[lo:hi],
        'mass_outside': float(probs[:lo].sum() + probs[hi:].sum()),
        'floor': WINDOW_FLOOR,
    }


def _is_pairs(value: Any) -> bool:
    return (isinstance(value, list) and len(value) > 3
            and all(isinstance(p, list) and len(p) == 2
                    and all(isinstance(q, (int, float)) and not isinstance(q, bool) for q in p)
                    for p in value))


def _linspace(xs: List[float]):
    """[start, stop, n] if xs is exactly numpy.linspace(start, stop, n), else None."""
    n = len(xs)
    rebuilt = np.linspace(xs[0], xs[-1], n)
    if all(float(a) == b and type(b) is float for a, b in zip(rebuilt, xs)):
        return [xs[0], xs[-1], n]
    return None


def encode_series(value: Any) -> Any:
    """Every list of [x, y] pairs as a $series (see the module docstring)."""
    if isinstance(value, dict):
        return {k: encode_series(v) for k, v in value.items()}
    if _is_pairs(value):
        xs = [p[0] for p in value]
        grid = _linspace(xs)
        return {'$series': {'x': {'linspace': grid} if grid else xs, 'y': [p[1] for p in value]}}
    if isinstance(value, list):
        return [encode_series(v) for v in value]
    return value


def decode_series(value: Any) -> Any:
    """encode_series undone, as radical-corpus does it; used to prove the round trip."""
    if isinstance(value, dict):
        if set(value) == {'$series'}:
            x, y = value['$series']['x'], value['$series']['y']
            if isinstance(x, dict):
                start, stop, n = x['linspace']
                x = [float(v) for v in np.linspace(start, stop, n)]
            return [[a, b] for a, b in zip(x, y)]
        return {k: decode_series(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_series(v) for v in value]
    return value


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def dumps(value: Any, depth: int = 0) -> str:
    """JSON with arrays of scalars on one line and everything else indented."""
    pad, inner = ' ' * depth, ' ' * (depth + 1)
    if isinstance(value, dict):
        if not value:
            return '{}'
        items = [f'{inner}{json.dumps(k, ensure_ascii=False)}: {dumps(v, depth + 1)}'
                 for k, v in value.items()]
        return '{\n' + ',\n'.join(items) + '\n' + pad + '}'
    if isinstance(value, list):
        if all(_scalar(v) for v in value):
            return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(',', ':'))
        return '[\n' + ',\n'.join(inner + dumps(v, depth + 1) for v in value) + '\n' + pad + ']'
    return json.dumps(value, allow_nan=False, ensure_ascii=False)


def write(family: str, cases: List[Dict[str, Any]]) -> str:
    path = os.path.join(OUT, f'{family}.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cases = plain(cases)
    body = {'schema': SCHEMA, 'family': family, 'cases': encode_series(cases)}
    text = dumps(body)
    # Neither the encoding nor the layout may change a single value or type.
    written = json.loads(text)
    assert written == body
    assert json.dumps(decode_series(written['cases'])) == json.dumps(cases)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text + '\n')
    print(f'  {family}: {len(cases)} cases')
    return path


def capture(name: str, inputs: Dict[str, Any], fn: Callable[[], Any]) -> Dict[str, Any]:
    """One case. A refusal is an answer too, so an exception is recorded, not raised."""
    import csv as _csv
    from app.services.reservoir_ingest import IngestError
    try:
        return {'name': name, 'inputs': inputs, 'outputs': fn()}
    except (ValueError, _csv.Error, IngestError) as exc:
        # csv.Error too: Python's csv module refuses some inputs outright, and
        # the importer does not catch it -- what the port must match. And the
        # reservoir ingest's own refusals, which are its answers.
        return {'name': name, 'inputs': inputs, 'error': {'type': type(exc).__name__, 'message': str(exc)}}


# ---------------------------------------------------------------------------
# units/ -- Phase 1
# ---------------------------------------------------------------------------

# Chosen to cross the places a port goes wrong: near-modern, a plateau, a
# steep stretch, deep time where the curve steps 20 years, and past the end.
AGES = [95, 250, 1000, 2450, 3000, 8000, 20000, 45000, 54500, 60000]
ERRORS = [15, 40, 120]
RESERVOIR = [(None, None), (-50.0, 30.0), (120.0, 60.0)]


def units_f14c():
    from app.services.calibration import f14c
    ages = [-500.0, 0.0, 1.0, 95.0, 2450.0, 8033.0, 45000.0, 60000.0]
    return [capture('f14c', {'age_bp': ages}, lambda: {'f14c': f14c(ages)})]


def units_linspace():
    """numpy.linspace, which the $series encoding and much of the port lean on."""
    specs = [(0.0, 1.0, 11), (503.9488636363636, 718.0, 472), (-1.5, 1.5, 7),
             (2216.0, 2751.0, 536), (55000.0, 0.0, 5501), (0.1, 0.7, 4)]
    return [capture(f'linspace/{a}/{b}/{n}', {'start': a, 'stop': b, 'n': n},
                    lambda a=a, b=b, n=n: {'values': np.linspace(a, b, n)}) for a, b, n in specs]


def units_cell_edges():
    from app.services.calibration import cell_edges, get_curve
    cases = [capture(f'cell_edges/{n}', {'ages': a}, lambda a=a: {'edges': cell_edges(a)})
             for n, a in [('empty', []), ('one', [5.0]), ('two', [0.0, 10.0]),
                          ('uneven', [0.0, 1.0, 5.0, 20.0, 40.0])]]
    for curve_id in CURVE_FILES:
        curve = get_curve(curve_id)
        cases.append(capture(f'cell_edges/{curve_id}', {'curve': curve_id},
                             lambda c=curve: {'edges': cell_edges(c.cal_ages)}))
    return cases


def units_interpolation():
    """Off-grid reads of each curve. The cubic one is SciPy's not-a-knot spline."""
    from app.services.calibration import get_curve
    cases = []
    for curve_id in CURVE_FILES:
        curve = get_curve(curve_id)
        lo, hi = float(curve.cal_ages[0]), float(curve.cal_ages[-1])
        at = sorted({lo - 10, lo, lo + 0.25, 0.5, 13.7, 250.5, 2451.3, 2999.9,
                     12345.6, 30000.5, 49999.5, hi - 0.5, hi, hi + 100})
        for method in ('interpolate', 'interpolate_cubic'):
            def run(curve=curve, method=method, at=at):
                c14, err = getattr(curve, method)(np.array(at))
                return {'c14_age': c14, 'c14_error': err}
            cases.append(capture(f'{method}/{curve_id}', {'curve': curve_id, 'cal_age': at}, run))
    return cases


def units_likelihood():
    """On a half-year grid, so the cubic interpolation and the OxCal cut-off both show."""
    from app.services.calibration import get_curve, matching_oxcal
    cases = []
    for curve_id in CURVE_FILES:
        curve = get_curve(curve_id)
        for age, err in [(250, 25), (2450, 40), (8000, 30), (45000, 400)]:
            for dr, dre in RESERVOIR if curve_id == 'marine20' else RESERVOIR[:1]:
                # A 300-year window around where the curve passes through the age.
                idx = int(np.argmin(np.abs(curve.c14_ages - age)))
                mid = float(curve.cal_ages[idx])
                grid = np.arange(max(curve.cal_ages[0], mid - 150), mid + 150, 0.5)
                for match in (False, True):
                    def run(curve=curve, age=age, err=err, dr=dr, dre=dre, grid=grid, match=match):
                        with matching_oxcal(match):
                            return {'likelihood': curve.likelihood(grid, age, err, dr, dre)}
                    inputs = {'curve': curve_id, 'age_bp': age, 'error_1sigma': err,
                              'delta_r': dr, 'delta_r_error': dre, 'match_oxcal': match,
                              'cal_ages': grid}
                    cases.append(capture(
                        f'likelihood/{curve_id}/{age}+-{err}/dr={dr}/match={match}', inputs, run))
    return cases


def units_calibrate():
    """Posterior, HPD ranges at each reporting level, point estimate, display series."""
    from app.services.calibration import REPORTING_CONFIDENCE_LEVELS, get_curve
    cases = []
    for curve_id in CURVE_FILES:
        curve = get_curve(curve_id)
        for age in AGES:
            for err in ERRORS:
                for dr, dre in RESERVOIR if curve_id == 'marine20' else RESERVOIR[:1]:
                    def run(curve=curve, age=age, err=err, dr=dr, dre=dre):
                        cal, probs = curve.calibrate(age, err, dr, dre)
                        display = curve.get_display_distribution(cal, probs)
                        return {
                            'posterior': _window(cal, probs),
                            'hpd': {f'{level:.10f}': curve.get_hpd_ranges(cal, probs, level)
                                    for level in REPORTING_CONFIDENCE_LEVELS},
                            'point_estimate': curve.get_point_estimate(cal, probs),
                            'display': display,
                        }
                    inputs = {'curve': curve_id, 'age_bp': age, 'error_1sigma': err,
                              'delta_r': dr, 'delta_r_error': dre}
                    cases.append(capture(f'calibrate/{curve_id}/{age}+-{err}/dr={dr}', inputs, run))
    return cases


# A mixed curve has ~10,000 points; every MIX_STEP-th is kept, from index 0.
MIX_STEP = 25


def units_curve_mix():
    from app.services.calibration import get_curve
    from app.services.curve_mix import mix
    cases = []
    for a, b in [('intcal20', 'marine20'), ('shcal20', 'marine20'), ('intcal20', 'shcal20')]:
        for pct, pct_err in [(0, 0), (30, 10), (50, 0), (85, 5), (100, 0)]:
            def run(a=a, b=b, pct=pct, pct_err=pct_err):
                m = mix(get_curve(a), get_curve(b), pct, pct_err)
                return {'length': len(m.cal_ages), 'step': MIX_STEP,
                        'cal_ages': m.cal_ages[::MIX_STEP], 'c14_ages': m.c14_ages[::MIX_STEP],
                        'c14_errors': m.c14_errors[::MIX_STEP]}
            cases.append(capture(f'mix/{a}+{b}/{pct}+-{pct_err}',
                                 {'curve_a': a, 'curve_b': b, 'percent_a': pct,
                                  'percent_a_error': pct_err}, run))
    cases.append(capture('mix/out-of-range', {'curve_a': 'intcal20', 'curve_b': 'marine20',
                                              'percent_a': 120, 'percent_a_error': 0},
                         lambda: mix(get_curve('intcal20'), get_curve('marine20'), 120, 0)))
    return cases


def units_resolution():
    from app.services import resolution
    from app.services.calibration import get_curve
    cases = [capture('uncertainty_at', {'c14_age': [0, 1000, 5000, 20000], 'u0': 25.0},
                     lambda: {'uncertainty': [resolution.uncertainty_at(a, 25.0)
                                              for a in (0, 1000, 5000, 20000)]})]
    for curve_id, lo, hi in [('intcal20', 2300, 2800), ('shcal20', 0, 600), ('marine20', 5000, 5400)]:
        for u0 in (15.0, 25.0):
            cases.append(capture(
                f'profile/{curve_id}/{lo}-{hi}/u0={u0}',
                {'curve': curve_id, 'cal_from': lo, 'cal_to': hi, 'u0': u0, 'stride': 5},
                lambda c=curve_id, lo=lo, hi=hi, u0=u0:
                    resolution.profile(get_curve(c), lo, hi, u0=u0, stride=5).as_dict()))
    return cases


def units_same_event():
    from app.services import statistics
    def m(age, err, curve='intcal20', dr=None, dre=None):
        return {'age_bp': age, 'error_1sigma': err, 'curve_id': curve,
                'delta_r': dr, 'delta_r_error': dre}
    sets = {
        'consistent': [m(3000, 25), m(3020, 30), m(2990, 20)],
        'inconsistent': [m(3000, 25), m(3400, 30)],
        'one': [m(3000, 25)],
        'two-curves': [m(3000, 25), m(3000, 25, 'shcal20')],
        'marine-same-offset': [m(4000, 30, 'marine20', 50, 20), m(4050, 35, 'marine20', 50, 20)],
    }
    return [capture(f'same_event/{k}', {'measurements': v},
                    lambda v=v: statistics.same_event(v)) for k, v in sets.items()]


def units_reservoir():
    from app.services import reservoir
    pairs = [(0, 0, 0, 0), (-45.87, 170.5, -36.85, 174.76), (51.5, -0.12, 40.71, -74.0),
             (89.9, 10, -89.9, -170), (0, 179.9, 0, -179.9)]
    cases = [capture('haversine_km', {'pairs': pairs},
                     lambda: {'km': [reservoir.haversine_km(*p) for p in pairs]})]
    point_sets = {
        'weighted': [(1, 10.0, 20.0), (2, -30.0, 40.0), (3, 55.0, 25.0)],
        'single': [(1, 120.0, 50.0)],
        'missing-error': [(1, 10.0, 20.0), (2, 40.0, None)],
        'scatter-wins': [(1, -200.0, 10.0), (2, 200.0, 10.0)],
    }
    for name, rows in point_sets.items():
        points = [reservoir.ReservoirPoint(map_no=n, latitude=0.0, longitude=0.0,
                                           delta_r=dr, delta_r_error=e) for n, dr, e in rows]
        cases.append(capture(f'combine/{name}', {'points': [list(r) for r in rows]},
                             lambda p=points: reservoir.combine(p).as_dict()))

    # A made-up coast: (map_no, lat, lon, delta_r, error, habit, raw habit).
    coast = [
        (101, -41.10, 174.00, 30.0, 40.0, 'suspension', 'filter'),
        (102, -41.20, 174.10, -20.0, 35.0, 'suspension', 'suspension'),
        (103, -41.05, 173.90, 250.0, 60.0, 'deposit', 'deposit'),
        (104, -41.30, 174.30, 80.0, None, 'grazer', 'herbivore'),
        (105, -40.90, 173.80, 15.0, 25.0, 'carnivore', 'predator'),
        (106, -41.60, 174.70, 60.0, 45.0, 'unknown', None),
        (107, -41.15, 174.05, None, None, 'suspension', 'filter'),
        (108, -42.50, 175.50, -5.0, 30.0, 'suspension', 'filter'),
        (109, -41.10, 174.00, 45.0, 50.0, 'migratory', 'migratory'),
    ]
    points = [reservoir.ReservoirPoint(map_no=m, latitude=la, longitude=lo, delta_r=dr,
                                       delta_r_error=e, feeding=f, feeding_raw=raw,
                                       locality=f'Point {m}', reference='Made up')
              for m, la, lo, dr, e, f, raw in coast]
    cases.append(capture('nearest', {'points': coast, 'at': [-41.12, 174.02], 'n': 4},
                         lambda: [[p.map_no, p.distance_km]
                                  for p in reservoir.nearest(-41.12, 174.02, points, 4)]))
    for name, opts in {
        'default': {'n': 6},
        'all': {'n': 50},
        'within': {'n': 10, 'max_km': 20.0},
        'nothing-within': {'n': 10, 'max_km': 0.5},
        'excluded': {'n': 6, 'exclude_map_nos': [101, 103]},
        'suspension-only': {'n': 9, 'suspension_only': True},
        'suspension-only-refused': {'n': 2, 'suspension_only': True},
        'too-few-weighted': {'n': 10, 'exclude_map_nos': [101, 102, 103, 105, 106, 108, 109]},
    }.items():
        def run(opts=opts):
            try:
                result = reservoir.lookup(-41.12, 174.02, points, snapshot_id='snap-1', **opts)
            except reservoir.LookupRefused as exc:
                return {'refused': str(exc)}
            source = reservoir.provenance(result, max_km=opts.get('max_km'),
                                          suspension_only=opts.get('suspension_only', False))
            source.pop('computed_at')
            return {'lookup': result.as_dict(), 'provenance': source}
        cases.append(capture(f'lookup/{name}', {'points': coast, 'at': [-41.12, 174.02],
                                                'options': opts}, run))
    return cases


def units_date_format():
    from app.services import date_format
    ages = [-60.0, -0.4, 0.0, 0.5, 1949.0, 1950.0, 1950.6, 3000.0, 12345.0]
    ranges = [{'min': 3100.0, 'max': 3270.0, 'probability_percent': 81.9},
              {'min': 1900.0, 'max': 2000.0, 'probability_percent': 13.5}]
    cases = []
    # The three valid ids, then spellings that are not ids and must fall back
    # to cal BP.
    for fmt in ('calBP', 'bcad', 'bce_ce', 'cal_bp', 'bc_ad', None, 'nonsense'):
        def run(fmt=fmt):
            norm = date_format.normalise(fmt)
            return {
                'normalised': norm,
                'ages': [date_format.format_age(a, norm) for a in ages],
                'range': date_format.format_range(3270.0, 3100.0, norm),
                'ranges': date_format.format_hpd_ranges(ranges, norm),
            }
        cases.append(capture(f'date_format/{fmt}', {'format': fmt, 'ages': ages, 'ranges': ranges}, run))
    return cases


# ---------------------------------------------------------------------------
# units/ -- the rest of Phase 1: the random generator, KDE, curve view and the
# measurable half of the curve registry
# ---------------------------------------------------------------------------

def _rng_state(rng) -> Dict[str, str]:
    """PCG64's 128-bit state and increment, as decimal strings (JSON numbers
    stop being exact at 2^53)."""
    state = rng.bit_generator.state['state']
    return {'state': str(state['state']), 'inc': str(state['inc'])}


def units_rng():
    """
    numpy.random.default_rng: SeedSequence seeding PCG64, and the draws the
    services take from it. Seeded draws are part of what a run reproduces, so
    the port reproduces the generator, not merely its distribution.
    """
    cases = []
    for seed in [0, 1, 42, 2**31 - 1, 2**32 + 5, 2**64 + 7, [42, 3], [0, 0], [7, 2**33]]:
        def run(seed=seed):
            rng = np.random.default_rng(seed)
            state = _rng_state(rng)
            return {'state': state, 'random': rng.random(8)}
        # As decimal strings: a JSON reader need not hold integers past 2^53.
        written = [str(v) for v in seed] if isinstance(seed, list) else str(seed)
        cases.append(capture(f'random/{seed}', {'seed': written}, run))

    def uniforms():
        rng = np.random.default_rng(42)
        scalars = [float(rng.uniform(a, b)) for a, b in
                   [(0.0, 1.0), (-0.5, 0.5), (100.0, 250.0), (3.0, 3.0)]]
        return {'scalars': scalars, 'array': rng.uniform(-0.5, 0.5, 6), 'random_after': rng.random(2)}
    cases.append(capture('uniform', {'seed': 42, 'scalars': [[0.0, 1.0], [-0.5, 0.5], [100.0, 250.0],
                                                             [3.0, 3.0]],
                                     'array': [-0.5, 0.5, 6]}, uniforms))

    # Draws interleaved as the sampler takes them: integers come from PCG64's
    # buffered 32-bit output, which a double draw between two of them must
    # neither use nor clear.
    script = [['integers', 0, 5], ['random'], ['integers', -3, 4], ['integers', 0, 12],
              ['normal', 0.0, 2.5], ['integers', 0, 1], ['integers', 0, 2**40],
              ['integers', 0, 2**32], ['integers', -2**62, 2**62], ['random'],
              ['integers', 0, 7], ['integers', 0, 7], ['normal', 3.0, 60.0],
              ['integers', 0, 3], ['random'], ['integers', 0, 1000003]] * 4
    def interleaved():
        rng = np.random.default_rng(17)
        out = []
        for op in script:
            if op[0] == 'random':
                out.append(float(rng.random()))
            elif op[0] == 'integers':
                out.append(str(int(rng.integers(op[1], op[2]))))
            else:
                out.append(float(rng.normal(op[1], op[2])))
        return {'values': out}
    # Integers as decimal strings, for the same reason as the seeds.
    cases.append(capture('interleaved', {'seed': 17, 'script': [
        [op[0]] + [str(v) if isinstance(v, int) else v for v in op[1:]] for op in script]},
        interleaved))

    def normals():
        rng = np.random.default_rng(23)
        draws = rng.standard_normal(200_000)
        # The ziggurat's tail (layer 0, about one draw in 3,800) is reached
        # some fifty times in this many; the hash holds every draw to the bit.
        return {'first': draws[:20], 'sha256_le': hashlib.sha256(draws.astype('<f8').tobytes()).hexdigest(),
                'tail_draws': int((np.abs(draws) > 3.6541528853610088).sum())}
    cases.append(capture('standard_normal', {'seed': 23, 'count': 200_000}, normals))

    p = [0.1, 0.0, 0.25, 0.4, 0.25]
    def choices():
        rng = np.random.default_rng(9)
        return {'choices': [int(rng.choice(len(p), p=p)) for _ in range(12)]}
    cases.append(capture('choice', {'seed': 9, 'p': p, 'count': 12}, choices))
    return cases


# Fixed sets of event ages for the deterministic KDE functions: written out
# rather than drawn, so the inputs do not depend on the generator under test.
KDE_SETS = {
    'five': [3010.0, 2987.5, 3044.25, 2950.0, 3001.0],
    'bimodal': [2480.0, 2510.5, 2495.0, 2530.0, 2470.25, 2502.0,
                3490.0, 3520.0, 3505.5, 3475.0, 3512.0, 3499.0],
    'two': [1200.0, 1260.0],
    'isolated': [500.0 + 3.0 * i for i in range(20)] + [9000.0],
    'ties': [800.0, 800.0, 800.0, 810.0, 790.0, 800.0],
}


def units_kde():
    from app.services import kde
    from app.services.calibration import get_curve
    from app.services.intervals import draws_from_distribution
    cases = []
    for name, values in KDE_SETS.items():
        t = np.array(values)
        h_s = kde.silverman_bandwidth(t)
        cases.append(capture(f'silverman/{name}', {'t': values}, lambda h_s=h_s: {'h_s': h_s}))
        gs = [0.03, 0.1, 0.25, 0.5, 0.9, 1.0]
        cases.append(capture(f'log_likelihood/{name}', {'t': values, 'h_s': h_s, 'g': gs},
                             lambda t=t, h_s=h_s: {'log_likelihood': [kde.log_likelihood(t, h_s, g) for g in gs]}))
        def grid(t=t, h_s=h_s):
            nodes, bounds, weight = kde._g_grid(t, h_s)
            return {'nodes': nodes, 'bounds': bounds, 'weight': weight}
        cases.append(capture(f'g_grid/{name}', {'t': values, 'h_s': h_s}, grid))
        def draw(t=t, h_s=h_s):
            rng = np.random.default_rng(5)
            return {'g': [kde.draw_g(t, h_s, rng) for _ in range(4)]}
        cases.append(capture(f'draw_g/{name}', {'t': values, 'h_s': h_s, 'seed': 5, 'count': 4}, draw))

    t = np.array(KDE_SETS['bimodal'])
    for label, h, bounds in [('plain', 40.0, None), ('narrow', 6.5, None),
                             ('reflected', 40.0, [2460.0, 3530.0]), ('reflected-tight', 15.0, [2475.0, 3510.0])]:
        ages = np.arange(2300.0, 3700.0, 5.0)
        def density(h=h, bounds=bounds, ages=ages):
            return {'density': kde.kde_density(t, h, ages, None if bounds is None else tuple(bounds))}
        cases.append(capture(f'kde_density/{label}', {'t': KDE_SETS['bimodal'], 'h': h, 'bounds': bounds,
                                                      'ages': [2300.0, 3700.0, 5.0]}, density))

    draws = np.array([KDE_SETS['five'], [v + 20.0 for v in KDE_SETS['five']]]).T
    for bandwidths in ([12.0, 30.5], [0.4, 0.9], [300.0, 2.0]):
        cases.append(capture(f'output_grid/{bandwidths}', {'draws': draws, 'bandwidths': bandwidths},
                             lambda b=bandwidths: {'ages': kde._output_grid(draws, np.array(b))}))

    for values in ([1.0, 2.0, 3.0, 4.0], [5.5], [9.0, 1.0, 4.0, 7.5, 2.25, 3.0, 8.0]):
        cases.append(capture(f'summary/{values}', {'values': values},
                             lambda v=values: kde._summary(np.array(v))))

    curve = get_curve('intcal20')
    ages, masses = curve.calibrate(3000, 30)
    cases.append(capture('draws_from_distribution', {'curve': 'intcal20', 'age_bp': 3000, 'error_1sigma': 30,
                                                     'seed': 3, 'count': 20},
                         lambda: {'draws': draws_from_distribution(ages, masses, np.random.default_rng(3), 20)}))

    for label, dates in [('clustered', [(2950, 25), (3000, 30), (3020, 25), (2990, 40), (3060, 30)]),
                         ('spread', [(2450, 30), (2600, 25), (3100, 35), (3150, 30)]),
                         ('one', [(3000, 30)])]:
        def run(dates=dates):
            calibrated = [get_curve('intcal20').calibrate(a, e) for a, e in dates]
            return kde.summarise(calibrated, draws=60, seed=42)
        cases.append(capture(f'summarise/{label}', {'curve': 'intcal20', 'dates': dates, 'draws': 60, 'seed': 42}, run))

    # Joint states written out, as a solver would hand them over.
    states = np.random.default_rng(11).normal(3000.0, 40.0, (4, 90)).round(3)
    older = (states.max(axis=0) + 15.0).round(3)
    younger = (states.min(axis=0) - 12.0).round(3)
    for label, bounds in [('plain', None), ('reflected', [older, younger])]:
        cases.append(capture(f'summarise_draws/{label}',
                             {'sampled': states, 'bounds': bounds, 'draws': 50, 'seed': 42},
                             lambda b=bounds: kde.summarise_draws(states, None if b is None else tuple(b),
                                                                  draws=50, seed=42)))
    return cases


def _small_curve(cal, c14, err):
    from app.services.calibration import CalibrationCurve
    return CalibrationCurve(cal, c14, err)


def units_curve_view():
    from app.services import curve_view
    from app.services.calibration import get_curve
    cases = []
    windows = [('intcal20', None, None, 800), ('intcal20', 2300, 2800, 800), ('intcal20', 2300, 2800, 100),
               ('intcal20', 2450.2, 2450.6, 800), ('intcal20', 2800, 2300, 50), ('intcal20', 60000, 70000, 800),
               ('intcal20', 12000, 30000, 1), ('shcal20', 0, 600, 9999), ('marine20', 5000, 5400, 20),
               ('marine20', 5003, 5007, 800)]
    for curve_id, lo, hi, cap in windows:
        def run(curve_id=curve_id, lo=lo, hi=hi, cap=cap):
            w = curve_view.window(get_curve(curve_id), lo, hi, cap)
            return {'window': w, 'csv': curve_view.to_csv(w, get_curve(curve_id).curve_name)}
        cases.append(capture(f'window/{curve_id}/{lo}-{hi}/{cap}',
                             {'curve': curve_id, 'cal_from': lo, 'cal_to': hi, 'max_points': cap}, run))
    for a, b, lo, hi, cap in [('intcal20', 'shcal20', 2000, 3000, 200), ('intcal20', 'marine20', None, None, 800),
                              ('shcal20', 'intcal20', 100.2, 100.4, 800), ('intcal20', 'marine20', 4000, 3000, 10)]:
        cases.append(capture(f'compare/{a}-{b}/{lo}-{hi}/{cap}',
                             {'curve_a': a, 'curve_b': b, 'cal_from': lo, 'cal_to': hi, 'max_points': cap},
                             lambda a=a, b=b, lo=lo, hi=hi, cap=cap:
                                 curve_view.compare(get_curve(a), get_curve(b), lo, hi, cap)))
    left = ([0.0, 10.0, 20.0], [100.0, 110.0, 125.0], [5.0, 5.0, 6.0])
    right = ([30.0, 40.0, 50.0], [140.0, 150.0, 170.0], [5.0, 6.0, 7.0])
    gappy = ([0.0, 100.0], [50.0, 160.0], [4.0, 4.5])
    for label, a, b in [('no-overlap', left, right), ('sparse-grid', gappy, gappy)]:
        cases.append(capture(f'compare/{label}', {'a': a, 'b': b, 'cal_from': 40.2, 'cal_to': 40.7, 'max_points': 800},
                             lambda a=a, b=b: curve_view.compare(_small_curve(*a), _small_curve(*b), 40.2, 40.7, 800)))
    return cases


def units_curve_registry():
    from app.services import curve_registry as reg
    from app.services.calibration import get_curve
    from app.services.curve_mix import mix
    names = ['IntCal20.14c', 'intcal-20', 'IntCal 20', ' =SHCal20 ', 'Marine20.CSV', 'my curve.txt',
             '', '   ', 'IntCal13', 'mix.14c.bak', 'Marine', 'SH-Cal', 'intcal20.14C', '=']
    cases = [
        capture('normalise', {'names': names}, lambda: {'normalised': [reg._normalise(n) for n in names]}),
        capture('resolve_alias/builtin', {'names': names},
                lambda: {'ids': [reg._builtin_index().get(reg._normalise(n)) if reg._normalise(n) else None
                                 for n in names]}),
    ]
    grids = {
        'empty': [], 'one': [5.0], 'two': [0.0, 1.0], 'uniform': [0.0, 5.0, 10.0, 15.0],
        'halves': [0.0, 0.5, 1.0, 1.5, 3.5, 5.5],
        'two-runs-thousands': [0.0, 5000.0, 10000.0, 10020.0, 10040.0, 10060.0],
        'nearly-equal': [0.0, 1.0, 2.0000000001, 3.0, 4.5],
        'irregular': [0.0, 1.0, 3.0, 6.0, 10.0, 15.0, 21.0, 28.0],
    }
    for name, grid in grids.items():
        cases.append(capture(f'step_summary/{name}', {'cal_ages': grid},
                             lambda g=grid: {'summary': reg._step_summary(np.array(g, dtype=float))}))
    for curve_id in CURVE_FILES:
        cases.append(capture(f'spec/{curve_id}', {'curve': curve_id},
                             lambda c=curve_id: reg.get_spec(c).as_dict()))
    mixed = mix(get_curve('intcal20'), get_curve('marine20'), 30.0, 10.0)
    cases.append(capture('content_hash/mixed', {'curve_a': 'intcal20', 'curve_b': 'marine20',
                                                'percent_a': 30.0, 'percent_a_error': 10.0},
                         lambda: {'sha256': reg.content_hash(mixed)}))
    odd = ([0.1234567, 1e-7, 123456789.0, 2.5, -0.0, 1e21],
           [0.5, 99999.95, 1234567.0, 0.000123456, 100000.0, 7.0],
           [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    cases.append(capture('content_hash/odd-numbers', {'cal_ages': odd[0], 'c14_ages': odd[1], 'c14_errors': odd[2]},
                         lambda: {'sha256': reg.content_hash(_small_curve(*odd))}))
    return cases

# ---------------------------------------------------------------------------
# units/ -- Phase 2: the model tree and the exact solvers
# ---------------------------------------------------------------------------

def _b(eid, name=None, **kw):
    return {'id': eid, 'element_type': 'boundary', 'name': name, 'children': [], **kw}


def _p(eid, name=None, children=(), **kw):
    return {'id': eid, 'element_type': 'phase', 'name': name, 'children': list(children), **kw}


def _s(eid, name, children, **kw):
    return {'id': eid, 'element_type': 'sequence', 'name': name, 'children': list(children), **kw}


def _h(eid, name='Hiatus'):
    return {'id': eid, 'element_type': 'hiatus', 'name': name, 'children': []}


def _d(eid, element, age, err=25, **kw):
    return {'id': eid, 'sample_name': eid.upper(), 'age_bp': float(age), 'error_1sigma': float(err),
            'curve_id': 'intcal20', 'element_id': element, **kw}


def _hand_models():
    """
    Small models written out by hand, each crossing one thing the planner, the
    layouts or the solvers treat specially. Ages are IntCal20 ones a few
    decades apart, so the same models serve the solver cases.
    """
    m = {}
    m['chain'] = (_s('r', 'Site', [_b('b0', 'Start'), _p('p1', 'Lower'), _b('b1', 'Transition'),
                                   _p('p2', 'Upper'), _b('b2', 'End')]),
                  [_d('a1', 'p1', 3050), _d('a2', 'p1', 3010, 30), _d('a3', 'p1', 2990),
                   _d('a4', 'p2', 2900), _d('a5', 'p2', 2880, 35)])
    m['one-phase'] = (_s('r', 'Site', [_b('b0'), _p('p1', 'Only'), _b('b1')]),
                      [_d('a1', 'p1', 2450, 30), _d('a2', 'p1', 2480), _d('a3', 'p1', 2400, 35),
                       _d('a4', 'p1', 2520, 40)])
    m['hiatus'] = (_s('r', 'Site', [_b('b0'), _p('p1', 'Lower'), _b('b1'), _h('h1'), _b('b2'),
                                    _p('p2', 'Upper'), _b('b3')]),
                   [_d('a1', 'p1', 3400), _d('a2', 'p1', 3350), _d('a3', 'p2', 3000),
                    _d('a4', 'p2', 2950, 30)])
    m['named-hiatus'] = (_s('r', 'Site', [_b('b0'), _p('p1', 'Lower'), _b('b1'), _h('h1', 'Abandonment'),
                                          _b('b2'), _p('p2', 'Upper'), _b('b3')]),
                         [_d('a1', 'p1', 3400), _d('a2', 'p2', 3000)])
    m['nested'] = (_s('r', 'Site', [_b('b0', 'Start'), _p('p1', 'Early'), _b('b1'),
                                    _s('s2', 'Inner', [_b('b2'), _p('p2', 'Mid A'), _b('b3'),
                                                       _p('p3', 'Mid B'), _b('b4')]),
                                    _b('b5', 'End')]),
                   [_d('a1', 'p1', 3200), _d('a2', 'p1', 3150), _d('a3', 'p2', 3050),
                    _d('a4', 'p2', 3020), _d('a5', 'p3', 2950), _d('a6', 'p3', 2900, 30)])
    m['seq-of-seqs'] = (_s('r', 'Site', [_s('s1', 'First', [_b('b0'), _p('p1'), _b('b1')]),
                                         _s('s2', 'Second', [_b('b2'), _p('p2'), _b('b3')])]),
                        [_d('a1', 'p1', 3300), _d('a2', 'p1', 3260), _d('a3', 'p2', 3000),
                         _d('a4', 'p2', 2980)])
    m['areas'] = (_s('r', 'Site', [_b('b0', 'Start'), _p('p1', 'Early'), _b('b1', 'Transition'),
                                   _p('pa', 'Areas', [
                                       _s('sa', 'Area A', [_b('ba0'), _p('pa1', 'A lower'), _b('ba1'),
                                                           _p('pa2', 'A upper'), _b('ba2')]),
                                       _s('sb', 'Area B', [_b('bb0'), _p('pb1', 'B only'), _b('bb1')])]),
                                   _b('b2', 'End')]),
                  [_d('a1', 'p1', 3300), _d('a2', 'p1', 3250), _d('a3', 'pa1', 3100),
                   _d('a4', 'pa1', 3080), _d('a5', 'pa2', 2980), _d('a6', 'pb1', 3050),
                   _d('a7', 'pb1', 2990, 30)])
    m['ordered-blocks'] = (_s('r', 'Site', [_b('b0'), _p('p1', 'Lower'), _p('p2', 'Upper'), _b('b1')]),
                           [_d('a1', 'p1', 3060), _d('a2', 'p1', 3030), _d('a3', 'p2', 2990),
                            _d('a4', 'p2', 2970, 30), _d('a5', 'p2', 2950)])
    m['tails'] = (_s('r', 'Site', [_p('p0', 'Before'), _b('b0'), _p('p1', 'Middle'), _b('b1'),
                                   _p('p2', 'After')]),
                  [_d('a0', 'p0', 3300), _d('a1', 'p1', 3100), _d('a2', 'p1', 3060),
                   _d('a3', 'p2', 2900), _d('a4', 'p2', 2870)])
    m['undated-phase'] = (_s('r', 'Site', [_b('b0'), _p('p1'), _b('b1'), _p('p2', 'Empty'), _b('b2'),
                                           _p('p3'), _b('b3')]),
                          [_d('a1', 'p1', 3300), _d('a2', 'p1', 3280), _d('a3', 'p3', 3000),
                           _d('a4', 'p3', 2970)])
    m['limits'] = (_s('r', 'Site', [_b('b0'), _p('p1', tpq_age_bp=3500.0), _b('b1'),
                                    _p('p2', taq_age_bp=2700.0), _b('b2')],
                      tpq_age_bp=4000.0, taq_age_bp=2500.0),
                   [_d('a1', 'p1', 3200, tpq_age_bp=3450.0), _d('a2', 'p1', 3150),
                    _d('a3', 'p2', 2950, taq_age_bp=2800.0), _d('a4', 'p2', 2900)])
    m['unbounded'] = (_p('r', 'Loose'), [_d('a1', 'r', 3000), _d('a2', 'r', 3040)])
    m['outliers'] = (_s('r', 'Site', [_b('b0'), _p('p1'), _b('b1')]),
                     [_d('a1', 'p1', 3000), _d('a2', 'p1', 3030), _d('a3', 'p1', 3400, outlier_prior=0.05),
                      _d('a4', 'p1', 2990, outlier_prior=0.1, outlier_model='charcoal'),
                      _d('a5', 'p1', 3010, inbuilt_years=40.0, inbuilt_shape='exponential'),
                      _d('a6', 'p1', 2980, inbuilt_years=25.0)])
    m['marine'] = (_s('r', 'Midden', [_b('b0'), _p('p1'), _b('b1')]),
                   [_d('a1', 'p1', 3400, 30, curve_id='marine20', delta_r=-50.0, delta_r_error=30.0),
                    _d('a2', 'p1', 3000, 25), _d('a3', 'p1', 3450, 35, curve_id='marine20',
                                                 delta_r=-50.0, delta_r_error=30.0)])
    m['wiggle'] = ({'id': 'r', 'element_type': 'phase', 'name': 'Timber', 'children': [
                        {'id': 'w', 'element_type': 'wiggle_sequence', 'name': None, 'children': [],
                         'params': {'gap_units': 'rings', 'reference_sample_id': 'a1'}}]},
                   [_d('a1', 'w', 2480, 20, ring_offset=0.0), _d('a2', 'w', 2520, 20, ring_offset=30.0),
                    _d('a3', 'w', 2455, 20, ring_offset=10.0, ring_offset_error=3.0),
                    _d('a4', 'w', 2560, 25, ring_offset=55.0)])
    m['deposition'] = (_s('r', 'Core', [_b('b0'), {'id': 'dep', 'element_type': 'deposition_sequence',
                                                   'name': 'Core 1', 'params': {'k': 1.0, 'k_variable': False},
                                                   'children': [
                                                       {'id': 'lev', 'element_type': 'interpolated_depth',
                                                        'name': 'Tephra', 'children': [],
                                                        'params': {'depth_cm': 47.0}},
                                                       {'id': 'lev2', 'element_type': 'interpolated_depth',
                                                        'name': None, 'children': [],
                                                        'params': {'depth_cm': 12.5}}]},
                                         _b('b1')]),
                       [_d('a1', 'dep', 3200, depth_cm=80.0), _d('a2', 'dep', 2900, depth_cm=20.0),
                        _d('a3', 'dep', 3050, depth_cm=55.0)])
    return m


def _invalid_models():
    """Trees and dates each validator must refuse, with the reason."""
    ok = _s('r', 'Site', [_b('b0'), _p('p1'), _b('b1')])
    return {
        'empty': (None, []),
        'boundary-root': (_b('r'), []),
        'no-id': (_s('r', 'S', [_p('')]), []),
        'duplicate-id': (_s('r', 'S', [_p('dup'), _p('dup')]), []),
        'unknown-type': (_s('r', 'S', [{'id': 'x', 'element_type': 'trench', 'children': []}]), []),
        'boundary-with-children': (_s('r', 'S', [_b('b0', children=[_p('p')])]), []),
        'wiggle-with-children': (_s('r', 'S', [{'id': 'w', 'element_type': 'wiggle_sequence',
                                                'children': [_p('p')]}]), []),
        'empty-wiggle': (_s('r', 'S', [{'id': 'w', 'element_type': 'wiggle_sequence', 'name': 'Rings',
                                        'children': []}]), []),
        'deposition-with-phase': (_s('r', 'S', [{'id': 'd', 'element_type': 'deposition_sequence',
                                                 'children': [_p('p')]}]), []),
        'empty-deposition': (_s('r', 'S', [{'id': 'd', 'element_type': 'deposition_sequence',
                                            'children': []}]), []),
        'one-level': (_s('r', 'S', [{'id': 'd', 'element_type': 'deposition_sequence', 'name': 'Core',
                                     'children': []}]), [_d('a1', 'd', 3000, depth_cm=10.0)]),
        'dated-level': (_s('r', 'S', [{'id': 'd', 'element_type': 'deposition_sequence', 'children': [
                            {'id': 'l', 'element_type': 'interpolated_depth', 'name': 'Lev', 'children': [],
                             'params': {'depth_cm': 3.0}}]}]),
                        [_d('a1', 'd', 3000, depth_cm=1.0), _d('a2', 'l', 3000)]),
        'level-without-depth': (_s('r', 'S', [{'id': 'd', 'element_type': 'deposition_sequence', 'children': [
                                    {'id': 'l', 'element_type': 'interpolated_depth', 'children': []}]}]),
                                [_d('a1', 'd', 3000, depth_cm=1.0)]),
        'boundary-in-phase': (_s('r', 'S', [_p('p', children=[_b('bx')])]), []),
        'loose-hiatus': (_s('r', 'S', [_b('b0'), _h('h'), _p('p1')]), []),
        'limits-crossed': (_s('r', 'S', [_p('p', tpq_age_bp=1000.0, taq_age_bp=1500.5)]), []),
        'unassigned-date': (ok, [_d('a1', None, 3000)]),
        'unknown-element': (ok, [_d('a1', 'nope', 3000)]),
        'date-on-boundary': (ok, [_d('a1', 'b0', 3000)]),
        'nameless-unknown-element': (ok, [{'id': 'a1', 'age_bp': 3000.0, 'error_1sigma': 25.0,
                                           'element_id': 'zz'}]),
    }


def _invalid_ordinates():
    def wig(params=None):
        return _p('r', 'P', [{'id': 'w', 'element_type': 'wiggle_sequence', 'name': 'Rings',
                              'children': [], 'params': params}])

    def dep(params=None, children=()):
        return _s('r', 'S', [{'id': 'd', 'element_type': 'deposition_sequence', 'name': 'Core',
                              'children': list(children), 'params': params}])
    return {
        'no-gap': (wig(), [_d('z1', 'w', 3000), _d('a2', 'w', 3000, ring_offset=1.0), _d('b3', 'w', 3000)]),
        'same-gap': (wig(), [_d('a1', 'w', 3000, ring_offset=5.0), _d('a2', 'w', 3000, ring_offset=5.0)]),
        'negative-error': (wig(), [_d('a1', 'w', 3000, ring_offset=0.0, ring_offset_error=-1.0)]),
        'stray-reference': (wig({'reference_sample_id': 'zz'}), [_d('a1', 'w', 3000, ring_offset=0.0)]),
        'inbuilt': (wig(), [_d('a1', 'w', 3000, ring_offset=0.0, inbuilt_years=20.0)]),
        'outlier': (wig(), [_d('a1', 'w', 3000, ring_offset=0.0, outlier_prior=0.05),
                            _d('a0', 'w', 3000, ring_offset=2.0, outlier_prior=0.05)]),
        'no-depth': (dep(), [_d('a1', 'd', 3000)]),
        'same-depth': (dep(children=[{'id': 'l', 'element_type': 'interpolated_depth', 'children': [],
                                      'params': {'depth_cm': 10.0}}]), [_d('a1', 'd', 3000, depth_cm=10.0)]),
        'bad-k': (dep({'k': -1.5}), [_d('a1', 'd', 3000, depth_cm=1.0), _d('a2', 'd', 2900, depth_cm=0.5)]),
        'empty-k-range': (dep({'log_k_min': 1.0, 'log_k_max': 1.0}),
                          [_d('a1', 'd', 3000, depth_cm=1.0), _d('a2', 'd', 2900, depth_cm=0.5)]),
        'fine': (dep({'k': 2.0}), [_d('a1', 'd', 3000, depth_cm=1.0), _d('a2', 'd', 2900, depth_cm=0.5)]),
    }


def _oxcal_models():
    """Every .oxcal fixture as the importer reads it, ids made stable: (name,
    tree, dates, resolution)."""
    from app.services.date_import import parse_upload
    out = []
    for path in _oxcal_fixtures():
        with open(path, 'rb') as fh:
            content = fh.read()
        result = parse_upload(os.path.basename(path), content, 'corpus').as_dict()
        if not result.get('ok'):
            continue
        stable = Stable()
        tree = stable(result['model_tree'])
        dates = stable([{k: v for k, v in d.items() if k != 'phase_name'} for d in result['dates']])
        out.append((os.path.relpath(path, FIXTURES), tree, dates, result.get('resolution')))
    return out


def _all_models():
    models = [(f'hand/{k}', tree, dates) for k, (tree, dates) in _hand_models().items()]
    models += [(f'oxcal/{name}', tree, dates) for name, tree, dates, _res in _oxcal_models()]
    return models


def _plan_dict(plan) -> Dict[str, Any]:
    return {
        'ordering': [[[list(t) for t in older], [list(t) for t in younger]]
                     for older, younger in plan.ordering],
        'span_groups': [{'older': g.older_boundary_id, 'younger': g.younger_boundary_id,
                         'dates': g.date_ids, 'contained': g.contained_ids}
                        for g in plan.span_groups],
        'marginalized': sorted(plan.marginalized_boundaries),
        'sampled': plan.sampled_boundaries,
        'boundary_names': [[k, v] for k, v in plan.boundary_names.items()],
        'wiggle_groups': [{'element_id': g.element_id, 'name': g.name,
                           'members': [list(m) for m in g.members], 'gap_units': g.gap_units,
                           'reference_sample_id': g.reference_sample_id}
                          for g in plan.wiggle_groups],
        'deposition_groups': [{'element_id': g.element_id, 'name': g.name,
                               'levels': [list(level) for level in g.levels], 'k': g.k,
                               'k_variable': g.k_variable, 'log_k_min': g.log_k_min,
                               'log_k_max': g.log_k_max, 'depth_units': g.depth_units,
                               'level_names': g.level_names}
                              for g in plan.deposition_groups],
    }


def units_model_tree():
    from app.services import model_tree as mt
    cases = []
    for name, tree, dates in _all_models():
        def run(tree=tree, dates=dates):
            mt.validate_tree(tree, dates)
            mt.validate_ordinates(tree, dates)
            elements = list(mt.iter_elements(tree))
            bounds = mt.inherited_bounds(tree)
            wiggle = mt.standalone_wiggle_match(tree, dates)
            ids = [d['id'] for d in dates]
            return {
                'plan': _plan_dict(mt.plan_model(tree, dates)),
                'elements': [e['id'] for e in elements],
                'labels': [mt.element_label(e) for e in elements],
                'params': [mt.element_params(e) for e in elements],
                'inherited_bounds': [list(bounds[e['id']]) for e in elements],
                'standalone_wiggle_match': None if wiggle is None else wiggle['id'],
                'flattened': [{**{k: v for k, v in row.items() if k not in ('sample', 'posterior')},
                               'sample': None if row['sample'] is None else ids.index(row['sample']['id'])}
                              for row in mt.flatten_structure(tree, dates)],
            }
        cases.append(capture(f'model/{name}', {'tree': tree, 'dates': dates}, run))
    for name, (tree, dates) in _invalid_models().items():
        cases.append(capture(f'validate_tree/{name}', {'tree': tree, 'dates': dates},
                             lambda t=tree, d=dates: mt.validate_tree(t, d)))
    for name, (tree, dates) in _invalid_ordinates().items():
        cases.append(capture(f'validate_ordinates/{name}', {'tree': tree, 'dates': dates},
                             lambda t=tree, d=dates: mt.validate_ordinates(t, d)))
    # A flattened reading with dates in no element, and one found by group_id.
    tree, dates = _hand_models()['chain']
    loose = dates + [{**_d('x1', None, 3000), 'group_id': 'p2'}, _d('x2', 'gone', 2000)]
    ids = [d['id'] for d in loose]
    cases.append(capture('flatten/orphans', {'tree': tree, 'dates': loose}, lambda: {
        'flattened': [{**{k: v for k, v in row.items() if k not in ('sample', 'posterior')},
                       'sample': None if row['sample'] is None else ids.index(row['sample']['id'])}
                      for row in mt.flatten_structure(tree, loose)]}))
    return cases


def units_stratigraphy():
    from app.services import model_tree as mt
    from app.services import stratigraphy as st
    cases = []
    groups = [{'id': 'g1', 'name': 'Upper', 'tpq_age_bp': 3000.0, 'taq_age_bp': None},
              {'id': 'g2', 'name': 'Lower', 'tpq_age_bp': None, 'taq_age_bp': 2000.0},
              {'id': 'g3', 'name': None, 'tpq_age_bp': None, 'taq_age_bp': None},
              {'id': 'g4', 'name': 'Empty', 'tpq_age_bp': None, 'taq_age_bp': None}]
    dates = [{'id': 'a', 'group_id': 'g1'}, {'id': 'b', 'group_id': 'g2'}, {'id': 'c', 'group_id': 'g2'},
             {'id': 'd', 'group_id': 'g3'}, {'id': 'e', 'group_id': 'g9'}, {'id': 'f', 'group_id': None}]
    relationship_sets = {
        'none': [],
        'chain': [{'from_sample_id': 'g1', 'to_sample_id': 'g2', 'relationship': 'above',
                   'from_type': 'group', 'to_type': 'group'},
                  {'from_sample_id': 'g2', 'to_sample_id': 'd', 'relationship': 'above',
                   'from_type': 'group', 'to_type': 'sample'}],
        'below-and-equal': [{'from_sample_id': 'a', 'to_sample_id': 'b', 'relationship': 'below'},
                            {'from_sample_id': 'c', 'to_sample_id': 'b', 'relationship': 'equal'},
                            {'from_sample_id': 'g2', 'to_sample_id': 'g2', 'relationship': 'above',
                             'from_type': 'group', 'to_type': 'group'},
                            {'from_sample_id': 'g4', 'to_sample_id': 'a', 'relationship': 'above',
                             'from_type': 'group', 'to_type': None}],
        'cycle': [{'from_sample_id': 'a', 'to_sample_id': 'b', 'relationship': 'above'},
                  {'from_sample_id': 'b', 'to_sample_id': 'c', 'relationship': 'above'},
                  {'from_sample_id': 'c', 'to_sample_id': 'a', 'relationship': 'above'},
                  {'from_sample_id': 'd', 'to_sample_id': 'a', 'relationship': 'above'}],
        'partial': [{'from_sample_id': 'g3', 'to_sample_id': 'g1', 'relationship': 'above',
                     'from_type': 'group', 'to_type': 'group'}],
    }
    cases.append(capture('group_members', {'dates': dates, 'groups': groups},
                         lambda: {'members': [[k, v] for k, v in st.group_members(dates, groups).items()]}))
    for name, rels in relationship_sets.items():
        def run(rels=rels):
            older, equal = st.expand_relationships(dates, groups, rels)
            counter = iter(range(1000))
            root, linearized, boundaries = mt.migrate_from_relationships(
                dates, groups, rels, lambda: f'gen{next(counter)}')
            return {'older_younger': sorted(list(e) for e in older),
                    'equal': sorted(list(e) for e in equal),
                    'cycle': st.find_cycle(older),
                    'migrated': {'root': root, 'linearized': linearized,
                                 'group_boundaries': [[k, list(v)] for k, v in boundaries.items()]}}
        cases.append(capture(f'relationships/{name}', {'dates': dates, 'groups': groups,
                                                       'relationships': rels}, run))
    pairs = [[None, None, None], [3000.0, None, None], [None, 2500.0, [None, None]],
             [3000.0, 2000.0, [3100.0, 2100.0]], [3000.0, 2000.0, [2900.0, 1900.0]],
             [None, None, [2900.0, 1900.0]], [3000.0, None, [None, 2500.0]]]
    cases.append(capture('effective_bounds', {'cases': pairs}, lambda: {'bounds': [
        list(st.effective_bounds({'tpq_age_bp': tpq, 'taq_age_bp': taq},
                                 None if g is None else {'tpq_age_bp': g[0], 'taq_age_bp': g[1]}))
        for tpq, taq, g in pairs]}))
    return cases


# Joint draws the interval cases are evaluated on. Fewer than a solve's
# 20,000, to keep the corpus small; the arithmetic is the same at any count.
INTERVAL_DRAWS = 1000


def _joint_draws(tree, dates, count=INTERVAL_DRAWS):
    """
    ({parameter id: draws}, grid step) for a model: the exact solver's own
    joint draws where it can take the model, else draws made up for it --
    ordered along each deposition sequence, so an age-depth model reads as one.
    """
    from app.api.analysis import _exact_layout
    from app.services import exact_chain, exact_parallel
    layout, _reason = _exact_layout(tree, dates)
    if layout is not None:
        solve = (exact_parallel.solve if isinstance(layout, exact_parallel.Stages)
                 else exact_chain.solve)
        solved = solve(dates, tree, layout, num_samples=10, interval_draws=count)
        draws = {**solved['joint_boundaries'], **solved['joint_draws']}
        return {k: [float(v) for v in values] for k, values in draws.items()}, solved['grid']['step']
    from app.services import model_tree as mt
    rng = np.random.default_rng(17)
    draws = {}
    for m in dates:
        draws[m['id']] = list(np.round(rng.normal(3000.0 - 10.0 * len(draws), 30.0, count), 2))
    for group in mt.plan_model(tree, dates).deposition_groups:
        ids = group.parameter_ids
        stacked = np.sort(np.round(rng.normal(3000.0, 120.0, (len(ids), count)), 2), axis=0)[::-1]
        for pid, row in zip(ids, stacked):
            draws[pid] = [float(v) for v in row]
    return draws, 0.0


def _block_dict(block):
    return plain(block)


def units_intervals():
    from app.services import intervals as iv
    from app.services import kde
    cases = []
    models = _hand_models()
    for name, (tree, dates) in models.items():
        draws, grid_step = _joint_draws(tree, dates)
        steps = [None, 1.0] if name == 'chain' else [None]
        for step in steps:
            def run(tree=tree, dates=dates, draws=draws, step=step, grid_step=grid_step):
                arrays = {k: np.asarray(v) for k, v in draws.items()}
                differences, note = iv.plan_phase_differences(tree, dates)
                return {
                    'durations_plan': [{**spec, 'boundaries': list(spec['boundaries']),
                                        'dates': list(spec['dates'])}
                                       for spec in iv.plan_durations(tree, dates)],
                    'differences_plan': differences, 'note': note,
                    'activity_plan': iv.plan_phase_activity(tree, dates),
                    'age_depth_plan': iv.plan_age_depth(tree, dates),
                    'report': iv.report(tree, dates, arrays, independent=True, step=step),
                    'phases': kde.report_phases(tree, dates, arrays, grid_step=grid_step),
                }
            cases.append(capture(f'report/{name}/step={step}',
                                 {'tree': tree, 'dates': dates, 'draws': draws, 'step': step,
                                  'grid_step': grid_step}, run))
    # Without durations the age-depth section alone.
    tree, dates = models['deposition']
    draws, _step = _joint_draws(tree, dates)
    cases.append(capture('report/deposition/no-durations',
                         {'tree': tree, 'dates': dates, 'draws': draws, 'step': None},
                         lambda: iv.report(tree, dates, {k: np.asarray(v) for k, v in draws.items()},
                                           independent=False, durations=False)))

    rng = np.random.default_rng(23)
    arrays = {
        'normal': np.round(rng.normal(40.0, 12.0, 500), 3),
        'against-zero': np.abs(np.round(rng.normal(0.0, 20.0, 400), 1)),
        'heavy-tail': np.round(np.concatenate([rng.normal(100.0, 5.0, 300), [900.0, -700.0]]), 2),
        'wide': np.round(rng.normal(0.0, 3000.0, 800), 1),
        'one': np.array([12.5]),
        'two': np.array([3.0, 3.0]),
        'constant': np.full(50, 7.0),
        'with-nan': np.array([1.0, np.nan, 5.0, np.inf, 9.0, 2.0, 4.0]),
        'all-nan': np.array([np.nan, np.nan]),
        'grid-ties': np.repeat(np.arange(0.0, 60.0, 2.5), 7),
    }
    for label, values in arrays.items():
        for base in (None, 1.0, 0.25, 20.0):
            cases.append(capture(f'summarise/{label}/base={base}',
                                 {'values': values, 'base': base},
                                 lambda v=values, b=base: {'summary': iv.summarise(v, b),
                                                           'step': iv._step_for(v[np.isfinite(v)], b)
                                                           if np.isfinite(v).any() else None}))
    overlaps = [0.0, 0.0455, 0.04550026389635842, 0.2, 0.3173, 0.31731050786291415, 0.5,
                0.6826894921370859, 0.9, 0.9544997361036416, 0.99, 1.0]
    cases.append(capture('severity', {'overlaps': overlaps},
                         lambda: {'words': [iv.severity(o) for o in overlaps]}))
    named = [{'id': f'd{i}', 'name': f'D-{i}'} for i in range(5)]
    draws = {d['id']: np.round(rng.normal(3000.0 + 15.0 * i, 25.0, 300), 1) for i, d in enumerate(named)}
    cases.append(capture('report_dates', {'dates': named, 'draws': draws},
                         lambda: iv.report_dates(named, draws)))
    many = [{'id': f'd{i}', 'name': f'D-{i}'} for i in range(26)]
    cases.append(capture('plan_date_differences/many', {'dates': many},
                         lambda: dict(zip(('specs', 'note'), iv.plan_date_differences(many)))))
    band = np.round(rng.normal(2500.0, 40.0, 999), 2)
    cases.append(capture('calendar_band', {'values': band}, lambda: iv.calendar_band(band)))
    return cases


def _posterior(summary: Dict) -> Dict:
    """A solver's summary as stored: its HPD ranges as plain lists."""
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in summary.items()}


def _branching_models():
    """Shapes the chain solver must decline, each for its own reason."""
    m = {}
    m['no-boundaries'] = (_s('r', 'S', [_p('p1'), _p('p2')]), [_d('a1', 'p1', 3000), _d('a2', 'p2', 2900)])
    m['one-boundary'] = (_s('r', 'S', [_b('b0'), _p('p1')]), [_d('a1', 'p1', 3000)])
    m['branches'] = (_s('r', 'S', [_b('b0'), _p('pa', 'Areas', [
                         _s('sa', 'A', [_b('ba0'), _p('pa1'), _b('ba1')]),
                         _s('sb', 'B', [_b('bb0'), _p('pb1'), _b('bb1')])]), _b('b1')]),
                     [_d('a1', 'pa1', 3000), _d('a2', 'pb1', 2950)])
    m['loose-date'] = (_p('r', 'Outer', [_s('s', 'S', [_b('b0'), _p('p1'), _b('b1')]), _p('p2')]),
                       [_d('a1', 'p1', 3000), _d('a2', 'p2', 3100)])
    m['flagged-tail'] = (_s('r', 'S', [_p('p0'), _b('b0'), _p('p1'), _b('b1')]),
                         [_d('a0', 'p0', 3200, outlier_prior=0.05), _d('a1', 'p1', 3000)])
    m['side-by-side-sequences'] = (_s('r', 'S', [_b('b0'), _p('p', 'Both', [
                                       _s('x', 'X', [_p('x1'), _p('x2')]),
                                       _s('y', 'Y', [_p('y1'), _p('y2')])]), _b('b1')]),
                                   [_d('a1', 'x1', 3050), _d('a2', 'x2', 3000), _d('a3', 'y1', 3040),
                                    _d('a4', 'y2', 2990)])
    m['areas-unbounded'] = (_p('r', 'Site', [_s('sa', 'A', [_b('ba0'), _p('pa1'), _b('ba1')]),
                                             _s('sb', 'B', [_b('bb0'), _p('pb1'), _b('bb1')])]),
                            [_d('a1', 'pa1', 3000), _d('a2', 'pb1', 2950)])
    m['areas-in-a-row'] = (_s('r', 'Site', [_b('b0'), _p('p1'), _p('pa', 'Areas', [
                               _s('sa', 'A', [_b('ba0'), _p('pa1'), _b('ba1')])]), _b('b1')]),
                           [_d('a1', 'p1', 3100), _d('a2', 'pa1', 3000)])
    m['area-not-a-line'] = (_s('r', 'Site', [_b('b0'), _p('pa', 'Areas', [
                                _s('sa', 'A', [_p('pa1'), _p('pa2')])]), _b('b1')]),
                            [_d('a1', 'pa1', 3000), _d('a2', 'pa2', 2950)])
    m['area-with-boundary-phase'] = (_s('r', 'Site', [_b('b0'), _p('pa', 'Areas', [
                                         _s('sa', 'A', [_b('ba0'), _p('pa1'), _b('ba1')]),
                                         {'id': 'w', 'element_type': 'wiggle_sequence', 'children': []}]),
                                         _b('b1')]),
                                     [_d('a1', 'pa1', 3000)])
    return m


def _parallel_models():
    """Areas side by side, as exact_parallel takes them."""
    m = {'areas': _hand_models()['areas']}
    m['three-areas'] = (_s('r', 'Site', [_b('b0', 'Start'), _p('pa', 'Trenches', [
                            _s('sa', 'T1', [_b('ba0'), _p('pa1'), _b('ba1')]),
                            _s('sb', 'T2', [_b('bb0'), _p('pb1'), _b('bb1'), _p('pb2'), _b('bb2')]),
                            _s('sc', 'T3', [_b('bc0'), _p('pc1'), _b('bc1')])]),
                                         _b('b1', 'End')]),
                        [_d('a1', 'pa1', 2500), _d('a2', 'pa1', 2470), _d('a3', 'pb1', 2520),
                         _d('a4', 'pb2', 2440), _d('a5', 'pc1', 2480, 30), _d('a6', 'pc1', 2455)])
    m['areas-with-own-dates'] = (_s('r', 'Site', [_b('b0'), _p('pa', 'Areas', [
                                     _s('sa', 'A', [_b('ba0'), _p('pa1'), _b('ba1')]),
                                     _s('sb', 'B', [_b('bb0'), _p('pb1'), _b('bb1')])]),
                                                  _b('b1'), _p('p2', 'Late'), _b('b2')]),
                                 [_d('a0', 'pa', 3060), _d('a1', 'pa1', 3080), _d('a2', 'pa1', 3040),
                                  _d('a3', 'pb1', 3030), _d('a4', 'p2', 2900), _d('a5', 'p2', 2930)])
    m['areas-and-tail'] = (_s('r', 'Site', [_p('p0', 'Before'), _b('b0'), _p('pa', 'Areas', [
                               _s('sa', 'A', [_b('ba0'), _p('pa1'), _b('ba1')]),
                               _s('sb', 'B', [_b('bb0'), _p('pb1'), _b('bb1')])]), _b('b1')]),
                           [_d('a0', 'p0', 3250), _d('a1', 'pa1', 3050), _d('a2', 'pb1', 3000),
                            _d('a3', 'pb1', 2980)])
    return m


def _chain_dict(chain) -> Dict[str, Any]:
    return {'boundary_ids': chain.boundary_ids, 'groups': chain.groups,
            'boundary_names': [[k, v] for k, v in chain.boundary_names.items()],
            'older_tail': chain.older_tail, 'younger_tail': chain.younger_tail,
            'blocks': chain.blocks, 'older_tail_blocks': chain.older_tail_blocks,
            'younger_tail_blocks': chain.younger_tail_blocks}


# Smaller than an API run: the arithmetic is the same at any count.
SOLVE_SAMPLES = 40
SOLVE_DRAWS = 400


def _solution_dict(solved: Dict) -> Dict[str, Any]:
    out = {
        'measurements': [[k, _posterior(v)] for k, v in solved['measurements'].items()],
        'boundaries': [[k, {'name': v['name'], 'posterior': _posterior(v['posterior'])}]
                       for k, v in solved['boundaries'].items()],
        'joint_draws': solved['joint_draws'],
        'joint_boundaries': solved['joint_boundaries'],
        'intervals': solved.get('intervals'),
        'log_evidence': solved['log_evidence'],
        'uniform_span_prior': solved['uniform_span_prior'],
        'grid': solved['grid'],
        'edge_mass': solved['edge_mass'],
        'outlier_scale': solved.get('outlier_scale'),
        'joint_scales': solved.get('joint_scales'),
        'evidence': solved.get('evidence'),
    }
    return out


def units_exact_chain():
    from app.services import exact_chain as ec
    from app.services.model_tree import inherited_bounds
    from scipy.special import gammaln
    cases = []
    xs = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 7.3, 12.9, 13.0, 14.0, 26.0, 100.0, 999.5, 1000.0, 1001.0,
          2000.5, 3999.0, 5e8, 1e9, 0.1, 1e-5]
    xs += [float(x) for x in np.random.default_rng(31).uniform(0.001, 3000.0, 200)]
    cases.append(capture('gammaln', {'x': xs}, lambda: {'gammaln': [float(gammaln(x)) for x in xs]}))

    allowances = [(None, None), (40.0, None), (40.0, 'uniform'), (25.3, 'exponential'), (0.4, 'uniform'),
                  (3.0, 'exponential'), (12.0, 'sinusoidal')]
    for step in (0.25, 1.0, 5.0):
        for years, shape in allowances:
            m = {'id': 'x', 'sample_name': 'X', 'inbuilt_years': years, 'inbuilt_shape': shape}
            cases.append(capture(f'inbuilt_kernel/{step}/{years}/{shape}',
                                 {'step': step, 'measurement': m},
                                 lambda m=m, step=step: {'reach': ec.inbuilt_reach(m),
                                                         'kernel': ec.inbuilt_kernel(step, m)}))
    for years, shape in [(30.0, 'uniform'), (15.0, 'exponential')]:
        m = _d('e', None, 2450, 30, inbuilt_years=years, inbuilt_shape=shape)
        grid = 2300.0 + 2.5 * np.arange(160)
        cases.append(capture(f'event_likelihood/{shape}', {'measurement': m, 'grid': grid},
                             lambda m=m, grid=grid: {'likelihood': ec.event_likelihood(grid, m)}))

    for model in ec.OUTLIER_MODELS:
        cases.append(capture(f'scale_nodes/{model}', {'model': model},
                             lambda model=model: {'nodes': ec.scale_nodes(model)}))
        for step in (25.0, 100.0):
            for log_scale in (0.0, 1.3, 3.0, 4.0):
                cases.append(capture(f'kernel_at_scale/{model}/{step}/{log_scale}',
                                     {'model': model, 'step': step, 'half_width': 20000.0,
                                      'log_scale': log_scale},
                                     lambda model=model, step=step, s=log_scale:
                                         {'kernel': ec._outlier_kernel_at_scale(step, 20000.0, model, s)}))
            cases.append(capture(f'outlier_kernel/{model}/{step}',
                                 {'model': model, 'step': step, 'half_width': 20000.0},
                                 lambda model=model, step=step:
                                     {'kernel': ec._outlier_kernel(step, 20000.0, model)}))
    rng = np.random.default_rng(7)
    values = np.exp(-0.5 * ((np.arange(300) - 140.0) / 12.0) ** 2) + 0.3 * np.exp(-0.5 * ((np.arange(300) - 60.0) / 5.0) ** 2)
    for label, kernel in [('short', np.array([0.2, 0.5, 0.3])),
                          ('long', ec._outlier_kernel_at_scale(1.0, 400.0, 'general', 1.5)),
                          ('one-sided', ec._outlier_kernel_at_scale(1.0, 400.0, 'charcoal', 1.2)),
                          ('random', rng.uniform(0.0, 1.0, 51))]:
        cases.append(capture(f'convolve_on_grid/{label}', {'values': values, 'kernel': kernel},
                             lambda kernel=kernel: {'convolved': ec.convolve_on_grid(values, kernel)}))
    for step in (0.3, 1.0, 1.015, 2.6, 5.0, 7.5, 13.0):
        cases.append(capture(f'reporting_aligned_step/{step}', {'step': step},
                             lambda step=step: {'step': ec._reporting_aligned_step(step)}))
    cases.append(capture('point_budget', {'groups': list(range(1, 25))},
                         lambda: {'budget': [ec._point_budget(n) for n in range(1, 25)]}))
    cases.append(capture('log_interior_placements', {'spans': list(range(0, 60)), 'interior': [1, 2, 3, 5]},
                         lambda: {'placements': [ec.log_interior_placements(np.arange(60.0), k)
                                                 for k in (1, 2, 3, 5)]}))
    spans = list(np.linspace(-20.0, 400.0, 43))
    limit_sets = [((np.inf, -np.inf), (np.inf, -np.inf)), ((3500.0, 3100.0), (3000.0, 2600.0)),
                  ((3500.0, -np.inf), (np.inf, 2600.0)), ((np.inf, 3100.0), (3000.0, -np.inf)),
                  ((3300.0, 3299.0), (3000.0, 2999.5))]
    cases.append(capture('log_limits_factor_of_span', {'spans': spans, 'limits': limit_sets},
                         lambda: {'factor': [ec.log_limits_factor_of_span(np.array(spans), o, y)
                                             for o, y in limit_sets]}))
    grid = 2800.0 + 7.5 * np.arange(24)
    for n, limits in [(2, None), (3, None), (5, None), (2, limit_sets[1]), (4, limit_sets[2])]:
        o, y = limits if limits else (None, None)
        cases.append(capture(f'span_prior_correction/{n}/{limits is not None}',
                             {'grid': grid, 'boundaries': n, 'limits': limits},
                             lambda n=n, o=o, y=y: {'correction': ec._span_prior_correction(grid, n, True, o, y)}))
    cases.append(capture('boundary_pair_term', {'grid': grid[:12], 'dates': [0, 1, 3]},
                         lambda: {'term': [ec._boundary_pair_term(grid[:12], k) for k in (0, 1, 3)]}))

    # Tables on one grid, and what is read off them.
    models = _hand_models()
    tree, dates = models['outliers']
    bounds = inherited_bounds(tree)
    grid = 2700.0 + 2.5 * np.arange(400)
    def tables(scale_weights=None):
        lik, cum, parts = ec.date_tables(grid, dates, bounds, scale_weights)
        return {'likelihood': lik, 'cumulative': cum,
                'outlier_parts': {k: {'inlier': v[0], 'broad': v[1], 'prior': v[2]} for k, v in parts.items()}}
    cases.append(capture('date_tables/outliers', {'tree': tree, 'dates': dates, 'grid': grid}, tables))
    weights = {'general': np.linspace(1.0, 2.0, 24) / np.linspace(1.0, 2.0, 24).sum(),
               'charcoal': np.r_[np.zeros(23), 1.0]}
    cases.append(capture('date_tables/outliers/weighed', {'tree': tree, 'dates': dates, 'grid': grid,
                                                          'scale_weights': weights},
                         lambda: tables(weights)))
    tree, dates = models['limits']
    bounds = inherited_bounds(tree)
    cases.append(capture('flat_date_tables/limits', {'tree': tree, 'dates': dates, 'grid': grid},
                         lambda: dict(zip(('likelihood', 'cumulative'),
                                          ec.flat_date_tables(grid, dates, bounds)))))
    for name, (tree, dates) in models.items():
        if name in ('wiggle', 'deposition'):
            continue
        bounds = inherited_bounds(tree)
        cases.append(capture(f'grid/{name}', {'tree': tree, 'dates': dates}, lambda dates=dates, bounds=bounds: {
            'extent': ec._dates_extent(dates, bounds),
            'grid': {str(n): ec._build_grid(dates, bounds, n)[[0, 1, -1]] for n in (1, 3, 9, 25)}}))
    lik = np.exp(-0.5 * ((grid - 3000.0) / 30.0) ** 2)
    lik /= lik.sum() * 2.5
    cum = np.cumsum(lik) * 2.5
    cases.append(capture('interval_integral', {'grid': grid[:40], 'likelihood': lik[100:140],
                                               'cumulative': cum[100:140]},
                         lambda: {'log_date_factor': ec._log_date_factor(cum[100:140], lik[100:140], 2.5)}))
    on_y, on_o = rng.uniform(0.0, 1.0, 50), rng.uniform(0.0, 1.0, 50)
    cases.append(capture('straddling', {'rows': on_y, 'cols': on_o},
                         lambda: {'straddling': ec._straddling(on_y, on_o)}))
    density = lik * (1.0 + 0.4 * np.sin(grid / 17.0))
    for step in (5.0, 1.0, 20.0):
        cases.append(capture(f'summarize/{step}', {'grid': grid, 'density': density, 'step': step, 'seed': 3,
                                                   'samples': 25},
                             lambda step=step: _posterior(ec._summarize(grid, density, np.random.default_rng(3),
                                                                        25, step))))
    cases.append(capture('edge_mass', {'grid': grid, 'densities': [density, lik[::-1]]},
                         lambda: {'edge_mass': ec._edge_mass([density, lik[::-1]], grid)}))
    weights = np.where(np.arange(30) % 7 == 0, 0.0, rng.uniform(0.0, 3.0, 30))
    cases.append(capture('sample_indices', {'weights': weights, 'seed': 11, 'count': 60},
                         lambda: {'indices': ec._sample_indices(np.random.default_rng(11), weights, 60)}))
    rows = rng.uniform(0.0, 1.0, (6, 30)) * (rng.uniform(0.0, 1.0, (6, 30)) > 0.3)
    keys = rng.integers(0, 6, 80)
    cases.append(capture('draw_from_rows', {'rows': rows, 'keys': keys, 'seed': 13},
                         lambda: {'picked': ec._draw_from_rows(np.random.default_rng(13), lambda k: rows[k],
                                                               keys)}))
    lows = rng.integers(0, 200, 60)
    highs = lows + rng.integers(0, 120, 60)
    cases.append(capture('draw_in', {'likelihood': lik, 'cumulative': cum, 'low': lows, 'high': highs,
                                     'step': 2.5, 'seed': 17},
                         lambda: {'half': ec._draw_in(np.random.default_rng(17), cum, lik, lows, highs, 2.5, True),
                                  'whole': ec._draw_in(np.random.default_rng(17), cum, lik, lows, highs, 2.5, False)}))

    # Layouts: every model, as the API asks.
    for name, (tree, dates) in list(models.items()) + list(_branching_models().items()):
        def lay(tree=tree, dates=dates):
            chain, reason = ec.layout_with_reason(tree, dates, allow_tails=True)
            strict, strict_reason = ec.layout_with_reason(tree, dates)
            return {'chain': None if chain is None else _chain_dict(chain), 'reason': reason,
                    'strict_reason': strict_reason,
                    'strict': None if strict is None else _chain_dict(strict)}
        cases.append(capture(f'layout/{name}', {'tree': tree, 'dates': dates}, lay))
    for name, tree, dates, _res in _oxcal_models():
        def lay(tree=tree, dates=dates):
            chain, reason = ec.layout_with_reason(tree, dates, allow_tails=True)
            return {'chain': None if chain is None else _chain_dict(chain), 'reason': reason}
        cases.append(capture(f'layout/oxcal/{name}', {'tree': tree, 'dates': dates}, lay))

    # Whole solves.
    for name, (tree, dates) in models.items():
        chain, _reason = ec.layout_with_reason(tree, dates, allow_tails=True)
        if chain is None:
            continue
        variants = [{}]
        if name == 'chain':
            # Reported coarser than the solver's grid: finer, every bin inside
            # one grid cell has the same mass exactly, and which of those tied
            # bins an HPD region takes is decided by rounding.
            variants += [{'uniform_span_prior': False}, {'resolution': 10.0}]
        for extra in variants:
            options = dict(num_samples=SOLVE_SAMPLES, interval_draws=SOLVE_DRAWS, estimate_durations=True, **extra)
            label = '/'.join(f'{k}={v}' for k, v in extra.items())
            cases.append(capture(f'solve/{name}' + (f'/{label}' if label else ''),
                                 {'tree': tree, 'dates': dates, 'options': options},
                                 lambda tree=tree, dates=dates, chain=chain, options=options:
                                     _solution_dict(ec.solve(dates, tree, chain, **options))))
    # Evidence on a model whose outer Boundaries are bounded.
    tree, dates = models['chain']
    bounded = {**tree, 'tpq_age_bp': 3600.0, 'taq_age_bp': 2500.0}
    chain, _ = ec.layout_with_reason(bounded, dates)
    options = dict(num_samples=SOLVE_SAMPLES, interval_draws=SOLVE_DRAWS)
    cases.append(capture('solve/chain/bounded', {'tree': bounded, 'dates': dates, 'options': options},
                         lambda: _solution_dict(ec.solve(dates, bounded, chain, **options))))
    return cases


def units_exact_parallel():
    from app.services import exact_parallel as ep
    cases = []
    rng = np.random.default_rng(19)
    j = rng.uniform(0.0, 1.0, (7, 7))
    cases.append(capture('sums', {'matrix': j}, lambda: {
        'tied_cumsum_0': ep.tied_cumsum(j, 0), 'tied_cumsum_1': ep.tied_cumsum(j, 1),
        'within': ep.within(j), 'outside': ep.outside(j)}))
    models = {**_branching_models(), **_parallel_models()}
    for name, (tree, dates) in models.items():
        def lay(tree=tree, dates=dates):
            stages, reason = ep.layout_with_reason(tree, dates)
            if stages is None:
                return {'stages': None, 'reason': reason}
            return {'reason': reason, 'stages': {
                'boundary_ids': stages.boundary_ids,
                'stages': [{'older': s.older_id, 'younger': s.younger_id, 'dates': s.dates,
                            'blocks': s.blocks, 'areas': [_chain_dict(a) for a in s.areas]}
                           for s in stages.stages],
                'older_tail': stages.older_tail, 'younger_tail': stages.younger_tail,
                'all_boundary_ids': stages.all_boundary_ids, 'num_groups': stages.num_groups}}
        cases.append(capture(f'layout/{name}', {'tree': tree, 'dates': dates}, lay))
    for name, (tree, dates) in _parallel_models().items():
        stages, reason = ep.layout_with_reason(tree, dates)
        assert stages is not None, (name, reason)
        options = dict(num_samples=SOLVE_SAMPLES, interval_draws=SOLVE_DRAWS, estimate_durations=True)
        cases.append(capture(f'solve/{name}', {'tree': tree, 'dates': dates, 'options': options},
                             lambda tree=tree, dates=dates, stages=stages:
                                 _solution_dict(ep.solve(dates, tree, stages, **options))))
    return cases


def units_agreement():
    from app.services import agreement as ag
    from app.services.calibration import get_curve
    cases = []
    rng = np.random.default_rng(29)
    plain = _d('p', None, 2450, 30)
    marine = _d('m', None, 3400, 30, curve_id='marine20', delta_r=-50.0, delta_r_error=30.0)
    general = _d('g', None, 3000, 25, outlier_prior=0.05)
    charcoal = _d('c', None, 3010, 25, outlier_prior=0.1, outlier_model='charcoal')
    inbuilt = _d('i', None, 2980, 25, inbuilt_years=40.0, inbuilt_shape='exponential')
    for m in (plain, marine, general, charcoal, inbuilt):
        cal, prob = get_curve(m['curve_id']).calibrate(m['age_bp'], m['error_1sigma'], m.get('delta_r'),
                                                       m.get('delta_r_error'))
        peak = int(np.argmax(prob))
        # A charcoal date's offset runs one way only: its context is younger.
        # Moved the other way, past where the kernel reaches, its smeared
        # likelihood is zero, and SciPy's FFT leaves rounding there that the
        # index divides by -- Python scores such a date at 6e36.
        shifts = (0.0, -40.0, -400.0) if m.get('outlier_model') == 'charcoal' else (0.0, 40.0, 400.0)
        for shift in shifts:
            ages = cal[max(0, peak - 120):peak + 120] + shift
            # A modelled posterior: the calibration narrowed and moved.
            modelled = np.column_stack([ages, prob[max(0, peak - 120):peak + 120] ** 2])
            for scale in ((None, 2.0) if m.get('outlier_prior') else (None,)):
                cases.append(capture(f'agreement_index/{m["id"]}/{shift}/{scale}',
                                     {'measurement': m, 'modelled': modelled, 'log_scale': scale},
                                     lambda m=m, modelled=modelled, scale=scale:
                                         {'index': ag.agreement_index(m, modelled, log_scale=scale)}))
        draws = rng.normal(float(cal[peak]), 25.0, 300).round(1)
        scales = rng.choice(ec_scale_nodes(m), 300) if m.get('outlier_prior') else None
        cases.append(capture(f'date_agreement/{m["id"]}', {'measurement': m, 'draws': draws, 'scales': scales},
                             lambda m=m, draws=draws, scales=scales:
                                 {'index': ag.date_agreement(m, draws, scales)}))
    unmodelled = np.column_stack([np.arange(2400.0, 2600.0), np.exp(-0.5 * ((np.arange(2400.0, 2600.0) - 2500.0) / 20.0) ** 2)])
    modelled = np.column_stack([np.arange(2450.0, 2550.0, 2.0), np.exp(-0.5 * ((np.arange(2450.0, 2550.0, 2.0) - 2490.0) / 10.0) ** 2)])
    cases.append(capture('agreement_index/supplied', {'measurement': plain, 'modelled': modelled,
                                                      'unmodelled': unmodelled},
                         lambda: {'index': ag.agreement_index(plain, modelled, unmodelled)}))
    cases.append(capture('agreement_index/too-short', {'measurement': plain, 'modelled': [[2450.0, 1.0]]},
                         lambda: {'index': ag.agreement_index(plain, [[2450.0, 1.0]])}))
    indices = [[100.0, 95.0, 120.0], [55.0, None, 0.0, 130.0], [], [None], [87.3]]
    cases.append(capture('overall_agreement', {'indices': indices, 'extra': [0, 1, 2]},
                         lambda: {'overall': [[ag.overall_agreement(i, e) for e in (0, 1, 2)] for i in indices]}))
    cases.append(capture('combination_threshold', {'n': list(range(0, 12))},
                         lambda: {'threshold': [ag.combination_threshold(n) for n in range(0, 12)]}))
    measurements = [plain, marine, general, charcoal, inbuilt]
    cases.append(capture('outlier_scale_terms', {'measurements': measurements},
                         lambda: {'terms': ag.outlier_scale_terms(measurements)}))
    centres = {'p': 2480.0, 'm': 3030.0, 'g': 3200.0, 'c': 3220.0, 'i': 3150.0}
    draws = {k: rng.normal(v, 15.0, 250).round(1) for k, v in centres.items()}
    scales = {'general': rng.choice(np.linspace(0.0, 4.0, 24), 250),
              'charcoal': rng.choice(np.linspace(0.0, 3.0, 24), 250)}
    for label, sc in [('marginalised', None), ('sampled', scales)]:
        cases.append(capture(f'model_agreement/{label}', {'measurements': measurements, 'draws': draws,
                                                          'scales': sc},
                             lambda sc=sc: {'index': ag.model_agreement(measurements, draws, sc)}))
    return cases


def ec_scale_nodes(m):
    from app.services.exact_chain import scale_nodes
    return scale_nodes(m.get('outlier_model') or 'general')


def units_wiggle_match():
    from app.services import model_tree as mt
    from app.services import wiggle_match as wm
    cases = []
    tree, dates = _hand_models()['wiggle']
    variants = {
        'mixed-gaps': (tree, dates),
        'counted': (tree, [{**d, 'ring_offset_error': None} for d in dates]),
        'limited': ({**tree, 'tpq_age_bp': 2700.0}, [{**d, 'taq_age_bp': 2300.0} for d in dates]),
        'flagged': (tree, [{**dates[0], 'outlier_prior': 0.05}] + dates[1:]),
        'off-the-curve': (tree, [{**d, 'ring_offset': 54990.0 + 10 * i} for i, d in enumerate(dates)]),
        'wrong-gaps': (tree, [{**d, 'age_bp': [2480.0, 9000.0, 2455.0, 2560.0][i], 'error_1sigma': 15.0}
                              for i, d in enumerate(dates)]),
    }
    for name, (tree, dates) in variants.items():
        def run(tree=tree, dates=dates):
            group = mt.plan_model(tree, dates).wiggle_groups[0]
            solved = wm.solve(dates, group, root=tree, num_samples=SOLVE_SAMPLES)
            return {**solved, 'reference': _posterior(solved['reference']),
                    'measurements': [[k, _posterior(v)] for k, v in solved['measurements'].items()]}
        cases.append(capture(f'solve/{name}', {'tree': tree, 'dates': dates}, run))
    for step, sigma in [(1.0, 3.0), (1.0, 0.3), (2.5, 10.0)]:
        cases.append(capture(f'gap_kernel/{step}/{sigma}', {'step': step, 'sigma': sigma},
                             lambda step=step, sigma=sigma: {'kernel': wm._gap_kernel(step, sigma)}))
    return cases



def units_influence():
    """
    services/influence.py on a tree built to cross each of its rules: a Phase
    either side of a Boundary and of a Hiatus, a Phase whose neighbour is not
    a plain Phase, one outside any Sequence, and dates in a wiggle match and
    with an outlier prior already at one.
    """
    from app.services import influence
    def el(eid, etype, name=None, children=()):
        return {'id': eid, 'element_type': etype, 'name': name, 'children': list(children)}
    tree = el('root', 'sequence', 'Site', [
        el('b1', 'boundary', 'Start'),
        el('pA', 'phase', 'Phase A'),
        el('b2', 'boundary', 'A to B'),
        el('pB', 'phase', 'Phase B'),
        el('h1', 'hiatus', 'Gap'),
        el('pC', 'phase', None),
        el('pD', 'phase', 'Nested', [el('pD1', 'phase', 'Inner')]),
        el('w1', 'wiggle_sequence', 'Timber'),
        el('b3', 'boundary', 'End'),
    ])
    def m(mid, element, name=None, prior=None, model=None):
        return {'id': mid, 'sample_name': name, 'element_id': element, 'age_bp': 3000.0,
                'error_1sigma': 25.0, 'outlier_prior': prior, 'outlier_model': model}
    dates = [m('d1', 'pA', 'A-1'), m('d2', 'pA', None, 0.05, 'charcoal'), m('d3', 'pB', 'B-1'),
             m('d4', 'pC', 'C-1', 1.0), m('d5', 'pD1', 'Inner-1'), m('d6', 'w1', 'W-1'),
             m('d7', None, 'Loose'), m('d8', 'pB', '')]
    cases = []
    for kinds in (['leave_out', 'move', 'outlier'], ['move'], ['outlier', 'leave_out']):
        def run(kinds=kinds):
            variants = influence.plan(dates, tree, kinds)
            return {'variants': variants,
                    'applied': [[{k: d.get(k) for k in ('id', 'element_id', 'outlier_prior', 'outlier_model')}
                                 for d in influence.apply(v, dates)] for v in variants]}
        cases.append(capture(f"plan/{'+'.join(kinds)}", {'tree': tree, 'measurements': dates, 'kinds': kinds}, run))
    for phase in ('pA', 'pB', 'pC', 'pD', 'pD1', 'missing', 'root'):
        cases.append(capture(f'neighbouring_phases/{phase}', {'tree': tree, 'phase_id': phase},
                             lambda phase=phase: {k: (v or {}).get('id') for k, v in
                                                  influence.neighbouring_phases(tree, phase).items()}))
    rng = np.random.default_rng(13)
    long = [[float(x), float(y)] for x, y in zip(np.linspace(2400.3, 3100.7, 523), rng.random(523))]
    unsorted = [[3.0, 0.2], [1.0, 0.1], [2.0, 0.4]]
    for label, series in [('long', long), ('unsorted', unsorted), ('one', [[1.0, 1.0]]), ('none', None)]:
        cases.append(capture(f'compact_distribution/{label}', {'distribution': series},
                             lambda series=series: {'compact': influence.compact_distribution(series)}))
    boundaries = {
        'b1': {'name': 'Start', 'posterior': {'posterior_median': 3050.5, 'hpd_95': [3120.0, 2990.0],
                                              'probability_distribution': long[:200]}},
        'b2': {'name': 'A to B', 'posterior': {'posterior_median': None}},
        'b3': {'name': 'End', 'posterior': {'posterior_median': 2800.0, 'hpd_95': None,
                                            'probability_distribution': [[2800.0, 1.0]]}},
    }
    block = {'durations': [
        {'kind': 'phase_duration', 'id': 'phase:b1:b2', 'element_id': None, 'name': 'Phase A',
         'years': {'median': 40.0, 'hpd_95': [10.0, 90.0], 'distribution': long[:150]}},
        {'kind': 'sample_duration', 'id': 'sample:pB', 'element_id': 'pB', 'name': 'Phase B',
         'years': {'median': None}},
    ]}
    cases.append(capture('summarise', {'boundaries': boundaries, 'intervals': block},
                         lambda: influence.summarise(boundaries, block)))
    return cases


# ---------------------------------------------------------------------------
# units/ -- Phase 4: reading files in
# ---------------------------------------------------------------------------

CORPUS_OWNER = 'corpus-owner'

DATE_CSVS = {
    'basic-with-phases': ('site.csv',
        'name,age,error,curve,phase,material\n'
        'A-1,3000,25,intcal20,Pit 12,charcoal\n'
        'A-2,3050,30,IntCal 20,Pit 12,\n'
        'B-1,2900,25,SHCal20.14c,Layer 3,bone\n'),
    'tab-separated': ('tab.tsv', 'sample\tbp\tsd\nX-1\t1200\t20\nX-2\t1250\t20\n'),
    'semicolon': ('excel.csv', 'Lab code;CRA;±1σ\nE-1;4,100;35\nE-2;4150;35\n'),
    'missing-required': ('m.csv', 'name,age\nA,3000\n'),
    'unknown-and-duplicate': ('u.csv', 'name,age,error,eror,sigma,colour\nA,3000,25,1,2,red\n'),
    'bad-cells': ('b.csv',
        'name,age,error,delta_r,delta_r_error\n'
        ',3000,25,,\n'
        'B,,25,,\n'
        'C,3000,,,\n'
        'D,three thousand,25,,\n'
        'E,±3000,+/-25,~-50,40\n'
        'F,−120,25,,\n'),
    'curves': ('c.csv', 'name,age,error,curve\nA,3000,25,IntCal13\nB,3000,25,=Marine20\nC,3000,25,\n'),
    'kinds': ('k.csv',
        'name,age,error,type,outlier_model,outlier_prior,inbuilt_shape,inbuilt_years\n'
        'A,3000,25,lake,general,5%,uniform,50\n'
        'B,3000,25,marine,stratigraphic,0.1,,\n'
        'C,3000,25,terrestrial,charcoal,120%,cone,\n'
        'D,3000,25,,,2,exponential,20\n'
        'E,3000,25,,,-0.5,,\n'),
    'coordinates': ('xy.csv',
        'name,age,error,lat,lon\n'
        'A,3000,25,54°30\',-5.6\n'
        'B,3000,25,54.5,5 36\n'
        'C,3000,25,"54,5",-5.6\n'
        'D,3000,25,N54.5,-5.6\n'
        'E,3000,25,54.5 deg,-5.6\n'),
    'coordinate-validation': ('xy2.csv',
        'name,age,error,lat,lon\n'
        'A,3000,25,0,0\n'
        'B,3000,25,95,10\n'
        'C,3000,25,10,\n'),
    'results-export': ('results.csv',
        'Order,Row Type,Depth,Sample Name,Age BP,Error (1σ),Posterior Mean,95.4% HPD,95% HPD,TPQ BP (BC/AD),Phase\n'
        '1,sequence,0,Site,,,,,,,\n'
        '2,phase,1,Pit,,,,,,,\n'
        '3,date,2,A-1,3000,25,2700,1,1,1,Site > Pit\n'
        '4,boundary,1,Start,,,,,,,\n'),
    'depth-without-row-type': ('d.csv', 'name,age,error,depth\nA,3000,25,120\n'),
    'delta-r': ('dr.csv',
        'name,age,error,curve,delta_r,delta_r_error,delta_r_source\n'
        'A,4000,30,marine20,50,,\n'
        'B,4000,30,marine20,50,20,"{""method"": ""nearest_n"", ""n"": 3}"\n'
        'C,4000,30,marine20,50,20,not json\n'
        'D,4000,30,marine20,50,20,[1]\n'),
    'repeated-names': ('r.csv', 'name,age,error\nA,3000,25\na,3010,25\nB,3020,25\n'),
    'some-unnamed-phases': ('p.csv', 'name,age,error,phase\nA,3000,25,Pit\nB,3010,25,\nC,3020,25,Pit\n'),
    'empty': ('e.csv', ''),
    'whitespace-only': ('w.csv', '  \n \n'),
    'header-only': ('h.csv', 'name,age,error\n'),
    'bom-and-crlf': ('bom.csv', '﻿name,age,error\r\nA,3000,25\r\n\r\nB,3010,25\r\n'),
    'quoting': ('q.csv',
        'name,age,error,notes\n'
        'A,3000,25,"has, a comma"\n'
        'B,3010,25,"two\nlines"\n'
        'C,3020,25,"say ""hi"""\n'
        'D,3030,25,mid"quote\n'
        'E,3040,25,"open quote never closed\n'),
    'ragged': ('rag.csv', 'name,age,error\nA,3000,25,extra,more\nB,3010\n'),
    'carriage-returns': ('cr.csv', 'name,age,error\rA,3000,25\rB,3010,25\r'),
    'mixed-line-endings': ('m.csv', 'name,age,error,notes\r\nA,3000,25,"one\rtwo"\rB,3010,25,\n'),
    'paths-and-limits': ('pl.csv',
        'name,age,error,phase,tpq,taq,ring_offset,ring_offset_error,depth_cm\n'
        'A,3000,25,Hillside > North > Feature J,500,600,,,\n'
        'B,3000,25,Feature J,,,,5,\n'
        'C,3000,25,Feature J,,,10,2,15\n'),
    'odd-numbers': ('n.csv', 'name,age,error\nA,nan,25\nB,inf,25\nC,1_000,25\nD,1e3,2.5e1\nE, 3000 ,25\n'),
    'long-strings': ('l.csv', 'name,age,error,material\nA,3000,25,' + 'x' * 101 + '\n'),
    'too-many': ('t.csv', 'name,age,error\n' + ''.join(f'S{i},{3000 + i},25\n' for i in range(501))),
}


def units_date_import():
    from app.services import date_import
    cases = []
    for label, (filename, text) in DATE_CSVS.items():
        cases.append(capture(f'parse_csv/{label}', {'filename': filename, 'text': text},
                             lambda f=filename, t=text: Stable()(
                                 date_import.parse_csv(t, CORPUS_OWNER, f).as_dict())))
    # Cells at and past the csv reader's field limit, written as a recipe
    # (head + fill * count + tail) rather than 128 KB of text.
    long_cells = {
        'cell-at-the-limit': ('name,age,error,location\nA,3000,25,', 'y', 131072, '\n'),
        'quote-never-closes': ('name,age,error,notes\nA,3000,25,"see below\nB,3010,25,', 'x', 131072, '\n'),
    }
    for label, (head, fill, count, tail) in long_cells.items():
        text = head + fill * count + tail
        cases.append(capture(f'parse_csv/{label}',
                             {'filename': 'long.csv', 'head': head, 'fill': fill, 'count': count, 'tail': tail},
                             lambda t=text: Stable()(date_import.parse_csv(t, CORPUS_OWNER, 'long.csv').as_dict())))
    uploads = {
        'utf8-sig.csv': '﻿name,age,error\nA,3000,25\n'.encode('utf-8'),
        'latin.csv': 'name,age,error,notes\nA,3000,25,caf\xe9 \u20ac sample\n'.encode('cp1252'),
        'undecodable.csv': b'name,age,error\nA,3000,25,\x81\xff\n',
        'table.txt': b'name,age,error\nSequence(3),3000,25\n',
        'notes.csv': b'name,age,error,notes\nA,3000,25,see Phase( 2 )\n',
    }
    for filename, content in uploads.items():
        def run(filename=filename, content=content):
            result = date_import.parse_upload(filename, content, CORPUS_OWNER)
            return Stable()(result.as_dict())
        cases.append(capture(f'parse_upload/{filename}', {'filename': filename, 'hex': content.hex()}, run))
    headers = ['Sample Name', '± 1σ', 'ΔR', '95.4% HPD', '  Lab-No. ', '', 'C14_Age (BP)', 'δ13C ‰']
    cases.append(capture('normalise_header', {'headers': headers},
                         lambda: {'keys': [date_import.normalise_header(h) for h in headers]}))
    texts = ['Plot()\n{\n R_Date("A",3000,25);\n};', 'name,Sequence\nA,1', '// Sequence(\nname,age',
             'Options(){ };', 'R_Simulate ("x", 1000, 20);', 'Curve("IntCal20","IntCal20.14c");']
    cases.append(capture('looks_like_cql2', {'texts': texts},
                         lambda: {'cql2': [date_import.looks_like_cql2(t) for t in texts]}))
    cases.append(capture('template_csv', {}, lambda: {'csv': date_import.template_csv(),
                                                      'columns': date_import.COLUMN_ALIASES}))
    return cases

# The stored curves the corpus owner has, for the OxCal reader's Curve() and
# Mix_Curves(): one mixture it can match, one archived, which a file may still
# name but the curve lists offered back leave out of date_import's.
CORPUS_LIBRARY = [
    {'id': 'corpus-diet', 'name': 'Corpus diet', 'aliases': ['Diet mix'], 'archived': False,
     'recipe': {'a': 'intcal20', 'b': 'marine20', 'percent_a': 30.0, 'percent_a_error': 10.0}},
    {'id': 'corpus-old', 'name': 'Old shell mix', 'aliases': [], 'archived': True,
     'recipe': {'a': 'shcal20', 'b': 'marine20', 'percent_a': 50.0, 'percent_a_error': 0.0}},
]


def _store_corpus_library():
    """CORPUS_LIBRARY as rows the registry reads, owned by CORPUS_OWNER."""
    from app.database import SessionLocal, init_db
    from app.models import StoredCalibrationCurve, User
    from app.services import curve_registry
    from app.services.curve_mix import mix
    from app.services.calibration import get_curve
    init_db()
    with SessionLocal() as db:
        if db.get(User, CORPUS_OWNER) is None:
            db.add(User(id=CORPUS_OWNER, email='corpus-owner@radical.example.com',
                        password_hash='-', display_name='Corpus owner'))
        for i, row in enumerate(CORPUS_LIBRARY):
            if db.get(StoredCalibrationCurve, row['id']) is not None:
                continue
            r = row['recipe']
            curve = mix(get_curve(r['a']), get_curve(r['b']), r['percent_a'], r['percent_a_error'])
            db.add(StoredCalibrationCurve(
                id=row['id'], kind='mixed', name=row['name'], recipe=r, aliases=row['aliases'],
                archived=row['archived'], content_sha256=curve_registry.content_hash(curve),
                type='terrestrial', sample_type='terrestrial', created_by=CORPUS_OWNER,
                created_at=datetime.datetime(2026, 1, 1, 0, 0, i)))
        db.commit()
    curve_registry.invalidate()


def units_oxcal_script():
    """parse_oxcal over every script the import tests read, and the fixtures."""
    from app.services import oxcal_script, date_import
    _store_corpus_library()
    cases = [capture('library', {}, lambda: {'curves': CORPUS_LIBRARY})]
    with open(os.path.join(HERE, 'inputs', 'oxcal_scripts.json'), encoding='utf-8') as fh:
        scripts = json.load(fh)
    for i, entry in enumerate(scripts):
        if 'fixture' in entry:
            with open(os.path.join(ROOT, entry['fixture']), encoding='utf-8') as fh:
                text = fh.read()
        else:
            text = entry['text']
        cases.append(capture(f'parse_oxcal/{i:03d}', entry,
                             lambda t=text, f=entry['filename']: Stable()(
                                 oxcal_script.parse_oxcal(t, CORPUS_OWNER, f).as_dict())))
    # Files the reader once raised on rather than refused.
    edges = {
        'ends-in-a-call': 'R_Date(',
        'ends-in-an-assignment': 'Options(){ Resolution=',
        'resolution-inf': 'Options(){ Resolution="inf"; }; Phase("P"){ R_Date("a",3000,25); };',
        'resolution-minus-inf': 'Options(){ Resolution="-inf"; }; Phase("P"){ R_Date("a",3000,25); };',
        'resolution-nan': 'Options(){ Resolution="nan"; }; Phase("P"){ R_Date("a",3000,25); };',
    }
    for label, text in edges.items():
        cases.append(capture(f'parse_oxcal/{label}', {'filename': 't.oxcal', 'text': text},
                             lambda t=text: Stable()(
                                 oxcal_script.parse_oxcal(t, CORPUS_OWNER, 't.oxcal').as_dict())))
    uploads = {
        'model.oxcal': b'Plot(){ R_Date("a",3000,25); }',
        'model.txt': b'Plot(){ Phase("P"){ R_Date("a",3000,25); R_Date("b",3010,25); }; };',
        'dates.csv': b'name,age,error,phase\nA,3000,25,Sequence\n',
        'notes.txt': b'Just some prose about the site.\n',
        'model.OXCAL': b'Phase("A"){ Tau_Boundary("T"); R_Date("a",3000,25); };',
    }
    for filename, content in uploads.items():
        cases.append(capture(f'parse_upload/{filename}', {'filename': filename, 'hex': content.hex()},
                             lambda f=filename, c=content: Stable()(
                                 date_import.parse_upload(f, c, CORPUS_OWNER).as_dict())))
    big = b'x' * (date_import.MAX_CONTENT_BYTES + 1)
    cases.append(capture('parse_upload/big.csv', {'filename': 'big.csv', 'repeat': 'x',
                                                  'length': len(big)},
                         lambda: Stable()(date_import.parse_upload('big.csv', big, CORPUS_OWNER).as_dict())))
    return cases


def _export_extra_cases():
    """Export inputs the tests do not reach: the omissions and odd names."""
    def m(name, age, err, element=None, **kw):
        return {'sample_name': name, 'age_bp': age, 'error_1sigma': err, 'element_id': element, **kw}
    tree = {'id': 'e-root', 'element_type': 'sequence', 'name': 'Say "hi"\n*/ now',
            'tpq_age_bp': 5000.0, 'taq_age_bp': None, 'children': [
                {'id': 'e-start', 'element_type': 'boundary', 'name': None, 'children': []},
                {'id': 'e-a', 'element_type': 'phase', 'name': 'A', 'taq_age_bp': 100.5, 'children': []},
                {'id': 'e-gap', 'element_type': 'hiatus', 'name': 'Gap */', 'children': []},
                {'id': 'e-odd', 'element_type': 'interpolated_depth', 'name': 'Loose', 'children': []},
                {'id': 'e-w', 'element_type': 'wiggle_sequence', 'name': None, 'children': []},
                {'id': 'e-p', 'element_type': 'deposition_sequence', 'name': 'Core',
                 'params': {'k_variable': True, 'log_k_min': -1.5, 'log_k_max': 0.25}, 'children': [
                     {'id': 'e-l', 'element_type': 'interpolated_depth', 'name': 'Level 47',
                      'params': {'depth_cm': 47.0}, 'children': []}]},
                {'id': 'e-end', 'element_type': 'boundary', 'name': 'End', 'children': []}]}
    dates = [
        m('a1', 3000.0, 25.0, 'e-a', outlier_prior=0.05, outlier_model='CHARCOAL', tpq_age_bp=3500.0,
          inbuilt_years=50.0),
        m('a2', 3010.0, 25.0, 'e-a', outlier_prior=0.1, outlier_model='mystery', curve_id='marine20',
          delta_r=-120.0, delta_r_error=30.0, inbuilt_years=20.0, inbuilt_shape='exponential'),
        m(None, 3020.0, 25.0, 'e-a', curve_id='marine20', delta_r=-120.0, delta_r_error=None),
        m('w1', 3100.0, 20.0, 'e-w', ring_offset=0.0, ring_offset_error=5.0),
        m('w2', 3150.0, 20.0, 'e-w', ring_offset=40.0, ring_offset_error=3.0),
        m('w3', 3190.0, 20.0, 'e-w', ring_offset=80.0, ring_offset_error=4.0),
        m('p1', 4000.0, 30.0, 'e-p', depth_cm=10.0, curve_id='corpus-diet'),
        m('p2', 4400.0, 30.0, 'e-p', depth_cm=90.0, delta_r=12.5, delta_r_error=1e-7),
        m('loose', 2000.0, 20.0, 'e-nowhere'),
        m('unplaced', 2100.0, 20.0, None),
    ]
    return [
        {'name': 'Edges', 'analysis_type': 'bayesian', 'measurements': dates, 'tree': tree,
         'config': {'resolution': 2.0, 'uniform_span_prior': False}, 'app_commit': 'abc'},
        {'name': 'Edges as dates', 'analysis_type': 'group_calibration', 'measurements': dates,
         'tree': tree, 'config': {'resolution': 2}, 'app_commit': None},
        {'name': 'Fixed k', 'analysis_type': 'bayesian', 'measurements': [
            m('q1', 4000.0, 30.0, 'e-p', depth_cm=None), m('q2', 4100.0, 30.0, 'e-p', depth_cm=5.0)],
         'tree': {'id': 'e-p', 'element_type': 'deposition_sequence', 'name': '', 'params': {'k': 0.5},
                  'children': [{'id': 'e-l', 'element_type': 'interpolated_depth', 'name': 'L',
                                'params': {}, 'children': []}]},
         'config': None, 'app_commit': None},
    ]


def units_oxcal_export():
    """write_oxcal over the inputs the export tests make, and each result read
    back by parse_oxcal and written again -- the round trip."""
    from app.services import oxcal_export, oxcal_script

    class Fixed(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.datetime(2026, 1, 1, tzinfo=tz)

    oxcal_export.datetime = Fixed
    _store_corpus_library()
    with open(os.path.join(HERE, 'inputs', 'oxcal_exports.json'), encoding='utf-8') as fh:
        inputs = json.load(fh) + _export_extra_cases()
    cases = []
    for i, entry in enumerate(inputs):
        def run(e=entry):
            text, omitted = oxcal_export.write_oxcal(e['name'], e['analysis_type'], e['measurements'],
                                                     e['tree'], e['config'], e['app_commit'])
            back = oxcal_script.parse_oxcal(text, CORPUS_OWNER, 'export.oxcal')
            again = None
            if back.ok:
                again, _ = oxcal_export.write_oxcal(
                    e['name'], 'bayesian' if back.model_tree is not None else 'group_calibration',
                    back.dates, back.model_tree,
                    {'resolution': back.resolution} if back.resolution else {}, e['app_commit'])
            return {'text': text, 'omitted': omitted, 'reimported_ok': back.ok, 'reexported': again}
        cases.append(capture(f'write_oxcal/{i:03d}', entry, run))
    return cases


CURVE_TEXTS = {
    'bc-ad-header': ('bc_ad', None, 'year,c14,sigma\n-500,2400,20\n1,1950,15\n1000,1000,15\n'),
    'bc-ad': ('bc_ad', None, '-500,2400,20\n-1,1960,15\n1,1950,15\n1000,1000,15\n1950,10,5\n'),
    'whitespace-tab': ('cal_bp', None, '# title\n0 100 10\n10   110\t11\n20 120 12\n'),
    'whitespace': ('cal_bp', None, '# title\n  0 100 10\n10   110  11 extra\n20 120\n30\u3000130 13\n'),
    'tabs': ('cal_bp', None, 'cal\tc14\terr\n0\t100\t10\n10\t110\t11\n'),
    'columns-given': ('cal_bp', {'cal_bp': 2, 'c14_age': 0, 'error': 1}, 'c,e,cal\n100,10,0\n110,11,10\n'),
    'reordered-header': ('cal_bp', None, 'Sigma,Cal BP,14C Age\n10,0,100\n11,10,110\n'),
    'missing-column': ('cal_bp', None, 'cal_bp,age,foo\n0,100,1\n'),
    'duplicates': ('cal_bp', None, ''.join(f'{y},{100 + y},5\n{y},{101 + y},5\n' for y in range(0, 70, 10))),
    'f14c': ('cal_bp', None, '0,1.0,0.01\n10,0.99,0.01\n'),
    'descending': ('cal_bp', None, '20,120,5\n10,110,5\n0,100,5\n'),
    'unsorted': ('cal_bp', None, '10,110,5\n0,100,5\n20,120,5\n'),
    'too-few': ('cal_bp', None, '0,100,5\n'),
    'comments-only': ('cal_bp', None, '# only\n\n   \n# this\n'),
    'bad-rows': ('cal_bp', None, '0,100,5\n10,abc,5\n20,120\n30,130,0\n-5,90,5\nnan,1,1\n40,140,-1e-3\n'),
    'many-bad': ('cal_bp', None, '0,1,5\n' + ''.join(f'{y},x,5\n' for y in range(1, 40))),
    'quoted-header': ('cal_bp', None, '"cal BP","14C age","error"\n"0","100","5"\n"10","110","5"\n'),
    'line-breaks': ('cal_bp', None, '0,100,5\r\n10,110,5\r20,120,5\x0c30,130,5\u202840,140,5\x1c50,150,5\n'),
    'suggestions': ('cal_bp', None, '# Marine 20 calibration curve\n#  Heaton et al., 2020 Radiocarbon\n'
                    '#  SHCal-13 is older\n0,100,5\n10,110,5\n'),
    'numbers': ('cal_bp', None, ' 1_0 , +100 , 5e0 \n20,1.2e2,.5\n'),
}


def _points_digest(points):
    text = '\n'.join(f'{a!r},{b!r},{c!r}' for a, b, c in points)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def units_curve_import():
    from app.services import curve_import
    cases = []

    def answer(result):
        out = result.as_dict()
        out['points_sha256'] = _points_digest(result.points)
        if len(result.points) <= 40:
            out['points'] = [list(p) for p in result.points]
        return out

    with open(os.path.join(HERE, 'inputs', 'curve_imports.json'), encoding='utf-8') as fh:
        harvested = json.load(fh)
    for i, entry in enumerate(harvested):
        def run(e=entry):
            if e['kind'] == 'parse_curve':
                if 'file' in e:
                    with open(os.path.join(ROOT, e['file']), 'rb') as fh:
                        text = fh.read().decode(e['encoding'])
                else:
                    text = e['text']
                return answer(curve_import.parse_curve(text, e['calendar'], e['columns']))
            if 'file' in e:
                with open(os.path.join(ROOT, e['file']), 'rb') as fh:
                    content = fh.read()
            else:
                content = bytes.fromhex(e['hex'])
            return answer(curve_import.parse_upload(e['filename'], content, e['calendar']))
        cases.append(capture(f'harvested/{i:03d}', entry, run))
    for label, (calendar, columns, text) in CURVE_TEXTS.items():
        cases.append(capture(f'parse_curve/{label}', {'text': text, 'calendar': calendar, 'columns': columns},
                             lambda t=text, c=calendar, k=columns: answer(curve_import.parse_curve(t, c, k))))
    head, fill, count, tail = '0,100,5\n10,110,5\n20,"', 'x', 131073, '",5\n'
    cases.append(capture('parse_curve/cell-past-the-limit',
                         {'head': head, 'fill': fill, 'count': count, 'tail': tail,
                          'calendar': 'cal_bp', 'columns': None},
                         lambda: answer(curve_import.parse_curve(head + fill * count + tail))))
    uploads = {
        'path/to/Old Curve.v2.csv': '# Ünïcode header\n0,100,5\n10,110,5\n'.encode('latin-1'),
        'C:\\curves\\bom.14c': '\ufeff0,100,5\n10,110,5\n'.encode('utf-8'),
        '': b'0,100,5\n10,110,5\n',
        'noext': b'# IntCal 13 rows\n0,100,5\n10,110,5\n',
    }
    for filename, content in uploads.items():
        cases.append(capture(f'parse_upload/{filename or "unnamed"}',
                             {'filename': filename, 'hex': content.hex(), 'calendar': 'cal_bp'},
                             lambda f=filename, c=content: answer(curve_import.parse_upload(f, c))))
    points = [(0.0, 100.0, 5.0), (10.5, 110.25, 5.125), (123456789.0, 1e-7, 3.0)]
    for header in ('', 'Two lines\n\nof header'):
        cases.append(capture(f'to_14c_text/{len(header)}', {'points': points, 'header': header},
                             lambda h=header: {'text': curve_import.to_14c_text(points, h)}))
    return cases


def units_reservoir_ingest():
    """The ingest's parsing, end to end over the captured calib.org response,
    and each stage's edges on its own. Nothing here touches a network."""
    from dataclasses import asdict
    import html as html_module
    from app.services import reservoir, reservoir_ingest as ri
    fixtures = os.path.join(FIXTURES, 'calib_marine')
    cases = []

    def rows_json(rows):
        return [asdict(r) for r in rows]

    def pipeline(raw):
        records, declared = ri.extract_records(raw)
        rows, report = ri.records_to_rows(records, declared=declared)
        text = ri.rows_to_csv(rows)
        back = ri.rows_from_csv(text) if rows else []
        return {'declared': declared, 'record_count': len(records), 'rows': rows_json(rows),
                'report': report.as_dict(), 'content_sha256': ri.content_hash(rows),
                'csv': text, 'csv_round_trip': back == sorted(rows, key=lambda r: r.map_no)}

    with open(os.path.join(fixtures, 'query_response.html'), 'rb') as fh:
        query = fh.read()
    with open(os.path.join(fixtures, 'freshness_response.html'), 'rb') as fh:
        fresh = fh.read()
    def digested(raw):
        # The fixture's rows quote the source's citations verbatim; held by
        # digest, so the corpus does not repeat them. rows_to_csv writes every
        # field, so the digest is as exact as the rows.
        out = pipeline(raw)
        del out['rows']
        out['csv_sha256'] = hashlib.sha256(out.pop('csv').encode('utf-8')).hexdigest()
        return out

    cases.append(capture('pipeline/fixture', {'file': 'backend/tests/fixtures/calib_marine/query_response.html'},
                         lambda: digested(query)))
    cases.append(capture('freshness/fixture', {'file': 'backend/tests/fixtures/calib_marine/freshness_response.html'},
                         lambda: ri.extract_freshness(fresh).as_dict()))

    head = '<tr>' + ''.join(f'<td>{c}</td>' for c in ri.EXPECTED_COLUMNS) + '</tr>\n'

    def row(**cells):
        values = {c: '' for c in ri.EXPECTED_COLUMNS}
        values.update(cells)
        return '<tr>\n' + ''.join(f'<TD>{values[c]}</td>' for c in ri.EXPECTED_COLUMNS) + '\n</TR>\n'

    pages = {
        'no-table': b'<html>nothing</html>',
        'no-rows': b'3 records<table border>\n</table>',
        'wrong-header': ('<table  border >' + head.replace('<td>lon</td><td>lat</td>', '<td>lat</td><td>LON\xff</td>')
                         + row(mapno=1, lat=1, lon=1)).encode('latin-1'),
        'short-row': ('<table border>' + head + '<tr><td>1</td><td>2</td></tr>').encode(),
        'declared-mismatch': ('7\n RECORDS <table border>' + head + row(mapno=1, lat=50, lon=-5)).encode(),
        'small': ('2 records<table border>' + head
                  + row(mapno=10, lat='54.5', lon='183.5', deltar='-50', deltarerr='&nbsp;',
                        locality='Caf\xe9 &amp; bar', feeding='\xa0Filter', last_changed='2024-02-29 23:59:59')
                  + row(mapno=9, lat='-12', lon='-181', deltar='20', deltarerr='30', feeding='New habit',
                        notes='&#x41;&#128;&#0;&#xD800;&#1;&#x110000;&ampx &notit; &unknown;',
                        last_changed='2024-02-30')).encode('latin-1')
                 + b'',
    }
    for label, raw in pages.items():
        cases.append(capture(f'pipeline/{label}', {'hex': raw.hex()}, lambda r=raw: pipeline(r)))
    probes = {
        'no-table': b'none',
        'no-count': b'<table border><tr><td>max(last_changed)</td><td>2020-01-01</td></tr>',
        'bad-count': b'<table border><tr><td>COUNT(*) </td><td>many</td></tr>',
        'no-stamp': b'<table border><tr><td>count(*)</td><td> 12 </td></tr><tr><td>max(last_changed)</td><td>soon</td></tr>',
    }
    for label, raw in probes.items():
        cases.append(capture(f'freshness/{label}', {'hex': raw.hex()},
                             lambda r=raw: ri.extract_freshness(r).as_dict()))

    fields = [b'plain', 'H\xe5kansson'.encode('utf-8'), 'Thybor\xf8n'.encode('cp1252'),
              b'\x81\x8d bad \\ \' "', b"it's", b'', b'\x00\t\n\r\x7f']
    cases.append(capture('decode_field', {'hex': [f.hex() for f in fields]},
                         lambda: {'decoded': [list(ri.decode_field(f)) for f in fields]}))

    def records_case(records, declared=None):
        rows, report = ri.records_to_rows(records, declared=declared)
        return {'rows': rows_json(rows), 'report': report.as_dict(), 'content_sha256': ri.content_hash(rows)}

    base = {'mapno': '1', 'lat': '50', 'lon': '-5'}
    record_sets = {
        'problems': [
            {'mapno': '1', 'lat': ' ', 'lon': 'NULL'},
            {'mapno': 'x\xa0', 'lat': '1', 'lon': '2'},
            {'mapno': '2.9', 'lat': '91', 'lon': '0'},
            dict(base),
            dict(base, lat='10'),
            {'mapno': '3', 'lat': '-', 'lon': '--'},
        ],
        'values': [
            dict(base, deltar=' 12.5 ', deltarerr='NULL', collectionyear='1931.7', c14age='1e3',
                 reference='  ', species='null', feeding='DEPOSIT OR SUSPENSION', last_changed='1999-12-31'),
            dict(base, mapno='2', deltar='-', feeding='Unheard of', last_changed='2000-1-2 3:4:5'),
            dict(base, mapno='3', feeding=None, last_changed='2000-01-02T03:04:05'),
            dict(base, mapno='4', lon='-540', feeding='unheard OF ', last_changed=' 2001-06-30 12:00:00 '),
        ],
        'empty': [],
        'years-that-are-not': [dict(base, mapno=str(i + 1), collectionyear=y)
                               for i, y in enumerate(['inf', '-inf', 'nan', 'NaN', ' 1931.9 '])],
    }
    for label, records in record_sets.items():
        cases.append(capture(f'records_to_rows/{label}', {'records': records},
                             lambda r=records: records_case(r)))
    cases.append(capture('records_to_rows/declared', {'records': [base], 'declared': 2},
                         lambda: records_case([base], 2)))

    header = ','.join(ri.CSV_COLUMNS)
    csvs = {
        'empty': '',
        'blank-header': '\n1,2\n',
        'wrong-header': 'a,b\n',
        'header-only': header + '\n\n , \n',
        'ragged': header + '\n1,2,3\n',
        'bad-map': header + '\nx,1,2' + ',' * 15 + '\n',
        'bad-lat': header + '\n1,north,2' + ',' * 15 + '\n',
        'nan-year': header + '\n' + ','.join(['1', '1', '2'] + [''] * 9 + ['nan'] + [''] * 5) + '\n',
        'inf-year': header + '\n' + ','.join(['1', '1', '2'] + [''] * 9 + ['inf'] + [''] * 5) + '\n',
        'minus-inf-year': header + '\n' + ','.join(['1', '1', '2'] + [''] * 9 + ['-inf'] + [''] * 5) + '\n',
        'good': (' ' + header.replace(',', ' ,') + '\n 1_0 ,+1.5,-2e1,NULL,-,"a, b",,,,,,,1931.9,,,,'
                 'suspension,Filter\n'),
    }
    for label, text in csvs.items():
        cases.append(capture(f'rows_from_csv/{label}', {'text': text},
                             lambda t=text: {'rows': rows_json(ri.rows_from_csv(t))}))

    entities = ['&nbsp;x', 'a &amp b', '&AMP;&lt&gt;', '&#65;&#x42;&#X43', '&#128;&#159;&#0;',
                '&#xd800;&#1114112;&#99999999999999999999;', '&#1;&#11;&#xfffe;', '&notin;&notit;&noti',
                '&ampamp;', '&#;&#x;&;', '& lone', '&' + 'a' * 40 + ';', '&ZeroWidthSpace;&zwnj;',
                '&acE;']
    cases.append(capture('html_unescape', {'texts': entities},
                         lambda: {'unescaped': [html_module.unescape(t) for t in entities]}))
    stamps = ['2024-02-29 23:59:59', '2023-02-29', '2024-1-2', '2024-01-02  3:04:05', '2024-01-02 24:00:00',
              '2024-01-02 23:59:60', '0000-01-01', '24-01-01', '2024-01-02 03:04', 'NULL', ' 2024-01-02 ',
              '2024-01- 2', '2024-13-01', '2024-12-31 23:59:59 ']
    cases.append(capture('parse_timestamp', {'texts': stamps}, lambda: {'parsed': [
        None if (d := ri._parse_timestamp(t)) is None else d.isoformat() for t in stamps]}))
    longitudes = [180.0, -180.0, 183.5, -181.0, 540.0, -540.0, 1e6, 359.999]
    cases.append(capture('wrap_longitude', {'values': longitudes},
                         lambda: {'wrapped': [ri._wrap_longitude(v) for v in longitudes]}))
    cases.append(capture('snapshot_id', {'slug': 'calib-marine', 'stamp': '2026-05-11T10:00:00'},
                         lambda: {'id': ri.snapshot_id('calib-marine', datetime.datetime(2026, 5, 11, 10))}))
    spellings = [None, '', ' Filter ', 'NULL', 'deposit or suspension feeder', 'Carnivore/Scavenger',
                 'algal grazer', 'Nonsense', '\xa0Herbivore']
    cases.append(capture('feeding', {'spellings': spellings}, lambda: {
        'normalised': [reservoir.normalise_feeding(s) for s in spellings],
        'recognised': [reservoir.is_recognised_feeding(s) for s in spellings]}))
    return cases


def _recipe_draws(recipe):
    """Joint draws from a recipe both sides can replay: one shared shift per
    draw (what makes them JOINT), then each parameter's own scatter."""
    rng = np.random.default_rng(recipe['seed'])
    common = rng.normal(0.0, recipe['common_sd'], recipe['j'])
    return {pid: mean + common + rng.normal(0.0, sd, recipe['j'])
            for pid, mean, sd in recipe['params']}


def _dispersal_scenarios():
    from app.services.reservoir import haversine_km
    out = {}

    def front(name, positions, ages, sd=30.0, common=50.0, j=240, seed=11, origin=None):
        sites = [{'element_id': f'e{i}', 'name': f'Site {i}', 'parameter_id': f'b{i}',
                  'basis': 'boundary', 'latitude': lat, 'longitude': lon}
                 for i, (lat, lon) in enumerate(positions)]
        recipe = {'seed': seed, 'j': j, 'common_sd': common,
                  'params': [[f'b{i}', age, sd] for i, age in enumerate(ages)]}
        out[name] = {'sites': sites, 'recipe': recipe, 'origin': origin}

    eq = [(0.0, 1.5 * (i + 1)) for i in range(8)]
    front('equator-east', eq, [7500.0 - haversine_km(0, 0, 0, lon) for _lat, lon in eq], origin=[0.0, 0.0])
    front('equator-reversed', eq, [5000.0 + 0.5 * haversine_km(0, 0, 0, lon) for _lat, lon in eq],
          origin=[0.0, 0.0], seed=12)
    front('exact', eq, [7500.0 - haversine_km(0, 0, 0, lon) / 2.0 for _lat, lon in eq],
          sd=0.0, common=0.0, j=50, origin=[0.0, 0.0])
    grid = []
    for i in range(3):
        for k in range(3):
            lat = 50.0 + 80.0 * i / 111.32
            lon = 80.0 * k / (111.32 * math.cos(math.radians(lat)))
            grid.append((lat, lon))
    bearing = math.radians(60.0)
    front('grid-60', grid, [6500.0 - (80.0 * k * math.sin(bearing) + 80.0 * i * math.cos(bearing)) / 1.2
                            for i in range(3) for k in range(3)], sd=20.0, common=40.0, j=200, seed=5,
          origin=[49.0, -2.0])
    pacific = [(-18.0, 177.0), (-14.0, 179.0), (-20.0, -179.5), (-15.0, -177.0), (-21.5, -175.0),
               (-12.0, -172.5), (-17.0, -170.0)]
    front('pacific', pacific, [3200.0 - 0.6 * haversine_km(-18.0, 177.0, lat, lon) for lat, lon in pacific],
          sd=25.0, common=30.0, j=160, seed=21, origin=[-19.0, 176.0])
    front('five-sites', eq[:5], [7000.0, 6900.0, 6800.0, 6700.0, 6600.0], origin=[0.0, 0.0])
    circle = [(10.0 * math.cos(math.radians(a)), 10.0 * math.sin(math.radians(a))) for a in (0, 60, 120, 180, 240, 300)]
    front('one-circle', circle, [6000.0] * 6, origin=[0.0, 0.0], seed=3)
    meridian = [(40.0 + 0.7 * i, 10.0) for i in range(7)]
    front('meridian', meridian, [5000.0 - 30.0 * i for i in range(7)], origin=[39.0, 10.0], seed=4)
    return out


def units_dispersal():
    """The spatial models over replayable joint draws, and the draw-free
    pieces over every input the dispersal tests use."""
    from app.services import dispersal as d
    cases = []

    def site(spec):
        return d.SiteSpec(**{**spec, 'date_ids': tuple(spec.get('date_ids', ()))})

    for name, sc in _dispersal_scenarios().items():
        sites = [site(s) for s in sc['sites']]
        draws = _recipe_draws(sc['recipe'])
        origin = tuple(sc['origin']) if sc['origin'] else None
        inputs = {'sites': sc['sites'], 'recipe': sc['recipe'], 'origin': sc['origin']}
        cases.append(capture(f'fit_wave/{name}', inputs, lambda s=sites, dr=draws, o=origin: d.fit_wave(s, dr, o)))
        cases.append(capture(f'fit_trend/{name}', inputs, lambda s=sites, dr=draws: d.fit_trend(s, dr)))
        cases.append(capture(f'profile_origin/{name}', inputs,
                             lambda s=sites, dr=draws: d.profile_origin(s, dr)))
    refused = [{'element_id': 'bad', 'name': 'Bad', 'parameter_id': 'x', 'basis': 'boundary',
                'refusal': 'two places'}]
    for fn in ('fit_wave', 'fit_trend', 'profile_origin'):
        call = {'fit_wave': lambda: d.fit_wave([site(refused[0])], {}, (0.0, 0.0)),
                'fit_trend': lambda: d.fit_trend([site(refused[0])], {}),
                'profile_origin': lambda: d.profile_origin([site(refused[0])], {})}[fn]
        cases.append(capture(f'{fn}/refused-site', {'sites': refused, 'recipe': None, 'origin': [0.0, 0.0]}, call))

    clusters = [('a1', 50.0, 1.0, 6000.0), ('a2', 50.01, 1.02, 6100.0), ('b1', 51.0, 2.0, 5800.0),
                ('b2', 51.02, 2.01, 5900.0), ('c1', 52.0, 3.0, 5600.0), ('lone', None, None, 5000.0),
                ('nodraws', 50.0, 1.0, None)]
    measurements = [{'id': i, 'sample_name': None if i == 'b1' else f'S-{i}', 'latitude': lat, 'longitude': lon}
                    for i, lat, lon, _age in clusters]
    recipe = {'seed': 9, 'j': 101, 'common_sd': 10.0,
              'params': [[i, age, 25.0] for i, _lat, _lon, age in clusters if age is not None]}
    cases.append(capture('derive_sites_from_dates', {'measurements': measurements, 'recipe': recipe},
                         lambda: {'sites': [s.as_dict() for s in
                                            d.derive_sites_from_dates(measurements, _recipe_draws(recipe))]}))

    with open(os.path.join(HERE, 'inputs', 'dispersal.json'), encoding='utf-8') as fh:
        harvested = json.load(fh)
    for i, e in enumerate(harvested):
        kind = e['kind']
        if kind == 'derive_sites':
            fn = lambda e=e: {'sites': [s.as_dict() for s in d.derive_sites(e['root'], e['measurements'])]}
        elif kind == 'propose_sequence':
            fn = lambda e=e: d.propose_sequence([site(s) for s in e['sites']],
                                                tuple(e['origin']) if e['origin'] else None)
        elif kind == 'lon_extent':
            fn = lambda e=e: {'extent': list(d._lon_extent(e['lons']))}
        elif kind == 'unproject':
            fn = lambda e=e: {'point': list(d._unproject(e['x'], e['y'], tuple(e['centre'])))}
        else:
            fn = lambda e=e: {'step': d._round_step(e['raw'])}
        cases.append(capture(f'{kind}/{i:03d}', e, fn))
    return cases


def schema_sqlite():
    """The schema the backend's migrations build on SQLite, table by table:
    what radical-store's own schema is held to."""
    from app.database import engine, init_db
    init_db()
    cases = []
    with engine.connect() as conn:
        raw = conn.connection.dbapi_connection
        tables = [r[0] for r in raw.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
        for table in tables:
            columns = [{'name': c[1], 'type': c[2], 'notnull': bool(c[3]), 'default': c[4], 'pk': c[5]}
                       for c in raw.execute(f'pragma table_info("{table}")')]
            foreign = [{'column': f[3], 'references': f[2], 'to': f[4], 'on_delete': f[6]}
                       for f in raw.execute(f'pragma foreign_key_list("{table}")')]
            indexes = []
            for ix in raw.execute(f'pragma index_list("{table}")'):
                if ix[3] != 'c':          # only declared indexes, not the PK's own
                    continue
                cols = [c[2] for c in raw.execute(f'pragma index_info("{ix[1]}")')]
                indexes.append({'columns': cols, 'unique': bool(ix[2])})
            cases.append({'name': table, 'inputs': {},
                          'outputs': {'columns': columns, 'foreign_keys': foreign, 'indexes': indexes}})
    return cases


# Numeric arrays longer than this are recorded as a summary in the exchange
# corpus: the units families hold the numbers themselves to the bit, and the
# exchanges are about routes, statuses, fields and shapes.
EXCHANGE_ARRAY_LIMIT = 64


def summarise_long_arrays(value: Any) -> Any:
    """Every all-numeric (possibly nested) list with more than
    EXCHANGE_ARRAY_LIMIT numbers in it, as {'$summary': len, count, min, max,
    sum}. radical-server's replay test summarises its own answers the same way."""
    def numbers(v, out):
        if isinstance(v, bool):
            return False
        if isinstance(v, (int, float)):
            out.append(float(v))
            return True
        if isinstance(v, list):
            return all(numbers(x, out) for x in v)
        return False

    if isinstance(value, list):
        flat: List[float] = []
        if value and numbers(value, flat) and len(flat) > EXCHANGE_ARRAY_LIMIT:
            return {'$summary': {'len': len(value), 'count': len(flat), 'min': min(flat),
                                 'max': max(flat), 'sum': math.fsum(flat)}}
        return [summarise_long_arrays(v) for v in value]
    if isinstance(value, dict):
        return {k: summarise_long_arrays(v) for k, v in value.items()}
    return value


def _readable_uploads(files: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """An uploaded zip as its members' text, as a zip response is kept, rather
    than as base64: the replay builds the zip again, and the text is what the
    names check can read."""
    if not files:
        return files
    out = {}
    for field, upload in files.items():
        raw = base64.b64decode(upload.get('b64') or '')
        if raw[:4] != b'PK\x03\x04':
            out[field] = upload
            continue
        members = {}
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for name in archive.namelist():
                members[name] = archive.read(name).decode('utf-8')
        out[field] = {'filename': upload.get('filename'), 'zip': members}
    return out


def api_exchanges(recording: str) -> List[Dict[str, Any]]:
    """The backend's own API tests as HTTP exchanges (corpus/record_api.py), in
    the order they ran, ids placeholdered across the whole run and the
    credentials reduced to whose they were."""
    with open(recording, encoding='utf-8') as fh:
        raw = json.load(fh)
    suite = Counter(x['auth'] for x in raw if x['auth']).most_common(1)[0][0]
    stable = Stable()
    cases = []
    for i, x in enumerate(raw):
        who = 'none' if x['auth'] is None else 'suite' if x['auth'] == suite else 'other'
        inputs = {k: x.get(k) for k in ('test', 'cleanup', 'method', 'url', 'params',
                                        'request_json', 'request_data', 'request_content',
                                        'files')}
        inputs['files'] = _readable_uploads(inputs['files'])
        inputs['auth'] = who
        outputs = {'status': x['status'], 'content_type': x['content_type']}
        for k in ('response_json', 'response_text', 'response_zip', 'response_sha256',
                  'response_len'):
            if k in x:
                outputs[k] = summarise_long_arrays(x[k]) if k == 'response_json' else x[k]
        case = stable({'name': f"{i:04d} {x['method']} {x['url'].split('?')[0]}",
                       'inputs': inputs, 'outputs': outputs})
        cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# api/ -- Phases 2, 3 and 5
# ---------------------------------------------------------------------------

# Database ids, and the random ids the importer gives each date (d-...) and
# model element (e-...) -- alone, or inside a composite id such as
# 'phase:e-...:e-...'.
ID = re.compile(r'(?<![0-9A-Za-z])(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
                r'|[a-z]-[0-9a-f]{12})(?![0-9A-Za-z])')
# Fields that differ between two identical runs.
VOLATILE = {'created_at', 'updated_at', 'started_at', 'completed_at', 'finished_at',
            'queued_at', 'elapsed_seconds', 'duration_seconds', 'runtime_seconds',
            'compute_seconds', 'wall_seconds', 'last_run_at', 'seconds',
            'app_commit', 'exported_at'}


class Stable:
    """Replaces ids with placeholders in order of first appearance, and drops times."""

    def __init__(self):
        self.ids: Dict[str, str] = {}

    def __call__(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {self(k): self(v) for k, v in value.items() if k not in VOLATILE}
        if isinstance(value, list):
            return [self(v) for v in value]
        if isinstance(value, str):
            return ID.sub(self._placeholder, value)
        return value

    def _placeholder(self, match: re.Match) -> str:
        return self.ids.setdefault(match.group(0), f'<id:{len(self.ids) + 1}>')


def api_client():
    from fastapi.testclient import TestClient
    from app.database import init_db
    from app.main import app
    init_db()
    bare = TestClient(app)
    token = bare.post('/api/auth/register', json={
        'email': 'corpus@radical.example.com', 'password': 'a corpus password',
        'display_name': 'Corpus'}).json()['data']['token']
    return TestClient(app, headers={'Authorization': f'Bearer {token}'})


def _ok(response) -> Dict[str, Any]:
    assert response.status_code == 200, f'{response.status_code}: {response.text[:500]}'
    return response.json()


def api_calibrate(client):
    cases = []
    for curve_id, age, err, dr, dre in [('intcal20', 3000, 25, None, None),
                                        ('shcal20', 2450, 40, None, None),
                                        ('marine20', 4000, 30, 120.0, 60.0)]:
        body = {'sample_name': f'C-{age}', 'age_bp': age, 'error_1sigma': err,
                'curve_id': curve_id, 'save': False}
        if dr is not None:
            body.update(delta_r=dr, delta_r_error=dre, sample_type='marine')
        cases.append({'name': f'calibrate/{curve_id}/{age}', 'inputs': {'request': body},
                      'outputs': {'response': Stable()(_ok(client.post('/api/analysis/calibrate', json=body)))}})
    body = {'name': 'Group', 'save': False, 'estimate_durations': True, 'estimate_kde': True,
            'measurements': [{'id': f'g{i}', 'sample_name': f'G-{i}', 'age_bp': a, 'error_1sigma': 25,
                              'curve_id': 'intcal20'} for i, a in enumerate([2900, 2950, 3000, 3050, 3100])]}
    cases.append({'name': 'calibrate-group/intcal20/five', 'inputs': {'request': body},
                  'outputs': {'response': Stable()(_ok(client.post('/api/analysis/calibrate-group', json=body)))}})
    return cases


def _oxcal_fixtures() -> List[str]:
    found = []
    for folder in sorted(os.listdir(FIXTURES)):
        path = os.path.join(FIXTURES, folder)
        if os.path.isdir(path):
            found += [os.path.join(path, f) for f in sorted(os.listdir(path)) if f.endswith('.oxcal')]
    return found


def _point_at_analysis(request: Dict[str, Any], response: Dict[str, Any],
                       analysis: Dict[str, Any]) -> None:
    """Replace a saved run's POST `results` by $from_analysis, having checked it loses nothing."""
    results = response['results']
    # The POST keys results by the request's date ids, the GET by the ids the
    # dates were stored under; both keep the request's order.
    sent, stored = request['measurements'], analysis['measurements']
    assert [m['sample_name'] for m in sent] == [m['sample_name'] for m in stored]
    stored_id = {a['id']: b['id'] for a, b in zip(sent, stored)}
    by_id = {entry['sample_id']: entry for entry in analysis['results']}
    fields = sorted(next(iter(results.values())))
    for client_id, entry in results.items():
        assert sorted(entry) == fields, f'{client_id}: results differ in shape'
        assert all(entry[f] == by_id[stored_id[client_id]][f] for f in fields), \
            f'{client_id}: the POST and GET results disagree'
    response['results'] = {'$from_analysis': {
        'ids': [[client_id, stored_id[client_id]] for client_id in results],
        'fields': fields}}


def api_models(client):
    """Every .oxcal fixture: imported, then run through each solver it can take."""
    cases = []
    for path in _oxcal_fixtures():
        rel = os.path.relpath(path, FIXTURES)
        with open(path, 'rb') as fh:
            content = fh.read()
        imported = _ok(client.post('/api/import/dates', files={
            'file': (os.path.basename(path), io.BytesIO(content), 'text/plain')}))['data']
        if not imported.get('ok'):
            cases.append({'name': f'import/{rel}', 'inputs': {'fixture': rel},
                          'outputs': {'import': Stable()(imported)}})
            continue
        for solver in ('auto', 'gibbs'):
            body = {
                'name': os.path.basename(path)[:-len('.oxcal')],
                # Saved once, so the analysis can be read back the way the
                # Results page reads it; the second solver's run is not, as
                # saving it would store the same model element ids twice.
                'save': solver == 'auto',
                'measurements': [{k: v for k, v in d.items() if k != 'phase_name'}
                                 for d in imported['dates']],
                'model_tree': imported['model_tree'],
                'bayesian_config': {'solver': solver, 'seed': SEED, 'estimate_durations': True},
            }
            # The file's own Resolution=, as the importer reports it. Without
            # it the *_res1 fixtures would run as their five-year twins.
            if imported.get('resolution'):
                body['bayesian_config']['resolution'] = imported['resolution']
            print(f'    {rel} [{solver}]', flush=True)
            response = client.post('/api/analysis/bayesian?wait=true', json=body)
            stable = Stable()
            case = {'name': f'bayesian/{rel}/{solver}',
                    'inputs': {'fixture': rel, 'import': stable(imported), 'request': stable(body)}}
            if response.status_code != 200:
                case['error'] = {'status': response.status_code, 'body': stable(response.json())}
            else:
                data = response.json()['data']
                case['outputs'] = {'response': stable(response.json())}
                if body['save']:
                    analysis = stable(_ok(client.get(f"/api/analysis/{data['id']}")))
                    case['outputs']['analysis'] = analysis
                    _point_at_analysis(case['inputs']['request'], case['outputs']['response']['data'],
                                       analysis['data'])
            cases.append(case)
    return cases


# ---------------------------------------------------------------------------

def _sha256(path: str) -> str:
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _corpus_files() -> List[str]:
    return [os.path.join(d, f) for d, _, files in os.walk(OUT) for f in files if f.endswith('.json')]


def _git(*args) -> str:
    try:
        return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return ''


def main() -> None:
    np.seterr(all='ignore')
    print('units/')
    for family, build in [
        ('units/f14c', units_f14c), ('units/linspace', units_linspace),
        ('units/cell_edges', units_cell_edges),
        ('units/interpolation', units_interpolation), ('units/likelihood', units_likelihood),
        ('units/calibrate', units_calibrate), ('units/curve_mix', units_curve_mix),
        ('units/resolution', units_resolution), ('units/same_event', units_same_event),
        ('units/reservoir', units_reservoir), ('units/date_format', units_date_format),
        ('units/rng', units_rng), ('units/kde', units_kde),
        ('units/curve_view', units_curve_view), ('units/curve_registry', units_curve_registry),
        ('units/influence', units_influence),
        ('units/date_import', units_date_import), ('units/oxcal_script', units_oxcal_script),
        ('units/oxcal_export', units_oxcal_export), ('units/curve_import', units_curve_import),
        ('units/reservoir_ingest', units_reservoir_ingest), ('units/dispersal', units_dispersal),
        ('units/model_tree', units_model_tree), ('units/stratigraphy', units_stratigraphy),
        ('units/intervals', units_intervals), ('units/exact_chain', units_exact_chain),
        ('units/exact_parallel', units_exact_parallel), ('units/agreement', units_agreement),
        ('units/wiggle_match', units_wiggle_match),
    ]:
        write(family, build())

    write('schema/sqlite', schema_sqlite())

    if '--exchanges' in sys.argv:
        # A recording made by corpus/record_api.py over the backend suite.
        write('api/exchanges', api_exchanges(sys.argv[sys.argv.index('--exchanges') + 1]))

    if '--units-only' not in sys.argv:
        print('api/')
        client = api_client()
        write('api/calibrate', api_calibrate(client))
        write('api/models', api_models(client))

    manifest = {
        'schema': SCHEMA,
        'generated_from': {
            'commit': _git('rev-parse', 'HEAD'),
            'dirty': bool(_git('status', '--porcelain', '--', 'backend', 'calibration_data')),
            'python': platform.python_version(),
            'numpy': np.__version__,
            'scipy': scipy.__version__,
        },
        'seed': SEED,
        'curves': {cid: {'file': f'calibration_data/{f}', 'sha256': _sha256(os.path.join(CURVES_DIR, f))}
                   for cid, f in CURVE_FILES.items()},
        # Every file present, not only those written now: --units-only leaves
        # the api/ files as they were, and they stay listed.
        'files': {os.path.relpath(p, HERE).replace(os.sep, '/'): _sha256(p)
                  for p in sorted(_corpus_files())},
    }
    with open(os.path.join(HERE, 'manifest.json'), 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
        fh.write('\n')
    print('manifest.json')
    os.remove(_DB_PATH)


if __name__ == '__main__':
    main()
