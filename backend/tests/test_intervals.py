"""
Durations and differences (services/intervals.py) and the joint sampling that
feeds them.

Four things are checked, in increasing order of what they'd catch:

  * the STRUCTURE: which durations a model has and which pairs of phases are
    comparable. Pure tree walking, so it is asserted exactly.
  * the JOINT SAMPLER in exact_chain, against distributions written out by
    hand. This is the one that matters. Every duration is a function of two or
    more parameters at once, so a sampler that got the marginals right and the
    correlation wrong would produce plausible-looking, badly wrong durations
    with nothing else in the suite to notice.
  * the durations themselves, against the two facts that hold whatever the
    correlation is: a mean difference is the difference of the means, and a
    phase is at least as long as the spread of the dates inside it.
  * the two solvers against each other, which is how every other quantity in
    this app is cross-checked.
"""
import csv
import io

import numpy as np
import pytest

from app.services import exact_chain as ec
from app.services import gibbs
from app.services import intervals


def _phase(eid, name='Phase'):
    return {'id': eid, 'element_type': 'phase', 'name': name,
            'tpq_age_bp': None, 'taq_age_bp': None, 'children': []}


def _boundary(eid, name='Boundary'):
    return {'id': eid, 'element_type': 'boundary', 'name': name,
            'tpq_age_bp': None, 'taq_age_bp': None, 'children': []}


def _hiatus(eid, name='Hiatus'):
    return {'id': eid, 'element_type': 'hiatus', 'name': name,
            'tpq_age_bp': None, 'taq_age_bp': None, 'children': []}


def _sequence(eid, children, name='Sequence'):
    return {'id': eid, 'element_type': 'sequence', 'name': name,
            'tpq_age_bp': None, 'taq_age_bp': None, 'children': children}


def _dates(*specs):
    return [{'id': i, 'sample_name': i, 'age_bp': a, 'error_1sigma': e,
             'curve_id': 'intcal20', 'element_id': p}
            for i, a, e, p in specs]


def _by_kind(records, kind):
    return [r for r in records if r['kind'] == kind]


# --------------------------------------------------------------------------

