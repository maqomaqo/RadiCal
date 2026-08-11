"""
The OxCal-style MH/Gibbs sampler (services/gibbs.py).

The oracle is services/exact_chain.py. On a chain-shaped model it integrates the
same posterior exactly, so the sampler can be held to a real answer rather than
to its own convergence diagnostics -- which is the whole reason to build it in
that order. A branching model is checked the one way it can be without an exact
solver for it: two INDEPENDENT branches, each of which is separately a chain, so
exact_chain solves them one at a time while the sampler has to solve the
combined model that layout() refuses.

Sweep counts are kept modest so the suite stays quick, with tolerances set from
the measured agreement rather than from what happens to pass -- see the
docstrings for the numbers each was chosen against. No pymc anywhere: the
sampler is pure numpy.
"""
import itertools

import numpy as np
import pytest

from app.services import exact_chain as ec
from app.services import gibbs
from app.services.model_tree import with_implicit_boundaries

import oxcal_fixture as ox

_IDS = itertools.count()


def _element(kind, name, children=None):
    return {'id': f'g{next(_IDS)}', 'element_type': kind, 'name': name,
            'tpq_age_bp': None, 'taq_age_bp': None, 'children': children or []}


def _date(element_id, age_bp, name=None):
    tag = f'gd{next(_IDS)}'
    return {'id': tag, 'sample_name': name or tag, 'age_bp': age_bp,
            'error_1sigma': 30, 'curve_id': 'shcal20', 'element_id': element_id,
            'material_type': 'charcoal', 'sample_type': 'terrestrial'}


def _exact_grid(exact):
    return np.arange(exact['grid']['lower'], exact['grid']['upper'] + 1e-9,
                     exact['grid']['step'])


def _fixed(measurements, tree, sweeps=20000, **kwargs):
    """
    Run exactly `sweeps` sweeps, with the convergence-driven extension disabled.

    solve() extends until it converges, which is what production wants and what
    TestRunLength covers -- but it makes every other test's runtime depend on how
    hard its model happens to be, and the tolerances here are calibrated against
    a known length.
    """
    return gibbs.solve(measurements, tree, sweeps=sweeps, max_sweeps=sweeps,
                       **kwargs)


class TestAgreesWithTheExactSolver:
    """
    The chain models, where an exact answer exists. Both solvers are given the
    same grid so a resolution difference cannot be mistaken for a difference in
    inference.

    These comparisons are also what pins the two solvers' DISCRETISATION together,
    not merely their inference. Both integrate a date over its group's interval
    with the same trapezoidal rule -- exact_chain._interval_integral does it in
    closed form, Model.endpoint_weight does it by halving the endpoint cells -- so
    they agree by construction. When only one of the two had the rule they
    disagreed by 5-6 years on these same fixtures, having previously agreed to
    under one, which is the size of error this bound is set to catch.
    """

    @pytest.mark.parametrize('run', ['shared_boundary', 'unordered'])
    def test_posterior_means_match(self, run):
        """
        Measured at 20,000 sweeps across five seeds: 0.19-1.23 years from the
        exact answer on `unordered`, 0.21-0.41 on `shared_boundary`. 3 years is
        comfortably above that and still far below the ~40-year separation
        between this fixture's two calibration lobes, which is the error that
        would actually matter.
        """
        measurements = ox.measurements()
        tree = ox.model_tree(run)
        chain = ec.layout(tree, measurements)
        exact = ec.solve(measurements, tree, chain, num_samples=100)

        sampled = _fixed(measurements, tree, sweeps=20000, num_samples=100,
                         grid=_exact_grid(exact))
        errors = np.array([
            sampled['measurements'][m['id']]['posterior_mean']
            - exact['measurements'][m['id']]['posterior_mean']
            for m in measurements
        ])
        assert np.sqrt((errors ** 2).mean()) < 3.0, errors.round(2)

    def test_it_gets_the_calibration_lobes_right(self):
        """
        The point of the exercise. These dates calibrate to two ranges about 40
        years apart, and SMC mis-weights them -- it put two ordered groups 57
        years apart where the exact answer is 19 and OxCal says 13. Landing
        within a few years of the exact answer means the lobe weights are right,
        since getting them wrong moves the mean by tens of years.
        """
        measurements = ox.measurements()
        tree, inserted = with_implicit_boundaries(ox.model_tree('ordered'))
        assert inserted
        chain = ec.layout(tree, measurements)
        exact = ec.solve(measurements, tree, chain, num_samples=100)
        sampled = _fixed(measurements, tree, sweeps=20000, num_samples=100,
                         grid=_exact_grid(exact))

        def mean_ad(results, ids):
            return np.mean([1950 - results['measurements'][i]['posterior_mean']
                            for i in ids])

        exact_gap = mean_ad(exact, ox.ROCKSHELTER) - mean_ad(exact, ox.QUARRY)
        sampled_gap = mean_ad(sampled, ox.ROCKSHELTER) - mean_ad(sampled, ox.QUARRY)
        assert abs(sampled_gap - exact_gap) < 5.0, (sampled_gap, exact_gap)

    def test_interior_boundaries_match(self):
        """
        Interior boundaries only. An OUTERMOST boundary's posterior is genuinely
        heavy-tailed (sd of 240+ years on the branching case below), so its mean
        depends on how wide the calendar window is -- exact_chain reports
        edge_mass for exactly that reason. Pinning it here would be pinning the
        window, not the sampler.
        """
        measurements = ox.measurements()
        tree = ox.model_tree('shared_boundary')
        chain = ec.layout(tree, measurements)
        exact = ec.solve(measurements, tree, chain, num_samples=100)
        sampled = _fixed(measurements, tree, sweeps=20000, num_samples=100,
                         grid=_exact_grid(exact))

        interior = chain.boundary_ids[1:-1]
        assert interior, 'this fixture should have a shared interior boundary'
        for bid in interior:
            a = exact['boundaries'][bid]['posterior']['posterior_mean']
            b = sampled['boundaries'][bid]['posterior']['posterior_mean']
            assert abs(a - b) < 5.0, (bid, a, b)


