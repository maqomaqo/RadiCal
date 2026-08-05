"""
Tests for the explicit model structure: the tree itself
(services/model_tree.py), its persistence, and PUT /analysis/{id}/model-tree.

The planner tests here need no MCMC -- they are pure structure, which is
most of the point of keeping model_tree.py free of PyMC.
"""
import pytest

from app.services.model_tree import (
    validate_tree,
    plan_model,
    inherited_bounds,
    migrate_from_relationships,
)

from conftest import requires_pymc


def _boundary(element_id, name="Boundary"):
    return {"id": element_id, "element_type": "boundary", "name": name, "children": []}


def _phase(element_id, name="Phase", **kwargs):
    return {"id": element_id, "element_type": "phase", "name": name, "children": [], **kwargs}


def _sequence(element_id, name, children, **kwargs):
    return {"id": element_id, "element_type": "sequence", "name": name,
            "children": children, **kwargs}


def _dates(*, element_id, count, start=3000):
    return [{"id": f"{element_id}_{i}", "sample_name": f"{element_id}{i}",
             "age_bp": start + i * 30, "error_1sigma": 25, "element_id": element_id}
            for i in range(count)]


class TestValidation:
    def test_accepts_a_site_sequence(self):
        tree = _sequence("r", "Site", [
            _boundary("b0", "Start"), _phase("p1"), _boundary("b1"), _phase("p2"), _boundary("b2"),
        ])
        validate_tree(tree, _dates(element_id="p1", count=2) + _dates(element_id="p2", count=2))

    def test_rejects_date_attached_to_a_boundary(self):
        tree = _sequence("r", "Site", [_boundary("b0"), _phase("p1"), _boundary("b1")])
        with pytest.raises(ValueError, match="dates belong to a phase"):
            validate_tree(tree, _dates(element_id="b0", count=1))

    def test_rejects_orphan_date(self):
        tree = _sequence("r", "Site", [_boundary("b0"), _phase("p1"), _boundary("b1")])
        with pytest.raises(ValueError, match="unknown model element"):
            validate_tree(tree, _dates(element_id="nope", count=1))

    def test_rejects_duplicate_element_id(self):
        tree = _sequence("r", "Site", [_phase("dup"), _phase("dup")])
        with pytest.raises(ValueError, match="more than once"):
            validate_tree(tree, [])

    def test_rejects_hiatus_not_between_boundaries(self):
        tree = _sequence("r", "Site", [
            _phase("p1"), {"id": "h", "element_type": "hiatus", "name": "Gap", "children": []},
            _phase("p2"),
        ])
        with pytest.raises(ValueError, match="hiatus must sit between two boundaries"):
            validate_tree(tree, [])

    def test_accepts_hiatus_between_boundaries(self):
        tree = _sequence("r", "Site", [
            _boundary("b0"), _phase("p1"), _boundary("b1"),
            {"id": "h", "element_type": "hiatus", "name": "Gap", "children": []},
            _boundary("b2"), _phase("p2"), _boundary("b3"),
        ])
        validate_tree(tree, _dates(element_id="p1", count=2) + _dates(element_id="p2", count=2))

    def test_rejects_boundary_inside_a_phase(self):
        tree = _sequence("r", "Site", [_phase("p1", children=[_boundary("b")])])
        with pytest.raises(ValueError, match="cannot sit directly inside a phase"):
            validate_tree(tree, [])

    def test_rejects_contradictory_limiters(self):
        tree = _sequence("r", "Site", [_phase("p1", tpq_age_bp=100, taq_age_bp=500)])
        with pytest.raises(ValueError, match="taq_age_bp"):
            validate_tree(tree, [])


