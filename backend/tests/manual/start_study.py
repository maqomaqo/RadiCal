"""
Study: how a multi-chain run should START, judged by whether the diagnostics
catch a sampler that has missed a calibration lobe.

Not part of the suite -- it runs for minutes to hours. The question and the
decision rule are below.

    python tests/manual/start_study.py run   --out study.jsonl [--models ...]
    python tests/manual/start_study.py cost  --out cost.jsonl
    python tests/manual/start_study.py report study.jsonl [cost.jsonl]

Arms (gibbs.START_STRATEGIES): 'quantile' is the shared-quantile start alone
(the behaviour before lobe-aware starts); 'position', 'calendar', 'equalised'
and 'random' are gibbs.Model.start_lobes' strategies.

Faults, each a way of making the sampler wrong that the diagnostics should see:
  none        the sampler as it is -- measures false alarms and accuracy
  ties        Boundaries allowed to share a cell again (the bug that put every
              chain in a late lobe at R-hat 1.001)
  slowmix     almost no wide proposals, no peak-gap jumps, no enclosure moves:
              chains that cross between lobes rarely, so where they start
              decides much of where they end
  enclosure   only the enclosure move removed -- the defect that gave lobe
              weights 0.27 against the exact 0.40; parallel models only
  singlesite  single-site updates only, no translation moves at all
  nojumps     (not in the default set) no peak-gap jumps, no enclosure moves

Truth is cross_check.reference(): the direct integration of the same model,
so only models it can integrate (chains) have it. A run is WRONG when its
worst Boundary is more than cross_check.DISAGREEMENT (0.15) from truth in total
variation. A diagnostic FLAGS a run when R-hat exceeds 1.05, or when the worst
between-chain total variation, or the worst between-chain lobe-mass gap (how
far any chain's share of any lobe of a Boundary is from the pool's), exceeds a
threshold taken from the fault-free runs (the 95th percentile, so those two are
held to the same 5% false-alarm rate). Detection is P(flag | wrong).

Decision rule (set before looking): adopt an arm over 'quantile' only if it
detects every fault at least as often, is no less accurate within seed noise,
and needs no more than ~10% more sweeps to converge.
"""
import argparse
import contextlib
import itertools
import json
import multiprocessing
import os
import sys
import time
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
for name, filename in (('INTCAL_CURVE_PATH', 'intcal20.14c'),
                       ('MARINE_CURVE_PATH', 'marine20.14c'),
                       ('SHCAL_CURVE_PATH', 'shcal20.14c')):
    os.environ.setdefault(name, os.path.join(ROOT, 'calibration_data', filename))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, '..', '..')))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, '..')))

from app.services import calibration, cross_check, exact_chain, gibbs  # noqa: E402

ARMS = gibbs.START_STRATEGIES
FAULTS = ('none', 'ties', 'slowmix', 'enclosure', 'singlesite')
CHAINS = 4
RHAT_GATE = gibbs.TARGET_RHAT
WRONG = cross_check.DISAGREEMENT
# A start counts as IN a lobe when it stands this far up the lobe's peak, which
# is stricter than being inside its basin: the far tail of a basin is not where
# a chain would be taken to have found the lobe.
STRICT_HEIGHT = 0.5

_IDS = itertools.count()


# -- models -----------------------------------------------------------------

def _element(kind, name, children=None):
    return {'id': f's{next(_IDS)}', 'element_type': kind, 'name': name,
            'tpq_age_bp': None, 'taq_age_bp': None, 'children': children or []}


def _date(element_id, age_bp, error, curve_id):
    tag = f'sd{next(_IDS)}'
    return {'id': tag, 'sample_name': tag, 'age_bp': float(age_bp),
            'error_1sigma': float(error), 'curve_id': curve_id,
            'element_id': element_id, 'material_type': 'charcoal',
            'sample_type': 'terrestrial'}


# Stretches of IntCal20 (cal BP) with wiggles and plateaus, where a date is
# likely to calibrate to several lobes.
REGIONS = [(2350, 2800), (500, 750), (950, 1250), (3000, 3300), (350, 560)]


def generated(index):
    """A chain of phases separated by Boundaries, ages simulated from the curve
    at known calendar dates. Deterministic in `index`."""
    rng = np.random.default_rng(10_000 + index)
    curve = calibration.get_curve('intcal20')
    low, high = REGIONS[index % len(REGIONS)]
    age = rng.uniform(low + 150, high)
    children, measurements = [_element('boundary', 'B0')], []
    for i in range(int(rng.integers(3, 7))):
        width = rng.uniform(15, 60)
        phase = _element('phase', f'P{i}')
        for _ in range(int(rng.integers(2, 5))):
            true = age - rng.uniform(0, width)
            error = float(rng.choice([20, 25, 30]))
            c14 = float(np.interp(true, curve.cal_ages, curve.c14_ages)
                        + rng.normal(0, error))
            measurements.append(_date(phase['id'], c14, error, 'intcal20'))
        children += [phase, _element('boundary', f'B{i + 1}')]
        age -= width + rng.uniform(0, 40)
    return measurements, _element('sequence', f'Generated {index}', children)


def parallel(index):
    """A site's Start and End around two or three areas' Sequences, each of one
    or two phases, the areas overlapping in time. Deterministic in `index`."""
    rng = np.random.default_rng(20_000 + index)
    curve = calibration.get_curve('intcal20')
    low, high = REGIONS[index % len(REGIONS)]
    top = rng.uniform(low + 120, high)
    areas, measurements = [], []
    for a in range(int(rng.integers(2, 4))):
        age = top - rng.uniform(0, 80)
        children = [_element('boundary', f'sA{a}')]
        for i in range(int(rng.integers(1, 3))):
            width = rng.uniform(15, 50)
            phase = _element('phase', f'A{a}P{i}')
            for _ in range(int(rng.integers(2, 4))):
                true = age - rng.uniform(0, width)
                error = float(rng.choice([20, 25, 30]))
                c14 = float(np.interp(true, curve.cal_ages, curve.c14_ages)
                            + rng.normal(0, error))
                measurements.append(_date(phase['id'], c14, error, 'intcal20'))
            children += [phase, _element('boundary', f'm{a}_{i}')]
            age -= width + rng.uniform(0, 25)
        children[-1]['name'] = f'eA{a}'
        areas.append(_element('sequence', f'Area {a}', children))
    tree = _element('sequence', f'Site {index}', [
        _element('boundary', 'Site start'), _element('phase', 'Activity', areas),
        _element('boundary', 'Site end')])
    return measurements, tree