class TestTheEndpointWeighting:
    """
    Model.endpoint_weight, which is the sampler's half of the interval-quadrature
    correction (see exact_chain._interval_integral for the other half and for why
    either is needed at all).

    Worth its own class because the symptom is remote from the cause. Nothing in
    the sampler computes an interval integral, so the bug does not look like a
    quadrature bug from here: it looks like the phases coming out several times
    too narrow. What goes wrong is a volume mismatch. A date confined between
    boundaries k cells apart can occupy k+1 grid CELLS while the (span)^(-K)
    factor charges k*step of span, so the near-coincident configurations the span
    prior already favours get an extra ((k+1)/k)^K -- 2^K at the smallest span --
    and the chain parks there.
    """

    def _phase(self, count=5, step=1.0):
        phase = _element('phase', 'Only')
        tree = _element('sequence', 'Site', [
            _element('boundary', 'Start'), phase, _element('boundary', 'End')])
        dates = [_date(phase['id'], 3000 + 15 * i) for i in range(count)]
        return tree, dates

    def _span(self, result, chain=None, ids=None):
        ids = ids or (chain.boundary_ids[0], chain.boundary_ids[-1])
        older = result['boundaries'][ids[0]]['posterior']['posterior_mean']
        younger = result['boundaries'][ids[1]]['posterior']['posterior_mean']
        return abs(older - younger)

    def test_the_span_matches_the_exact_solver_not_just_the_means(self):
        """
        The span is the sharp test and the means are the blunt one. With the
        weighting missing the dates' means were 5-6 years from the exact answer,
        which reads as ordinary sampling error; the span was out by a factor of
        SEVEN (5.3 years against 34.9). Anything that biases the sampler toward
        coincident configurations shows up here first.
        """
        tree, dates = self._phase()
        chain = ec.layout(tree, dates)
        exact = ec.solve(dates, tree, chain, num_samples=200)
        sampled = _fixed(dates, tree, sweeps=20000, num_samples=200,
                         grid=_exact_grid(exact))
        assert self._span(sampled, chain) == pytest.approx(
            self._span(exact, chain), rel=0.15)

    def test_refining_the_grid_does_not_move_the_answer(self):
        """
        The diagnostic that found this in the exact solver, applied to the
        sampler. A discretisation error that scales with the step size cannot
        survive halving the step: uncorrected, the exact solver's span went
        5.8 -> 8.1 -> 12.2 as the step went 1.0 -> 0.5 -> 0.25, with no sign of
        settling. A correct answer is a property of the model, not of the grid.

        Two steps rather than three, and a loose bound, because each run is a
        full 20,000-sweep solve; the failure this guards against is a drift of
        tens of percent, not of one.
        """
        tree, dates = self._phase()
        chain = ec.layout(tree, dates)
        exact = ec.solve(dates, tree, chain, num_samples=200)
        coarse = _exact_grid(exact)
        fine = np.arange(coarse[0], coarse[-1] + 1e-9,
                         (coarse[1] - coarse[0]) / 2.0)
        spans = [self._span(_fixed(dates, tree, sweeps=20000, num_samples=200,
                                   grid=grid), chain)
                 for grid in (coarse, fine)]
        assert spans[1] == pytest.approx(spans[0], rel=0.2), spans

    def test_a_date_on_a_boundary_counts_a_half(self):
        """endpoint_weight itself, on the three cases it distinguishes."""
        tree, dates = self._phase(count=2)
        model = gibbs.Model(dates, tree)
        state = model.initial_state()
        older, younger, group = model.group_dates[0]
        date_id = group[0]

        state.index[younger] = 100
        state.index[older] = 110
        state.index[date_id] = 105
        assert model.endpoint_weight(date_id, state) == 1.0
        state.index[date_id] = 100
        assert model.endpoint_weight(date_id, state) == 0.5
        state.index[date_id] = 110
        assert model.endpoint_weight(date_id, state) == 0.5

    def test_the_split_draw_is_the_weighted_distribution(self):
        """
        draw_date applies the half weights by splitting the draw into interior
        and two endpoints rather than by rebuilding a cumulative table per sweep,
        so that it stays one searchsorted. That is an optimisation, and this
        checks it against the distribution it is meant to be: the date's
        likelihood over the allowed cells with the two ends halved.
        """
        tree, dates = self._phase(count=2)
        model = gibbs.Model(dates, tree)
        state = model.initial_state()
        older, younger, group = model.group_dates[0]
        date_id = group[0]
        # Somewhere the date actually has likelihood, or every weight is zero
        # and draw_date takes its uniform escape hatch instead.
        peak = int(np.argmax(model.likelihood[date_id]))
        lo, hi = peak - 4, peak + 5
        state.index[younger], state.index[older] = lo, hi

        weights = model.likelihood[date_id][lo:hi + 1].astype(float).copy()
        weights[0] *= 0.5
        weights[-1] *= 0.5
        expected = np.cumsum(weights) / weights.sum()

        for u in np.linspace(1e-9, 1 - 1e-9, 200):
            assert model.draw_date(date_id, state, float(u)) == (
                lo + int(np.searchsorted(expected, u)))

    def test_the_smallest_span_is_not_free(self):
        """
        The mechanism, at the one configuration where it is largest. Boundaries
        one cell apart leave a date two cells to sit in, both of them endpoints,
        so the weighting must remove exactly a factor of two per date -- the
        2^K that inflated the near-coincident region for a group of K.
        """
        tree, dates = self._phase(count=4)
        model = gibbs.Model(dates, tree)
        state = model.initial_state()
        older, younger, group = model.group_dates[0]

        peak = int(np.argmax(model.likelihood[group[0]]))
        state.index[younger], state.index[older] = peak, peak + 1
        for date_id in group:
            state.index[date_id] = peak
            assert model.endpoint_weight(date_id, state) == 0.5

        # What log_posterior would report with no weighting: the raw likelihoods
        # and the span factor, nothing else.
        span = model.grid[peak + 1] - model.grid[peak]
        unweighted = sum(float(model.log_likelihood[d][peak]) for d in group)
        unweighted -= len(group) * float(np.log(span))
        assert model.log_posterior(state) == pytest.approx(
            unweighted - len(group) * np.log(2.0))


