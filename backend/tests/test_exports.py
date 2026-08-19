"""
Tests for the results export routes: /table, /export/json, /graphs, and
the POST /export dispatcher. The PDF report has its own file
(test_pdf_report.py), which needs machinery none of these do.

The CSV body itself is covered by test_model_tree.py's TestCsvExport,
which pins the structure ordering the CSV shares with the results table.
What matters here is what these routes add on top of it -- and most of it
is delegation, which is precisely what rots quietly: /table promises the
same bytes as /export/csv, POST /export promises to hand off to the GET
routes rather than render its own variant, /graphs promises the same
distributions the Results page plots, and /export/json promises each
sample's measured detail merged into its result.

Most of these use a single calibration rather than a Bayesian analysis --
a saved calibration carries a full posterior and HPD breakdown, so it
exercises the whole export assembly without needing an MCMC run.
"""
import csv
import io



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


# Cache for the one structured analysis TestJsonStructure shares; see
# TestJsonStructure._structured for why it is built only once.
_STRUCTURED: dict = {}


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
            "id", "name", "analysis_type", "status", "created_at", "project_id",
            "results", "structure",
        }

    def test_agrees_with_the_csv_on_the_dates(self, client):
        analysis_id = _calibration(client, sample_name="Charcoal A")
        rows = client.get(f"/api/results/{analysis_id}/export/json").json()["data"]["results"]
        _header, csv_rows = _csv_rows(client, analysis_id)

        csv_dates = [r[4] for r in csv_rows if r[1] == 'Date']
        assert csv_dates == [r["sample_name"] for r in rows] == ["Charcoal A"]

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


