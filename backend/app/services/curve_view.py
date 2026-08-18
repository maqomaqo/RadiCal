"""
Serving a piece of a calibration curve to be looked at.

A curve is up to 9,501 points spanning 55,000 years, and the whole point of
browsing one is to see the wiggles -- so the two things this module has to get
right are that a window can be asked for at all, and that reducing it to a
drawable number of points does not erase the features being examined.

Which is why decimation here is min/max per bucket rather than "every Nth
point". Stride sampling a wiggly curve removes exactly the extremes that make
it wiggly: the Hallstatt plateau's reversals, the sharp drop at its start, the
short excursions that split a calibrated date into two ranges. Taking the
lowest and highest radiocarbon age in each bucket keeps the envelope of the
curve intact at any zoom level, at the cost of two points per bucket instead of
one.

Local slope is computed on the FULL grid before any of that and carried
through, so a hover readout in a decimated view still reports the curve's real
gradient rather than one measured between two surviving points that were never
adjacent.
"""
from typing import Any, Dict, List, Optional

import numpy as np

from app.services.calibration import CalibrationCurve

# Enough to draw a curve on a wide screen without the payload getting silly.
DEFAULT_MAX_POINTS = 800

# A comparison window narrower than this is widened to it, so that two curves
# whose grids happen to have no shared point in a very tight window still
# produce a drawable line rather than nothing.
GRID_MINIMUM_SPAN = 1.0
MAX_MAX_POINTS = 5000


# How far either side of a point the slope is measured over, in calendar
# years.
#
# A point-to-point gradient is not usable for this. Where IntCal20 is on a
# 1-year grid it is dominated by the curve's own year-to-year scatter, and the
# readout swings between +4 and -3 across a stretch that is, at any scale a
# person cares about, flat. Measured on IntCal20 over three known stretches --
# the Hallstatt plateau (2450-2700 cal BP), an ordinary stretch (3400-3650)
# and the early Holocene (9500-9800) -- the mean slope each half-span reports:
#
#     half-span    plateau    ordinary    early Holocene
#        25 yr        0.27        0.87            1.08     (min -1.28)
#        50 yr        0.31        0.91            1.05     (min -0.53)
#       100 yr        0.44        0.95            0.96     (over-smoothed)
#
# 50 separates a plateau from ordinary curve by a factor of three without the
# scatter that makes 25 unreadable, and without 100's smearing of the plateau
# into its surroundings.
SLOPE_HALF_SPAN_YEARS = 50.0


def _slope(curve: CalibrationCurve, at: np.ndarray) -> np.ndarray:
    """
    d(14C age)/d(calendar year) at each requested calendar age, in radiocarbon
    years per calendar year, measured over a fixed calendar span rather than
    between adjacent grid points.

    Measuring over a span rather than a grid step also makes the value mean
    the same thing everywhere on the curve: the grid is 1 year near the
    present and 20 years beyond 25,000 cal BP, so a per-step difference would
    be describing a twenty-times longer stretch of curve at one end than the
    other.

    The value is what makes a plateau a plateau. Around 1 the curve tracks
    calendar time and a date calibrates about as tightly as it was measured;
    near 0 the curve is flat and a measurement maps to a wide span of calendar
    years; below 0 the curve runs backwards and one measurement necessarily
    splits into disjoint calendar ranges.
    """
    cal_min = float(curve.cal_ages.min())
    cal_max = float(curve.cal_ages.max())

    # Clamped at the curve's ends, with the denominator taken from the span
    # actually used, so an edge point reports a one-sided slope rather than a
    # halved one.
    lo = np.clip(at - SLOPE_HALF_SPAN_YEARS, cal_min, cal_max)
    hi = np.clip(at + SLOPE_HALF_SPAN_YEARS, cal_min, cal_max)
    span = hi - lo

    younger = np.interp(lo, curve.cal_ages, curve.c14_ages)
    older = np.interp(hi, curve.cal_ages, curve.c14_ages)

    return np.divide(
        older - younger, span,
        out=np.zeros_like(span, dtype=float),
        where=span > 0,
    )


