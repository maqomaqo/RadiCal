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

A_overall is an approximation, and its error is not small: it treats the dates
as independent, which a model is precisely what makes them not. OxCal's
`Amodel` is the same index computed on the JOINT distribution instead, over
every date at once:

    A_model = 100 * (E_posterior[product of L_i(t_i)] / product of sum(L_i^2))
                    ^ (1/sqrt(n))

with each L_i the date's own normalised likelihood. In one dimension that IS
A_i, and with a model that leaves the dates independent it returns A_overall;
what separates the two is the correlation the model's structure puts between
the dates -- which is why Amodel drops below Aoverall exactly when constraints
are doing work (100.4 against 96.0 on the `ordered` fixture) and the two
converge when they are not (107.0 against 106.6 on `unordered`, whose branches
are unordered). That also settles the earlier reading of this file, that Amodel
could not be reached from the per-date indices: it cannot, because it is not a
function of them.

The expectation is a Monte Carlo average over the run's own joint states, so
Amodel carries sampling error where A_i and A_overall do not -- about +/-0.5 at
the 20,000 joint draws both solvers keep (tests/test_agreement.py pins it).
Both solvers return those states as `joint_draws`, the same draws the duration
and age-depth sections use, and this module scores them.

Dates under an OUTLIER MODEL are judged against the likelihood the model
actually gave them, not their bare calibration. An outlier model is an
instruction to move a date -- Charcoal at prior 1 says "this wood is older than
its context by some unknown amount" -- and scoring the move it asked for as a
failure turns the diagnostic against the analyst who set it up. So the
comparison likelihood is the date's calibration mixed with its outlier
component, weighted by the POSTERIOR probability that it is an outlier:

    L_compare = (1 - q) * L_calibration + q * L_outlier,  q = P(outlier | data)

The posterior rather than the prior, because that is the state the model
settled on: OxCal scores a date against its likelihood as shifted by the
outlier state it sampled, and averaged over the run that is this mixture at q.
Measured against OxCal's own run with an outlier model active
(tests/fixtures/oxcal_outlier, Shag River Mouth, whose outlier OxCal gives 5.3
and a 99% outlier probability), fed OxCal's own posteriors: the bare
calibration gives A_overall 15.4, a prior-weighted mixture 32.0, and the
posterior-weighted one 79.2, against OxCal's 82.9. A date with no outlier model
is compared against its bare calibration exactly as before.
"""
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from app.services.calibration import get_curve
from app.services.exact_chain import DEFAULT_OUTLIER_MODEL, _with_outlier_component

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
# Density given to a joint draw the date's own likelihood puts no mass at,
# where a true zero would take the whole product to -inf and lose every other
# draw with it.
_UNSUPPORTED = 1e-300


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


def comparison_likelihood(
    measurement: Dict,
    grid: np.ndarray,
    outlier_probability: Optional[float] = None,
) -> np.ndarray:
    """
    The likelihood a date's modelled posterior is judged against, on `grid`.

    Its bare calibration, unless it carries an outlier model: then that
    calibration mixed with the same outlier component the solvers use
    (exact_chain._with_outlier_component, so the two cannot drift), weighted by
    `outlier_probability` -- the POSTERIOR chance the date is an outlier, see
    the module docstring for why. Falls back to the prior when no posterior is
    known, which is the best available statement of what the model was told.

    The element's TPQ/TAQ bounds are deliberately NOT applied, though the
    solvers' own likelihood tables carry them: a bound is part of the model, and
    judging a date against a likelihood already truncated by it would hide
    exactly the conflict with the bound this exists to show.
    """
    lik = unmodelled_likelihood(measurement, grid)
    prior = measurement.get('outlier_prior') or 0.0
    if prior <= 0:
        return lik
    weight = prior if outlier_probability is None else float(outlier_probability)
    if weight <= 0:
        return lik
    step = float(grid[1] - grid[0]) if len(grid) > 1 else COMPARISON_STEP_YEARS
    total = lik.sum() * step
    if total <= 0:
        return lik
    # Normalised as the solvers' tables are before the component is mixed in,
    # so the two halves of the mixture are on the scale it is defined on.
    mixture, _component = _with_outlier_component(
        grid, lik / total, min(weight, 1.0),
        measurement.get('outlier_model') or DEFAULT_OUTLIER_MODEL)
    return mixture


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


def _comparison_grid(lo: float, hi: float, anchor: float = 0.0) -> np.ndarray:
    """
    The calendar grid a date is compared on, spanning [lo, hi].

    Its nodes sit on a FIXED lattice through `anchor` rather than starting at
    `lo` itself. A grid that started at the window's own edge moved every node
    with it, and since the likelihood is normalised over the nodes, the same two
    distributions compared over a window three years wider scored about 2%
    differently -- which is also what stopped A_model, whose window comes from
    the draws, from reducing to A_i on a single date.

    `anchor` is a node of a distribution being compared, when the caller has one
    it did not compute itself: landing on that distribution's own nodes reads it
    as exported rather than interpolating it, which matters for the OxCal
    fixtures, whose export is on half-year cal BP values.
    """
    step = COMPARISON_STEP_YEARS
    start = anchor + np.floor((lo - anchor) / step) * step
    return np.arange(start, hi + step, step)


def agreement_index(
    measurement: Dict,
    modelled: Sequence[Sequence[float]],
    unmodelled: Optional[Sequence[Sequence[float]]] = None,
    outlier_probability: Optional[float] = None,
) -> Optional[float]:
    """
    A_i for one date. `modelled` is its posterior as [[cal_age, density], ...]
    -- the `probability_distribution` both solver paths already return.

    `unmodelled` is the likelihood to judge it against, in the same form.
    Normally left out and computed from the measurement (comparison_likelihood,
    which takes `outlier_probability` when the date carries an outlier model);
    passing one holds the comparison against a likelihood from elsewhere, which
    is how the formula is checked against OxCal's exports without our own
    calibration differences muddying the test.

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
    # The posterior's extent is where it has meaningful mass, by the same rule
    # the likelihood's is, rather than wherever its array happens to end. That
    # matters once a date carries an outlier component: the mixture's tail runs
    # far past the calibration, so the window's width moves the score, and a
    # posterior stored with a long run of near-zero tail would otherwise be
    # judged over a different window from the same posterior read off the
    # run's draws -- which is what model_agreement sees.
    meaningful = modelled[modelled[:, 1] >= modelled[:, 1].max() * SUPPORT_THRESHOLD, 0]
    if not len(meaningful):
        return None
    lo = min(float(meaningful.min()), lo_support)
    hi = max(float(meaningful.max()), hi_support)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return None

    grid = _comparison_grid(lo, hi, anchor=lo_support if supplied is not None else 0.0)
    if supplied is not None:
        observed = np.interp(grid, supplied[:, 0], supplied[:, 1], left=0.0, right=0.0)
    else:
        observed = comparison_likelihood(measurement, grid, outlier_probability)
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


