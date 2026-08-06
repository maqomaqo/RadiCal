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


@requires_pymc
class TestSpanTermsArePresent:
    """
    Every bounded group must actually contribute its uniform-scatter term to
    the model. These check the emitted pm.Potential names rather than a
    posterior, so they pin down the maths without paying for an MCMC run.

    The gap they exist for: the one-marginalized-boundary branch of
    build_model was guarded by `num_dates >= 2`, because the closed form it
    used divides by (K - 1) and so has no K = 1 case. The integral itself is
    perfectly well defined at K = 1 -- it is just a logarithm instead of a
    power -- so the guard silently dropped the prior for any outermost group
    holding exactly one date. That is the ordinary shape of the first and
    last phase of a stratigraphic sequence, and it left those dates free to
    drift outward (82 and 50 years of posterior-mean error against an exact
    grid reference on the same model window).
    """

    def _potentials(self, measurements, tree):
        bc = BayesianChronology(mcmc_samples=50, mcmc_tune=50, mcmc_chains=1)
        model, _ages = bc.build_model(measurements, tree)
        return bc, {v.name for v in model.potentials}

    def test_single_date_outermost_groups_get_a_span_term(self):
        measurements = [
            _measurement("sd_old", 3300, element_id="sd_q0"),
            _measurement("sd_mid", 3150, element_id="sd_q1"),
            _measurement("sd_new", 3000, element_id="sd_q2"),
        ]
        tree = _sequence("sd_root", "Site", [
            _boundary("sd_b0", "Start"), _phase("sd_q0", "Lowest"),
            _boundary("sd_b1", "T1"), _phase("sd_q1", "Middle"),
            _boundary("sd_b2", "T2"), _phase("sd_q2", "Highest"),
            _boundary("sd_b3", "End"),
        ])

        bc, potentials = self._potentials(measurements, tree)

        # The two outermost boundaries are integrated out, so their groups are
        # the one-sided case -- with one date each, exactly the dropped case.
        assert bc.model_plan.marginalized_boundaries == {"sd_b0", "sd_b3"}
        assert [len(g.date_ids) for g in bc.model_plan.span_groups] == [1, 1, 1]
        assert "phase_sd_b0_sd_b1" in potentials
        assert "phase_sd_b2_sd_b3" in potentials
        # ...and the fully interior group, which was never affected.
        assert "phase_sd_b1_sd_b2" in potentials

    def test_every_group_with_dates_contributes_a_term(self):
        """
        The general statement, over group sizes 1-4 in one model: a bounded
        group holding at least one date always contributes a span term. The
        one documented exception is a two-sided marginalized group, where the
        exponent 2-K really is zero at K = 2 (flat) and undefined at K = 1,
        and that group is interior here.
        """
        measurements = []
        children = [_boundary("eg_b0", "Start")]
        for group, size in enumerate((1, 2, 3, 4)):
            pid = f"eg_p{group}"
            children.append(_phase(pid, f"Phase {group}"))
            children.append(_boundary(f"eg_b{group + 1}", f"B{group + 1}"))
            for i in range(size):
                measurements.append(
                    _measurement(f"eg_{group}_{i}", 3400 - group * 120 + i * 10, element_id=pid)
                )

        bc, potentials = self._potentials(measurements, _sequence("eg_root", "Site", children))

        assert [len(g.date_ids) for g in bc.model_plan.span_groups] == [1, 2, 3, 4]
        for group in bc.model_plan.span_groups:
            name = f"phase_{group.older_boundary_id}_{group.younger_boundary_id}"
            assert name in potentials, f"{name} missing for a {len(group.date_ids)}-date group"

    def test_empty_group_contributes_nothing(self):
        """A boundary pair with no dates between it has no scatter to model."""
        measurements = [_measurement("ne_only", 3100, element_id="ne_p1")]
        tree = _sequence("ne_root", "Site", [
            _boundary("ne_b0", "Start"), _phase("ne_p1", "Dated"),
            _boundary("ne_b1", "T"), _phase("ne_p2", "Undated"),
            _boundary("ne_b2", "End"),
        ])

        bc, potentials = self._potentials(measurements, tree)

        assert "phase_ne_b1_ne_b2" not in potentials
        assert "phase_ne_b0_ne_b1" in potentials


