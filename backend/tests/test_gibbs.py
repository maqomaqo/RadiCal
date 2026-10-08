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

Runs are untempered (temperatures=1) unless they need tempering: each tempered
copy costs a whole sweep, so the default of three temperatures triples a run,
and most of these models mix without it. The tempered ones are the ladder in
TestEveryKernelTargetsOneDistribution, which holds the recorded chain to its
target; TestParallelTempering and TestLobeProbe, which are about crossing
lobes; and the dune-shaped model in TestBoundaryTiesCountHalf, where
untempered chains stay in one lobe.
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


def _fixed(measurements, tree, sweeps=20000, temperatures=1, **kwargs):
    """
    Run exactly `sweeps` sweeps, with the convergence-driven extension disabled.

    solve() extends until it converges, which is what production wants and what
    TestRunLength covers -- but it makes every other test's runtime depend on how
    hard its model happens to be, and the tolerances here are calibrated against
    a known length. Untempered, as the module docstring says.
    """
    return gibbs.solve(measurements, tree, sweeps=sweeps, max_sweeps=sweeps,
                       temperatures=temperatures, **kwargs)


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

        # What log_posterior would report with no endpoint weighting: the raw
        # likelihoods, the span factor, and the limits factor -- that last one
        # because the dates bound the boundaries they sit between, so it applies
        # even here with nothing else constraining them (see
        # exact_chain.pair_limits).
        span = model.grid[peak + 1] - model.grid[peak]
        unweighted = sum(float(model.log_likelihood[d][peak]) for d in group)
        unweighted -= len(group) * float(np.log(span))
        for (oldest_id, youngest_id), row in model.limits_tables.items():
            if (oldest_id, youngest_id) == (older, younger):
                unweighted += float(row[1])          # a span of one cell
        assert model.log_posterior(state) == pytest.approx(
            unweighted - len(group) * np.log(2.0))


