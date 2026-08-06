import numpy as np
from scipy.interpolate import interp1d
from dataclasses import dataclass
from typing import Tuple, Optional, List, Dict
from functools import lru_cache
import os
import csv

@dataclass
class CalibrationPoint:
    cal_bp: float
    c14_age: float
    c14_error: float


def hpd_ranges_from_distribution(ages, weights, confidence: float = 0.95) -> List[Dict[str, float]]:
    """
    Compute one or more Highest Posterior Density (HPD) ranges from a
    discretized calendar-age probability distribution, following standard
    radiocarbon calibration reporting practice (OxCal/CALIB-style).

    Radiocarbon calibration curves are non-monotonic -- plateaus and
    "wiggles" mean a single measurement can map to two or more separate,
    disjoint calendar-age ranges rather than one contiguous span. Reporting
    only the overall min/max envelope in that situation silently implies
    probability mass in the (often large, near-zero-density) gap between
    the ranges, which is misleading. This function instead returns each
    disjoint range in the HPD set individually, with its own share of the
    total probability, e.g.:
        [{'min': 3300, 'max': 3360, 'probability_percent': 13.5},
         {'min': 3100, 'max': 3270, 'probability_percent': 81.9}]
    which for a 95% request sums to 95.4% rather than exactly 95%, because
    the HPD set is built from whole grid points -- this matches the
    familiar "at 95.4% probability" style used in OxCal output.

    Shared by CalibrationCurve.get_hpd_ranges() (grid points from a
    calibration curve) and the Bayesian posterior extraction in
    bayesian.py (bins from a histogram of MCMC draws) -- both are just
    (age, weight) series at that point, so the same logic applies to
    either.

    Args:
        ages: 1D array of calendar ages, sorted ascending
        weights: 1D array of probability weights aligned with `ages`
            (need not already be normalized to sum to 1)
        confidence: target coverage, e.g. 0.68 or 0.95

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

    # Sort by probability (descending) and accumulate until reaching the
    # requested confidence level -- this is the standard HPD construction:
    # include the highest-density points first, regardless of where they
    # fall on the age axis.
    sorted_indices = np.argsort(-norm_weights)
    cumsum = np.cumsum(norm_weights[sorted_indices])
    idx = np.where(cumsum >= confidence)[0]
    if len(idx) == 0:
        # Even the full distribution doesn't reach the requested
        # confidence (can happen with a few NaN/zero weights) -- fall back
        # to the entire range as one segment.
        selected = np.arange(len(ages))
    else:
        selected = sorted_indices[:idx[0] + 1]

    # Group the selected grid points into contiguous runs by array index
    # (ages are sorted ascending, so adjacent indices are adjacent in
    # calendar time) -- each run is one disjoint range in the HPD set.
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
        Trapezoidal weight (effective calendar-year width) represented by each
        curve grid point. Calibration curves like IntCal20 are NOT uniformly
        spaced (e.g. 1-year steps near the present, 20+ year steps further
        back), so summing raw likelihood values without accounting for this
        would bias the posterior toward densely-sampled regions of the curve
        regardless of how likely they actually are. Weighting each point by
        the calendar-year interval it represents (and integrating, i.e.
        weighted-summing, rather than plain-summing) corrects for that.
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
        Calibrate radiocarbon age to calendar age using the probability method.

        For each point on the calibration curve:
        - Calculate combined uncertainty (curve error + sample error in quadrature)
        - Compute likelihood: P(observed c14_age | cal_age) using a Gaussian
          PDF, including the 1/sigma normalization term (not just the
          exponential). Early calibration programs (and OxCal <=3.2) dropped
          this term, using p(t) ~ exp(-(rm-r(t))^2 / 2(sigma_m^2+sigma(t)^2))
          -- see Bronk Ramsey (2001) eq. 1. Because calibration curve error
          sigma(t) varies substantially with age (e.g. IntCal20 in the early
          Holocene), omitting the term biases the posterior toward
          high-curve-error regions. Later OxCal versions and CALIB (Stuiver &
          Reimer 1993) fixed this by dividing by sqrt(sigma_m^2+sigma(t)^2)
          (eq. 2), which is what we do here.
        - Weight by the calendar-year interval each grid point represents
          (trapezoidal rule) so uneven curve resolution doesn't bias the result
        - Normalize to get posterior probability distribution

        A local marine reservoir offset (Delta-R) shifts the age the curve
        predicts: a marine sample of calendar age t is expected to measure
        curve(t) + delta_r, because the curve models only the global average
        reservoir effect. Comparing (c14_age - delta_r) against the curve is
        the same statement, and its uncertainty joins the quadrature sum --
        so an offset that is poorly known widens the calibrated range rather
        than just moving it.

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

        # Likelihood: P(c14_obs | cal_age) = exp(-0.5 * ((c14_obs - c14_model) / sigma)^2) / sigma
        # This is the (correctly normalized, up to the shared 1/sqrt(2*pi)
        # constant) Gaussian probability of observing c14_age given each
        # cal_age on the curve. The 1/sigma factor matters here because
        # sigma = combined_error varies from point to point along the curve.
        log_likelihood = -0.5 * ((self.c14_ages - (c14_age - offset)) / combined_error)**2
        likelihood = np.exp(log_likelihood) / combined_error

        # Weight by local grid spacing before normalizing so calendar-year
        # density in the curve file doesn't distort the posterior shape.
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
    
    def get_hpd_interval(self, cal_ages: np.ndarray, probs: np.ndarray, confidence: float = 0.95) -> Tuple[float, float]:
        """
        Calculate the overall Highest Posterior Density (HPD) envelope.

        Radiocarbon posteriors are frequently multimodal -- calibration
        curve plateaus and wiggles routinely produce two or more disjoint
        calendar-age ranges rather than one contiguous span (this is why
        OxCal/CALIB report "at 95.4% probability: 3360-3300 (13.5%),
        3270-3100 (81.9%)" rather than a single "3360-3100" span, which
        would silently claim probability mass in the gap between the
        peaks). This method returns just the min/max envelope across
        *all* HPD ranges combined, kept for callers that only need a
        single span (e.g. a compact table column or a quick summary
        statistic). For the actual per-range breakdown with each
        segment's probability share, use get_hpd_ranges() instead.

        Args:
            cal_ages: Calendar ages (years BP)
            probs: Posterior probabilities
            confidence: Confidence level (0.68 = 1-sigma, 0.95 = 2-sigma)

        Returns:
            (hpd_min, hpd_max) in calendar years BP
        """
        ranges = self.get_hpd_ranges(cal_ages, probs, confidence)
        hpd_min = min(r['min'] for r in ranges)
        hpd_max = max(r['max'] for r in ranges)
        return float(hpd_min), float(hpd_max)

    def get_hpd_ranges(self, cal_ages: np.ndarray, probs: np.ndarray, confidence: float = 0.95) -> list:
        """
        Calculate the Highest Posterior Density (HPD) region as one or more
        disjoint calendar-age ranges, each with its own share of the total
        probability -- this is standard radiocarbon calibration reporting
        practice (see OxCal/CALIB output), needed because calibration
        curves are not monotonic. A single steep or plateaued stretch of
        curve can map one measurement to multiple separate calendar-age
        peaks; collapsing those into one min/max span (as get_hpd_interval
        does) misrepresents the result by implying probability mass across
        the low-density gap between the peaks.

        Returns:
            List of dicts, ordered oldest-first, each:
                {'min': float, 'max': float, 'probability_percent': float}
            The percentages are each range's share of the *entire*
            distribution (not just the selected HPD set), so for a 95%
            request they will typically sum to slightly more than 95%
            (e.g. 95.4%) rather than exactly 95%, because the HPD set is
            built from whole grid points -- the same convention OxCal uses.
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
        Return a compact (cal_age, probability) series suitable for sending
        to the frontend for plotting.

        The full curve has thousands of grid points (e.g. ~9,500 for
        IntCal20) spanning 0-55,000 cal BP, but any single sample's posterior
        is concentrated in a narrow window of that range. Just taking the
        first N points of the array (as this endpoint used to) silently
        returns whichever slice happens to be first in calendar-age order --
        for most real samples that's nowhere near where the actual
        probability mass is, so the frontend would plot a flat near-zero
        line instead of the sample's actual distribution.

        This instead:
        1. Finds the contiguous window where probability exceeds
           `mass_threshold` of the peak (padded by a few points either side).
        2. Downsamples that window (not the whole curve) to at most
           `max_points` points, if needed, for a reasonably sized payload.

        Args:
            cal_ages: full curve calendar ages for this posterior
            probs: full curve posterior probabilities (same length)
            max_points: cap on how many (age, prob) pairs to return
            mass_threshold: fraction of the peak probability below which
                points are considered negligible and excluded

        Returns:
            List of [cal_age, probability] pairs, ordered by calendar age.
        """
        if len(cal_ages) == 0 or len(probs) == 0:
            return []

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


_CURVE_LOADERS = {
    'intcal20': load_intcal20,
    'marine20': load_marine20,
    'shcal20': load_shcal20,
}


def get_curve(curve_id: str) -> CalibrationCurve:
    """
    Shared curve lookup used by both the single-sample calibration endpoint
    and the Bayesian model builder, so the two code paths can never end up
    calibrating against inconsistent curve data.

    An unrecognized curve_id raises rather than silently falling back to
    IntCal20 (the previous behaviour): a typo'd or unsupported curve_id
    would otherwise produce a real, plausible-looking, but wrong
    calibration -- e.g. someone asking for "marine20" but mistyping
    "marine2020" would have silently gotten a terrestrial calibration with
    no reservoir correction applied, with nothing in the response
    indicating the requested curve wasn't actually used. The API layer
    (schemas/sample.py's CalibrateRequest/BayesianMeasurementInput) also
    validates curve_id against this same set before a request ever reaches
    here, so this check is defense-in-depth for any other caller of
    get_curve() directly.

    Loaders are lru_cache'd, so the (fairly large) .14c files are parsed
    once per process rather than on every request.
    """
    if curve_id not in _CURVE_LOADERS:
        raise ValueError(
            f"Unknown calibration curve '{curve_id}'. "
            f"Supported curves: {', '.join(sorted(_CURVE_LOADERS))}"
        )
    curve = _CURVE_LOADERS[curve_id]()
    if curve is None or len(curve.cal_ages) == 0:
        raise ValueError(f"Calibration curve '{curve_id}' data is not available")
    return curve
