"""
Tests for:
  - PATCH /api/analysis/{id} project (re)assignment (endpoint already
    existed, but had no test coverage at all).
  - POST /api/analysis/{id}/rerun -- recompute an already-saved analysis's
    results in place from its own persisted inputs.
  - POST /api/projects/{id}/rerun -- rerun every analysis in a project.

The rerun endpoints need a real MCMC run for a bayesian analysis, so those
are @requires_pymc-marked like the rest of the MCMC-dependent test suite;
the project-assignment test and the 404 checks don't touch pymc at all.
"""
import csv
import io
import itertools

import pytest

from conftest import requires_pymc


def _measurement(sample_id, age_bp=3000, **overrides):
    m = {
        "id": sample_id,
        "age_bp": age_bp,
        "error_1sigma": 30,
        "sample_name": f"Sample {sample_id}",
    }
    m.update(overrides)
    return m


class TestProjectAssignment:
    def test_assign_unassigned_analysis_to_project(self, client):
        project_resp = client.post("/api/projects", json={"name": "Test Project"})
        project_id = project_resp.json()["data"]["id"]

        calibrate_resp = client.post(
            "/api/analysis/calibrate",
            json={"sample_name": "A", "age_bp": 3000, "error_1sigma": 30, "material_type": "charcoal", "save": True},
        )
        analysis_id = calibrate_resp.json()["data"]["id"]
        assert calibrate_resp.json()["data"]["project_id"] is None

        patch_resp = client.patch(f"/api/analysis/{analysis_id}", json={"project_id": project_id})
        assert patch_resp.status_code == 200
        assert patch_resp.json()["data"]["project_id"] == project_id
        assert patch_resp.json()["data"]["project_name"] == "Test Project"

        get_resp = client.get(f"/api/analysis/{analysis_id}")
        assert get_resp.json()["data"]["project_id"] == project_id

    def test_unassign_analysis(self, client):
        project_resp = client.post("/api/projects", json={"name": "Another Project"})
        project_id = project_resp.json()["data"]["id"]

        calibrate_resp = client.post(
            "/api/analysis/calibrate",
            json={"sample_name": "A", "age_bp": 3000, "error_1sigma": 30, "material_type": "charcoal", "project_id": project_id, "save": True},
        )
        analysis_id = calibrate_resp.json()["data"]["id"]

        patch_resp = client.patch(f"/api/analysis/{analysis_id}", json={"project_id": None})
        assert patch_resp.status_code == 200
        assert patch_resp.json()["data"]["project_id"] is None

    def test_assign_to_unknown_project_rejected(self, client):
        calibrate_resp = client.post(
            "/api/analysis/calibrate",
            json={"sample_name": "A", "age_bp": 3000, "error_1sigma": 30, "material_type": "charcoal", "save": True},
        )
        analysis_id = calibrate_resp.json()["data"]["id"]

        patch_resp = client.patch(f"/api/analysis/{analysis_id}", json={"project_id": "does-not-exist"})
        assert patch_resp.status_code == 404


class TestRerunNotFound:
    def test_rerun_unknown_analysis_404s(self, client):
        response = client.post("/api/analysis/does-not-exist/rerun")
        assert response.status_code == 404

    def test_rerun_unknown_project_404s(self, client):
        response = client.post("/api/projects/does-not-exist/rerun")
        assert response.status_code == 404


