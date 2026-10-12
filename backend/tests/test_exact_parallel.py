"""
The exact solver for areas side by side (services/exact_parallel.py).

Checked in the same three ways as the chain solver, plus the two references
only this shape has:

  * layout_with_reason recognises the shapes it can integrate and declines,
    with a reason an archaeologist can act on, the ones it cannot.
  * it reproduces an explicit sum over every Boundary configuration of a
    small model, on a grid coarse enough to afford it.
  * one area inside a stage is the same model as a chain with empty groups,
    and the two solvers must agree on it.
  * its Boundaries agree with services/cross_check.py's direct integration,
    and its dates with the sampler.
  * its joint draws are ordered as the model says and drawn from the joint
    the marginals describe, and the API routes such a model to it.
"""
import itertools
import uuid

import numpy as np
import pytest

from app.services import cross_check
from app.services import exact_chain as ec
from app.services import exact_parallel as ep
from app.services import gibbs
from app.services.model_tree import inherited_bounds

_IDS = itertools.count(1)
# test_exact_tails.py imports these helpers as tests.test_exact_parallel while
# pytest loads this file as test_exact_parallel: two copies, two counters. A
# token per copy keeps their ids apart in the shared database.
_COPY = uuid.uuid4().hex[:6]


def _element(kind, name, children=()):
    return {'id': f'xp{_COPY}-{next(_IDS)}', 'element_type': kind, 'name': name,
            'tpq_age_bp': None, 'taq_age_bp': None, 'children': list(children)}


def _date(element, age_bp, error=25, **extra):
    tag = f'xpd{_COPY}-{next(_IDS)}'
    date = {'id': tag, 'sample_name': tag, 'age_bp': float(age_bp),
            'error_1sigma': float(error), 'curve_id': 'intcal20',
            'element_id': element['id']}
    date.update(extra)
    return date


def _area(name, groups, error=25, **extra):
    """A Sequence of Phases, one per list of ages, each between Boundaries."""
    children = [_element('boundary', f'{name} start')]
    dates = []
    for k, ages in enumerate(groups):
        phase = _element('phase', f'{name} {k + 1}')
        children += [phase, _element('boundary', f'{name} {k + 1} end')]
        dates += [_date(phase, age, error, **extra) for age in ages]
    return _element('sequence', name, children), dates


def _site(stages, error=25, **extra):
    """
    A Sequence of stages between Boundaries. Each stage is ('plain', ages) or
    ('areas', [groups of ages per area], ages lying directly in the stage).
    """
    children = [_element('boundary', 'Start')]
    dates = []
    for index, stage in enumerate(stages):
        if stage[0] == 'plain':
            phase = _element('phase', f'Stage {index}')
            dates += [_date(phase, age, error, **extra) for age in stage[1]]
        else:
            areas = []
            for k, groups in enumerate(stage[1]):
                sequence, area_dates = _area(f'Area {index}{k}', groups, error, **extra)
                areas.append(sequence)
                dates += area_dates
            phase = _element('phase', f'Stage {index}', areas)
            dates += [_date(phase, age, error, **extra) for age in stage[2]]
        children += [phase, _element('boundary', f'Boundary {index + 1}')]
    return _element('sequence', 'Site', children), dates


TWO_AREAS = [('areas', [[[3010, 2990], [2950]], [[2980, 2965]]], [])]