@requires_pymc
class TestSpanTermCannotDiverge:
    """
    The uniform-scatter density (older - younger)^(-K) must stay bounded as a
    group's two sampled boundaries close on each other.

    In the correct model it is bounded because the group's K dates are hard-
    confined between the boundaries, so the volume they can occupy shrinks as
    eps^K and cancels the eps^(-K) exactly. build_model makes that
    containment a soft softplus penalty (a hard -inf being unusable under SMC
    tempering), which leaves the dates a fixed sliver of slack regardless of
    eps -- so the cancellation fails and the density diverges. The posterior
    is then improper, and samplers behave accordingly: NUTS collapsed three
    adjacent boundaries onto one value and reported r_hat 2.3 / ess 3, and
    SMC quietly degraded to r_hat 1.32 / ess 10 on a plain three-phase
    sequence.

    These tests read the model's own logp rather than sampling it, so they
    pin the property down in milliseconds.
    """

    @staticmethod
    def _point(model, values):
        """
        A model point built from calendar-year values, mapped through each
        variable's OWN PyMC transform rather than any assumption about it.
        """
        import pytensor
        import pytensor.tensor as pt

        point = dict(model.initial_point())
        for name, target in values.items():
            rv = model.named_vars[name]
            key = model.rvs_to_values[rv].name
            transform = model.rvs_to_transforms.get(rv)
            if transform is None:
                point[key] = np.array(float(target))
            else:
                forward = transform.forward(
                    pt.as_tensor_variable(float(target)), *rv.owner.inputs
                )
                point[key] = np.asarray(pytensor.function([], forward)())
        return point

    def _model(self):
        """Three phases of four dates, boundary between each: the smallest
        model with two ADJACENT sampled boundaries, hence a closable gap."""
        measurements = []
        children = [_boundary("cl_b0", "Start")]
        for group in range(3):
            pid = f"cl_p{group}"
            children.append(_phase(pid, f"Phase {group}"))
            children.append(_boundary(f"cl_b{group + 1}", f"B{group + 1}"))
            for i in range(4):
                measurements.append(
                    _measurement(f"cl_{group}_{i}", 3300 - group * 200 + i * 30,
                                 element_id=pid)
                )

        bc = BayesianChronology(mcmc_samples=50, mcmc_tune=50, mcmc_chains=1)
        model, _ages = bc.build_model(measurements, _sequence("cl_root", "Site", children))
        assert bc.model_plan.sampled_boundaries == ["cl_b1", "cl_b2"]
        return model, measurements

    def test_logp_does_not_grow_as_a_sampled_gap_closes(self):
        """
        With the middle phase's dates riding along at the collapse point --
        which is what makes the soft containment cost nothing -- shrinking the
        gap from 1 year to 1/10000 of a year must not buy any extra
        probability. Unfloored it buys 4 * log(1e4) = 37 log units, and
        without bound as the gap shrinks further.
        """
        model, _measurements = self._model()
        logp = model.compile_logp()
        centre = 3100.0

        def at(gap):
            values = {"boundary_cl_b1": centre + gap / 2.0,
                      "boundary_cl_b2": centre - gap / 2.0}
            values.update({f"age_cl_1_{i}": centre for i in range(4)})
            return float(logp(self._point(model, values)))

        wide, floored, collapsed = at(1.0), at(1e-2), at(1e-4)

        assert np.isfinite(collapsed)
        # Closing the gap past the floor gains nothing at all...
        assert collapsed <= wide + 0.5
        assert floored <= wide + 0.5
        # ...and the sliver of soft-constraint violation it costs means the
        # collapsed point is, if anything, slightly worse.
        assert collapsed <= floored + 1e-6

    def test_a_groups_own_dates_floor_its_span(self):
        """
        The floor is not an arbitrary epsilon: a group's boundaries enclose
        all of its dates, so the dates' own span is a real lower bound on the
        gap and is what the density is evaluated at. Two gaps both narrower
        than the dates they supposedly enclose must therefore score
        identically on the span term -- the only difference left between them
        is the containment penalty.
        """
        model, _measurements = self._model()
        term = next(v for v in model.potentials if v.name == "phase_cl_b1_cl_b2")
        # replace_rvs_by_values is the step that makes the term a function of
        # the point at all: compiled as-is it still refers to the random
        # variables and would draw fresh values on every call. Compiled over
        # the full set of value vars so the whole point dict is accepted.
        (graph,) = model.replace_rvs_by_values([term])
        value = model.compile_fn(graph, inputs=model.value_vars, point_fn=True,
                                 on_unused_input="ignore")
        # Dates spread over 90 years, so any gap below 90 is infeasible.
        spread = {f"age_cl_1_{i}": 3100.0 + i * 30.0 for i in range(4)}

        def at(gap):
            values = {"boundary_cl_b1": 3145.0 + gap / 2.0,
                      "boundary_cl_b2": 3145.0 - gap / 2.0}
            values.update(spread)
            return float(value(self._point(model, values)))

        assert at(1.0) == pytest.approx(at(50.0))
        # A feasible gap is scored on its own width, not the floor.
        assert at(400.0) < at(90.0)
