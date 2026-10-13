"""
Dates outside every Boundary, at either end of a model the exact solver
otherwise integrates: a chain (services/exact_chain.py) or areas side by side
(services/exact_parallel.py). Such dates have a flat prior and are held only
by their order against the end Boundary, so they enter the solve as one
weight on it (exact_chain._Tails).

Checked as the two shapes are:

  * both layouts take one unordered group at each end, and decline, with a
    reason, groups ordered one after another out there, dates ordered against
    neither end, and dates out there with an outlier model.
  * the solvers reproduce an explicit sum over every Boundary configuration
    on a coarse grid, the cross-check's Boundaries on its grid, and the
    sampler's dates.
  * every joint draw has each tail date beyond its end Boundary, and the
    draws come from the joint the marginals describe.
  * the API solves such a model exactly under Automatic.
"""
import numpy as np
import pytest

from app.services import cross_check
from app.services import exact_chain as ec
from app.services import exact_parallel as ep
from app.services import gibbs
from app.services.model_tree import inherited_bounds
from tests.test_exact_parallel import (TWO_AREAS, _brute_force, _date, _element, _run,
                                       _site)

OPTIONS = dict(num_samples=10, seed=1, uniform_span_prior=True, estimate_durations=False,
               dispersal=None, interval_draws=10, resolution=5.0)


def _with_tails(tree, dates, before=(), after=(), **extra):
    """`tree` with a Phase of `before` ages ahead of its first child and one
    of `after` ages behind its last, each outside every Boundary."""
    if before:
        phase = _element('phase', 'Before')
        tree['children'].insert(0, phase)
        dates += [_date(phase, age, **extra) for age in before]
    if after:
        phase = _element('phase', 'After')
        tree['children'].append(phase)
        dates += [_date(phase, age, **extra) for age in after]
    return tree, dates


def _chain(groups, before=(), after=(), **extra):
    """A chain of plain Phases with tails, and its layout."""
    tree, dates = _site([('plain', ages) for ages in groups])
    tree, dates = _with_tails(tree, dates, before, after, **extra)
    chain, reason = ec.layout_with_reason(tree, dates, allow_tails=True)
    assert chain is not None, reason
    return tree, dates, chain


def _as_stages(chain):
    """A chain as Stages with no areas, for the brute force."""
    return ep.Stages(chain.boundary_ids,
                     [ep.Stage(older, younger, group, [])
                      for older, younger, group in zip(chain.boundary_ids,
                                                       chain.boundary_ids[1:],
                                                       chain.groups)],
                     chain.boundary_names, chain.older_tail, chain.younger_tail)


def _ids(dates, ages):
    return [d['id'] for d in dates if d['age_bp'] in ages]


class TestLayout:
    def test_a_chain_takes_a_tail_at_either_end(self):
        tree, dates, chain = _chain([[3000, 2990]], before=[3100, 3080], after=[2900])
        assert sorted(chain.older_tail) == sorted(_ids(dates, (3100, 3080)))
        assert chain.younger_tail == _ids(dates, (2900,))
        assert chain.groups == [_ids(dates, (3000, 2990))]

    def test_only_when_asked(self):
        """A part of a model -- an area, a cross-check branch -- has dates
        outside its own Boundaries that are not outside the model's."""
        tree, dates = _with_tails(*_site([('plain', [3000])]), before=[3100])
        chain, reason = ec.layout_with_reason(tree, dates)
        assert chain is None and 'outside every Boundary' in reason

    def test_side_by_side_areas_take_them_too(self):
        tree, dates = _with_tails(*_site(TWO_AREAS), before=[3300], after=[2800])
        stages, reason = ep.layout_with_reason(tree, dates)
        assert reason is None
        assert stages.older_tail == _ids(dates, (3300,))
        assert stages.younger_tail == _ids(dates, (2800,))

    def test_several_phases_with_nothing_ordering_them_are_one_tail(self):
        tree, dates = _site([('plain', [3000])])
        first, second = _element('phase', 'Pit'), _element('phase', 'Ditch')
        tree['children'].insert(0, _element('phase', 'Before', [first, second]))
        dates += [_date(first, 3100), _date(second, 3090)]
        chain, reason = ec.layout_with_reason(tree, dates, allow_tails=True)
        assert reason is None and len(chain.older_tail) == 2

    @pytest.mark.parametrize('parallel', [False, True], ids=['chain', 'areas'])
    def test_phases_in_a_row_out_there_are_blocks(self, parallel):
        """More in test_exact_ordered.py."""
        tree, dates = _site(TWO_AREAS if parallel else [('plain', [3000])])
        tree, dates = _with_tails(tree, dates, before=[3100])
        earlier = _element('phase', 'Earlier')
        tree['children'].insert(0, earlier)
        dates.append(_date(earlier, 3200))
        layout, reason = (ep.layout_with_reason(tree, dates) if parallel else
                          ec.layout_with_reason(tree, dates, allow_tails=True))
        assert reason is None
        assert layout.older_tail_blocks == [_ids(dates, (3200,)), _ids(dates, (3100,))]

    @pytest.mark.parametrize('parallel', [False, True], ids=['chain', 'areas'])
    def test_an_outlier_model_out_there_is_declined(self, parallel):
        tree, dates = _site(TWO_AREAS if parallel else [('plain', [3000])])
        tree, dates = _with_tails(tree, dates, before=[3100], outlier_prior=0.05)
        layout, reason = (ep.layout_with_reason(tree, dates) if parallel else
                          ec.layout_with_reason(tree, dates, allow_tails=True))
        assert layout is None and 'carry an outlier model' in reason

    def test_a_phase_beside_the_whole_sequence_is_declined(self):
        sequence, dates = _site([('plain', [3000])])
        beside = _element('phase', 'Beside')
        tree = _element('phase', 'Everything', [sequence, beside])
        dates.append(_date(beside, 3050))
        chain, reason = ec.layout_with_reason(tree, dates, allow_tails=True)
        assert chain is None and 'outside every Boundary' in reason


