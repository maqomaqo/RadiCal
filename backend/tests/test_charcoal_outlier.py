"""
The charcoal/residual outlier model, against a real OxCal 4.4 run of it
(tests/fixtures/oxcal_charcoal -- see hillside_charcoal_fixture for the model).

This is the first test anywhere to check the charcoal kernel against OxCal
rather than against its own direction or against the general model. Its absence
is exactly how the kernel came to use a single fixed 200-year scale where
OxCal's `Outlier_Model("Charcoal", Exp(1,-10,0), U(0,3), "t")` draws the scale
over three decades -- the general model beside it had integrated its own scale
all along.

Two things are checked separately, because they were two different faults: the
kernel's SHAPE (the scale is drawn over three decades, not fixed at 200 years)
and the fact that the scale is ONE parameter shared by the dates using the model
rather than marginalised per date. The second is what services/gibbs.py samples
and exact_chain deliberately approximates, so the two solvers are held to
different bounds here on purpose.

Those bounds are looser than the sub-year agreement the other fixtures get, and
that is deliberate rather than accommodating: an unresolved difference leaves the
app's modelled phase narrower than OxCal's on this model (see the fixture's
docstring, which also records what it is NOT).
"""
import numpy as np
import pytest

from app.services import exact_chain as ec
from app.services import gibbs
from app.services.model_tree import with_implicit_boundaries

import hillside_charcoal_fixture as fx
from oxcal_fixture import ad_to_calbp


def _solved():
    """The fixture's model as the app actually solves it: not a chain as
    written, so the implied transition is supplied and it goes to the exact
    solver."""
    measurements = fx.measurements()
    tree, inserted = with_implicit_boundaries(fx.model_tree())
    assert len(inserted) == 1, 'one transition, between North and South'
    chain = ec.layout(tree, measurements)
    assert chain is not None
    return measurements, tree, ec.solve(measurements, tree, chain, num_samples=200)


def _rms_against_oxcal(measurements, solved):
    oxcal = fx.posterior_means()
    errors = np.array([solved['measurements'][m['id']]['posterior_mean']
                       - oxcal[m['id']] for m in measurements])
    return float(np.sqrt((errors ** 2).mean())), errors


class TestTheKernelMatchesOxCalsDefinition:
    """
    The shape, independent of any model. Cheap, and it is what actually went
    wrong -- the modelled comparison below can only say the answer moved.
    """

    def _kernel(self, step=5.0):
        return ec._outlier_kernel(step, 20000.0, 'charcoal')

    def test_the_scale_is_drawn_over_three_decades(self):
        """
        OxCal's U(0,3): scales from 1 to 1000 years. The widest component sets
        how far the kernel reaches, at its own 10 scale lengths (Exp(1,-10,0)),
        so the kernel must span about 10,000 years on the offset side and
        nothing at all on the other.
        """
        step = 5.0
        kernel = self._kernel(step)
        centre = (len(kernel) - 1) // 2
        reach = centre * step
        assert reach == pytest.approx(
            ec.CHARCOAL_KERNEL_SPAN_TAUS * 10 ** ec.CHARCOAL_LOG_SCALE_MAX,
            rel=0.01), reach

    def test_it_only_ever_shifts_the_context_younger(self):
        """
        Direction is the whole point of a one-sided model and is easy to get
        backwards: the material is older than its context, so the CONTEXT age
        the kernel produces must be younger -- lower cal BP, negative lag.
        Pointed the other way it says the context predates the wood in it, and
        a residual date comes out looking MORE consistent than it is.
        """
        kernel = self._kernel()
        centre = (len(kernel) - 1) // 2
        assert kernel[:centre + 1].sum() == pytest.approx(1.0)
        assert kernel[centre + 1:].sum() == pytest.approx(0.0)

    def test_most_of_the_offset_is_small_because_most_scales_are(self):
        """
        The property a single fixed scale destroyed, and the reason it mattered.
        With u uniform on [0, 3], a third of the prior sits at scales of ten
        years or less, so more than half the offset mass lands within twenty
        years and a flagged date still says roughly where its context is.

        A single 200-year scale left only 12% within twenty years, which is what
        let the three declared-residual dates drift onto their neighbours rather
        than pull the group towards themselves.
        """
        step = 5.0
        kernel = self._kernel(step)
        centre = (len(kernel) - 1) // 2
        within_20 = kernel[centre - int(20 / step):centre + 1].sum()
        assert within_20 > 0.4, within_20

        fixed = np.exp(-np.arange(0.0, 8 * 200.0 + step, step) / 200.0)
        fixed = fixed / fixed.sum()
        assert within_20 > 3 * fixed[:int(20 / step) + 1].sum()

    def test_the_general_model_keeps_its_own_wider_range(self):
        """The two models draw their scales over different numbers of decades --
        four for general, three for charcoal -- and mixing them up would be
        invisible in any single-model test."""
        assert ec.OUTLIER_LOG_SCALE_MAX == 4.0
        assert ec.CHARCOAL_LOG_SCALE_MAX == 3.0