class TestLayout:
    def test_one_stage_of_two_areas(self):
        tree, dates = _site(TWO_AREAS)
        stages, reason = ep.layout_with_reason(tree, dates)
        assert reason is None
        assert len(stages.stages) == 1 and len(stages.stages[0].areas) == 2
        assert [len(a.groups) for a in stages.stages[0].areas] == [2, 1]
        assert len(stages.all_boundary_ids) == 2 + 3 + 2
        assert stages.num_groups == 1 + 2 + 1

    def test_areas_as_one_stage_of_a_longer_sequence(self):
        tree, dates = _site([('plain', [3150, 3120])] + TWO_AREAS + [('plain', [2850])])
        stages, reason = ep.layout_with_reason(tree, dates)
        assert reason is None
        assert [len(s.areas) for s in stages.stages] == [0, 2, 0]
        assert [len(s.dates) for s in stages.stages] == [2, 0, 1]

    def test_dates_directly_in_the_stage_are_its_own(self):
        tree, dates = _site([('areas', [[[3010]], [[2980]]], [3000, 2995])])
        stages, _reason = ep.layout_with_reason(tree, dates)
        assert len(stages.stages[0].dates) == 2

    def test_one_area_is_enough(self):
        tree, dates = _site([('areas', [[[3010, 2990]]], [])])
        stages, reason = ep.layout_with_reason(tree, dates)
        assert reason is None and len(stages.stages[0].areas) == 1

    def test_a_phase_around_the_areas_adds_nothing(self):
        tree, dates = _site(TWO_AREAS)
        stage = tree['children'][1]
        stage['children'] = [_element('phase', 'Trenches', stage['children'])]
        stages, reason = ep.layout_with_reason(tree, dates)
        assert reason is None and len(stages.stages[0].areas) == 2

    def test_a_chain_is_not_its_business(self):
        tree, dates = _site([('plain', [3000, 2990])])
        assert ep.layout_with_reason(tree, dates) == (None, None)

    def test_areas_with_nothing_around_them_are_declined(self):
        area_a, dates_a = _area('North', [[3000]])
        area_b, dates_b = _area('South', [[2950]])
        tree = _element('phase', 'Site', [area_a, area_b])
        stages, reason = ep.layout_with_reason(tree, dates_a + dates_b)
        assert stages is None
        assert 'no Boundary before and after them' in reason

    def test_an_area_that_is_not_one_line_is_named(self):
        tree, dates = _site(TWO_AREAS)
        area = tree['children'][1]['children'][0]
        # Two Sequences of Phases side by side, with no Boundaries of their
        # own, inside one of the area's groups: an order that is not blocks.
        lines = []
        for name, ages in (('Pit', (3005, 2995)), ('Ditch', (3004, 2994))):
            first, second = _element('phase', f'{name} 1'), _element('phase', f'{name} 2')
            lines.append(_element('sequence', name, [first, second]))
            dates += [_date(first, ages[0]), _date(second, ages[1])]
        area['children'][1]['children'] = lines
        stages, reason = ep.layout_with_reason(tree, dates)
        assert stages is None
        assert reason.startswith(f"in '{area['name']}', ")
        assert reason.endswith(ec.NOT_ONE_AFTER_ANOTHER)

    def test_dates_before_the_first_boundary_are_a_tail(self):
        """More in test_exact_tails.py."""
        tree, dates = _site(TWO_AREAS)
        before = _element('phase', 'Before')
        tree['children'].insert(0, before)
        dates.append(_date(before, 3300))
        stages, reason = ep.layout_with_reason(tree, dates)
        assert reason is None and stages.older_tail == [dates[-1]['id']]

    def test_areas_inside_an_area_are_declined(self):
        inner_a, dates_a = _area('Pit 1', [[3000]])
        inner_b, dates_b = _area('Pit 2', [[2990]])
        outer = _element('sequence', 'North', [
            _element('boundary', 'North start'),
            _element('phase', 'Pits', [inner_a, inner_b]),
            _element('boundary', 'North end')])
        other, dates_c = _area('South', [[2950]])
        tree = _element('sequence', 'Site', [
            _element('boundary', 'Start'), _element('phase', 'Areas', [outer, other]),
            _element('boundary', 'End')])
        stages, reason = ep.layout_with_reason(tree, dates_a + dates_b + dates_c)
        assert stages is None and reason.startswith("in 'North', ")

    def test_only_the_model_as_written(self):
        """
        Spelling out an inner Sequence that does not begin with a Boundary of
        its own would invent a group: here the Phase it opens with belongs to
        no pair of Boundaries in the written model, and the layout must not
        put it between Start and the inner Sequence's Boundary.
        """
        tree, dates = _site(TWO_AREAS)
        loose = _element('phase', 'Loose')
        middle = _element('boundary', 'Middle')
        tree['children'].insert(1, _element('sequence', 'Inner', [loose, middle]))
        dates.append(_date(loose, 3100))
        stages, reason = ep.layout_with_reason(tree, dates)
        assert stages is None
        assert reason is not None