def model_agreement(
    measurements: List[Dict],
    draws: Dict[str, Sequence[float]],
    outlier_probability: Optional[Dict[str, Optional[float]]] = None,
) -> Optional[float]:
    """
    A_model: the agreement index of the whole model, from its JOINT posterior.

    `draws` maps a date's id to the calendar ages it took across the run's
    joint states -- one state per draw, every date in it at once, which is
    what carries the correlation the model imposed. Both solvers keep such
    states (gibbs.run's `collect_draws`, exact_chain's own boundary and date
    draws), and this is read off the same set the duration and age-depth
    sections use, so the three cannot describe different runs.

    Where A_overall multiplies the per-date indices and so assumes the dates
    are independent, this is the same index computed over all of them at once:
    the posterior expectation of the product of their likelihoods, against what
    that product would be if the model had left each date alone. Dates missing
    from `draws`, or with no likelihood to speak of, are left out of both the
    product and the count of n, exactly as A_overall leaves out a date whose
    index could not be computed.

    `outlier_probability` maps a date's id to its posterior outlier
    probability, so a date under an outlier model is judged against the same
    likelihood its A_i is (comparison_likelihood).

    Returns None when no date could be included, rather than a number standing
    for nothing.
    """
    outlier_probability = outlier_probability or {}
    log_joint: Optional[np.ndarray] = None
    log_independent = 0.0
    counted = 0
    for measurement in measurements:
        drawn = draws.get(measurement['id'])
        if drawn is None:
            continue
        drawn = np.asarray(drawn, dtype=float)
        if not drawn.size:
            continue
        lo_support, hi_support = _support(measurement)
        lo = min(lo_support, float(drawn.min()))
        hi = max(hi_support, float(drawn.max()))
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            continue
        # The same grid and the same likelihood the per-date index is computed
        # on, so Amodel and the A column are one definition read two ways.
        grid = _comparison_grid(lo, hi)
        observed = comparison_likelihood(
            measurement, grid, outlier_probability.get(measurement['id']))
        total = observed.sum()
        if total <= 0:
            continue
        observed = observed / total
        log_independent += float(np.log((observed ** 2).sum()))
        # Each draw is read at the grid CELL it falls in rather than
        # interpolated between nodes, which is what keeps this the same
        # comparison A_i makes: on a posterior sharp against the five-year step
        # an interpolated reading runs about 2% high, and at n = 1 the two
        # would then disagree about the same date.
        cell = np.clip(np.rint((drawn - grid[0]) / COMPARISON_STEP_YEARS).astype(int),
                       0, len(grid) - 1)
        # In logs from here: the product runs over every date, and at eight
        # dates it is already at 1e-14.
        at_draw = observed[cell]
        # A draw the likelihood gives no mass to contributes nothing to the
        # expectation, which is the honest reading: the model put the date
        # where its own measurement says it cannot be.
        term = np.log(np.maximum(at_draw, _UNSUPPORTED))
        log_joint = term if log_joint is None else log_joint + term
        counted += 1

    if log_joint is None or not counted:
        return None
    # log of the MEAN, not the mean of the logs: the index is a ratio of
    # expectations, and averaging in log space would report the geometric mean
    # of the product instead -- a different and much smaller number.
    peak = float(log_joint.max())
    log_expectation = peak + float(np.log(np.exp(log_joint - peak).mean()))
    return float(100.0 * np.exp((log_expectation - log_independent) / np.sqrt(counted)))


def attach(results: Dict[str, Dict], measurements: List[Dict]) -> Optional[float]:
    """
    Add `agreement_index` to each per-date result in place and return the
    overall index. Works off `probability_distribution` and, for a date under
    an outlier model, `outlier_probability` -- both of which both solvers
    produce, so the two report the same diagnostic.
    """
    indices = []
    for m in measurements:
        entry = results.get(m['id'])
        if entry is None:
            continue
        index = agreement_index(m, entry.get('probability_distribution') or [],
                                outlier_probability=entry.get('outlier_probability'))
        entry['agreement_index'] = index
        indices.append(index)
    return overall_agreement(indices)