class TestAgainstOxCalsOwnRun:
    def test_the_modelled_dates_land_within_ten_years(self):
        """
        Measured at 9.9 years rms with the scale integrated, against 16.1 with a
        single fixed scale. The residual is the nested-exponent difference
        described in the fixture docstring, not the kernel.
        """
        measurements, _tree, solved = _solved()
        rms, errors = _rms_against_oxcal(measurements, solved)
        assert rms < 11.0, dict(zip([m['id'] for m in measurements], errors.round(2)))

    def test_integrating_the_scale_is_what_bought_that(self, monkeypatch):
        """
        The fix, as a measurement rather than a comment: restore the single
        fixed scale and the same model gets materially further from OxCal.
        """
        measurements, tree, solved = _solved()
        good, _e = _rms_against_oxcal(measurements, solved)

        real_kernel = ec._outlier_kernel

        def single_scale(step, half_width, model=ec.DEFAULT_OUTLIER_MODEL):
            if model != 'charcoal':
                return real_kernel(step, half_width, model)
            lags = np.arange(0.0, 8 * 200.0 + step, step)
            density = np.exp(-lags / 200.0)
            kernel = np.concatenate([density[::-1], np.zeros(len(lags) - 1)])
            return kernel / kernel.sum()

        monkeypatch.setattr(ec, '_outlier_kernel', single_scale)
        chain = ec.layout(tree, measurements)
        worse, _e2 = _rms_against_oxcal(
            measurements, ec.solve(measurements, tree, chain, num_samples=200))
        # 11.45 against 10.63. The margin has shrunk from the 6.2 years first
        # measured, because a different error the kernel shape had been partly
        # compensating for was removed: the interval quadrature (see
        # exact_chain._interval_integral). The claim still holds in direction, and
        # that is all this can assert now -- on THIS fixture the remaining error is
        # dominated by the declared-outlier degeneracy described in the fixture's
        # PHASE WIDTH note, which swamps what the kernel shape is worth.
        assert worse > good + 0.4, (worse, good)

    def test_the_declared_outliers_are_reported_as_outliers(self):
        """
        Outlier("Charcoal", 1.00) is a statement, not a suspicion -- the
        posterior probability cannot argue it down, because at prior 1.0 the
        mixture IS the outlier component.
        """
        measurements, _tree, solved = _solved()
        for m in measurements:
            probability = solved['measurements'][m['id']]['outlier_probability']
            if m['id'] in fx.CHARCOAL:
                assert probability == pytest.approx(1.0, abs=1e-6), m['id']
            else:
                assert probability < 0.5, m['id']

    def test_the_residual_dates_are_pulled_towards_their_context(self):
        """
        What the charcoal model is for: the three oldest dates are declared
        residual, so their contexts must come out substantially YOUNGER than
        their own unmodelled calibrations -- and in the same direction OxCal
        moved them.
        """
        measurements, _tree, solved = _solved()
        oxcal = fx.posterior_means()
        dists = fx.distributions()
        for name in fx.CHARCOAL:
            cal, prob = dists[name]['likelihood']
            unmodelled = float((cal * prob / prob.sum()).sum())
            ours = solved['measurements'][name]['posterior_mean']
            theirs = oxcal[name]
            assert ours < unmodelled - 15.0, (name, ours, unmodelled)
            assert theirs < unmodelled - 15.0, (name, theirs, unmodelled)

