"""
The direct calculation a sampled model is checked against (services/cross_check.py).

It is only worth anything if it is the SAME model as the sampler's, so it is
held to the exact solver on a chain, to the sampler on parallel branches, and
shown to flag a sampled answer that is in the wrong place.
"""
import copy
import itertools

import numpy as np
import pytest

from app.services import cross_check
from app.services import exact_chain as ec
from app.services import gibbs

_IDS = itertools.count()


def _element(kind, name, children=None):
    return {'id': f'x{next(_IDS)}', 'element_type': kind, 'name': name,
            'tpq_age_bp': None, 'taq_age_bp': None, 'children': children or []}


def _date(element_id, age_bp, **extra):
    tag = f'xd{next(_IDS)}'
    date = {'id': tag, 'sample_name': tag, 'age_bp': age_bp, 'error_1sigma': 25,
            'curve_id': 'shcal20', 'element_id': element_id}
    date.update(extra)
    return date


def _nested_chain():
    """Two Sequences in a Sequence: one line of Boundaries, written nested."""
    a, b = _element('phase', 'A'), _element('phase', 'B')
    tree = _element('sequence', 'A then B', [
        _element('sequence', 'First', [_element('boundary', 'sA'), a,
                                       _element('boundary', 'eA')]),
        _element('sequence', 'Second', [_element('boundary', 'sB'), b,
                                        _element('boundary', 'eB')]),
    ])
    dates = [_date(a['id'], 900), _date(a['id'], 930),
             _date(b['id'], 700), _date(b['id'], 720)]
    return dates, tree


def _parallel(outliers=False):
    """A site's Start and End around two areas' Sequences."""
    extra = {'outlier_model': 'general', 'outlier_prior': 0.05} if outliers else {}
    a1, a2, b1 = _element('phase', 'A1'), _element('phase', 'A2'), _element('phase', 'B1')
    tree = _element('sequence', 'Site', [
        _element('boundary', 'Site start'),
        _element('phase', 'Activity', [
            _element('sequence', 'Area A', [
                _element('boundary', 'sA'), a1, _element('boundary', 'mA'),
                a2, _element('boundary', 'eA')]),
            _element('sequence', 'Area B', [
                _element('boundary', 'sB'), b1, _element('boundary', 'eB')]),
        ]),
        _element('boundary', 'Site end'),
    ])
    dates = [_date(a1['id'], 660, **extra), _date(a1['id'], 680, **extra),
             _date(a2['id'], 600, **extra), _date(a2['id'], 615, **extra),
             _date(b1['id'], 640, **extra), _date(b1['id'], 650, **extra)]
    return dates, tree


class TestShape:
    def test_a_nested_chain_is_one_chain(self):
        dates, tree = _nested_chain()
        # Exactly solvable as written since the exact solver joined Sequences
        # bracketed one after another (cf1f56b); the check reads it the same way.
        chain = ec.layout(tree, dates)
        assert chain is not None and len(chain.boundary_ids) == 4
        outer, branches, _tails = cross_check.shape(tree, dates)
        assert outer is None and len(branches) == 1
        assert len(branches[0].boundary_ids) == 4

    def test_parallel_areas_are_branches_inside_an_outer_pair(self):
        dates, tree = _parallel()
        outer, branches, _tails = cross_check.shape(tree, dates)
        assert outer[0] == tree['children'][0]['id']
        assert outer[1] == tree['children'][2]['id']
        assert outer[2] == []
        assert [len(b.boundary_ids) for b in branches] == [3, 2]

    def test_anything_else_is_not_checked(self):
        dates, tree = _parallel()
        tree['children'][1]['children'].append(_element('phase', 'Loose phase'))
        with pytest.raises(cross_check.NotChecked):
            cross_check.shape(tree, dates)
        with pytest.raises(cross_check.NotChecked):
            cross_check.shape(_element('phase', 'Just a phase'), dates)