class TestBranchingModels:
    """
    The models this sampler exists for -- the ones exact_chain declines.
    """

    def _two_branches(self):
        a1, a2 = _element('phase', 'A1'), _element('phase', 'A2')
        branch_a = _element('sequence', 'Trench A', [
            _element('boundary', 'sA'), a1, _element('boundary', 'mA'),
            a2, _element('boundary', 'eA')])
        b1, b2 = _element('phase', 'B1'), _element('phase', 'B2')
        branch_b = _element('sequence', 'Trench B', [
            _element('boundary', 'sB'), b1, _element('boundary', 'mB'),
            b2, _element('boundary', 'eB')])
        combined = _element('phase', 'Site', [branch_a, branch_b])
        dates_a = [_date(a1['id'], 600), _date(a1['id'], 620),
                   _date(a2['id'], 540), _date(a2['id'], 560)]
        dates_b = [_date(b1['id'], 900), _date(b1['id'], 920),
                   _date(b2['id'], 840), _date(b2['id'], 860)]
        return branch_a, branch_b, combined, dates_a, dates_b

    def test_two_parallel_trenches_are_not_exactly_solvable(self):
        """The premise: this is the shape that has no exact answer to fall back
        on, and the shape that used to reach SMC silently."""
        _a, _b, combined, dates_a, dates_b = self._two_branches()
        assert ec.layout(combined, dates_a + dates_b) is None

    @pytest.mark.parametrize('uniform_span_prior', [True, False])
    def test_it_reproduces_each_branch_solved_separately(self, uniform_span_prior):
        """
        Two branches with nothing ordering them and no enclosing boundary are
        independent, so solving them one at a time is exact -- and the sampler,
        given the combined model it cannot decompose, must agree.

        Run with the span prior both on and off. On is the case that used to
        fail: the correction was applied once over the whole model, so the
        combined solve got no correction at all (there is no unique outermost
        boundary across two independent branches) while the oracle applied one
        per branch. That mismatch alone put the dates 9.92 years out and the
        boundaries 70. Grouping the correction per component of the boundary
        ordering gives each branch its own, and both now agree to about 0.5
        years -- see Model._span_priors.
        """
        branch_a, branch_b, combined, dates_a, dates_b = self._two_branches()
        exact_a = ec.solve(dates_a, branch_a, ec.layout(branch_a, dates_a),
                           num_samples=100, uniform_span_prior=uniform_span_prior)
        exact_b = ec.solve(dates_b, branch_b, ec.layout(branch_b, dates_b),
                           num_samples=100, uniform_span_prior=uniform_span_prior)
        sampled = _fixed(dates_a + dates_b, combined, sweeps=20000,
                         num_samples=100, uniform_span_prior=uniform_span_prior)

        errors = []
        for exact, subset in ((exact_a, dates_a), (exact_b, dates_b)):
            for m in subset:
                errors.append(sampled['measurements'][m['id']]['posterior_mean']
                              - exact['measurements'][m['id']]['posterior_mean'])
        errors = np.array(errors)
        assert np.sqrt((errors ** 2).mean()) < 3.0, errors.round(2)

    def test_each_independent_branch_gets_its_own_span_prior(self):
        """
        The structural claim behind the fix, asserted directly rather than only
        through its effect on the numbers: two independent branches produce two
        corrections, each over its own branch's outermost boundaries.
        """
        branch_a, branch_b, combined, dates_a, dates_b = self._two_branches()
        model = gibbs.Model(dates_a + dates_b, combined)
        assert len(model.span_priors) == 2

        branch_boundaries = []
        for branch in (branch_a, branch_b):
            chain = ec.layout(branch, dates_a if branch is branch_a else dates_b)
            branch_boundaries.append((chain.boundary_ids[0], chain.boundary_ids[-1],
                                      len(chain.boundary_ids) - 2))
        assert sorted(model.span_priors) == sorted(branch_boundaries)

    def test_the_two_branches_stay_where_their_own_dates_put_them(self):
        """
        A sanity check on the thing most likely to go wrong structurally: the
        branches are unordered, so nothing should drag one towards the other. A
        sampler that coupled them -- through a global span term, say -- would
        pull them together, and that would still be invisible in a per-date
        comparison if both branches moved by similar amounts.

        The separation is taken from the two branches' own exact solves rather
        than from the radiocarbon ages, which are 300 years apart: calibration
        does not preserve that gap (it comes out near 190 calendar years), so a
        threshold read off the inputs would be measuring the curve, not the
        sampler.
        """
        branch_a, branch_b, combined, dates_a, dates_b = self._two_branches()
        exact_a = ec.solve(dates_a, branch_a, ec.layout(branch_a, dates_a),
                           num_samples=100, uniform_span_prior=False)
        exact_b = ec.solve(dates_b, branch_b, ec.layout(branch_b, dates_b),
                           num_samples=100, uniform_span_prior=False)
        sampled = _fixed(dates_a + dates_b, combined, sweeps=20000,
                         num_samples=100, uniform_span_prior=False)

        def separation(results_a, results_b):
            mean_a = np.mean([results_a['measurements'][m['id']]['posterior_mean']
                              for m in dates_a])
            mean_b = np.mean([results_b['measurements'][m['id']]['posterior_mean']
                              for m in dates_b])
            return mean_b - mean_a

        expected = separation(exact_a, exact_b)
        assert expected > 100.0, 'the fixture should keep the branches well apart'
        assert abs(separation(sampled, sampled) - expected) < 3.0, (
            separation(sampled, sampled), expected)


