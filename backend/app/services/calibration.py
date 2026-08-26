import numpy as np
from scipy.interpolate import interp1d
from dataclasses import dataclass
from math import erf, sqrt
from typing import Tuple, Optional, List, Dict
from functools import lru_cache
import os
import csv

# --------------------------------------------------------------------------
# The coverages every HPD region in this app is reported at
# --------------------------------------------------------------------------
#
# "1 sigma" means the two-sided normal coverage of one standard deviation --
# erf(1/sqrt(2)) = 0.682689..., quoted as 68.3%. Likewise 95.4% and 99.7%.
# These are the levels OxCal reports against and the ones this app's labels
# claim.
#
# Computed rather than typed, because `confidence` is the threshold the HPD
# set is accumulated to (see hpd_ranges_from_distribution) and so a truncated
# value is a different reported range: over thirty IntCal20 calibrations from
# 500-10,000 BP, a two-sigma level of 0.95 differed from the exact coverage in
# 27 of 30 cases, by up to 10 years.
SIGMA_1 = erf(1 / sqrt(2))      # 0.6826894921...  reported as 68.3%
SIGMA_2 = erf(2 / sqrt(2))      # 0.9544997361...  reported as 95.4%
SIGMA_3 = erf(3 / sqrt(2))      # 0.9973002039...  reported as 99.7%

# The levels the results page offers, in order, with the label each is quoted
# under. One decimal place, correctly ROUNDED: 68.27% rounds to 68.3%.
# frontend/src/utils/hpd.ts carries the same three pairs.
REPORTING_CONFIDENCE_LEVELS = (SIGMA_1, SIGMA_2, SIGMA_3)
CONFIDENCE_LABELS = {SIGMA_1: '68.3%', SIGMA_2: '95.4%', SIGMA_3: '99.7%'}


@dataclass
class CalibrationPoint:
    cal_bp: float
    c14_age: float
    c14_error: float


def hpd_ranges_from_distribution(ages, weights, confidence: float = SIGMA_2) -> List[Dict[str, float]]:
    """
    Highest Posterior Density (HPD) ranges from a discretized calendar-age
    distribution, in OxCal/CALIB reporting style.

    Calibration curves are non-monotonic, so one measurement can map to
    several disjoint calendar-age ranges. Reporting only the min/max envelope
    would imply probability mass in the near-zero-density gap between them, so
    each range is returned with its own share of the total:
        [{'min': 3300, 'max': 3360, 'probability_percent': 13.5},
         {'min': 3100, 'max': 3270, 'probability_percent': 81.9}]
    The shares sum to slightly MORE than the level asked for, because the set
    is built from whole grid points and the last one taken overshoots.

    Shared by CalibrationCurve.get_hpd_ranges (curve grid points) and the
    solvers' posterior extraction (histogram bins) -- both are (age, weight)
    series.

    Args:
        ages: 1D array of calendar ages, sorted ascending
        weights: 1D array of probability weights aligned with `ages`
            (need not already be normalized to sum to 1)
        confidence: target coverage -- SIGMA_1 / SIGMA_2 / SIGMA_3 above,
            which are the levels this app reports at

    Returns:
        List of dicts, ordered oldest-first (largest 'max' first), each
        with 'min', 'max' (years, min < max) and 'probability_percent'
        (0-100, share of the *entire* input distribution).
    """
    ages = np.asarray(ages, dtype=float)
    weights = np.asarray(weights, dtype=float)

    if len(ages) == 0 or len(weights) == 0:
        raise ValueError("Empty ages or probabilities")

    total = np.sum(weights)
    if total <= 0 or not np.isfinite(total):
        raise ValueError("Distribution has zero or non-finite total probability")

    norm_weights = weights / total

    # The standard HPD construction: take the highest-density points first,
    # regardless of where they fall on the age axis, until the level is met.
    sorted_indices = np.argsort(-norm_weights)
    cumsum = np.cumsum(norm_weights[sorted_indices])
    idx = np.where(cumsum >= confidence)[0]
    if len(idx) == 0:
        # Even the full distribution falls short of the requested
        # confidence, which a few NaN/zero weights can cause.
        selected = np.arange(len(ages))
    else:
        selected = sorted_indices[:idx[0] + 1]

    # Ages are sorted ascending, so each contiguous run of indices is one
    # disjoint range in the HPD set.
    selected_sorted = np.sort(selected)
    segments = []
    start = prev = int(selected_sorted[0])
    for i in selected_sorted[1:]:
        i = int(i)
        if i == prev + 1:
            prev = i
            continue
        segments.append((start, prev))
        start = prev = i
    segments.append((start, prev))

    ranges = []
    for s, e in segments:
        seg_mass = float(np.sum(norm_weights[s:e + 1]))
        ranges.append({
            'min': float(ages[s]),
            'max': float(ages[e]),
            'probability_percent': seg_mass * 100.0,
        })

    # Oldest first, matching conventional radiocarbon reporting order.
    ranges.sort(key=lambda r: r['max'], reverse=True)
    return ranges


