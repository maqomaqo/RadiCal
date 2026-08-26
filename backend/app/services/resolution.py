"""
The expected temporal resolution of a calibration curve: how tightly a date
from a given calendar age can be known, before anyone measures anything.

The method is Svetlik et al. (2019), Radiocarbon 61(6):1729-1740, as
implemented by Demjan in res14c (github.com/demjanp/res14c_online). For each
radiocarbon age a sample of that period could plausibly return:

  1. scale the laboratory uncertainty with age, u(a) = u0 * exp(a / 2*8033),
     because counting statistics degrade as there is less 14C left to count;
  2. calibrate that measurement against the curve;
  3. take the width of its 95.45% HPD envelope -- first to last point of the
     set, gaps included -- as the resolution that measurement achieved.

Then for each calendar year, average those widths over the radiocarbon ages a
sample of that true age could actually yield, weighting each by how likely it
is to yield it. That gives a mean, and a 5th-95th percentile band for how
lucky or unlucky a single date can be.

Written from the published method and this app's own calibration code, not
translated from res14c, which is GPL-3.0 where this project is CC0. It
reproduces the method's OUTPUT: over 1,000-4,000 cal BP it agrees with res14c's
published figures to about 0.3%, including the sharp 33-year trough at 2,750
cal BP that a subtly wrong implementation would smear away.

Affordable at request time despite the obvious formulation being a dense
50,000 x 55,000 problem: every calendar age more than 5 sigma from a
measurement contributes nothing and is masked out, and the calculation runs on
a uniform 1-year grid, where `calibrate`'s trapezoidal weights are constant and
so cannot change which points the HPD selects. Full range on one core takes
about ten seconds; a browser window, a twentieth of a second.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from app.services.calibration import SIGMA_2, CalibrationCurve, hpd_ranges_from_distribution

# Twice the mean life of 14C, in years -- the constant in the uncertainty
# scaling law. Libby's 5568-year half-life gives a mean life of 8033 years,
# the convention conventional radiocarbon ages are reported on.
MEAN_LIFE_DOUBLED = 2 * 8033.0

# The laboratory uncertainty assumed when none is given: 25 years on a modern
# sample, what a commercial date typically carries.
#
# The reference implementation defaults to 15, about the best a good AMS lab
# offers, which is right for reproducing its published figures and wrong for
# "can I date this period well enough" -- it would quote a precision most
# samples will not get. 15 stays one click away, and the res14c parity test
# asks for it explicitly.
DEFAULT_U0 = 25.0

# 2 sigma, the level every other HPD region here is reported at, shared by
# construction rather than by a matching literal.
CONFIDENCE = SIGMA_2

# Beyond this many combined sigma a measurement contributes nothing to a
# calendar age, and masking it out is what makes this computable in a request.
SIGMA_REACH = 5.0

# Calendar-year step of the internal grid. One year: the curves are given at
# one-year resolution over the Holocene, and the whole quantity being measured
# is a width in years.
GRID_STEP = 1.0


@dataclass
class ResolutionProfile:
    """Expected resolution across a span of calendar time."""
    cal_ages: np.ndarray          # cal BP, ascending
    mean: np.ndarray              # mean 95.45% envelope width, years
    p05: np.ndarray               # 5th percentile (an unusually lucky date)
    p95: np.ndarray               # 95th percentile (an unusually unlucky one)
    uncertainty: np.ndarray       # mean laboratory sigma used, years
    u0: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            'u0': self.u0,
            'points': [
                [float(c), float(m), float(lo), float(hi), float(u)]
                for c, m, lo, hi, u in zip(
                    self.cal_ages, self.mean, self.p05, self.p95, self.uncertainty
                )
            ],
        }


def uncertainty_at(c14_age: float, u0: float = DEFAULT_U0) -> float:
    """The laboratory uncertainty expected for a measurement of this age."""
    return u0 * np.exp(c14_age / MEAN_LIFE_DOUBLED)


def _padding(curve_sigma_max: float, u_max: float) -> float:
    """
    How far outside the requested window the calculation has to reach.

    A measurement whose calendar distribution runs past the edge of the window
    would have its HPD envelope clipped there, and would report a resolution
    better than it really achieves -- the error being worst exactly where
    resolution is worst, since those are the distributions that spread
    furthest. Reaching SIGMA_REACH combined sigma beyond the window covers
    every measurement that could contribute, converted to calendar years at
    the roughly one-to-one rate the curve runs at. The floor keeps a narrow
    window on a flat stretch honest, where the local rate is far below one.

    That one-to-one conversion is the approximation. A curve running shallower
    than one spreads a distribution further in calendar years than in
    radiocarbon years, by roughly the inverse of its slope. On the three
    bundled curves the floor absorbs this -- a windowed profile matches a
    whole-curve one to about one part in a billion -- but a curve holding a
    shallow constant slope for well over a thousand years reaches one part in
    10^5 at a window's outermost point (measured in test_resolution.py, on a
    synthetic curve built to provoke it). Worth knowing before trusting this
    on a curve unlike the real ones.
    """
    return max(500.0, SIGMA_REACH * float(np.hypot(curve_sigma_max, u_max)))


def profile(
    curve: CalibrationCurve,
    cal_from: Optional[float] = None,
    cal_to: Optional[float] = None,
    u0: float = DEFAULT_U0,
    stride: int = 5,
) -> ResolutionProfile:
    """
    Expected resolution across a window of calendar time.

    Args:
        curve: the curve to measure.
        cal_from, cal_to: the window in cal BP; the whole curve if omitted.
        u0: laboratory uncertainty at 0 BP, scaled with age from there.
        stride: report every `stride` calendar years. The calculation itself
            is always at one-year resolution; this only thins what comes back.
    """
    if len(curve.cal_ages) == 0:
        raise ValueError("Calibration curve data is empty")
    if u0 <= 0:
        raise ValueError("The laboratory uncertainty must be positive")

    curve_min = float(curve.cal_ages.min())
    curve_max = float(curve.cal_ages.max())
    lo = curve_min if cal_from is None else max(float(cal_from), curve_min)
    hi = curve_max if cal_to is None else min(float(cal_to), curve_max)
    if hi < lo:
        lo, hi = hi, lo
    if hi - lo < GRID_STEP:
        hi = min(lo + GRID_STEP, curve_max)

    # How far the working grid has to reach beyond what is reported.
    reported_c14_max = float(np.interp(hi, curve.cal_ages, curve.c14_ages))
    u_max = uncertainty_at(reported_c14_max, u0)
    sigma_max = float(np.max(np.interp(
        np.linspace(lo, hi, 64), curve.cal_ages, curve.c14_errors
    )))
    pad = _padding(sigma_max, u_max)

    grid_lo = max(curve_min, lo - pad)
    grid_hi = min(curve_max, hi + pad)
    cal = np.arange(grid_lo, grid_hi + GRID_STEP, GRID_STEP)
    c14 = np.interp(cal, curve.cal_ages, curve.c14_ages)
    sigma = np.interp(cal, curve.cal_ages, curve.c14_errors)

    # The calendar ages actually reported.
    out_mask = (cal >= lo) & (cal <= hi)
    out_index = np.where(out_mask)[0][::stride]
    out_cal = cal[out_index]
    if len(out_cal) == 0:
        raise ValueError("The requested window contains no calendar years")

    # Every radiocarbon age a sample from this window could plausibly give.
    in_window = c14[out_mask]
    u_window_max = uncertainty_at(float(in_window.max()), u0)
    reach = SIGMA_REACH * float(np.hypot(sigma_max, u_window_max))
    ages = np.arange(
        max(0.0, np.floor(in_window.min() - reach)),
        np.ceil(in_window.max() + reach) + 1,
    )

    # Sparse: for each (measurement, reported year) pair that matters, the
    # width that measurement achieved and how likely the year is to have
    # produced it. Dense would be a 50,000 x 11,000 matrix, almost all zero.
    year_ids: List[np.ndarray] = []
    weights: List[np.ndarray] = []
    widths: List[np.ndarray] = []

    # Where each output year sits in the grid, for pulling its likelihood out.
    out_position = np.searchsorted(np.arange(len(cal)), out_index)

    for age in ages:
        u = u0 * np.exp(age / MEAN_LIFE_DOUBLED)
        variance = u * u + sigma * sigma
        delta = age - c14
        near = np.abs(delta) <= SIGMA_REACH * np.sqrt(variance)
        if not near.any():
            continue

        likelihood = np.exp(-(delta[near] ** 2) / (2 * variance[near])) / np.sqrt(variance[near])
        total = likelihood.sum()
        if not np.isfinite(total) or total <= 0:
            continue

        # Width of the 95.45% HPD set, gaps included -- the app's own HPD
        # construction, and on a uniform grid selecting by density and by mass
        # are the same selection.
        ranges = hpd_ranges_from_distribution(cal[near], likelihood, CONFIDENCE)
        width = max(r['max'] for r in ranges) - min(r['min'] for r in ranges)

        # Which reported years this measurement says anything about.
        contributing = np.zeros(len(cal))
        contributing[near] = likelihood
        w = contributing[out_index]
        hit = np.where(w > 0)[0]
        if len(hit) == 0:
            continue

        year_ids.append(hit.astype(np.int32))
        weights.append(w[hit].astype(np.float64))
        widths.append(np.full(len(hit), width, dtype=np.float64))

    if not year_ids:
        raise ValueError(
            "No radiocarbon age maps into this window; it may lie outside the "
            "range the curve covers."
        )

    year_id = np.concatenate(year_ids)
    weight = np.concatenate(weights)
    width = np.concatenate(widths)

    mean = np.full(len(out_cal), np.nan)
    p05 = np.full(len(out_cal), np.nan)
    p95 = np.full(len(out_cal), np.nan)
    mean_u = np.full(len(out_cal), np.nan)

    # Grouped by reported year, then the weighted mean and percentiles read
    # off the sorted widths. Exact rather than binned, since a histogram would
    # floor the precision of the numbers being reported.
    order = np.lexsort((width, year_id))
    year_id, weight, width = year_id[order], weight[order], width[order]
    boundaries = np.searchsorted(year_id, np.arange(len(out_cal) + 1))

    c14_at_out = c14[out_index]
    for i in range(len(out_cal)):
        start, end = boundaries[i], boundaries[i + 1]
        if end <= start:
            continue
        w = weight[start:end]
        r = width[start:end]
        total = w.sum()
        if total <= 0:
            continue
        mean[i] = float((w * r).sum() / total)
        cumulative = np.cumsum(w) / total
        p05[i] = float(r[int(np.searchsorted(cumulative, 0.05))])
        p95[i] = float(r[min(int(np.searchsorted(cumulative, 0.95)), len(r) - 1)])
        mean_u[i] = float(uncertainty_at(c14_at_out[i], u0))

    return ResolutionProfile(
        cal_ages=out_cal, mean=mean, p05=p05, p95=p95, uncertainty=mean_u, u0=u0
    )


# --------------------------------------------------------------------------
# The whole-curve profile, which is the only slow case
# --------------------------------------------------------------------------
#
# A browser window computes in a twentieth of a second, so it is just computed.
# The whole 55,000 years takes about twelve, too long to do twice, so it is
# cached -- keyed by the curve's CONTENT hash rather than its id, because the
# builtin curve files are overridable by environment variable and a swapped
# file must not be served a previous file's answer.
#
# Slicing it for a window is exact, not approximate: a windowed calculation
# pads far enough to return identical numbers (asserted in
# tests/test_resolution.py), so cache and direct path cannot disagree.

_FULL_CACHE: Dict[Tuple[str, float, int], ResolutionProfile] = {}
_FULL_CACHE_LIMIT = 6


def full_profile(
    curve: CalibrationCurve,
    content_sha: str,
    u0: float = DEFAULT_U0,
    stride: int = 5,
) -> ResolutionProfile:
    """The profile for a whole curve, computed once per (curve, u0, stride)."""
    key = (content_sha, float(u0), int(stride))
    hit = _FULL_CACHE.get(key)
    if hit is not None:
        return hit

    computed = profile(curve, None, None, u0=u0, stride=stride)
    if len(_FULL_CACHE) >= _FULL_CACHE_LIMIT:
        _FULL_CACHE.pop(next(iter(_FULL_CACHE)))
    _FULL_CACHE[key] = computed
    return computed


def slice_profile(source: ResolutionProfile, cal_from: float, cal_to: float) -> ResolutionProfile:
    """The part of an already-computed profile covering a window."""
    mask = (source.cal_ages >= cal_from) & (source.cal_ages <= cal_to)
    return ResolutionProfile(
        cal_ages=source.cal_ages[mask],
        mean=source.mean[mask],
        p05=source.p05[mask],
        p95=source.p95[mask],
        uncertainty=source.uncertainty[mask],
        u0=source.u0,
    )


def to_csv(source: ResolutionProfile) -> str:
    """
    The profile as CSV, in the column order res14c publishes so the two can be
    compared directly -- calendar age first, then mean, 5th and 95th
    percentile resolution and the laboratory uncertainty assumed.
    """
    lines = ["Sample Age (cal BP),Mean Resolution,5th Percentile,95th Percentile,Uncertainty Used"]
    for cal, mean, p05, p95, u in zip(
        source.cal_ages, source.mean, source.p05, source.p95, source.uncertainty
    ):
        lines.append(f"{cal:.0f},{mean:.3f},{p05:.3f},{p95:.3f},{u:.3f}")
    return "\n".join(lines) + "\n"
