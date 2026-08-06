"""Unit tests for calibration algorithm"""
import pytest
import numpy as np
from app.services.calibration import CalibrationCurve, hpd_ranges_from_distribution


class TestCalibrationCurve:
    """Test radiocarbon calibration logic"""
    
    @pytest.fixture
    def sample_curve(self):
        """Create a simple test calibration curve"""
        # Simple linear curve: cal_age increases by 1, c14_age by 0.95 (slope < 1)
        cal_ages = np.arange(0, 5001, 100)  # 0, 100, 200, ..., 5000 cal BP
        c14_ages = cal_ages * 0.95  # Slightly younger in c14 terms
        c14_errors = np.ones_like(cal_ages) * 20  # Constant 20-year error
        return CalibrationCurve(cal_ages, c14_ages, c14_errors)
    
    def test_calibrate_returns_distributions(self, sample_curve):
        """Test that calibrate returns proper probability distribution"""
        cal_ages, probs = sample_curve.calibrate(2500, 50)
        
        # Check shapes
        assert len(cal_ages) == len(probs)
        assert len(cal_ages) > 0
        
        # Check probabilities sum to 1
        assert np.isclose(np.sum(probs), 1.0)
        
        # Check all probabilities are positive
        assert np.all(probs >= 0)
    
    def test_calibrate_peak_near_expected(self, sample_curve):
        """Test that calibration peak is near expected age"""
        c14_age = 2500
        cal_ages, probs = sample_curve.calibrate(c14_age, 50)
        
        # Peak should be near 2500 / 0.95 ≈ 2632 cal BP
        peak_idx = np.argmax(probs)
        peak_age = cal_ages[peak_idx]
        
        # Allow 500-year tolerance
        assert abs(peak_age - c14_age / 0.95) < 500
    
    def test_hpd_interval_contains_peak(self, sample_curve):
        """Test that HPD interval contains the peak"""
        cal_ages, probs = sample_curve.calibrate(2500, 50)
        peak_idx = np.argmax(probs)
        peak_age = cal_ages[peak_idx]
        
        hpd_min, hpd_max = sample_curve.get_hpd_interval(cal_ages, probs, 0.68)
        assert hpd_min <= peak_age <= hpd_max
    
    def test_hpd_interval_95_larger_than_68(self, sample_curve):
        """Test that 95% HPD is larger than 68% HPD"""
        cal_ages, probs = sample_curve.calibrate(2500, 50)
        
        hpd_68_min, hpd_68_max = sample_curve.get_hpd_interval(cal_ages, probs, 0.68)
        hpd_95_min, hpd_95_max = sample_curve.get_hpd_interval(cal_ages, probs, 0.95)
        
        range_68 = hpd_68_max - hpd_68_min
        range_95 = hpd_95_max - hpd_95_min
        
        # 95% range should be larger
        assert range_95 >= range_68
    
    def test_larger_error_wider_distribution(self, sample_curve):
        """Test that larger measurement error produces wider distribution"""
        cal_ages_1, probs_1 = sample_curve.calibrate(2500, 25)
        cal_ages_2, probs_2 = sample_curve.calibrate(2500, 100)
        
        # Same cal_ages for both
        assert np.allclose(cal_ages_1, cal_ages_2)
        
        # Get widths
        hpd_min_1, hpd_max_1 = sample_curve.get_hpd_interval(cal_ages_1, probs_1, 0.68)
        hpd_min_2, hpd_max_2 = sample_curve.get_hpd_interval(cal_ages_2, probs_2, 0.68)
        
        width_1 = hpd_max_1 - hpd_min_1
        width_2 = hpd_max_2 - hpd_min_2
        
        # Larger error should produce wider interval
        assert width_2 > width_1
    
    def test_point_estimates(self, sample_curve):
        """Test point estimate calculations"""
        cal_ages, probs = sample_curve.calibrate(2500, 50)
        estimates = sample_curve.get_point_estimate(cal_ages, probs)
        
        # Check all estimates are present
        assert 'mean' in estimates
        assert 'median' in estimates
        assert 'mode' in estimates
        assert 'std' in estimates
        
        # Check estimates are within curve range
        assert cal_ages[0] <= estimates['mean'] <= cal_ages[-1]
        assert cal_ages[0] <= estimates['median'] <= cal_ages[-1]
        assert cal_ages[0] <= estimates['mode'] <= cal_ages[-1]
        
        # Std should be positive
        assert estimates['std'] > 0
    
    def test_empty_curve_raises_error(self):
        """Test that empty curve raises error on calibrate"""
        empty_curve = CalibrationCurve([], [], [])

        with pytest.raises(ValueError):
            empty_curve.calibrate(2500, 50)

    def test_hpd_ranges_unimodal_matches_interval(self, sample_curve):
        """A single-peaked posterior should collapse to one HPD range whose
        min/max match get_hpd_interval's envelope."""
        cal_ages, probs = sample_curve.calibrate(2500, 50)

        ranges = sample_curve.get_hpd_ranges(cal_ages, probs, 0.68)
        hpd_min, hpd_max = sample_curve.get_hpd_interval(cal_ages, probs, 0.68)

        assert len(ranges) == 1
        assert ranges[0]['min'] == pytest.approx(hpd_min)
        assert ranges[0]['max'] == pytest.approx(hpd_max)
        # A single range should carry (approximately) all of the envelope's
        # probability mass.
        assert ranges[0]['probability_percent'] > 65

    def test_hpd_ranges_bimodal_splits_into_disjoint_segments(self):
        """A bimodal posterior (as produced by a calibration curve plateau
        or wiggle giving two separate intercepts) should be reported as two
        disjoint ranges, each with its own probability share, rather than
        one span that silently includes the near-zero-density gap between
        the two peaks."""
        ages = np.arange(0, 200, 1.0)
        probs = (
            np.exp(-0.5 * ((ages - 40) / 5) ** 2) * 0.3
            + np.exp(-0.5 * ((ages - 160) / 5) ** 2) * 0.7
        )
        probs /= probs.sum()

        ranges = hpd_ranges_from_distribution(ages, probs, 0.95)

        assert len(ranges) == 2
        # Ordered oldest-first (largest 'max' first).
        assert ranges[0]['max'] > ranges[1]['max']
        # Neither range should overlap or touch the other -- there's a real
        # gap between the two peaks around age 100.
        assert ranges[1]['max'] < ranges[0]['min']
        # Each range's own min < max.
        for r in ranges:
            assert r['min'] < r['max']
            assert 0 < r['probability_percent'] <= 100
        # Percentages are shares of the *whole* distribution, so for a 95%
        # request they should sum to at least 95% (and, because the HPD set
        # is built from discrete grid points, typically a bit more).
        total = sum(r['probability_percent'] for r in ranges)
        assert 95.0 <= total <= 100.0

    def test_hpd_ranges_from_distribution_rejects_empty(self):
        with pytest.raises(ValueError):
            hpd_ranges_from_distribution(np.array([]), np.array([]), 0.95)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])


