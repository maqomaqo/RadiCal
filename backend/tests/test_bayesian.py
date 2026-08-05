"""
Tests for BayesianChronology's Boundary parameters (services/bayesian.py) --
the "Modelled Start"/"Modelled End" reported for a context/group with 2+
dated members, which are meant to behave like OxCal's Boundary command:
separate parameters that may extend BEYOND the oldest/youngest dated member,
because a context's dates are modelled as a random uniform scatter between
its start and end rather than as the boundaries themselves.

The boundaries are analytically marginalized out of the MCMC and recovered
in closed form afterwards (see build_model/_sample_boundaries for why), so
the core property is testable without any MCMC at all -- TestBoundarySampler
below checks the recovered conditional directly against its analytic mean,
and only the end-to-end tests need pymc.

Regression note: an earlier version sampled the boundaries as PyMC variables
and SMC silently collapsed them onto the extreme member dates (E[a+b] = 2.0
against a true 150.9), which is the order-statistic behaviour Boundaries
exist to avoid. The test that was supposed to catch this only asserted
`start >= max(member means)`, which is satisfied just as well by a boundary
pinned 1.7 years out as by a correct one 79 years out. Hence the
quantitative assertions here.
"""
import numpy as np
import pytest

from app.services.bayesian import BayesianChronology

from conftest import requires_pymc


def _measurement(sample_id, age_bp, group_id=None, error_1sigma=25):
    return {
        "id": sample_id,
        "age_bp": age_bp,
        "error_1sigma": error_1sigma,
        "sample_name": f"Sample {sample_id}",
        "group_id": group_id,
    }


class TestBoundarySampler:
    """
    Pure-numpy tests of the closed-form boundary conditional
    p(a, b | t) ∝ (w + a + b)^(-K); no MCMC involved.
    """

    @pytest.mark.parametrize("num_members", [4, 5, 6, 8])
    def test_matches_analytic_mean(self, num_members):
        """
        With a window wide enough for truncation to be negligible, the
        expected total outward extension has the closed form
        E[a+b] = 2w/(K-3). This is the assertion that would have failed
        against the old SMC-sampled boundaries.
        """
        w = 160.0
        ages = np.linspace(3000.0, 3000.0 + w, num_members)
        draws = np.repeat(ages[:, None], 20000, axis=1)

        start, end = BayesianChronology._sample_boundaries(
            draws, window_lower=-1e6, window_upper=1e6, seed=1
        )
        a = start - ages.max()
        b = ages.min() - end

        expected = 2 * w / (num_members - 3)
        assert (a + b).mean() == pytest.approx(expected, rel=0.10)
        # Symmetric: neither end is systematically favoured.
        assert a.mean() == pytest.approx(b.mean(), rel=0.10)

    def test_boundaries_always_enclose_the_dates(self):
        w = 200.0
        ages = np.linspace(2000.0, 2000.0 + w, 5)
        draws = np.repeat(ages[:, None], 5000, axis=1)

        start, end = BayesianChronology._sample_boundaries(
            draws, window_lower=0.0, window_upper=8000.0, seed=3
        )
        assert np.all(start >= ages.max() - 1e-9)
        assert np.all(end <= ages.min() + 1e-9)

    def test_respects_window_truncation(self):
        """A tight window must clamp how far the boundaries can extend."""
        ages = np.linspace(3000.0, 3160.0, 5)
        draws = np.repeat(ages[:, None], 5000, axis=1)

        start, end = BayesianChronology._sample_boundaries(
            draws, window_lower=2990.0, window_upper=3170.0, seed=5
        )
        assert np.all(start <= 3170.0 + 1e-6)
        assert np.all(end >= 2990.0 - 1e-6)

    def test_more_dates_tighten_the_boundaries(self):
        """Extra dates in a context should pin its span down more tightly."""
        w = 160.0
        spread = []
        for num_members in (4, 8):
            ages = np.linspace(3000.0, 3000.0 + w, num_members)
            draws = np.repeat(ages[:, None], 20000, axis=1)
            start, end = BayesianChronology._sample_boundaries(
                draws, window_lower=-1e6, window_upper=1e6, seed=7
            )
            spread.append((start - ages.max()).mean())
        assert spread[1] < spread[0]


@requires_pymc
class TestBoundaryEndToEnd:
    def test_boundaries_extend_beyond_the_dated_range(self):
        measurements = [
            _measurement(f"s{i}", 3000 + i * 40, group_id="g1") for i in range(5)
        ]
        groups = [{"id": "g1", "name": "Phase A", "tpq_age_bp": None, "taq_age_bp": None}]

        bc = BayesianChronology(mcmc_samples=600, mcmc_tune=600, mcmc_chains=2)
        model, ages = bc.build_model(measurements, [], groups)

        # The boundaries are marginalized out, so they are NOT model variables.
        assert not any("boundary" in v.name for v in model.free_RVs)
        assert "g1" in bc.context_windows

        idata, ages_dict = bc.sample(model, ages)
        extracted = bc.extract_results(idata, ages_dict, measurements=measurements, groups=groups)
        ctx = extracted["contexts"]["g1"]

        member = np.stack(
            [np.asarray(extracted["measurements"][m["id"]]["posterior_samples"]) for m in measurements]
        )
        start = np.asarray(ctx["start"]["posterior_samples"])
        end = np.asarray(ctx["end"]["posterior_samples"])

        a = start - member.max(axis=0)
        b = member.min(axis=0) - end
        w = (member.max(axis=0) - member.min(axis=0)).mean()

        # Every draw must enclose that draw's dates...
        assert np.all(a >= -1e-6)
        assert np.all(b >= -1e-6)
        # ...and on average extend substantially past them (theory: a+b ~ w).
        # The old collapsed behaviour gave ~3 years here, so this both
        # asserts correctness and locks out that regression.
        assert (a + b).mean() == pytest.approx(w, rel=0.35)
        assert (a + b).mean() > 30

    def test_single_member_context_reports_no_boundaries(self):
        measurements = [_measurement("a", 3000, group_id="g1")]
        groups = [{"id": "g1", "name": "Phase A", "tpq_age_bp": None, "taq_age_bp": None}]

        bc = BayesianChronology(mcmc_samples=200, mcmc_tune=200, mcmc_chains=2)
        model, ages = bc.build_model(measurements, [], groups)
        idata, ages_dict = bc.sample(model, ages)
        extracted = bc.extract_results(idata, ages_dict, measurements=measurements, groups=groups)

        assert "g1" not in extracted["contexts"]