class TestEveryKernelTargetsOneDistribution:
    """
    The Gibbs draws and the Metropolis shift moves are two kernels for ONE
    posterior, so each must be a kernel of the same discrete weights -- which
    is Model.log_posterior, the only place the weights are written down.

    They once were not. draw_date halved the end cells of the interval it
    allows, whatever set them, while log_posterior halved only a date sitting
    on one of its group's Boundaries: a tie between two ORDERED DATES (a
    Sequence of Phases with no Boundary between them) was half weight in the
    draws and full weight in the Metropolis ratio. Nothing failed -- each
    kernel is a valid sampler of its own target -- and the pooled answer
    depended on how much of each a run used. Against a direct enumeration of
    the weights, Gibbs alone sat 0.07-0.10 (total variation, per marginal)
    from log_posterior's target and MH alone sat 0.07-0.14 from the Gibbs one,
    at both of two grid steps.
    """

    def _ordered(self, step=40.0):
        """Two Phases of two dates each, older then younger, no Boundary
        between them, with dates close enough that ties matter."""
        older = _element('phase', 'Older')
        younger = _element('phase', 'Younger')
        tree = _element('sequence', 'Site', [
            _element('boundary', 'Start'), older, younger,
            _element('boundary', 'End')])
        dates = [_date(older['id'], 3020), _date(older['id'], 3010),
                 _date(younger['id'], 3000), _date(younger['id'], 2990)]
        return gibbs.Model(dates, tree, grid=np.arange(3100, 3500 + 1e-9, step))

    def _random_states(self, model, count=12, seed=3):
        rng = np.random.default_rng(seed)
        state = model.initial_state()
        for _ in range(count):
            for _ in range(int(rng.integers(5, 25))):
                for date_id in model.drawable_ids:
                    state.index[date_id] = model.draw_date(
                        date_id, state, float(rng.random()))
                for boundary_id in model.boundary_ids:
                    state.index[boundary_id] = model.draw_boundary(
                        boundary_id, state, float(rng.random()))
            yield state.copy()

    def test_every_full_conditional_is_the_log_posterior_over_its_interval(self):
        """
        No sampling: the probability a draw gives each cell, against what
        log_posterior says that cell is worth with everything else held. Their
        ratios must agree for every date and Boundary at every state, and
        a tie is where they did not -- by exactly log 2.
        """
        model = self._ordered()
        us = (np.arange(1500) + 0.5) / 1500
        for state in self._random_states(model):
            for param in model.params:
                draw = (model.draw_boundary if param in model.boundary_set
                        else model.draw_date)
                lo, hi = model.allowed(param, state)
                if hi <= lo:
                    continue
                drawn = np.bincount([draw(param, state, float(u)) for u in us],
                                    minlength=model.M) / len(us)
                held, log_post = state.index[param], {}
                for cell in range(lo, hi + 1):
                    state.index[param] = cell
                    log_post[cell] = model.log_posterior(state)
                state.index[param] = held
                # Cells the draw visits often enough to read a ratio from.
                cells = [c for c in range(lo, hi + 1)
                         if drawn[c] > 0.03 and np.isfinite(log_post[c])]
                if len(cells) < 2:
                    continue
                ref = cells[0]
                for cell in cells[1:]:
                    assert (np.log(drawn[cell] / drawn[ref])
                            == pytest.approx(log_post[cell] - log_post[ref],
                                             abs=0.15)), (param, lo, hi, cell)

    def test_a_tie_between_ordered_dates_counts_a_half(self):
        model = self._ordered()
        state = model.initial_state()
        first, second, third, fourth = model.date_ids
        older, younger = model.group_dates[0][:2]
        state.index[older], state.index[younger] = 10, 0
        for date_id, cell in ((first, 6), (second, 9), (third, 5), (fourth, 2)):
            state.index[date_id] = cell
        apart = model.log_posterior(state)
        assert model.endpoint_weight(first, state) == 1.0

        state.index[first] = 5
        tied = model.log_posterior(state)
        assert model.endpoint_weight(first, state) == 0.5
        assert model.endpoint_weight(third, state) == 0.5
        assert model.endpoint_weight(second, state) == 1.0
        log_lik = model.log_likelihood[first]
        assert tied - apart == pytest.approx(
            float(log_lik[5] - log_lik[6]) - np.log(2.0))

    def test_every_tie_a_date_has_is_its_own_half(self):
        """
        One half per tied ordering EDGE. A date tied to both dates of the
        next Phase at one cell has two, not one: given only one half the
        conditionals of its neighbours would disagree about the weight of the
        configuration, and no joint distribution has them all.
        """
        model = self._ordered()
        state = model.initial_state()
        date_id = model.date_ids[0]
        for cell, other in zip((8, 9, 8, 8), model.date_ids):
            state.index[other] = cell
        assert model.endpoint_weight(date_id, state) == 0.25

    def test_a_date_at_the_edge_of_its_own_window_is_not_halved(self):
        """
        The interval a draw may use can end at the date's own TPQ/TAQ window as
        well as at a neighbour. The likelihood table is already cut there, and
        log_posterior gives that cell no half, so neither may the draw.
        """
        phase = _element('phase', 'Only')
        tree = _element('sequence', 'Site', [
            _element('boundary', 'Start'), phase, _element('boundary', 'End')])
        dates = [_date(phase['id'], 3000 + 15 * i) for i in range(2)]
        model = gibbs.Model(dates, tree)
        state = model.initial_state()
        older, younger, group = model.group_dates[0]
        date_id = group[0]
        peak = int(np.argmax(model.likelihood[date_id]))
        lo, hi = peak - 4, peak + 5
        state.index[younger], state.index[older] = lo - 3, hi + 3
        model.window[date_id] = (lo, hi)
        weights = model.likelihood[date_id][lo:hi + 1].astype(float)
        expected = np.cumsum(weights) / weights.sum()
        for u in np.linspace(1e-9, 1 - 1e-9, 200):
            assert model.draw_date(date_id, state, float(u)) == (
                lo + int(np.searchsorted(expected, u)))

    def test_every_date_on_a_group_boundary_is_still_tied_to_it(self):
        """What date_groups used to hold: the tie set contains every
        (date, boundary) containment, and a date has each boundary once."""
        model = gibbs.Model(ox.measurements(), ox.model_tree('ordered'))
        for older, younger, dates in model.group_dates:
            for date_id in dates:
                assert older in model.tied_with[date_id]
                assert younger in model.tied_with[date_id]
                assert len(model.tied_with[date_id]) == len(set(model.tied_with[date_id]))

    def _enumerated(self, model):
        """The posterior over (older, younger, four dates), by summing every
        configuration -- the weights log_posterior defines, written out
        independently of it -- as marginals of the two Boundaries and dates."""
        cells = model.M
        likelihood = np.stack([model.likelihood[d] for d in model.date_ids])
        group = model.group_dates[0]
        span_row = model.span_prior_rows[0]
        marginals = {name: np.zeros(cells) for name in ('older', 'younger')}
        dates = np.zeros((4, cells))
        total = 0.0
        for o in range(cells):
            for y in range(o + 1):
                n = o - y
                ends = np.ones(n + 1)
                # A date on a Boundary is tied to it, a half; with both
                # Boundaries in its cell it is tied to each, a quarter.
                ends[0] *= 0.5
                ends[-1] *= 0.5
                weight = likelihood[:, y:o + 1] * ends
                # The Boundaries' own tie, a half, and the span a group meeting
                # in one cell is charged: a quarter of a cell per date, so the
                # group is its dates' likelihoods there at the tie's half.
                span = n * model.dt if n else model.dt / 4
                prefactor = (0.5 if n == 0 else 1.0) * np.exp(-4 * np.log(span) + span_row[n])
                axes = np.arange(n + 1)
                a1, a2, b1, b2 = np.meshgrid(axes, axes, axes, axes, indexing='ij')
                ordered = np.minimum(a1, a2) >= np.maximum(b1, b2)
                ties = (a1 == b1).astype(int) + (a1 == b2) + (a2 == b1) + (a2 == b2)
                w = (weight[0][a1] * weight[1][a2] * weight[2][b1] * weight[3][b2]
                     * np.where(ordered, 0.5 ** ties, 0.0) * prefactor)
                marginals['older'][o] += w.sum()
                marginals['younger'][y] += w.sum()
                total += w.sum()
                for k, axis in enumerate((a1, a2, b1, b2)):
                    dates[k, y:o + 1] += np.bincount(
                        axis.ravel(), weights=w.ravel(), minlength=n + 1)
        return (marginals['older'] / total, marginals['younger'] / total,
                dates / total)

    @staticmethod
    def _tv(p, q):
        return 0.5 * float(np.abs(p - q).sum())

    def _histograms(self, model, move):
        """Marginals of (older, younger, dates) over a chain that makes `move`
        and nothing else, from the same start."""
        older_id, younger_id = model.group_dates[0][:2]
        older = np.zeros(model.M)
        younger = np.zeros(model.M)
        dates = np.zeros((4, model.M))
        for step, state in move:
            older[state.index[older_id]] += 1
            younger[state.index[younger_id]] += 1
            for k, d in enumerate(model.date_ids):
                dates[k, state.index[d]] += 1
        n = older.sum()
        return older / n, younger / n, dates / n

    def _gibbs_only(self, model, sweeps=40000, burn=2000, seed=0):
        rng = np.random.default_rng(seed)
        state = model.initial_state()

        def chain():
            for s in range(sweeps + burn):
                for block in model.blocks:
                    for date_id in block:
                        state.index[date_id] = model.draw_date(
                            date_id, state, float(rng.random()))
                for boundary_id in model.boundary_ids:
                    state.index[boundary_id] = model.draw_boundary(
                        boundary_id, state, float(rng.random()))
                if s >= burn:
                    yield s, state
        return self._histograms(model, chain())

    def _mh_only(self, model, steps=400000, burn=20000, seed=0):
        rng = np.random.default_rng(seed)
        sets = [[p] for p in model.params] + model.move_sets()
        holder = {'state': model.initial_state()}
        holder['lp'] = model.log_posterior(holder['state'])

        def chain():
            for s in range(steps + burn):
                params = sets[int(rng.integers(len(sets)))]
                step = int(rng.integers(1, 4)) * (1 if rng.random() < 0.5 else -1)
                proposal = holder['state'].copy()
                inside = True
                for param in params:
                    moved = holder['state'].index[param] + step
                    if not 0 <= moved < model.M:
                        inside = False
                        break
                    proposal.index[param] = moved
                if inside:
                    lp = model.log_posterior(proposal)
                    if np.isfinite(lp) and (
                            lp >= holder['lp'] or rng.random() < np.exp(lp - holder['lp'])):
                        holder['state'], holder['lp'] = proposal, lp
                if s >= burn and s % 3 == 0:
                    yield s, holder['state']
        return self._histograms(model, chain())

    def test_gibbs_alone_and_metropolis_alone_reach_the_same_distribution(self):
        """
        The two kernels run separately, each against the enumeration. Measured
        on this grid (11 cells), both within 0.01 total variation of it
        per marginal; before the weights agreed, 0.04-0.09 for Gibbs and
        0.07-0.14 for MH on the same marginals -- the lopsided half they
        disagreed about. 0.03 is what the noise of these run lengths allows
        with room to spare and what that gap does not.
        """
        model = self._ordered(step=40.0)
        target = self._enumerated(model)
        for name, got in (('Gibbs', self._gibbs_only(model)),
                          ('MH', self._mh_only(model))):
            assert self._tv(got[0], target[0]) < 0.03, (name, 'older boundary')
            assert self._tv(got[1], target[1]) < 0.03, (name, 'younger boundary')
            for k in range(4):
                assert self._tv(got[2][k], target[2][k]) < 0.03, (name, 'date', k)


    def test_the_recorded_chain_of_a_tempered_ladder_reaches_the_same_distribution(self):
        """
        Parallel tempering (gibbs._Chain) adds hot copies and swaps; only the
        power-1 chain is recorded, and the swap rule must leave ITS target
        alone. Its marginals against the enumeration, at the same tolerance.
        """
        model = self._ordered(step=40.0)
        target = self._enumerated(model)
        chain = gibbs._Chain(model, 30000, 5, 2000, 30000, 0, temperatures=3)
        chain.burn(2000)

        def run():
            for s in range(30000):
                chain.sweep()
                yield s, chain.state
        got = self._histograms(model, run())
        assert self._tv(got[0], target[0]) < 0.03
        assert self._tv(got[1], target[1]) < 0.03
        for k in range(4):
            assert self._tv(got[2][k], target[2][k]) < 0.03, k
        assert all(a > 0 for a in chain.swaps_accepted)

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
        The load-bearing check. exact_chain divides out the interior boundaries'
        freedom over the chain's own ends, so on any chain this must produce exactly
        that: one correction, the chain's ends, that exponent.

        `unordered` has only two boundaries, so the exponent is zero and there is no
        interior freedom to divide out. The PAIR is still listed, because the limits
        factor applies to any pair with a span between them -- but these fixtures
        constrain no boundary, so nothing is applied, and limits_tables is the thing
        to assert on rather than the list.
        """
        measurements = ox.measurements()
        tree = ox.model_tree(run)
        chain = ec.layout(tree, measurements)
        if chain is None:
            tree, _inserted = with_implicit_boundaries(tree)
            chain = ec.layout(tree, measurements)

        model = gibbs.Model(measurements, tree)
        exponent = len(chain.boundary_ids) - 2
        assert model.span_priors == [
            (chain.boundary_ids[0], chain.boundary_ids[-1], max(exponent, 0))]
        # One limits factor, over the same pair. It applies at any number of
        # boundaries -- the dates bound the ones enclosing them -- so `unordered`,
        # with an exponent of zero, is still corrected.
        assert list(model.limits_tables) == [
            (chain.boundary_ids[0], chain.boundary_ids[-1])]

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
        change the model rather than de-bias it. Two branches inside an unordered
        Phase, with no outer boundaries, is that case at the top level -- the
        branch that DOES have a pair still gets its own entry.

        That entry carries exponent 0 (two boundaries, no interior freedom) and,
        because nothing constrains those boundaries, no limits factor either. So
        the model is corrected nowhere, which is the point -- asserted on what is
        applied rather than on the list, since the list now names every pair with
        a span whether or not anything acts on it.
        """
        a = _element('phase', 'A')
        branch = _element('sequence', 'A seq', [
            _element('boundary', 'aStart'), a, _element('boundary', 'aEnd')])
        loose = _element('phase', 'Loose')
        tree = _element('phase', 'Site', [branch, loose])
        measurements = [_date(a['id'], 3000), _date(loose['id'], 3100)]

        model = gibbs.Model(measurements, tree)
        # The branch's own pair, with no interior boundaries to divide out. It does
        # get a limits factor, from the dates inside it -- and only from those: the
        # loose phase outside the branch must not contribute, which is what the
        # enclosure test in _limits_factor_tables is for.
        assert [exponent for _o, _y, exponent in model.span_priors] == [0]
        assert len(model.limits_tables) == 1
        pair = next(iter(model.limits_tables))
        assert set(pair) == {b['id'] for b in tree['children'][0]['children']
                             if b['element_type'] == 'boundary'}


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

    def test_the_starting_state_leaves_room_below_a_date_at_the_young_edge(self):
        """
        A date whose likelihood peaks in the youngest cell of the grid -- a
        marine shell at 620 BP, which is about what the ocean reads AT 1950,
        so its likelihood only rises towards the young edge -- used to be
        placed THERE, and the End boundary that has to be younger than it
        then had no cell to go to. Found on a fourteen-site regional model,
        on the atoll with the youngest dates, as "must be older than 0 and
        younger than -1".

        The oldest-to-youngest walk has to look ahead: a parameter with
        things below it cannot take the youngest cells those things need.
        """
        phase = _element('phase', 'Tongareva')
        tree = _element('sequence', 'Site', [
            _element('boundary', 'Start'), phase, _element('boundary', 'End')])
        shell = dict(_date(phase['id'], 620), curve_id='marine20',
                     error_1sigma=60, material_type='shell',
                     sample_type='marine')
        measurements = [shell, _date(phase['id'], 150)]

        model = gibbs.Model(measurements, tree)
        # The premise: the naive placement really is the youngest cell.
        assert int(np.argmax(model.likelihood[shell['id']])) == 0
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
        0.19-1.23. Since Boundaries were kept a cell apart both are steadier --
        a spread across seeds of 1.27 years without it against 0.53 with -- so
        the margin asserted is twofold rather than the threefold it once was.

        Asserted against the sampler ITSELF rather than against the exact solver,
        because those two currently disagree for an unrelated reason (see
        TestAgreesWithTheExactSolver). Seed-to-seed spread is what the wide
        proposal fixes, so that is what is measured.

        Untempered: parallel tempering also carries chains between lobes, and
        with it on the wide proposal's own effect no longer shows.
        """
        measurements = ox.measurements()
        tree = ox.model_tree('unordered')

        def means(seed):
            sampled = _fixed(measurements, tree, sweeps=20000, num_samples=100,
                             seed=seed, temperatures=1)
            return np.array([sampled['measurements'][m['id']]['posterior_mean']
                             for m in measurements])

        def spread_across_seeds():
            runs = np.array([means(seed) for seed in (2, 3, 4)])
            return float(runs.std(axis=0).mean())

        with_wide = spread_across_seeds()
        monkeypatch.setattr(gibbs, 'WIDE_MOVE_FRACTION', 0.0)
        without = spread_across_seeds()
        assert with_wide < 2.0, with_wide
        assert without > 2 * with_wide, (without, with_wide)


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

    def _residual_case(self, model, sweeps=20000, offset=400):
        """Four contemporary dates plus one `offset` radiocarbon years too old."""
        phase = _element('phase', 'p')
        tree = _element('sequence', 's', [
            _element('boundary', 'b0'), phase, _element('boundary', 'b1')])
        dates = [dict(_date(phase['id'], age), outlier_prior=0.05,
                      outlier_model=model, curve_id='intcal20')
                 for age in (3000, 3020, 3040, 3060, 3060 + offset)]
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

        At a 250-year offset rather than this class's usual 400, because at 400 both
        models are past 0.99 and the question stops being answerable -- measured
        0.9876 against 0.9931 for the exact solver and 0.9973 against 0.9923 for the
        sampler, i.e. the sampler's own noise is larger than what is being compared.
        At 250 the effect is a factor of about 1.5 and both solvers show it plainly.
        """
        _d, residual, general_exact, general = self._residual_case('general', offset=250)
        _d2, residual2, charcoal_exact, charcoal = self._residual_case('charcoal',
                                                                      offset=250)
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
        sampled = gibbs.solve(measurements, tree, sweeps=2000, num_samples=50,
                              temperatures=1)
        assert sampled['sweeps'] >= 2000
        assert sampled['converged'] is True
        assert sampled['convergence'] >= gibbs.TARGET_CONVERGENCE

    def test_it_extends_a_run_that_has_not_converged(self):
        """An unreachable target forces the extension path, which must then stop
        at the cap and say so rather than running forever or claiming success."""
        measurements, tree = self._easy_model()
        sampled = gibbs.solve(measurements, tree, sweeps=2000, num_samples=50,
                              target_convergence=1.01, max_sweeps=8000,
                              temperatures=1)
        assert sampled['sweeps'] > 2000
        assert sampled['sweeps'] <= 8000 + 2000, sampled['sweeps']
        assert sampled['converged'] is False
        assert sampled['convergence'] < 1.01

    def test_min_sweeps_holds_a_converged_run_to_its_floor(self):
        """
        A floor above the length the run would have stopped at is the only
        thing that keeps it going -- on one chain or several. Without it,
        asking for a longer run did nothing at all.
        """
        measurements, tree = self._easy_model()
        plain = gibbs.solve(measurements, tree, sweeps=6000, num_samples=50,
                            temperatures=1)
        floor = plain['sweeps'] + 20000
        held = gibbs.solve(measurements, tree, sweeps=6000, num_samples=50,
                           min_sweeps=floor, temperatures=1)
        assert held['sweeps'] >= floor
        assert held['converged'] is True

        two = gibbs.solve(measurements, tree, sweeps=6000, num_samples=50, chains=2,
                          temperatures=1)
        floor = max(two['sweeps_per_chain']) + 10000
        held = gibbs.solve(measurements, tree, sweeps=6000, num_samples=50,
                           chains=2, min_sweeps=floor, temperatures=1)
        assert min(held['sweeps_per_chain']) >= floor

    def test_the_seed_reproduces_a_run_and_a_new_one_replicates_it(self):
        measurements, tree = self._easy_model()
        a = gibbs.solve(measurements, tree, sweeps=2000, num_samples=50, seed=11,
                        temperatures=1)
        b = gibbs.solve(measurements, tree, sweeps=2000, num_samples=50, seed=11,
                        temperatures=1)
        c = gibbs.solve(measurements, tree, sweeps=2000, num_samples=50, seed=12,
                        temperatures=1)
        date = measurements[0]['id']
        means = [r['measurements'][date]['posterior_mean'] for r in (a, b, c)]
        assert means[0] == means[1]
        assert means[0] != means[2]
        assert means[2] == pytest.approx(means[0], abs=5)

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
                          max_sweeps=16000, temperatures=1)
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


