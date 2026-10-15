"""Unit tests for calibration algorithm"""
import pytest
import numpy as np
from app.services.calibration import (
    LIBBY_MEAN_LIFE, CalibrationCurve, f14c, get_curve, hpd_ranges_from_distribution,
)


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


class TestF14CLikelihood:
    """
    The likelihood compares measurement and curve in fraction modern, where
    counting error is Gaussian, rather than in radiocarbon years.
    """

    @staticmethod
    def _bp_likelihood(curve, cal, age, error):
        c14, curve_error = curve.interpolate(cal)
        sigma2 = curve_error ** 2 + error ** 2
        return np.exp(-0.5 * (c14 - age) ** 2 / sigma2) / np.sqrt(sigma2)

    @staticmethod
    def _mean(curve, probs):
        return float((curve.cal_ages * probs).sum())

    def test_is_a_density_over_the_measured_age(self):
        """
        Integrated over the reported BP age at a fixed calendar age it gives
        sqrt(2 pi), the same scale as the BP form (both leave out the shared
        1/sqrt(2 pi)), so model evidence keeps its units.
        """
        curve = get_curve('intcal20')
        ages = np.arange(1500.0, 4500.0, 0.5)
        total = sum(float(curve.likelihood(np.array([3000.0]), a, 30.0)[0]) for a in ages) * 0.5
        assert total == pytest.approx(np.sqrt(2 * np.pi), rel=1e-3)

    @pytest.mark.parametrize('age, error', [(250, 25), (1450, 25), (3000, 30), (10000, 40)])
    def test_holocene_dates_barely_move(self, age, error):
        curve = get_curve('intcal20')
        old = self._bp_likelihood(curve, curve.cal_ages, age, error) * curve._grid_weights()
        _, new = curve.calibrate(age, error)
        assert self._mean(curve, new) == pytest.approx(self._mean(curve, old / old.sum()), abs=1.0)

    def test_old_dates_move_older(self):
        """
        Near the curve's old end a symmetric BP error understates how far the
        material could be OLDER (F14C is tiny and its relative error large),
        so working in F14C moves the posterior older -- by tens of years at
        42,000 BP.
        """
        curve = get_curve('intcal20')
        old = self._bp_likelihood(curve, curve.cal_ages, 42000, 500) * curve._grid_weights()
        _, new = curve.calibrate(42000, 500)
        shift = self._mean(curve, new) - self._mean(curve, old / old.sum())
        assert 20.0 < shift < 100.0

    def test_reservoir_offset_is_applied_in_years(self):
        """Delta-R shifts the curve in radiocarbon years before conversion."""
        curve = get_curve('intcal20')
        cal = np.arange(2500.0, 3500.0)
        shifted = curve.likelihood(cal, 3100.0, 30.0, delta_r=100.0)
        plain = curve.likelihood(cal, 3000.0, 30.0)
        # Equal up to the Jacobian F_m/8033, which differs by f14c(100).
        assert np.allclose(shifted / shifted.max(), plain / plain.max(), atol=2e-3)

    def test_f14c_matches_the_definition_of_a_conventional_age(self):
        assert float(f14c(0.0)) == 1.0
        assert float(f14c(LIBBY_MEAN_LIFE)) == pytest.approx(np.exp(-1.0))

    def test_zero_beyond_the_curve(self):
        curve = get_curve('intcal20')
        lik = curve.likelihood(np.array([-10.0, 60000.0]), 3000.0, 30.0)
        assert np.all(lik == 0.0)


class TestDisplayDistributionIsADensity:
    """
    get_display_distribution returns a series to be plotted against a
    calendar axis, so it has to be a probability DENSITY -- integrating to 1
    over calendar years, regardless of how finely the curve happens to be
    sampled where the date falls.

    It used to return the raw masses `calibrate` produces, which are weighted
    by grid spacing and therefore carry different units in different parts of
    the curve. That surfaced as a real defect: the stacked plot draws this
    series behind a modelled posterior, which IS a density, and integrating
    each over its own grid on one real analysis gave 1.000 for every
    posterior but 10.000 for the marine rows -- so the prior was drawn an
    order of magnitude too tall and overflowed into the row above on 16 of
    22 rows.

    Marine20 is the case that exposed it: uniformly 10-year spaced, where
    IntCal20 and SHCal20 are 1-year in the recent range.
    """

    @staticmethod
    def _integral(series):
        """Trapezium rule over the returned window."""
        total = 0.0
        for (x0, y0), (x1, y1) in zip(series, series[1:]):
            total += (y0 + y1) / 2.0 * abs(x1 - x0)
        return total

    @pytest.mark.parametrize('curve_id', ['intcal20', 'shcal20', 'marine20'])
    def test_it_integrates_to_one_on_every_curve(self, curve_id):
        from app.services.calibration import get_curve

        curve = get_curve(curve_id)
        cal_ages, probs = curve.calibrate(1000, 40)
        series = curve.get_display_distribution(cal_ages, probs)

        # Slightly under 1: the window trims tails below 1e-4 of the peak.
        assert self._integral(series) == pytest.approx(1.0, abs=0.01)

    def test_a_ten_year_grid_no_longer_reads_ten_times_too_tall(self):
        """
        The specific failure. Marine20's 10-year spacing made its masses ten
        times a per-year density, so a marine date's curve towered over a
        terrestrial one drawn on the same scale.
        """
        from app.services.calibration import get_curve

        marine = get_curve('marine20')
        intcal = get_curve('intcal20')

        marine_series = marine.get_display_distribution(*marine.calibrate(1000, 40))
        intcal_series = intcal.get_display_distribution(*intcal.calibrate(1000, 40))

        marine_peak = max(p for _, p in marine_series)
        intcal_peak = max(p for _, p in intcal_series)

        # Two dates of similar precision now have peaks of the same order,
        # rather than differing by the curves' grid spacing.
        assert 0.3 < marine_peak / intcal_peak < 3.0, (marine_peak, intcal_peak)

    def test_it_still_windows_to_where_the_mass_is(self):
        """The behaviour this function existed for in the first place."""
        from app.services.calibration import get_curve

        curve = get_curve('intcal20')
        cal_ages, probs = curve.calibrate(3000, 30)
        series = curve.get_display_distribution(cal_ages, probs)

        ages = [a for a, _ in series]
        peak_age = float(cal_ages[np.argmax(probs)])
        assert min(ages) < peak_age < max(ages)
        # A narrow window, not the curve's whole 0-55,000 range.
        assert max(ages) - min(ages) < 1000
        assert len(series) <= 200

    def test_downsampling_does_not_change_the_integral(self):
        """
        Downsampling keeps every k-th point, which changes the spacing. On a
        density that is harmless -- the values still mean the same thing --
        which is exactly what was not true of the masses.
        """
        from app.services.calibration import get_curve

        curve = get_curve('intcal20')
        cal_ages, probs = curve.calibrate(3000, 200)   # wide, forces downsampling

        dense = curve.get_display_distribution(cal_ages, probs, max_points=10_000)
        sparse = curve.get_display_distribution(cal_ages, probs, max_points=50)

        assert len(sparse) <= 50
        assert len(dense) > len(sparse)
        assert self._integral(sparse) == pytest.approx(self._integral(dense), rel=0.02)

    def test_an_empty_input_is_still_empty(self):
        from app.services.calibration import get_curve

        curve = get_curve('intcal20')
        assert curve.get_display_distribution(np.array([]), np.array([])) == []


