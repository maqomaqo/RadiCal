"""
Agreement indices -- how well each modelled date still fits the measurement
it came from, and how well the model as a whole does.

A Bayesian chronological model moves every date. That is the point of it, but
it also means a date that simply does not belong -- residual charcoal, an
intrusive fragment, a lab error -- gets dragged into line with its neighbours
and reported with a confident range, with nothing in the output to say the
data ever objected. The agreement index is the standard warning
(Bronk Ramsey 1995): it compares each date's MODELLED posterior against its
own UNMODELLED likelihood, so a date the model had to move a long way scores
low regardless of how tidy its final range looks.

    A_i = 100 * sum(modelled * unmodelled) / sum(unmodelled^2)

with both distributions normalised over a shared calendar grid. A date the
model leaves alone scores 100; one pushed off its likelihood scores low; and
a date the model SHARPENS onto the peak of its own likelihood scores above
100, which is why values over 100 are normal and not a bug. Below about 60 is
the conventional threshold for taking a second look.

What it does NOT do, which matters if you are reaching for it to find
outliers: it detects a date the MODEL HAD TO MOVE, which is not the same as
a date that does not belong. Measured on a single unconstrained phase, a
date 600 radiocarbon years away from its four neighbours scores 100.5 --
because a lone phase simply widens to accommodate it and nothing is forced
anywhere. Put the same outlier where a constraint bites, in the younger half
of an ordered sequence, and it collapses to zero along with everything it
contradicts. So this is a diagnostic for conflict between the data and the
model's structure. Finding a date that is merely wrong, in a model with
enough freedom to swallow it, needs an outlier model instead.

The overall index combines them:

    A_overall = 100 * (product of A_i/100) ^ (1/sqrt(n))

Both formulas were identified from the OxCal 4.4 runs in
tests/fixtures/oxcal rather than taken on trust: the fixture holds each
date's likelihood, its posterior and the agreement index OxCal reported, for
three separate models, so 24 known answers were available to check against.
Reproduced to rms 0.81 and worst 1.4 on the individual indices, and to 0.1 on
all three overall indices. See tests/test_agreement.py, which asserts exactly
that against the fixture.

NOT implemented: OxCal's `Amodel`. It is a different quantity that cannot be
derived from the per-date indices -- on the fixture's `ordered` run the
product of those indices is greater than one, so no exponent of it can reach
the 96.0 OxCal reports, meaning Amodel carries a term for the model structure
itself. Reporting a guess for the headline number archaeologists screen on
would be worse than reporting nothing.
"""
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from app.services.calibration import get_curve

# Calendar step the comparison is made on. OxCal's own exports are at 5
# years and its reported indices are reproduced most closely at that step
# (rms 0.81 against 1.15 at one year), which is unsurprising -- a finer grid
# is measuring something slightly different from what OxCal measured.
COMPARISON_STEP_YEARS = 5.0
# Likelihood below this fraction of its peak is outside the compared window.
SUPPORT_THRESHOLD = 1e-6
# Conventional threshold for a date worth re-examining (Bronk Ramsey 1995).
POOR_AGREEMENT_BELOW = 60.0
# Floor applied before taking logs in the overall index. A date the model
# pushed clean off its likelihood scores a true zero, and dropping those as
# unusable would remove precisely the worst dates and make a broken model
# report a healthy overall -- so they are floored rather than skipped.
MINIMUM_FOR_OVERALL = 0.01


def unmodelled_likelihood(measurement: Dict, grid: np.ndarray) -> np.ndarray:
    """
    The date's own calibration likelihood on `grid` -- what it would say with
    no model at all, which is what the modelled posterior is judged against.
    """
    curve = get_curve(measurement.get('curve_id') or 'intcal20')
    delta_r = measurement.get('delta_r') or 0.0
    delta_r_error = measurement.get('delta_r_error') or 0.0
    c14, curve_error = curve.interpolate(grid)
    sigma2 = curve_error ** 2 + measurement['error_1sigma'] ** 2 + delta_r_error ** 2
    return np.exp(-0.5 * (c14 - (measurement['age_bp'] - delta_r)) ** 2 / sigma2) / np.sqrt(sigma2)