def dune():
    import test_gibbs
    return test_gibbs.TestBoundaryTiesCountHalf()._layers()


def fixture(name):
    module = {'oxcal': 'oxcal_fixture', 'hillside': 'hillside_charcoal_fixture',
              'dunesec': 'dune_section_fixture'}[name]
    mod = __import__(module)
    return mod.measurements(), mod.model_tree()


def build_inputs(key):
    if key == 'dune':
        return dune()
    if key.startswith('gen'):
        return generated(int(key[3:]))
    if key.startswith('par'):
        return parallel(int(key[3:]))
    return fixture(key)


# -- faults -----------------------------------------------------------------

def _allowed_with_ties(self, param, state):
    lo, hi = self.window[param]
    for older in self.below[param]:
        hi = min(hi, state.index[older])
    for younger in self.above[param]:
        lo = max(lo, state.index[younger])
    return lo, hi


@contextlib.contextmanager
def faulted(fault):
    """Apply a fault to the sampler for the duration of the block. Process-local,
    so each job runs in a worker and nothing leaks between them."""
    saved = (gibbs.Model.allowed, gibbs.Model.move_sets, gibbs.Model._with_enclosure,
             gibbs.PEAK_JUMP_FRACTION, gibbs.WIDE_MOVE_FRACTION)
    try:
        if fault == 'ties':
            gibbs.Model.allowed = _allowed_with_ties
        elif fault == 'nojumps':
            gibbs.PEAK_JUMP_FRACTION = 0.0
            gibbs.Model._with_enclosure = lambda self, members: []
        elif fault == 'slowmix':
            gibbs.PEAK_JUMP_FRACTION = 0.0
            gibbs.WIDE_MOVE_FRACTION = 0.02
            gibbs.Model._with_enclosure = lambda self, members: []
        elif fault == 'enclosure':
            gibbs.Model._with_enclosure = lambda self, members: []
        elif fault == 'singlesite':
            gibbs.Model.move_sets = lambda self: []
        yield
    finally:
        (gibbs.Model.allowed, gibbs.Model.move_sets, gibbs.Model._with_enclosure,
         gibbs.PEAK_JUMP_FRACTION, gibbs.WIDE_MOVE_FRACTION) = saved


# -- measures ---------------------------------------------------------------

def _bin(grid, mass, low, count):
    return cross_check._binned(grid, mass / mass.sum(), low, count)


def _tv(a, b):
    return 0.5 * float(np.abs(a - b).sum())


_TRUTH = {}


def _truth(key, measurements, tree):
    """The direct integration, once per model per worker; None for a shape it
    does not cover."""
    if key not in _TRUTH:
        try:
            _TRUTH[key] = cross_check.reference(measurements, tree)
        except Exception:
            _TRUTH[key] = None
    return _TRUTH[key]


def _lobe_pattern(model, state, strict):
    """Which lobe each multi-lobe date sits in (-1 if none, or if `strict` and
    not standing STRICT_HEIGHT up the lobe)."""
    pattern = []
    for date_id in model.date_ids:
        lobes = model.lobes(date_id)
        if not lobes:
            continue
        cell, row, found = state.index[date_id], model._inlier_row(date_id), -1
        for k, (peak, _m, first, last) in enumerate(lobes):
            if first <= cell < last and (not strict or row[cell] >= STRICT_HEIGHT * row[peak]):
                found = k
        pattern.append(found)
    return pattern


def start_quality(model, starts):
    """Strict lobe coverage, and how different the chains' lobe patterns are."""
    states = [model.initial_state(s['start_quantile'], s['start_lobe']) for s in starts]
    multi = [d for d in model.date_ids if model.lobes(d)]
    patterns = [_lobe_pattern(model, s, True) for s in states]
    uncovered, full = [], 0
    for column, date_id in enumerate(multi):
        lobes = model.lobes(date_id)
        seen = {p[column] for p in patterns}
        missed = sum(m for k, (_p, m, _f, _l) in enumerate(lobes) if k not in seen)
        uncovered.append(missed / sum(m for _p, m, _f, _l in lobes))
        full += not missed
    pairs = list(itertools.combinations(range(len(patterns)), 2))
    hamming = (float(np.mean([np.mean(np.array(patterns[a]) != np.array(patterns[b]))
                              for a, b in pairs])) if multi else 0.0)
    return {'multi_lobe_dates': len(multi),
            'dates_fully_covered': full,
            'uncovered_mass': float(np.mean(uncovered)) if uncovered else 0.0,
            'pattern_distance': hamming}, states


# The lobe-occupancy flag of the feature spec (S2): a lobe that holds at least
# this share of a date's likelihood, yet less than OCC_EMPTY of the pooled
# posterior, and that no chain spent more than OCC_EMPTY of its draws in.
OCC_LIKELIHOOD_SHARE = 0.10
OCC_EMPTY = 0.01
# A lobe the exact answer puts at least this much on is a real miss, not a lobe
# the model rules out.
OCC_REAL = 0.05

_DATE_TRUTH = {}