class TestParallelChains:
    """
    Several independent chains in worker processes, pooled (gibbs.solve_chains).

    The pooling is arithmetic on run()'s output, so it is checked as
    arithmetic: the pooled histograms must be exactly the sum of the chains
    run one at a time with the seeds solve_chains gives them -- at a length
    forced to the cap, since the pooled run stops on its own rule and a
    chain alone on its own. Everything else here is about the stopping rule,
    the diagnostics and the plumbing -- progress, cancellation, a failing
    chain -- which are the parts a process boundary can silently break.
    """

    def _model(self):
        phase = _element('phase', 'Only')
        tree = _element('sequence', 'Site', [
            _element('boundary', 'Start'), phase, _element('boundary', 'End')])
        return ([_date(phase['id'], 3000), _date(phase['id'], 3020),
                 _date(phase['id'], 3040)], tree)

    def test_the_pool_is_the_sum_of_the_chains_run_alone(self):
        measurements, tree = self._model()
        model = gibbs.Model(measurements, tree)
        pooled = gibbs.solve_chains(measurements, tree, chains=3, sweeps=1500,
                                    max_sweeps=1500, seed=7, grid=model.grid,
                                    target_convergence=1.01, temperatures=1)
        alone = [gibbs.run(model, sweeps=1500, max_sweeps=1500, seed=7 + c,
                           target_convergence=1.01, temperatures=1, **start)
                 for c, start in enumerate(gibbs.chain_starts(3, 7))]
        for param in model.params:
            expected = sum(r['histograms'][param] for r in alone)
            assert np.array_equal(pooled['histograms'][param], expected), param
        assert pooled['sweeps'] == sum(r['sweeps'] for r in alone)
        assert pooled['sweeps_per_chain'] == [r['sweeps'] for r in alone]
        assert pooled['sweeps_kept'] == sum(r['sweeps_kept'] for r in alone)
        assert pooled['chains'] == 3

    def test_chain_zero_is_the_single_chain_run(self):
        """So a multi-chain result is reproducible chain by chain, and a
        single-chain run is chain zero of any wider one with the same seed."""
        measurements, tree = self._model()
        model = gibbs.Model(measurements, tree)
        alone = gibbs.run(model, sweeps=1500, max_sweeps=1500, seed=42,
                          target_convergence=1.01, temperatures=1)
        pooled = gibbs.solve_chains(measurements, tree, chains=2, sweeps=1500,
                                    max_sweeps=1500, seed=42, grid=model.grid,
                                    target_convergence=1.01, temperatures=1)
        other = gibbs.run(model, sweeps=1500, max_sweeps=1500, seed=43,
                          target_convergence=1.01, temperatures=1,
                          **gibbs.chain_starts(2, 42)[1])
        for param in model.params:
            assert np.array_equal(pooled['histograms'][param],
                                  alone['histograms'][param] + other['histograms'][param])

    def test_solve_reports_the_agreement_between_chains(self):
        measurements, tree = self._model()
        one = _fixed(measurements, tree, sweeps=3000, num_samples=50)
        four = _fixed(measurements, tree, sweeps=3000, num_samples=50, chains=4)
        assert one['chains'] == 1
        assert one['between_chain_convergence'] is None and one['rhat'] is None
        assert four['chains'] == 4
        assert four['sweeps'] == sum(four['sweeps_per_chain'])
        assert len(four['per_chain_convergence']) == 4
        assert 0.0 < four['between_chain_convergence'] <= 1.0
        assert four['rhat'] >= 1.0
        # An easy model: the chains agree by R-hat. The overlap is held
        # loosely because 2,400 kept sweeps on a one-year grid is a noisy
        # histogram (0.89 measured) -- which is why it is not the gate.
        assert four['between_chain_convergence'] > 0.8
        assert four['rhat'] < gibbs.TARGET_RHAT
        assert four['rhat_target'] == gibbs.TARGET_RHAT
        assert four['convergence'] == four['within_chain_convergence']
        # Same answer, more draws behind it.
        for d in measurements:
            assert four['measurements'][d['id']]['posterior_mean'] == pytest.approx(
                one['measurements'][d['id']]['posterior_mean'], abs=3.0)

    def test_the_shared_rule_stops_each_chain_sooner(self):
        """
        Why the chains share a stopping rule. The first-half/second-half
        deficit is mostly Monte Carlo noise, which falls with the draws
        pooled, so four chains reach the target in well under the sweeps one
        chain needs -- and that, not the extra draws, is what makes the idle
        cores into wall time. Measured on the Nenthorn fixture: 54,000 on one
        chain against about 14,000 each on four.
        """
        measurements = ox.measurements()
        tree = ox.model_tree('unordered')
        one = gibbs.solve(measurements, tree, num_samples=50, sweeps=4000,
                          temperatures=1)
        four = gibbs.solve(measurements, tree, num_samples=50, sweeps=4000, chains=4,
                           temperatures=1)
        assert one['converged'] and four['converged']
        assert max(four['sweeps_per_chain']) < one['sweeps'] * 0.6, (
            four['sweeps_per_chain'], one['sweeps'])
        # All chains advance in step under one rule.
        assert len(set(four['sweeps_per_chain'])) == 1

    def test_disagreeing_chains_are_reported_as_such(self):
        """_between_chains on histograms built by hand: identical chains score
        perfect agreement, chains in different places score none."""
        same = [{'p': np.array([1.0, 4.0, 1.0])}] * 3
        overlap, rhat = gibbs._between_chains(same, ['p'])
        assert overlap == pytest.approx(1.0)
        assert rhat == pytest.approx(1.0)

        apart = [{'p': np.array([6.0, 0.0, 0.0, 0.0])},
                 {'p': np.array([0.0, 0.0, 0.0, 6.0])}]
        overlap, rhat = gibbs._between_chains(apart, ['p'])
        assert overlap == pytest.approx(0.5)
        # No variance within either chain, so R-hat is undefined rather than
        # infinite: None survives JSON where inf does not.
        assert rhat is None

        drifted = [{'p': np.array([4.0, 2.0, 0.0, 0.0])},
                   {'p': np.array([0.0, 0.0, 2.0, 4.0])}]
        overlap, rhat = gibbs._between_chains(drifted, ['p'])
        assert overlap < 0.6
        assert rhat > 2.0

    def test_progress_is_reported_as_one_run(self):
        measurements, tree = self._model()
        seen = []
        gibbs.solve_chains(measurements, tree, chains=2, sweeps=1500,
                           max_sweeps=1500, temperatures=1,
                           on_progress=lambda info: seen.append(info) or True)
        assert seen, 'no progress reported'
        assert all(info['chains'] == 2 for info in seen)
        assert all(info['max_sweeps'] == 2 * 1500 for info in seen)
        assert seen[-1]['sweeps'] == 2 * 1500
        assert seen[-1]['phase'] == 'sampling'
        assert 0.0 < seen[-1]['convergence'] <= 1.0

    def test_returning_false_cancels_every_chain(self):
        measurements, tree = self._model()
        calls = []

        def stop(info):
            calls.append(info)
            return len(calls) < 2

        with pytest.raises(gibbs.Cancelled):
            gibbs.solve_chains(measurements, tree, chains=2, sweeps=4000,
                               max_sweeps=4000, on_progress=stop, temperatures=1)
        # ...and the pool is still usable afterwards.
        again = gibbs.solve_chains(measurements, tree, chains=2, sweeps=1000,
                                   max_sweeps=1000, temperatures=1)
        assert again['sweeps'] == 2000

    def test_a_failing_chain_raises_its_error_here(self):
        measurements, tree = self._model()
        measurements[0]['taq_age_bp'] = 100.0
        measurements[0]['tpq_age_bp'] = 50.0
        with pytest.raises(ValueError, match='no probability'):
            gibbs.solve_chains(measurements, tree, chains=2, sweeps=1000,
                               max_sweeps=1000)

    def test_a_failure_leaves_the_workers_clean_for_the_next_solve(self):
        """
        The regression: with one worker's error raised before the others'
        replies were read, a reply to the FAILED solve's build sat at the
        head of the next solve's pipe and was read as its first answer --
        "unsupported operand type(s) for +: 'int' and 'dict'" from the
        progress arithmetic, several tests later.
        """
        measurements, tree = self._model()
        broken = [dict(m) for m in measurements]
        broken[0]['taq_age_bp'] = 100.0
        broken[0]['tpq_age_bp'] = 50.0
        for _ in range(2):
            with pytest.raises(ValueError):
                gibbs.solve_chains(broken, tree, chains=3, sweeps=1000, max_sweeps=1000)
            good = gibbs.solve_chains(measurements, tree, chains=3, sweeps=1000,
                                      max_sweeps=1000, target_convergence=1.01,
                                      on_progress=lambda info: True, temperatures=1)
            assert good['sweeps'] == 3000


