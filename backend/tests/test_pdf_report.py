"""
Tests for the PDF report: services/pdf_report.py and the route that serves
it.

A PDF is awkward to assert on, and the tempting response is to check only
that some bytes came back starting with "%PDF-". That pins nothing: a
report that silently lost its results table, cited the wrong curve, or
printed a column of blanks would pass. So `pdf_text` below pulls the actual
text back out of the file, using nothing but the standard library, and the
tests read the report the way a reader would.

What is deliberately NOT asserted here is layout -- where a table breaks
across pages, how wide a column came out. That is ReportLab's business and
it changes with the data; pinning it would produce tests that fail on a
longer sample name.
"""
import base64
import re
import zlib

from app.services import pdf_report


# --- reading a PDF back, with the standard library only ------------------

def pdf_text(data: bytes) -> str:
    """
    The text of a PDF, as one string.

    ReportLab writes page content through /ASCII85Decode then
    /FlateDecode, both of which the standard library can undo, and lays
    text down as `(...) Tj` operators. Image streams are skipped: a
    200 dpi figure decompresses to megabytes of pixels with no text in it.

    Words can be split across several operators -- a bold run inside a
    sentence becomes three of them -- so the result is the operators joined
    by spaces, and assertions here look for phrases short enough not to
    straddle a split.
    """
    chunks = []
    for match in re.finditer(rb'(<<[^<>]*(?:<<.*?>>)?[^<>]*>>)\s*stream\r?\n(.*?)endstream',
                             data, re.S):
        header, stream = match.group(1), match.group(2)
        if b'/Image' in header:
            continue
        raw = stream.rstrip(b'\r\n')
        try:
            raw = base64.a85decode(raw, adobe=True)
        except Exception:
            pass
        try:
            chunks.append(zlib.decompress(raw).decode('latin-1'))
        except Exception:
            continue

    content = '\n'.join(chunks)
    # Unescape the few sequences ReportLab writes inside a text string.
    pieces = [
        piece.replace('\\(', '(').replace('\\)', ')').replace('\\\\', '\\')
        for piece in re.findall(r'\((.*?)(?<!\\)\) Tj', content)
    ]
    return ' '.join(pieces)


def pdf_page_count(data: bytes) -> int:
    """How many pages the document has. Page objects are not compressed,
    so this reads straight out of the bytes."""
    return data.count(b'/Type /Page') - data.count(b'/Type /Pages')


# --- analyses to report on ----------------------------------------------

def _calibration(client, **overrides) -> str:
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


_MODELLED: dict = {}


def _modelled(client) -> str:
    """
    A Bayesian analysis with a real structure -- two phases inside a
    sequence, a Boundary either side of each -- created once and shared,
    for the reason test_exports.py shares its own: element ids are a global
    primary key, so posting the same tree twice collides, and the solver run
    is the slowest thing in the file.
    """
    if _MODELLED.get("id"):
        return _MODELLED["id"]

    tree = {
        "id": "proot", "element_type": "sequence", "name": "Site", "children": [
            {"id": "pb0", "element_type": "boundary", "name": "Start", "children": []},
            {"id": "pp1", "element_type": "phase", "name": "Lower", "children": []},
            {"id": "pb1", "element_type": "boundary", "name": "Mid", "children": []},
            {"id": "pp2", "element_type": "phase", "name": "Upper", "children": []},
            {"id": "pb2", "element_type": "boundary", "name": "End", "children": []},
        ],
    }
    created = client.post(
        "/api/analysis/bayesian",
        json={
            "name": "Pdf structure",
            "measurements": [
                {"id": "pa", "sample_name": "Lower charcoal", "age_bp": 3300,
                 "error_1sigma": 30, "element_id": "pp1"},
                {"id": "pb", "sample_name": "Upper charcoal", "age_bp": 3000,
                 "error_1sigma": 30, "element_id": "pp2"},
            ],
            "model_tree": tree,
            "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
            "save": True,
        },
    )
    assert created.status_code == 200, created.text
    _MODELLED["id"] = created.json()["data"]["id"]
    return _MODELLED["id"]


def _pdf(client, analysis_id: str, query: str = "") -> bytes:
    response = client.get(f"/api/results/{analysis_id}/pdf{query}")
    assert response.status_code == 200, response.text
    return response.content


# --- the route -----------------------------------------------------------