def date_truth(key, measurements, tree):
    """{date id: (calendar ages, mass per age)} from the exact solver, or None
    where the model is not a chain it can solve."""
    if key not in _DATE_TRUTH:
        chain = exact_chain.layout(tree, measurements)
        found = None
        if chain is not None:
            result = exact_chain.solve(measurements, tree, chain, interval_draws=50)
            step = float(result['grid']['step'])
            found = {}
            for date_id, row in result['measurements'].items():
                points = np.array(row['probability_distribution'], dtype=float)
                found[date_id] = (points[:, 0], points[:, 1] * step)
        _DATE_TRUTH[key] = found
    return _DATE_TRUTH[key]


def occupancy(model, histograms, truth):
    """
    The S2 flags over every date with several lobes (Model.lobes): per chain,
    the posterior mass in each lobe; a lobe is UNVISITED when it holds at least
    OCC_LIKELIHOOD_SHARE of the date's likelihood but no chain put more than
    OCC_EMPTY of its draws there; the chains DISAGREE when they do not share a
    dominant lobe. With `truth` (date posteriors from the exact solver), an
    unvisited lobe is REAL when the exact answer puts OCC_REAL or more on it and
    RULED OUT when it puts under OCC_EMPTY, so the flag's two readings can be
    told apart; `lobe_error` is the worst gap between pooled and exact mass.
    """
    out = {'occ_dates': 0, 'occ_unvisited': 0, 'occ_unvisited_real': 0,
           'occ_unvisited_ruled_out': 0, 'occ_disagree': 0, 'occ_lobe_error': None}
    worst = 0.0
    for date_id in model.date_ids:
        lobes = model.lobes(date_id)
        if not lobes:
            continue
        out['occ_dates'] += 1
        per_chain = []
        for chain in histograms:
            row = chain[date_id]
            per_chain.append([float(row[first:last].sum() / row.sum())
                              for _peak, _share, first, last in lobes])
        pooled = np.mean(per_chain, axis=0)
        if len({int(np.argmax(masses)) for masses in per_chain}) > 1:
            out['occ_disagree'] += 1
        for k, (_peak, share, first, last) in enumerate(lobes):
            exact = None
            if truth is not None and date_id in truth:
                ages, mass = truth[date_id]
                low, high = model.grid[first], model.grid[last - 1]
                exact = float(mass[(ages >= low) & (ages <= high)].sum())
                worst = max(worst, abs(float(pooled[k]) - exact))
            if (share >= OCC_LIKELIHOOD_SHARE and pooled[k] < OCC_EMPTY
                    and max(masses[k] for masses in per_chain) <= OCC_EMPTY):
                out['occ_unvisited'] += 1
                if exact is not None:
                    out['occ_unvisited_real'] += exact >= OCC_REAL
                    out['occ_unvisited_ruled_out'] += exact < OCC_EMPTY
    if truth is not None:
        out['occ_lobe_error'] = worst
    return out


_BOUNDARY_LOBES = {}


def boundary_lobes(key, measurements, tree, model):
    """
    The lobes of each Boundary, defined from its own dates and nothing else: a
    Boundary has no likelihood, so each group it bounds is solved ALONE and
    exactly (its Boundaries, its dates, no other stratigraphy, the direct
    calculation of services/cross_check.py), and the Boundary's marginal in each
    such group is a likelihood-implied picture of where it can be. A Boundary
    shared by two groups takes the product of the two, which is where both
    allow it. Mode-complete by construction (no sampler) and independent of the
    answer. Lobes are those of that marginal (Model._lobes_of: peaks holding at
    least LOBE_MIN_MASS), and only a Boundary with two or more has any.

    {boundary id: [(peak cell, mass, first cell, one past the last)]} on the
    model's grid. Groups the direct calculation will not take alone (one date
    leaves a Boundary's tail unbounded) contribute nothing.
    """
    if key in _BOUNDARY_LOBES:
        return _BOUNDARY_LOBES[key]
    by_id = {m['id']: m for m in measurements}
    elements = {}

    def walk(element):
        elements[element['id']] = element
        for child in element.get('children') or []:
            walk(child)

    walk(tree)
    pictures = {}
    for older, younger, dates in model.group_dates:
        if not dates:
            continue
        phase = _element('phase', 'Group')
        group = [dict(by_id[d], element_id=phase['id']) for d in dates]
        alone = _element('sequence', 'Group alone', [
            dict(elements[older], children=[]), phase, dict(elements[younger], children=[])])
        try:
            reference = cross_check.reference(group, alone)
        except Exception:
            continue
        grid = reference['grid']
        for boundary_id in (older, younger):
            density = reference['boundaries'].get(boundary_id)
            if density is None:
                continue
            on_model = np.interp(model.grid, grid, density / (grid[1] - grid[0]),
                                 left=0.0, right=0.0)
            if on_model.sum() > 0:
                pictures.setdefault(boundary_id, []).append(on_model / on_model.sum())
    found = {}
    for boundary_id, arrays in pictures.items():
        row = np.prod(arrays, axis=0) if len(arrays) > 1 else arrays[0]
        if row.sum() < 1e-12:
            row = np.mean(arrays, axis=0)
        lobes = gibbs.Model._lobes_of(row / row.sum())
        if len(lobes) >= 2:
            found[boundary_id] = lobes
    _BOUNDARY_LOBES[key] = found
    return found