class TestPlanner:
    def test_shared_boundary_is_one_interior_parameter(self):
        tree = _sequence("r", "Site", [
            _boundary("b0", "Start"), _phase("p1"), _boundary("b1", "Transition"),
            _phase("p2"), _boundary("b2", "End"),
        ])
        measurements = _dates(element_id="p1", count=3) + _dates(element_id="p2", count=4)
        plan = plan_model(tree, measurements)

        assert plan.sampled_boundaries == ["b1"]
        assert plan.marginalized_boundaries == {"b0", "b2"}
        # Two groups, sharing b1 -- not four independent per-phase boundaries.
        assert [(g.older_boundary_id, g.younger_boundary_id, len(g.date_ids))
                for g in plan.span_groups] == [("b0", "b1", 3), ("b1", "b2", 4)]

    def test_nested_group_dates_are_not_double_counted(self):
        inner = _sequence("inner", "Inner", [_boundary("i0"), _phase("pi"), _boundary("i1")])
        tree = _sequence("r", "Site", [_boundary("b0"), inner, _boundary("b1")])
        measurements = _dates(element_id="pi", count=3)
        plan = plan_model(tree, measurements)

        by_pair = {(g.older_boundary_id, g.younger_boundary_id): len(g.date_ids)
                   for g in plan.span_groups}
        # The inner group claims its dates; the outer group sees none of them.
        assert by_pair[("i0", "i1")] == 3
        assert by_pair[("b0", "b1")] == 0

    def test_phase_of_sequences_is_unordered_between_branches(self):
        branch_a = _sequence("sa", "A", [_phase("pa1"), _phase("pa2")])
        branch_b = _sequence("sb", "B", [_phase("pb1"), _phase("pb2")])
        tree = _sequence("r", "Site", [_phase("par", children=[branch_a, branch_b])])
        measurements = (_dates(element_id="pa1", count=1) + _dates(element_id="pa2", count=1)
                        + _dates(element_id="pb1", count=1) + _dates(element_id="pb2", count=1))
        plan = plan_model(tree, measurements)

        pairs = [({t for _k, t in o}, {t for _k, t in y}) for o, y in plan.ordering]
        assert ({"pa1_0"}, {"pa2_0"}) in pairs
        assert ({"pb1_0"}, {"pb2_0"}) in pairs
        for older, younger in pairs:
            assert not (older & {"pa1_0", "pa2_0"} and younger & {"pb1_0", "pb2_0"})
            assert not (older & {"pb1_0", "pb2_0"} and younger & {"pa1_0", "pa2_0"})

    def test_hiatus_gives_two_boundaries_where_shared_gives_one(self):
        shared = _sequence("r", "S", [
            _boundary("b0"), _phase("p1"), _boundary("b1"), _phase("p2"), _boundary("b2")])
        gapped = _sequence("r", "S", [
            _boundary("b0"), _phase("p1"), _boundary("b1"),
            {"id": "h", "element_type": "hiatus", "name": "Gap", "children": []},
            _boundary("b2"), _phase("p2"), _boundary("b3")])
        m = _dates(element_id="p1", count=2) + _dates(element_id="p2", count=2)

        assert len(plan_model(shared, m).span_groups) == 2
        # The gapped model has an extra group -- the hiatus itself, empty --
        # and one more boundary, which is exactly the difference.
        gapped_plan = plan_model(gapped, m)
        assert len(gapped_plan.boundary_names) == 4
        assert any(len(g.date_ids) == 0 for g in gapped_plan.span_groups)

    def test_limiters_are_inherited_through_nesting(self):
        inner = _sequence("inner", "Inner", [_phase("p", tpq_age_bp=4000)], taq_age_bp=1000)
        tree = _sequence("r", "Site", [inner], tpq_age_bp=5000)
        bounds = inherited_bounds(tree)
        # Tightest wins: tpq from the phase, taq from the inner sequence.
        assert bounds["p"] == (1000, 4000)


class TestMigration:
    def _new_id(self):
        counter = iter(range(1000))
        return lambda: f"gen{next(counter)}"

    def test_chain_becomes_a_sequence_with_boundaries(self):
        measurements = [{"id": "a", "group_id": "g1"}, {"id": "b", "group_id": "g2"}]
        groups = [{"id": "g1", "name": "Upper"}, {"id": "g2", "name": "Lower"}]
        relationships = [{"from_sample_id": "g1", "to_sample_id": "g2",
                          "relationship": "above", "from_type": "group", "to_type": "group"}]

        root, linearized, group_boundaries = migrate_from_relationships(
            measurements, groups, relationships, self._new_id())

        types = [c["element_type"] for c in root["children"]]
        assert types == ["boundary", "phase", "boundary", "phase", "boundary"]
        # Law of superposition: g1 is above g2, so g2 (older) comes first.
        assert [c["id"] for c in root["children"] if c["element_type"] == "phase"] == ["g2", "g1"]
        assert not linearized
        # Adjacent contexts share the boundary between them.
        assert group_boundaries["g2"][1] == group_boundaries["g1"][0]

    def test_unordered_contexts_are_flagged_as_linearized(self):
        measurements = [{"id": "a", "group_id": "g1"}, {"id": "b", "group_id": "g2"}]
        groups = [{"id": "g1", "name": "One"}, {"id": "g2", "name": "Two"}]
        _root, linearized, _gb = migrate_from_relationships(
            measurements, groups, [], self._new_id())
        assert linearized