class TestMatchesBruteForce:
    GRID = np.linspace(3000.0, 3350.0, 10)

    def test_a_chain(self):
        tree, dates, chain = _chain([[3000]], before=[3060], after=[2960], error=30)
        expected = _brute_force(tree, dates, self.GRID, layout=_as_stages(chain))
        solved, densities = ec._solve_on_grid(self.GRID, dates, tree, chain,
                                              inherited_bounds(tree), **OPTIONS)
        self._agree(solved, densities, expected)

    def test_a_chain_with_the_span_correction(self):
        """Two groups: the tails' weights go through the end factor."""
        tree, dates, chain = _chain([[3020], [2990]], before=[3060], after=[2960],
                                    error=30)
        grid = np.linspace(3000.0, 3350.0, 8)
        expected = _brute_force(tree, dates, grid, layout=_as_stages(chain))
        solved, densities = ec._solve_on_grid(grid, dates, tree, chain,
                                              inherited_bounds(tree), **OPTIONS)
        self._agree(solved, densities, expected)

    @pytest.mark.parametrize('stages, M', [
        ([('areas', [[[3000]], [[2950]]], [])], 10),
        ([('plain', [3030])] + [('areas', [[[2990]], [[2960]]], [])], 7),
    ], ids=['one stage', 'two stages'])
    def test_side_by_side_areas(self, stages, M):
        tree, dates = _with_tails(*_site(stages, error=30), before=[3060], after=[2930],
                                  error=30)
        layout, _reason = ep.layout_with_reason(tree, dates)
        grid = np.linspace(3000.0, 3350.0, M)
        expected = _brute_force(tree, dates, grid)
        solved, densities = ep._solve_on_grid(grid, dates, tree, layout,
                                              inherited_bounds(tree), **OPTIONS)
        self._agree(solved, densities, expected)

    @staticmethod
    def _agree(solved, densities, expected):
        ids = list(solved['measurements']) + list(solved['boundaries'])
        assert set(ids) == set(expected)
        for pid, density in zip(ids, densities):
            np.testing.assert_allclose(density / density.sum(),
                                       expected[pid] / expected[pid].sum(),
                                       atol=1e-12, err_msg=pid)


class TestAgreesWithTheReferences:
    @pytest.mark.parametrize('parallel', [False, True], ids=['chain', 'areas'])
    def test_the_boundaries_are_the_cross_checks(self, parallel):
        """services/cross_check.py integrates tails its own way; on its grid
        the two must agree to rounding."""
        if parallel:
            tree, dates = _with_tails(*_site(TWO_AREAS), before=[3150], after=[2850])
            layout, _reason = ep.layout_with_reason(tree, dates)
            solver = ep._solve_on_grid
        else:
            tree, dates, layout = _chain([[3010, 2990], [2950]], before=[3150, 3120],
                                         after=[2850])
            solver = ec._solve_on_grid
        reference = cross_check.reference(dates, tree)
        solved, densities = solver(reference['grid'], dates, tree, layout,
                                   inherited_bounds(tree), **OPTIONS)
        mine = dict(zip(list(solved['measurements']) + list(solved['boundaries']),
                        densities))
        for bid, density in reference['boundaries'].items():
            np.testing.assert_allclose(mine[bid] / mine[bid].sum(), density / density.sum(),
                                       atol=1e-12, err_msg=bid)

    def test_the_dates_are_the_samplers(self):
        tree, dates = _with_tails(*_site([('plain', [3150])] + TWO_AREAS), before=[3250],
                                  after=[2850])
        layout, _reason = ep.layout_with_reason(tree, dates)
        exact = ep.solve(dates, tree, layout, num_samples=50, interval_draws=50)
        sampled = gibbs.solve(dates, tree, sweeps=30000, max_sweeps=30000,
                              num_samples=200, seed=11, chains=1, temperatures=1)
        errors = [sampled['measurements'][d['id']]['posterior_mean']
                  - exact['measurements'][d['id']]['posterior_mean'] for d in dates]
        assert np.sqrt(np.mean(np.square(errors))) < 2.0, np.round(errors, 2)