def boundary_occupancy(model, histograms, lobes_by_boundary, truth):
    """
    The S2 flags over Boundaries, with lobes from boundary_lobes: UNVISITED when
    a lobe holding at least OCC_LIKELIHOOD_SHARE of the Boundary's likelihood-
    implied picture gets no more than OCC_EMPTY of any chain's draws, and the
    chains DISAGREE when they do not share a dominant lobe. With the direct
    answer, an unvisited lobe is REAL when that answer puts OCC_REAL or more on
    it, RULED OUT under OCC_EMPTY.

    Separately, `occb_truth_missed` counts Boundaries whose DIRECT answer has a
    lobe of OCC_LIKELIHOOD_SHARE or more that the pooled run gave under
    OCC_EMPTY: how many wrong runs are missed-lobe failures at all, whatever
    lobes a user could have known about beforehand.
    """
    out = {'occb_boundaries': len(lobes_by_boundary), 'occb_unvisited': 0,
           'occb_unvisited_real': 0, 'occb_unvisited_ruled_out': 0,
           'occb_disagree': 0, 'occb_truth_missed': 0}

    def per_chain(boundary_id, lobes):
        rows = []
        for chain in histograms:
            row = chain[boundary_id]
            rows.append([float(row[first:last].sum() / row.sum())
                         for _peak, _share, first, last in lobes])
        return rows

    for boundary_id, lobes in lobes_by_boundary.items():
        masses = per_chain(boundary_id, lobes)
        pooled = np.mean(masses, axis=0)
        if len({int(np.argmax(m)) for m in masses}) > 1:
            out['occb_disagree'] += 1
        density = truth['boundaries'].get(boundary_id) if truth else None
        for k, (_peak, share, first, last) in enumerate(lobes):
            if (share >= OCC_LIKELIHOOD_SHARE and pooled[k] < OCC_EMPTY
                    and max(m[k] for m in masses) <= OCC_EMPTY):
                out['occb_unvisited'] += 1
                if density is not None:
                    exact = float(density[first:last].sum())
                    out['occb_unvisited_real'] += exact >= OCC_REAL
                    out['occb_unvisited_ruled_out'] += exact < OCC_EMPTY
    if truth:
        for boundary_id, density in truth['boundaries'].items():
            for _peak, share, first, last in gibbs.Model._lobes_of(density):
                pooled = sum(float(h[boundary_id][first:last].sum() / h[boundary_id].sum())
                             for h in histograms) / len(histograms)
                if share >= OCC_LIKELIHOOD_SHARE and pooled < OCC_EMPTY:
                    out['occb_truth_missed'] += 1
    return out


def drift(model, starts, states, sweeps, seed):
    """Share of multi-lobe dates still in the lobe they started in after burn-in."""
    kept = []
    for c, (start, state) in enumerate(zip(starts, states)):
        chain = gibbs._Chain(model, sweeps, seed + c, None, sweeps, 0,
                             start['start_quantile'], start['start_lobe'])
        before = _lobe_pattern(model, chain.state, False)
        chain.burn(chain.burn_in)
        after = _lobe_pattern(model, chain.state, False)
        if before:
            kept.append(float(np.mean(np.array(before) == np.array(after))))
    return float(np.mean(kept)) if kept else None


_INPUTS = {}


def _inputs(key):
    """A model's measurements and tree, built once per worker."""
    if key not in _INPUTS:
        _INPUTS[key] = build_inputs(key)
    return _INPUTS[key]


def job(args):
    key, arm, fault, seed, sweeps, want_drift = args
    started = time.perf_counter()
    try:
        measurements, tree = _inputs(key)
        truth = _truth(key, measurements, tree)
        dates_exact = date_truth(key, measurements, tree)
        grid = truth['grid'] if truth else None
        with faulted(fault):
            model = gibbs.Model(measurements, tree, grid=grid)
            if fault == 'ties':
                model.edge_gap[:] = 0
            starts = gibbs.chain_starts(CHAINS, seed, arm)
            quality, states = start_quality(model, starts)
            runs = [gibbs.run(model, sweeps=sweeps, max_sweeps=sweeps, seed=seed + c,
                              target_convergence=1.01, **start)
                    for c, start in enumerate(starts)]
            kept = drift(model, starts, states, sweeps, seed) if want_drift else None
        histograms = [r['histograms'] for r in runs]
        _overlap, rhat = gibbs._between_chains(histograms, list(model.params))

        record = {'model': key, 'arm': arm, 'fault': fault, 'seed': seed,
                  'sweeps': sweeps, 'rhat': rhat, 'drift_kept': kept,
                  'has_truth': truth is not None, 'has_date_truth': dates_exact is not None,
                  **quality, **occupancy(model, histograms, dates_exact),
                  **boundary_occupancy(model, histograms,
                                       boundary_lobes(key, measurements, tree, model), truth)}
        if truth:
            low = float(truth['grid'][0])
            count = int(np.ceil((truth['grid'][-1] - low) / cross_check.REPORTING_STEP_YEARS)) + 1
            worst, between, lobe_gap = 0.0, 0.0, 0.0
            for boundary_id, density in truth['boundaries'].items():
                ours = _bin(truth['grid'], density, low, count)
                pooled = sum(h[boundary_id] for h in histograms)
                binned_pool = _bin(model.grid, pooled, low, count)
                worst = max(worst, _tv(binned_pool, ours))
                chains = [_bin(model.grid, h[boundary_id], low, count) for h in histograms]
                between = max(between, max(_tv(a, b) for a, b in itertools.combinations(chains, 2)))
                for _peak, share, first, last in gibbs.Model._lobes_of(binned_pool):
                    lobe_gap = max([lobe_gap] + [abs(float(c[first:last].sum()) - share)
                                                 for c in chains])
            record.update({'tv_truth': worst, 'tv_between': between, 'lobe_gap': lobe_gap})
        record['seconds'] = time.perf_counter() - started
        return record
    except Exception as error:                       # a start that cannot be built is a result
        return {'model': key, 'arm': arm, 'fault': fault, 'seed': seed,
                'error': f'{type(error).__name__}: {error}'[:300]}


# -- commands ---------------------------------------------------------------

def _done(path):
    seen = set()
    if os.path.exists(path):
        for line in open(path):
            r = json.loads(line)
            seen.add((r['model'], r['arm'], r['fault'], r['seed']))
    return seen