class TestOnlyTheWrittenModel:
    """
    Spelling nested Sequences out in place is only a restatement when each is
    bracketed by Boundaries of its own; otherwise the flattened chain has
    groups the written model does not, and the check must decline rather
    than compare the sampler with a different model.
    """

    def _inner(self, inner_children):
        a = _element('phase', 'A')
        tree = _element('sequence', 'Site', [
            _element('boundary', 'S'), a, _element('boundary', 'M'),
            _element('sequence', 'Inner', inner_children), _element('boundary', 'E')])
        return a, tree

    @pytest.mark.parametrize('layout', ['middle', 'no_close', 'no_open'])
    def test_an_unbracketed_inner_sequence_is_not_checked(self, layout):
        x, y = _element('phase', 'X'), _element('phase', 'Y')
        children = {
            'middle': [x, _element('boundary', 'N'), y],
            'no_close': [_element('boundary', 'N'), x],
            'no_open': [x, _element('boundary', 'N')],
        }[layout]
        a, tree = self._inner(children)
        dates = [_date(a['id'], 900), _date(x['id'], 800), _date(x['id'], 790)]
        if layout == 'middle':
            dates.append(_date(y['id'], 700))
        with pytest.raises(cross_check.NotChecked):
            cross_check.reference(dates, tree)

    def test_a_bracketed_one_still_is(self):
        dates, tree = _nested_chain()
        assert cross_check.reference(dates, tree)['shape'] == 'chain'


class TestTheSameModel:
    def test_it_is_the_exact_solver_on_a_chain(self):
        """The nested chain, spelled out, is something exact_chain solves."""
        dates, tree = _nested_chain()
        flat = copy.deepcopy(tree)
        flat['children'] = [c for s in tree['children'] for c in s['children']]
        exact = ec.solve(dates, flat, ec.layout(flat, dates), interval_draws=200)
        direct = cross_check.reference(dates, tree)
        for boundary_id, density in direct['boundaries'].items():
            mean = float((direct['grid'] * density).sum())
            assert mean == pytest.approx(
                exact['boundaries'][boundary_id]['posterior']['posterior_mean'], abs=1.0)

    @pytest.mark.parametrize('outliers', [False, True])
    def test_it_is_the_sampler_on_parallel_branches(self, outliers):
        """
        With the outlier model on, the sampler shares one offset scale across
        every date, which the direct calculation can only match by integrating
        over that scale -- the case that would drift if it were done wrong.
        """
        dates, tree = _parallel(outliers)
        # Four chains: one chain of this length puts the median Boundary 0.5
        # to 3.4 years from the direct answer, either side, by noise alone.
        sampled = gibbs.solve(dates, tree, sweeps=20000, max_sweeps=20000,
                              num_samples=200, chains=4)
        record = cross_check.check(dates, tree, sampled)
        assert record['status'] == 'agrees', record
        assert record['checked'] == 7
        assert record['worst']['distance'] < 0.08
        # Typical rather than worst: an outer Boundary's mean rides on a long
        # tail and moves several years with Monte Carlo noise alone.
        rows = cross_check.compare(cross_check.reference(dates, tree), sampled['boundaries'])
        assert np.median([abs(r['sampled_mean'] - r['direct_mean']) for r in rows]) < 2.0


def _ordered_phases(ages=((700, 720), (640, 650))):
    """Phases in a Sequence with NO Boundary between them: one Boundary pair,
    and every date of one phase older than every date of the next."""
    phases = [_element('phase', f'Phase {i}') for i in range(len(ages))]
    tree = _element('sequence', 'Site', [_element('boundary', 'S'), *phases,
                                         _element('boundary', 'E')])
    dates = [_date(phase['id'], age) for phase, group in zip(phases, ages) for age in group]
    return dates, tree


class _Reach:
    """Just enough of a gibbs.Model for _blocks: what each date is older than."""

    def __init__(self, younger):
        self._younger = younger

    def _younger_reachable(self):
        return {d: set(y) for d, y in self._younger.items()}