class TestChainStarts:
    """
    Where the chains of a multi-chain run start (gibbs.start_quantiles,
    Model.initial_state). R-hat can only report a calibration range some chain
    is in, so chains that all start at the likelihood modes can settle in one
    range together and pass at 1.00 -- which is what a real multi-area site model did.
    """

    def _model(self):
        phase = _element('phase', 'Only')
        tree = _element('sequence', 'Site', [
            _element('boundary', 'Start'), phase, _element('boundary', 'End')])
        return ([_date(phase['id'], 640), _date(phase['id'], 650),
                 _date(phase['id'], 660)], tree)

    def test_chain_zero_starts_at_the_modes_and_the_rest_are_spread(self):
        starts = gibbs.start_quantiles(4, 42)
        assert starts[0] is None
        low, high = gibbs.START_QUANTILE_RANGE
        width = (high - low) / 3
        for c, start in enumerate(starts[1:]):
            assert low + c * width <= start <= low + (c + 1) * width
        assert gibbs.start_quantiles(4, 42) == starts
        assert gibbs.start_quantiles(1, 42) == [None]

    def test_a_quantile_moves_every_date_together(self):
        measurements, tree = self._model()
        model = gibbs.Model(measurements, tree)
        young, old = model.initial_state(0.05), model.initial_state(0.95)
        for date_id in model.date_ids:
            assert young.index[date_id] < old.index[date_id]
        for state in (young, old, model.initial_state()):
            assert np.isfinite(model.log_posterior(state))

    def test_the_chains_of_a_run_do_not_all_start_in_one_place(self):
        measurements, tree = self._model()
        model = gibbs.Model(measurements, tree)
        starts = [model.initial_state(q).index[model.date_ids[0]]
                  for q in gibbs.start_quantiles(4, 42)]
        assert len(set(starts)) == 4

    def _dune(self):
        return TestBoundaryTiesCountHalf()._layers()

    @staticmethod
    def _lobe_of(lobes, cell):
        return next(k for k, (_p, _m, first, last) in enumerate(lobes)
                    if first <= cell < last)

    def test_every_lobe_of_every_date_is_started_in_by_some_chain(self):
        """
        What a quantile start does not do: it lands in a lobe in proportion to
        its mass. On this section, with sterile layers between dated ones, five
        of nine dates with several lobes had a lobe holding a third of their
        likelihood that no chain started in. Under the stratigraphic order, as
        a start has to be, each lobe of each such date is now started in.
        """
        measurements, tree = self._dune()
        model = gibbs.Model(measurements, tree)
        quantiles = gibbs.start_quantiles(4, 42)
        states = [model.initial_state(q, None if c == 0 else (c - 1, 3, 'position'))
                  for c, q in enumerate(quantiles)]
        several = [d for d in model.date_ids if model.lobes(d)]
        assert len(several) >= 5
        for date_id in several:
            lobes = model.lobes(date_id)
            started = {self._lobe_of(lobes, s.index[date_id]) for s in states}
            assert started == set(range(len(lobes))), date_id

    def test_a_lobe_start_is_feasible_and_chain_zero_is_unchanged(self):
        measurements, tree = self._dune()
        model = gibbs.Model(measurements, tree)
        modes = model.initial_state()
        assert modes.index == model.initial_state(None, None).index
        for rank in range(3):
            state = model.initial_state(0.5, (rank, 3, 'position'))
            assert np.isfinite(model.log_posterior(state))
            assert state.index != modes.index

    def test_the_default_start_is_the_shared_quantile_alone(self):
        """Lobe-aware starts are an option, not the default: the study in
        tests/manual/start_study.py found no gain in detection or accuracy."""
        assert gibbs.DEFAULT_START_STRATEGY == 'quantile'
        starts = gibbs.chain_starts(4, 42)
        assert [s['start_lobe'] for s in starts] == [None] * 4
        assert [s['start_quantile'] for s in starts] == gibbs.start_quantiles(4, 42)
        for strategy in gibbs.START_STRATEGIES[1:]:
            lobes = gibbs.chain_starts(4, 42, strategy)
            assert lobes[0]['start_lobe'] is None
            assert lobes[2]['start_lobe'] == (1, 3, strategy, 42)
        with pytest.raises(ValueError):
            gibbs.chain_starts(4, 42, 'nonsense')

    def test_a_date_with_one_lobe_still_starts_by_quantile(self):
        # 9000 BP on IntCal20 calibrates to one stretch of curve.
        phase = _element('phase', 'Only')
        tree = _element('sequence', 'Site', [
            _element('boundary', 'Start'), phase, _element('boundary', 'End')])
        date = _date(phase['id'], 9000)
        date.update(curve_id='intcal20', error_1sigma=15)
        model = gibbs.Model([date], tree)
        assert model.lobes(date['id']) == []
        for rank in range(3):
            assert (model.initial_state(0.9, (rank, 3, 'position')).index[date['id']]
                    == model.initial_state(0.9).index[date['id']])