class TestReservoirCorrection:
    """
    Delta-R: the local marine reservoir offset. A marine curve models only
    the global average reservoir effect, so a sample's local deviation from
    that average has to be applied on top of it.

    The synthetic curve here has a known slope (c14 = 0.95 * cal), so the
    expected shift can be stated exactly rather than eyeballed: subtracting
    an offset of D from the measured age moves the calibrated age younger by
    D / 0.95 calendar years.
    """

    @pytest.fixture
    def curve(self):
        cal_ages = np.arange(0, 5001, 20)
        return CalibrationCurve(cal_ages, cal_ages * 0.95, np.ones_like(cal_ages) * 20)

    def _peak(self, curve, c14_age, error, delta_r=None, delta_r_error=None):
        cal_ages, probs = curve.calibrate(c14_age, error, delta_r, delta_r_error)
        return cal_ages[np.argmax(probs)]

    def test_no_offset_is_unchanged(self, curve):
        """
        The correction must be inert unless asked for -- every analysis that
        predates it has to calibrate to exactly what it did before.
        """
        baseline = curve.calibrate(2500, 50)
        for args in ((None, None), (0, 0), (0, None), (None, 0)):
            cal_ages, probs = curve.calibrate(2500, 50, *args)
            assert np.array_equal(cal_ages, baseline[0]), args
            assert np.allclose(probs, baseline[1]), args

    def test_a_positive_offset_dates_the_sample_younger(self, curve):
        """
        Positive Delta-R means the local water is more depleted than the
        global average, so the sample measures older than the curve predicts
        and its true calendar age is younger.
        """
        uncorrected = self._peak(curve, 2500, 50)
        corrected = self._peak(curve, 2500, 50, 190, 0)
        assert corrected < uncorrected
        # 190 radiocarbon years / 0.95 = 200 calendar years, within a grid step.
        assert uncorrected - corrected == pytest.approx(200, abs=20)

    def test_a_negative_offset_dates_the_sample_older(self, curve):
        uncorrected = self._peak(curve, 2500, 50)
        corrected = self._peak(curve, 2500, 50, -190, 0)
        assert corrected > uncorrected
        assert corrected - uncorrected == pytest.approx(200, abs=20)

    def test_the_offsets_uncertainty_widens_the_result(self, curve):
        """
        Delta-R is combined in quadrature with the measurement error, so a
        poorly known offset must widen the calibrated range and not merely
        move it. Treating the offset as exact would report a date more
        precise than the evidence supports.
        """
        def spread(delta_r_error):
            cal_ages, probs = curve.calibrate(2500, 50, 190, delta_r_error)
            mean = np.sum(cal_ages * probs)
            return float(np.sqrt(np.sum(probs * (cal_ages - mean) ** 2)))

        tight, loose = spread(0), spread(100)
        assert loose > tight * 1.5, (tight, loose)
        # The centre is unmoved -- the uncertainty widens, it doesn't shift.
        assert self._peak(curve, 2500, 50, 190, 0) == pytest.approx(
            self._peak(curve, 2500, 50, 190, 100), abs=40
        )