class TestOrderedGroups:
    """
    Dates ordered against each other inside one pair of Boundaries -- the shape
    of the Nenthorn reference model (a project run through OxCal and kept as a
    fixture), and one the exact solver declines ("two groups are ordered against
    each other with no Boundary between them").
    """

    def test_the_exact_solver_declines_it_and_the_check_reads_it_as_a_chain(self):
        dates, tree = _ordered_phases()
        assert ec.layout(tree, dates) is None
        outer, branches, _tails = cross_check.shape(tree, dates)
        assert outer is None and len(branches) == 1
        assert len(branches[0].boundary_ids) == 2
        assert len(branches[0].groups) == 1 and len(branches[0].groups[0]) == 4

    def test_blocks_come_oldest_first(self):
        model = _Reach({'a1': {'b1', 'b2'}, 'a2': {'b1', 'b2'}, 'b1': set(), 'b2': set()})
        blocks = cross_check._blocks(model, ['b2', 'a1', 'b1', 'a2'])
        assert [sorted(b) for b in blocks] == [['a1', 'a2'], ['b1', 'b2']]

    def test_three_blocks_in_any_listing_order(self):
        model = _Reach({'a': {'b', 'c'}, 'b': {'c'}, 'c': set()})
        assert cross_check._blocks(model, ['c', 'a', 'b']) == [['a'], ['b'], ['c']]

    def test_dates_ordered_in_any_other_way_are_not_checked(self):
        # a is older than b, and c is unrelated to both: not blocks.
        model = _Reach({'a': {'b'}, 'b': set(), 'c': set()})
        with pytest.raises(cross_check.NotChecked):
            cross_check._blocks(model, ['a', 'b', 'c'])

    def test_it_matches_a_brute_force_sum_over_every_placement(self):
        """
        Two dates, the first phase's older than the second's, so
        p(S, E) is proportional to the span correction times (S - E)^-2 times
        the sum of L_a(x) L_b(y) over E <= y < x <= S. Summed by a different
        route, a 2-D running sum. The correction is the sampler's own row for
        the pair, which is what keeps the improper tails of two dates and a
        pair of Boundaries from running off.
        """
        dates, tree = _ordered_phases(((700,), (640,)))
        direct = cross_check.reference(dates, tree)
        grid = direct['grid']
        model = gibbs.Model(dates, tree, grid=grid)
        a, b = (model.likelihood[d['id']] for d in dates)
        weight = np.tril(np.outer(a, b), -1) * model.dt ** 2       # x older than y
        running = np.zeros((model.M + 1, model.M + 1))
        running[1:, 1:] = weight.cumsum(0).cumsum(1)
        start, end = tree['children'][0]['id'], tree['children'][3]['id']
        joint = np.zeros((model.M, model.M))
        for s in range(model.M):
            for e in range(s):
                inside = (running[s + 1, s + 1] - running[e, s + 1]
                          - running[s + 1, e] + running[e, e])
                joint[s, e] = (inside / ((s - e) * model.dt) ** 2
                               * np.exp(model.span_prior_rows[0][s - e]))
        low = float(grid[0])
        count = int(np.ceil((grid[-1] - low) / cross_check.REPORTING_STEP_YEARS)) + 1
        for boundary_id, marginal in ((start, joint.sum(axis=1)), (end, joint.sum(axis=0))):
            theirs = cross_check._binned(grid, marginal / marginal.sum(), low, count)
            ours = cross_check._binned(grid, direct['boundaries'][boundary_id], low, count)
            assert 0.5 * np.abs(theirs - ours).sum() < 0.04

    def test_it_agrees_with_the_sampler_on_the_oxcal_nenthorn_model(self):
        import oxcal_fixture as ox
        dates, tree = ox.measurements(), ox.model_tree('ordered')
        assert ec.layout(tree, dates) is None
        sampled = gibbs.solve(dates, tree, sweeps=20000, max_sweeps=20000,
                              num_samples=200, chains=4)
        record = cross_check.check(dates, tree, sampled)
        assert record['status'] == 'agrees', record
        assert record['checked'] == 2
        assert record['worst']['distance'] < 0.08

    def test_it_agrees_with_the_sampler_when_the_phases_are_unordered(self):
        import oxcal_fixture as ox
        dates, tree = ox.measurements(), ox.model_tree('unordered')
        sampled = gibbs.solve(dates, tree, sweeps=20000, max_sweeps=20000,
                              num_samples=200, chains=4)
        record = cross_check.check(dates, tree, sampled)
        assert record['status'] == 'agrees', record
        assert record['worst']['distance'] < 0.08

    def test_a_flagged_answer_is_reported_on_the_ordered_model(self):
        dates, tree = _ordered_phases()
        sampled = gibbs.solve(dates, tree, sweeps=8000, max_sweeps=8000,
                              num_samples=200, chains=1)
        moved = copy.deepcopy(sampled['boundaries'])
        end = tree['children'][3]['id']
        points = moved[end]['posterior']['probability_distribution']
        moved[end]['posterior']['probability_distribution'] = [[x + 60.0, p] for x, p in points]
        record = cross_check.check(dates, tree, {'boundaries': moved})
        assert record['status'] == 'differs'
        assert [row['id'] for row in record['differing']] == [end]