class TestTheReportingLevels:
    """
    The coverages every HPD region in this app is quoted at.

    These are not labels over a nominal level: `confidence` is the threshold
    the HPD set is accumulated to, so the digits ARE the reported range. The
    app had grown three different numbers for one sigma (0.68, 0.682, 0.683)
    and three for two sigma (0.95, 0.954, 0.9545), spread across the solvers,
    the API and the browser -- these tests exist so that cannot happen again.

    frontend/src/utils/hpd.ts writes the same three values out (JavaScript has
    no erf) and pins them in its own suite. The mirror is deliberate and is the
    same arrangement services/pdf_report.py uses for the plot palette; it is
    not checked from here, because the frontend source is not mounted where
    this suite runs and a check that silently skips is worse than none.
    """

    def test_they_are_the_exact_two_sided_normal_coverages(self):
        from math import erf, sqrt

        from app.services.calibration import SIGMA_1, SIGMA_2, SIGMA_3

        assert SIGMA_1 == erf(1 / sqrt(2))
        assert SIGMA_2 == erf(2 / sqrt(2))
        assert SIGMA_3 == erf(3 / sqrt(2))

    def test_the_labels_round_rather_than_truncate(self):
        """68.269 rounds to 68.3%. The 68.2% this app used to print was the
        truncation, which is the bug these constants were introduced for."""
        from app.services.calibration import CONFIDENCE_LABELS, REPORTING_CONFIDENCE_LEVELS

        assert [CONFIDENCE_LABELS[level] for level in REPORTING_CONFIDENCE_LEVELS] == [
            '68.3%', '95.4%', '99.7%']
        for level, label in CONFIDENCE_LABELS.items():
            assert label == f"{level * 100:.1f}%"

    def test_a_region_covers_at_least_the_level_it_is_labelled_with(self):
        """
        The level reaches the HPD construction unrounded. If a call site
        rounded 0.6827 down to 0.68 on the way in -- which several of them
        did -- the region would come back short of what its label claims.
        """
        from app.services.calibration import REPORTING_CONFIDENCE_LEVELS, get_curve

        curve = get_curve('intcal20')
        for age in (1000, 2450, 3000, 5000):
            cal_ages, probs = curve.calibrate(age, 30)
            for level in REPORTING_CONFIDENCE_LEVELS:
                ranges = curve.get_hpd_ranges(cal_ages, probs, level)
                covered = sum(r['probability_percent'] for r in ranges) / 100.0
                assert covered >= level - 1e-9, (
                    f'a {level:.4f} region at {age} BP covered only {covered:.4f}')

    def test_one_sigma_is_not_the_two_decimal_truncation(self):
        """
        The difference is real, not notional. Over IntCal20 the 0.68 this app
        used to accumulate to gives a measurably narrower region than the
        actual 1-sigma coverage -- which is the whole reason for the change,
        and is invisible unless something asserts it.
        """
        from app.services.calibration import SIGMA_1, get_curve

        curve = get_curve('intcal20')
        narrower = 0
        for age in (1000, 2000, 2450, 3000, 4000, 5000, 7000):
            cal_ages, probs = curve.calibrate(age, 25)
            def width(level):
                return sum(r['max'] - r['min']
                           for r in curve.get_hpd_ranges(cal_ages, probs, level))
            truncated, exact = width(0.68), width(SIGMA_1)
            assert truncated <= exact, 'a lower level cannot give a wider region'
            if truncated < exact:
                narrower += 1
        assert narrower >= 3, (
            '0.68 and the true 1-sigma coverage now agree everywhere tested, so '
            'this no longer demonstrates anything -- check the curve or the ages'
        )