class TestWhatAModelHasToReport:
    """Which durations exist, before any arithmetic happens."""

    def test_a_single_bounded_phase_has_a_phase_and_a_sample_duration(self):
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        specs = intervals.plan_durations(
            tree, _dates(('d1', 3000, 30, 'p'), ('d2', 3050, 30, 'p')))
        assert [s['kind'] for s in specs] == ['phase_duration', 'sample_duration']
        assert specs[0]['boundaries'] == ('b0', 'b1')
        assert specs[1]['dates'] == ('d1', 'd2')

    def test_a_sequence_of_one_group_reports_no_sequence_duration(self):
        """It would be the phase duration under a second name."""
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        specs = intervals.plan_durations(tree, _dates(('d1', 3000, 30, 'p')))
        assert _by_kind(specs, 'sequence_duration') == []

    def test_a_longer_sequence_reports_its_own_duration_end_to_end(self):
        tree = _sequence('s', [_boundary('b0'), _phase('p1'), _boundary('b1'),
                               _phase('p2'), _boundary('b2')])
        specs = intervals.plan_durations(
            tree, _dates(('d1', 3000, 30, 'p1'), ('d2', 2800, 30, 'p2')))
        sequence = _by_kind(specs, 'sequence_duration')
        assert len(sequence) == 1
        assert sequence[0]['boundaries'] == ('b0', 'b2')
        assert [s['name'] for s in _by_kind(specs, 'phase_duration')] == ['Phase', 'Phase']

    def test_a_lone_date_has_no_span_to_report(self):
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        specs = intervals.plan_durations(tree, _dates(('d1', 3000, 30, 'p')))
        assert _by_kind(specs, 'sample_duration') == []

    def test_a_hiatus_is_reported_as_its_own_duration(self):
        """
        Two Boundaries with only a Hiatus between them bracket a GAP, not a
        phase. It is reported as a hiatus duration, in place between the
        phases either side, and named by what it separates.
        """
        tree = _sequence('s', [
            _boundary('b0'), _phase('p1', 'Lower'), _boundary('b1'), _hiatus('h'),
            _boundary('b2'), _phase('p2', 'Upper'), _boundary('b3')])
        measurements = _dates(('d1', 3000, 30, 'p1'), ('d2', 2600, 30, 'p2'))
        specs = intervals.plan_durations(tree, measurements)
        bracketed = [(s['kind'], s['boundaries']) for s in specs
                     if s['kind'] in ('phase_duration', 'hiatus_duration')]
        assert bracketed == [('phase_duration', ('b0', 'b1')),
                             ('hiatus_duration', ('b1', 'b2')),
                             ('phase_duration', ('b2', 'b3'))]
        [gap] = _by_kind(specs, 'hiatus_duration')
        assert gap['name'] == 'Lower \u2192 Upper'
        assert gap['element_id'] == 'h'

    def test_a_hiatus_named_by_the_analyst_keeps_its_name(self):
        tree = _sequence('s', [
            _boundary('b0'), _phase('p1'), _boundary('b1'), _hiatus('h', 'Abandonment'),
            _boundary('b2'), _phase('p2'), _boundary('b3')])
        [gap] = _by_kind(intervals.plan_durations(tree, []), 'hiatus_duration')
        assert gap['name'] == 'Abandonment'

    def test_two_adjacent_boundaries_are_a_gap_without_a_hiatus_drawn(self):
        """The Hiatus carries no parameter, so the gap is the same without it."""
        tree = _sequence('s', [
            _boundary('b0'), _phase('p1'), _boundary('b1'),
            _boundary('b2'), _phase('p2'), _boundary('b3')])
        [gap] = _by_kind(intervals.plan_durations(tree, []), 'hiatus_duration')
        assert gap['boundaries'] == ('b1', 'b2')
        assert gap['element_id'] is None

    def test_a_hiatus_duration_is_the_gap_between_its_boundaries(self):
        tree = _sequence('s', [
            _boundary('b0'), _phase('p1'), _boundary('b1'), _hiatus('h'),
            _boundary('b2'), _phase('p2'), _boundary('b3')])
        specs = _by_kind(intervals.plan_durations(tree, []), 'hiatus_duration')
        draws = {'b0': np.full(500, 3400.0), 'b1': np.full(500, 3300.0),
                 'b2': np.full(500, 3180.0), 'b3': np.full(500, 3100.0)}
        [gap], _differences = intervals.evaluate(specs, [], draws)
        assert gap['kind'] == 'hiatus_duration'
        assert gap['years']['mean'] == pytest.approx(120.0)

    def test_phases_in_one_sequence_are_not_compared(self):
        """
        The Sequence orders every phase in it, so no two can overlap: a pair
        sharing a Boundary has no gap by construction, and any other gap is
        the durations between them, Hiatus included.
        """
        tree = _sequence('s', [
            _boundary('b0'), _phase('p1'), _boundary('b1'), _phase('p2'),
            _boundary('b2'), _hiatus('h'), _boundary('b3'), _phase('p3'),
            _boundary('b4')])
        specs, note = intervals.plan_phase_differences(
            tree, _dates(('d1', 3000, 30, 'p1'), ('d2', 2800, 30, 'p3')))
        assert specs == []
        assert note is None

    def test_phases_in_parallel_branches_are_compared(self):
        """
        Two sequences side by side in one phase leave the order between their
        phases open -- the only place the overlap is a finding. Within each
        branch the order is still settled.
        """
        tree = _sequence('s', [
            _boundary('start'),
            {'id': 'site', 'element_type': 'phase', 'name': 'Site',
             'tpq_age_bp': None, 'taq_age_bp': None, 'children': [
                 _sequence('north', [_boundary('n0'), _phase('n1', 'North 1'),
                                     _boundary('n2'), _phase('n3', 'North 2'),
                                     _boundary('n4')]),
                 _sequence('south', [_boundary('s0'), _phase('s1', 'South'),
                                     _boundary('s2')]),
             ]},
            _boundary('end'),
            _phase('later', 'Later'),
            _boundary('last')])
        specs, _note = intervals.plan_phase_differences(tree, [])
        assert {(s['from_name'], s['to_name']) for s in specs} == {
            ('North 1', 'South'), ('North 2', 'South')}

    def test_a_phase_nested_inside_another_is_not_compared(self):
        """Its interval sits inside the outer one, so they overlap by construction."""
        tree = _sequence('s', [
            _boundary('b0'),
            {'id': 'outer', 'element_type': 'phase', 'name': 'Outer',
             'tpq_age_bp': None, 'taq_age_bp': None, 'children': [
                 _sequence('inner', [_boundary('i0'), _phase('p', 'Inner'),
                                     _boundary('i1')])]},
            _boundary('b1')])
        specs, _note = intervals.plan_phase_differences(tree, [])
        assert specs == []

    def test_phases_sharing_a_bracket_are_compared_by_their_dates(self):
        """
        Two phases inside one pair of Boundaries have no interval of their
        own -- the bracket is the outer phase's -- so their dated events stand
        in. The outer phase contains both, so it is not compared with either.
        """
        tree = _sequence('s', [
            _boundary('b0'),
            {'id': 'both', 'element_type': 'phase', 'name': 'Areas',
             'tpq_age_bp': None, 'taq_age_bp': None,
             'children': [_phase('p1', 'East'), _phase('p2', 'West')]},
            _boundary('b1')])
        specs, _note = intervals.plan_phase_differences(
            tree, _dates(('d1', 3000, 30, 'p1'), ('d2', 2900, 30, 'p2'),
                         ('d3', 2950, 30, 'p2')))
        [spec] = specs
        assert (spec['from_name'], spec['to_name']) == ('East', 'West')
        assert spec['from_dates'] == ('d1',)
        assert spec['to_dates'] == ('d2', 'd3')
        assert 'from_boundaries' not in spec

    def test_a_single_date_area_is_compared_with_the_other_areas(self):
        """
        The site as excavated: areas side by side in one site phase, some
        with their own stratigraphy and Boundaries, some a Sequence holding
        one undivided phase and one date.
        """
        tree = _sequence('s', [
            _boundary('start'),
            {'id': 'site', 'element_type': 'phase', 'name': 'Site',
             'tpq_age_bp': None, 'taq_age_bp': None, 'children': [
                 _sequence('a', [_phase('pa', 'Area A')]),
                 _sequence('b', [_boundary('b0'), _phase('pb1', 'B lower'),
                                 _boundary('b1'), _phase('pb2', 'B upper'),
                                 _boundary('b2')]),
                 _sequence('c', [_phase('pc', 'Area C')]),
             ]},
            _boundary('end')])
        measurements = _dates(('da', 3000, 30, 'pa'), ('db1', 3100, 30, 'pb1'),
                              ('db2', 2900, 30, 'pb2'), ('dc', 2950, 30, 'pc'))
        specs, _note = intervals.plan_phase_differences(tree, measurements)
        assert {(s['from_name'], s['to_name']) for s in specs} == {
            ('Area A', 'B lower'), ('Area A', 'B upper'), ('Area A', 'Area C'),
            ('B lower', 'Area C'), ('B upper', 'Area C')}

    def test_a_date_inside_a_phase_overlaps_it(self):
        draws = {'o': np.full(1000, 3400.0), 'y': np.full(1000, 3200.0),
                 'd': np.concatenate([np.full(600, 3300.0), np.full(400, 3150.0)])}
        spec = {'kind': 'phase_difference', 'id': 'x',
                'from_id': 'a', 'from_name': 'A', 'to_id': 'b', 'to_name': 'B',
                'from_boundaries': ('o', 'y'), 'to_dates': ('d',)}
        _d, [record] = intervals.evaluate([], [spec], draws)
        assert record['overlap'] == pytest.approx(0.6)
        assert record['to_dates'] == 1
        assert 'from_dates' not in record

        draws['d'] = np.full(1000, 3200.0)       # on the phase's end
        _d, [record] = intervals.evaluate([], [spec], draws)
        assert record['overlap'] == 1.0

    def test_two_single_dates_overlap_as_far_as_they_cannot_be_ordered(self):
        rng = np.random.default_rng(4)
        draws = {'d1': rng.normal(3000, 40, 20000), 'd2': rng.normal(3000, 40, 20000)}
        spec = {'kind': 'phase_difference', 'id': 'x',
                'from_id': 'a', 'from_name': 'A', 'to_id': 'b', 'to_name': 'B',
                'from_dates': ('d1',), 'to_dates': ('d2',)}
        _d, [record] = intervals.evaluate([], [spec], draws)
        assert record['overlap'] == pytest.approx(1.0, abs=0.03)
        assert record['years']['mean'] > 0, 'the gap between two points'