class TestPdfRoute:
    def test_serves_a_pdf(self, client):
        analysis_id = _calibration(client)
        response = client.get(f"/api/results/{analysis_id}/pdf")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content.startswith(b"%PDF-")

    def test_download_is_named_after_the_analysis(self, client):
        analysis_id = _calibration(client)
        response = client.get(f"/api/results/{analysis_id}/pdf")
        disposition = response.headers["content-disposition"]
        assert f"analysis-{analysis_id}-report.pdf" in disposition
        # Distinct from the CSV's filename, so saving both in one directory
        # keeps both.
        assert "-results.csv" not in disposition

    def test_unknown_analysis_404s(self, client):
        assert client.get("/api/results/ghost/pdf").status_code == 404

    def test_dispatcher_delegates_to_the_same_route(self, client):
        """
        POST /export {"format": "pdf"} must hand off rather than render a
        second variant of the report. Byte equality would be too strong --
        nothing in the document is timestamped, but ReportLab embeds a
        document id -- so this compares what the two say.
        """
        analysis_id = _calibration(client)
        posted = client.post(f"/api/results/{analysis_id}/export", json={"format": "pdf"})
        assert posted.status_code == 200
        assert posted.headers["content-type"] == "application/pdf"
        assert pdf_text(posted.content) == pdf_text(_pdf(client, analysis_id))

    def test_dispatcher_reports_a_bad_id_as_a_bad_id(self, client):
        assert client.post("/api/results/ghost/export", json={"format": "pdf"}).status_code == 404


# --- what the report says ------------------------------------------------

class TestReportContent:
    def test_names_the_analysis_and_its_provenance(self, client):
        analysis_id = _calibration(client, sample_name="Pit fill")
        text = pdf_text(_pdf(client, analysis_id))
        assert "Pit fill" in text
        assert "status: completed" in text
        assert "Calibration" in text

    def test_cites_the_curve_it_calibrated_against(self, client):
        """
        A report with no curve on it cannot be checked or cited. The name
        and reference come from the curve registry rather than from a list
        kept in the report module, so an imported curve is cited by its own
        description.
        """
        analysis_id = _calibration(client)
        text = pdf_text(_pdf(client, analysis_id))
        assert "IntCal20" in text
        assert "Reimer" in text

    def test_reports_the_hpd_ranges(self, client):
        analysis_id = _calibration(client)
        text = pdf_text(_pdf(client, analysis_id))
        assert "68% HPD" in text
        assert "95% HPD" in text
        assert "cal BP" in text

    def test_reads_in_the_requested_date_format(self, client):
        analysis_id = _calibration(client)
        text = pdf_text(_pdf(client, analysis_id, "?date_format=bcad"))
        assert "BC/AD" in text
        assert "BC" in text

    def test_an_unrecognised_date_format_falls_back_rather_than_failing(self, client):
        # The reporting convention is a presentation choice; refusing to
        # produce the download over it would be a poor trade. Matches what
        # the CSV export does with the same input.
        analysis_id = _calibration(client)
        text = pdf_text(_pdf(client, analysis_id, "?date_format=klingon"))
        assert "cal BP" in text

    def test_a_pending_analysis_still_produces_a_report(self, client):
        """
        An analysis with no results says so on the page, rather than
        failing the download -- the same answer the CSV gives with its
        "No results" row.

        The row is made directly because every endpoint that creates an
        analysis also runs it; a pending one is what exists between those
        two steps, and after a run that failed.
        """
        from app.database import SessionLocal
        from app.models import Analysis

        db = SessionLocal()
        analysis = Analysis(name="Not run yet", analysis_type="calibration",
                            status="pending")
        db.add(analysis)
        db.commit()
        analysis_id = analysis.id
        db.close()

        data = _pdf(client, analysis_id)
        text = pdf_text(data)
        assert "no results" in text.lower()
        assert pdf_page_count(data) == 1

    def test_a_modelled_run_reports_its_structure_and_boundaries(self, client):
        analysis_id = _modelled(client)
        text = pdf_text(_pdf(client, analysis_id))
        for name in ("Site", "Lower", "Upper", "Start", "Mid", "End",
                     "Lower charcoal", "Upper charcoal"):
            assert name in text, name

    def test_a_modelled_run_states_which_solver_ran(self, client):
        analysis_id = _modelled(client)
        text = pdf_text(_pdf(client, analysis_id))
        assert "Solved by" in text

    def test_a_modelled_run_carries_the_unmodelled_comparison(self, client):
        # The whole point of a Bayesian report: what the date says on its
        # own, beside what the model made of it.
        analysis_id = _modelled(client)
        assert "Unmodelled 95% HPD" in pdf_text(_pdf(client, analysis_id))

    def test_a_plain_calibration_omits_the_modelled_only_columns(self, client):
        """
        Columns are chosen by what a run actually produced. A plain
        calibration has no model, so it has no unmodelled comparison to
        make and no agreement index -- printing those headers over a column
        of blanks would imply the figures were simply missing.
        """
        text = pdf_text(_pdf(client, _calibration(client)))
        assert "Unmodelled 95% HPD" not in text
        assert "Agree %" not in text

    def test_draws_a_figure_per_modelled_parameter(self, client):
        """
        Two dates and three Boundaries, each with a posterior worth
        drawing.

        Counted through the captions rather than through the PDF's image
        objects: ReportLab writes a shared soft mask alongside the figures,
        so the image count is one more than the number of plots, which is
        an implementation detail of the library and not something this
        report should be pinned to.
        """
        analysis_id = _modelled(client)
        text = pdf_text(_pdf(client, analysis_id))
        assert "Posterior Distributions" in text
        for name in ("Lower charcoal", "Upper charcoal", "Start", "Mid", "End"):
            # Once in the results table, once captioning its figure.
            assert text.count(name) >= 2, name

    def test_every_page_says_which_analysis_it_belongs_to(self, client):
        """
        A report is quoted from and photocopied page by page, so a page
        that has drifted from its title page has to still identify itself.
        """
        analysis_id = _modelled(client)
        data = _pdf(client, analysis_id)
        assert pdf_page_count(data) > 1
        assert pdf_text(data).count(analysis_id) == pdf_page_count(data)


