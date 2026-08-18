"""
Serving a window of a curve to be drawn: services/curve_view.py and
GET /api/curves/{id}/points.

Two claims are worth holding down here, because both are invisible in a
screenshot and both are the reason this module exists rather than the frontend
slicing an array itself.

Decimation must not erase the wiggles. A calibration curve's reversals and
short excursions are exactly what a browser is for, and stride sampling
removes them precisely where they are sharpest.

Slope must describe the curve rather than its grid. The curves step 1 year
near the present and 20 years past 25,000 cal BP, so anything measured
per-grid-step is describing a twenty-times longer stretch of curve at one end
than the other.
"""
import numpy as np
import pytest

from app.services import curve_registry, curve_view
from app.services.calibration import CalibrationCurve


@pytest.fixture
def spiky():
    """A curve with a one-point spike in every ten, for testing that
    decimation keeps extremes."""
    cal = np.arange(0, 1000, 1.0)
    c14 = cal * 0.95
    c14[::10] += 200          # a spike every tenth point
    c14[5::10] -= 200         # and a trough, offset from it
    return CalibrationCurve(cal, c14, np.full_like(cal, 20.0))


class TestDecimationKeepsTheShape:

    def test_extremes_survive_decimation(self, spiky):
        full = curve_view.window(spiky, max_points=5000)
        reduced = curve_view.window(spiky, max_points=100)

        assert reduced['decimated'] is True
        assert reduced['point_count'] <= 100 + 2

        full_c14 = [p[1] for p in full['points']]
        reduced_c14 = [p[1] for p in reduced['points']]
        # The envelope is what a curve browser is looking at. Stride sampling
        # this curve would lose most of both.
        assert max(reduced_c14) == pytest.approx(max(full_c14))
        assert min(reduced_c14) == pytest.approx(min(full_c14))

    def test_points_stay_in_calendar_order(self, spiky):
        points = curve_view.window(spiky, max_points=60)['points']
        cals = [p[0] for p in points]
        assert cals == sorted(cals)
        assert len(set(cals)) == len(cals)

    def test_the_windows_own_ends_are_kept(self, spiky):
        result = curve_view.window(spiky, 200, 800, max_points=40)
        assert result['points'][0][0] == pytest.approx(result['cal_from'])
        assert result['points'][-1][0] == pytest.approx(result['cal_to'])

    def test_a_small_window_is_not_decimated_at_all(self, spiky):
        result = curve_view.window(spiky, 100, 150, max_points=800)
        assert result['decimated'] is False
        assert result['point_count'] == result['points_in_window']


class TestWindowing:

    def test_window_is_clamped_to_the_curve(self, spiky):
        result = curve_view.window(spiky, -5000, 99999)
        assert result['cal_from'] == 0
        assert result['cal_to'] == 999

    def test_reversed_bounds_are_read_as_a_window_not_an_error(self, spiky):
        forward = curve_view.window(spiky, 200, 400, max_points=5000)
        backward = curve_view.window(spiky, 400, 200, max_points=5000)
        assert forward['points'] == backward['points']

    def test_a_window_narrower_than_the_grid_still_draws_something(self):
        """
        IntCal20 steps 20 years past 25,000 cal BP, so a 5-year window there
        contains no grid points at all. Returning nothing would draw an empty
        chart at high zoom; the bracketing points are what interpolation would
        use anyway.
        """
        curve = curve_registry.resolve('intcal20')
        result = curve_view.window(curve, 30000, 30005)
        assert result['point_count'] >= 2


class TestSlope:

    def test_slope_is_one_on_a_curve_that_tracks_calendar_time(self):
        cal = np.arange(0, 2000, 5.0)
        curve = CalibrationCurve(cal, cal, np.full_like(cal, 20.0))
        slopes = [p[3] for p in curve_view.window(curve, 500, 1500)['points']]
        assert all(s == pytest.approx(1.0) for s in slopes)

    def test_slope_is_zero_on_a_plateau(self):
        cal = np.arange(0, 2000, 5.0)
        c14 = np.where(cal < 1000, cal, 1000.0)
        curve = CalibrationCurve(cal, c14, np.full_like(cal, 20.0))
        slopes = [p[3] for p in curve_view.window(curve, 1200, 1800)['points']]
        assert all(s == pytest.approx(0.0) for s in slopes)

    def test_the_hallstatt_plateau_reads_flatter_than_ordinary_curve(self):
        """
        The check that the number means what the readout will say it means.
        The Hallstatt plateau (roughly 2750-2400 cal BP) is the best known
        flat stretch in the Holocene; if it does not read as flat here, the
        browser's plateau readout is decorative.
        """
        curve = curve_registry.resolve('intcal20')
        plateau = np.mean([p[3] for p in curve_view.window(curve, 2450, 2700, 500)['points']])
        ordinary = np.mean([p[3] for p in curve_view.window(curve, 3400, 3650, 500)['points']])
        assert plateau < 0.5
        assert ordinary > 0.8
        assert ordinary > plateau * 2

    def test_slope_does_not_jump_where_the_grid_spacing_changes(self):
        """
        IntCal20 changes from 1-year to 5-year steps at 5,000 cal BP. A slope
        measured per grid step would change by a factor of five across that
        line while the curve itself does nothing unusual.
        """
        curve = curve_registry.resolve('intcal20')
        before = np.mean([p[3] for p in curve_view.window(curve, 4700, 4950, 500)['points']])
        after = np.mean([p[3] for p in curve_view.window(curve, 5050, 5300, 500)['points']])
        assert abs(before - after) < 0.6

    def test_slope_at_a_window_edge_is_measured_against_the_whole_curve(self):
        """A point does not get a different slope because of where the view
        happens to stop."""
        curve = curve_registry.resolve('intcal20')
        wide = {p[0]: p[3] for p in curve_view.window(curve, 3000, 4000, 5000)['points']}
        narrow = {p[0]: p[3] for p in curve_view.window(curve, 3500, 3600, 5000)['points']}
        shared = set(wide) & set(narrow)
        assert len(shared) > 50
        for cal in shared:
            assert wide[cal] == pytest.approx(narrow[cal])


class TestEndpoint:

    def test_points_for_a_window(self, client):
        response = client.get('/api/curves/intcal20/points?cal_from=2000&cal_to=3000&max_points=200')
        assert response.status_code == 200
        data = response.json()['data']
        assert data['curve_id'] == 'intcal20'
        assert data['cal_from'] == 2000 and data['cal_to'] == 3000
        assert 2 <= data['point_count'] <= 202
        assert all(len(p) == 4 for p in data['points'])

    def test_the_whole_curve_is_summarised_rather_than_sent(self, client):
        data = client.get('/api/curves/intcal20/points').json()['data']
        assert data['points_in_window'] == 9501
        assert data['decimated'] is True
        assert data['point_count'] <= curve_view.DEFAULT_MAX_POINTS + 2

    def test_curve_extent_is_reported_so_a_scrubber_can_be_drawn(self, client):
        data = client.get('/api/curves/intcal20/points?cal_from=100&cal_to=200').json()['data']
        assert data['curve_cal_min'] == 0
        assert data['curve_cal_max'] == 55000

    def test_unknown_curve_is_404(self, client):
        assert client.get('/api/curves/intcal13/points').status_code == 404

    def test_max_points_is_bounded(self, client):
        assert client.get('/api/curves/intcal20/points?max_points=1').status_code == 422
        assert client.get('/api/curves/intcal20/points?max_points=99999').status_code == 422