@requires_pymc
class TestRerunAnalysis:
    def test_rerun_calibration_analysis(self, client):
        create_resp = client.post(
            "/api/analysis/calibrate",
            json={"sample_name": "A", "age_bp": 3000, "error_1sigma": 30, "material_type": "charcoal", "save": True},
        )
        analysis_id = create_resp.json()["data"]["id"]
        original_mean = create_resp.json()["data"]["posterior_mean"]

        rerun_resp = client.post(f"/api/analysis/{analysis_id}/rerun")
        assert rerun_resp.status_code == 200
        assert rerun_resp.json()["data"]["status"] == "completed"

        get_resp = client.get(f"/api/analysis/{analysis_id}")
        results = get_resp.json()["data"]["results"]
        assert len(results) == 1
        assert results[0]["posterior_mean"] == pytest.approx(original_mean, rel=1e-6)

    def test_rerun_bayesian_analysis_refreshes_boundary(self, client):
        create_resp = client.post(
            "/api/analysis/bayesian",
            json={
                "name": "Rerun target",
                "measurements": [
                    _measurement("a", group_id="g1"),
                    _measurement("b", age_bp=3200, group_id="g1"),
                ],
                "groups": [{"id": "g1", "name": "Phase A", "tpq_age_bp": None, "taq_age_bp": None}],
                "relationships": [],
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        )
        analysis_id = create_resp.json()["data"]["id"]

        rerun_resp = client.post(f"/api/analysis/{analysis_id}/rerun")
        assert rerun_resp.status_code == 200
        assert rerun_resp.json()["data"]["status"] == "completed"

        get_resp = client.get(f"/api/analysis/{analysis_id}")
        groups = get_resp.json()["data"]["groups"]
        assert len(groups) == 1
        assert groups[0]["modeled_start"] is not None
        assert groups[0]["modeled_end"] is not None
        assert len(get_resp.json()["data"]["results"]) == 2


@requires_pymc
class TestRerunProject:
    def test_rerun_all_analyses_in_project(self, client):
        project_resp = client.post("/api/projects", json={"name": "Batch Rerun"})
        project_id = project_resp.json()["data"]["id"]

        for age in (3000, 3100):
            client.post(
                "/api/analysis/calibrate",
                json={
                    "sample_name": f"S{age}", "age_bp": age, "error_1sigma": 30,
                    "material_type": "charcoal", "project_id": project_id, "save": True,
                },
            )

        rerun_resp = client.post(f"/api/projects/{project_id}/rerun")
        assert rerun_resp.status_code == 200
        data = rerun_resp.json()["data"]
        assert data["rerun_count"] == 2
        assert data["failed"] == []


_ROUND_TRIP_IDS = itertools.count(1)


class TestEditRoundTrip:
    """
    The edit page rebuilds its draft from GET /api/analysis/{id} and
    resubmits it, so anything that payload omits is silently replaced by the
    client's default on save. That made editing an analysis rewrite every
    material as "unknown" and -- because it changes the calibration, not
    just a label -- recalibrate marine samples against the terrestrial
    curve.
    """

    def _analysis(self, client):
        """
        A saved analysis with an explicit tree, so each date has a
        well-defined phase to be resubmitted with.

        Ids are made unique per call because model element ids are a global
        primary key (see conftest.client) -- and each test needs its own
        analysis rather than a shared one, since one of them edits it.
        """
        tag = f"rt{next(_ROUND_TRIP_IDS)}"
        tree = {
            "id": f"{tag}root", "element_type": "sequence", "name": "Site", "children": [
                {"id": f"{tag}b0", "element_type": "boundary", "name": "Start", "children": []},
                {"id": f"{tag}p1", "element_type": "phase", "name": "Only", "children": []},
                {"id": f"{tag}b1", "element_type": "boundary", "name": "End", "children": []},
            ],
        }
        return client.post(
            "/api/analysis/bayesian",
            json={
                "name": "Round trip",
                "measurements": [
                    _measurement(f"{tag}s1", sample_name="Charcoal", material_type="charcoal",
                                 sample_type="terrestrial", curve_id="shcal20",
                                 element_id=f"{tag}p1"),
                    _measurement(f"{tag}s2", age_bp=3100, sample_name="Shell",
                                 material_type="marine shell", sample_type="marine",
                                 curve_id="marine20", element_id=f"{tag}p1"),
                ],
                "model_tree": tree,
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        )

    @requires_pymc
    def test_get_returns_what_an_edit_needs_to_resubmit(self, client):
        created = self._analysis(client)
        assert created.status_code == 200, created.text
        analysis_id = created.json()["data"]["id"]

        measurements = client.get(f"/api/analysis/{analysis_id}").json()["data"]["measurements"]
        by_name = {m["sample_name"]: m for m in measurements}

        assert by_name["Charcoal"]["material_type"] == "charcoal"
        assert by_name["Charcoal"]["curve_id"] == "shcal20"
        assert by_name["Charcoal"]["sample_type"] == "terrestrial"
        assert by_name["Shell"]["material_type"] == "marine shell"
        assert by_name["Shell"]["curve_id"] == "marine20"
        assert by_name["Shell"]["sample_type"] == "marine"

    @requires_pymc
    def test_editing_preserves_material_curve_and_sample_type(self, client):
        created = self._analysis(client)
        assert created.status_code == 200, created.text
        analysis_id = created.json()["data"]["id"]
        saved = client.get(f"/api/analysis/{analysis_id}").json()["data"]

        # Resubmit exactly as the edit page does: whatever the payload gave
        # it, falling back to the same defaults it uses for missing fields.
        replaced = client.put(
            f"/api/analysis/{analysis_id}/inputs",
            json={
                "name": saved["name"],
                "measurements": [
                    {
                        "id": m["id"], "sample_name": m["sample_name"],
                        "age_bp": m["age_bp"], "error_1sigma": m["error_1sigma"],
                        "material_type": m.get("material_type") or "unknown",
                        "sample_type": m.get("sample_type") or "terrestrial",
                        "curve_id": m.get("curve_id") or "intcal20",
                        "element_id": m.get("element_id") or m.get("group_id"),
                    }
                    for m in saved["measurements"]
                ],
                "groups": [], "relationships": [],
                "model_tree": saved["model_tree"],
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
            },
        )
        assert replaced.status_code == 200, replaced.text

        after = {
            m["sample_name"]: m
            for m in client.get(f"/api/analysis/{analysis_id}").json()["data"]["measurements"]
        }
        assert after["Charcoal"]["material_type"] == "charcoal"
        assert after["Shell"]["material_type"] == "marine shell"
        # The curve is the part that changes the numbers, not just a label.
        assert after["Charcoal"]["curve_id"] == "shcal20"
        assert after["Shell"]["curve_id"] == "marine20"
        assert after["Shell"]["sample_type"] == "marine"

    @requires_pymc
    def test_the_csv_reports_the_material_that_was_entered(self, client):
        created = self._analysis(client)
        assert created.status_code == 200, created.text
        analysis_id = created.json()["data"]["id"]

        rows = list(csv.reader(io.StringIO(
            client.get(f"/api/results/{analysis_id}/export/csv").text)))
        header = rows[0]
        material, curve = header.index('Material'), header.index('Curve')
        reported = {r[4]: (r[material], r[curve]) for r in rows[1:] if r[1] == 'Date'}
        assert reported["Charcoal"] == ("charcoal", "shcal20")
        assert reported["Shell"] == ("marine shell", "marine20")