class TestBoundaryTiesCountHalf:
    """
    Two neighbouring Boundaries may share a grid cell, at half weight, as in
    the exact solver (exact_chain._boundary_pair_term).

    The span correction divides by the ways n - 2 interior Boundaries fit
    across a span, counted with each tie at a half
    (exact_chain.log_interior_placements), so the ties add nothing it does
    not divide out. Counted whole against a count of strictly separated
    Boundaries, they once put a whole multi-area site model into a late
    calibration lobe holding under 1% of the posterior, with four chains
    agreeing at R-hat 1.001; forbidding them fixed that at the price of an
    error of first order in the grid step.
    """

    def _layers(self):
        # Shaped like a real dune section: dated layers with sterile ones between,
        # every date under a General outlier model. Synthetic ages.
        layers = [[615, 630, 705], [], [690], [], [625, 645], [],
                  [550, 565], [595, 590, 620], [545]]
        children, measurements = [_element('boundary', 'Start')], []
        for i, ages in enumerate(layers):
            phase = _element('phase', f'Layer {i}')
            children.append(phase)
            for age in ages:
                date = _date(phase['id'], age)
                date.update(error_1sigma=18, outlier_model='general', outlier_prior=0.05)
                measurements.append(date)
            children.append(_element('boundary', f'Top {i}'))
        return measurements, _element('sequence', 'Dune', children)

    def test_a_boundary_between_two_empty_phases_may_meet_either_neighbour(self):
        # Start, dated, Mid-1, empty, Mid-2, empty, Mid-3, dated, End: Mid-2
        # bounds only empty phases, so moving it changes nothing but its ties.
        children, measurements = [_element('boundary', 'Start')], []
        for i, ages in enumerate([[640, 660], [], [], [600, 620]]):
            phase = _element('phase', f'Layer {i}')
            children.append(phase)
            measurements += [_date(phase['id'], age) for age in ages]
            children.append(_element('boundary', f'Top {i}'))
        tree = _element('sequence', 'Site', children)
        model = gibbs.Model(measurements, tree)
        older, middle, younger = (children[k]['id'] for k in (2, 4, 6))
        state = model.initial_state()
        # The outer two as far apart as their dated neighbours allow.
        state.index[older] = min(state.index[d] for d in model.group_dates[0][2])
        state.index[younger] = max(state.index[d] for d in model.group_dates[3][2])
        assert state.index[older] - state.index[younger] >= 2
        state.index[middle] = (state.index[older] + state.index[younger]) // 2
        assert model.allowed(middle, state) == (state.index[younger], state.index[older])
        apart = model.log_posterior(state)
        state.index[middle] = state.index[older]
        assert model.log_posterior(state) == pytest.approx(apart - np.log(2.0))
        state.index[middle] = state.index[younger]
        assert model.log_posterior(state) == pytest.approx(apart - np.log(2.0))

    def test_only_neighbouring_boundaries_are_tied(self):
        # A Sequence's Start is ordered against an inner Sequence's End as
        # well as its Start; only the Start is its neighbour.
        inner = _element('sequence', 'Area', [
            _element('boundary', 'Area start'), _element('phase', 'Work'),
            _element('boundary', 'Area end')])
        tree = _element('sequence', 'Site', [
            _element('boundary', 'Site start'), inner, _element('boundary', 'Site end')])
        model = gibbs.Model([_date(inner['children'][1]['id'], 600)], tree)
        site_start, site_end = tree['children'][0]['id'], tree['children'][2]['id']
        area_start, area_end = inner['children'][0]['id'], inner['children'][2]['id']
        boundary_ties = {pair for pair in model.tie_pairs
                         if set(pair) <= set(model.boundary_ids)}
        assert boundary_ties == {(area_start, site_start), (area_end, area_start),
                                 (site_end, area_end)}

    def test_it_agrees_with_the_exact_solver_across_empty_phases(self):
        """
        Exact Start 617.0. With coincident Boundaries counted whole the same
        runs gave 606.3 and 562.4 -- the second a whole lobe out -- and with
        them kept apart 619.5 and 621.7.

        Tempered, at the default. Untempered, these two runs put the Start 14.9
        years young, and five other pairs of seeds 8 to 42; tempered, all six
        pairs land within 3.5.
        """
        measurements, tree = self._layers()
        chain = ec.layout(tree, measurements)
        exact = ec.solve(measurements, tree, chain, interval_draws=500)
        grid = _exact_grid(exact)
        start = chain.boundary_ids[0]
        means = []
        for seed in (1, 2):
            model = gibbs.Model(measurements, tree, grid=grid)
            run = gibbs.run(model, sweeps=6000, max_sweeps=6000, seed=seed,
                            target_convergence=1.01)
            visits = run['histograms'][start]
            means.append(float((grid * visits).sum() / visits.sum()))
        assert np.mean(means) == pytest.approx(
            exact['boundaries'][start]['posterior']['posterior_mean'], abs=6.0)


