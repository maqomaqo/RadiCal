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

    def test_a_hiatus_is_not_reported_as_a_phase_duration(self):
        """
        Two Boundaries with only a Hiatus between them bracket a GAP, and the
        gap between the phases either side is what the difference table
        reports. Reporting it here too would print one quantity twice.
        """
        tree = _sequence('s', [
            _boundary('b0'), _phase('p1'), _boundary('b1'), _hiatus('h'),
            _boundary('b2'), _phase('p2'), _boundary('b3')])
        measurements = _dates(('d1', 3000, 30, 'p1'), ('d2', 2600, 30, 'p2'))
        specs = intervals.plan_durations(tree, measurements)
        pairs = [s['boundaries'] for s in _by_kind(specs, 'phase_duration')]
        assert ('b1', 'b2') not in pairs
        assert pairs == [('b0', 'b1'), ('b2', 'b3')]

    def test_adjacent_phases_are_flagged_as_sharing_their_transition(self):
        tree = _sequence('s', [_boundary('b0'), _phase('p1'), _boundary('b1'),
                               _phase('p2'), _boundary('b2')])
        specs, note = intervals.plan_phase_differences(
            tree, _dates(('d1', 3000, 30, 'p1'), ('d2', 2800, 30, 'p2')))
        assert note is None
        assert len(specs) == 1
        assert specs[0]['shared_boundary'] is True

    def test_phases_bracketed_identically_are_not_compared(self):
        """
        Two phases inside one pair of Boundaries have the same modelled
        interval by construction, so "they coincide" would be a fact about the
        model rather than anything inferred from the dates.
        """
        tree = _sequence('s', [
            _boundary('b0'),
            {'id': 'both', 'element_type': 'phase', 'name': 'Areas',
             'tpq_age_bp': None, 'taq_age_bp': None,
             'children': [_phase('p1'), _phase('p2')]},
            _boundary('b1')])
        specs, _note = intervals.plan_phase_differences(
            tree, _dates(('d1', 3000, 30, 'p1'), ('d2', 2900, 30, 'p2')))
        assert specs == []


class TestSeverity:
    def test_the_bands_are_the_reporting_confidences(self):
        assert intervals.severity(0.0) == 'none'
        assert intervals.severity(0.045) == 'none'
        assert intervals.severity(0.05) == 'slight'
        assert intervals.severity(0.5) == 'moderate'
        assert intervals.severity(0.8) == 'substantial'
        assert intervals.severity(0.96) == 'significant'
        assert intervals.severity(1.0) == 'significant'

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
        _pair, _density, draw = ec._messages(chain, allowed, lambda i: matrix)

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
        _pair, _density, draw = ec._messages(
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
        _pair, _density, draw = ec._messages(chain, allowed, lambda i: matrix)
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
        _pair, _density, draw = ec._messages_with_span_prior(
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
        _lik, cumulative, _outliers = ec.date_tables(grid, measurements, {})

        rng = np.random.default_rng(7)
        # Boundary index pairs picked directly, so this tests the truncation
        # rather than whatever the solver would have drawn.
        count = 2000
        boundaries = np.empty((count, 2), dtype=np.int64)
        boundaries[:, 0] = rng.integers(len(grid) // 2, len(grid), count)
        boundaries[:, 1] = rng.integers(0, len(grid) // 2, count)

        drawn = ec._draw_dates(rng, chain, cumulative, boundaries)
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
        assert crossing['severity'] == 'significant'

    def test_two_dates_that_cannot_be_ordered_overlap_completely(self):
        rng = np.random.default_rng(9)
        same = rng.normal(3000, 40, 40000)
        spec = {'kind': 'date_difference', 'id': 'x',
                'from_id': 'a', 'from_name': 'A', 'to_id': 'b', 'to_name': 'B'}
        _d, [record] = intervals.evaluate([], [spec], {
            'a': same, 'b': rng.normal(3000, 40, 40000)})
        assert record['older_probability'] == pytest.approx(0.5, abs=0.02)
        assert record['overlap'] == pytest.approx(1.0, abs=0.04)
        assert record['severity'] == 'significant'

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
                            'Median (yr)', 'Std (yr)', '68% Range (yr)',
                            '95% Range (yr)', 'Overlap Probability', 'Overlap',
                            'Dated Events'])
        labels = [r[0] for r in rows[start + 1:] if r]
        assert 'Sequence Duration' in labels
        assert 'Phase Duration' in labels
        assert 'Sample Duration' in labels
        assert 'Phase Difference' in labels

        exported = client.get(f'/api/results/{analysis_id}/export/json').json()['data']
        stored = client.get(f'/api/analysis/{analysis_id}').json()['data']['intervals']
        assert exported['intervals'] == stored, (
            'the CSV, the JSON and the page must describe one run')

        assert client.get(f'/api/results/{analysis_id}/pdf').status_code == 200

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