def _starts(key):
    """Whether the sampler can build a starting state for the model on the
    direct integral's grid at all. Some generated parallel models it cannot
    (the walk packs the branches' Boundaries into an outer span too narrow for
    the span correction), and a model nothing can start tells us nothing."""
    try:
        measurements, tree = build_inputs(key)
        truth = cross_check.reference(measurements, tree)
        gibbs.Model(measurements, tree, grid=truth['grid']).initial_state()
        return True
    except Exception:
        return False


def _models(options):
    if options.models:
        return options.models.split(',')
    parallel, index = [], 0
    while len(parallel) < options.parallel and index < 10 * options.parallel:
        if _starts(f'par{index}'):
            parallel.append(f'par{index}')
        index += 1
    return ['dune'] + [f'gen{i}' for i in range(options.generated)] + parallel


def command_run(options):
    models = _models(options)
    seen = _done(options.out)
    jobs = [(m, a, f, 1000 + s, options.sweeps, f == 'none' and options.drift)
            for m in models for f in options.faults.split(',')
            for a in options.arms.split(',') for s in range(options.seeds)
            if (f != 'enclosure' or m.startswith('par'))
            and (m, a, f, 1000 + s) not in seen]
    print(f'{len(jobs)} jobs ({len(seen)} already done)', flush=True)
    with open(options.out, 'a') as out, multiprocessing.Pool(options.workers) as pool:
        for n, record in enumerate(pool.imap_unordered(job, jobs), 1):
            out.write(json.dumps(record) + '\n')
            out.flush()
            if n % 20 == 0 or n == len(jobs):
                print(f'{n}/{len(jobs)}', flush=True)


def command_cost(options):
    """Sweeps and wall time to converge under the production stopping rule."""
    models = _models(options)
    seen = _done(options.out)
    with open(options.out, 'a') as out:
        for m in models:
            measurements, tree = build_inputs(m)
            for a in options.arms.split(','):
                for s in range(options.seeds):
                    if (m, a, 'cost', 1000 + s) in seen:
                        continue
                    started = time.perf_counter()
                    try:
                        r = gibbs.solve_chains(measurements, tree, chains=CHAINS,
                                               seed=1000 + s, start_strategy=a,
                                               sweeps=options.sweeps,
                                               max_sweeps=options.max_sweeps)
                        record = {'model': m, 'arm': a, 'fault': 'cost', 'seed': 1000 + s,
                                  'sweeps_total': r['sweeps'], 'rhat': r['rhat'],
                                  'seconds': time.perf_counter() - started}
                    except Exception as error:
                        record = {'model': m, 'arm': a, 'fault': 'cost', 'seed': 1000 + s,
                                  'error': f'{type(error).__name__}: {error}'[:300]}
                    out.write(json.dumps(record) + '\n')
                    out.flush()
                    print(record, flush=True)


def _load(path):
    return [json.loads(line) for line in open(path)]


def _pct(values, q):
    return float(np.percentile(values, q)) if len(values) else float('nan')


def command_report(options):
    rows = _load(options.results)
    errors = [r for r in rows if 'error' in r]
    rows = [r for r in rows if 'error' not in r]
    arms = [a for a in ARMS if any(r['arm'] == a for r in rows)]
    print(f'{len(rows)} runs, {len(errors)} errors')
    for e in errors[:5]:
        print('  ERROR', e['model'], e['arm'], e['fault'], e['error'])
    truthful = [r for r in rows if r.get('has_truth')]
    clean = [r for r in truthful if r['fault'] == 'none']

    # Gates for the two noisy between-chain measures: the 95th percentile of the
    # fault-free runs, over every arm, so each gives 5% false alarms overall.
    tv_gate = _pct([r['tv_between'] for r in clean], 95)
    gap_gate = _pct([r['lobe_gap'] for r in clean], 95)
    rhat_value = lambda r: float('inf') if r['rhat'] is None else r['rhat']
    flags = {
        'R-hat': lambda r: rhat_value(r) > RHAT_GATE,
        'TV': lambda r: r['tv_between'] > tv_gate,
        'lobe gap': lambda r: r['lobe_gap'] > gap_gate,
        'any': lambda r: (rhat_value(r) > RHAT_GATE or r['tv_between'] > tv_gate
                          or r['lobe_gap'] > gap_gate),
    }
    print(f'gates: between-chain TV > {tv_gate:.3f}, lobe-mass gap > {gap_gate:.3f}, '
          f'R-hat > {RHAT_GATE}')

    print('\nSTART QUALITY (fault-free; strict coverage, higher is better)')
    print(f"{'arm':10s} {'dates fully covered':>20s} {'uncovered mass':>15s} "
          f"{'chain pattern dist':>19s} {'kept lobe in burn-in':>21s}")
    for a in arms:
        rs = [r for r in rows if r['arm'] == a and r['fault'] == 'none' and r['multi_lobe_dates']]
        if not rs:
            continue
        full = sum(r['dates_fully_covered'] for r in rs) / max(1, sum(r['multi_lobe_dates'] for r in rs))
        kept = [r['drift_kept'] for r in rs if r.get('drift_kept') is not None]
        print(f"{a:10s} {full:20.3f} {np.mean([r['uncovered_mass'] for r in rs]):15.3f} "
              f"{np.mean([r['pattern_distance'] for r in rs]):19.3f} "
              f"{(np.mean(kept) if kept else float('nan')):21.3f}")

    print('\nFAULT-FREE: accuracy (TV from truth) and false alarms')
    print(f"{'arm':10s} {'n':>4s} {'median':>8s} {'p90':>8s} {'wrong':>7s} "
          + ''.join(f'{name + " FA":>11s}' for name in flags))
    for a in arms:
        rs = [r for r in clean if r['arm'] == a]
        if not rs:
            continue
        tv = [r['tv_truth'] for r in rs]
        print(f"{a:10s} {len(rs):4d} {_pct(tv, 50):8.3f} {_pct(tv, 90):8.3f} "
              f"{np.mean([t > WRONG for t in tv]):7.3f} "
              + ''.join(f'{np.mean([f(r) for r in rs]):11.3f}' for f in flags.values()))

    print('\nDETECTION: of the runs that were WRONG, how many each diagnostic flagged')
    print(f"{'fault':11s} {'arm':10s} {'wrong/runs':>11s} "
          + ''.join(f'{name:>10s}' for name in flags))
    pooled = defaultdict(lambda: [0, 0, defaultdict(int)])
    for fault in FAULTS[1:] + ('nojumps',):
        for a in arms:
            all_runs = [r for r in truthful if r['arm'] == a and r['fault'] == fault]
            rs = [r for r in all_runs if r['tv_truth'] > WRONG]
            if not all_runs:
                continue
            counts = {name: sum(f(r) for r in rs) for name, f in flags.items()}
            print(f"{fault:11s} {a:10s} {f'{len(rs)}/{len(all_runs)}':>11s} "
                  + ''.join(f"{(str(counts[n]) + '/' + str(len(rs))):>10s}" for n in flags))
            total = pooled[a]
            total[0] += len(rs)
            total[1] += len(all_runs)
            for name in flags:
                total[2][name] += counts[name]
    print('\nDETECTION pooled over faults')
    print(f"{'arm':10s} {'wrong/runs':>11s} " + ''.join(f'{name:>10s}' for name in flags))
    for a in arms:
        wrong, runs, got = pooled[a]
        if runs:
            print(f"{a:10s} {f'{wrong}/{runs}':>11s} "
                  + ''.join(f"{got[n] / wrong if wrong else float('nan'):10.3f}" for n in flags))

    print('\nWRONG RUNS BY FAULT: the faults are only informative where they break the answer')
    for fault in FAULTS[1:]:
        rs = [r for r in truthful if r['fault'] == fault]
        if rs:
            print(f"  {fault:11s} {sum(r['tv_truth'] > WRONG for r in rs)}/{len(rs)} wrong; "
                  f"median TV {_pct([r['tv_truth'] for r in rs], 50):.3f}")

    if options.cost:
        cost = [r for r in _load(options.cost) if 'error' not in r]
        print('\nCOST to converge under the production stopping rule (median)')
        print(f"{'arm':10s} {'n':>4s} {'sweeps':>10s} {'seconds':>9s} {'vs quantile':>12s}")
        base = _pct([r['sweeps_total'] for r in cost if r['arm'] == 'quantile'], 50)
        for a in arms:
            rs = [r for r in cost if r['arm'] == a]
            if rs:
                sweeps = _pct([r['sweeps_total'] for r in rs], 50)
                print(f"{a:10s} {len(rs):4d} {sweeps:10.0f} "
                      f"{_pct([r['seconds'] for r in rs], 50):9.1f} {sweeps / base:12.2f}")