class TestParallelStartsOnCoarseGrids:
    """
    The start of a model with parallel branches is feasible on a coarse grid.

    The walk keeps one cell per link of the LONGEST chain, so the outer pair
    can come out narrower than the n - 2 boundaries inside it. When the span
    correction counted only strictly separated boundaries, C(cells - 1, n - 2)
    was zero there and the start infeasible -- 'could not find a feasible
    starting state' on generated site models solved on cross_check's grid --
    and the start was widened to suit. Counting ties at a half, it is finite
    at every span (exact_chain.log_interior_placements).
    """

    def _site(self, areas=3):
        measurements, children = [], []
        for a in range(areas):
            first, second = _element('phase', f'A{a}P0'), _element('phase', f'A{a}P1')
            for phase, ages in ((first, (650, 660)), (second, (700, 710))):
                measurements += [_date(phase['id'], age + 10 * a) for age in ages]
            children.append(_element('sequence', f'Area {a}', [
                _element('boundary', f's{a}'), first, _element('boundary', f'm{a}'),
                second, _element('boundary', f'e{a}')]))
        tree = _element('sequence', 'Site', [
            _element('boundary', 'Start'), _element('phase', 'Activity', children),
            _element('boundary', 'End')])
        return measurements, tree

    def _coarse_grid(self, measurements, tree, step, pad):
        model = gibbs.Model(measurements, tree)
        modes = [model.grid[int(np.argmax(model.likelihood[d]))]
                 for d in model.date_ids]
        return np.arange(min(modes) - pad, max(modes) + pad + step / 2, step)

    @pytest.mark.parametrize('step,pad', [(5, 0), (10, 25)])
    def test_a_coarse_grid_still_gives_a_feasible_start(self, step, pad):
        measurements, tree = self._site()
        grid = self._coarse_grid(measurements, tree, step, pad)
        model = gibbs.Model(measurements, tree, grid=grid)
        (oldest, youngest, interior), = model.span_priors
        # The point of the test: fewer cells than boundaries is reachable.
        assert len(grid) > interior + 1
        for quantile in (None, 0.2, 0.8):
            state = model.initial_state(quantile)
            assert np.isfinite(model.log_posterior(state))

    def test_a_grid_with_no_room_for_the_boundaries_still_says_so(self):
        measurements, tree = self._site()
        grid = self._coarse_grid(measurements, tree, 25, 0)
        model = gibbs.Model(measurements, tree, grid=grid)
        with pytest.raises(ValueError):
            model.initial_state()