def _decimate(indices: np.ndarray, c14: np.ndarray, max_points: int) -> np.ndarray:
    """
    Reduce a run of indices to at most `max_points`, keeping the lowest and
    highest radiocarbon age in each bucket.

    Returns the surviving indices in calendar order. The first and last points
    of the window always survive, so the drawn curve still starts and ends
    where the window does rather than at whatever the outer buckets happened
    to contain.
    """
    if len(indices) <= max_points:
        return indices

    # Two points per bucket, so the bucket count is half the budget.
    buckets = max(1, max_points // 2)
    edges = np.linspace(0, len(indices), buckets + 1).astype(int)

    keep = {int(indices[0]), int(indices[-1])}
    for start, end in zip(edges[:-1], edges[1:]):
        if end <= start:
            continue
        chunk = indices[start:end]
        values = c14[chunk]
        keep.add(int(chunk[int(np.argmin(values))]))
        keep.add(int(chunk[int(np.argmax(values))]))

    return np.array(sorted(keep), dtype=int)


def window(
    curve: CalibrationCurve,
    cal_from: Optional[float] = None,
    cal_to: Optional[float] = None,
    max_points: int = DEFAULT_MAX_POINTS,
) -> Dict[str, Any]:
    """
    A drawable slice of a curve.

    Args:
        curve: the loaded curve.
        cal_from, cal_to: the calendar window in cal BP, clamped to what the
            curve covers. Either omitted means "to the end of the curve".
        max_points: cap on returned points, decimated as described above.

    Returns a dict with `points` as [cal BP, 14C age, 1-sigma, slope] rows in
    calendar order, plus what the window actually turned out to be and how
    many points it holds before decimation -- the caller needs the latter to
    know whether it is looking at the whole curve or a summary of it.
    """
    cal = curve.cal_ages
    if len(cal) == 0:
        raise ValueError("Calibration curve data is empty")

    lo = float(cal.min()) if cal_from is None else float(cal_from)
    hi = float(cal.max()) if cal_to is None else float(cal_to)
    if hi < lo:
        lo, hi = hi, lo
    lo = max(lo, float(cal.min()))
    hi = min(hi, float(cal.max()))

    max_points = int(np.clip(max_points, 2, MAX_MAX_POINTS))

    selected = np.where((cal >= lo) & (cal <= hi))[0]
    if len(selected) < 2:
        # A window narrower than the curve's own grid spacing -- 20-year steps
        # in the old half of IntCal20 -- holds no points, or one, and neither
        # draws a line. Widen to the points that bracket it, which is what
        # interpolating across the window would use anyway. Fewer than two,
        # not zero: one point is as undrawable as none, and a window landing
        # exactly on a grid point is the common way to get one.
        if len(selected) == 0:
            right = int(np.searchsorted(cal, lo))
            left = max(0, right - 1)
        else:
            left = max(0, int(selected[0]) - 1)
            right = int(selected[0]) + 1
        right = min(len(cal) - 1, right)
        selected = np.arange(left, right + 1)

    kept = _decimate(selected, curve.c14_ages, max_points)
    # Measured against the whole curve, not the window, so a point at the
    # window's edge is not described by a slope that stops where the view
    # does.
    kept_slopes = _slope(curve, cal[kept])

    points = [
        [float(cal[i]), float(curve.c14_ages[i]), float(curve.c14_errors[i]), float(s)]
        for i, s in zip(kept, kept_slopes)
    ]

    return {
        'cal_from': float(cal[selected[0]]),
        'cal_to': float(cal[selected[-1]]),
        'points': points,
        'point_count': len(points),
        'points_in_window': int(len(selected)),
        'decimated': len(kept) < len(selected),
        'curve_cal_min': float(cal.min()),
        'curve_cal_max': float(cal.max()),
    }


def compare(
    curve_a: CalibrationCurve,
    curve_b: CalibrationCurve,
    cal_from: Optional[float] = None,
    cal_to: Optional[float] = None,
    max_points: int = DEFAULT_MAX_POINTS,
) -> Dict[str, Any]:
    """
    Two curves on one grid, with their difference.

    The difference is what the comparison is actually for. Two calibration
    curves drawn over each other look much alike at any zoom that fits a
    millennium on screen -- the hemispheric offset between IntCal20 and
    SHCal20 is a few decades against a vertical axis spanning thousands of
    years, and the eye cannot read it off two nearly-coincident lines. Plotted
    as A - B it is legible immediately, which is the difference between "these
    look similar" and "these differ by 40 +/- 12 years here".

    Decimation keeps the extremes of the DIFFERENCE rather than of either
    curve, since that is the series being examined; the two curves are
    reported at whichever points survive.

    Returns rows of [cal BP, A, sigma_A, B, sigma_B, A - B, sigma of the
    difference], the last being the two curve errors in quadrature.
    """
    lo_curve = max(float(curve_a.cal_ages.min()), float(curve_b.cal_ages.min()))
    hi_curve = min(float(curve_a.cal_ages.max()), float(curve_b.cal_ages.max()))
    if hi_curve <= lo_curve:
        raise ValueError(
            "These two curves do not overlap in calendar time, so there is "
            "nothing to compare."
        )

    lo = lo_curve if cal_from is None else max(float(cal_from), lo_curve)
    hi = hi_curve if cal_to is None else min(float(cal_to), hi_curve)
    if hi < lo:
        lo, hi = hi, lo

    grid = np.union1d(curve_a.cal_ages, curve_b.cal_ages)
    grid = grid[(grid >= lo) & (grid <= hi)]
    if len(grid) < 2:
        grid = np.linspace(lo, max(hi, lo + GRID_MINIMUM_SPAN), 2)

    a_c14, a_sigma = curve_a.interpolate(grid)
    b_c14, b_sigma = curve_b.interpolate(grid)
    difference = a_c14 - b_c14
    difference_sigma = np.hypot(a_sigma, b_sigma)

    kept = _decimate(np.arange(len(grid)), difference, int(np.clip(max_points, 2, MAX_MAX_POINTS)))

    return {
        'cal_from': float(grid[0]),
        'cal_to': float(grid[-1]),
        'points': [
            [
                float(grid[i]), float(a_c14[i]), float(a_sigma[i]),
                float(b_c14[i]), float(b_sigma[i]),
                float(difference[i]), float(difference_sigma[i]),
            ]
            for i in kept
        ],
        'point_count': int(len(kept)),
        'points_in_window': int(len(grid)),
        'decimated': bool(len(kept) < len(grid)),
        'overlap_cal_min': lo_curve,
        'overlap_cal_max': hi_curve,
    }


def to_csv(window: Dict[str, Any], curve_name: str) -> str:
    """The visible window as CSV, in the .14c column order with the local
    gradient alongside -- so a stretch of curve can leave this app as data,
    not only as a picture."""
    lines = [f'# {curve_name}', '# CAL BP,14C age,Sigma,Slope (14C yr / cal yr)']
    for cal, c14, sigma, slope in window['points']:
        lines.append(f'{cal:g},{c14:g},{sigma:g},{slope:.4f}')
    return '\n'.join(lines) + '\n'
