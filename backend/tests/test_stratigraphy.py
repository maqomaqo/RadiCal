"""
Tests for the stratigraphy (groups, TPQ/TAQ, above/below/equal
relationships) feature added to POST /analysis/bayesian.

Split into three layers:
  - TestStratigraphyServiceUnit: pure-Python tests of
    app.services.stratigraphy's expand_relationships/find_cycle/
    effective_bounds, independent of pymc or the API.
  - TestBayesianRequestValidation: POST /api/analysis/bayesian with bad
    stratigraphy input, verifying it's rejected with a 422 *before* any
    MCMC run is attempted (so these pass even in environments -- like this
    one -- without a real pymc installed, since request validation always
    runs first).
  - TestStratigraphyPersistence: requires_pymc-marked full round trip
    (save a bayesian analysis with groups/relationships/TPQ-TAQ, then GET
    it back and confirm they were actually persisted) -- skipped unless
    pymc is installed, same as any other test that needs a real MCMC run.
"""
import pytest

from app.services.stratigraphy import expand_relationships, find_cycle, effective_bounds, group_members

from conftest import requires_pymc


class TestStratigraphyServiceUnit:
    def test_expand_simple_sample_relationship(self):
        # Law of superposition: "a above b" means a is younger, so b (the
        # lower one) is older -- edge is (b, a).
        measurements = [{"id": "a"}, {"id": "b"}]
        relationships = [{"from_sample_id": "a", "to_sample_id": "b", "relationship": "above", "from_type": "sample", "to_type": "sample"}]
        older_younger, equal = expand_relationships(measurements, [], relationships)
        assert older_younger == [("b", "a")]
        assert equal == []

    def test_below_is_normalized_to_older_younger(self):
        measurements = [{"id": "a"}, {"id": "b"}]
        relationships = [{"from_sample_id": "a", "to_sample_id": "b", "relationship": "below", "from_type": "sample", "to_type": "sample"}]
        older_younger, _ = expand_relationships(measurements, [], relationships)
        # "a below b" means a is older than b
        assert older_younger == [("a", "b")]

    def test_group_relationship_expands_to_all_pairs(self):
        measurements = [
            {"id": "a", "group_id": "g1"},
            {"id": "b", "group_id": "g1"},
            {"id": "c", "group_id": "g2"},
            {"id": "d", "group_id": "g2"},
        ]
        groups = [{"id": "g1"}, {"id": "g2"}]
        # "g1 above g2" means g1's members are younger, so g2's members
        # (the lower phase) are older.
        relationships = [{"from_sample_id": "g1", "to_sample_id": "g2", "relationship": "above", "from_type": "group", "to_type": "group"}]
        older_younger, _ = expand_relationships(measurements, groups, relationships)
        assert set(older_younger) == {("c", "a"), ("c", "b"), ("d", "a"), ("d", "b")}

    def test_equal_pairs_deduplicated(self):
        measurements = [{"id": "a"}, {"id": "b"}]
        relationships = [
            {"from_sample_id": "a", "to_sample_id": "b", "relationship": "equal", "from_type": "sample", "to_type": "sample"},
            {"from_sample_id": "b", "to_sample_id": "a", "relationship": "equal", "from_type": "sample", "to_type": "sample"},
        ]
        _, equal = expand_relationships(measurements, [], relationships)
        assert len(equal) == 1

    def test_find_cycle_detects_simple_cycle(self):
        edges = [("a", "b"), ("b", "c"), ("c", "a")]
        cycle = find_cycle(edges)
        assert cycle is not None
        assert set(cycle) == {"a", "b", "c"}

    def test_find_cycle_none_for_dag(self):
        edges = [("a", "b"), ("b", "c"), ("a", "c")]
        assert find_cycle(edges) is None

    def test_find_cycle_none_for_empty(self):
        assert find_cycle([]) is None

    def test_effective_bounds_tightest_wins(self):
        sample = {"tpq_age_bp": 500, "taq_age_bp": None}
        group = {"tpq_age_bp": 800, "taq_age_bp": 100}
        taq, tpq = effective_bounds(sample, group)
        assert taq == 100
        assert tpq == 500  # sample's tighter (smaller) max bound wins

    def test_group_members_maps_correctly(self):
        measurements = [{"id": "a", "group_id": "g1"}, {"id": "b", "group_id": None}, {"id": "c", "group_id": "g1"}]
        groups = [{"id": "g1"}]
        members = group_members(measurements, groups)
        assert set(members["g1"]) == {"a", "c"}


def _measurement(sample_id, age_bp=3000, **overrides):
    m = {
        "id": sample_id,
        "age_bp": age_bp,
        "error_1sigma": 30,
        "sample_name": f"Sample {sample_id}",
    }
    m.update(overrides)
    return m


