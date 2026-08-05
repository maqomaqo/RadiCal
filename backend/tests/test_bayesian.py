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


def _measurement(sample_id, age_bp, element_id=None, error_1sigma=25):
    return {
        "id": sample_id,
        "age_bp": age_bp,
        "error_1sigma": error_1sigma,
        "sample_name": f"Sample {sample_id}",
        "element_id": element_id,
    }


def _boundary(element_id, name):
    return {"id": element_id, "element_type": "boundary", "name": name, "children": []}


def _phase(element_id, name, **kwargs):
    return {"id": element_id, "element_type": "phase", "name": name, "children": [], **kwargs}


def _sequence(element_id, name, children):
    return {"id": element_id, "element_type": "sequence", "name": name, "children": children}


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
    """
    End-to-end behaviour of the model tree. A single Phase between two
    Boundaries is the degenerate case that must keep reproducing exactly what
    the previous per-context implementation did.
    """

    def test_boundaries_extend_beyond_the_dated_range(self):
        measurements = [
            _measurement(f"s{i}", 3000 + i * 40, element_id="p1") for i in range(5)
        ]
        tree = _sequence("root", "Site", [
            _boundary("b0", "Start"), _phase("p1", "Phase A"), _boundary("b1", "End"),
        ])

        bc = BayesianChronology(mcmc_samples=600, mcmc_tune=600, mcmc_chains=2)
        model, ages = bc.build_model(measurements, tree)

        # Both boundaries are outermost, so both are integrated out rather
        # than sampled -- exactly the previous implementation's behaviour.
        assert not any("boundary" in v.name for v in model.free_RVs)
        assert bc.model_plan.marginalized_boundaries == {"b0", "b1"}

        idata, ages_dict = bc.sample(model, ages)
        extracted = bc.extract_results(idata, ages_dict, measurements=measurements)

        member = np.stack(
            [np.asarray(extracted["measurements"][m["id"]]["posterior_samples"]) for m in measurements]
        )
        start = np.asarray(extracted["boundaries"]["b0"]["posterior"]["posterior_samples"])
        end = np.asarray(extracted["boundaries"]["b1"]["posterior"]["posterior_samples"])

        a = start - member.max(axis=0)
        b = member.min(axis=0) - end
        w = (member.max(axis=0) - member.min(axis=0)).mean()

        assert np.all(a >= -1e-6)
        assert np.all(b >= -1e-6)
        # Theory for K=5: E[a+b] = 2w/(K-3) = w.
        assert (a + b).mean() == pytest.approx(w, rel=0.35)
        assert (a + b).mean() > 30

    def test_shared_transition_between_two_phases(self):
        """
        A Boundary between two Phases in a Sequence is ONE shared parameter:
        it is sampled (being interior and therefore bounded), while the two
        outermost boundaries are integrated out.
        """
        measurements = (
            [_measurement(f"a{i}", 3300 + i * 30, element_id="p1") for i in range(4)]
            + [_measurement(f"c{i}", 3000 + i * 30, element_id="p2") for i in range(4)]
        )
        tree = _sequence("root", "Site", [
            _boundary("b0", "Start"), _phase("p1", "Layer 3"),
            _boundary("b1", "Transition"), _phase("p2", "Pit 12"),
            _boundary("b2", "End"),
        ])

        bc = BayesianChronology(mcmc_samples=800, mcmc_tune=800, mcmc_chains=2)
        model, ages = bc.build_model(measurements, tree)

        assert bc.model_plan.sampled_boundaries == ["b1"]
        assert bc.model_plan.marginalized_boundaries == {"b0", "b2"}
        assert sum("boundary" in v.name for v in model.free_RVs) == 1

        idata, ages_dict = bc.sample(model, ages)
        extracted = bc.extract_results(idata, ages_dict, measurements=measurements)

        start = extracted["boundaries"]["b0"]["posterior"]["posterior_mean"]
        transition = extracted["boundaries"]["b1"]["posterior"]["posterior_mean"]
        end = extracted["boundaries"]["b2"]["posterior"]["posterior_mean"]

        older = [extracted["measurements"][f"a{i}"]["posterior_mean"] for i in range(4)]
        younger = [extracted["measurements"][f"c{i}"]["posterior_mean"] for i in range(4)]

        # Start is older than everything, end younger than everything, and the
        # shared transition sits between the two phases.
        assert start > max(older)
        assert end < min(younger)
        assert min(younger) < transition < max(older)

    def test_phase_of_sequences_leaves_branches_unordered(self):
        """Nested groups: two parallel branches, each internally ordered but
        unordered with respect to each other."""
        measurements = (
            [_measurement("a1", 3200, element_id="pa1"), _measurement("a2", 3000, element_id="pa2")]
            + [_measurement("b1", 3200, element_id="pb1"), _measurement("b2", 3000, element_id="pb2")]
        )
        branch_a = _sequence("sa", "Trench A", [_phase("pa1", "A1"), _phase("pa2", "A2")])
        branch_b = _sequence("sb", "Trench B", [_phase("pb1", "B1"), _phase("pb2", "B2")])
        tree = _sequence("root", "Site", [
            {"id": "par", "element_type": "phase", "name": "Parallel", "children": [branch_a, branch_b]},
        ])

        bc = BayesianChronology(mcmc_samples=200, mcmc_tune=200, mcmc_chains=1)
        model, _ages = bc.build_model(measurements, tree)

        ordering = bc.model_plan.ordering
        flat = [
            ({t for _k, t in older}, {t for _k, t in younger})
            for older, younger in ordering
        ]
        # A1 before A2 and B1 before B2 are constrained...
        assert ({"a1"}, {"a2"}) in flat
        assert ({"b1"}, {"b2"}) in flat
        # ...but nothing orders a branch against the other.
        for older, younger in flat:
            assert not (older & {"a1", "a2"} and younger & {"b1", "b2"})
            assert not (older & {"b1", "b2"} and younger & {"a1", "a2"})