class TestPhaseActivity:
    """Which phases were in progress together, year by year."""

    def _tree(self):
        return _sequence('s', [
            _boundary('start'),
            {'id': 'site', 'element_type': 'phase', 'name': 'Site',
             'tpq_age_bp': None, 'taq_age_bp': None, 'children': [
                 _sequence('a', [_boundary('a0'), _phase('pa', 'Area A'),
                                 _boundary('a1')]),
                 _sequence('b', [_phase('pb', 'Area B')]),
             ]},
            _boundary('end')])

    def test_one_row_per_phase_in_model_order(self):
        rows = intervals.plan_phase_activity(
            self._tree(), _dates(('da', 3000, 30, 'pa'), ('db', 2950, 30, 'pb')))
        assert [(r['name'], r.get('boundaries'), r.get('dates')) for r in rows] == [
            ('Site', ('start', 'end'), None),
            ('Area A', ('a0', 'a1'), None),
            ('Area B', None, ('db',))]

    def test_a_phase_is_in_progress_between_its_boundaries(self):
        n = 1000
        draws = {'a0': np.full(n, 3300.0), 'a1': np.full(n, 3200.0),
                 'start': np.full(n, 3400.0), 'end': np.full(n, 3100.0),
                 'db': np.full(n, 3250.0)}
        rows = intervals.plan_phase_activity(
            self._tree(), _dates(('db', 2950, 30, 'pb')))
        block = intervals.evaluate_phase_activity(rows, draws)
        ages = [block['start'] - i * block['step'] for i in range(len(block['rows'][0]['values']))]
        area_a = dict(zip(ages, block['rows'][1]['values']))
        assert area_a[3250.0] == 1.0
        assert area_a[3300.0] == 1.0 and area_a[3200.0] == 1.0
        assert area_a[3350.0] == 0.0 and area_a[3150.0] == 0.0

        area_b = block['rows'][2]
        assert area_b['relative'] is True
        assert area_b['dates'] == 1
        assert max(area_b['values']) == 1.0
        assert dict(zip(ages, area_b['values']))[3250.0] == 1.0

    def test_a_linear_model_gets_one_too(self):
        """Along one Sequence the strips draw the phases succeeding each other."""
        rng = np.random.default_rng(2)
        n = 500
        older = rng.normal(3300, 10, n)
        draws = {'b0': older + 100, 'b1': older, 'b2': older - 100, 'd': older + 50}
        linear = _sequence('s', [_boundary('b0'), _phase('p1', 'Lower'), _boundary('b1'),
                                 _phase('p2', 'Upper'), _boundary('b2')])
        block = intervals.report(linear, _dates(('d', 3000, 30, 'p1')), draws, True)
        assert [row['name'] for row in block['phase_activity']['rows']] == ['Lower', 'Upper']
        assert block['differences'] == []

        unasked = intervals.report(linear, _dates(('d', 3000, 30, 'p1')), draws, True,
                                   durations=False)
        assert unasked['phase_activity'] is None

    def test_the_pdf_draws_it(self):
        from app.services import pdf_report
        block = {'start': 3300.0, 'step': 1.0, 'rows': [
            {'id': 'a', 'name': 'Lower', 'values': [0.0, 0.5, 1.0, 0.5]},
            {'id': 'b', 'name': 'Area B', 'dates': 1, 'relative': True,
             'values': [0.2, 1.0, 0.2, 0.0]}]}
        png, height = pdf_report._phase_activity_figure(block, 'calBP')
        assert png.startswith(b'\x89PNG')
        assert height > 0
        assert pdf_report._phase_activity_figure({**block, 'rows': block['rows'][:1]},
                                                 'calBP') is None


class TestSeverity:
    def test_the_bands_are_the_reporting_confidences(self):
        assert intervals.severity(0.0) == 'none'
        assert intervals.severity(0.045) == 'none'
        assert intervals.severity(0.05) == 'slight'
        assert intervals.severity(0.5) == 'moderate'
        assert intervals.severity(0.8) == 'substantial'
        assert intervals.severity(0.96) == 'extensive'
        assert intervals.severity(1.0) == 'extensive'

    def test_every_band_has_a_word(self):
        found = {intervals.severity(p) for p in np.linspace(0, 1, 501)}
        assert found == set(intervals.SEVERITY_WORDS)


class TestTheJointSampler:
    """
    exact_chain's backward sampling, against distributions written out by hand.

    This is the load-bearing test of the whole feature. The marginals were
    already right before any of it, and the durations are functions of the
    CORRELATION between them, so a sampler that drew each boundary from its own
    marginal would pass every other assertion here while making every phase
    look several times longer than it is.
    """

    def _chain(self, boundary_ids, groups):
        return ec.Chain(boundary_ids, groups,
                        {b: b for b in boundary_ids})

    def test_one_group_reproduces_its_transfer_matrix(self):
        rng = np.random.default_rng(0)
        size = 6
        # An arbitrary strictly-positive T, so nothing can pass by symmetry.
        matrix = rng.random((size, size)) + 0.05      # [younger, older]
        chain = self._chain(['b0', 'b1'], [['d']])
        allowed = {'b0': np.ones(size, bool), 'b1': np.ones(size, bool)}
        _pair, _density, draw, _log_mass = ec._messages(chain, allowed, lambda i: matrix)

        drawn = draw(np.random.default_rng(1), 200000)
        empirical = np.zeros((size, size))
        np.add.at(empirical, (drawn[:, 1], drawn[:, 0]), 1.0)
        empirical /= empirical.sum()

        assert np.allclose(empirical, matrix / matrix.sum(), atol=0.004)

    def test_two_groups_reproduce_the_product_of_their_transfers(self):
        rng = np.random.default_rng(2)
        size = 5
        first = rng.random((size, size)) + 0.05
        second = rng.random((size, size)) + 0.05
        transfers = [first, second]
        chain = self._chain(['b0', 'b1', 'b2'], [['d1'], ['d2']])
        allowed = {b: np.ones(size, bool) for b in chain.boundary_ids}
        _pair, _density, draw, _log_mass = ec._messages(
            chain, allowed, lambda i: transfers[i])

        drawn = draw(np.random.default_rng(3), 300000)
        empirical = np.zeros((size, size, size))
        np.add.at(empirical, (drawn[:, 0], drawn[:, 1], drawn[:, 2]), 1.0)
        empirical /= empirical.sum()

        # p(b0, b1, b2) proportional to T0(b0, b1) T1(b1, b2).
        expected = (first.T[:, :, None] * second.T[None, :, :])
        expected /= expected.sum()
        assert np.allclose(empirical, expected, atol=0.004)

    def test_a_forbidden_boundary_position_is_never_drawn(self):
        size = 6
        matrix = np.ones((size, size))
        chain = self._chain(['b0', 'b1'], [['d']])
        allowed = {'b0': np.array([False, False, True, True, True, True]),
                   'b1': np.ones(size, bool)}
        _pair, _density, draw, _log_mass = ec._messages(chain, allowed, lambda i: matrix)
        drawn = draw(np.random.default_rng(4), 5000)
        assert drawn[:, 0].min() >= 2

    def test_the_span_prior_path_reproduces_its_own_joint(self):
        """
        With the UniformSpanPrior correction the chain is no longer Markov, so
        the two ends are drawn together and the interior filled in between
        them. Different code, same obligation.
        """
        rng = np.random.default_rng(5)
        size = 5
        first = rng.random((size, size)) + 0.05
        second = rng.random((size, size)) + 0.05
        correction = rng.random((size, size)) + 0.05     # [younger, older]
        transfers = [first, second]
        chain = self._chain(['b0', 'b1', 'b2'], [['d1'], ['d2']])
        allowed = {b: np.ones(size, bool) for b in chain.boundary_ids}
        _pair, _density, draw, _log_mass = ec._messages_with_span_prior(
            chain, allowed, lambda i: transfers[i], correction)

        drawn = draw(np.random.default_rng(6), 300000)
        empirical = np.zeros((size, size, size))
        np.add.at(empirical, (drawn[:, 0], drawn[:, 1], drawn[:, 2]), 1.0)
        empirical /= empirical.sum()

        expected = (first.T[:, :, None] * second.T[None, :, :]
                    * correction.T[:, None, :])
        expected /= expected.sum()
        assert np.allclose(empirical, expected, atol=0.004)


