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
measurement contributes nothing and is skipped -- found by a banded search
rather than by testing them all (see _reach_bands) -- and the calculation runs
on a uniform 1-year grid, where `calibrate`'s trapezoidal weights are constant
and so cannot change which points the HPD selects. A browser window costs a
few hundredths of a second; the full 55,000 years about eight, computed once
and cached.
"""
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from app.services.calibration import SIGMA_2, CalibrationCurve

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


# --------------------------------------------------------------------------
# Finding the stretches of curve a measurement can have come from
# --------------------------------------------------------------------------
#
# The obvious loop tests every calendar year against every radiocarbon age,
# which is 50,000 x 55,000 comparisons for a whole curve and was most of the
# time this took. A measurement only reaches SIGMA_REACH combined sigma, so
# the candidates are the grid points whose curve value is within that of it --
# a contiguous slice of the grid sorted by radiocarbon age.
#
# Sorted in BANDS of curve error rather than as one list, because the reach
# depends on that error and a single global maximum is set by the late-glacial
# end of the curve, where it is fifty times the Holocene value. One list would
# hand every Holocene measurement thousands of candidates it must then reject;
# banded, it gets hundreds. The bands only have to bracket the reach -- the
# exact test still runs on what they return -- so their edges are arbitrary.
_BAND_EDGES = (20.0, 40.0, 80.0, 160.0, 320.0)


def _reach_bands(
    c14: np.ndarray, sigma: np.ndarray
) -> List[Tuple[np.ndarray, np.ndarray, float]]:
    """Grid indices grouped by curve error, each sorted by radiocarbon age."""
    bands: List[Tuple[np.ndarray, np.ndarray, float]] = []
    lower = -np.inf
    for upper in (*_BAND_EDGES, np.inf):
        member = np.flatnonzero((sigma > lower) & (sigma <= upper))
        lower = upper
        if member.size == 0:
            continue
        member = member[np.argsort(c14[member], kind='stable')]
        bands.append((member, c14[member], float(sigma[member].max())))
    return bands


def _candidates(
    bands: List[Tuple[np.ndarray, np.ndarray, float]], age: float, u: float
) -> np.ndarray:
    """
    Grid points a measurement of `age` could plausibly have come from.

    A superset of the answer -- each band is taken at its own worst error --
    returned in ascending grid order, since the HPD construction downstream
    reads the calendar ages as a sorted series.
    """
    parts: List[np.ndarray] = []
    for member, band_c14, band_sigma_max in bands:
        reach = SIGMA_REACH * float(np.hypot(band_sigma_max, u))
        first = int(np.searchsorted(band_c14, age - reach, side='left'))
        last = int(np.searchsorted(band_c14, age + reach, side='right'))
        if last > first:
            parts.append(member[first:last])
    if not parts:
        return np.empty(0, dtype=np.int64)
    return np.sort(parts[0] if len(parts) == 1 else np.concatenate(parts))


def _hpd_width(ages: np.ndarray, weights: np.ndarray, total: float) -> float:
    """
    How far the 95.45% HPD set spans, gaps included.

    hpd_ranges_from_distribution's selection without its ranges: this runs
    once per radiocarbon age -- tens of thousands of times for one profile --
    and everything that function builds between the first and last point is
    thrown away here. The selection is identical, normalising before
    accumulating as it does, so the width is too.
    """
    normalised = weights / total
    order = np.argsort(-normalised)
    cumulative = np.cumsum(normalised[order])
    # The first point at which the level is met; if it never is -- which a few
    # zero weights can cause -- the whole distribution is the set.
    reached = int(np.searchsorted(cumulative, CONFIDENCE, side='left'))
    selected = order[:reached + 1] if reached < len(cumulative) else order
    chosen = ages[selected]
    return float(chosen.max() - chosen.min())


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

    # Which reported year each grid point is, or -1 where it is only padding.
    # A measurement's contribution is then gathered straight from its own
    # points, rather than scattered across a grid-length array and read back.
    slot = np.full(len(cal), -1, dtype=np.int64)
    slot[out_index] = np.arange(len(out_cal))

    bands = _reach_bands(c14, sigma)
    sigma_sq = sigma * sigma

    for age in ages:
        u = u0 * np.exp(age / MEAN_LIFE_DOUBLED)
        candidate = _candidates(bands, age, u)
        if candidate.size == 0:
            continue

        variance = u * u + sigma_sq[candidate]
        delta = age - c14[candidate]
        keep = np.abs(delta) <= SIGMA_REACH * np.sqrt(variance)
        near = candidate[keep]
        if near.size == 0:
            continue
        variance = variance[keep]
        delta = delta[keep]

        likelihood = np.exp(-(delta ** 2) / (2 * variance)) / np.sqrt(variance)
        total = likelihood.sum()
        if not np.isfinite(total) or total <= 0:
            continue

        # Width of the 95.45% HPD set, gaps included -- the app's own HPD
        # construction, and on a uniform grid selecting by density and by mass
        # are the same selection.
        width = _hpd_width(cal[near], likelihood, total)

        # Which reported years this measurement says anything about.
        out_slot = slot[near]
        hit = np.flatnonzero((out_slot >= 0) & (likelihood > 0))
        if hit.size == 0:
            continue

        year_ids.append(out_slot[hit].astype(np.int32))
        weights.append(likelihood[hit].astype(np.float64))
        widths.append(np.full(hit.size, width, dtype=np.float64))

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
# A browser window computes in a fiftieth of a second, so it is just computed.
# The whole 55,000 years takes about eight, too long to do twice, so it is
# cached -- keyed by the curve's CONTENT hash rather than its id, because the
# builtin curve files are overridable by environment variable and a swapped
# file must not be served a previous file's answer.
#
# Slicing it for a window is exact, not approximate: a windowed calculation
# pads far enough to return identical numbers (asserted in
# tests/test_resolution.py), so cache and direct path cannot disagree.
#
# The eight seconds buys a `stride` that costs nothing, so the cache is keyed
# WITHOUT one: the whole curve is computed once on a BASE_STRIDE lattice and
# thinned from there. `stride` decides how many points come back, and a
# browser picks it from its own pixel width -- so keying on it meant a window
# a few pixels wider recomputed all 55,000 years to draw the same picture.

# The lattice a whole-curve profile is computed on. It reports every fifth
# year: at the spans that reach this path -- twenty thousand years and up,
# sixty years to a pixel -- nothing finer is visible, and a whole curve at
# one-year output holds seventy-five million (measurement, year) pairs, which
# is gigabytes. Windows narrower than that are computed directly, at whatever
# stride was asked for.
BASE_STRIDE = 5

_BASE_CACHE: Dict[Tuple[str, float], ResolutionProfile] = {}
_FULL_CACHE: Dict[Tuple[str, float, int], ResolutionProfile] = {}
_FULL_CACHE_LIMIT = 6

# Held while a whole curve is computed, so two browsers asking at once wait
# for one run rather than each starting their own.
_FULL_LOCK = threading.Lock()


def has_full_profile(content_sha: str, u0: float = DEFAULT_U0) -> bool:
    """Whether a whole-curve profile is already computed and can be sliced."""
    return (content_sha, float(u0)) in _BASE_CACHE


def full_profile(
    curve: CalibrationCurve,
    content_sha: str,
    u0: float = DEFAULT_U0,
    stride: int = BASE_STRIDE,
) -> ResolutionProfile:
    """
    The profile for a whole curve, computed once per (curve, u0).

    Reported every `stride` years, thinned from the BASE_STRIDE lattice the
    calculation runs on -- so a stride finer than that, or not a multiple of
    it, comes back on the nearest multiple rather than exactly as asked.
    """
    key = (content_sha, float(u0), int(stride))
    hit = _FULL_CACHE.get(key)
    if hit is not None:
        return hit

    with _FULL_LOCK:
        # Another request may have finished this one while the lock was held.
        hit = _FULL_CACHE.get(key)
        if hit is not None:
            return hit

        base_key = (content_sha, float(u0))
        base = _BASE_CACHE.get(base_key)
        if base is None:
            base = profile(curve, None, None, u0=u0, stride=BASE_STRIDE)
            if len(_BASE_CACHE) >= _FULL_CACHE_LIMIT:
                _BASE_CACHE.pop(next(iter(_BASE_CACHE)))
            _BASE_CACHE[base_key] = base

        computed = _thinned(base, max(1, round(int(stride) / BASE_STRIDE)))
        if len(_FULL_CACHE) >= _FULL_CACHE_LIMIT:
            _FULL_CACHE.pop(next(iter(_FULL_CACHE)))
        _FULL_CACHE[key] = computed
        return computed


def _thinned(source: ResolutionProfile, every: int) -> ResolutionProfile:
    """Every `every`-th point of a profile, or the profile itself at 1."""
    if every <= 1:
        return source
    return ResolutionProfile(
        cal_ages=source.cal_ages[::every],
        mean=source.mean[::every],
        p05=source.p05[::every],
        p95=source.p95[::every],
        uncertainty=source.uncertainty[::every],
        u0=source.u0,
    )


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
