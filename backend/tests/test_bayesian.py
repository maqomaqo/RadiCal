"""
Tests for BayesianChronology's Boundary parameters (services/bayesian.py) --
the "Modelled Start"/"Modelled End" reported for a context/group with 2+
dated members. These are meant to behave like OxCal's Boundary command: a
genuine latent parameter constrained to lie outside every dated member's
own age, not simply that member's own posterior. See the long comment
above the Potential loop in build_model for the generative model.

Both tests below need a real MCMC run, so they're @requires_pymc-marked
like the rest of the stratigraphy-related test suite.
"""
import pytest

from conftest import requires_pymc


def _measurement(sample_id, age_bp, group_id=None, error_1sigma=25):
    return {
        "id": sample_id,
        "age_bp": age_bp,
        "error_1sigma": error_1sigma,
        "sample_name": f"Sample {sample_id}",
        "group_id": group_id,
    }


@requires_pymc
class TestBoundaryParameters:
    def test_boundary_extends_beyond_member_range(self):
        """
        The defining property of a Boundary (vs. just taking the oldest/
        youngest member's own posterior): boundary_start's posterior mean
        should be >= the older of the two members' own posterior mean, and
        boundary_end's should be <= the younger's -- not equal to it, since
        a Boundary's own Uniform prior lets it range past the sampled
        members while the members are only pulled toward it via a soft
        containment penalty.
        """
        from app.services.bayesian import BayesianChronology

        measurements = [
            _measurement("a", 3000, group_id="g1"),
            _measurement("b", 3200, group_id="g1"),
        ]
        groups = [{"id": "g1", "name": "Phase A", "tpq_age_bp": None, "taq_age_bp": None}]

        bc = BayesianChronology(mcmc_samples=200, mcmc_tune=200, mcmc_chains=2)
        model, ages = bc.build_model(measurements, [], groups)

        assert "boundary_start_g1" in model.named_vars
        assert "boundary_end_g1" in model.named_vars

        idata, ages_dict = bc.sample(model, ages)
        extracted = bc.extract_results(idata, ages_dict, measurements=measurements, groups=groups)

        ctx = extracted["contexts"]["g1"]
        member_means = [extracted["measurements"][mid]["posterior_mean"] for mid in ("a", "b")]

        assert ctx["start"]["posterior_mean"] >= max(member_means)
        assert ctx["end"]["posterior_mean"] <= min(member_means)

    def test_single_member_group_has_no_boundary(self):
        """A group with fewer than 2 dated members has no meaningful span,
        so build_model shouldn't create boundary variables for it."""
        from app.services.bayesian import BayesianChronology

        measurements = [_measurement("a", 3000, group_id="g1")]
        groups = [{"id": "g1", "name": "Phase A", "tpq_age_bp": None, "taq_age_bp": None}]

        bc = BayesianChronology(mcmc_samples=100, mcmc_tune=100, mcmc_chains=1)
        model, _ages = bc.build_model(measurements, [], groups)

        assert "boundary_start_g1" not in model.named_vars
        assert "boundary_end_g1" not in model.named_vars