def _brute_force(tree, dates, grid, layout=None):
    """
    Every date's and Boundary's marginal by explicit summation over every
    configuration of the model's Boundaries, written from the model statement:

        weight = C(B_0, B_n) * prod over plain stages of its group's term
                 * prod over areas of h(S, f) G(f, l) h(l, E)

    with G a group's span factor and interval integrals -- or where its two
    Boundaries share a cell, half its dates' likelihoods there -- h a half
    where neighbouring Boundaries share a cell, and nothing for S and E
    themselves, which are not neighbours once an area lies between them.
    Dates are integrated out given their pair and then put back: a date's
    conditional is its likelihood over the pair's interval, half at each end,
    and in the cell its two Boundaries share when they share one.

    Dates outside every Boundary add, for each, the integral of its
    likelihood from its end Boundary out to the grid's edge, a half at each
    end of that run -- nothing where the Boundary is on the edge -- and given
    the Boundary sit anywhere in that run, its likelihood times those halves.
    They are in no span term and not in the limits factor.

    The span correction and the interval integral are the solver's own
    definitions, as in test_exact_chain's _brute_force: what this checks is
    how the pieces are combined, not the pieces.

    `layout` gives a chain's layout as Stages, for models with no areas.
    """
    stages, reason = (layout, None) if layout is not None else ep.layout_with_reason(
        tree, dates)
    assert stages is not None, reason
    bounds = inherited_bounds(tree)
    M = len(grid)
    dt = grid[1] - grid[0]
    likelihood, cumulative, _parts = ec.date_tables(grid, dates, bounds)
    F = {d['id']: ec._interval_integral(cumulative[d['id']], likelihood[d['id']], dt)
         for d in dates}

    def group(date_ids, older, younger):
        apart = older > younger
        span = np.where(apart, (older - younger) * dt, 1.0)
        weight = np.where(apart, span ** -len(date_ids), 0.0)
        tied = np.full(older.shape, 0.5)
        for d in date_ids:
            weight = weight * np.where(apart, F[d][younger, older], 0.0)
            tied = tied * likelihood[d][older]
        return np.where(older == younger, tied, weight)

    def tie(older, younger):
        return np.where(older > younger, 1.0, np.where(older == younger, 0.5, 0.0))

    def run(date_id, F, older):
        """Date date_id's weight at each cell given its end Boundary at F."""
        h = np.zeros(M)
        if (older and F == M - 1) or (not older and F == 0):
            return h
        span = slice(F, M) if older else slice(0, F + 1)
        h[span] = 1.0
        h[F] = h[M - 1 if older else 0] = 0.5
        return likelihood[date_id] * h * dt

    tails = [(d, True) for d in stages.older_tail] + [(d, False) for d in stages.younger_tail]
    in_tails = {d for d, _older in tails}
    names = stages.all_boundary_ids
    grid_of = dict(zip(names, np.indices((M,) * len(names), dtype=np.int32)
                       .reshape(len(names), -1)))
    outer = stages.boundary_ids
    log_c = ec._span_prior_correction(
        grid, len(names), True,
        *ec.pair_limits(bounds, outer[0], outer[-1], grid, likelihood,
                        [d['id'] for d in dates if d['id'] not in in_tails]))
    weight = np.exp(np.where(np.isfinite(log_c), log_c, -np.inf))[
        grid_of[outer[-1]], grid_of[outer[0]]]
    pairs = []
    for index, stage in enumerate(stages.stages):
        S, E = grid_of[stage.older_id], grid_of[stage.younger_id]
        weight = weight * (S >= E)
        if not stage.areas:
            weight = weight * group(stage.dates, S, E)
            pairs.append((stage.dates, S, E))
        for area in stage.areas:
            ends = [grid_of[b] for b in area.boundary_ids]
            weight = weight * tie(S, ends[0]) * tie(ends[-1], E)
            for k, date_ids in enumerate(area.groups):
                weight = weight * group(date_ids, ends[k], ends[k + 1])
                pairs.append((date_ids, ends[k], ends[k + 1]))
    G = {d: np.array([run(d, F, older).sum() for F in range(M)]) for d, older in tails}
    for d, older in tails:
        weight = weight * G[d][grid_of[outer[0] if older else outer[-1]]]
    weight = weight / weight.sum()

    out = {b: np.bincount(grid_of[b], weights=weight, minlength=M) for b in names}
    for d, older in tails:
        end = out[outer[0] if older else outer[-1]]
        out[d] = sum(end[F] * run(d, F, older) / G[d][F]
                     for F in range(M) if G[d][F] > 0)
    for date_ids, older, younger in pairs:
        pair = np.zeros((M, M))
        np.add.at(pair, (older, younger), weight)
        for d in date_ids:
            mass = np.zeros(M)
            for o in range(M):
                for y in range(o + 1):
                    if pair[o, y] == 0:
                        continue
                    if o == y:
                        mass[o] += pair[o, y]
                        continue
                    h = np.zeros(M)
                    h[y:o + 1] = 1.0
                    h[y] = h[o] = 0.5
                    mass += pair[o, y] * likelihood[d] * h * dt / F[d][y, o]
            out[d] = mass
    return out