class TestJsonStructure:
    """
    The `structure` view: the same run read in the model's own order, so a
    consumer can reproduce the model and not just the dates. It is the CSV's
    rows without the stringifying, and the tests hold the two together.
    """

    def _structured(self, client):
        """
        One Bayesian analysis with a real structure, created on first use and
        reused by every test in this class.

        Created once rather than per test for two reasons: element ids are a
        global primary key (see conftest.client), so posting this tree twice
        collides on the second test, and the MCMC run is much the slowest
        thing in this file. Every test here only reads it.
        """
        if _STRUCTURED.get("id"):
            return _STRUCTURED["id"]

        tree = {
            "id": "sroot", "element_type": "sequence", "name": "Site", "children": [
                {"id": "sb0", "element_type": "boundary", "name": "Start", "children": []},
                {"id": "sp1", "element_type": "phase", "name": "Lower",
                 "tpq_age_bp": 5000, "children": []},
                {"id": "sb1", "element_type": "boundary", "name": "Mid", "children": []},
                {"id": "sp2", "element_type": "phase", "name": "Upper", "children": []},
                {"id": "sb2", "element_type": "boundary", "name": "End", "children": []},
            ],
        }
        created = client.post(
            "/api/analysis/bayesian",
            json={
                "name": "Json model structure",
                "measurements": [
                    {"id": "sa", "sample_name": "A", "age_bp": 3300, "error_1sigma": 30, "element_id": "sp1"},
                    {"id": "sb", "sample_name": "B", "age_bp": 3000, "error_1sigma": 30, "element_id": "sp2"},
                ],
                "model_tree": tree,
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        )
        assert created.status_code == 200, created.text
        _STRUCTURED["id"] = created.json()["data"]["id"]
        return _STRUCTURED["id"]

    def test_reads_in_model_order(self, client):
        analysis_id = self._structured(client)
        structure = client.get(
            f"/api/results/{analysis_id}/export/json"
        ).json()["data"]["structure"]

        assert [(r["kind"], r["name"]) for r in structure] == [
            ("sequence", "Site"),
            ("boundary", "Start"),
            ("phase", "Lower"),
            ("date", "A"),
            ("boundary", "Mid"),
            ("phase", "Upper"),
            ("date", "B"),
            ("boundary", "End"),
        ]
        assert [r["depth"] for r in structure] == [0, 1, 1, 2, 1, 1, 2, 1]
        assert structure[3]["path"] == "Site > Lower"

    def test_matches_the_csv_row_for_row(self, client):
        """
        The two exports describe one model; if they ever disagree on the
        order or the membership, one of them is lying.
        """
        analysis_id = self._structured(client)
        structure = client.get(
            f"/api/results/{analysis_id}/export/json"
        ).json()["data"]["structure"]
        _header, csv_rows = _csv_rows(client, analysis_id)

        assert [(r["kind"].title(), r["name"], str(r["depth"]), r["path"]) for r in structure] == [
            (row[1], row[4], row[2], row[3]) for row in csv_rows
        ]

    def test_a_date_carries_its_measured_and_modelled_result(self, client):
        analysis_id = self._structured(client)
        structure = client.get(
            f"/api/results/{analysis_id}/export/json"
        ).json()["data"]["structure"]

        date = next(r for r in structure if r["kind"] == "date" and r["name"] == "A")
        assert date["sample_id"]
        assert date["element_id"] == "sp1"
        assert date["result"]["age_bp"] == 3300
        assert date["result"]["posterior_mean"] is not None
        assert date["result"]["hpd_95_ranges"]

    def test_a_boundary_carries_a_posterior_without_the_raw_chain(self, client):
        """
        The stored boundary posterior also holds the full MCMC chain, which
        is tens of thousands of floats and would dwarf the export. The
        summary and the binned distribution are kept; the chain is not.
        """
        analysis_id = self._structured(client)
        structure = client.get(
            f"/api/results/{analysis_id}/export/json"
        ).json()["data"]["structure"]

        boundary = next(r for r in structure if r["kind"] == "boundary")
        posterior = boundary["posterior"]
        assert posterior["posterior_mean"] is not None
        assert posterior["hpd_95_ranges"]
        assert posterior["probability_distribution"], "kept, so a consumer can plot the transition"
        assert "posterior_samples" not in posterior

        # Non-vacuous: the stored value this was trimmed from does carry the
        # chain (GET /api/analysis/{id} returns the column as stored).
        stored = client.get(f"/api/analysis/{analysis_id}").json()["data"]["model_tree"]
        stored_boundary = next(
            c for c in stored["children"] if c["element_type"] == "boundary" and c["posterior"]
        )
        assert "posterior_samples" in stored_boundary["posterior"]

    def test_headings_carry_their_limiters_but_no_result(self, client):
        analysis_id = self._structured(client)
        structure = client.get(
            f"/api/results/{analysis_id}/export/json"
        ).json()["data"]["structure"]

        phase = next(r for r in structure if r["name"] == "Lower")
        assert phase["tpq_age_bp"] == 5000
        assert "result" not in phase and "posterior" not in phase

    def test_is_empty_without_a_stored_structure(self, client):
        # A plain calibration has nothing to read in model order, so the
        # flat `results` list is the whole export.
        analysis_id = _calibration(client)
        payload = client.get(f"/api/results/{analysis_id}/export/json").json()["data"]
        assert payload["structure"] == []
        assert len(payload["results"]) == 1

    def test_a_date_carries_its_unmodelled_calibration(self, client):
        """
        The unconstrained calibration, at both levels the modelled posterior
        reports, so a consumer can compare like with like. Structured as
        ranges rather than the CSV's formatted string -- that is the point of
        having a JSON export.
        """
        analysis_id = self._structured(client)
        payload = client.get(f"/api/results/{analysis_id}/export/json").json()["data"]

        row = next(r for r in payload["results"] if r["sample_name"] == "A")
        for field in ("unmodelled_hpd_68_ranges", "unmodelled_hpd_95_ranges"):
            assert row[field], field
            assert all(
                {"min", "max", "probability_percent"} <= set(r) for r in row[field]
            ), field
        # Genuinely unconstrained, not a copy of the posterior -- narrowing
        # the date is what the model does, so the two must differ.
        assert row["unmodelled_hpd_95_ranges"] != row["hpd_95_ranges"]

        # And it reaches the structure view too, through the same result.
        date = next(r for r in payload["structure"] if r["kind"] == "date" and r["name"] == "A")
        assert date["result"]["unmodelled_hpd_95_ranges"] == row["unmodelled_hpd_95_ranges"]

    def test_the_unmodelled_figure_agrees_with_the_csv(self, client):
        """
        Both exports derive it from one assembly, so they report the same
        calibration -- the CSV formatted, the JSON structured. If these ever
        disagree, one of the two files is misreporting the date.
        """
        analysis_id = self._structured(client)
        rows = client.get(f"/api/results/{analysis_id}/export/json").json()["data"]["results"]
        header, csv_rows = _csv_rows(client, analysis_id)
        column = header.index('Unmodelled 95% HPD')

        def formatted(ranges):
            return '; '.join(
                f"{r['max']:.0f}-{r['min']:.0f} ({r['probability_percent']:.1f}%)" for r in ranges
            )

        by_name = {r["sample_name"]: r for r in rows}
        for csv_row in (r for r in csv_rows if r[1] == 'Date'):
            assert csv_row[column] == formatted(by_name[csv_row[4]]["unmodelled_hpd_95_ranges"])

    def test_a_boundary_has_no_unmodelled_counterpart(self, client):
        # A boundary exists only as a modelled parameter, so there is no
        # unconstrained version of it to report.
        analysis_id = self._structured(client)
        structure = client.get(
            f"/api/results/{analysis_id}/export/json"
        ).json()["data"]["structure"]

        boundary = next(r for r in structure if r["kind"] == "boundary")
        assert "result" not in boundary

    def test_a_plain_calibration_reports_no_unmodelled(self, client):
        # The modelled figures already ARE the unmodelled result there, so
        # repeating them would invite comparing a number with itself.
        analysis_id = _calibration(client)
        row = client.get(f"/api/results/{analysis_id}/export/json").json()["data"]["results"][0]
        assert row["unmodelled_hpd_68_ranges"] is None
        assert row["unmodelled_hpd_95_ranges"] is None


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
        The handler checks the analysis exists before it reads any rows.
        Without that check a bad id would return an empty graph list and
        look like a valid, empty analysis.
        """
        assert client.get("/api/results/ghost/graphs").status_code == 404


class TestPostExport:
    """
    POST /export is a thin dispatcher over the GET routes, so what matters
    is that it really delegates -- same bytes, same headers -- rather than
    growing a second, subtly different rendering of the same data.
    """

    def test_csv_is_the_default_format(self, client):
        analysis_id = _calibration(client)
        posted = client.post(f"/api/results/{analysis_id}/export", json={})
        assert posted.status_code == 200
        assert posted.headers["content-type"].startswith("text/csv")
        assert posted.text == client.get(f"/api/results/{analysis_id}/export/csv").text

    def test_csv_delegates_to_the_get_route(self, client):
        analysis_id = _calibration(client)
        posted = client.post(f"/api/results/{analysis_id}/export", json={"format": "csv"})
        exported = client.get(f"/api/results/{analysis_id}/export/csv")
        assert posted.text == exported.text
        # Same download filename too -- the delegation is the whole route.
        assert posted.headers["content-disposition"] == exported.headers["content-disposition"]

    def test_json_delegates_to_the_get_route(self, client):
        analysis_id = _calibration(client)
        posted = client.post(f"/api/results/{analysis_id}/export", json={"format": "json"})
        assert posted.status_code == 200
        assert posted.json() == client.get(f"/api/results/{analysis_id}/export/json").json()

    def test_unsupported_format_400s_and_names_it(self, client):
        analysis_id = _calibration(client)
        response = client.post(f"/api/results/{analysis_id}/export", json={"format": "xlsx"})
        assert response.status_code == 400
        assert "xlsx" in response.json()["detail"]

    def test_format_matching_is_exact(self, client):
        # "CSV" is not accepted as "csv"; a caller sending the wrong case
        # gets the 400 rather than silently falling through to a default.
        analysis_id = _calibration(client)
        response = client.post(f"/api/results/{analysis_id}/export", json={"format": "CSV"})
        assert response.status_code == 400

    def test_unknown_analysis_404s(self, client):
        for body in ({}, {"format": "csv"}, {"format": "json"}):
            response = client.post("/api/results/ghost/export", json=body)
            assert response.status_code == 404, body

    def test_unknown_analysis_404s_on_every_format(self, client):
        """
        A bad id is reported as a bad id whichever format was asked for --
        every branch resolves the analysis before it renders anything.
        """
        for fmt in ("csv", "json", "pdf"):
            response = client.post("/api/results/ghost/export", json={"format": fmt})
            assert response.status_code == 404, fmt


# The PDF report -- the route, what the document says, and the pieces it is
# assembled from -- lives in tests/test_pdf_report.py, which carries the
# machinery for reading text back out of a PDF.


class TestCsvDateFormat:
    """
    The download reports in whatever format the results page is showing,
    carried on the query string. Formatted columns sit BESIDE the cal BP
    ones rather than replacing them: "1247 BC" reads better than "3197.1"
    but you cannot sort or compute on it.
    """

    def test_cal_bp_is_unchanged_from_no_format_at_all(self, client):
        """
        The numbers already are cal BP, so a formatted counterpart would
        only restate them with a suffix. Nothing is added, and the default
        export stays exactly what it was -- anything already parsing it
        keeps working.
        """
        analysis_id = _calibration(client)
        plain = client.get(f"/api/results/{analysis_id}/export/csv").text
        explicit = client.get(f"/api/results/{analysis_id}/export/csv?date_format=calBP").text
        assert explicit == plain
        assert 'BC/AD' not in plain

    def test_a_chosen_format_adds_a_column_beside_each_calendar_age(self, client):
        analysis_id = _calibration(client)
        base_header, _ = _csv_rows(client, analysis_id)
        header, rows = _csv_rows(client, analysis_id, "export/csv?date_format=bcad")

        # The original columns are untouched and still first.
        assert header[:len(base_header)] == base_header
        assert header[len(base_header):] == [
            'Unmodelled 95% HPD (BC/AD)', 'Posterior Mean (BC/AD)',
            'Posterior Median (BC/AD)', '68% HPD (BC/AD)', '95% HPD (BC/AD)',
            'TPQ BP (BC/AD)', 'TAQ BP (BC/AD)',
        ]

        row = rows[0]
        numeric = row[header.index('Posterior Mean')]
        formatted = row[header.index('Posterior Mean (BC/AD)')]
        # Both present: the numeric one still numeric, the other readable.
        assert float(numeric) > 0
        assert formatted.endswith(' BC') or formatted.startswith('AD ')

    def test_only_calendar_ages_are_converted(self, client):
        """
        Age BP is a radiocarbon age, and the error and standard deviation
        are durations in years. None is a calendar date, so none gets a
        BC/AD counterpart -- converting a duration would be nonsense.
        """
        analysis_id = _calibration(client)
        header, _ = _csv_rows(client, analysis_id, "export/csv?date_format=bcad")
        for never_converted in ('Age BP', '1-Sigma Error', 'Posterior Std'):
            assert f'{never_converted} (BC/AD)' not in header

    def test_bce_ce_is_labelled_as_such(self, client):
        analysis_id = _calibration(client)
        header, rows = _csv_rows(client, analysis_id, "export/csv?date_format=bce_ce")
        assert 'Posterior Mean (BCE/CE)' in header
        value = rows[0][header.index('Posterior Mean (BCE/CE)')]
        assert value.endswith(' BCE') or value.endswith(' CE')

    def test_an_unrecognised_format_still_downloads(self, client):
        # A bad query string shouldn't cost someone their export.
        analysis_id = _calibration(client)
        plain = client.get(f"/api/results/{analysis_id}/export/csv").text
        assert client.get(
            f"/api/results/{analysis_id}/export/csv?date_format=klingon"
        ).text == plain

    def test_the_table_alias_honours_it_too(self, client):
        analysis_id = _calibration(client)
        table = client.get(f"/api/results/{analysis_id}/table?date_format=bcad").text
        exported = client.get(f"/api/results/{analysis_id}/export/csv?date_format=bcad").text
        assert table == exported

    def test_the_post_dispatcher_passes_it_through(self, client):
        analysis_id = _calibration(client)
        posted = client.post(
            f"/api/results/{analysis_id}/export",
            json={"format": "csv", "date_format": "bcad"},
        )
        assert posted.status_code == 200
        assert posted.text == client.get(
            f"/api/results/{analysis_id}/export/csv?date_format=bcad"
        ).text


class TestUnmodelledRangesOnTheAnalysisPayload:
    """
    The results page reads its unmodelled figures from GET /api/analysis/{id}.
    It used to be handed the plottable series and derive the HPD ranges
    itself, from at most 200 points -- so the screen and the CSV disagreed in
    their last digits about the same calibration. The ranges are now computed
    server-side over the curve's full grid and served finished.
    """

    def test_serves_finished_ranges_at_each_level_the_page_offers(self, client):
        from app.api.analysis import REPORTING_CONFIDENCE_LEVELS

        analysis_id = TestJsonStructure()._structured(client)
        results = client.get(f"/api/analysis/{analysis_id}").json()["data"]["results"]
        row = results[0]

        assert [e["confidence"] for e in row["unmodelled_hpd_ranges"]] == list(
            REPORTING_CONFIDENCE_LEVELS
        )
        for entry in row["unmodelled_hpd_ranges"]:
            assert entry["ranges"], entry["confidence"]
            # An HPD set is built from whole grid points, so its coverage
            # lands at or just above the level asked for -- never under.
            total = sum(r["probability_percent"] for r in entry["ranges"])
            assert entry["confidence"] * 100 <= total < entry["confidence"] * 100 + 2

        # The series it used to derive them from is no longer sent: nothing
        # read it any more, and it was 200 points per date.
        assert "unmodelled_probability_distribution" not in row

    def test_the_ranges_are_the_full_grid_calculation(self, client):
        """
        The whole point of moving this server-side. Asserted against the
        curve directly, so a regression to deriving them from the downsampled
        series would show up here rather than as a quiet last-digit drift
        between the screen and the export.
        """
        from app.services.calibration import get_curve

        analysis_id = TestJsonStructure()._structured(client)
        data = client.get(f"/api/analysis/{analysis_id}").json()["data"]
        samples = {m["id"]: m for m in data["measurements"]}

        for row in data["results"]:
            sample = samples[row["sample_id"]]
            curve = get_curve(sample["curve_id"])
            cal_ages, probs = curve.calibrate(
                sample["age_bp"], sample["error_1sigma"],
                sample.get("delta_r"), sample.get("delta_r_error"),
            )
            for entry in row["unmodelled_hpd_ranges"]:
                expected = curve.get_hpd_ranges(cal_ages, probs, entry["confidence"])
                assert entry["ranges"] == expected, entry["confidence"]

    def test_a_plain_calibration_has_none(self, client):
        # Its stored distribution already IS the unmodelled result.
        analysis_id = _calibration(client)
        row = client.get(f"/api/analysis/{analysis_id}").json()["data"]["results"][0]
        assert row["unmodelled_hpd_ranges"] is None
