"""
Tests for the calendar fields on GET /api/analysis.

The list endpoint used to return only bookkeeping -- name, type, status,
sample count -- so a page listing analyses could say that one had finished
but never what it found. `calendar_span` and `intervals` are what let a list
show the answer, and both are derived from the same result rows, so the
span can never disagree with the intervals drawn inside it.

The behaviour worth pinning down is the disjointness. A calibrated HPD is
genuinely multi-modal wherever the curve plateaus, and collapsing one to a
single min/max would claim the probability sitting in the gap between two
peaks -- the same reason the frontend reports every range separately.
"""
import pytest

from app.api.analysis import _analysis_intervals, _analysis_span, _hpd_segments


class _Result:
    """Only the columns the serializer helpers read."""

    def __init__(self, h68=None, h95=None, median=None):
        self.hpd_68_ranges = h68
        self.hpd_95_ranges = h95
        self.posterior_median = median


def _r(lo, hi, pct=100.0):
    return {"min": lo, "max": hi, "probability_percent": pct}


class TestHpdSegments:
    def test_keeps_disjoint_ranges_apart(self):
        # Two peaks either side of a plateau. One [3100, 3360] span would
        # assert probability across the gap that the calibration withheld.
        segments = _hpd_segments([_r(3300, 3360, 13.5), _r(3100, 3270, 81.9)])
        assert segments == [[3300.0, 3360.0], [3100.0, 3270.0]]

    def test_orders_oldest_range_first(self):
        segments = _hpd_segments([_r(100, 200), _r(800, 900), _r(400, 500)])
        assert segments == [[800.0, 900.0], [400.0, 500.0], [100.0, 200.0]]

    def test_treats_missing_ranges_as_nothing_to_draw(self):
        assert _hpd_segments(None) == []
        assert _hpd_segments([]) == []

    def test_skips_a_range_missing_an_edge(self):
        # A half-open range has no width to draw and no honest way to guess one.
        segments = _hpd_segments([_r(100, 200), {"min": None, "max": 300}])
        assert segments == [[100.0, 200.0]]


class TestAnalysisSpan:
    def test_envelopes_every_sample(self):
        results = [
            _Result(h95=[_r(555, 655)]),
            _Result(h95=[_r(600, 780)]),
        ]
        assert _analysis_span(results) == [555.0, 780.0]

    def test_spans_across_a_gap_between_peaks(self):
        results = [_Result(h95=[_r(3300, 3360), _r(3100, 3270)])]
        assert _analysis_span(results) == [3100.0, 3360.0]

    def test_is_none_when_nothing_is_solved(self):
        assert _analysis_span([]) is None
        assert _analysis_span([_Result()]) is None


class TestAnalysisIntervals:
    def test_carries_both_bands_and_the_point_estimate(self):
        intervals = _analysis_intervals(
            [_Result(h68=[_r(600, 640)], h95=[_r(580, 660)], median=618.0)]
        )
        assert intervals == [
            {"h68": [[600.0, 640.0]], "h95": [[580.0, 660.0]], "median": 618.0}
        ]

    def test_omits_results_with_no_95_percent_region(self):
        # Nothing to draw, and an entry of nulls would only make the caller
        # re-check what an empty list already says.
        intervals = _analysis_intervals([_Result(), _Result(h95=[_r(580, 660)])])
        assert len(intervals) == 1

    def test_keeps_a_result_that_has_no_68_percent_region(self):
        # The outer band is what places a sample on the axis; the inner one
        # is detail, and its absence should not hide the sample.
        intervals = _analysis_intervals([_Result(h95=[_r(580, 660)], median=620.0)])
        assert intervals == [{"h68": [], "h95": [[580.0, 660.0]], "median": 620.0}]


class TestListEndpoint:
    def test_a_saved_calibration_reports_where_it_landed(self, client):
        created = client.post(
            "/api/analysis/calibrate",
            json={
                "sample_name": "Span probe",
                "age_bp": 3000,
                "error_1sigma": 30,
                "material_type": "charcoal",
                "save": True,
            },
        )
        analysis_id = created.json()["data"]["id"]

        items = client.get("/api/analysis?per_page=5").json()["data"]["items"]
        saved = next(i for i in items if i["id"] == analysis_id)

        low, high = saved["calendar_span"]
        assert high > low
        # A 3000 BP charcoal date calibrates to roughly 3350-3060 cal BP;
        # asserting the neighbourhood rather than the exact edges keeps this
        # a test of the plumbing, not of IntCal20's values.
        assert 2900 < low < 3400
        assert 2900 < high < 3400

        assert saved["intervals"], "a solved analysis should carry drawable intervals"
        edges = [v for i in saved["intervals"] for seg in i["h95"] for v in seg]
        assert min(edges) == low and max(edges) == high