# The lobe probe (the feature spec's S4): a short chain started with a lobe's
# group placed IN that lobe. If the model rules the lobe out the chain leaves it;
# if the lobe holds real posterior mass it stays. Tested against the exact answer.
PROBE_SWEEPS = 3000
PROBE_EARLY = 500          # sweeps; the "short probe" reads occupancy over [500, 1000)
PROBE_LATE_FROM = 1000


def probe_state(model, kind, target, lobe):
    """
    A starting state with the target's whole group in `lobe`, the rest by the
    modes and repaired by the stratigraphy, or None if the target did not end
    up in the lobe. Every date of a group the target belongs to (for a Boundary,
    every group it bounds) takes the lobe nearest the lobe's peak, so a group
    that can only be found in its other lobe is placed there together.
    """
    peak, _share, first, last = lobe
    if kind == 'date':
        members = next((dates for _o, _y, dates in model.group_dates if target in dates), [])
    else:
        members = [d for o, y, dates in model.group_dates if target in (o, y) for d in dates]
    wishes = {}
    for date_id in members:
        lobes = model.lobes(date_id)
        if lobes:
            wishes[date_id] = [(l, None) for l in sorted(
                lobes, key=lambda l: (abs(l[0] - peak), l[0]))]
    if kind == 'date':
        own = [l for l in model.lobes(target) if l[2] == first and l[3] == last]
        if not own:
            return None
        wishes[target] = [(own[0], None)] + [w for w in wishes.get(target, [])
                                              if w[0][2] != first]
    model.start_lobes = lambda *args, **kwargs: wishes
    try:
        state = model.initial_state(None, (0, 1, 'position'))
    except ValueError:
        return None
    finally:
        del model.start_lobes
    return state if first <= state.index[target] < last else None


def probe_job(args):
    key, seed = args
    try:
        measurements, tree = _inputs(key)
        truth = _truth(key, measurements, tree)
        dates_exact = date_truth(key, measurements, tree)
        model = gibbs.Model(measurements, tree, grid=truth['grid'] if truth else None)
        targets = []
        for date_id in model.date_ids:
            lobes = model.lobes(date_id)
            for lobe in lobes:
                if lobe[1] >= OCC_LIKELIHOOD_SHARE:
                    exact = None
                    if dates_exact and date_id in dates_exact:
                        ages, mass = dates_exact[date_id]
                        exact = float(mass[(ages >= model.grid[lobe[2]])
                                           & (ages <= model.grid[lobe[3] - 1])].sum())
                    targets.append(('date', date_id, lobe, exact))
        for boundary_id, lobes in boundary_lobes(key, measurements, tree, model).items():
            for lobe in lobes:
                if lobe[1] >= OCC_LIKELIHOOD_SHARE:
                    exact = (float(truth['boundaries'][boundary_id][lobe[2]:lobe[3]].sum())
                             if truth and boundary_id in truth['boundaries'] else None)
                    targets.append(('boundary', boundary_id, lobe, exact))
        records = []
        for i, (kind, target, lobe, exact) in enumerate(targets):
            record = {'model': key, 'seed': seed, 'kind': kind, 'share': lobe[1],
                      'exact': exact, 'placed': False}
            state = probe_state(model, kind, target, lobe)
            if state is not None:
                record['placed'] = True
                chain = gibbs._Chain(model, PROBE_SWEEPS, 7000 + 100 * seed + i, None,
                                     PROBE_SWEEPS, 0)
                chain.state = state
                inside = []
                for _ in range(PROBE_SWEEPS):
                    chain.sweep()
                    inside.append(lobe[2] <= chain.state.index[target] < lobe[3])
                inside = np.array(inside, dtype=float)
                record['occ_early'] = float(inside[:PROBE_EARLY].mean())
                record['occ_short'] = float(inside[PROBE_EARLY:PROBE_LATE_FROM].mean())
                record['occ_late'] = float(inside[PROBE_LATE_FROM:].mean())
            records.append(record)
        return records
    except Exception as error:
        return [{'model': key, 'seed': seed, 'error': f'{type(error).__name__}: {error}'[:300]}]