class TestUniformSpanPriorGrouping:
    """
    Which boundaries the correction is applied over. The exponent counts
    boundaries left FREE inside a span, so the grouping is by component of the
    boundary ordering rather than by Sequence element -- see
    Model._span_priors for the derivation this follows.
    """

    @pytest.mark.parametrize('run', ['shared_boundary', 'ordered', 'unordered'])
    def test_it_reduces_to_the_exact_solver_on_a_chain(self, run):
        """
        The load-bearing check. exact_chain applies 1/(first - last)^(n-2) over
        the chain's own ends, so on any chain this must produce exactly that:
        one correction, the chain's ends, that exponent. `unordered` has only
        two boundaries, where the exponent is zero and there is nothing to
        apply -- exact_chain skips it there too.
        """
        measurements = ox.measurements()
        tree = ox.model_tree(run)
        chain = ec.layout(tree, measurements)
        if chain is None:
            tree, _inserted = with_implicit_boundaries(tree)
            chain = ec.layout(tree, measurements)

        model = gibbs.Model(measurements, tree)
        exponent = len(chain.boundary_ids) - 2
        if exponent <= 0:
            assert model.span_priors == []
        else:
            assert model.span_priors == [
                (chain.boundary_ids[0], chain.boundary_ids[-1], exponent)]

    def test_switching_it_off_removes_every_correction(self):
        measurements = ox.measurements()
        tree = ox.model_tree('shared_boundary')
        assert gibbs.Model(measurements, tree, uniform_span_prior=True).span_priors
        assert gibbs.Model(measurements, tree,
                           uniform_span_prior=False).span_priors == []

    def test_parallel_branches_inside_one_outer_pair_share_one_correction(self):
        """
        Two branches bracketed by a single outer Start/End take ONE correction
        over that outer pair, counting every boundary between them. They are
        comparable to the same outer pair even though they are not comparable to
        each other, and the exponent is a count of free interior boundaries --
        which does not care whether those are ordered among themselves.
        """
        left_phase, right_phase = _element('phase', 'L'), _element('phase', 'R')
        left = _element('sequence', 'Left', [
            _element('boundary', 'lStart'), left_phase, _element('boundary', 'lEnd')])
        right = _element('sequence', 'Right', [
            _element('boundary', 'rStart'), right_phase, _element('boundary', 'rEnd')])
        start, end = _element('boundary', 'Start'), _element('boundary', 'End')
        tree = _element('sequence', 'Site', [
            start, _element('phase', 'Both', [left, right]), end])
        measurements = [_date(left_phase['id'], 3000), _date(right_phase['id'], 3100)]

        model = gibbs.Model(measurements, tree)
        assert len(model.span_priors) == 1
        oldest, youngest, exponent = model.span_priors[0]
        assert (oldest, youngest) == (start['id'], end['id'])
        # Six boundaries in the component, four of them interior.
        assert exponent == 4

    def test_a_component_with_no_unique_outermost_pair_is_skipped(self):
        """
        Nothing to be about: with no single span, applying a correction would
        change the model rather than de-bias it. Two branches inside an
        unordered Phase, with no outer boundaries, is that case at the top
        level -- each branch still gets its own.
        """
        a = _element('phase', 'A')
        branch = _element('sequence', 'A seq', [
            _element('boundary', 'aStart'), a, _element('boundary', 'aEnd')])
        loose = _element('phase', 'Loose')
        tree = _element('phase', 'Site', [branch, loose])
        measurements = [_date(a['id'], 3000), _date(loose['id'], 3100)]

        model = gibbs.Model(measurements, tree)
        # aStart/aEnd are only two boundaries, so exponent 0 -- nothing at all.
        assert model.span_priors == []