class TestMatchesBruteForce:
    @pytest.mark.parametrize('stages, M', [
        ([('areas', [[[3000]], [[2950]]], [])], 12),
        ([('plain', [3060])] + [('areas', [[[2990]], [[2960]]], [])], 8),
    ], ids=['one stage', 'two stages'])
    def test_every_marginal(self, stages, M):
        tree, dates = _site(stages, error=30)
        layout, _reason = ep.layout_with_reason(tree, dates)
        grid = np.linspace(3000.0, 3350.0, M)
        expected = _brute_force(tree, dates, grid)
        solved, densities = ep._solve_on_grid(
            grid, dates, tree, layout, inherited_bounds(tree), num_samples=10, seed=1,
            uniform_span_prior=True, estimate_durations=False, dispersal=None,
            interval_draws=10, resolution=5.0)
        ids = list(solved['measurements']) + list(solved['boundaries'])
        assert set(ids) == set(expected)
        for pid, density in zip(ids, densities):
            np.testing.assert_allclose(density / density.sum(),
                                       expected[pid] / expected[pid].sum(), atol=1e-12)


class TestOneAreaIsAChain:
    def test_it_solves_as_the_chain_with_empty_groups(self):
        """
        One area inside a stage is the chain Start, f, ..., l, End with an
        empty group either side: the same Boundaries, the same ties between
        neighbours, the same span correction over the same count.
        """
        tree, dates = _site([('areas', [[[3010, 2990], [2950]]], [])])
        stage = tree['children'][1]
        area = stage['children'][0]
        chain_tree = _element('sequence', 'Line', [tree['children'][0]]
                              + area['children'] + [tree['children'][2]])
        layout, _reason = ep.layout_with_reason(tree, dates)
        chain = ec.layout(chain_tree, dates)
        assert chain is not None

        grid = np.arange(2700.0, 3600.0, 2.5)
        options = dict(num_samples=10, seed=1, uniform_span_prior=True,
                       estimate_durations=False, dispersal=None, interval_draws=10,
                       resolution=5.0)
        parallel, _d = ep._solve_on_grid(grid, dates, tree, layout,
                                         inherited_bounds(tree), **options)
        line, _d2 = ec._solve_on_grid(grid, dates, chain_tree, chain,
                                      inherited_bounds(chain_tree), **options)
        for kind in ('measurements', 'boundaries'):
            for pid, summary in line[kind].items():
                mine = parallel[kind][pid]
                a = summary.get('posterior', summary)
                b = mine.get('posterior', mine)
                assert b['posterior_mean'] == pytest.approx(a['posterior_mean'], abs=1e-6), pid
                assert b['posterior_std'] == pytest.approx(a['posterior_std'], abs=1e-6), pid