class TestEnclosureMoves:
    """
    A group in one of several parallel branches is offered its translation
    with the Boundary enclosing it on one side (Model._with_enclosure), since
    it cannot reach another calibration lobe while that Boundary holds it in.
    """

    def test_a_branch_inside_a_site_can_move_with_the_site_start_or_end(self):
        nen, dune = _element('phase', 'Nen'), _element('phase', 'Dune')
        start, end = _element('boundary', 'Site start'), _element('boundary', 'Site end')
        tree = _element('sequence', 'Site', [start, _element('phase', 'Activity', [
            _element('sequence', 'A', [_element('boundary', 'sA'), nen,
                                       _element('boundary', 'eA')]),
            _element('sequence', 'B', [_element('boundary', 'sB'), dune,
                                       _element('boundary', 'eB')]),
        ]), end])
        measurements = [_date(nen['id'], 650), _date(nen['id'], 660),
                        _date(dune['id'], 600), _date(dune['id'], 610)]
        model = gibbs.Model(measurements, tree)
        sets = [set(s) for s in model.move_sets()]
        nen_dates = {m['id'] for m in measurements[:2]}
        assert any(nen_dates <= s and start['id'] in s and end['id'] not in s for s in sets)
        assert any(nen_dates <= s and end['id'] in s and start['id'] not in s for s in sets)