class TestTheChainCanActuallyMove:
    """
    Two failures found while building this, both of which produced a sampler
    that ran, reported healthy-looking diagnostics, and was wrong.
    """

    def test_the_starting_state_is_feasible_despite_a_date_inversion(self):
        """
        Raw radiocarbon ages that contradict the stratigraphy are ordinary --
        resolving that inversion is much of why the model is built -- so the
        naive "every date at its own likelihood mode" state is usually
        infeasible. With HARD constraints that does not merely mix badly: every
        conditional collapses onto a single allowed index and the chain cannot
        move at all. The first version of this repaired the state locally and
        drove whole groups onto one index, leaving the log posterior at -inf.
        """
        early, late = _element('phase', 'Early'), _element('phase', 'Late')
        tree = _element('sequence', 'Site', [
            _element('boundary', 'Start'), early, _element('boundary', 'Mid'),
            late, _element('boundary', 'End')])
        # Inverted: the stratigraphically EARLIER phase holds the YOUNGER date.
        measurements = [_date(early['id'], 500), _date(late['id'], 900)]

        model = gibbs.Model(measurements, tree)
        state = model.initial_state()
        assert np.isfinite(model.log_posterior(state))

    def test_a_phase_of_unordered_dates_updates_as_one_block(self):
        """
        Dates in a phase are unordered with respect to each other, so they are
        conditionally independent given the boundaries and belong in one blocked
        update -- fewer steps and better mixing than sweeping them singly.
        """
        phase = _element('phase', 'Only')
        tree = _element('sequence', 'Site', [
            _element('boundary', 'Start'), phase, _element('boundary', 'End')])
        measurements = [_date(phase['id'], 3000 + 20 * i) for i in range(5)]
        model = gibbs.Model(measurements, tree)
        assert len(model.blocks) == 1
        assert len(model.blocks[0]) == 5

    def test_ordered_groups_are_never_in_the_same_block(self):
        early, late = _element('phase', 'Early'), _element('phase', 'Late')
        tree = _element('sequence', 'Site', [early, late])
        measurements = [_date(early['id'], 3200), _date(late['id'], 3000)]
        model = gibbs.Model(measurements, tree)
        for block in model.blocks:
            assert len(block) == 1, 'these two dates constrain each other'

    def test_the_wide_proposal_is_what_makes_it_usable(self, monkeypatch):
        """
        Why WIDE_MOVE_FRACTION exists, as a measurement rather than a comment.

        Adaptation tunes the translation proposal to ~30% acceptance, which on
        these targets is a step of a few years -- so moving between calibration
        lobes 40 years apart happens only by diffusion and the lobe WEIGHTS
        converge far too slowly. With the wide component switched off, the same
        run was 1.5-32.6 years from the exact answer across five seeds; with it,
        0.19-1.23.

        Asserted against the sampler ITSELF rather than against the exact solver,
        because those two currently disagree for an unrelated reason (see
        TestAgreesWithTheExactSolver). Seed-to-seed spread is what the wide
        proposal fixes, so that is what is measured.
        """
        measurements = ox.measurements()
        tree = ox.model_tree('unordered')

        def means(seed):
            sampled = _fixed(measurements, tree, sweeps=20000, num_samples=100,
                             seed=seed)
            return np.array([sampled['measurements'][m['id']]['posterior_mean']
                             for m in measurements])

        def spread_across_seeds():
            runs = np.array([means(seed) for seed in (2, 3, 4)])
            return float(runs.std(axis=0).mean())

        with_wide = spread_across_seeds()
        monkeypatch.setattr(gibbs, 'WIDE_MOVE_FRACTION', 0.0)
        without = spread_across_seeds()
        assert with_wide < 2.0, with_wide
        assert without > 3 * with_wide, (without, with_wide)


