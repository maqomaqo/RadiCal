"""
Tests for the results export endpoints -- /export/json and /table.

The CSV body itself is covered by test_model_tree.py's TestCsvExport,
which pins the structure ordering the CSV shares with the results table.
What matters here is what these two routes add on top: /table promises the
same bytes as /export/csv, and /export/json promises each sample's measured
detail merged into its result.

Most of these use a single calibration rather than a Bayesian analysis --
a saved calibration carries a full posterior and HPD breakdown, so it
exercises the whole export assembly without needing an MCMC run.
"""
import csv
import io

from conftest import requires_pymc


def _calibration(client, **overrides) -> str:
    """A saved single-calibration analysis, with real posterior data."""
    body = {
        "sample_name": "Hearth charcoal",
        "age_bp": 3000,
        "error_1sigma": 30,
        "material_type": "charcoal",
        "sample_type": "terrestrial",
        "curve_id": "intcal20",
        "save": True,
    }
    body.update(overrides)
    response = client.post("/api/analysis/calibrate", json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def _csv_rows(client, analysis_id: str, path: str = "export/csv"):
    response = client.get(f"/api/results/{analysis_id}/{path}")
    assert response.status_code == 200, response.text
    rows = list(csv.reader(io.StringIO(response.text)))
    return rows[0], rows[1:]


class TestTableExport:
    def test_table_is_the_same_content_as_export_csv(self, client):
        """
        /table exists only as a backwards-compatible alias, so the thing
        worth pinning is that it hasn't quietly diverged: the two routes
        share _build_csv today, and a caller on the older path should get
        the structure-ordered rows too.
        """
        analysis_id = _calibration(client)
        table = client.get(f"/api/results/{analysis_id}/table")
        exported = client.get(f"/api/results/{analysis_id}/export/csv")
        assert table.status_code == exported.status_code == 200
        assert table.text == exported.text

    def test_table_download_is_named_distinctly(self, client):
        # Same bytes, different filename -- otherwise saving both in one
        # directory would silently overwrite.
        analysis_id = _calibration(client)
        table = client.get(f"/api/results/{analysis_id}/table")
        exported = client.get(f"/api/results/{analysis_id}/export/csv")
        assert f"analysis-{analysis_id}-table.csv" in table.headers["content-disposition"]
        assert f"analysis-{analysis_id}-results.csv" in exported.headers["content-disposition"]
        assert table.headers["content-type"].startswith("text/csv")

    def test_table_carries_the_structure_columns(self, client):
        analysis_id = _calibration(client)
        header, _rows = _csv_rows(client, analysis_id, "table")
        assert header[:5] == ['Order', 'Row Type', 'Depth', 'Structure Path', 'Name']

    def test_table_unknown_analysis_404s(self, client):
        assert client.get("/api/results/ghost/table").status_code == 404


class TestJsonExport:
    def test_each_result_carries_its_samples_measured_detail(self, client):
        """
        The point of this endpoint over the raw results: a consumer gets
        what was measured alongside what was modelled, without a second
        request to join the sample back on.
        """
        analysis_id = _calibration(client, sample_name="Pit fill", material_type="bone")
        data = client.get(f"/api/results/{analysis_id}/export/json").json()

        assert data["success"] is True
        assert len(data["data"]["results"]) == 1
        row = data["data"]["results"][0]
        assert row["sample_name"] == "Pit fill"
        assert row["material_type"] == "bone"
        assert row["age_bp"] == 3000
        assert row["error_1sigma"] == 30
        assert row["sample_type"] == "terrestrial"
        assert row["curve_id"] == "intcal20"
        assert row["sample_id"]

    def test_posterior_and_hpd_breakdown_are_included(self, client):
        analysis_id = _calibration(client)
        row = client.get(f"/api/results/{analysis_id}/export/json").json()["data"]["results"][0]

        for field in ("calibrated_age_bp", "posterior_mean", "posterior_median", "posterior_std"):
            assert row[field] is not None, field
        # The multi-range HPD breakdown, not just the min/max envelope --
        # a calibrated posterior is routinely multimodal, so the envelope
        # alone overstates the range.
        assert row["hpd_95_ranges"], "expected the per-range HPD breakdown"
        assert all(
            {"min", "max", "probability_percent"} <= set(r) for r in row["hpd_95_ranges"]
        )
        assert row["hpd_95_min"] is not None and row["hpd_95_max"] is not None

    def test_envelope_carries_the_analysis_metadata(self, client):
        analysis_id = _calibration(client)
        payload = client.get(f"/api/results/{analysis_id}/export/json").json()["data"]

        assert payload["id"] == analysis_id
        assert payload["analysis_type"] == "calibration"
        assert payload["status"] == "completed"
        assert payload["name"]
        assert payload["created_at"]
        assert "project_id" in payload

    def test_internal_assembly_does_not_leak(self, client):
        """
        _gather_export_rows also hands back the sample lookup the CSV
        writer needs. That is internal plumbing -- ORM objects that would
        not serialise -- so the response must keep naming its keys
        explicitly rather than spreading whatever the assembly returns.
        """
        analysis_id = _calibration(client)
        payload = client.get(f"/api/results/{analysis_id}/export/json").json()["data"]
        assert "samples_by_id" not in payload
        assert set(payload) == {
            "id", "name", "analysis_type", "status", "created_at", "project_id", "results",
        }

    def test_agrees_with_the_csv_on_the_dates(self, client):
        analysis_id = _calibration(client, sample_name="Charcoal A")
        rows = client.get(f"/api/results/{analysis_id}/export/json").json()["data"]["results"]
        _header, csv_rows = _csv_rows(client, analysis_id)

        csv_dates = [r[4] for r in csv_rows if r[1] == 'Date']
        assert csv_dates == [r["sample_name"] for r in rows] == ["Charcoal A"]

    @requires_pymc
    def test_covers_exactly_the_dates_of_a_structured_analysis(self, client):
        """
        JSON is a per-date export: it reports every date the CSV does, and
        none of the structural rows (sequences, phases, boundaries) the CSV
        carries around them.
        """
        tree = {
            "id": "jroot", "element_type": "sequence", "name": "Site", "children": [
                {"id": "jb0", "element_type": "boundary", "name": "Start", "children": []},
                {"id": "jp1", "element_type": "phase", "name": "Lower", "children": []},
                {"id": "jb1", "element_type": "boundary", "name": "Mid", "children": []},
                {"id": "jp2", "element_type": "phase", "name": "Upper", "children": []},
                {"id": "jb2", "element_type": "boundary", "name": "End", "children": []},
            ],
        }
        created = client.post(
            "/api/analysis/bayesian",
            json={
                "name": "Json structure",
                "measurements": [
                    {"id": "ja", "sample_name": "A", "age_bp": 3300, "error_1sigma": 30, "element_id": "jp1"},
                    {"id": "jb", "sample_name": "B", "age_bp": 3000, "error_1sigma": 30, "element_id": "jp2"},
                ],
                "model_tree": tree,
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        )
        assert created.status_code == 200, created.text
        analysis_id = created.json()["data"]["id"]

        rows = client.get(f"/api/results/{analysis_id}/export/json").json()["data"]["results"]
        _header, csv_rows = _csv_rows(client, analysis_id)

        assert sorted(r["sample_name"] for r in rows) == ["A", "B"]
        assert [r[4] for r in csv_rows if r[1] == 'Date'] == ["A", "B"]
        # The structure is the CSV's job; nothing here claims to describe it.
        assert not any(k in rows[0] for k in ("element_id", "path", "row_type"))

    def test_unknown_analysis_404s(self, client):
        assert client.get("/api/results/ghost/export/json").status_code == 404


class TestGraphs:
    """
    /graphs is the plot-data-only endpoint for API consumers; the Results
    page renders from GET /api/analysis/{id} instead. Nothing in the app
    calls it, so it is exactly the sort of route that rots unnoticed.
    """

    def test_returns_one_graph_per_result(self, client):
        analysis_id = _calibration(client)
        payload = client.get(f"/api/results/{analysis_id}/graphs").json()

        assert payload["success"] is True
        graphs = payload["data"]["graphs"]
        assert len(graphs) == 1
        assert set(graphs[0]) == {"sample_id", "probability_distribution"}
        assert graphs[0]["sample_id"]

    def test_distribution_is_a_series_of_age_probability_pairs(self, client):
        analysis_id = _calibration(client)
        distribution = client.get(
            f"/api/results/{analysis_id}/graphs"
        ).json()["data"]["graphs"][0]["probability_distribution"]

        assert distribution, "expected a plottable distribution"
        assert all(len(point) == 2 for point in distribution)
        ages = [age for age, _prob in distribution]
        probabilities = [prob for _age, prob in distribution]
        assert all(isinstance(v, (int, float)) for v in ages + probabilities)
        assert all(prob >= 0 for prob in probabilities)
        # Ascending in calendar age: a consumer plots this straight onto an
        # axis without sorting it first.
        assert ages == sorted(ages)

    def test_matches_the_distribution_the_results_page_renders(self, client):
        """
        The page reads its plot data from GET /api/analysis/{id}, so this
        endpoint is a second path to the same column. Pin them together --
        a consumer plotting from here should not get a different curve to
        the one on screen.
        """
        analysis_id = _calibration(client)
        graphs = client.get(f"/api/results/{analysis_id}/graphs").json()["data"]["graphs"]
        page_results = client.get(f"/api/analysis/{analysis_id}").json()["data"]["results"]

        by_sample = {g["sample_id"]: g["probability_distribution"] for g in graphs}
        assert by_sample == {
            r["sample_id"]: r["probability_distribution"] for r in page_results
        }

    def test_covers_the_same_samples_as_the_json_export(self, client):
        analysis_id = _calibration(client)
        graphs = client.get(f"/api/results/{analysis_id}/graphs").json()["data"]["graphs"]
        rows = client.get(f"/api/results/{analysis_id}/export/json").json()["data"]["results"]

        assert sorted(g["sample_id"] for g in graphs) == sorted(r["sample_id"] for r in rows)

    @requires_pymc
    def test_reports_dates_only_for_a_structured_analysis(self, client):
        # Boundaries are modelled parameters with posteriors of their own,
        # but they are not samples and have no AnalysisResult row, so they
        # never appear here.
        tree = {
            "id": "groot", "element_type": "sequence", "name": "Site", "children": [
                {"id": "gb0", "element_type": "boundary", "name": "Start", "children": []},
                {"id": "gp1", "element_type": "phase", "name": "Only", "children": []},
                {"id": "gb1", "element_type": "boundary", "name": "End", "children": []},
            ],
        }
        created = client.post(
            "/api/analysis/bayesian",
            json={
                "name": "Graphs structure",
                "measurements": [
                    {"id": "ga", "sample_name": "A", "age_bp": 3300, "error_1sigma": 30, "element_id": "gp1"},
                    {"id": "gb", "sample_name": "B", "age_bp": 3200, "error_1sigma": 30, "element_id": "gp1"},
                ],
                "model_tree": tree,
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        )
        assert created.status_code == 200, created.text
        analysis_id = created.json()["data"]["id"]

        graphs = client.get(f"/api/results/{analysis_id}/graphs").json()["data"]["graphs"]
        assert len(graphs) == 2
        assert all(g["probability_distribution"] for g in graphs)

    def test_unknown_analysis_404s(self, client):
        """
        The handler's only use of _gather_export_rows is this check -- the
        result is deliberately discarded. Without it a bad id would return
        an empty graph list and look like a valid, empty analysis.
        """
        assert client.get("/api/results/ghost/graphs").status_code == 404
