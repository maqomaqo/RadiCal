"""
Tests for a project's date collection (models.ProjectSample,
api/project_samples.py) and the duplicate / edit-inputs endpoints that
make "same dates, different model" possible.

The load-bearing test here is TestSnapshotSemantics: deploying a date into
an analysis must COPY it, so editing the collection afterwards can never
mutate an already-run analysis or silently invalidate its stored results.
Everything else is CRUD and validation.
"""
import pytest



def _date(name="OxA-1", age_bp=3000, **overrides):
    d = {
        "sample_name": name,
        "age_bp": age_bp,
        "error_1sigma": 30,
        "material_type": "charcoal",
    }
    d.update(overrides)
    return d


def _make_project(client, name="Test Site"):
    return client.post("/api/projects", json={"name": name}).json()["data"]["id"]


def _add_dates(client, project_id, dates):
    response = client.post(f"/api/projects/{project_id}/samples", json={"samples": dates})
    assert response.status_code == 200, response.text
    return response.json()["data"]["items"]


class TestProjectSampleCrud:
    def test_add_and_list_dates(self, client):
        pid = _make_project(client)
        _add_dates(client, pid, [_date("OxA-1", 3000), _date("OxA-2", 3200)])

        items = client.get(f"/api/projects/{pid}/samples").json()["data"]["items"]
        assert [i["sample_name"] for i in items] == ["OxA-1", "OxA-2"]
        assert items[0]["age_bp"] == 3000

    def test_bulk_add_in_one_request(self, client):
        pid = _make_project(client)
        created = _add_dates(client, pid, [_date(f"S{i}", 3000 + i * 10) for i in range(25)])
        assert len(created) == 25

    def test_update_a_date(self, client):
        pid = _make_project(client)
        sample_id = _add_dates(client, pid, [_date("OxA-1", 3000)])[0]["id"]

        response = client.patch(
            f"/api/projects/{pid}/samples/{sample_id}",
            json={"age_bp": 3055, "default_context_name": "Pit 12"},
        )
        assert response.status_code == 200
        assert response.json()["data"]["age_bp"] == 3055
        assert response.json()["data"]["default_context_name"] == "Pit 12"

    def test_delete_a_date(self, client):
        pid = _make_project(client)
        sample_id = _add_dates(client, pid, [_date("OxA-1")])[0]["id"]

        assert client.delete(f"/api/projects/{pid}/samples/{sample_id}").status_code == 200
        assert client.get(f"/api/projects/{pid}/samples").json()["data"]["items"] == []

    def test_unknown_project_404s(self, client):
        assert client.get("/api/projects/nope/samples").status_code == 404

    def test_validation_rejects_bad_measurement(self, client):
        pid = _make_project(client)
        assert client.post(
            f"/api/projects/{pid}/samples", json={"samples": [_date(age_bp=-5)]}
        ).status_code == 422
        assert client.post(
            f"/api/projects/{pid}/samples", json={"samples": [_date(error_1sigma=0)]}
        ).status_code == 422
        assert client.post(
            f"/api/projects/{pid}/samples", json={"samples": [_date(curve_id="nonsense")]}
        ).status_code == 422

    def test_update_rejects_contradictory_bounds(self, client):
        """taq > tpq must be caught even when the two arrive in separate
        PATCHes, since the merged row is what matters."""
        pid = _make_project(client)
        sample_id = _add_dates(client, pid, [_date(tpq_age_bp=2000)])[0]["id"]

        response = client.patch(
            f"/api/projects/{pid}/samples/{sample_id}", json={"taq_age_bp": 2500}
        )
        assert response.status_code == 422