def _support(measurement: Dict) -> Tuple[float, float]:
    """Where the date's likelihood has any meaningful mass, in cal BP."""
    curve = get_curve(measurement.get('curve_id') or 'intcal20')
    cal, prob = curve.calibrate(
        measurement['age_bp'], measurement['error_1sigma'],
        measurement.get('delta_r'), measurement.get('delta_r_error'),
    )
    significant = np.where(prob >= prob.max() * SUPPORT_THRESHOLD)[0]
    if not len(significant):
        return float(cal[0]), float(cal[-1])
    return float(cal[significant[0]]), float(cal[significant[-1]])


def agreement_index(
    measurement: Dict,
    modelled: Sequence[Sequence[float]],
    unmodelled: Optional[Sequence[Sequence[float]]] = None,
) -> Optional[float]:
    """
    A_i for one date. `modelled` is its posterior as [[cal_age, density], ...]
    -- the `probability_distribution` both solver paths already return.

    `unmodelled` is the likelihood to judge it against, in the same form.
    It is normally left out and computed from the measurement; passing one
    is for holding the comparison against a likelihood from elsewhere, which
    is how the formula itself is checked against OxCal's exports without our
    own calibration differences muddying the test.

    Returns None rather than a number when there is nothing meaningful to
    compare, so a caller can leave the field empty instead of publishing a
    fabricated score.

    The comparison window spans BOTH distributions, and each is normalised
    over the whole of it. Normalising the likelihood over only the posterior's
    own support would be the tempting shortcut and is actively wrong: a date
    the model has pushed right off its likelihood would then have that
    likelihood renormalised over precisely the sliver where the two still
    overlap, and would score well for it -- hiding exactly the disagreement
    this exists to surface.
    """
    modelled = np.asarray(modelled, dtype=float)
    if modelled.ndim != 2 or len(modelled) < 2:
        return None

    supplied = None if unmodelled is None else np.asarray(unmodelled, dtype=float)
    if supplied is not None:
        lo_support = float(supplied[:, 0].min())
        hi_support = float(supplied[:, 0].max())
    else:
        lo_support, hi_support = _support(measurement)
    lo = min(float(modelled[:, 0].min()), lo_support)
    hi = max(float(modelled[:, 0].max()), hi_support)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return None

    grid = np.arange(lo, hi + COMPARISON_STEP_YEARS, COMPARISON_STEP_YEARS)
    if supplied is not None:
        observed = np.interp(grid, supplied[:, 0], supplied[:, 1], left=0.0, right=0.0)
    else:
        observed = unmodelled_likelihood(measurement, grid)
    fitted = np.interp(grid, modelled[:, 0], modelled[:, 1], left=0.0, right=0.0)

    observed_total, fitted_total = observed.sum(), fitted.sum()
    if observed_total <= 0 or fitted_total <= 0:
        return None
    observed = observed / observed_total
    fitted = fitted / fitted_total

    denominator = float((observed ** 2).sum())
    if denominator <= 0:
        return None
    return float(100.0 * (fitted * observed).sum() / denominator)


def overall_agreement(indices: Iterable[Optional[float]]) -> Optional[float]:
    """
    The whole model's agreement, combining the per-date indices.

    Computed in log space: the product runs over every date and underflows
    for a large model otherwise.

    Note the 1/sqrt(n) exponent multiplies any systematic per-date bias by
    sqrt(n) -- on the eight-date fixture our average per-date offset of about
    +0.7 against OxCal arrives here as roughly +2. Fed OxCal's own published
    indices the rule reproduces its overall to 0.1, so that is the per-date
    difference being amplified rather than error in the combination.
    """
    usable = [a for a in indices if a is not None]
    if not usable:
        return None
    floored = np.maximum(np.asarray(usable, dtype=float), MINIMUM_FOR_OVERALL)
    logs = np.log(floored / 100.0)
    return float(100.0 * np.exp(logs.sum() / np.sqrt(len(usable))))


def attach(results: Dict[str, Dict], measurements: List[Dict]) -> Optional[float]:
    """
    Add `agreement_index` to each per-date result in place and return the
    overall index. Works off `probability_distribution`, which both the exact
    solver and the MCMC path produce, so the two report the same diagnostic.
    """
    indices = []
    for m in measurements:
        entry = results.get(m['id'])
        if entry is None:
            continue
        index = agreement_index(m, entry.get('probability_distribution') or [])
        entry['agreement_index'] = index
        indices.append(index)
    return overall_agreement(indices)