class TestDatesLandWhereTheyAreAllowed:
    def test_a_date_is_always_between_the_boundaries_drawn_with_it(self):
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        measurements = _dates(('d1', 3000, 30, 'p'), ('d2', 3060, 30, 'p'))
        chain = ec.layout(tree, measurements)
        grid = ec._build_grid(measurements, {}, 1)
        likelihood, cumulative, _outliers = ec.date_tables(grid, measurements, {})

        rng = np.random.default_rng(7)
        # Boundary index pairs picked directly, so this tests the truncation
        # rather than whatever the solver would have drawn.
        count = 2000
        boundaries = np.empty((count, 2), dtype=np.int64)
        boundaries[:, 0] = rng.integers(len(grid) // 2, len(grid), count)
        boundaries[:, 1] = rng.integers(0, len(grid) // 2, count)

        drawn = ec._draw_dates(rng, chain, cumulative, boundaries,
                               likelihood, float(grid[1] - grid[0]))
        for date_id in ('d1', 'd2'):
            assert np.all(drawn[date_id] >= boundaries[:, 1])
            assert np.all(drawn[date_id] <= boundaries[:, 0])


class TestTheDurationsThemselves:
    """
    Facts that hold whatever the posterior looks like, so they pin the answer
    without pinning it to today's numbers.
    """

    def _solved(self):
        tree = _sequence('s', [_boundary('b0'), _phase('p1'), _boundary('b1'),
                               _phase('p2'), _boundary('b2')])
        measurements = _dates(
            ('a1', 3200, 30, 'p1'), ('a2', 3180, 30, 'p1'), ('a3', 3210, 30, 'p1'),
            ('c1', 2800, 30, 'p2'), ('c2', 2820, 30, 'p2'), ('c3', 2790, 30, 'p2'))
        chain = ec.layout(tree, measurements)
        solved = ec.solve(measurements, tree, chain, estimate_durations=True)
        return tree, measurements, solved

    def test_a_duration_is_the_difference_of_the_boundary_means(self):
        """
        Linearity of expectation holds however correlated the two boundaries
        are, so this checks the draws carry the right MARGINALS without saying
        anything about the correlation -- which the joint sampler tests above
        are for.
        """
        _tree, _measurements, solved = self._solved()
        by_id = {d['id']: d for d in solved['intervals']['durations']}
        posteriors = solved['boundaries']

        sequence = by_id['sequence:s']['years']['mean']
        ends = (posteriors['b0']['posterior']['posterior_mean']
                - posteriors['b2']['posterior']['posterior_mean'])
        assert sequence == pytest.approx(ends, abs=5.0)

        first = by_id['phase:b0:b1']['years']['mean']
        assert first == pytest.approx(
            posteriors['b0']['posterior']['posterior_mean']
            - posteriors['b1']['posterior']['posterior_mean'], abs=5.0)

    def test_a_phase_lasts_at_least_as_long_as_the_dates_inside_it(self):
        """
        The dated events are a sample of the phase, not the whole of it. If
        this ever inverted, the two quantities would have been read off
        different draws.
        """
        _tree, _measurements, solved = self._solved()
        by_id = {d['id']: d for d in solved['intervals']['durations']}
        for phase_id, sample_id in (('phase:b0:b1', 'sample:p1'),
                                    ('phase:b1:b2', 'sample:p2')):
            assert by_id[phase_id]['years']['mean'] > by_id[sample_id]['years']['mean']

    def test_pairing_the_marginals_would_give_a_negative_duration(self):
        """
        The reason the joint draws exist at all.

        The model requires a phase's older boundary to be older than its
        younger one, and the joint posterior honours that in every draw. Two
        MARGINALS paired up do not: on this model 8% of such pairs put the end
        of the phase before its start. The failure is in the shape rather than
        the width -- the two spreads here agree to a few percent -- so nothing
        short of this would catch a sampler that lost the correlation.
        """
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        measurements = _dates(
            ('d1', 3200, 25, 'p'), ('d2', 3180, 25, 'p'), ('d3', 3210, 25, 'p'),
            ('d4', 3195, 25, 'p'), ('d5', 3230, 25, 'p'), ('d6', 3170, 25, 'p'))
        chain = ec.layout(tree, measurements)
        solved = ec.solve(measurements, tree, chain, estimate_durations=True)

        duration = next(d for d in solved['intervals']['durations']
                        if d['id'] == 'phase:b0:b1')
        assert duration['years']['hpd_95'][0] >= 0.0
        assert min(r['min'] for r in duration['years']['hpd_68_ranges']) >= 0.0

        rng = np.random.default_rng(8)
        older = rng.choice(solved['boundaries']['b0']['posterior']['posterior_samples'],
                           40000)
        younger = rng.choice(solved['boundaries']['b1']['posterior']['posterior_samples'],
                             40000)
        assert (older - younger).mean() < 0.0 or float((older < younger).mean()) > 0.03, (
            'pairing the marginals produced no impossible durations, so this '
            'model no longer demonstrates anything')

    def test_a_run_not_asked_for_durations_reports_none(self):
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        measurements = _dates(('d1', 3000, 30, 'p'), ('d2', 3050, 30, 'p'))
        chain = ec.layout(tree, measurements)
        assert 'intervals' not in ec.solve(measurements, tree, chain)

    def test_asking_for_durations_changes_nothing_else(self):
        """
        The option must not look like it moved a date. The joint draws are
        taken with their own generator for exactly this reason.
        """
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        measurements = _dates(('d1', 3000, 30, 'p'), ('d2', 3050, 30, 'p'))
        chain = ec.layout(tree, measurements)
        without = ec.solve(measurements, tree, chain)
        with_them = ec.solve(measurements, tree, chain, estimate_durations=True)
        for date_id in ('d1', 'd2'):
            assert (without['measurements'][date_id]['posterior_mean']
                    == with_them['measurements'][date_id]['posterior_mean'])
            assert (without['measurements'][date_id]['posterior_samples']
                    == with_them['measurements'][date_id]['posterior_samples'])


class TestTheDifference:
    def test_a_gap_is_positive_and_an_overlap_negative(self):
        draws = {
            'early_start': np.full(1000, 3400.0), 'early_end': np.full(1000, 3300.0),
            'late_start': np.full(1000, 3200.0), 'late_end': np.full(1000, 3100.0),
        }
        spec = {'kind': 'phase_difference', 'id': 'x',
                'from_id': 'a', 'from_name': 'A', 'to_id': 'b', 'to_name': 'B',
                'from_boundaries': ('early_start', 'early_end'),
                'to_boundaries': ('late_start', 'late_end')}
        _d, [apart] = intervals.evaluate([], [spec], draws)
        assert apart['years']['mean'] == pytest.approx(100.0)
        assert apart['overlap'] == 0.0
        assert apart['severity'] == 'none'

        draws['late_start'] = np.full(1000, 3350.0)
        _d, [crossing] = intervals.evaluate([], [spec], draws)
        assert crossing['years']['mean'] == pytest.approx(-50.0)
        assert crossing['overlap'] == 1.0
        assert crossing['severity'] == 'extensive'

    def test_two_dates_that_cannot_be_ordered_overlap_completely(self):
        rng = np.random.default_rng(9)
        same = rng.normal(3000, 40, 40000)
        spec = {'kind': 'date_difference', 'id': 'x',
                'from_id': 'a', 'from_name': 'A', 'to_id': 'b', 'to_name': 'B'}
        _d, [record] = intervals.evaluate([], [spec], {
            'a': same, 'b': rng.normal(3000, 40, 40000)})
        assert record['older_probability'] == pytest.approx(0.5, abs=0.02)
        assert record['overlap'] == pytest.approx(1.0, abs=0.04)
        assert record['severity'] == 'extensive'

    def test_two_dates_the_model_separates_do_not_overlap(self):
        rng = np.random.default_rng(10)
        spec = {'kind': 'date_difference', 'id': 'x',
                'from_id': 'a', 'from_name': 'A', 'to_id': 'b', 'to_name': 'B'}
        _d, [record] = intervals.evaluate([], [spec], {
            'a': rng.normal(3400, 30, 20000), 'b': rng.normal(3000, 30, 20000)})
        assert record['older_probability'] == 1.0
        assert record['overlap'] == 0.0
        assert record['severity'] == 'none'
        assert record['years']['mean'] == pytest.approx(400, abs=5)


class TestTheTwoSolversAgree:
    def test_a_phase_duration_is_the_same_either_way(self):
        """
        The cross-check every other quantity in this app gets. The exact
        solver's draws come from backward sampling and the sampler's from its
        own sweeps, so agreement here is two independent routes to one answer.
        """
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        measurements = _dates(
            ('d1', 3200, 25, 'p'), ('d2', 3180, 25, 'p'), ('d3', 3210, 25, 'p'),
            ('d4', 3195, 25, 'p'), ('d5', 3230, 25, 'p'), ('d6', 3170, 25, 'p'))
        chain = ec.layout(tree, measurements)
        exact = ec.solve(measurements, tree, chain, estimate_durations=True)
        sampled = gibbs.solve(measurements, tree, sweeps=8000,
                              estimate_durations=True, interval_draws=8000)

        def mean_of(solved, record_id):
            return next(d['years']['mean'] for d in solved['intervals']['durations']
                        if d['id'] == record_id)

        assert mean_of(exact, 'phase:b0:b1') == pytest.approx(
            mean_of(sampled, 'phase:b0:b1'), abs=8.0)
        assert mean_of(exact, 'sample:p') == pytest.approx(
            mean_of(sampled, 'sample:p'), abs=5.0)


class TestReporting:
    def test_a_summary_is_not_shaped_like_a_calendar_posterior(self):
        """
        Different key names on purpose: a duration is a length, and the three
        exports and the report all convert calendar-age fields to the reader's
        era by name. Sharing the names would put a BC/AD-converted duration one
        typo away.
        """
        summary = intervals.summarise(np.random.default_rng(11).normal(120, 30, 5000))
        assert 'posterior_mean' not in summary
        assert 'probability_distribution' not in summary
        assert set(summary) >= {'mean', 'median', 'std', 'hpd_68', 'hpd_95',
                                'hpd_68_ranges', 'hpd_95_ranges', 'step',
                                'distribution'}

    def test_the_reported_step_does_not_depend_on_the_extreme_draws(self):
        """
        The bin width comes from a percentile spread rather than min-to-max, so
        asking for more draws sharpens the answer instead of coarsening it.
        """
        rng = np.random.default_rng(12)
        small = intervals.summarise(rng.normal(300, 90, 20000))
        large = intervals.summarise(rng.normal(300, 90, 200000))
        assert small['step'] == large['step']

    def test_a_smooth_posterior_comes_back_as_one_range(self):
        """A single-humped duration reported in five pieces would be reporting
        the draws' own noise as structure."""
        values = np.random.default_rng(13).normal(400, 120, 20000)
        summary = intervals.summarise(values)
        assert len(summary['hpd_68_ranges']) == 1
        assert len(summary['hpd_95_ranges']) == 1


class TestThroughTheApi:
    """
    The option, the persistence and the three exports. Element ids are unique
    to this module -- see the `client` fixture's note on the shared database.
    """

    def _tree(self):
        return _sequence('iv-root', [
            _boundary('iv-b0', 'Start'), _phase('iv-p1', 'Lower'),
            _boundary('iv-b1', 'Mid'), _phase('iv-p2', 'Upper'),
            _boundary('iv-b2', 'End')], name='Site')

    def _run(self, client, tree, name, estimate, ids, phases=('iv-p1', 'iv-p2')):
        body = {
            'name': name,
            'measurements': [
                {'id': ids[0], 'sample_name': 'A', 'age_bp': 3300,
                 'error_1sigma': 30, 'element_id': phases[0]},
                {'id': ids[1], 'sample_name': 'B', 'age_bp': 3280,
                 'error_1sigma': 30, 'element_id': phases[0]},
                {'id': ids[2], 'sample_name': 'C', 'age_bp': 3000,
                 'error_1sigma': 30, 'element_id': phases[1]},
                {'id': ids[3], 'sample_name': 'D', 'age_bp': 3020,
                 'error_1sigma': 30, 'element_id': phases[1]},
            ],
            'model_tree': tree,
            'bayesian_config': {'mcmc_samples': 200,
                                'estimate_durations': estimate},
            'save': True,
        }
        created = client.post('/api/analysis/bayesian?wait=true', json=body)
        assert created.status_code == 200, created.text
        return created.json()['data']['id']

    def test_it_is_off_unless_asked_for(self, client):
        analysis_id = self._run(client, self._tree(), 'No durations', False,
                                ['iv-n1', 'iv-n2', 'iv-n3', 'iv-n4'])
        fetched = client.get(f'/api/analysis/{analysis_id}').json()['data']
        assert fetched['intervals'] is None, (
            'a run that was not asked for durations must report none, not an '
            'empty table -- the two mean different things to the page')

    def test_asking_for_it_stores_durations_against_the_saved_elements(self, client):
        tree = _sequence('iv2-root', [
            _boundary('iv2-b0', 'Start'), _phase('iv2-p1', 'Lower'),
            _boundary('iv2-b1', 'Mid'), _phase('iv2-p2', 'Upper'),
            _boundary('iv2-b2', 'End')], name='Site')
        body = {
            'name': 'With durations',
            'measurements': [
                {'id': 'iv2-a', 'sample_name': 'A', 'age_bp': 3300,
                 'error_1sigma': 30, 'element_id': 'iv2-p1'},
                {'id': 'iv2-b', 'sample_name': 'B', 'age_bp': 3280,
                 'error_1sigma': 30, 'element_id': 'iv2-p1'},
                {'id': 'iv2-c', 'sample_name': 'C', 'age_bp': 3000,
                 'error_1sigma': 30, 'element_id': 'iv2-p2'},
                {'id': 'iv2-d', 'sample_name': 'D', 'age_bp': 3020,
                 'error_1sigma': 30, 'element_id': 'iv2-p2'},
            ],
            'model_tree': tree,
            'bayesian_config': {'mcmc_samples': 200, 'estimate_durations': True},
            'save': True,
        }
        created = client.post('/api/analysis/bayesian?wait=true', json=body)
        assert created.status_code == 200, created.text
        analysis_id = created.json()['data']['id']

        fetched = client.get(f'/api/analysis/{analysis_id}').json()['data']
        block = fetched['intervals']
        assert block is not None
        kinds = {d['kind'] for d in block['durations']}
        assert kinds == {'sequence_duration', 'phase_duration', 'sample_duration'}

        # Every element a record names must exist in the tree that came back,
        # or the results page has nothing to attach the figure to.
        present = set()

        def walk(node):
            present.add(node['id'])
            for child in node.get('children') or []:
                walk(child)

        walk(fetched['model_tree'])
        for record in block['durations']:
            if record['element_id']:
                assert record['element_id'] in present
        for record in block['differences']:
            assert record['from_id'] in present
            assert record['to_id'] in present

    def test_the_csv_carries_the_same_table(self, client):
        analysis_id = self._run(client, _sequence('iv3-root', [
            _boundary('iv3-b0', 'Start'), _phase('iv3-p1', 'Lower'),
            _boundary('iv3-b1', 'Mid'), _phase('iv3-p2', 'Upper'),
            _boundary('iv3-b2', 'End')], name='Site'),
            'CSV durations', True, ['iv3-a', 'iv3-b', 'iv3-c', 'iv3-d'],
            phases=('iv3-p1', 'iv3-p2'))

        body = client.get(f'/api/results/{analysis_id}/export/csv').text
        rows = list(csv.reader(io.StringIO(body)))
        start = rows.index(['Row Type', 'Name', 'Compared With', 'Mean (yr)',
                            'Median (yr)', 'Std (yr)', '68.3% Range (yr)',
                            '95.4% Range (yr)', 'Overlap Probability', 'Overlap',
                            'Dated Events'])
        labels = [r[0] for r in rows[start + 1:] if r]
        assert 'Sequence Duration' in labels
        assert 'Phase Duration' in labels
        assert 'Sample Duration' in labels
        # One Sequence orders both phases, so there is no difference to report.
        assert 'Phase Difference' not in labels

        exported = client.get(f'/api/results/{analysis_id}/export/json').json()['data']
        stored = client.get(f'/api/analysis/{analysis_id}').json()['data']['intervals']
        assert exported['intervals'] == stored, (
            'the CSV, the JSON and the page must describe one run')

        assert client.get(f'/api/results/{analysis_id}/pdf').status_code == 200

    def test_parallel_branches_are_compared_through_to_the_exports(self, client):
        tree = _sequence('iv5-root', [
            _boundary('iv5-start', 'Start'),
            {'id': 'iv5-site', 'element_type': 'phase', 'name': 'Site',
             'tpq_age_bp': None, 'taq_age_bp': None, 'children': [
                 _sequence('iv5-n', [_boundary('iv5-n0', 'North start'),
                                     _phase('iv5-p1', 'North'),
                                     _boundary('iv5-n1', 'North end')]),
                 _sequence('iv5-s', [_boundary('iv5-s0', 'South start'),
                                     _phase('iv5-p2', 'South'),
                                     _boundary('iv5-s1', 'South end')]),
             ]},
            _boundary('iv5-end', 'End')], name='Site')
        analysis_id = self._run(client, tree, 'Parallel branches', True,
                                ['iv5-a', 'iv5-b', 'iv5-c', 'iv5-d'],
                                phases=('iv5-p1', 'iv5-p2'))

        stored = client.get(f'/api/analysis/{analysis_id}').json()['data']['intervals']
        [difference] = stored['differences']
        assert (difference['from_name'], difference['to_name']) == ('North', 'South')
        assert 'shared_boundary' not in difference

        # The figure's rows name the saved elements, as every record does.
        activity = stored['phase_activity']
        assert [row['name'] for row in activity['rows']] == ['Site', 'North', 'South']
        saved = set()

        def walk(node):
            saved.add(node['id'])
            for child in node.get('children') or []:
                walk(child)

        walk(client.get(f'/api/analysis/{analysis_id}').json()['data']['model_tree'])
        assert all(row['id'] in saved for row in activity['rows'])

        from app.api.results import INTERVAL_HEADER

        body = client.get(f'/api/results/{analysis_id}/export/csv').text
        rows = list(csv.reader(io.StringIO(body)))
        header = rows.index(INTERVAL_HEADER)
        row = next(r for r in rows[header + 1:] if r and r[0] == 'Phase Difference')
        cells = dict(zip(INTERVAL_HEADER, row))
        assert cells['Mean (yr)'] != ''
        assert cells['Overlap'] in intervals.SEVERITY_WORDS

        assert client.get(f'/api/results/{analysis_id}/pdf').status_code == 200

    def test_a_stored_shared_transition_is_not_exported_as_a_measurement(self):
        """
        Analyses stored before ordered pairs were dropped carry pairs either
        side of one Boundary, whose gap and overlap probability are both zero
        by construction. Printed bare, those zeroes read as a measured
        separation -- and `none` as "separated at 2 sigma". The export must
        say what the row is instead.
        """
        from app.api.results import INTERVAL_HEADER, _interval_rows

        summary = intervals.summarise(np.zeros(100))
        [row] = _interval_rows({'durations': [], 'differences': [{
            'kind': 'phase_difference', 'id': 'x', 'from_id': 'a',
            'from_name': 'Lower', 'to_id': 'b', 'to_name': 'Upper',
            'years': summary, 'overlap': 0.0, 'severity': 'none',
            'shared_boundary': True}]})
        cells = dict(zip(INTERVAL_HEADER, row))
        assert cells['Overlap'] == intervals.SHARED_BOUNDARY_TEXT
        assert cells['Overlap Probability'] == ''
        for column in ('Mean (yr)', 'Median (yr)', 'Std (yr)',
                       '68.3% Range (yr)', '95.4% Range (yr)'):
            assert cells[column] == '', (
                f'{column} must be blank, not a zero that reads as a measurement')

    def test_editing_the_model_drops_them(self, client):
        """
        Every record names a phase of the structure being replaced, so keeping
        them would report how long a phase lasted against a model without it --
        the same reasoning that clears the recorded solver.
        """
        tree = _sequence('iv4-root', [
            _boundary('iv4-b0', 'Start'), _phase('iv4-p1', 'Lower'),
            _boundary('iv4-b1', 'End')], name='Site')
        body = {
            'name': 'Edited', 'model_tree': tree,
            'measurements': [
                {'id': 'iv4-a', 'sample_name': 'A', 'age_bp': 3300,
                 'error_1sigma': 30, 'element_id': 'iv4-p1'},
                {'id': 'iv4-b', 'sample_name': 'B', 'age_bp': 3280,
                 'error_1sigma': 30, 'element_id': 'iv4-p1'},
            ],
            'bayesian_config': {'mcmc_samples': 200, 'estimate_durations': True},
            'save': True,
        }
        analysis_id = client.post(
            '/api/analysis/bayesian?wait=true', json=body).json()['data']['id']
        assert client.get(f'/api/analysis/{analysis_id}').json()['data']['intervals']

        body['bayesian_config'] = {'mcmc_samples': 200, 'estimate_durations': False}
        replaced = client.put(f'/api/analysis/{analysis_id}/inputs', json=body)
        assert replaced.status_code == 200, replaced.text
        assert client.get(f'/api/analysis/{analysis_id}').json()['data']['intervals'] is None


class TestAGroupCalibration:
    def test_it_reports_differences_and_no_durations(self, client):
        created = client.post('/api/analysis/calibrate-group', json={
            'name': 'Group differences', 'estimate_durations': True,
            'measurements': [
                {'id': 'gd-1', 'sample_name': 'G1', 'age_bp': 3000, 'error_1sigma': 30},
                {'id': 'gd-2', 'sample_name': 'G2', 'age_bp': 2990, 'error_1sigma': 30},
                {'id': 'gd-3', 'sample_name': 'G3', 'age_bp': 2500, 'error_1sigma': 30},
            ]})
        assert created.status_code == 200, created.text
        analysis_id = created.json()['data']['id']

        block = client.get(f'/api/analysis/{analysis_id}').json()['data']['intervals']
        assert block['durations'] == [], (
            'dates with no relationship asserted between them belong to no '
            'phase, so there is no duration for them to have')
        assert len(block['differences']) == 3
        assert block['independent'] is True

        by_pair = {(d['from_name'], d['to_name']): d for d in block['differences']}
        # G1 and G2 are ten radiocarbon years apart: the model cannot order
        # them. G1 and G3 are five hundred: it can.
        assert by_pair[('G1', 'G2')]['overlap'] > 0.5
        assert by_pair[('G1', 'G3')]['severity'] == 'none'
        assert by_pair[('G1', 'G3')]['years']['mean'] > 300

    def test_it_stays_off_by_default(self, client):
        created = client.post('/api/analysis/calibrate-group', json={
            'name': 'Group plain',
            'measurements': [
                {'id': 'gp-1', 'sample_name': 'G1', 'age_bp': 3000, 'error_1sigma': 30},
                {'id': 'gp-2', 'sample_name': 'G2', 'age_bp': 2500, 'error_1sigma': 30},
            ]})
        analysis_id = created.json()['data']['id']
        assert client.get(
            f'/api/analysis/{analysis_id}').json()['data']['intervals'] is None


# --------------------------------------------------------------------------
# Wave of advance: the third reason a run takes joint draws
# --------------------------------------------------------------------------

def _front_model():
    """
    Six sites on a west-to-east front, roughly 100 km and 100 years apart.

    A Sequence, because a front IS ordered in time and the model should say so.
    Each site is a Phase of two dates carrying the same coordinates, which is
    the ordinary shape dispersal.derive_sites reads.
    """
    children, measurements = [], []
    for index in range(6):
        children.append(_boundary(f'b{index}'))
        children.append(_phase(f'p{index}', f'Site {index}'))
        age = 4400 - 100 * index
        for half in (0, 1):
            measurements.append({
                'id': f'd{index}{half}', 'sample_name': f'S{index}-{half}',
                'age_bp': age + 10 * half, 'error_1sigma': 25,
                'curve_id': 'intcal20', 'element_id': f'p{index}',
                # ~1.4 degrees of longitude is about 100 km at this latitude.
                'latitude': 50.0, 'longitude': 1.4 * index,
            })
    children.append(_boundary('b6'))
    return _sequence('s', children), measurements


class TestDispersalThroughTheSolvers:
    """
    §6.3: a dispersal fit is computed DURING the run or not at all, because it
    is a function of many modelled ages at once and joint draws exist only
    inside a solver. So the wiring, not the arithmetic, is what these check --
    the arithmetic is tests/test_dispersal.py's.
    """

    ORIGIN = (50.0, 0.0)

    def test_it_is_absent_unless_asked_for(self):
        """
        Absent meaning NOT ASKED FOR, which is the convention the results page
        already reads for durations and age-depth: it offers a re-run rather
        than showing an empty table.
        """
        tree, measurements = _front_model()
        solved = ec.solve(measurements, tree, ec.layout(tree, measurements),
                          estimate_durations=True)
        assert solved['intervals']['dispersal'] is None

    def test_the_exact_solver_reports_it_when_asked(self):
        tree, measurements = _front_model()
        solved = ec.solve(measurements, tree, ec.layout(tree, measurements),
                          dispersal={'origin': self.ORIGIN})

        fit = solved['intervals']['dispersal']['wave']
        assert fit['refused'] is None
        assert fit['n_sites'] == 6
        assert fit['origin'] == {'lat': 50.0, 'lon': 0.0}
        # West to east, oldest first: a front, and a positive speed.
        assert fit['speed']['median'] > 0
        assert len(fit['residuals']) == 6

    def test_the_sampler_reports_it_too(self):
        tree, measurements = _front_model()
        solved = gibbs.solve(measurements, tree, sweeps=4000,
                             dispersal={'origin': self.ORIGIN}, interval_draws=4000)

        fit = solved['intervals']['dispersal']['wave']
        assert fit['refused'] is None
        assert fit['n_sites'] == 6
        assert fit['speed']['median'] > 0

    def test_asking_for_it_does_not_ask_for_durations(self):
        """
        The third reason to take joint draws is independent of the other two.
        A phase duration is a modelling claim of its own -- that the dates are
        a fair sample of a bounded episode -- and a dispersal fit must not
        smuggle it in.
        """
        tree, measurements = _front_model()
        solved = ec.solve(measurements, tree, ec.layout(tree, measurements),
                          dispersal={'origin': self.ORIGIN})

        assert solved['intervals']['dispersal'] is not None
        assert solved['intervals']['durations'] == []

    def test_a_site_is_its_opening_boundary_not_its_oldest_date(self):
        """
        §6.2, and the whole reason this is not a regression on a table of
        dates. The Boundary extrapolates BEYOND the oldest dated member, so a
        site's arrival is older than anything actually dug there -- and the
        residuals must be measured against that, not against the date.
        """
        tree, measurements = _front_model()
        solved = ec.solve(measurements, tree, ec.layout(tree, measurements),
                          dispersal={'origin': self.ORIGIN})

        sites = solved['intervals']['dispersal']['wave']['sites']
        assert [s['parameter_id'] for s in sites] == [f'b{i}' for i in range(6)]
        assert all(s['basis'] == 'boundary' for s in sites)

    def test_two_sites_in_one_phase_refuse_the_fit(self):
        """§11.6 reaching the report rather than stopping the run: every other
        number the analysis was asked for still comes back."""
        tree, measurements = _front_model()
        # Move one date of Site 3 forty kilometres away.
        measurements[7]['longitude'] = 1.4 * 3 + 0.6

        solved = ec.solve(measurements, tree, ec.layout(tree, measurements),
                          dispersal={'origin': self.ORIGIN})
        fit = solved['intervals']['dispersal']['wave']
        assert fit['refused'] is not None
        assert fit['speed'] is None
        # ...and the run still produced everything else.
        assert len(solved['measurements']) == 12


class TestTheDispersalConfig:

    def test_a_dispersal_without_an_origin_is_allowed(self):
        """
        It asks for the trend surface, which has no origin to be missing. The
        wave of advance is what an origin buys.
        """
        from app.schemas.sample import BayesianConfigInput
        config = BayesianConfigInput(estimate_dispersal=True)
        assert config.dispersal_origin is None

    def test_an_origin_alone_asks_for_nothing(self):
        from app.schemas.sample import BayesianConfigInput
        config = BayesianConfigInput(dispersal_origin={'lat': 50.0, 'lon': 0.0})
        assert config.estimate_dispersal is False

    def test_an_origin_left_behind_by_a_switched_off_fit_is_not_a_request(self):
        """A rerun reads the STORED config. An origin still sitting in it after
        the analyst turned the fit off must not quietly re-enable it."""
        from app.api.analysis import _dispersal_spec
        assert _dispersal_spec({
            'estimate_dispersal': False,
            'dispersal_origin': {'lat': 50.0, 'lon': 0.0}}) is None

    def test_the_flag_without_a_pin_asks_for_the_trend(self):
        from app.api.analysis import _dispersal_spec
        assert _dispersal_spec({'estimate_dispersal': True}) == {'origin': None}

    def test_half_an_origin_is_not_an_origin(self):
        from app.schemas.sample import BayesianConfigInput
        with pytest.raises(ValueError):
            BayesianConfigInput(estimate_dispersal=True,
                                dispersal_origin={'lat': 50.0})


class TestDispersalThroughTheApi:
    """
    The wiring that the solver tests above cannot reach: measurement
    coordinates surviving the trip from the database into the solver, and the
    flag surviving the trip from bayesian_config into the fit.
    """

    def _body(self, name, prefix, config):
        children = []
        measurements = []
        for index in range(6):
            children.append(_boundary(f'{prefix}-b{index}', f'Start {index}'))
            children.append(_phase(f'{prefix}-p{index}', f'Site {index}'))
            for half in (0, 1):
                measurements.append({
                    'id': f'{prefix}-d{index}{half}',
                    'sample_name': f'S{index}-{half}',
                    'age_bp': 4400 - 100 * index + 10 * half,
                    'error_1sigma': 25,
                    'element_id': f'{prefix}-p{index}',
                    'latitude': 50.0, 'longitude': 1.4 * index,
                })
        children.append(_boundary(f'{prefix}-b6', 'End'))
        return {
            'name': name,
            'measurements': measurements,
            'model_tree': _sequence(f'{prefix}-root', children, name='Region'),
            'bayesian_config': {'mcmc_samples': 200, **config},
            'save': True,
        }

    def test_a_front_comes_back_from_a_saved_analysis(self, client):
        created = client.post('/api/analysis/bayesian?wait=true', json=self._body(
            'Front', 'dsp', {'estimate_dispersal': True,
                             'dispersal_origin': {'lat': 50.0, 'lon': 0.0}}))
        assert created.status_code == 200, created.text
        analysis_id = created.json()['data']['id']

        section = client.get(
            f'/api/analysis/{analysis_id}').json()['data']['intervals']['dispersal']
        fit = section['wave']
        assert fit['refused'] is None, fit['refused']
        # Six sites means the coordinates survived the trip through the
        # database and into the solver's measurement dicts. Without them the
        # fit would refuse for having nothing to place.
        assert fit['n_sites'] == 6
        assert fit['speed']['median'] > 0
        assert fit['t0']['median'] > 0

    def test_it_stays_off_by_default(self, client):
        created = client.post('/api/analysis/bayesian?wait=true', json=self._body(
            'No front', 'dspoff', {'estimate_durations': True}))
        block = client.get(
            f'/api/analysis/{created.json()["data"]["id"]}').json()['data']['intervals']
        assert block['dispersal'] is None

    def test_asking_without_an_origin_gives_the_trend_alone(self, client):
        """
        Not a refusal. A trend surface assumes no origin -- that is what makes
        it the right first look at a dataset -- so a dispersal request with no
        pin is a request for the trend, not a half-finished one.
        """
        created = client.post('/api/analysis/bayesian?wait=true', json=self._body(
            'Originless', 'dspno', {'estimate_dispersal': True}))
        assert created.status_code == 200, created.text
        section = client.get(
            f'/api/analysis/{created.json()["data"]["id"]}'
        ).json()['data']['intervals']['dispersal']

        assert section['wave'] is None, (
            'no origin was placed, so there is nothing to measure a front '
            'speed from and the app must not invent one')
        # The sites are collinear (a transect), so the trend is refused for
        # THAT reason -- which is still the trend having been attempted.
        assert section['trend'] is not None


class TestTheTrendSurfaceThroughTheSolvers:
    """
    §6.5 through the wiring. The trend is computed whenever dispersal is asked
    for at all, origin or not, because the two models answer different
    questions off the same draws and comparing them is the point.
    """

    def _region(self, prefix='tr'):
        """Six sites in two rows, so a plane through them is determined."""
        children, measurements = [], []
        places = [(50.0, 0.0), (50.0, 1.4), (50.0, 2.8),
                  (50.8, 0.0), (50.8, 1.4), (50.8, 2.8)]
        for index, (lat, lon) in enumerate(places):
            children.append(_boundary(f'{prefix}-b{index}', f'Start {index}'))
            children.append(_phase(f'{prefix}-p{index}', f'Site {index}'))
            for half in (0, 1):
                measurements.append({
                    'id': f'{prefix}-d{index}{half}', 'sample_name': f'S{index}{half}',
                    'age_bp': 4400 - 100 * index + 10 * half, 'error_1sigma': 25,
                    'curve_id': 'intcal20', 'element_id': f'{prefix}-p{index}',
                    'latitude': lat, 'longitude': lon,
                })
        children.append(_boundary(f'{prefix}-b6', 'End'))
        return _sequence(f'{prefix}-root', children, name='Region'), measurements

    def test_a_trend_comes_back_with_no_origin_at_all(self):
        tree, measurements = self._region()
        solved = ec.solve(measurements, tree, ec.layout(tree, measurements),
                          dispersal={'origin': None})

        section = solved['intervals']['dispersal']
        assert section['wave'] is None
        trend = section['trend']
        assert trend['refused'] is None, trend['refused']
        assert trend['n_sites'] == 6
        assert trend['speed']['median'] > 0
        assert 0.0 <= trend['azimuth']['mean_deg'] < 360.0
        assert trend['isochrons']

    def test_an_origin_buys_both_models(self):
        tree, measurements = self._region()
        solved = ec.solve(measurements, tree, ec.layout(tree, measurements),
                          dispersal={'origin': (50.4, 0.0)})

        section = solved['intervals']['dispersal']
        assert section['wave']['refused'] is None
        assert section['trend']['refused'] is None

    def test_a_transect_gets_a_wave_but_no_surface(self):
        """
        The honest answer for the commonest layout there is. A line of sites
        fixes the gradient along itself and leaves it free across, so a wave
        from a placed origin is the model a transect can support and a plane
        is not.
        """
        tree, measurements = _front_model()      # six sites on one parallel
        solved = ec.solve(measurements, tree, ec.layout(tree, measurements),
                          dispersal={'origin': (50.0, 0.0)})

        section = solved['intervals']['dispersal']
        assert section['wave']['refused'] is None
        assert section['trend']['refused'] is not None
        assert 'line' in section['trend']['refused']

    def test_the_sampler_reports_the_trend_too(self):
        tree, measurements = self._region('trg')
        solved = gibbs.solve(measurements, tree, sweeps=4000,
                             dispersal={'origin': None}, interval_draws=4000)
        assert solved['intervals']['dispersal']['trend']['refused'] is None