class TestItFlagsAWrongAnswer:
    def test_a_boundary_in_the_wrong_place_is_reported(self):
        dates, tree = _parallel()
        sampled = gibbs.solve(dates, tree, sweeps=8000, max_sweeps=8000,
                              num_samples=200, chains=1)
        moved = copy.deepcopy(sampled['boundaries'])
        site_start = tree['children'][0]['id']
        points = moved[site_start]['posterior']['probability_distribution']
        moved[site_start]['posterior']['probability_distribution'] = [
            [x - 60.0, p] for x, p in points]
        record = cross_check.check(dates, tree, {'boundaries': moved})
        assert record['status'] == 'differs'
        assert [row['id'] for row in record['differing']] == [site_start]

    def test_a_small_lobe_moved_is_reported_though_total_variation_passes(self):
        """
        Total variation cannot see a small calibration lobe the sampler
        misplaces; the largest difference in the probability of being older
        than a given year can. Move 8% of the Site start's mass 250 years older,
        well clear of the rest: total variation stays under its 0.15 limit,
        the threshold gap does not.
        """
        dates, tree = _parallel()
        sampled = gibbs.solve(dates, tree, sweeps=8000, max_sweeps=8000,
                              num_samples=200, chains=1)
        moved = copy.deepcopy(sampled['boundaries'])
        site_start = tree['children'][0]['id']
        points = np.array(moved[site_start]['posterior']['probability_distribution'])
        step = float(np.median(np.diff(points[:, 0])))
        ages = np.arange(points[0, 0], points[-1, 0] + 250.0 + step, step)
        original = np.interp(ages, points[:, 0], points[:, 1], left=0.0, right=0.0)
        older = np.interp(ages - 250.0, points[:, 0], points[:, 1], left=0.0, right=0.0)
        moved[site_start]['posterior']['probability_distribution'] = [
            [float(x), float(p)] for x, p in zip(ages, 0.92 * original + 0.08 * older)]
        # As from a run of several chains, the only kind the gap is held on.
        record = cross_check.check(dates, tree, {'boundaries': moved, 'chains': 4})
        row = next(r for r in record['differing'] if r['id'] == site_start)
        assert row['distance'] < cross_check.DISAGREEMENT
        assert row['threshold_gap'] > cross_check.THRESHOLD_DISAGREEMENT
        assert record['status'] == 'differs'

    def test_a_model_it_cannot_integrate_says_why(self):
        dates, tree = _parallel()
        tree['children'][1]['children'].append(_element('phase', 'Loose phase'))
        record = cross_check.check(dates, tree, {'boundaries': {}})
        assert record['status'] == 'not_checked'
        assert 'Loose phase' in record['reason']