class TestContextSpans:
    """
    The per-context modelled start/end -- the one thing the results page
    shows that no export carried until now.

    In practice this is a fallback for analyses stored before model trees
    existed. Every Bayesian run since then persists a tree with explicit
    Boundary elements, and those report the same transitions in the results
    table itself, so both the report and the results page suppress this
    table whenever the model has boundaries of its own.
    """

    def _legacy(self, client) -> str:
        """
        A run whose contexts carry modelled spans but whose model tree has
        been removed -- what an analysis from before trees looks like on
        disk. Nothing creates this state any more, which is exactly why the
        fallback needs a test.
        """
        created = client.post(
            "/api/analysis/bayesian",
            json={
                "name": "Pdf contexts",
                "measurements": [
                    {"id": "ca", "sample_name": "A", "age_bp": 3300,
                     "error_1sigma": 30, "group_id": "cg1"},
                    {"id": "cb", "sample_name": "B", "age_bp": 3250,
                     "error_1sigma": 30, "group_id": "cg1"},
                    {"id": "cc", "sample_name": "C", "age_bp": 3000,
                     "error_1sigma": 30, "group_id": "cg2"},
                    {"id": "cd", "sample_name": "D", "age_bp": 2950,
                     "error_1sigma": 30, "group_id": "cg2"},
                ],
                "groups": [{"id": "cg1", "name": "Occupation"},
                           {"id": "cg2", "name": "Abandonment"}],
                "relationships": [{"from_sample_id": "cg1", "to_sample_id": "cg2",
                                   "relationship": "above",
                                   "from_type": "group", "to_type": "group"}],
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200,
                                    "mcmc_chains": 1},
                "save": True,
            },
        )
        assert created.status_code == 200, created.text
        analysis_id = created.json()["data"]["id"]

        from app.database import SessionLocal
        from app.models import ModelElement

        db = SessionLocal()
        db.query(ModelElement).filter(
            ModelElement.analysis_id == analysis_id
        ).delete(synchronize_session=False)
        db.commit()
        db.close()
        return analysis_id

    def test_reported_when_the_model_has_no_boundaries_of_its_own(self, client):
        text = pdf_text(_pdf(client, self._legacy(client)))
        assert "Context Spans" in text
        assert "Occupation" in text and "Abandonment" in text

    def test_the_dates_still_report_without_a_structure(self, client):
        # No tree means no reading order, so the report falls back to the
        # flat list of dates rather than coming back empty -- the same
        # fallback the CSV makes.
        text = pdf_text(_pdf(client, self._legacy(client)))
        for name in ("A", "B", "C", "D"):
            assert name in text

    def test_suppressed_when_boundaries_already_report_the_transitions(self, client):
        """
        Adjacent contexts SHARE a boundary, so with the Boundaries in the
        results table already, this table would print each transition twice
        -- once as one context's end and again as the next one's start.
        """
        assert "Context Spans" not in pdf_text(_pdf(client, _modelled(client)))