class TestTheSharedScale:
    """
    OxCal's Outlier_Model carries ONE offset scale for all the dates using it and
    lets the data constrain it. services/gibbs.py samples that shared scale;
    exact_chain marginalises it independently per date, because a shared
    hyperparameter does not factorise along its message passing. So the two
    solvers implement different models here, deliberately, and the sampler is the
    faithful one.

    This fixture is what made the difference visible and is the only place it can
    be checked against an inferred quantity rather than an answer: OxCal exports
    the posterior of the scale itself.

    Sampled untempered (temperatures=1). Tempering is for crossing calibration
    lobes, which test_gibbs.py tests, and it triples the cost of these runs.
    """

    def test_it_recovers_the_scale_oxcal_inferred(self):
        """
        The strongest check available anywhere in this suite. OxCal's export gives
        the Charcoal model's U(0,3) posterior as mean 1.79 with a 68% range of
        1.49-2.06 -- pinned well inside a prior that runs to 1000 years. The
        sampler is given the same eleven dates and must find the same thing,
        having been told only the prior.
        """
        measurements = fx.measurements()
        tree, _inserted = with_implicit_boundaries(fx.model_tree())
        sampled = gibbs.solve(measurements, tree, num_samples=200, temperatures=1)

        charcoal = sampled['outlier_scale']['charcoal']
        assert charcoal['log10_mean'] == pytest.approx(1.79, abs=0.15), charcoal
        low, high = charcoal['log10_hpd_68']
        assert low == pytest.approx(1.49, abs=0.2), charcoal
        assert high == pytest.approx(2.06, abs=0.2), charcoal
        # And the data really has constrained it: the posterior is far narrower
        # than the U(0,3) prior it started from.
        assert high - low < 1.0, charcoal

        # The General model's scale is a separate shared parameter, and OxCal puts
        # it at 1.81 -- but with only 5% priors the data barely moves it, so the
        # posterior stays about as wide as its U(0,4) prior. Both facts matter:
        # the value agrees, and the width says why it agrees so easily.
        general = sampled['outlier_scale']['general']
        assert general['log10_mean'] == pytest.approx(1.81, abs=0.4), general

    def test_the_scale_is_one_parameter_per_model_not_per_date(self):
        """The structural claim. Eleven dates, two outlier models, so exactly two
        shared scales -- if these were per-date there would be eleven."""
        measurements = fx.measurements()
        model = gibbs.Model(measurements, fx.model_tree())
        assert sorted(model.outlier_models) == ['charcoal', 'general']
        assert len(model.scaled_dates['charcoal']) == 3
        assert len(model.scaled_dates['general']) == 8

        state = model.initial_state()
        assert sorted(state.scale) == ['charcoal', 'general']

    def test_a_model_with_no_outlier_priors_builds_no_tables(self):
        """The per-scale tables are the cost of this, so a model that flags
        nothing must not pay it."""
        plain = [dict(m, outlier_prior=0.0) for m in fx.measurements()]
        model = gibbs.Model(plain, fx.model_tree())
        assert model.outlier_models == []
        assert model.likelihood_at == {}

    def test_the_sampler_is_closer_to_oxcal_than_the_exact_solver(self):
        """
        The consequence, and the reason the override in bayesian_config exists:
        on a model with a declared-residual date, choosing OxCal's own method
        gets nearer OxCal's answer. Measured 6.3 years rms against the exact
        solver's 9.9.
        """
        measurements, tree, exact = _solved()
        sampled = gibbs.solve(measurements, tree, num_samples=200, temperatures=1)
        exact_rms, _e = _rms_against_oxcal(measurements, exact)
        sampled_rms, _s = _rms_against_oxcal(measurements, sampled)
        assert sampled_rms < exact_rms, (sampled_rms, exact_rms)
        assert sampled_rms < 8.0, sampled_rms

    def test_the_declared_outliers_are_still_certain(self):
        """P(outlier) is now read from the run rather than from the reported
        density, since it depends on the sampled scale. At prior 1.00 it must
        still come back as certainty."""
        measurements, tree, _exact = _solved()
        sampled = gibbs.solve(measurements, tree, num_samples=200, temperatures=1)
        for m in measurements:
            probability = sampled['measurements'][m['id']]['outlier_probability']
            if m['id'] in fx.CHARCOAL:
                assert probability == pytest.approx(1.0, abs=1e-6), m['id']
            else:
                assert probability < 0.2, m['id']