class TestOutlierModel:
    """
    The outlier model, against the same OxCal 4.4 run the exact solver is held
    to: tests/fixtures/oxcal_outlier, Shag River Mouth, 16 dates in one phase
    with NZ7756 at 970 +/- 47 BP against the rest at 537-660, a 5% prior on
    each and Outlier_Model("General", T(5), U(0,4), "t").

    That model is a chain, so all three answers are available at once -- OxCal's
    own, the exact solver's, and the sampler's -- which is what makes it worth
    testing here rather than only in test_exact_chain.

    The two solvers deliberately implement DIFFERENT outlier models, so these no
    longer check that they agree. OxCal's Outlier_Model carries one offset scale
    shared by every date using it, constrained by the data; this sampler does
    that (Model._build_outlier_tables), while exact_chain marginalises the scale
    independently per date because a shared hyperparameter does not factorise
    along its message passing. The sampler is therefore the faithful path, and
    what these tests pin is that it is closer to OxCal than the approximation.
    """

    @pytest.fixture(scope='class')
    def solved(self):
        import shag_fixture as sf
        samples, tree = sf.measurements(), sf.model_tree()
        chain = ec.layout(tree, samples)
        assert chain is not None
        exact = ec.solve(samples, tree, chain, num_samples=200)
        sampled = _fixed(samples, tree, sweeps=20000, num_samples=200,
                         grid=_exact_grid(exact))
        return sf, exact, sampled

    def test_finds_the_outlier_oxcal_found(self, solved):
        sf, _exact, sampled = solved
        probabilities = {
            m['id']: sampled['measurements'][m['id']]['outlier_probability']
            for m in sf.measurements()
        }
        assert probabilities[sf.OUTLIER] > 0.95, probabilities
        others = [v for k, v in probabilities.items() if k != sf.OUTLIER]
        # Every sound date below its own prior: the evidence should argue them
        # down, not merely leave them where they started.
        assert max(others) < sf.OUTLIER_PRIOR, probabilities

    def test_probabilities_track_oxcals(self, solved):
        """The same 0.05 bar the exact solver is held to."""
        sf, _exact, sampled = solved
        table = sf.table()
        errors = np.array([
            sampled['measurements'][m['id']]['outlier_probability']
            - table[m['id']]['outlier_probability']
            for m in sf.measurements()
        ])
        assert np.abs(errors).max() < 0.05, dict(zip(
            [m['id'] for m in sf.measurements()], errors.round(4)))

    def test_sharing_the_scale_is_what_closes_the_gap_to_oxcal(self, solved):
        """
        This gap used to be attributed to "this app's outlier implementation" in
        general, since both solvers sat the same 0.0166 from OxCal and agreed
        with each other to 0.0001. Sampling the SHARED scale instead of
        marginalising it per date turns out to be the whole of it: the sampler
        now lands within 0.0016 on all sixteen dates, ten times closer, while the
        exact solver keeps its 0.0166.
        """
        sf, exact, sampled = solved
        table = sf.table()

        def worst(results):
            return max(abs(results['measurements'][m['id']]['outlier_probability']
                           - table[m['id']]['outlier_probability'])
                       for m in sf.measurements())

        assert worst(sampled) < 0.005, worst(sampled)
        assert worst(sampled) < worst(exact) / 3

    def test_it_recovers_the_scale_oxcal_inferred(self, solved):
        """
        The shared scale is a parameter with a posterior, and OxCal exports its
        own -- so this is a direct comparison of an inferred quantity rather than
        of an answer it feeds into. On the Nenthorn charcoal fixture the two agree
        to 0.03 in log10; here the General model's 5% priors leave the data barely
        able to constrain it, so all that can be asked is that it stays inside its
        U(0,4) prior.
        """
        _sf, _exact, sampled = solved
        scale = sampled['outlier_scale']['general']
        assert 0.0 <= scale['log10_mean'] <= 4.0, scale
        assert scale['years_mean'] > 1.0

    def test_the_outlier_component_keeps_its_own_scale(self, solved):
        """
        The trap the exact solver walked into once, re-checked here because the
        sampler draws from the same mixture. The outlier component is a proper
        density whose mass mostly lies OFF the grid -- that is the statement
        that a flagged date could be anywhere -- so renormalising what remains
        would make the answer depend on the grid width.

        Same dates on a grid 4x wider must give much the same probabilities.
        """
        sf, _exact, sampled = solved
        import unittest.mock as mock
        samples, tree = sf.measurements(), sf.model_tree()
        with mock.patch.object(ec, 'BOUNDARY_WINDOW_FACTOR', 30.0):
            wide = _fixed(samples, tree, sweeps=20000, num_samples=200)
        assert (wide['grid']['upper'] - wide['grid']['lower']) > 2 * (
            sampled['grid']['upper'] - sampled['grid']['lower'])
        for m in samples:
            narrow_p = sampled['measurements'][m['id']]['outlier_probability']
            assert wide['measurements'][m['id']]['outlier_probability'] == \
                pytest.approx(narrow_p, abs=0.05), m['id']

    def _residual_case(self, model, sweeps=20000):
        """Four contemporary dates plus one 400 radiocarbon years too old."""
        phase = _element('phase', 'p')
        tree = _element('sequence', 's', [
            _element('boundary', 'b0'), phase, _element('boundary', 'b1')])
        dates = [dict(_date(phase['id'], age), outlier_prior=0.05,
                      outlier_model=model, curve_id='intcal20')
                 for age in (3000, 3020, 3040, 3060, 3460)]
        residual = dates[-1]['id']
        exact = ec.solve(dates, tree, ec.layout(tree, dates), num_samples=200)
        sampled = _fixed(dates, tree, sweeps=sweeps, num_samples=200,
                         grid=_exact_grid(exact))
        return dates, residual, exact, sampled

    def test_the_one_sided_charcoal_model_is_more_confident(self):
        """
        Same direction as the exact solver: a model that knows the material can
        only be too OLD does not have to spend prior mass on the direction it
        cannot be wrong in, so it flags a residual date harder.
        """
        _d, residual, general_exact, general = self._residual_case('general')
        _d2, residual2, charcoal_exact, charcoal = self._residual_case('charcoal')
        assert (charcoal['measurements'][residual2]['outlier_probability']
                > general['measurements'][residual]['outlier_probability'])
        # ...and the exact solver says so too, so this is not a sampler artefact.
        assert (charcoal_exact['measurements'][residual2]['outlier_probability']
                > general_exact['measurements'][residual]['outlier_probability'])

    def test_sound_dates_are_left_alone_beside_a_residual_one(self):
        dates, residual, _exact, sampled = self._residual_case('charcoal')
        for d in dates:
            if d['id'] == residual:
                continue
            assert sampled['measurements'][d['id']]['outlier_probability'] < 0.2, d['id']

    def test_an_ambiguous_outlier_needs_the_date_and_boundary_to_move_together(self):
        """
        Why the per-flagged-date move sets exist. A date that might be an
        outlier has two plausible positions hundreds of years apart, and
        reaching the far one needs the enclosing boundary to come out past it --
        neither can move alone. Measured at 100,000 sweeps over three seeds,
        those move sets took the spread of the residual date's outlier
        probability from 0.09 to 0.01 around the exact answer.

        Asserted structurally: the pairs exist for a flagged date and not for an
        unflagged one, since adding them for every date cost 2.8x the runtime
        for no gain on the sound ones.
        """
        phase = _element('phase', 'p')
        tree = _element('sequence', 's', [
            _element('boundary', 'b0'), phase, _element('boundary', 'b1')])
        flagged = dict(_date(phase['id'], 3000), outlier_prior=0.05,
                       outlier_model='general')
        plain = _date(phase['id'], 3020)
        model = gibbs.Model([flagged, plain], tree)

        pairs = [s for s in model.move_sets() if len(s) == 2]
        assert any(flagged['id'] in s for s in pairs), 'a flagged date needs them'
        assert not any(plain['id'] in s for s in pairs), 'an unflagged one does not'


