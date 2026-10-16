"""
Tests for:
  - PATCH /api/analysis/{id} project (re)assignment (endpoint already
    existed, but had no test coverage at all).
  - POST /api/analysis/{id}/rerun -- recompute an already-saved analysis's
    results in place from its own persisted inputs.
  - POST /api/projects/{id}/rerun -- rerun every analysis in a project.

The bayesian reruns here solve a real model, which is affordable now that both
solvers are pure numpy -- they used to be skipped unless pymc was installed.
"""
import csv
import io
import itertools

import pytest



def _results_block(client, analysis_id):
    """
    The CSV's results table, header first: the rows before the first blank
    line. Blocks after it (durations, KDE) have headers of their own.
    """
    rows = csv.reader(io.StringIO(
        client.get(f'/api/results/{analysis_id}/export/csv').text))
    return list(itertools.takewhile(bool, rows))


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
        response = client.post("/api/analysis/does-not-exist/rerun?wait=true")
        assert response.status_code == 404

    def test_rerun_unknown_project_404s(self, client):
        response = client.post("/api/projects/does-not-exist/rerun?wait=true")
        assert response.status_code == 404


class TestRerunAnalysis:
    def test_rerun_calibration_analysis(self, client):
        create_resp = client.post(
            "/api/analysis/calibrate",
            json={"sample_name": "A", "age_bp": 3000, "error_1sigma": 30, "material_type": "charcoal", "save": True},
        )
        analysis_id = create_resp.json()["data"]["id"]
        original_mean = create_resp.json()["data"]["posterior_mean"]

        rerun_resp = client.post(f"/api/analysis/{analysis_id}/rerun?wait=true")
        assert rerun_resp.status_code == 200
        assert rerun_resp.json()["data"]["status"] == "completed"

        get_resp = client.get(f"/api/analysis/{analysis_id}")
        results = get_resp.json()["data"]["results"]
        assert len(results) == 1
        assert results[0]["posterior_mean"] == pytest.approx(original_mean, rel=1e-6)

    def test_rerun_bayesian_analysis_refreshes_boundary(self, client):
        create_resp = client.post(
            "/api/analysis/bayesian?wait=true",
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

        rerun_resp = client.post(f"/api/analysis/{analysis_id}/rerun?wait=true")
        assert rerun_resp.status_code == 200
        assert rerun_resp.json()["data"]["status"] == "completed"

        get_resp = client.get(f"/api/analysis/{analysis_id}")
        groups = get_resp.json()["data"]["groups"]
        assert len(groups) == 1
        assert groups[0]["modeled_start"] is not None
        assert groups[0]["modeled_end"] is not None
        assert len(get_resp.json()["data"]["results"]) == 2


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

        rerun_resp = client.post(f"/api/projects/{project_id}/rerun?wait=true")
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
            "/api/analysis/bayesian?wait=true",
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

    def test_the_csv_reports_the_material_that_was_entered(self, client):
        created = self._analysis(client)
        assert created.status_code == 200, created.text
        analysis_id = created.json()["data"]["id"]

        rows = _results_block(client, analysis_id)
        header = rows[0]
        material, curve = header.index('Material'), header.index('Curve')
        reported = {r[4]: (r[material], r[curve]) for r in rows[1:] if r[1] == 'Date'}
        assert reported["Charcoal"] == ("charcoal", "shcal20")
        assert reported["Shell"] == ("marine shell", "marine20")

    def test_editing_preserves_the_fields_the_form_does_not_show(self, client):
        """
        reservoir_age, depth_cm, location and notes are recorded against a
        date but take no part in the calculation, and the new-analysis form
        does not surface them -- so nothing on screen would reveal that an
        edit had blanked them. Dates deployed from a project collection
        arrive with them set.
        """
        tag = f"ex{next(_ROUND_TRIP_IDS)}"
        tree = {
            "id": f"{tag}root", "element_type": "sequence", "name": "Site", "children": [
                {"id": f"{tag}b0", "element_type": "boundary", "name": "Start", "children": []},
                {"id": f"{tag}p1", "element_type": "phase", "name": "Only", "children": []},
                {"id": f"{tag}b1", "element_type": "boundary", "name": "End", "children": []},
            ],
        }
        extras = {"reservoir_age": 400, "depth_cm": 125.5,
                  "location": "Trench 4", "notes": "possible bioturbation"}
        created = client.post(
            "/api/analysis/bayesian?wait=true",
            json={
                "name": "Extra fields",
                "measurements": [
                    _measurement(f"{tag}s1", sample_name="Shell", material_type="marine shell",
                                 sample_type="marine", curve_id="marine20",
                                 element_id=f"{tag}p1", **extras),
                ],
                "model_tree": tree,
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        )
        assert created.status_code == 200, created.text
        analysis_id = created.json()["data"]["id"]

        def stored():
            m = client.get(f"/api/analysis/{analysis_id}").json()["data"]["measurements"][0]
            return {k: m.get(k) for k in extras}

        # Persisted on create, and returned so an edit can resubmit them.
        assert stored() == extras

        saved = client.get(f"/api/analysis/{analysis_id}").json()["data"]
        replaced = client.put(
            f"/api/analysis/{analysis_id}/inputs",
            json={
                "name": saved["name"],
                "measurements": [
                    {
                        "id": m["id"], "sample_name": m["sample_name"],
                        "age_bp": m["age_bp"], "error_1sigma": m["error_1sigma"],
                        "material_type": m["material_type"], "sample_type": m["sample_type"],
                        "curve_id": m["curve_id"],
                        "reservoir_age": m.get("reservoir_age"),
                        "depth_cm": m.get("depth_cm"),
                        "location": m.get("location"),
                        "notes": m.get("notes"),
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
        assert stored() == extras

    @pytest.mark.slow
    def test_a_date_in_no_context_still_gets_a_phase(self, client):
        """
        Saved with neither groups nor a tree, every date is "loose".
        _tree_from_request gives them a phase of their own, but the stored
        element_id used to be derived from the context map instead -- which
        has no entry for a loose date, so the date pointed at nothing and
        the next edit was rejected as "not assigned to any model element".
        """
        tag = f"ls{next(_ROUND_TRIP_IDS)}"
        created = client.post(
            "/api/analysis/bayesian?wait=true",
            json={
                "name": "Loose dates",
                "measurements": [
                    _measurement(f"{tag}s1", sample_name="Loose one"),
                    _measurement(f"{tag}s2", age_bp=3100, sample_name="Loose two"),
                ],
                # Loose dates are a phase with no Boundary, which is sampled;
                # untempered, since this is about the stored tree.
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1,
                                    "mcmc_temperatures": 1},
                "save": True,
            },
        )
        assert created.status_code == 200, created.text
        analysis_id = created.json()["data"]["id"]

        saved = client.get(f"/api/analysis/{analysis_id}").json()["data"]
        phases = {
            c["id"]: c.get("name") for c in saved["model_tree"]["children"]
            if c["element_type"] == "phase"
        }
        for m in saved["measurements"]:
            assert m["element_id"], f"{m['sample_name']} points at no element"
            assert phases.get(m["element_id"]) == "Unassigned dates"

        # Which is what makes the analysis editable at all.
        replaced = client.put(
            f"/api/analysis/{analysis_id}/inputs",
            json={
                "name": saved["name"],
                "measurements": [
                    {"id": m["id"], "sample_name": m["sample_name"], "age_bp": m["age_bp"],
                     "error_1sigma": m["error_1sigma"], "material_type": m["material_type"],
                     "sample_type": m["sample_type"], "curve_id": m["curve_id"],
                     "element_id": m["element_id"]}
                    for m in saved["measurements"]
                ],
                "groups": [], "relationships": [],
                "model_tree": saved["model_tree"],
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
            },
        )
        assert replaced.status_code == 200, replaced.text

    def test_a_reservoir_offset_survives_and_is_applied(self, client):
        """
        Delta-R changes the date, so losing it across an edit would silently
        change results -- and applying it in one code path but not another
        would make the modelled and unmodelled figures disagree.
        """
        tag = f"dr{next(_ROUND_TRIP_IDS)}"
        tree = {
            "id": f"{tag}root", "element_type": "sequence", "name": "Site", "children": [
                {"id": f"{tag}b0", "element_type": "boundary", "name": "Start", "children": []},
                {"id": f"{tag}p1", "element_type": "phase", "name": "Only", "children": []},
                {"id": f"{tag}b1", "element_type": "boundary", "name": "End", "children": []},
            ],
        }

        def build(**offset):
            return client.post(
                "/api/analysis/bayesian?wait=true",
                json={
                    "name": "Reservoir offset",
                    "measurements": [
                        _measurement(f"{tag}{offset.get('delta_r', 'no')}s", sample_name="Shell",
                                     sample_type="marine", curve_id="marine20",
                                     element_id=f"{tag}p1", **offset),
                    ],
                    "model_tree": tree,
                    "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                    "save": False,
                },
            )

        # Applied: a positive offset dates the sample younger.
        plain = build()
        corrected = build(delta_r=400, delta_r_error=50)
        assert plain.status_code == corrected.status_code == 200, corrected.text
        plain_median = plain.json()["data"]["results"][f"{tag}nos"]["posterior_median"]
        corrected_median = corrected.json()["data"]["results"][f"{tag}400s"]["posterior_median"]
        assert corrected_median < plain_median - 100, (plain_median, corrected_median)

        # Persisted, returned, and preserved by an edit.
        saved_resp = client.post(
            "/api/analysis/bayesian?wait=true",
            json={
                "name": "Reservoir offset saved",
                "measurements": [
                    _measurement(f"{tag}s1", sample_name="Shell", sample_type="marine",
                                 curve_id="marine20", element_id=f"{tag}p1x",
                                 delta_r=400, delta_r_error=50),
                ],
                "model_tree": {**tree, "id": f"{tag}root2",
                               "children": [{**c, "id": c["id"] + "x"} for c in tree["children"]]},
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        )
        assert saved_resp.status_code == 200, saved_resp.text
        analysis_id = saved_resp.json()["data"]["id"]

        def stored():
            m = client.get(f"/api/analysis/{analysis_id}").json()["data"]["measurements"][0]
            return m["delta_r"], m["delta_r_error"]

        assert stored() == (400, 50)

        saved = client.get(f"/api/analysis/{analysis_id}").json()["data"]
        replaced = client.put(
            f"/api/analysis/{analysis_id}/inputs",
            json={
                "name": saved["name"],
                "measurements": [
                    {"id": m["id"], "sample_name": m["sample_name"], "age_bp": m["age_bp"],
                     "error_1sigma": m["error_1sigma"], "material_type": m["material_type"],
                     "sample_type": m["sample_type"], "curve_id": m["curve_id"],
                     "delta_r": m["delta_r"], "delta_r_error": m["delta_r_error"],
                     "element_id": m["element_id"]}
                    for m in saved["measurements"]
                ],
                "groups": [], "relationships": [],
                "model_tree": saved["model_tree"],
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
            },
        )
        assert replaced.status_code == 200, replaced.text
        assert stored() == (400, 50)

    def test_an_offset_without_its_uncertainty_is_rejected(self, client):
        response = client.post(
            "/api/analysis/calibrate",
            json={"sample_name": "Shell", "age_bp": 3000, "error_1sigma": 30,
                  "curve_id": "marine20", "sample_type": "marine",
                  "delta_r": 400, "save": False},
        )
        assert response.status_code == 422
        assert "delta_r_error" in response.text

    def test_a_rerun_reproduces_a_corrected_calibration(self, client):
        """
        The offset has to be stored, not just applied. A rerun recalibrates
        from the saved row, so an offset that was applied on the first run
        but never persisted would quietly undo itself -- the date would move
        by centuries with nothing to show why.
        """
        created = client.post(
            "/api/analysis/calibrate",
            json={"sample_name": "Shell", "age_bp": 3000, "error_1sigma": 30,
                  "material_type": "marine shell", "sample_type": "marine",
                  "curve_id": "marine20", "delta_r": 400, "delta_r_error": 50,
                  "save": True},
        )
        assert created.status_code == 200, created.text
        analysis_id = created.json()["data"]["id"]
        first_run = created.json()["data"]["posterior_median"]

        stored = client.get(f"/api/analysis/{analysis_id}").json()["data"]["measurements"][0]
        assert (stored["delta_r"], stored["delta_r_error"]) == (400, 50)

        assert client.post(f"/api/analysis/{analysis_id}/rerun?wait=true").status_code == 200
        rerun = client.get(f"/api/analysis/{analysis_id}").json()["data"]["results"][0]
        assert rerun["posterior_median"] == pytest.approx(first_run, rel=1e-6)

        # And the correction really was in force -- an uncorrected date sits
        # centuries older.
        plain = client.post(
            "/api/analysis/calibrate",
            json={"sample_name": "Shell", "age_bp": 3000, "error_1sigma": 30,
                  "sample_type": "marine", "curve_id": "marine20", "save": False},
        )
        assert plain.json()["data"]["posterior_median"] > first_run + 100


# Model element ids are a global primary key and the test database is shared
# across the session, so each tree built here needs its own prefix.
_DIAGNOSTIC_IDS = itertools.count(1)


class TestRerunKeepsDiagnostics:
    """
    A rerun must produce the same columns a fresh run does.

    It did not: the two paths built their AnalysisResult rows independently,
    and when agreement_index and outlier_probability were added only the
    fresh one got them -- so rerunning an analysis silently blanked both in
    every export. Worse for the outlier probability, which also needs its
    PRIOR to survive: that was never persisted on the sample at all, so a
    rerun could not have recomputed it even with the row fixed.

    Asserted through the CSV rather than the database, because that is where
    the loss was visible and what people actually read.
    """

    def _tag(self, name):
        return f'rk{next(_DIAGNOSTIC_IDS)}{name}'

    def _create(self, client, tag, outlier_prior):
        dates = [(f'{tag}_a', 3000), (f'{tag}_b', 3020), (f'{tag}_c', 3040),
                 (f'{tag}_d', 3060), (f'{tag}_e', 3600)]
        created = client.post('/api/analysis/bayesian?wait=true', json={
            'name': tag,
            'measurements': [
                {'id': i, 'sample_name': i, 'age_bp': a, 'error_1sigma': 30,
                 'element_id': f'{tag}_p', 'outlier_prior': outlier_prior}
                for i, a in dates
            ],
            'model_tree': {
                'id': f'{tag}_s', 'element_type': 'sequence', 'name': 'Site',
                'tpq_age_bp': None, 'taq_age_bp': None, 'children': [
                    {'id': f'{tag}_b0', 'element_type': 'boundary', 'name': 'Start',
                     'tpq_age_bp': None, 'taq_age_bp': None, 'children': []},
                    {'id': f'{tag}_p', 'element_type': 'phase', 'name': 'Phase',
                     'tpq_age_bp': None, 'taq_age_bp': None, 'children': []},
                    {'id': f'{tag}_b1', 'element_type': 'boundary', 'name': 'End',
                     'tpq_age_bp': None, 'taq_age_bp': None, 'children': []},
                ],
            },
            'bayesian_config': {'mcmc_samples': 300, 'mcmc_tune': 200, 'mcmc_chains': 1},
            'save': True,
        })
        assert created.status_code == 200, created.text
        return created.json()['data']['id']

    def _columns(self, client, analysis_id):
        rows = _results_block(client, analysis_id)
        header, data = rows[0], rows[1:]
        kind, name = header.index('Row Type'), header.index('Name')
        out = {}
        for column in ('Agreement %', 'Outlier %'):
            out[column] = {r[name]: r[header.index(column)]
                           for r in data if r[kind] == 'Date'}
        return out

    def test_agreement_survives_a_rerun(self, client):
        tag = self._tag('ag')
        analysis_id = self._create(client, tag, outlier_prior=0.0)

        before = self._columns(client, analysis_id)['Agreement %']
        assert before and all(v != '' for v in before.values()), before

        assert client.post(f'/api/analysis/{analysis_id}/rerun?wait=true').status_code == 200
        after = self._columns(client, analysis_id)['Agreement %']
        assert set(after) == set(before)
        assert all(v != '' for v in after.values()), after
        for name in before:
            assert float(after[name]) == pytest.approx(float(before[name]), abs=2.0)

    def test_outlier_probability_survives_a_rerun(self, client):
        """
        This one also proves the prior itself is persisted: the rerun rebuilds
        the model from the stored sample rows, so a blank here means the
        outlier model never ran the second time.
        """
        tag = self._tag('ol')
        analysis_id = self._create(client, tag, outlier_prior=0.05)

        before = self._columns(client, analysis_id)['Outlier %']
        assert before and all(v != '' for v in before.values()), before
        # The 3600 BP date among four at ~3000 is the one that should stand out.
        assert float(before[f'{tag}_e']) > max(
            float(v) for k, v in before.items() if k != f'{tag}_e')

        assert client.post(f'/api/analysis/{analysis_id}/rerun?wait=true').status_code == 200
        after = self._columns(client, analysis_id)['Outlier %']
        assert all(v != '' for v in after.values()), after
        for name in before:
            assert float(after[name]) == pytest.approx(float(before[name]), abs=5.0)

    def test_no_outlier_model_leaves_the_column_blank_both_times(self, client):
        """Blank means "not applied", and must not become 0.0 on a rerun."""
        tag = self._tag('no')
        analysis_id = self._create(client, tag, outlier_prior=0.0)
        assert all(v == '' for v in self._columns(client, analysis_id)['Outlier %'].values())
        assert client.post(f'/api/analysis/{analysis_id}/rerun?wait=true').status_code == 200
        assert all(v == '' for v in self._columns(client, analysis_id)['Outlier %'].values())


class TestEditingKeepsTheOutlierSetting:
    """
    Editing an analysis must not switch its outlier model off.

    It did. Three endpoints build a RadiocarbonSample row -- create, replace
    inputs, and duplicate -- and only the first was given the outlier fields,
    so an edit wrote them back as null and the export's Outlier column went
    blank. The same three had already drifted once over the reservoir
    offset, which is why they now share one builder.

    Asserted on both the stored inputs and the exported column: the setting
    surviving in the database is no use if the result computed from it does
    not come back.
    """

    def _tag(self):
        return f'eo{next(_DIAGNOSTIC_IDS)}'

    def _payload(self, tag, model='charcoal'):
        return {
            'name': tag,
            'measurements': [
                {'id': f'{tag}_{i}', 'sample_name': f'S{i}', 'age_bp': age,
                 'error_1sigma': 30, 'element_id': f'{tag}_p',
                 'outlier_prior': 0.05, 'outlier_model': model}
                for i, age in enumerate((3000, 3030, 3600))
            ],
            'model_tree': {
                'id': f'{tag}_s', 'element_type': 'sequence', 'name': 'Site',
                'tpq_age_bp': None, 'taq_age_bp': None, 'children': [
                    {'id': f'{tag}_b0', 'element_type': 'boundary', 'name': 'Start',
                     'tpq_age_bp': None, 'taq_age_bp': None, 'children': []},
                    {'id': f'{tag}_p', 'element_type': 'phase', 'name': 'Phase',
                     'tpq_age_bp': None, 'taq_age_bp': None, 'children': []},
                    {'id': f'{tag}_b1', 'element_type': 'boundary', 'name': 'End',
                     'tpq_age_bp': None, 'taq_age_bp': None, 'children': []},
                ],
            },
            'groups': [], 'relationships': [],
            'bayesian_config': {'mcmc_samples': 300, 'mcmc_tune': 200, 'mcmc_chains': 1},
        }

    def _outlier_column(self, client, analysis_id):
        rows = _results_block(client, analysis_id)
        header, data = rows[0], rows[1:]
        column, name, kind = (header.index('Outlier %'), header.index('Name'),
                              header.index('Row Type'))
        return {r[name]: r[column] for r in data if r[kind] == 'Date'}

    def test_replacing_inputs_keeps_it(self, client):
        tag = self._tag()
        payload = dict(self._payload(tag), save=True)
        created = client.post('/api/analysis/bayesian?wait=true', json=payload)
        assert created.status_code == 200, created.text
        analysis_id = created.json()['data']['id']
        before = self._outlier_column(client, analysis_id)
        assert all(v != '' for v in before.values()), before

        # The edit page resubmits the payload it was given and re-runs.
        replaced = client.put(f'/api/analysis/{analysis_id}/inputs', json=self._payload(tag))
        assert replaced.status_code == 200, replaced.text
        assert client.post(f'/api/analysis/{analysis_id}/rerun?wait=true').status_code == 200

        stored = client.get(f'/api/analysis/{analysis_id}').json()['data']['measurements']
        assert all(m['outlier_prior'] == 0.05 for m in stored), stored
        assert all(m['outlier_model'] == 'charcoal' for m in stored), stored

        after = self._outlier_column(client, analysis_id)
        assert all(v != '' for v in after.values()), after
        for name in before:
            assert float(after[name]) == pytest.approx(float(before[name]), abs=5.0)

    def test_duplicating_keeps_it(self, client):
        """The third copy of the row builder, and the one nothing else covers."""
        tag = self._tag()
        created = client.post('/api/analysis/bayesian?wait=true', json=dict(self._payload(tag), save=True))
        assert created.status_code == 200, created.text
        source_id = created.json()['data']['id']

        duplicated = client.post(f'/api/analysis/{source_id}/duplicate')
        assert duplicated.status_code == 200, duplicated.text
        copy_id = duplicated.json()['data']['id']

        copied = client.get(f'/api/analysis/{copy_id}').json()['data']['measurements']
        assert copied, 'the duplicate has no measurements'
        assert all(m['outlier_prior'] == 0.05 for m in copied), copied
        assert all(m['outlier_model'] == 'charcoal' for m in copied), copied


def test_get_analysis_reports_diagnostics_on_each_result(client):
    """
    GET /analysis/{id} is what the results page reads. Agreement and
    outlier probability were computed and persisted correctly but never
    added to this endpoint's response shape, so the page had no way to see
    them short of the CSV -- reproducing the second half of the report this
    guards against.
    """
    tag = f'getdiag{next(_DIAGNOSTIC_IDS)}'
    payload = {
        'name': tag,
        'measurements': [
            {'id': f'{tag}_{i}', 'sample_name': f'S{i}', 'age_bp': age,
             'error_1sigma': 30, 'element_id': f'{tag}_p',
             'outlier_prior': 0.05, 'outlier_model': 'general'}
            for i, age in enumerate((3000, 3030, 3600))
        ],
        'model_tree': {
            'id': f'{tag}_s', 'element_type': 'sequence', 'name': 'Site',
            'tpq_age_bp': None, 'taq_age_bp': None, 'children': [
                {'id': f'{tag}_b0', 'element_type': 'boundary', 'name': 'Start',
                 'tpq_age_bp': None, 'taq_age_bp': None, 'children': []},
                {'id': f'{tag}_p', 'element_type': 'phase', 'name': 'Phase',
                 'tpq_age_bp': None, 'taq_age_bp': None, 'children': []},
                {'id': f'{tag}_b1', 'element_type': 'boundary', 'name': 'End',
                 'tpq_age_bp': None, 'taq_age_bp': None, 'children': []},
            ],
        },
        'bayesian_config': {'mcmc_samples': 300, 'mcmc_tune': 200, 'mcmc_chains': 1},
        'save': True,
    }
    created = client.post('/api/analysis/bayesian?wait=true', json=payload)
    assert created.status_code == 200, created.text
    analysis_id = created.json()['data']['id']

    results = client.get(f'/api/analysis/{analysis_id}').json()['data']['results']
    assert len(results) == 3
    for r in results:
        assert 'agreement_index' in r and r['agreement_index'] is not None
        assert 'outlier_probability' in r and r['outlier_probability'] is not None
    # The 3600 BP date against two at ~3000-3030 is the one that should stand out.
    by_name = {r['sample_name']: r for r in results}
    assert by_name['S2']['outlier_probability'] > by_name['S0']['outlier_probability']


def test_outlier_values_survive_a_rerun_of_an_ordered_sibling_model(client):
    """
    The reported symptom, on the model shape that produced it.

    Two Sequences ordered directly under one Sequence with no Boundary
    between them is not a chain, so this used to fall through to MCMC --
    which never applied the outlier model at all, so the Outlier column came
    back empty no matter how the dates were configured, on the first run and
    on every rerun after it. Nothing about persistence was wrong; the solver
    simply did not implement the feature.
    """
    tag = f'ordsib{next(_DIAGNOSTIC_IDS)}'
    tree = {
        'id': f'{tag}_s', 'element_type': 'sequence', 'name': 'Site',
        'tpq_age_bp': None, 'taq_age_bp': None, 'children': [
            {'id': f'{tag}_b0', 'element_type': 'boundary', 'name': 'Start',
             'tpq_age_bp': None, 'taq_age_bp': None, 'children': []},
            {'id': f'{tag}_q', 'element_type': 'sequence', 'name': 'Early',
             'tpq_age_bp': None, 'taq_age_bp': None, 'children': [
                 {'id': f'{tag}_p1', 'element_type': 'phase', 'name': 'P1',
                  'tpq_age_bp': None, 'taq_age_bp': None, 'children': []}]},
            {'id': f'{tag}_r', 'element_type': 'sequence', 'name': 'Late',
             'tpq_age_bp': None, 'taq_age_bp': None, 'children': [
                 {'id': f'{tag}_p2', 'element_type': 'phase', 'name': 'P2',
                  'tpq_age_bp': None, 'taq_age_bp': None, 'children': []}]},
            {'id': f'{tag}_b1', 'element_type': 'boundary', 'name': 'End',
             'tpq_age_bp': None, 'taq_age_bp': None, 'children': []},
        ],
    }
    measurements = [
        {'id': f'{tag}_a', 'sample_name': 'A', 'age_bp': 3200, 'error_1sigma': 30,
         'element_id': f'{tag}_p1', 'outlier_prior': 0.05, 'outlier_model': 'general'},
        {'id': f'{tag}_b', 'sample_name': 'B', 'age_bp': 3230, 'error_1sigma': 30,
         'element_id': f'{tag}_p1', 'outlier_prior': 0.05, 'outlier_model': 'general'},
        {'id': f'{tag}_c', 'sample_name': 'C', 'age_bp': 3000, 'error_1sigma': 30,
         'element_id': f'{tag}_p2', 'outlier_prior': 0.05, 'outlier_model': 'general'},
    ]
    created = client.post('/api/analysis/bayesian?wait=true', json={
        'name': tag, 'measurements': measurements, 'model_tree': tree,
        # Sampled, the model not being a chain; untempered, since what is
        # tested is that the values survive a rerun.
        'bayesian_config': {'mcmc_samples': 300, 'mcmc_tune': 200, 'mcmc_chains': 1,
                            'mcmc_temperatures': 1},
        'save': True,
    })
    assert created.status_code == 200, created.text
    analysis_id = created.json()['data']['id']

    def outlier_column():
        rows = _results_block(client, analysis_id)
        header, data = rows[0], rows[1:]
        col, name, kind = (header.index('Outlier %'), header.index('Name'),
                           header.index('Row Type'))
        return {r[name]: r[col] for r in data if r[kind] == 'Date'}

    before = outlier_column()
    assert len(before) == 3 and all(v != '' for v in before.values()), before
    # Populated is not enough -- MCMC could be made to fill the column too,
    # and did, with numbers that could not tell an outlier from a sound date
    # (43.5% against 45.6% on a case where the exact answer was 48% and 3%).
    # So this asserts the values are USABLE: three consistent dates must all
    # sit near their 5% prior rather than scattered across it.
    assert max(float(v) for v in before.values()) < 20.0, before

    assert client.post(f'/api/analysis/{analysis_id}/rerun?wait=true').status_code == 200
    after = outlier_column()
    assert all(v != '' for v in after.values()), after
    for name in before:
        assert float(after[name]) == pytest.approx(float(before[name]), abs=5.0)

    # ...and the results page reads the same numbers off the GET.
    results = client.get(f'/api/analysis/{analysis_id}').json()['data']['results']
    assert all(r['outlier_probability'] is not None for r in results), results


_CONVERT_IDS = itertools.count(1)


class TestChangingAnAnalysisType:
    """
    Editing an analysis and changing its type has to convert THAT analysis.

    The bug this pins: deploying a project's dates always creates a pending
    Bayesian analysis and drops the user into its editor, so choosing Group
    Calibration there was the ordinary way to reach the type switch. The edit
    posted a brand-new analysis instead of replacing the one being edited,
    leaving two analyses with the same name and the original stuck at
    'pending' for ever.
    """

    def _deployed(self, client, name="Deployed dates"):
        """A project, two collected dates, and the pending Bayesian analysis
        deploying them produces -- the exact state the bug started from."""
        tag = f"cv{next(_CONVERT_IDS)}"
        project_id = client.post(
            "/api/projects", json={"name": f"Project {tag}"}).json()["data"]["id"]
        created = client.post(
            f"/api/projects/{project_id}/samples",
            json={"samples": [
                {"sample_name": f"{tag}-{index}", "age_bp": age, "error_1sigma": 30,
                 "material_type": "charcoal", "sample_type": "terrestrial",
                 "curve_id": "intcal20"}
                for index, age in enumerate((3000, 2900))
            ]},
        )
        assert created.status_code in (200, 201), created.text
        sample_ids = [s["id"] for s in created.json()["data"]["items"]]

        deployed = client.post(
            f"/api/projects/{project_id}/deploy",
            json={"name": name, "sample_ids": sample_ids, "analysis_type": "bayesian"},
        )
        assert deployed.status_code == 200, deployed.text
        return project_id, deployed.json()["data"]["id"], sample_ids

    def _measurements(self, client, analysis_id):
        """The dates as the edit page resubmits them: read back from the
        analysis, with no context and no phase, which is what a group
        calibration has."""
        fetched = client.get(f"/api/analysis/{analysis_id}").json()["data"]
        return [
            {"id": m["id"], "sample_name": m["sample_name"], "age_bp": m["age_bp"],
             "error_1sigma": m["error_1sigma"], "material_type": m["material_type"],
             "sample_type": m["sample_type"], "curve_id": m["curve_id"],
             "group_id": None, "element_id": None}
            for m in fetched["measurements"]
        ]

    def test_switching_to_group_calibration_converts_it_in_place(self, client):
        project_id, analysis_id, _ = self._deployed(client, name="Converted")
        before = client.get(f"/api/analysis?project_id={project_id}").json()["data"]
        assert before["total"] == 1

        replaced = client.put(
            f"/api/analysis/{analysis_id}/inputs",
            json={"name": "Converted", "analysis_type": "group_calibration",
                  "measurements": self._measurements(client, analysis_id),
                  "groups": [], "relationships": [], "project_id": project_id},
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["data"]["analysis_type"] == "group_calibration"

        # wait=true so the run finishes inside the request; the page uses the
        # queued form and polls, which is the same work either way.
        ran = client.post(f"/api/analysis/{analysis_id}/rerun?wait=true")
        assert ran.status_code == 200, ran.text

        after = client.get(f"/api/analysis?project_id={project_id}").json()["data"]
        assert after["total"] == 1, (
            "editing an analysis must not leave a second one behind: "
            f"{[(a['name'], a['status']) for a in after['items']]}"
        )
        fetched = client.get(f"/api/analysis/{analysis_id}").json()["data"]
        assert fetched["analysis_type"] == "group_calibration"
        assert fetched["status"] == "completed"
        assert len(fetched["results"]) == 2, "both dates must still be calibrated"
        # A group calibration has no model, so nothing may be left claiming it
        # sits in one.
        assert fetched["model_tree"] is None
        assert all(m["element_id"] is None for m in fetched["measurements"])

    def test_a_single_calibration_of_several_dates_is_stored_as_a_group(self, client):
        """
        'calibration' means ONE date on its own, so several of them are
        stored as the group calibration they actually are -- the same
        promotion deploying makes. Silently keeping the label would leave
        the results page describing an analysis it is not.
        """
        project_id, analysis_id, _ = self._deployed(client)
        replaced = client.put(
            f"/api/analysis/{analysis_id}/inputs",
            json={"name": "Two dates", "analysis_type": "calibration",
                  "measurements": self._measurements(client, analysis_id),
                  "groups": [], "relationships": [], "project_id": project_id},
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["data"]["analysis_type"] == "group_calibration"

    def test_omitting_the_type_leaves_it_alone(self, client):
        """An ordinary same-type edit, and every older client's request."""
        _project_id, analysis_id, _ = self._deployed(client)
        replaced = client.put(
            f"/api/analysis/{analysis_id}/inputs",
            json={"name": "Untouched",
                  "measurements": self._measurements(client, analysis_id),
                  "groups": [], "relationships": []},
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["data"]["analysis_type"] == "bayesian"