class CalibrationCurve:
    """Handles radiocarbon calibration using standard curves"""
    
    def __init__(self, cal_ages, c14_ages, c14_errors):
        self.cal_ages = np.array(cal_ages, dtype=float)
        self.c14_ages = np.array(c14_ages, dtype=float)
        self.c14_errors = np.array(c14_errors, dtype=float)
        self.curve_name = "Unknown"
        self._grid_weights_cache: Optional[np.ndarray] = None

    def _grid_weights(self) -> np.ndarray:
        """
        The calendar-year width each curve grid point stands for. Curves like
        IntCal20 are NOT uniformly spaced -- 1-year steps near the present,
        20+ further back -- so plain-summing the likelihood would bias the
        posterior toward densely-sampled stretches of curve.
        """
        if self._grid_weights_cache is not None:
            return self._grid_weights_cache

        n = len(self.cal_ages)
        if n == 0:
            self._grid_weights_cache = np.array([])
        elif n == 1:
            self._grid_weights_cache = np.array([1.0])
        else:
            d = np.diff(self.cal_ages)
            w = np.empty(n)
            w[0] = d[0] / 2.0
            w[-1] = d[-1] / 2.0
            w[1:-1] = (d[:-1] + d[1:]) / 2.0
            self._grid_weights_cache = w

        return self._grid_weights_cache

    def calibrate(
        self,
        c14_age: float,
        c14_error: float,
        delta_r: Optional[float] = None,
        delta_r_error: Optional[float] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Calibrate a radiocarbon age to a calendar age by the probability
        method: at each curve point, combine the curve and sample errors in
        quadrature, evaluate the Gaussian likelihood, weight by the
        calendar-year interval the point represents, and normalise.

        The likelihood includes the 1/sigma normalisation term, not just the
        exponential. Curve error varies substantially with age, so omitting it
        biases the posterior toward high-curve-error regions -- the fix CALIB
        (Stuiver & Reimer 1993) and later OxCal made; see Bronk Ramsey (2001)
        eqs. 1 and 2.

        A local marine reservoir offset (Delta-R) shifts the age the curve
        predicts, since the curve models only the global average: a marine
        sample of calendar age t is expected to measure curve(t) + delta_r.
        Its uncertainty joins the quadrature sum, so a poorly known offset
        widens the calibrated range rather than only moving it.

        Args:
            c14_age: Measured radiocarbon age (years BP)
            c14_error: 1-sigma measurement uncertainty (years)
            delta_r: Local marine reservoir offset in radiocarbon years
            delta_r_error: 1-sigma uncertainty on delta_r

        Returns:
            (cal_ages, probabilities) - calendar ages and their posterior probabilities
        """
        if len(self.cal_ages) == 0:
            raise ValueError("Calibration curve data is empty")

        offset = delta_r or 0.0
        offset_error = delta_r_error or 0.0

        # Combined uncertainty: sqrt(sigma_curve^2 + sigma_measurement^2 + sigma_deltaR^2)
        combined_error = np.sqrt(self.c14_errors**2 + c14_error**2 + offset_error**2)

        # P(c14_obs | cal_age), normalised up to the shared 1/sqrt(2*pi).
        # The 1/sigma factor matters because combined_error varies from point
        # to point along the curve.
        log_likelihood = -0.5 * ((self.c14_ages - (c14_age - offset)) / combined_error)**2
        likelihood = np.exp(log_likelihood) / combined_error

        # Weighted by local grid spacing before normalising, so the curve
        # file's calendar-year density does not distort the posterior shape.
        weighted_likelihood = likelihood * self._grid_weights()

        total = np.sum(weighted_likelihood)
        if total <= 0 or not np.isfinite(total):
            raise ValueError(
                "Calibration produced a degenerate (zero or non-finite) posterior; "
                "the measurement is likely far outside the calibration curve's range."
            )
        
        posterior_prob = weighted_likelihood / total
        
        return self.cal_ages, posterior_prob
    
    def interpolate(self, cal_age) -> Tuple[np.ndarray, np.ndarray]:
        """
        Linearly interpolate the curve's radiocarbon age and its 1-sigma
        curve error at arbitrary (non-grid) calendar age(s). Values outside
        the curve's range are clamped to the nearest edge value rather than
        extrapolated.
        
        Args:
            cal_age: scalar or array of calendar age(s) (years BP)
        
        Returns:
            (c14_age, c14_error) interpolated at each requested cal_age
        """
        if len(self.cal_ages) == 0:
            raise ValueError("Calibration curve data is empty")
        
        c14_age = np.interp(cal_age, self.cal_ages, self.c14_ages)
        c14_error = np.interp(cal_age, self.cal_ages, self.c14_errors)
        return c14_age, c14_error
    
    def get_hpd_interval(self, cal_ages: np.ndarray, probs: np.ndarray, confidence: float = SIGMA_2) -> Tuple[float, float]:
        """
        The overall HPD envelope: the min/max across all disjoint ranges
        combined, for callers that only need a single span. Because
        radiocarbon posteriors are frequently multimodal, that span claims
        probability mass in the gap between peaks -- use get_hpd_ranges() for
        the per-range breakdown.

        Args:
            cal_ages: Calendar ages (years BP)
            probs: Posterior probabilities
            confidence: Confidence level (SIGMA_1 = 1 sigma, SIGMA_2 = 2 sigma)

        Returns:
            (hpd_min, hpd_max) in calendar years BP
        """
        ranges = self.get_hpd_ranges(cal_ages, probs, confidence)
        hpd_min = min(r['min'] for r in ranges)
        hpd_max = max(r['max'] for r in ranges)
        return float(hpd_min), float(hpd_max)

    def get_hpd_ranges(self, cal_ages: np.ndarray, probs: np.ndarray, confidence: float = SIGMA_2) -> list:
        """
        The HPD region as one or more disjoint calendar-age ranges, each with
        its own share of the total probability -- standard radiocarbon
        reporting practice, needed because calibration curves are not
        monotonic.

        Returns:
            List of dicts, oldest-first, each
                {'min': float, 'max': float, 'probability_percent': float}
            The percentages are shares of the ENTIRE distribution, so they
            typically sum to slightly more than the level asked for: the set
            is built from whole grid points, as in OxCal.
        """
        return hpd_ranges_from_distribution(cal_ages, probs, confidence)
    
    def get_point_estimate(self, cal_ages: np.ndarray, probs: np.ndarray) -> dict:
        """
        Calculate point estimates from posterior distribution.
        
        Returns:
            dict with: mean, median, mode, std
        """
        mean = np.average(cal_ages, weights=probs)
        
        # Median: find age where cumsum reaches 0.5
        cumsum = np.cumsum(probs)
        median_idx = np.searchsorted(cumsum, 0.5)
        median = cal_ages[min(median_idx, len(cal_ages)-1)]
        
        # Mode: age with highest probability
        mode_idx = np.argmax(probs)
        mode = cal_ages[mode_idx]
        
        # Standard deviation (weighted)
        std = np.sqrt(np.average((cal_ages - mean)**2, weights=probs))
        
        return {
            'mean': float(mean),
            'median': float(median),
            'mode': float(mode),
            'std': float(std)
        }

    def get_display_distribution(self, cal_ages: np.ndarray, probs: np.ndarray,
                                  max_points: int = 200, mass_threshold: float = 1e-4):
        """
        A compact (cal_age, probability DENSITY) series for the frontend to
        plot.

        The full curve runs to thousands of grid points across 0-55,000 cal
        BP, but a single sample's posterior occupies a narrow window of it. So
        this converts the posterior MASSES `calibrate` returns into a density
        by dividing out each grid point's calendar-year width, finds the
        contiguous window where that density exceeds `mass_threshold` of the
        peak, and downsamples only that window to `max_points`.

        A DENSITY, not masses, because the curves are not uniformly spaced (1,
        5, 10 and 20-year steps in different ranges), so a mass array has
        different units in different parts of the curve while the series is
        plotted against a calendar axis. It is also what the plot's y-axis is
        labelled, and what exact_chain._plottable produces for the modelled
        posterior drawn over it.

        The returned window integrates to slightly less than 1, by whatever
        the trimmed tails held: it is a view of a density, not a renormalised
        distribution.

        Args:
            cal_ages: full curve calendar ages for this posterior
            probs: full curve posterior probability MASSES (as returned by
                calibrate, summing to 1)
            max_points: cap on how many (age, density) pairs to return
            mass_threshold: fraction of the peak below which points are
                considered negligible and excluded

        Returns:
            List of [cal_age, probability density] pairs, ordered by calendar
            age. Integrating with the trapezium rule gives ~1.
        """
        if len(cal_ages) == 0 or len(probs) == 0:
            return []

        # Masses -> density, guarded against a zero-width cell.
        weights = self._grid_weights()
        if len(weights) == len(probs):
            probs = np.divide(
                probs, weights,
                out=np.zeros_like(probs, dtype=float),
                where=weights > 0,
            )

        peak = np.max(probs)
        if peak <= 0:
            return []

        significant = np.where(probs >= peak * mass_threshold)[0]
        if len(significant) == 0:
            return []

        pad = 3
        lo = max(0, significant[0] - pad)
        hi = min(len(cal_ages) - 1, significant[-1] + pad)

        window_ages = cal_ages[lo:hi + 1]
        window_probs = probs[lo:hi + 1]

        if len(window_ages) > max_points:
            sample_idx = np.linspace(0, len(window_ages) - 1, max_points).astype(int)
            window_ages = window_ages[sample_idx]
            window_probs = window_probs[sample_idx]

        return [[float(a), float(p)] for a, p in zip(window_ages, window_probs)]

def _load_curve_from_file(filepath: str, curve_name: str = "") -> Optional[CalibrationCurve]:
    """
    Load calibration curve from .14c file.
    
    Format: CSV with 3 columns (space or comma separated)
    - Column 0: Calendar age (years BP)
    - Column 1: Radiocarbon age (years BP)
    - Column 2: 1-sigma error (years)
    
    Lines starting with '#' are comments and skipped.
    
    Args:
        filepath: Path to curve file
        curve_name: Optional name for the curve
    
    Returns:
        CalibrationCurve object or None if file not found/invalid
    """
    if not os.path.exists(filepath):
        print(f"Warning: Calibration curve file not found: {filepath}")
        return None
    
    cal_ages = []
    c14_ages = []
    c14_errors = []
    
    try:
        with open(filepath, 'r') as f:
            for line_num, line in enumerate(f, 1):
                # Skip empty lines and comments
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                # Try to parse as CSV (space or comma separated)
                try:
                    # Try comma-separated first
                    if ',' in line:
                        parts = [float(x.strip()) for x in line.split(',')]
                    else:
                        # Try space-separated
                        parts = [float(x.strip()) for x in line.split()]
                    
                    if len(parts) < 3:
                        print(f"Warning: Line {line_num} has < 3 columns, skipping: {line}")
                        continue
                    
                    cal_ages.append(float(parts[0]))
                    c14_ages.append(float(parts[1]))
                    c14_errors.append(float(parts[2]))
                except (ValueError, IndexError) as e:
                    print(f"Warning: Could not parse line {line_num}: {line}")
                    continue
        
        if not cal_ages:
            print(f"Error: No valid data found in {filepath}")
            return None
        
        # Verify curve is monotonically increasing in calendar age
        if not np.all(np.diff(cal_ages) > 0):
            print(f"Warning: Calendar ages not monotonically increasing in {filepath}")
            # Sort by calendar age
            sorted_indices = np.argsort(cal_ages)
            cal_ages = [cal_ages[i] for i in sorted_indices]
            c14_ages = [c14_ages[i] for i in sorted_indices]
            c14_errors = [c14_errors[i] for i in sorted_indices]
        
        curve = CalibrationCurve(cal_ages, c14_ages, c14_errors)
        curve.curve_name = curve_name or os.path.basename(filepath)
        
        print(f"Loaded {curve.curve_name}: {len(cal_ages)} points, "
              f"range {cal_ages[0]:.0f}-{cal_ages[-1]:.0f} cal BP")
        return curve
    except Exception as e:
        print(f"Error loading calibration curve {filepath}: {e}")
        return None

@lru_cache(maxsize=None)
def load_intcal20() -> Optional[CalibrationCurve]:
    """Load IntCal20 Northern Hemisphere curve data"""
    base_path = os.path.dirname(os.path.abspath(__file__))
    possible_paths = [
        os.path.join(base_path, '../../calibration_data/intcal20.14c'),
        os.path.join(base_path, '../../calibration_data/intcal20.csv'),
        os.environ.get('INTCAL_CURVE_PATH', '/app/data/intcal20.14c'),
    ]
    
    for path in possible_paths:
        curve = _load_curve_from_file(path, "IntCal20")
        if curve:
            return curve
    
    print("Warning: IntCal20 curve not found, returning empty curve")
    return CalibrationCurve([], [], [])

@lru_cache(maxsize=None)
def load_marine20() -> Optional[CalibrationCurve]:
    """Load Marine20 marine calibration curve data"""
    base_path = os.path.dirname(os.path.abspath(__file__))
    possible_paths = [
        os.path.join(base_path, '../../calibration_data/marine20.14c'),
        os.path.join(base_path, '../../calibration_data/marine20.csv'),
        os.environ.get('MARINE_CURVE_PATH', '/app/data/marine20.14c'),
    ]
    
    for path in possible_paths:
        curve = _load_curve_from_file(path, "Marine20")
        if curve:
            return curve
    
    print("Warning: Marine20 curve not found, returning empty curve")
    return CalibrationCurve([], [], [])

@lru_cache(maxsize=None)
def load_shcal20() -> Optional[CalibrationCurve]:
    """Load SHCal20 Southern Hemisphere calibration curve data"""
    base_path = os.path.dirname(os.path.abspath(__file__))
    possible_paths = [
        os.path.join(base_path, '../../calibration_data/shcal20.14c'),
        os.path.join(base_path, '../../calibration_data/shcal20.csv'),
        os.environ.get('SHCAL_CURVE_PATH', '/app/data/shcal20.14c'),
    ]
    
    for path in possible_paths:
        curve = _load_curve_from_file(path, "SHCal20")
        if curve:
            return curve
    
    print("Warning: SHCal20 curve not found, returning empty curve")
    return CalibrationCurve([], [], [])


def get_curve(curve_id: str) -> CalibrationCurve:
    """
    Shared curve lookup, so the calibration endpoint and the solvers cannot
    calibrate against inconsistent curve data.

    An unrecognized curve_id raises rather than falling back to IntCal20: a
    mistyped "marine2020" would otherwise return a plausible-looking
    terrestrial calibration with no reservoir correction and nothing to say
    so. schemas/sample.py validates curve_id before a request reaches here, so
    this is defence in depth for direct callers.

    A thin delegation to services/curve_registry, which owns the question of
    which curves exist; the import is function-level to break the cycle, since
    the registry imports this module for its loaders. Those loaders are
    lru_cache'd, so each .14c file is parsed once per process.
    """
    from app.services.curve_registry import resolve
    return resolve(curve_id)