class TestLobeProbe:
    """
    gibbs.probe_unvisited_lobes: a chain started in a calibration lobe the run
    left empty stays there when the sampler was trapped away from it, and
    leaves when the model rules it out.
    """

    def _petchey(self):
        # Petchey et al.'s Layers 11-4 with WkA-56765 left out and inbuilt ages
        # allowed (tests/test_inbuilt_age.py), where four chains can all sit in
        # the late solution while the exact answer gives the early one 0.08.
        from test_inbuilt_age import LAYERS, PUBLISHED
        children = [_element('boundary', 'Start 11')]
        layers = {}
        for layer in LAYERS:
            if layer not in ('10', '8', '6L'):
                layers[layer] = _element('phase', f'Layer {layer}')
                children.append(layers[layer])
            children.append(_element('boundary', f'After {layer}'))
        measurements = []
        for layer, code, cra, sigma, inbuilt in PUBLISHED:
            if code == 'WkA-56765':
                continue
            date = _date(layers[layer]['id'], cra, name=code)
            date.update(error_1sigma=sigma, curve_id='shcal20', inbuilt_years=inbuilt or None)
            measurements.append(date)
        return measurements, _element('sequence', 'SM/C:Dune', children)

    def test_a_run_trapped_in_the_late_solution_is_reported(self):
        measurements, tree = self._petchey()
        model = gibbs.Model(measurements, tree)
        # A run that only ever saw each date's youngest lobe: every other lobe
        # holding a tenth of a likelihood is left empty and probed.
        histograms = {}
        for param in model.params:
            row = np.zeros(model.M)
            lobes = model.lobes(param) if param in model.likelihood else []
            cell = (lobes[0][0] if lobes else int(np.argmax(model.likelihood[param]))
                    if param in model.likelihood else model.M // 2)
            row[cell] = 1.0
            histograms[param] = row
        # Untempered, as the run that produced such histograms would have been.
        record = gibbs.probe_unvisited_lobes(model, histograms, seed=3, temperatures=1)
        names = {row['name'] for row in record['not_reached']}
        assert {'WkA-43681', 'WkA-43682'} <= names, record

    def test_lobes_the_model_rules_out_are_left(self):
        """A run at the exact answer leaves only lobes the model rules out
        empty, and the probe reports none of them."""
        measurements, tree = TestBoundaryTiesCountHalf()._layers()
        exact = ec.solve(measurements, tree, ec.layout(tree, measurements), num_samples=50)
        model = gibbs.Model(measurements, tree)
        histograms = {}
        for param in model.params:
            source = (exact['measurements'].get(param) or
                      (exact['boundaries'].get(param) or {}).get('posterior'))
            points = np.array(source['probability_distribution'], float)
            histograms[param] = np.interp(model.grid, points[:, 0], points[:, 1], left=0.0, right=0.0)
        record = gibbs.probe_unvisited_lobes(model, histograms, seed=3)
        assert record['probed'] > 0
        assert record['not_reached'] == [], record


class TestParallelTempering:
    """
    Untempered chains on Petchey et al.'s dune model of Layers 11-4 each stayed
    in whichever calibration range of the Start they began in, so the pooled
    P(Start before AD 1360) depended on where the chains started. Tempered
    copies cross easily and swap what they find into the recorded chain.
    """

    def test_tempered_chains_cross_between_the_starts_two_ranges(self):
        measurements, tree = TestLobeProbe()._petchey()
        model = gibbs.Model(measurements, tree)
        start = model.boundary_ids[0]
        cut = int(np.searchsorted(model.grid, 590.0))            # AD 1360
        crossings = {}
        for temperatures in (1, 3):
            chain = gibbs._Chain(model, 6000, 43, None, 6000, 0, temperatures=temperatures)
            early = []
            for _ in range(6000):
                chain.sweep()
                early.append(chain.state.index[start] >= cut)
            crossings[temperatures] = int(np.abs(np.diff(np.array(early, int))).sum())
        assert crossings[1] < 20, crossings
        assert crossings[3] > 100, crossings

    def test_it_is_on_by_default_and_reported(self):
        assert gibbs.DEFAULT_TEMPERATURES == 3
        dates = [_date('p', 3000), _date('p', 3040)]
        tree = _element('sequence', 'S', [_element('boundary', 'a'),
                                          {**_element('phase', 'P'), 'id': 'p'},
                                          _element('boundary', 'b')])
        result = gibbs.solve(dates, tree, sweeps=2000, max_sweeps=2000, num_samples=50)
        assert result['temperatures'] == 3
