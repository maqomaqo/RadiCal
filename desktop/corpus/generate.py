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
           the measured half of the curve registry.

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
from typing import Any, Callable, Dict, List

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
    try:
        return {'name': name, 'inputs': inputs, 'outputs': fn()}
    except ValueError as exc:
        return {'name': name, 'inputs': inputs, 'error': {'type': 'ValueError', 'message': str(exc)}}


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
            'compute_seconds', 'wall_seconds', 'last_run_at', 'seconds'}


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
    ]:
        write(family, build())

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
