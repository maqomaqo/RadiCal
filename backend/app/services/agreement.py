"""
Agreement indices -- how well each modelled date still fits the measurement
it came from, and how well the model as a whole does.

A Bayesian model moves every date, which means one that does not belong --
residual charcoal, an intrusive fragment, a lab error -- gets dragged into line
with its neighbours and reported with a confident range. The agreement index is
the standard warning (Bronk Ramsey 1995): it compares each date's MODELLED
posterior against its own UNMODELLED likelihood, so a date the model had to
move a long way scores low however tidy its final range looks.

    A_i = 100 * sum(modelled * unmodelled) / sum(unmodelled^2)

with both distributions normalised over a shared calendar grid. A date the
model leaves alone scores 100; one pushed off its likelihood scores low; and
a date the model SHARPENS onto the peak of its own likelihood scores above
100, which is why values over 100 are normal and not a bug. Below about 60 is
the conventional threshold for taking a second look.

What it does NOT do: it detects a date the MODEL HAD TO MOVE, which is not the
same as a date that does not belong. On a single unconstrained phase, a date
600 radiocarbon years from its four neighbours scores 100.5, because the phase
simply widens and nothing is forced anywhere; put the same date where a
constraint bites and it collapses to zero. This is a diagnostic for conflict
between the data and the model's STRUCTURE -- finding a date that is merely
wrong needs an outlier model instead.

The overall index combines them:

    A_overall = 100 * (product of A_i/100) ^ (1/sqrt(n))

Both formulas were identified against the OxCal 4.4 runs in
tests/fixtures/oxcal, which hold 24 known answers across three models. The
individual indices are reproduced to rms 0.81 and worst 1.4, the overall ones
to 0.1; tests/test_agreement.py asserts that.

NOT implemented: OxCal's `Amodel`. It cannot be derived from the per-date
indices -- on the `ordered` fixture their product exceeds one, so no exponent
of it reaches the 96.0 OxCal reports, meaning Amodel carries a term for the
model structure itself. A guess at the headline number archaeologists screen on
would be worse than reporting nothing.
"""
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from app.services.calibration import get_curve

# Calendar step the comparison is made on. OxCal's exports are at 5 years and
# its indices are reproduced most closely there (rms 0.81 against 1.15 at one
# year), a finer grid measuring something slightly different.
COMPARISON_STEP_YEARS = 5.0
# Likelihood below this fraction of its peak is outside the compared window.
SUPPORT_THRESHOLD = 1e-6
# Conventional threshold for a date worth re-examining (Bronk Ramsey 1995).
POOR_AGREEMENT_BELOW = 60.0
# Floor applied before taking logs in the overall index. A date pushed clean
# off its likelihood scores a true zero, and dropping those would remove
# precisely the worst dates and let a broken model report a healthy overall.
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
    lik = np.exp(-0.5 * (c14 - (measurement['age_bp'] - delta_r)) ** 2 / sigma2) / np.sqrt(sigma2)
    # Nothing beyond the curve's ends; interpolate() clamps rather than
    # extrapolates there (see exact_chain._date_likelihood).
    return np.where((grid >= curve.cal_ages[0]) & (grid <= curve.cal_ages[-1]), lik, 0.0)


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
    Normally left out and computed from the measurement; passing one holds the
    comparison against a likelihood from elsewhere, which is how the formula is
    checked against OxCal's exports without our own calibration differences
    muddying the test.

    Returns None rather than a number when there is nothing meaningful to
    compare, so a caller can leave the field empty rather than publish a
    fabricated score.

    The comparison window spans BOTH distributions and each is normalised over
    the whole of it. Normalising the likelihood over only the posterior's
    support is the tempting shortcut and is wrong: a date pushed right off its
    likelihood would have it renormalised over the sliver where the two still
    overlap and score well for it, hiding the disagreement this exists to show.
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

    Computed in log space, since the product runs over every date and would
    otherwise underflow on a large model.

    The 1/sqrt(n) exponent multiplies any systematic per-date bias by sqrt(n):
    on the eight-date fixture a per-date offset of about +0.7 arrives here as
    roughly +2. Fed OxCal's own indices the rule reproduces its overall to 0.1,
    so that is amplification rather than error in the combination.
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
    overall index. Works off `probability_distribution`, which both solvers
    produce, so the two report the same diagnostic.
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