class TestAgreesWithTheReferences:
    def test_the_boundaries_are_the_cross_checks(self):
        """
        services/cross_check.py integrates the same model its own way, built
        on the sampler's definitions, to check the sampler. On its grid the two
        must agree to rounding. (On their own grids they differ by the
        cross-check's narrower window, which clips the outer Boundaries' tails.)
        """
        tree, dates = _site(TWO_AREAS)
        layout, _reason = ep.layout_with_reason(tree, dates)
        reference = cross_check.reference(dates, tree)
        solved, densities = ep._solve_on_grid(
            reference['grid'], dates, tree, layout, inherited_bounds(tree),
            num_samples=10, seed=1, uniform_span_prior=True, estimate_durations=False,
            dispersal=None, interval_draws=10, resolution=5.0)
        mine = dict(zip(list(solved['measurements']) + list(solved['boundaries']), densities))
        for bid, density in reference['boundaries'].items():
            np.testing.assert_allclose(mine[bid] / mine[bid].sum(), density / density.sum(),
                                       atol=1e-12, err_msg=bid)

    def test_the_dates_are_the_samplers(self):
        tree, dates = _site([('plain', [3150])] + TWO_AREAS, error=25)
        layout, _reason = ep.layout_with_reason(tree, dates)
        exact = ep.solve(dates, tree, layout, num_samples=50, interval_draws=50)
        sampled = gibbs.solve(dates, tree, sweeps=30000, max_sweeps=30000,
                              num_samples=200, seed=11, chains=1, temperatures=1)
        errors = [sampled['measurements'][d['id']]['posterior_mean']
                  - exact['measurements'][d['id']]['posterior_mean'] for d in dates]
        assert np.sqrt(np.mean(np.square(errors))) < 2.0, np.round(errors, 2)


class TestJointDraws:
    def test_every_draw_is_ordered_as_the_model_says(self):
        tree, dates = _site([('plain', [3150])] + [
            ('areas', [[[3010, 2990], [2950]], [[2980, 2965]]], [2970])] + [('plain', [2850])])
        layout, _reason = ep.layout_with_reason(tree, dates)
        solved = ep.solve(dates, tree, layout, num_samples=50, interval_draws=4000)
        joint = {**solved['joint_boundaries'], **solved['joint_draws']}
        outer = layout.boundary_ids
        for older, younger in zip(outer, outer[1:]):
            assert np.all(joint[older] >= joint[younger])
        stage = layout.stages[1]
        S, E = joint[stage.older_id], joint[stage.younger_id]
        for date_id in stage.dates:
            assert np.all((S >= joint[date_id]) & (joint[date_id] >= E))
        for area in stage.areas:
            ends = area.boundary_ids
            assert np.all(S >= joint[ends[0]]) and np.all(joint[ends[-1]] >= E)
            for k, date_ids in enumerate(area.groups):
                assert np.all(joint[ends[k]] >= joint[ends[k + 1]])
                for date_id in date_ids:
                    assert np.all((joint[ends[k]] >= joint[date_id])
                                  & (joint[date_id] >= joint[ends[k + 1]]))

    def test_the_draws_come_from_the_joint_the_marginals_describe(self):
        """On a grid small enough to sum: the drawn outer pair and each area's
        ends, against the brute force's joint over the same four."""
        tree, dates = _site([('areas', [[[3000]], [[2950]]], [])], error=30)
        layout, _reason = ep.layout_with_reason(tree, dates)
        grid = np.linspace(3000.0, 3350.0, 8)
        solved, _densities = ep._solve_on_grid(
            grid, dates, tree, layout, inherited_bounds(tree), num_samples=10, seed=1,
            uniform_span_prior=True, estimate_durations=False, dispersal=None,
            interval_draws=200000, resolution=5.0)
        expected = _brute_force(tree, dates, grid)
        for bid, drawn in solved['joint_boundaries'].items():
            counts = np.bincount(np.searchsorted(grid, drawn), minlength=len(grid))
            np.testing.assert_allclose(counts / counts.sum(),
                                       expected[bid] / expected[bid].sum(), atol=0.006)
        for date_id, drawn in solved['joint_draws'].items():
            counts = np.bincount(np.searchsorted(grid, drawn), minlength=len(grid))
            np.testing.assert_allclose(counts / counts.sum(),
                                       expected[date_id] / expected[date_id].sum(), atol=0.006)

    def test_durations_come_with_it(self):
        tree, dates = _site(TWO_AREAS)
        layout, _reason = ep.layout_with_reason(tree, dates)
        solved = ep.solve(dates, tree, layout, num_samples=50, interval_draws=2000,
                          estimate_durations=True)
        assert solved['intervals']['durations']