class TestBoundariesBehaveLikeOxCals:
    """
    Carried over from the deleted test_bayesian.py, which is where this property
    used to be pinned. It is the whole reason Boundary is a separate parameter
    rather than just the oldest and youngest date in a phase: a phase's dates are
    modelled as a uniform scatter BETWEEN its start and end, so the transitions
    themselves sit outside the dated range.

    Its regression note is worth keeping too. An earlier implementation sampled
    the boundaries under SMC, which collapsed them onto the extreme member dates
    -- and the test meant to catch that only asserted start >= max(dates), which
    a boundary pinned 1.7 years out satisfies just as well as a correct one 79
    years out. Hence the quantitative assertion here.
    """

    def _phase_of_five(self):
        phase = _element('phase', 'A')
        tree = _element('sequence', 'Site', [
            _element('boundary', 'Start'), phase, _element('boundary', 'End')])
        dates = [_date(phase['id'], 3000 + i * 40) for i in range(5)]
        return tree, dates, phase

    def test_boundaries_extend_well_beyond_the_dated_range(self):
        tree, dates, _phase = self._phase_of_five()
        chain = ec.layout(tree, dates)
        exact = ec.solve(dates, tree, chain, num_samples=200)
        sampled = _fixed(dates, tree, sweeps=20000, num_samples=200,
                         grid=_exact_grid(exact))

        means = [sampled['measurements'][d['id']]['posterior_mean'] for d in dates]
        start = sampled['boundaries'][chain.boundary_ids[0]]['posterior']['posterior_mean']
        end = sampled['boundaries'][chain.boundary_ids[-1]]['posterior']['posterior_mean']

        # Measured at 78 and 86 years on this model, against the exact solver's
        # 76 and 84. Far above the couple of years a collapsed boundary gives.
        assert start - max(means) > 40.0, (start, max(means))
        assert min(means) - end > 40.0, (end, min(means))

    def test_it_puts_them_where_the_exact_solver_does(self):
        """
        The quantitative half: not merely outside the dates, but the same
        distance outside that exact integration of the same span density gives.
        """
        tree, dates, _phase = self._phase_of_five()
        chain = ec.layout(tree, dates)
        exact = ec.solve(dates, tree, chain, num_samples=200)
        sampled = _fixed(dates, tree, sweeps=20000, num_samples=200,
                         grid=_exact_grid(exact))
        for bid in (chain.boundary_ids[0], chain.boundary_ids[-1]):
            a = exact['boundaries'][bid]['posterior']['posterior_mean']
            b = sampled['boundaries'][bid]['posterior']['posterior_mean']
            assert b == pytest.approx(a, abs=10.0), bid

    def test_more_dates_pull_the_boundaries_in(self):
        """
        A phase's duration is weakly constrained by few dates and tightly by
        many, which is a real property of the uniform-scatter model rather than
        an artefact: the span density is (older - younger)^(-K), so a larger K
        penalises a long span harder.
        """
        def offset(count):
            phase = _element('phase', 'A')
            tree = _element('sequence', 'Site', [
                _element('boundary', 'Start'), phase, _element('boundary', 'End')])
            # Same calendar spread however many dates, so only K changes.
            dates = [_date(phase['id'], 3000 + int(160 * i / (count - 1)))
                     for i in range(count)]
            chain = ec.layout(tree, dates)
            sampled = _fixed(dates, tree, sweeps=20000, num_samples=200)
            means = [sampled['measurements'][d['id']]['posterior_mean'] for d in dates]
            start = sampled['boundaries'][chain.boundary_ids[0]]['posterior']['posterior_mean']
            return start - max(means)

        assert offset(4) > offset(9), (offset(4), offset(9))