class TestModelTreeApi:
    def _analysis(self, client):
        return client.post(
            "/api/analysis/bayesian",
            json={
                "name": "Tree api",
                "measurements": [
                    {"id": "a", "sample_name": "A", "age_bp": 3300, "error_1sigma": 30, "group_id": "g1"},
                    {"id": "b", "sample_name": "B", "age_bp": 3000, "error_1sigma": 30, "group_id": "g2"},
                ],
                "groups": [{"id": "g1", "name": "Upper"}, {"id": "g2", "name": "Lower"}],
                "relationships": [{"from_sample_id": "g1", "to_sample_id": "g2",
                                   "relationship": "above", "from_type": "group", "to_type": "group"}],
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        ).json()["data"]["id"]

    @requires_pymc
    def test_get_returns_a_tree_with_boundary_posteriors(self, client):
        analysis_id = self._analysis(client)
        data = client.get(f"/api/analysis/{analysis_id}").json()["data"]

        tree = data["model_tree"]
        assert tree is not None
        assert tree["element_type"] == "sequence"
        types = [c["element_type"] for c in tree["children"]]
        assert types == ["boundary", "phase", "boundary", "phase", "boundary"]
        boundaries = [c for c in tree["children"] if c["element_type"] == "boundary"]
        assert all(b["posterior"] is not None for b in boundaries)
        # The shared transition is a single element carrying one posterior.
        assert boundaries[1]["posterior"]["posterior_mean"] > 0

    @requires_pymc
    def test_put_replaces_the_tree_and_clears_results(self, client):
        analysis_id = self._analysis(client)
        data = client.get(f"/api/analysis/{analysis_id}").json()["data"]
        sample_ids = [m["id"] for m in data["measurements"]]

        # Collapse both contexts into one phase inside a bracketed sequence.
        new_tree = _sequence("root2", "Site", [
            _boundary("nb0", "Start"), _phase("np1", "All one phase"), _boundary("nb1", "End"),
        ])
        response = client.put(
            f"/api/analysis/{analysis_id}/model-tree",
            json={"model_tree": new_tree,
                  "sample_elements": {sid: "np1" for sid in sample_ids}},
        )
        assert response.status_code == 200

        after = client.get(f"/api/analysis/{analysis_id}").json()["data"]
        assert after["status"] == "pending"
        assert after["results"] == []
        assert [c["element_type"] for c in after["model_tree"]["children"]] == [
            "boundary", "phase", "boundary"]

        # And it runs from the new structure.
        assert client.post(f"/api/analysis/{analysis_id}/rerun").status_code == 200
        rerun = client.get(f"/api/analysis/{analysis_id}").json()["data"]
        assert len(rerun["results"]) == 2

    @requires_pymc
    def test_put_rejects_an_invalid_tree(self, client):
        analysis_id = self._analysis(client)
        bad = _sequence("r2", "Site", [
            _phase("p1"), {"id": "h", "element_type": "hiatus", "name": "Gap", "children": []},
            _phase("p2"),
        ])
        response = client.put(
            f"/api/analysis/{analysis_id}/model-tree", json={"model_tree": bad})
        assert response.status_code == 422

    @requires_pymc
    def test_create_an_analysis_from_a_tree_directly(self, client):
        """
        A client can author the structure itself rather than sending contexts
        + relationships to be converted -- the tree is used as given.
        """
        tree = _sequence("root", "Site", [
            _boundary("b0", "Site start"),
            _phase("p1", "Lower"),
            _boundary("b1", "Lower/Upper"),
            _phase("p2", "Upper"),
            _boundary("b2", "Site end"),
        ])
        response = client.post(
            "/api/analysis/bayesian",
            json={
                "name": "Authored as a tree",
                "measurements": [
                    {"id": "a", "sample_name": "A", "age_bp": 3300, "error_1sigma": 30, "element_id": "p1"},
                    {"id": "b", "sample_name": "B", "age_bp": 3330, "error_1sigma": 30, "element_id": "p1"},
                    {"id": "c", "sample_name": "C", "age_bp": 3000, "error_1sigma": 30, "element_id": "p2"},
                    {"id": "d", "sample_name": "D", "age_bp": 3030, "error_1sigma": 30, "element_id": "p2"},
                ],
                "model_tree": tree,
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        )
        assert response.status_code == 200, response.text
        analysis_id = response.json()["data"]["id"]

        data = client.get(f"/api/analysis/{analysis_id}").json()["data"]
        stored = data["model_tree"]
        assert [c["name"] for c in stored["children"]] == [
            "Site start", "Lower", "Lower/Upper", "Upper", "Site end"]
        # Contexts are derived from the tree's phases rather than stored twice.
        assert sorted(g["name"] for g in data["groups"]) == ["Lower", "Upper"]
        # Every date kept its phase assignment.
        by_name = {m["sample_name"]: m["element_id"] for m in data["measurements"]}
        assert by_name["A"] == by_name["B"] == "p1"
        assert by_name["C"] == by_name["D"] == "p2"
        # And every boundary got a posterior.
        boundaries = [c for c in stored["children"] if c["element_type"] == "boundary"]
        assert all(b["posterior"] is not None for b in boundaries)

    def test_create_rejects_a_tree_with_an_orphan_date(self, client):
        response = client.post(
            "/api/analysis/bayesian",
            json={
                "name": "Bad tree",
                "measurements": [
                    {"id": "a", "sample_name": "A", "age_bp": 3000, "error_1sigma": 30,
                     "element_id": "nope"},
                ],
                "model_tree": _sequence("r", "S", [_phase("p1", "P")]),
                "save": False,
            },
        )
        assert response.status_code == 422

    def test_put_unknown_analysis_404s(self, client):
        assert client.put(
            "/api/analysis/ghost/model-tree",
            json={"model_tree": _sequence("r", "S", [])},
        ).status_code == 404