class TestBayesianRequestValidation:
    def test_circular_relationship_rejected(self, client):
        response = client.post(
            "/api/analysis/bayesian",
            json={
                "name": "Cyclic",
                "measurements": [_measurement("a"), _measurement("b"), _measurement("c")],
                "relationships": [
                    {"from_sample_id": "a", "to_sample_id": "b", "relationship": "above"},
                    {"from_sample_id": "b", "to_sample_id": "c", "relationship": "above"},
                    {"from_sample_id": "c", "to_sample_id": "a", "relationship": "above"},
                ],
                "save": False,
            },
        )
        assert response.status_code == 422
        assert "circular" in response.text or "contradictory" in response.text

    def test_relationship_references_unknown_group_rejected(self, client):
        response = client.post(
            "/api/analysis/bayesian",
            json={
                "name": "Bad group ref",
                "measurements": [_measurement("a")],
                "groups": [],
                "relationships": [
                    {"from_sample_id": "does-not-exist", "to_sample_id": "a", "relationship": "above", "from_type": "group"},
                ],
                "save": False,
            },
        )
        assert response.status_code == 422

    def test_measurement_references_unknown_group_rejected(self, client):
        response = client.post(
            "/api/analysis/bayesian",
            json={
                "name": "Bad measurement group",
                "measurements": [_measurement("a", group_id="ghost-group")],
                "groups": [],
                "save": False,
            },
        )
        assert response.status_code == 422

    def test_sample_tpq_taq_contradiction_rejected(self, client):
        response = client.post(
            "/api/analysis/bayesian",
            json={
                "name": "Bad TPQ/TAQ",
                "measurements": [_measurement("a", tpq_age_bp=100, taq_age_bp=500)],
                "save": False,
            },
        )
        assert response.status_code == 422

    def test_group_tpq_taq_contradiction_rejected(self, client):
        response = client.post(
            "/api/analysis/bayesian",
            json={
                "name": "Bad group TPQ/TAQ",
                "measurements": [_measurement("a", group_id="g1")],
                "groups": [{"id": "g1", "name": "Phase 1", "tpq_age_bp": 100, "taq_age_bp": 500}],
                "save": False,
            },
        )
        assert response.status_code == 422

    def test_duplicate_measurement_and_group_id_rejected(self, client):
        response = client.post(
            "/api/analysis/bayesian",
            json={
                "name": "Colliding ids",
                "measurements": [_measurement("shared-id")],
                "groups": [{"id": "shared-id", "name": "Phase 1"}],
                "save": False,
            },
        )
        assert response.status_code == 422

    def test_valid_group_and_relationship_passes_validation(self, client):
        """A well-formed group + relationship request should get past
        validation (schema-level 422) even though the actual MCMC run
        can't complete in this environment without pymc -- it should fail
        later, if at all, for a different reason than a 422."""
        response = client.post(
            "/api/analysis/bayesian",
            json={
                "name": "Valid stratigraphy",
                "measurements": [
                    _measurement("a", group_id="g1"),
                    _measurement("b", group_id="g1"),
                    _measurement("c", age_bp=3500, group_id="g2"),
                ],
                "groups": [
                    {"id": "g1", "name": "Upper phase", "tpq_age_bp": None, "taq_age_bp": None},
                    {"id": "g2", "name": "Lower phase", "tpq_age_bp": None, "taq_age_bp": None},
                ],
                "relationships": [
                    {"from_sample_id": "g2", "to_sample_id": "g1", "relationship": "above", "from_type": "group", "to_type": "group"},
                ],
                "save": False,
            },
        )
        assert response.status_code != 422


@requires_pymc
class TestStratigraphyPersistence:
    def test_groups_and_relationships_round_trip(self, client):
        response = client.post(
            "/api/analysis/bayesian",
            json={
                "name": "Persisted stratigraphy",
                "measurements": [
                    _measurement("a", group_id="g1"),
                    _measurement("b", age_bp=3200, group_id="g1"),
                ],
                "groups": [{"id": "g1", "name": "Phase A", "tpq_age_bp": None, "taq_age_bp": None}],
                "relationships": [
                    {"from_sample_id": "a", "to_sample_id": "b", "relationship": "above"},
                ],
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        )
        assert response.status_code == 200
        analysis_id = response.json()["data"]["id"]

        get_response = client.get(f"/api/analysis/{analysis_id}")
        assert get_response.status_code == 200
        data = get_response.json()["data"]
        assert len(data["groups"]) == 1
        assert data["groups"][0]["name"] == "Phase A"
        assert len(data["relationships"]) == 1
        assert len(data["measurements"]) == 2
        assert all(m["group_id"] == data["groups"][0]["id"] for m in data["measurements"])