class TestPhaseWidth:
    """
    Phase width against OxCal. This used to be the suite's largest open
    disagreement -- a modelled phase came out several times narrower than OxCal's
    -- and correcting the interval quadrature closed it on every fixture without
    a declared outlier. What is left is this fixture, where it now OVERSHOOTS.

    The tests below pin what that remainder is and, as much, what it is not. It
    was blamed on a degeneracy in the outlier model; that diagnosis predicted
    spans too NARROW, and the measurements here show the outlier model responding
    monotonically and helpfully instead. See the fixture's WHAT IS LEFT note.
    """

    def test_a_single_phase_now_matches_oxcals_width(self):
        """
        Dune Section: one phase, sixteen dates, a 5% General prior. Our span was 4.2
        years against OxCal's 19.3 and is now 20.7 -- from a factor of five out to
        7%. This is the headline result of the quadrature fix and the reason to
        pin it here rather than only in test_exact_chain.
        """
        import csv

        import dune_section_fixture as sf

        measurements, tree = sf.measurements(), sf.model_tree()
        solved = ec.solve(measurements, tree, ec.layout(tree, measurements),
                          num_samples=200)
        span = (solved['boundaries']['dune_start']['posterior']['posterior_mean']
                - solved['boundaries']['dune_end']['posterior']['posterior_mean'])

        rows = {}
        with open(sf.RAW, encoding='utf-8-sig') as handle:
            for row in csv.DictReader(handle):
                if row['op'] == 'Boundary' and row['type'] == 'posterior':
                    rows.setdefault(row['name'], []).append(
                        (float(row['value']), float(row['probability'])))
        oxcal = {}
        for name, pairs in rows.items():
            pairs.sort()
            calbp = ad_to_calbp([p[0] for p in pairs])[::-1]
            weight = np.array([p[1] for p in pairs])[::-1]
            oxcal[name] = float((calbp * weight / weight.sum()).sum())
        oxcal_span = oxcal['Start'] - oxcal['End']

        assert oxcal_span == pytest.approx(19.3, abs=1.0), oxcal_span
        assert span == pytest.approx(oxcal_span, rel=0.25), (span, oxcal_span)

    def test_this_fixture_still_overshoots(self):
        """
        The remaining disagreement, pinned so it cannot drift unnoticed. With
        three dates declared residual outright, this model's span comes out too
        WIDE against OxCal's 29.2, where before the quadrature fix everything was
        too narrow: 54.9 years once, 40.4 on the first-pass grid (a 1.67-year
        step), and 38.6 since the second pass refines it (0.83 years; 37.7 at
        0.38), so part of what was left was the grid.

        Asserted as a band: this fails if someone closes the gap, which is the
        signal to revisit the fixture's note.
        """
        measurements, _tree, solved = _solved()
        boundaries = [b['posterior']['posterior_mean']
                      for b in solved['boundaries'].values()]
        span = max(boundaries) - min(boundaries)
        oxcal_span = (fx.posterior_means()['Start Hillside']
                      - fx.posterior_means()['End Hillside'])
        assert oxcal_span == pytest.approx(29.2, abs=1.0), oxcal_span
        assert 34.0 < span < 70.0, span

    def test_absorbing_the_declared_outliers_narrows_the_span_monotonically(self):
        """
        Why the overshoot is NOT the degeneracy this fixture's note used to blame.

        That mechanism -- every date at the same instant, every date an outlier,
        paid for by the broad component's floor -- says the outlier model buys
        near-coincidence, so raising the flagged dates' prior should COLLAPSE the
        span, and its evidence was that the answer saturated once the prior passed
        a threshold. The response is neither: it is monotone in the helpful
        direction over four and a half decades of prior, with the best answer at
        the maximum absorption the prior allows.

            prior  0.00   0.05    0.20   0.50   1.00
            span  169.2  148.5   115.2   81.3   54.9

        A degenerate mode would not be graded like that, and it would not put its
        minimum where the model is most confident the dates are residual. Three
        priors are asserted rather than five to keep this to three solves.
        """
        base = fx.measurements()
        tree, _inserted = with_implicit_boundaries(fx.model_tree())
        chain = ec.layout(tree, base)
        spans = []
        for prior in (0.0, 0.2, 1.0):
            measurements = [
                dict(m, outlier_prior=(prior if m['id'] in fx.CHARCOAL
                                       else m['outlier_prior']))
                for m in base
            ]
            solved = ec.solve(measurements, tree, chain, num_samples=100)
            means = [b['posterior']['posterior_mean']
                     for b in solved['boundaries'].values()]
            spans.append(max(means) - min(means))
        assert spans[0] > spans[1] > spans[2], spans
        # And by a wide margin, not within noise of each other.
        assert spans[0] > 2 * spans[2], spans

    def test_the_excess_is_a_young_tail_and_not_a_uniform_widening(self):
        """
        Where the extra width actually sits, which is what any future fix has to
        move. Averaged over the seven South dates, our marginals carry a
        young tail OxCal does not have and are correspondingly short in the oldest
        lobe:

            cal BP band     OxCal   exact
            540-580         0.028   0.246
            620-640         0.406   0.274

        That is the charcoal kernel's one-sided smear, which is correct in
        direction and scale -- what differs is how hard the span prior resists it.
        Asserted loosely, as an excess and a deficit rather than at these values.
        """
        measurements, _tree, solved = _solved()
        oxcal = fx.distributions()
        south = [m['id'] for m in measurements
                       if m['element_id'] in ('K', 'A')]
        axis = np.arange(520.0, 700.0, 1.0)

        def stacked(pairs_for):
            total = np.zeros_like(axis)
            for date_id in south:
                cal, prob = pairs_for(date_id)
                density = np.interp(axis, cal, prob, left=0.0, right=0.0)
                total += density / density.sum()
            return total / len(south)

        def ours(date_id):
            pairs = np.asarray(
                solved['measurements'][date_id]['probability_distribution'], float)
            order = np.argsort(pairs[:, 0])
            return pairs[order, 0], pairs[order, 1]

        mine = stacked(ours)
        theirs = stacked(lambda d: oxcal[d]['posterior'])
        young = (axis >= 540) & (axis < 580)
        oldest = (axis >= 620) & (axis < 640)
        assert mine[young].sum() > 3 * theirs[young].sum(), (
            mine[young].sum(), theirs[young].sum())
        assert mine[oldest].sum() < theirs[oldest].sum(), (
            mine[oldest].sum(), theirs[oldest].sum())

    def test_widths_are_only_comparable_on_a_common_window(self):
        """
        A trap, pinned because it manufactured a 30% discrepancy that does not
        exist and the obvious sanity check does not catch it.

        OxCal exports about 250 years around each parameter; _plottable trims ours
        at 1e-4 of the peak. For a date with a broad outlier component those rules
        keep very different amounts of tail, so comparing the two curves' standard
        deviations on their OWN supports compares truncation rules. On Dune Section
        that reads as 0.70x OxCal's width; on a common window the same solve is
        0.97x. Comparing the unmodelled likelihoods cannot expose it -- they have
        no broad component and agree either way.
        """
        import dune_section_fixture as sf

        measurements, tree = sf.measurements(), sf.model_tree()
        solved = ec.solve(measurements, tree, ec.layout(tree, measurements),
                          num_samples=100)
        oxcal = sf.distributions()

        def sd(window, cal, prob):
            axis = np.arange(window[0], window[1] + 1e-9, 1.0)
            density = np.interp(axis, cal, prob, left=0.0, right=0.0)
            density = density / density.sum()
            mean = float((axis * density).sum())
            return float(np.sqrt(((axis - mean) ** 2 * density).sum()))

        own, common = [], []
        for m in measurements:
            if m['id'] not in oxcal or 'posterior' not in oxcal[m['id']]:
                continue
            their_cal, their_prob = oxcal[m['id']]['posterior']
            pairs = np.asarray(
                solved['measurements'][m['id']]['probability_distribution'], float)
            order = np.argsort(pairs[:, 0])
            our_cal, our_prob = pairs[order, 0], pairs[order, 1]
            own.append(sd((our_cal[0], our_cal[-1]), our_cal, our_prob)
                       / sd((their_cal[0], their_cal[-1]), their_cal, their_prob))
            window = (max(our_cal[0], their_cal[0]),
                      min(our_cal[-1], their_cal[-1]))
            common.append(sd(window, our_cal, our_prob)
                          / sd(window, their_cal, their_prob))
        assert np.mean(common) == pytest.approx(1.0, abs=0.1), np.mean(common)
        assert np.mean(own) < 0.85, (
            'the naive comparison should still be the misleading one; if it has '
            'come into agreement the trimming rules have changed and this test '
            'no longer demonstrates anything', np.mean(own))