class TestRunLength:
    """
    The run extends until it converges rather than stopping at a fixed length,
    because how many sweeps a model needs does not follow from its size -- it
    depends on how tightly the structure couples the parameters. Measured against
    exact answers: 9 dates in 3 phases reached 0.65 years at 20,000 sweeps, while
    30 dates in 6 phases was still 32 years out there, and 50 dates in 10 phases
    did BETTER than the 30-date model at the same length.
    """

    def _easy_model(self):
        """
        Three dates, not two. A TWO-date phase barely determines its own span, so
        its boundary marginals are heavy-tailed and the overlap diagnostic lands
        right on the 0.95 target -- measured 0.9501, after running to the sweep
        cap. Pinning a boolean to a margin of 0.0001 is a coin toss, and it did
        flip when an unrelated change moved it by 0.0014. Three dates reach 0.954
        in a third of the sweeps.
        """
        phase = _element('phase', 'Only')
        tree = _element('sequence', 'Site', [
            _element('boundary', 'Start'), phase, _element('boundary', 'End')])
        return ([_date(phase['id'], 3000), _date(phase['id'], 3020),
                 _date(phase['id'], 3040)], tree)

    def test_it_reports_the_length_it_actually_ran(self):
        measurements, tree = self._easy_model()
        sampled = gibbs.solve(measurements, tree, sweeps=2000, num_samples=50)
        assert sampled['sweeps'] >= 2000
        assert sampled['converged'] is True
        assert sampled['convergence'] >= gibbs.TARGET_CONVERGENCE

    def test_it_extends_a_run_that_has_not_converged(self):
        """An unreachable target forces the extension path, which must then stop
        at the cap and say so rather than running forever or claiming success."""
        measurements, tree = self._easy_model()
        sampled = gibbs.solve(measurements, tree, sweeps=2000, num_samples=50,
                              target_convergence=1.01, max_sweeps=8000)
        assert sampled['sweeps'] > 2000
        assert sampled['sweeps'] <= 8000 + 2000, sampled['sweeps']
        assert sampled['converged'] is False
        assert sampled['convergence'] < 1.01

    def test_burn_in_scales_with_the_run_rather_than_the_starting_budget(self):
        """
        The regression that made a longer run WORSE. The sweeps discarded up
        front are fixed before the length is known -- they are there to tune the
        proposal scales -- so an extended run kept its whole non-stationary head
        and the 30-date fixture came out 6.17 years from the exact answer after
        214,000 sweeps, against 1.46 at a fixed 100,000.

        Asserted on the mechanism: the reported draw count must be materially
        less than the sweeps run, because a fraction of rounds is dropped
        whatever the length.
        """
        measurements, tree = self._easy_model()
        model = gibbs.Model(measurements, tree)
        chain = gibbs.run(model, sweeps=4000, target_convergence=1.01,
                          max_sweeps=16000)
        assert chain['sweeps'] > 4000
        assert chain['sweeps_kept'] < chain['sweeps'] * (1 - gibbs.BURN_IN_FRACTION / 2)


class TestReportedShape:
    def test_it_returns_what_the_other_solvers_do(self):
        """
        Same shape as exact_chain.solve returns,
        so nothing downstream needs to know which produced a result.
        """
        phase = _element('phase', 'Only')
        tree = _element('sequence', 'Site', [
            _element('boundary', 'Start'), phase, _element('boundary', 'End')])
        measurements = [_date(phase['id'], 3000), _date(phase['id'], 3050)]
        sampled = _fixed(measurements, tree, sweeps=2000, num_samples=50)

        assert sampled['method'] == 'gibbs'
        assert set(sampled['measurements']) == {m['id'] for m in measurements}
        for summary in sampled['measurements'].values():
            for key in ('posterior_mean', 'posterior_median', 'posterior_std',
                        'hpd_68', 'hpd_95', 'hpd_68_ranges', 'hpd_95_ranges',
                        'probability_distribution', 'posterior_samples'):
                assert key in summary, key
        assert len(sampled['boundaries']) == 2
        assert 0.0 <= sampled['convergence'] <= 1.0

    def test_an_outlier_probability_is_reported_when_asked_for(self):
        """The outlier model is a property of a date, not of how the model is
        solved, so it comes through the shared likelihood tables unchanged."""
        phase = _element('phase', 'Only')
        tree = _element('sequence', 'Site', [
            _element('boundary', 'Start'), phase, _element('boundary', 'End')])
        measurements = [
            dict(_date(phase['id'], 3000), outlier_prior=0.05, outlier_model='general'),
            dict(_date(phase['id'], 3050), outlier_prior=0.05, outlier_model='general'),
        ]
        sampled = _fixed(measurements, tree, sweeps=2000, num_samples=50)
        for m in measurements:
            probability = sampled['measurements'][m['id']]['outlier_probability']
            assert 0.0 <= probability <= 1.0