def _with_tails(older=(), younger=(), ordered_older=False):
    """
    A phase between Start and End, with dates outside every Boundary: before
    Start (older) and after End (younger). `ordered_older` writes the older
    dates as two Phases one after the other, so they are blocks.
    """
    middle = _element('phase', 'Occupation')
    children = []
    dates = []
    if older:
        if ordered_older:
            first, second = _element('phase', 'Earliest'), _element('phase', 'Earlier')
            children += [first, second]
            dates += [_date(first['id'], older[0]), *[_date(second['id'], a) for a in older[1:]]]
        else:
            pre = _element('phase', 'Before')
            children.append(pre)
            dates += [_date(pre['id'], a) for a in older]
    children += [_element('boundary', 'Start'), middle, _element('boundary', 'End')]
    dates += [_date(middle['id'], a) for a in (660, 640, 620)]
    if younger:
        post = _element('phase', 'After')
        children.append(post)
        dates += [_date(post['id'], a) for a in younger]
    return dates, _element('sequence', 'Site', children)


class TestDatesOutsideEveryBoundary:
    def test_they_are_taken_off_as_tails(self):
        dates, tree = _with_tails(older=(720, 700), younger=(560,))
        outer, branches, tails = cross_check.shape(tree, dates)
        assert outer is None and len(branches[0].boundary_ids) == 2
        assert len(tails[0]) == 2 and len(tails[1]) == 1

    def test_a_tail_moves_its_boundary(self):
        """
        A date older than Start but close to the occupation pulls Start
        younger than the occupation alone would put it: the tail's weight is
        doing work, so it is not being ignored.
        """
        alone_dates, alone_tree = _with_tails()
        tail_dates, tail_tree = _with_tails(older=(665,))
        alone = cross_check.reference(alone_dates, alone_tree)
        tailed = cross_check.reference(tail_dates, tail_tree)
        start_alone = alone_tree['children'][0]['id']
        start_tailed = tail_tree['children'][1]['id']

        def mean(direct, boundary_id):
            return float((direct['grid'] * direct['boundaries'][boundary_id]).sum())

        assert mean(tailed, start_tailed) < mean(alone, start_alone) - 5
        assert tailed['outside'] == 1

    @pytest.mark.parametrize('older, younger, ordered', [
        ((720, 700), (), False),
        ((), (560, 590), False),
        ((730, 700, 690), (570,), True),
    ])
    def test_it_agrees_with_the_sampler(self, older, younger, ordered):
        dates, tree = _with_tails(older=older, younger=younger, ordered_older=ordered)
        sampled = gibbs.solve(dates, tree, sweeps=20000, max_sweeps=20000,
                              num_samples=200, chains=4)
        record = cross_check.check(dates, tree, sampled)
        assert record['status'] == 'agrees', record
        assert record['checked'] == 2
        assert record['worst']['distance'] < 0.08

    def test_a_wrong_answer_is_still_flagged(self):
        dates, tree = _with_tails(older=(720,), younger=(560,))
        sampled = gibbs.solve(dates, tree, sweeps=8000, max_sweeps=8000,
                              num_samples=200, chains=1)
        moved = copy.deepcopy(sampled['boundaries'])
        start = tree['children'][1]['id']
        points = moved[start]['posterior']['probability_distribution']
        moved[start]['posterior']['probability_distribution'] = [[x + 60.0, p] for x, p in points]
        record = cross_check.check(dates, tree, {'boundaries': moved})
        assert record['status'] == 'differs'
        assert [row['id'] for row in record['differing']] == [start]

    def test_one_with_an_outlier_model_is_not_checked(self):
        dates, tree = _with_tails(older=(720,))
        dates[0].update(outlier_model='general', outlier_prior=0.05)
        record = cross_check.check(dates, tree, {'boundaries': {}})
        assert record['status'] == 'not_checked'
        assert 'outlier' in record['reason']

    def test_a_model_with_no_boundary_says_so(self):
        phase = _element('phase', 'Only')
        tree = _element('sequence', 'Dates', [phase])
        dates = [_date(phase['id'], 700), _date(phase['id'], 650)]
        record = cross_check.check(dates, tree, {'boundaries': {}})
        assert record['status'] == 'not_checked'
        assert 'no Boundary' in record['reason']