def command_probe(options):
    models = _models(options)
    jobs = [(m, 1000 + s) for m in models for s in range(options.seeds)]
    print(f'{len(jobs)} jobs', flush=True)
    with open(options.out, 'w') as out, multiprocessing.Pool(options.workers) as pool:
        for n, records in enumerate(pool.imap_unordered(probe_job, jobs), 1):
            for record in records:
                out.write(json.dumps(record) + '\n')
            out.flush()
            if n % 10 == 0 or n == len(jobs):
                print(f'{n}/{len(jobs)}', flush=True)


def command_probe_report(options):
    rows = [r for r in _load(options.results)]
    errors = [r for r in rows if 'error' in r]
    rows = [r for r in rows if 'error' not in r and r['exact'] is not None]
    print(f'{len(rows)} lobes with an exact answer, {len(errors)} errors')
    for kind in ('date', 'boundary'):
        lobes = [r for r in rows if r['kind'] == kind]
        real = [r for r in lobes if r['exact'] >= OCC_REAL]
        ruled = [r for r in lobes if r['exact'] < OCC_EMPTY]
        between = len(lobes) - len(real) - len(ruled)
        print(f"\n{kind.upper()} lobes (>= {OCC_LIKELIHOOD_SHARE} of the likelihood): {len(lobes)} "
              f"-- {len(real)} real (exact >= {OCC_REAL}), {len(ruled)} ruled out "
              f"(exact < {OCC_EMPTY}), {between} between")
        for name, group in (('real', real), ('ruled out', ruled)):
            placed = [r for r in group if r['placed']]
            print(f"  {name}: {len(placed)} of {len(group)} could be started in the lobe")
        for field, label in (('occ_late', f'occupancy over sweeps {PROBE_LATE_FROM}+'),
                             ('occ_short', f'short probe, sweeps {PROBE_EARLY}-{PROBE_LATE_FROM}')):
            print(f"  {label}: the probe says 'stays' when the chain is in the lobe at least T of the time")
            print(f"    {'T':>6s} {'real that stay':>16s} {'ruled out that stay':>21s}")
            for t in (0.02, 0.05, 0.1, 0.25, 0.5):
                rs = [r for r in real if r['placed']]
                ns = [r for r in ruled if r['placed']]
                a = sum(r[field] >= t for r in rs)
                b = sum(r[field] >= t for r in ns)
                print(f"    {t:6.2f} {f'{a}/{len(rs)}':>16s} {f'{b}/{len(ns)}':>21s}")
    # What it would mean where it is wanted: the flagged lobes are almost all ruled out.
    print("\nIf a probe of sensitivity s and specificity c is run on flagged lobes, of which "
          "0.5% (dates) / 1% (Boundaries) are real misses (the occupancy study), the share of "
          "'not reached' verdicts that are real is s*p / (s*p + (1-c)*(1-p)).")


def command_occupancy(options):
    """How the S2 lobe-occupancy flag does against the runs the faults made wrong."""
    rows = [r for r in _load(options.results) if 'error' not in r and r.get('has_truth')]
    rhat_value = lambda r: float('inf') if r['rhat'] is None else r['rhat']
    flags = {
        'R-hat': lambda r: rhat_value(r) > RHAT_GATE,
        'unvisited': lambda r: r['occ_unvisited'] > 0,
        'disagree': lambda r: r['occ_disagree'] > 0,
        'unvisited+disagree': lambda r: r['occ_unvisited'] > 0 or r['occ_disagree'] > 0,
        'any + R-hat': lambda r: (r['occ_unvisited'] > 0 or r['occ_disagree'] > 0
                                  or rhat_value(r) > RHAT_GATE),
    }
    arms = sorted({r['arm'] for r in rows})
    print(f"{len(rows)} runs with truth; wrong = worst Boundary over {WRONG} from truth")
    for fault in sorted({r['fault'] for r in rows}):
        for arm in arms:
            runs = [r for r in rows if r['fault'] == fault and r['arm'] == arm]
            wrong = [r for r in runs if r['tv_truth'] > WRONG]
            right = [r for r in runs if r['tv_truth'] <= WRONG]
            print(f"\n{fault} / {arm}: {len(wrong)} wrong of {len(runs)}")
            print(f"  {'flag':20s} {'catches wrong':>14s} {'false alarms':>13s} {'precision':>10s}")
            for name, flag in flags.items():
                caught = sum(flag(r) for r in wrong)
                false = sum(flag(r) for r in right)
                fired = caught + false
                print(f"  {name:20s} {f'{caught}/{len(wrong)}':>14s} {f'{false}/{len(right)}':>13s} "
                      f"{(f'{caught}/{fired}' if fired else '-'):>10s}")
    # The flag's two readings, where the exact solver gives the date posteriors.
    solved = [r for r in rows if r.get('has_date_truth') and r['occ_unvisited']]
    real = sum(r['occ_unvisited_real'] for r in solved)
    ruled = sum(r['occ_unvisited_ruled_out'] for r in solved)
    total = sum(r['occ_unvisited'] for r in solved)
    print(f"\nUnvisited lobes on exactly solvable models: {total} flagged -- "
          f"{real} the exact answer puts at least {OCC_REAL} on (real misses), "
          f"{ruled} it puts under {OCC_EMPTY} on (ruled out by the model)")
    for fault in sorted({r['fault'] for r in rows}):
        errors = [r['occ_lobe_error'] for r in rows
                  if r['fault'] == fault and r.get('occ_lobe_error') is not None]
        wrong = [r['occ_lobe_error'] for r in rows if r['fault'] == fault
                 and r.get('occ_lobe_error') is not None and r['tv_truth'] > WRONG]
        if errors:
            print(f"worst date-lobe mass error, {fault}: median {_pct(errors, 50):.3f}"
                  + (f"; on wrong runs {_pct(wrong, 50):.3f}" if wrong else ""))


