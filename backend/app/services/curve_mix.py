"""
Curves made by mixing two others, for samples whose carbon came from more
than one reservoir.

The archaeological case is a human or animal with a partly marine diet. Their
bone is neither terrestrial nor marine carbon: IntCal20 ignores a reservoir
offset of several hundred years, Marine20 applies all of one they only partly
experienced, and both are wrong by centuries in opposite directions.

OxCal's answer is Mix_Curves(name, A, B, percent, error), and this is the same
construction:

    c14_mix(t)   = p * A(t) + (1 - p) * B(t)
    sigma_mix(t) = sqrt( p^2 sigma_A(t)^2
                       + (1-p)^2 sigma_B(t)^2
                       + ((A(t) - B(t)) * sigma_p)^2 )

The third term under the root earns its keep: a dietary fraction is estimated
from stable isotopes with real uncertainty, and that uncertainty matters most
where the two curves are furthest apart, because that is where being wrong
about the proportion moves the answer furthest. Without it a mixed curve would
state a confident age from an admittedly uncertain diet -- the same argument
MeasurementInput.delta_r makes for a reservoir offset.

A mixed curve is stored as its recipe rather than as data (see
models.StoredCalibrationCurve), so it stays honest about its parents and they
count as in use for as long as it exists.
"""
from typing import Tuple

import numpy as np

from app.services.calibration import CalibrationCurve


def mix(
    curve_a: CalibrationCurve,
    curve_b: CalibrationCurve,
    percent_a: float,
    percent_a_error: float = 0.0,
    name: str = 'Mixed',
) -> CalibrationCurve:
    """
    Combine two curves in a fixed proportion.

    Args:
        curve_a, curve_b: the curves to mix.
        percent_a: how much of the carbon came from A, 0-100. OxCal states
            this as a percentage of the FIRST curve, and so does this.
        percent_a_error: 1-sigma uncertainty on that percentage.
        name: for the resulting curve's own label.

    The result is defined on the union of the two grids, restricted to their
    overlap. Union rather than either alone because the curves are not sampled
    alike -- IntCal20 steps 1 year where Marine20 steps 10 -- and the coarser
    grid would throw away detail the mix can carry.
    """
    if not (0.0 <= percent_a <= 100.0):
        raise ValueError("The proportion must be between 0 and 100 percent")
    if percent_a_error < 0:
        raise ValueError("The uncertainty on the proportion cannot be negative")
    if len(curve_a.cal_ages) == 0 or len(curve_b.cal_ages) == 0:
        raise ValueError("Both curves must have data to be mixed")

    lo = max(float(curve_a.cal_ages.min()), float(curve_b.cal_ages.min()))
    hi = min(float(curve_a.cal_ages.max()), float(curve_b.cal_ages.max()))
    if hi <= lo:
        raise ValueError(
            "These two curves do not overlap in calendar time, so there is no "
            "span over which a mixture of them could be calibrated."
        )

    grid = np.union1d(curve_a.cal_ages, curve_b.cal_ages)
    grid = grid[(grid >= lo) & (grid <= hi)]

    a_c14, a_sigma = curve_a.interpolate(grid)
    b_c14, b_sigma = curve_b.interpolate(grid)

    p = percent_a / 100.0
    sigma_p = percent_a_error / 100.0

    c14 = p * a_c14 + (1.0 - p) * b_c14
    variance = (
        (p * a_sigma) ** 2
        + ((1.0 - p) * b_sigma) ** 2
        + ((a_c14 - b_c14) * sigma_p) ** 2
    )

    mixed = CalibrationCurve(grid, c14, np.sqrt(variance))
    mixed.curve_name = name
    return mixed


def describe(name_a: str, name_b: str, percent_a: float, percent_a_error: float) -> str:
    """A one-line account of a mixed curve, for its stored description, so a
    curve in the library says what it is made of without opening its recipe."""
    error = f" ± {percent_a_error:g}%" if percent_a_error else ""
    return (
        f"{percent_a:g}%{error} {name_a}, "
        f"{100 - percent_a:g}% {name_b}"
    )