# --- the pieces, directly ------------------------------------------------

class TestColumnSelection:
    def test_a_column_appears_only_when_something_fills_it(self):
        rows = [{'posterior_mean': 3100.0, 'agreement_index': None}]
        keys = [key for key, _, _ in pdf_report._columns(rows, is_bayesian=False,
                                                         has_limiters=False)]
        assert 'mean' in keys
        assert 'agreement' not in keys
        assert 'tpq' not in keys

    def test_limiters_appear_when_the_model_used_them(self):
        keys = [key for key, _, _ in pdf_report._columns([], is_bayesian=False,
                                                          has_limiters=True)]
        assert keys[-2:] == ['tpq', 'taq']

    def test_the_hpd_columns_are_never_dropped(self):
        # They are what the report exists to state; an empty pair of
        # columns is a better answer than silently having no result column
        # at all.
        keys = [key for key, _, _ in pdf_report._columns([], is_bayesian=True,
                                                          has_limiters=False)]
        assert 'hpd68' in keys and 'hpd95' in keys


class TestHpdText:
    def test_prefers_the_multimodal_breakdown(self):
        ranges = [
            {'min': 3103.0, 'max': 3253.0, 'probability_percent': 77.6},
            {'min': 3074.0, 'max': 3098.0, 'probability_percent': 8.0},
        ]
        text = pdf_report._hpd_text(ranges, None, None, 'calBP')
        assert '77.6%' in text and '8.0%' in text
        assert ';' in text

    def test_falls_back_to_the_envelope_for_older_rows(self):
        # Rows written before the breakdown existed carry only min/max.
        # The CSV makes the same fallback, so the two agree on an old
        # analysis rather than one of them going blank.
        text = pdf_report._hpd_text(None, 3081.0, 3234.0, 'calBP')
        assert '3234' in text and '3081' in text

    def test_nothing_at_all_is_blank_not_zero(self):
        assert pdf_report._hpd_text(None, None, None, 'calBP') == ''


class TestCalendarTicks:
    """
    Mirrored from frontend/src/utils/dateFormat.ts. The property worth
    pinning is the one the mirror exists for: ticks land on round years in
    the reader's own convention, not on round cal BP values relabelled.
    """

    def test_cal_bp_leaves_the_ticks_alone(self):
        assert pdf_report._axis_ticks(2000, 3000, 'calBP') is None

    def test_bc_ad_ticks_are_round_calendar_years(self):
        ticks = pdf_report._axis_ticks(2500, 3500, 'bcad')
        assert ticks is not None
        _positions, labels = ticks
        years = [int(re.sub(r'[^0-9]', '', label)) for label in labels]
        assert all(year % 100 == 0 for year in years), labels

    def test_no_year_zero(self):
        ticks = pdf_report._axis_ticks(1900, 2100, 'bcad')
        if ticks is not None:
            assert all(label.strip() not in ('0 BC', 'AD 0') for label in ticks[1])

    def test_a_degenerate_range_produces_no_ticks(self):
        assert pdf_report._axis_ticks(3000, 3000, 'bcad') is None


class TestFigures:
    def test_nothing_to_draw_yields_no_figure(self):
        # A missing or flat distribution costs the report that figure, not
        # the whole download.
        assert pdf_report._posterior_figure(None, None, None, (None, None),
                                            (None, None), None, False, 'calBP') is None
        flat = [[3000, 0.0], [3001, 0.0]]
        assert pdf_report._posterior_figure(flat, None, None, (None, None),
                                            (None, None), None, False, 'calBP') is None

    def test_a_distribution_renders_a_png(self):
        series = [[3000 + i, float(i % 7) / 10] for i in range(60)]
        png = pdf_report._posterior_figure(
            series, [{'min': 3010, 'max': 3030, 'probability_percent': 68.0}],
            None, (None, None), (None, None), None, False, 'bcad',
        )
        assert png is not None
        assert png.startswith(b'\x89PNG')