def command_boundary_occupancy(options):
    """The S2 flags over Boundaries, lobes from boundary_lobes."""
    rows = [r for r in _load(options.results)
            if 'error' not in r and r.get('has_truth') and 'occb_boundaries' in r]
    rhat_value = lambda r: float('inf') if r['rhat'] is None else r['rhat']
    flags = {
        'R-hat': lambda r: rhat_value(r) > RHAT_GATE,
        'unvisited': lambda r: r['occb_unvisited'] > 0,
        'disagree': lambda r: r['occb_disagree'] > 0,
        'either': lambda r: r['occb_unvisited'] > 0 or r['occb_disagree'] > 0,
        'either + R-hat': lambda r: (r['occb_unvisited'] > 0 or r['occb_disagree'] > 0
                                     or rhat_value(r) > RHAT_GATE),
    }
    with_lobes = [r for r in rows if r['occb_boundaries']]
    print(f"{len(rows)} runs with truth; {len(with_lobes)} have a Boundary with two or more lobes "
          f"(runs of {sorted({r['model'] for r in with_lobes})})")
    for fault in sorted({r['fault'] for r in rows}):
        for arm in sorted({r['arm'] for r in rows}):
            runs = [r for r in rows if r['fault'] == fault and r['arm'] == arm]
            wrong = [r for r in runs if r['tv_truth'] > WRONG]
            right = [r for r in runs if r['tv_truth'] <= WRONG]
            print(f"\n{fault} / {arm}: {len(wrong)} wrong of {len(runs)}")
            print(f"  {'flag':16s} {'catches wrong':>14s} {'false alarms':>13s} {'precision':>10s}")
            for name, flag in flags.items():
                caught, false = sum(flag(r) for r in wrong), sum(flag(r) for r in right)
                fired = caught + false
                print(f"  {name:16s} {f'{caught}/{len(wrong)}':>14s} {f'{false}/{len(right)}':>13s} "
                      f"{(f'{caught}/{fired}' if fired else '-'):>10s}")
    flagged = sum(r['occb_unvisited'] for r in rows)
    print(f"\nUnvisited Boundary lobes: {flagged} flagged -- "
          f"{sum(r['occb_unvisited_real'] for r in rows)} the direct answer puts at least {OCC_REAL} on, "
          f"{sum(r['occb_unvisited_ruled_out'] for r in rows)} it puts under {OCC_EMPTY} on")
    print("\nWHAT KIND OF FAILURE IS A WRONG RUN (direct answer has a lobe of "
          f">= {OCC_LIKELIHOOD_SHARE} that the pooled run gave < {OCC_EMPTY})")
    for fault in sorted({r['fault'] for r in rows}):
        wrong = [r for r in rows if r['fault'] == fault and r['tv_truth'] > WRONG]
        right = [r for r in rows if r['fault'] == fault and r['tv_truth'] <= WRONG]
        if wrong:
            print(f"  {fault}: {sum(r['occb_truth_missed'] > 0 for r in wrong)} of {len(wrong)} wrong runs "
                  f"missed a lobe outright; {sum(r['occb_truth_missed'] > 0 for r in right)} of "
                  f"{len(right)} correct runs did")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('run', 'cost'):
        p = sub.add_parser(name)
        p.add_argument('--out', required=True)
        p.add_argument('--models', help='comma list: dune, gen0.., oxcal, hillside, dunesec')
        p.add_argument('--generated', type=int, default=8, help='how many generated chains')
        p.add_argument('--parallel', type=int, default=4, help='how many generated parallel-branch models')
        p.add_argument('--arms', default=','.join(ARMS))
        p.add_argument('--seeds', type=int, default=20)
        p.add_argument('--sweeps', type=int, default=8000 if name == 'run' else gibbs.DEFAULT_SWEEPS)
        if name == 'run':
            p.add_argument('--faults', default=','.join(FAULTS))
            p.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 1))
            p.add_argument('--drift', action='store_true', help='also measure burn-in drift')
        else:
            p.add_argument('--max-sweeps', type=int, default=gibbs.MAX_SWEEPS)
    p = sub.add_parser('probe')
    p.add_argument('--out', required=True)
    p.add_argument('--models')
    p.add_argument('--generated', type=int, default=8)
    p.add_argument('--parallel', type=int, default=4)
    p.add_argument('--seeds', type=int, default=6)
    p.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 1))
    p = sub.add_parser('probe-report')
    p.add_argument('results')
    p = sub.add_parser('occupancy')
    p.add_argument('results')
    p = sub.add_parser('boundary-occupancy')
    p.add_argument('results')
    p = sub.add_parser('report')
    p.add_argument('results')
    p.add_argument('cost', nargs='?')
    options = parser.parse_args()
    {'run': command_run, 'cost': command_cost, 'report': command_report,
     'occupancy': command_occupancy,
     'boundary-occupancy': command_boundary_occupancy, 'probe': command_probe,
     'probe-report': command_probe_report}[options.command](options)


if __name__ == '__main__':
    main()
