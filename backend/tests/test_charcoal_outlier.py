"""
The charcoal/residual outlier model, against a real OxCal 4.4 run of it
(tests/fixtures/oxcal_charcoal -- see nenthorn_charcoal_fixture for the model).

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

import nenthorn_charcoal_fixture as fx


def _solved():
    """The fixture's model as the app actually solves it: not a chain as
    written, so the implied transition is supplied and it goes to the exact
    solver."""
    measurements = fx.measurements()
    tree, inserted = with_implicit_boundaries(fx.model_tree())
    assert len(inserted) == 1, 'one transition, between Quarry and Rockshelter'
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
        assert worse > good + 4.0, (worse, good)

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
        sampled = gibbs.solve(measurements, tree, num_samples=200)

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
        sampled = gibbs.solve(measurements, tree, num_samples=200)
        exact_rms, _e = _rms_against_oxcal(measurements, exact)
        sampled_rms, _s = _rms_against_oxcal(measurements, sampled)
        assert sampled_rms < exact_rms, (sampled_rms, exact_rms)
        assert sampled_rms < 8.0, sampled_rms

    def test_the_declared_outliers_are_still_certain(self):
        """P(outlier) is now read from the run rather than from the reported
        density, since it depends on the sampled scale. At prior 1.00 it must
        still come back as certainty."""
        measurements, tree, _exact = _solved()
        sampled = gibbs.solve(measurements, tree, num_samples=200)
        for m in measurements:
            probability = sampled['measurements'][m['id']]['outlier_probability']
            if m['id'] in fx.CHARCOAL:
                assert probability == pytest.approx(1.0, abs=1e-6), m['id']
            else:
                assert probability < 0.2, m['id']