class TestJointDraws:
    @pytest.mark.parametrize('parallel', [False, True], ids=['chain', 'areas'])
    def test_every_tail_date_is_beyond_its_end_boundary(self, parallel):
        if parallel:
            tree, dates = _with_tails(*_site(TWO_AREAS), before=[3150, 3100],
                                      after=[2850])
            layout, _reason = ep.layout_with_reason(tree, dates)
            solved = ep.solve(dates, tree, layout, num_samples=50, interval_draws=4000)
        else:
            tree, dates, layout = _chain([[3010, 2990], [2950]], before=[3150, 3100],
                                         after=[2850])
            solved = ec.solve(dates, tree, layout, num_samples=50, interval_draws=4000)
        joint = {**solved['joint_boundaries'], **solved['joint_draws']}
        first, last = joint[layout.boundary_ids[0]], joint[layout.boundary_ids[-1]]
        for date_id in layout.older_tail:
            assert np.all(joint[date_id] >= first)
        for date_id in layout.younger_tail:
            assert np.all(joint[date_id] <= last)

    @pytest.mark.parametrize('parallel', [False, True], ids=['chain', 'areas'])
    def test_the_draws_come_from_the_joint_the_marginals_describe(self, parallel):
        grid = np.linspace(3000.0, 3350.0, 8)
        if parallel:
            tree, dates = _with_tails(*_site([('areas', [[[3000]], [[2950]]], [])],
                                             error=30), before=[3060], error=30)
            layout, _reason = ep.layout_with_reason(tree, dates)
            expected = _brute_force(tree, dates, grid)
            solver = ep._solve_on_grid
        else:
            tree, dates, layout = _chain([[3000]], before=[3060], after=[2960], error=30)
            expected = _brute_force(tree, dates, grid, layout=_as_stages(layout))
            solver = ec._solve_on_grid
        solved, _densities = solver(grid, dates, tree, layout, inherited_bounds(tree),
                                    **{**OPTIONS, 'interval_draws': 200000})
        drawn = {**solved['joint_boundaries'], **solved['joint_draws']}
        for pid, values in drawn.items():
            counts = np.bincount(np.searchsorted(grid, values), minlength=len(grid))
            np.testing.assert_allclose(counts / counts.sum(),
                                       expected[pid] / expected[pid].sum(), atol=0.006,
                                       err_msg=pid)


class TestTheAnalysisSolvesThemExactly:
    @pytest.mark.parametrize('parallel', [False, True], ids=['chain', 'areas'])
    def test_automatic(self, client, parallel):
        tree, dates = _with_tails(*_site(TWO_AREAS if parallel else
                                         [('plain', [3000, 2990])]),
                                  before=[3150], after=[2850])
        created = _run(client, tree, dates)
        assert created.status_code == 200, created.text
        solver = created.json()['data']['solver']
        assert solver['method'] == 'exact'
        assert solver['shape'] == ('parallel' if parallel else 'chain')
        assert solver['reason'] is None

    def test_an_outlier_model_out_there_is_sampled_and_says_why(self, client):
        tree, dates = _with_tails(*_site([('plain', [3000, 2990])]), before=[3150],
                                  outlier_prior=0.05)
        created = _run(client, tree, dates)
        assert created.status_code == 200, created.text
        solver = created.json()['data']['solver']
        assert solver['method'] == 'gibbs'
        assert 'outlier model' in solver['reason']


def _bounded_outer(tree, young, old):
    """Limits on the first and last Boundary directly under `tree`."""
    boundaries = [c for c in tree['children'] if c['element_type'] == 'boundary']
    boundaries[0]['tpq_age_bp'], boundaries[-1]['taq_age_bp'] = old, young
    return tree


class TestEvidenceOfTheseShapes:
    """exact_chain._model_evidence on a chain with tails, and on areas."""

    def test_a_tail_leaves_the_prior_unbounded(self):
        tree, dates, chain = _chain([[3000, 2990]], before=[3100], after=[2900])
        _bounded_outer(tree, 2700, 3400)
        evidence = ec.solve(dates, tree, chain, num_samples=10)['evidence']
        assert not evidence['available']
        assert 'outside every Boundary' in evidence['reason']

    def test_areas_side_by_side_have_one(self):
        tree, dates = _site(TWO_AREAS)
        _bounded_outer(tree, 2700, 3400)
        layout, _reason = ep.layout_with_reason(tree, dates)
        evidence = ep.solve(dates, tree, layout, num_samples=10)['evidence']
        assert evidence['available']
        assert (evidence['lower'], evidence['upper']) == (2700, 3400)

    def test_one_area_has_the_evidence_of_its_chain(self):
        tree, dates = _site([('areas', [[[3010, 2990], [2950]]], [])])
        _bounded_outer(tree, 2700, 3400)
        area = tree['children'][1]['children'][0]
        chain_tree = _element('sequence', 'Line', [tree['children'][0]]
                              + area['children'] + [tree['children'][2]])
        layout, _reason = ep.layout_with_reason(tree, dates)
        chain = ec.layout(chain_tree, dates)
        parallel = ep.solve(dates, tree, layout, num_samples=10)['evidence']
        line = ec.solve(dates, chain_tree, chain, num_samples=10)['evidence']
        assert parallel['log'] == pytest.approx(line['log'], abs=0.02)