class TestDeploy:
    def test_deploy_creates_pending_analysis_with_copies(self, client):
        pid = _make_project(client)
        created = _add_dates(client, pid, [_date("OxA-1", 3000), _date("OxA-2", 3200)])
        ids = [c["id"] for c in created]

        response = client.post(
            f"/api/projects/{pid}/deploy",
            json={"name": "Model A", "sample_ids": ids},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "pending"
        assert data["measurements_count"] == 2

        analysis = client.get(f"/api/analysis/{data['id']}").json()["data"]
        assert analysis["project_id"] == pid
        assert sorted(m["sample_name"] for m in analysis["measurements"]) == ["OxA-1", "OxA-2"]

    def test_deploy_groups_by_default_context_name(self, client):
        pid = _make_project(client)
        created = _add_dates(client, pid, [
            _date("A", 3000, default_context_name="Pit 12"),
            _date("B", 3100, default_context_name="Pit 12"),
            _date("C", 3300, default_context_name="Layer 3"),
            _date("D", 3400),
        ])
        ids = [c["id"] for c in created]

        deployed = client.post(
            f"/api/projects/{pid}/deploy", json={"name": "M", "sample_ids": ids}
        ).json()["data"]
        assert deployed["contexts_created"] == 2

        analysis = client.get(f"/api/analysis/{deployed['id']}").json()["data"]
        by_name = {g["id"]: g["name"] for g in analysis["groups"]}
        assignment = {m["sample_name"]: by_name.get(m["group_id"]) for m in analysis["measurements"]}
        assert assignment["A"] == "Pit 12"
        assert assignment["B"] == "Pit 12"
        assert assignment["C"] == "Layer 3"
        assert assignment["D"] is None

    def test_same_dates_deploy_into_independent_analyses(self, client):
        """The whole point of the collection: one set of dates, several
        analyses, each free to model them differently."""
        pid = _make_project(client)
        ids = [c["id"] for c in _add_dates(client, pid, [_date("A", 3000), _date("B", 3200)])]

        first = client.post(f"/api/projects/{pid}/deploy", json={"name": "M1", "sample_ids": ids}).json()["data"]
        second = client.post(f"/api/projects/{pid}/deploy", json={"name": "M2", "sample_ids": ids}).json()["data"]

        assert first["id"] != second["id"]
        a1 = client.get(f"/api/analysis/{first['id']}").json()["data"]
        a2 = client.get(f"/api/analysis/{second['id']}").json()["data"]
        # Distinct sample rows, not shared ones.
        assert {m["id"] for m in a1["measurements"]}.isdisjoint({m["id"] for m in a2["measurements"]})

    def test_deploy_rejects_unknown_sample_id(self, client):
        pid = _make_project(client)
        assert client.post(
            f"/api/projects/{pid}/deploy", json={"name": "M", "sample_ids": ["ghost"]}
        ).status_code == 404

    def test_deploy_rejects_empty_selection(self, client):
        pid = _make_project(client)
        assert client.post(
            f"/api/projects/{pid}/deploy", json={"name": "M", "sample_ids": []}
        ).status_code == 422


class TestSnapshotSemantics:
    def test_editing_a_collected_date_does_not_touch_deployed_analyses(self, client):
        pid = _make_project(client)
        sample_id = _add_dates(client, pid, [_date("OxA-1", 3000)])[0]["id"]
        deployed = client.post(
            f"/api/projects/{pid}/deploy", json={"name": "M", "sample_ids": [sample_id]}
        ).json()["data"]

        client.patch(f"/api/projects/{pid}/samples/{sample_id}", json={"age_bp": 9999})

        analysis = client.get(f"/api/analysis/{deployed['id']}").json()["data"]
        assert analysis["measurements"][0]["age_bp"] == 3000

    def test_deleting_a_collected_date_does_not_touch_deployed_analyses(self, client):
        pid = _make_project(client)
        sample_id = _add_dates(client, pid, [_date("OxA-1", 3000)])[0]["id"]
        deployed = client.post(
            f"/api/projects/{pid}/deploy", json={"name": "M", "sample_ids": [sample_id]}
        ).json()["data"]

        client.delete(f"/api/projects/{pid}/samples/{sample_id}")

        analysis = client.get(f"/api/analysis/{deployed['id']}").json()["data"]
        assert len(analysis["measurements"]) == 1
        assert analysis["measurements"][0]["age_bp"] == 3000


class TestDuplicate:
    def _saved_bayesian(self, client, name="Original"):
        return client.post(
            "/api/analysis/bayesian",
            json={
                "name": name,
                "measurements": [
                    {"id": "a", "sample_name": "A", "age_bp": 3000, "error_1sigma": 30, "group_id": "g1"},
                    {"id": "b", "sample_name": "B", "age_bp": 3200, "error_1sigma": 30, "group_id": "g2"},
                ],
                "groups": [
                    {"id": "g1", "name": "Upper", "tpq_age_bp": None, "taq_age_bp": None},
                    {"id": "g2", "name": "Lower", "tpq_age_bp": None, "taq_age_bp": None},
                ],
                "relationships": [
                    {"from_sample_id": "g2", "to_sample_id": "g1", "relationship": "below",
                     "from_type": "group", "to_type": "group"},
                ],
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        )

    def test_duplicate_copies_inputs_but_not_results(self, client):
        original_id = self._saved_bayesian(client).json()["data"]["id"]

        response = client.post(f"/api/analysis/{original_id}/duplicate")
        assert response.status_code == 200
        copy_id = response.json()["data"]["id"]
        assert copy_id != original_id
        assert response.json()["data"]["status"] == "pending"

        copy = client.get(f"/api/analysis/{copy_id}").json()["data"]
        assert copy["name"] == "Original (copy)"
        assert len(copy["measurements"]) == 2
        assert len(copy["groups"]) == 2
        assert len(copy["relationships"]) == 1
        # Inputs copied, results not.
        assert copy["results"] == []
        # MCMC settings carried across.
        assert copy["bayesian_config"]["mcmc_samples"] == 200

        # Ids are remapped to the copy's own rows, not shared with the original.
        original = client.get(f"/api/analysis/{original_id}").json()["data"]
        assert {m["id"] for m in copy["measurements"]}.isdisjoint({m["id"] for m in original["measurements"]})
        assert {g["id"] for g in copy["groups"]}.isdisjoint({g["id"] for g in original["groups"]})

        # The copied relationship points at the COPY's groups.
        copy_group_ids = {g["id"] for g in copy["groups"]}
        rel = copy["relationships"][0]
        assert rel["from_sample_id"] in copy_group_ids
        assert rel["to_sample_id"] in copy_group_ids

    def test_running_a_duplicate_leaves_the_original_untouched(self, client):
        original_id = self._saved_bayesian(client).json()["data"]["id"]
        before = client.get(f"/api/analysis/{original_id}").json()["data"]

        copy_id = client.post(f"/api/analysis/{original_id}/duplicate").json()["data"]["id"]
        assert client.post(f"/api/analysis/{copy_id}/rerun").status_code == 200

        after = client.get(f"/api/analysis/{original_id}").json()["data"]
        assert after["status"] == before["status"]
        assert len(after["results"]) == len(before["results"])

    def test_duplicate_unknown_analysis_404s(self, client):
        assert client.post("/api/analysis/ghost/duplicate").status_code == 404


class TestReplaceInputs:
    def _body(self, **overrides):
        body = {
            "name": "Edited",
            "measurements": [
                {"id": "m1", "sample_name": "A", "age_bp": 3000, "error_1sigma": 30},
                {"id": "m2", "sample_name": "B", "age_bp": 3300, "error_1sigma": 30},
            ],
            "groups": [],
            "relationships": [],
            "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
        }
        body.update(overrides)
        return body

    def test_replacing_inputs_clears_stale_results(self, client):
        created = client.post(
            "/api/analysis/bayesian",
            json={
                "name": "Original",
                "measurements": [
                    {"id": "a", "sample_name": "A", "age_bp": 3000, "error_1sigma": 30, "group_id": "g1"},
                    {"id": "b", "sample_name": "B", "age_bp": 3100, "error_1sigma": 30, "group_id": "g1"},
                ],
                "groups": [{"id": "g1", "name": "Ctx", "tpq_age_bp": None, "taq_age_bp": None}],
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        ).json()["data"]["id"]

        before = client.get(f"/api/analysis/{created}").json()["data"]
        assert len(before["results"]) == 2
        assert before["groups"][0]["modeled_start"] is not None

        response = client.put(f"/api/analysis/{created}/inputs", json=self._body())
        assert response.status_code == 200

        after = client.get(f"/api/analysis/{created}").json()["data"]
        assert after["name"] == "Edited"
        assert after["status"] == "pending"
        assert after["results"] == []
        assert sorted(m["sample_name"] for m in after["measurements"]) == ["A", "B"]
        # The replacement supplied no contexts, so the rebuilt model tree
        # collects the dates into a single "Unassigned dates" phase -- which
        # is what `groups` now reflects, being derived from the tree.
        assert [g["name"] for g in after["groups"]] == ["Unassigned dates"]
        assert after["model_tree"] is not None

        # And it can be run again from the new inputs.
        assert client.post(f"/api/analysis/{created}/rerun").status_code == 200
        assert len(client.get(f"/api/analysis/{created}").json()["data"]["results"]) == 2

    def test_replacing_inputs_rejects_a_cycle(self, client):
        created = client.post(
            "/api/analysis/calibrate",
            json={"sample_name": "A", "age_bp": 3000, "error_1sigma": 30, "material_type": "wood", "save": True},
        ).json()["data"]["id"]

        response = client.put(f"/api/analysis/{created}/inputs", json=self._body(
            measurements=[
                {"id": "x", "sample_name": "X", "age_bp": 3000, "error_1sigma": 30},
                {"id": "y", "sample_name": "Y", "age_bp": 3100, "error_1sigma": 30},
            ],
            relationships=[
                {"from_sample_id": "x", "to_sample_id": "y", "relationship": "above"},
                {"from_sample_id": "y", "to_sample_id": "x", "relationship": "above"},
            ],
        ))
        assert response.status_code == 422

    def test_replacing_inputs_rejects_dangling_group_reference(self, client):
        created = client.post(
            "/api/analysis/calibrate",
            json={"sample_name": "A", "age_bp": 3000, "error_1sigma": 30, "material_type": "wood", "save": True},
        ).json()["data"]["id"]

        response = client.put(f"/api/analysis/{created}/inputs", json=self._body(
            measurements=[{"id": "x", "sample_name": "X", "age_bp": 3000, "error_1sigma": 30, "group_id": "ghost"}],
        ))
        assert response.status_code == 422

    def test_replace_inputs_unknown_analysis_404s(self, client):
        assert client.put("/api/analysis/ghost/inputs", json=self._body()).status_code == 404