def _api_tree(tree):
    """The API's element shape: no limits, children as given."""
    out = {k: tree[k] for k in ('id', 'element_type', 'name')}
    out['children'] = [_api_tree(child) for child in tree.get('children') or []]
    return out


def _run(client, tree, dates, **config):
    measurements = [{**{k: d[k] for k in ('id', 'sample_name', 'age_bp', 'error_1sigma',
                                         'curve_id', 'element_id')},
                     **{k: d[k] for k in ('outlier_prior',) if k in d},
                     'material_type': 'charcoal', 'sample_type': 'terrestrial'}
                    for d in dates]
    return client.post('/api/analysis/bayesian?wait=true', json={
        'name': f'Areas {next(_IDS)}',
        'measurements': measurements,
        'model_tree': _api_tree(tree),
        'bayesian_config': {'mcmc_samples': 200, 'mcmc_chains': 1,
                            'mcmc_temperatures': 1, **config},
        'save': True,
    })


class TestTheAnalysisRoutesItHere:
    def test_automatic_solves_side_by_side_areas_exactly(self, client):
        tree, dates = _site(TWO_AREAS)
        created = _run(client, tree, dates, estimate_durations=True)
        assert created.status_code == 200, created.text
        data = created.json()['data']
        assert data['solver']['method'] == 'exact'
        assert data['solver']['shape'] == 'parallel'
        assert data['solver']['reason'] is None
        assert data['solver']['model_agreement'] is not None

    def test_a_chain_is_still_a_chain(self, client):
        tree, dates = _site([('plain', [3000, 2990])])
        created = _run(client, tree, dates)
        assert created.status_code == 200, created.text
        assert created.json()['data']['solver']['shape'] == 'chain'

    def test_the_sampler_can_still_be_chosen(self, client):
        tree, dates = _site(TWO_AREAS)
        created = _run(client, tree, dates, solver='gibbs')
        assert created.status_code == 200, created.text
        solver = created.json()['data']['solver']
        assert solver['method'] == 'gibbs'
        assert solver['reason'] is None
        assert solver['cross_check']['status'] in ('agrees', 'differs')

    def test_an_area_that_is_not_one_line_is_sampled_and_says_why(self, client):
        tree, dates = _site(TWO_AREAS)
        area = tree['children'][1]['children'][0]
        # A Boundary-less Phase before the area's first Boundary: dates the
        # area does not enclose, which no transition can be supplied for.
        before = _element('phase', 'Spill')
        area['children'].insert(0, before)
        dates.append(_date(before, 3050))
        created = _run(client, tree, dates)
        assert created.status_code == 200, created.text
        solver = created.json()['data']['solver']
        assert solver['method'] == 'gibbs'
        assert solver['reason'].startswith(f"in '{area['name']}', ")
