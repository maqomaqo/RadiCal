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
