"""
Exact solver for chain-shaped chronological models -- a Sequence of Phases
separated by Boundaries, which is the ordinary shape of an archaeological
model and the one OxCal's documentation recommends.

Why integrate rather than sample
-------------------------------
For this shape the posterior can be integrated exactly, so there is no sampler
to converge and no mode to miss. That matters because calibration curves
wiggle: a single radiocarbon age routinely calibrates to two or more separated
calendar ranges, and resolving which one is right is much of the reason to
build a Bayesian model at all.

The maths
---------
Conditional on the boundaries enclosing it, a group's dated events are
independent and uniform between them, so for boundaries b_1 > b_2 > ... > b_n
(cal BP, oldest first) with group i holding K_i dates between b_i and b_(i+1):

    p(t, b)  ∝  ∏_i (b_i - b_(i+1))^(-K_i) ∏_(dates) L(t)

Every date other than the one being marginalised enters only through its
integrated likelihood over an interval, which a cumulative sum gives for all
intervals at once. What is left is a chain over the boundaries, so the exact
marginals come from forward/backward message passing:

    T_i(b_i, b_(i+1)) = (b_i - b_(i+1))^(-K_i) ∏_(j in group i) F_j(b_(i+1), b_i)
    alpha_(i+1)(b)    = sum over b_i > b of alpha_i(b_i) T_i(b_i, b)
    beta_i(b)         = sum over b_(i+1) < b of T_i(b, b_(i+1)) beta_(i+1)(b_(i+1))

That is O(M^2) per group on an M-point calendar grid: seconds, deterministic,
and exact up to the grid step.

What it does NOT cover
----------------------
The OUTLIER SCALE is approximated. OxCal's Outlier_Model carries one offset
scale shared by every date using it and constrained by the data; this solver
marginalises that scale independently per date over its prior, because a shared
hyperparameter couples the dates and the message passing above needs each to
enter through its own likelihood alone. The approximation is what makes the
exact integration possible; services/gibbs.py samples the shared scale properly
(bayesian_config.solver = "gibbs").

What it costs: on tests/fixtures/oxcal_outlier the per-date marginalisation puts
the reported outlier probabilities 0.0166 from OxCal's where the sampler gets to
0.0016, and on tests/fixtures/oxcal_charcoal the modelled dates land 9.9 years
rms from OxCal against the sampler's 6.3. So it matters where an outlier prior
is large, and hardly at all at the usual few percent.

PHASE WIDTH depends on the interval quadrature being right to second order --
see _interval_integral, which carries the measurements. On models with a date
declared an outright outlier (tests/fixtures/oxcal_charcoal) the span
OVERSHOOTS, 54.9 years against OxCal's 29.2; that is the degenerate mode
described in tests/nenthorn_charcoal_fixture's PHASE WIDTH note, not the phase
model. services/gibbs.py carries the same rule as Model.endpoint_weight, and the
two agree to about 0.6 years on the chain fixtures.

`layout()` returns None for anything that is not a single chain, and the caller
falls back to the sampler. In particular a Sequence that orders two groups with
NO boundary between them is not a chain: its dates share one enclosing boundary
pair while an extra constraint orders them against each other, and that
constraint does not factorise along the chain. Adding an explicit Boundary
between such groups makes it solvable here, and is better practice anyway.
"""
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.signal import fftconvolve
from scipy.special import gammaln

from app.services import intervals
from app.services.calibration import SIGMA_1, SIGMA_2, get_curve, hpd_ranges_from_distribution
from app.services.model_tree import inherited_bounds, plan_model
from app.services.stratigraphy import effective_bounds

# Largest calendar grid the solver will build. Every step of the algorithm is
# O(M^2) in space as well as time, so this caps one intermediate array at
# about 30 MB and the whole solve at a few seconds.
MAX_GRID_POINTS = 2000
# How many groups fit at that point count before the grid coarsens. One M x M
# array of doubles is 32 MB at M = 2000 and one is held per group, so a long
# sequence trades calendar resolution for staying inside the memory budget.
GROUPS_AT_FULL_RESOLUTION = 6
# Finest calendar step worth using. IntCal20/SHCal20 are themselves tabulated
# at 1-20 year intervals, so going below a year buys nothing real.
MIN_GRID_STEP_YEARS = 1.0
# How far beyond the dates' own likelihood support the boundaries are allowed
# to range, as a multiple of that support's width. Boundaries genuinely
# extend past the outermost date -- that is what they are for -- and the tail
# is heavy for a group of few dates, so this is deliberately generous.
BOUNDARY_WINDOW_FACTOR = 6.0
MIN_BOUNDARY_PAD_YEARS = 800.0
# Likelihood below this fraction of a date's peak is treated as absent when
# working out where the grid needs to reach.
SUPPORT_THRESHOLD = 1e-6
# OxCal's "General" outlier model: a Student-t offset on the calendar age,
# scaled by 10^u with u uniform over four decades (Bronk Ramsey 2009).
OUTLIER_DF = 5
OUTLIER_LOG_SCALE_MAX = 4.0
OUTLIER_SCALE_NODES = 24
OUTLIER_KERNEL_HALF_WIDTH_YEARS = 20000.0
# The charcoal/residual model instead assumes a one-sided gap: the dated
# material formed some years BEFORE the context it was found in, never after.
# OxCal's Charcoal_Outlier is
#
#     Outlier_Model("Charcoal", Exp(1,-10,0), U(0,3), "t")
#
# -- an exponential gap truncated at 10 scale lengths, with the SCALE itself
# drawn as 10^u over three decades, as the General model draws its own over
# four. Integrating that scale is not a refinement: at a single fixed scale
# only 12% of the offset mass lands within 20 years against OxCal's 55%, so a
# flagged date drifts onto its neighbours instead of pulling the group towards
# itself. On the Nenthorn charcoal fixture, integrating it moved the modelled
# dates from 16.1 to 9.9 years rms of OxCal's answer.
CHARCOAL_LOG_SCALE_MAX = 3.0
CHARCOAL_KERNEL_SPAN_TAUS = 10.0
OUTLIER_MODELS = ('general', 'charcoal')
DEFAULT_OUTLIER_MODEL = 'general'
# Resolution the reported HPD ranges are computed at, independent of the
# solver's internal grid step -- see _reported_ranges. Five years is OxCal's
# own default and about as fine as a calibrated date is meaningful.
REPORTING_STEP_YEARS = 5.0
# Density below this fraction of the peak is left off the plotted series, and
# this much padding is kept either side of what remains -- see _plottable.
DISPLAY_THRESHOLD = 1e-4
PLOT_MARGIN_YEARS = 50.0


class Chain:
    """A model that is a single line of bounded groups, oldest first."""

    def __init__(self, boundary_ids: List[str], groups: List[List[str]],
                 boundary_names: Dict[str, str]):
        # len(boundary_ids) == len(groups) + 1
        self.boundary_ids = boundary_ids
        self.groups = groups
        self.boundary_names = boundary_names

    def __repr__(self):
        return f"Chain({len(self.groups)} groups, {len(self.boundary_ids)} boundaries)"


def layout(root: Dict, measurements: List[Dict]) -> Optional[Chain]:
    """
    The chain this model reduces to, or None if it does not reduce to one.

    Semantic rather than syntactic: it works from plan_model's output, so
    nesting that adds no ordering of its own -- a Sequence wrapping a single
    Phase, say -- is still recognised as a chain.
    """
    return layout_with_reason(root, measurements)[0]


def layout_with_reason(
    root: Dict, measurements: List[Dict]
) -> Tuple[Optional[Chain], Optional[str]]:
    """
    layout(), plus a plain-English reason when the model is not a chain, so
    the API can say WHY it fell back to sampling.

    Produced here at the point of decision rather than by a separate diagnostic
    that inspects the tree afterwards, which would drift from these rules and
    then confidently explain a fallback that happened for another reason.

    Phrase each reason as what the model does and, where practical, what to
    change: these strings are shown to an archaeologist, not a developer.
    """
    if root is None:
        return None, 'the analysis has no model structure'
    plan = plan_model(root, measurements)

    # Both of these couple the dates to EACH OTHER rather than only through
    # their Boundaries, which is the one thing the message passing below
    # cannot express -- the same obstruction the module docstring records for
    # the outlier scale, and with the same resolution: say so, and let the
    # caller take the sampler or the closed form.
    if plan.deposition_groups:
        return None, (
            'this model contains a deposition sequence, whose ages are coupled '
            'to each other by the deposition rate rather than only through '
            'their Boundaries, so it cannot be integrated exactly'
        )
    if plan.wiggle_groups:
        return None, (
            'this model contains a wiggle match, whose dates are one calendar '
            'age with the measured gaps added to it rather than separate '
            'parameters, so it is not a chain of Boundaries. It is solved in '
            'closed form instead (services/wiggle_match.py)'
        )

    if not plan.span_groups:
        return None, (
            'no Boundary encloses any date. Add a Boundary at the start and '
            'end of the sequence so each phase has a transition either side '
            'of it'
        )

    # Link the groups into one path: each group's younger boundary must be the
    # next group's older boundary, and no boundary may be reused elsewhere.
    branched = (
        'the model branches: it has parallel groups rather than one line of '
        'phases, so the phases do not share a single chain of Boundaries'
    )
    by_older = {}
    for group in plan.span_groups:
        if group.older_boundary_id in by_older:
            return None, branched            # a boundary starting two groups
        by_older[group.older_boundary_id] = group
    younger = {g.younger_boundary_id for g in plan.span_groups}
    heads = [g for g in plan.span_groups if g.older_boundary_id not in younger]
    if len(heads) != 1:
        return None, branched

    ordered_groups, seen = [], set()
    current = heads[0]
    while current is not None:
        if id(current) in seen:
            return None, branched
        seen.add(id(current))
        ordered_groups.append(current)
        current = by_older.get(current.younger_boundary_id)
    if len(ordered_groups) != len(plan.span_groups):
        return None, branched                # more than one disjoint chain

    boundary_ids = ([g.older_boundary_id for g in ordered_groups]
                    + [ordered_groups[-1].younger_boundary_id])
    if len(set(boundary_ids)) != len(boundary_ids):
        return None, branched
    if set(boundary_ids) != set(plan.boundary_names):
        return None, (
            'a Boundary sits outside the sequence of phases, so it is not '
            'part of a single chain of transitions'
        )

    # Every date must sit in exactly one group.
    placed: Dict[str, int] = {}
    for index, group in enumerate(ordered_groups):
        for date_id in group.date_ids:
            if date_id in placed:
                return None, branched
            placed[date_id] = index
    unenclosed = [m for m in measurements if m['id'] not in placed]
    if unenclosed:
        names = ', '.join(
            sorted(m.get('sample_name') or m['id'] for m in unenclosed[:4])
        )
        if len(unenclosed) > 4:
            names += f' and {len(unenclosed) - 4} more'
        return None, (
            f'{len(unenclosed)} date(s) sit outside every Boundary '
            f'({names}). Add a Boundary either side of the phases holding '
            'them so they fall inside the chain'
        )

    # The chain must already imply every ordering constraint the tree asks
    # for. Rank boundary i at 2i and group i at 2i+1, oldest first; a
    # constraint is implied when everything on its older side outranks
    # everything on its younger side. Anything else -- above all two groups
    # ordered against each other INSIDE one boundary pair -- is structure this
    # solver cannot express.
    rank_of_boundary = {bid: 2 * i for i, bid in enumerate(boundary_ids)}

    def rank(kind: str, tid: str) -> Optional[int]:
        if kind == 'boundary':
            return rank_of_boundary.get(tid)
        index = placed.get(tid)
        return None if index is None else 2 * index + 1

    for older_terms, younger_terms in plan.ordering:
        older_ranks = [rank(k, t) for k, t in older_terms]
        younger_ranks = [rank(k, t) for k, t in younger_terms]
        if any(r is None for r in older_ranks + younger_ranks):
            return None, branched
        if max(older_ranks) >= min(younger_ranks):
            return None, (
                'two groups are ordered against each other with no Boundary '
                'between them. Add a Boundary at that transition to make the '
                'model exactly solvable'
            )

    return Chain(boundary_ids, [list(g.date_ids) for g in ordered_groups],
                 dict(plan.boundary_names)), None


# --------------------------------------------------------------------------
# Solving
# --------------------------------------------------------------------------

def _date_likelihood(grid: np.ndarray, measurement: Dict,
                     bounds: Tuple[Optional[float], Optional[float]]) -> np.ndarray:
    """
    One date's calibration likelihood on `grid`, with its reservoir offset
    applied and its TPQ/TAQ limiters imposed as a hard truncation.
    """
    curve = get_curve(measurement.get('curve_id') or 'intcal20')
    delta_r = measurement.get('delta_r') or 0.0
    delta_r_error = measurement.get('delta_r_error') or 0.0

    c14, curve_error = curve.interpolate(grid)
    sigma2 = curve_error ** 2 + measurement['error_1sigma'] ** 2 + delta_r_error ** 2
    lik = np.exp(-0.5 * (c14 - (measurement['age_bp'] - delta_r)) ** 2 / sigma2) / np.sqrt(sigma2)
    # interpolate() CLAMPS beyond the curve's ends rather than extrapolating,
    # which would give a grid point past the curve the finite likelihood of
    # its last point. The curve says nothing there, so neither does the date.
    lik = np.where((grid >= curve.cal_ages[0]) & (grid <= curve.cal_ages[-1]), lik, 0.0)

    taq, tpq = bounds
    if taq is not None:
        lik = np.where(grid >= taq, lik, 0.0)
    if tpq is not None:
        lik = np.where(grid <= tpq, lik, 0.0)
    return lik


def _outlier_kernel(step: float, half_width: float,
                    model: str = DEFAULT_OUTLIER_MODEL) -> np.ndarray:
    """
    The offset a flagged date is allowed, as a convolution kernel over
    CALENDAR years. Index order matches the grid, so a lag of +1 cell means
    the material is one step OLDER than the context.

    Two models, because the two ways a date goes wrong are not the same shape:

    "general" -- OxCal's `Outlier_Model("General", T(5), U(0,4), "t")` (Bronk
        Ramsey 2009): a Student-t offset scaled by 10^u over four decades.
        Symmetric and very heavy-tailed, so a flagged date says almost nothing
        about where it sits. The model for a date wrong in an unknown way.

    "charcoal" -- OxCal's `Outlier_Model("Charcoal", Exp(1,-10,0), U(0,3),
        "t")`: a ONE-SIDED exponential gap, the material having formed some
        years BEFORE the context and never after, its scale drawn over three
        decades. This is old wood and reworked material, and the known
        direction is worth a lot: on an injected residual date it recovered the
        Start boundary to AD 1292 against the general model's 1201 (clean value
        1339), and put the outlier probability at 90% rather than 66%.

    Both are summed over their scale, which APPROXIMATES OxCal: OxCal shares
    one scale across every date using the model and lets the data constrain it,
    where summing marginalises it independently per date. services/gibbs.py
    samples the shared scale properly -- see its Model._build_outlier_tables.
    """
    total = None
    for log_scale in scale_nodes(model):
        component = _outlier_kernel_at_scale(step, half_width, model, log_scale)
        total = component if total is None else total + component
    return total / total.sum()


def scale_nodes(model: str = DEFAULT_OUTLIER_MODEL) -> np.ndarray:
    """
    The offset scales an outlier model is allowed, as log10(years).

    The second argument of OxCal's Outlier_Model: U(0,4) for General, U(0,3)
    for Charcoal. Uniform on the LOG scale, so every decade is equally likely a
    priori, which puts a third of the charcoal prior at ten years or less.
    """
    top = CHARCOAL_LOG_SCALE_MAX if model == 'charcoal' else OUTLIER_LOG_SCALE_MAX
    return np.linspace(0.0, top, OUTLIER_SCALE_NODES)


def _outlier_kernel_at_scale(step: float, half_width: float, model: str,
                             log_scale: float) -> np.ndarray:
    """
    One scale's kernel, normalised, on a lag axis that does not depend on the
    scale -- so kernels for different scales are directly comparable and can be
    summed or selected between.
    """
    tau = 10.0 ** log_scale
    if model == 'charcoal':
        # One-sided, and the side matters. The MATERIAL is older than the
        # CONTEXT, so the context's calendar age is YOUNGER -- lower cal BP, a
        # lower grid index -- and the kernel carries its mass at negative lag.
        # At positive lag it would say the context predates the wood found in
        # it, making a residual date look MORE consistent rather than less.
        #
        # The lag axis runs to the WIDEST scale's reach so every scale shares
        # it, but each is truncated at its own 10 scale lengths (OxCal's
        # Exp(1,-10,0)).
        widest = CHARCOAL_KERNEL_SPAN_TAUS * (10.0 ** CHARCOAL_LOG_SCALE_MAX)
        lags = np.arange(0.0, widest + step, step)
        density = np.where(lags <= CHARCOAL_KERNEL_SPAN_TAUS * tau,
                           np.exp(-lags / tau), 0.0)
        kernel = np.concatenate([density[::-1], np.zeros(len(lags) - 1)])
    else:
        lags = np.arange(-half_width, half_width + step, step)
        x = lags / tau
        kernel = (1 + x * x / OUTLIER_DF) ** (-(OUTLIER_DF + 1) / 2.0)
    total = kernel.sum()
    return kernel / total if total > 0 else kernel


def convolve_on_grid(values: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """
    `values` smeared by `kernel`, returned on the same grid.

    The kernel is trimmed before convolving to the lags that can reach a grid
    point from a grid point. That is exact rather than an economy -- a longer
    lag contributes to no on-grid output -- and it matters because these
    kernels run to 10,000-40,000 years while the grid is a few thousand.
    Trimming happens AFTER normalisation, so mass the kernel throws off the
    grid stays thrown off; see _with_outlier_component for why that is the
    point.
    """
    centre = (len(kernel) - 1) // 2
    reach = len(values) - 1
    trimmed = kernel[max(0, centre - reach):centre + reach + 1]
    offset = min(centre, reach)
    full = fftconvolve(values, trimmed)
    # FFT convolution leaves tiny negative values where the true answer is zero.
    return np.maximum(full[offset:offset + len(values)], 0.0)


def _with_outlier_component(
    grid: np.ndarray, inlier: np.ndarray, prior: float,
    model: str = DEFAULT_OUTLIER_MODEL,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    (mixture, outlier_component) for one date, both normalised on `grid`.

    Integrating out a Bernoulli outlier indicator is exact and costs the
    solver nothing, because the indicator touches only this date's own
    likelihood:

        L_mix(t) = (1 - p) * L_inlier(t)  +  p * L_outlier(t)

    Every date enters the chain through its likelihood alone, so the message
    passing is untouched -- the outlier model is a property of a date, not of
    the inference.
    """
    step = float(grid[1] - grid[0])
    kernel = _outlier_kernel(step, max(OUTLIER_KERNEL_HALF_WIDTH_YEARS,
                                       float(grid[-1] - grid[0])), model)
    broad = convolve_on_grid(inlier, kernel)
    # NOT renormalised over the grid. The convolution of two normalised
    # densities is already proper on the real line, and most of its mass lies
    # off this grid -- which is exactly the statement that a flagged date's
    # calendar age could be anywhere. Rescaling what remains to one would
    # inflate the component by the reciprocal of the retained mass and make the
    # outlier probability depend on the grid width: it put the fifteen sound
    # Shag River dates at 9-14% against OxCal's 0.6-1.5%. The mixture is
    # therefore sub-unit here, the missing mass being the outlier possibility
    # the window does not cover.
    return (1.0 - prior) * inlier + prior * broad, broad


def _effective_date_bounds(measurement: Dict, element_bounds: Dict) -> Tuple:
    el_taq, el_tpq = element_bounds.get(measurement.get('element_id'), (None, None))
    return effective_bounds(measurement, {'taq_age_bp': el_taq, 'tpq_age_bp': el_tpq})


def date_tables(
    grid: np.ndarray, measurements: List[Dict], element_bounds: Dict,
) -> Tuple[Dict[str, np.ndarray], Dict[str, np.ndarray],
           Dict[str, Tuple[np.ndarray, np.ndarray, float]]]:
    """
    Every date's likelihood on `grid`, its cumulative integral, and the parts
    needed to report an outlier probability: ({id: likelihood},
    {id: cumulative}, {id: (inlier, outlier component, prior)}).

    Each likelihood is normalised to integrate to one over the grid, so every
    interval integral lands in [0, 1] and long products of them cannot
    underflow. A date carrying an outlier prior has its Bernoulli indicator
    integrated out here (_with_outlier_component), which is exact and leaves
    the rest of the inference untouched -- the outlier model is a property of a
    date, not of how the model is solved.

    Shared with services/gibbs.py: both solvers need the identical table for
    their answers to be comparable, and a second copy of the normalisation,
    truncation and outlier mixture would drift -- which would surface as the
    two disagreeing for a reason unrelated to either one's inference.
    """
    dt = float(grid[1] - grid[0])
    likelihood: Dict[str, np.ndarray] = {}
    cumulative: Dict[str, np.ndarray] = {}
    outlier_parts: Dict[str, Tuple[np.ndarray, np.ndarray, float]] = {}

    for m in measurements:
        lik = _date_likelihood(grid, m, _effective_date_bounds(m, element_bounds))
        total = lik.sum() * dt
        if total <= 0 or not np.isfinite(total):
            raise ValueError(
                f"sample '{m.get('sample_name', m['id'])}' has no probability inside "
                'its allowed calendar range'
            )
        lik = lik / total
        prior = m.get('outlier_prior') or 0.0
        if prior > 0:
            model_name = m.get('outlier_model') or DEFAULT_OUTLIER_MODEL
            if model_name not in OUTLIER_MODELS:
                raise ValueError(
                    f"sample '{m.get('sample_name', m['id'])}' has unknown outlier "
                    f"model '{model_name}'; expected one of {', '.join(OUTLIER_MODELS)}"
                )
            mixture, broad = _with_outlier_component(grid, lik, prior, model_name)
            outlier_parts[m['id']] = (lik, broad, float(prior))
            lik = mixture
        likelihood[m['id']] = lik
        cumulative[m['id']] = np.cumsum(lik) * dt

    return likelihood, cumulative, outlier_parts


def _build_grid(measurements: List[Dict], element_bounds: Dict,
                num_groups: int = 1) -> np.ndarray:
    """
    A calendar grid wide enough for the dates and the boundaries either side of
    them, at the finest step the memory budget allows.

    One M x M array of doubles is 32 MB at M = 2000 and a transfer matrix is
    held per group, so the point count scales down as the model grows: a long
    sequence gets a coarser calendar step rather than an out-of-memory kill.
    """
    curve_lo, curve_hi = np.inf, -np.inf
    lo, hi = np.inf, -np.inf
    for m in measurements:
        curve = get_curve(m.get('curve_id') or 'intcal20')
        curve_lo = min(curve_lo, float(curve.cal_ages[0]))
        curve_hi = max(curve_hi, float(curve.cal_ages[-1]))
        cal, prob = curve.calibrate(m['age_bp'], m['error_1sigma'],
                                    m.get('delta_r'), m.get('delta_r_error'))
        significant = np.where(prob >= prob.max() * SUPPORT_THRESHOLD)[0]
        if len(significant):
            lo = min(lo, float(cal[significant[0]]))
            hi = max(hi, float(cal[significant[-1]]))
        taq, tpq = _effective_date_bounds(m, element_bounds)
        if taq is not None:
            lo = max(lo, taq)
        if tpq is not None:
            hi = min(hi, tpq)

    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        raise ValueError('the dates have no overlapping calendar range to solve over')

    pad = max(MIN_BOUNDARY_PAD_YEARS, BOUNDARY_WINDOW_FACTOR * (hi - lo))
    lo = max(curve_lo, lo - pad)
    hi = min(curve_hi, hi + pad)

    # Up to GROUPS_AT_FULL_RESOLUTION groups the full point count fits.
    budget = MAX_GRID_POINTS / max(1.0, np.sqrt(num_groups / GROUPS_AT_FULL_RESOLUTION))
    step = _reporting_aligned_step(max(MIN_GRID_STEP_YEARS, (hi - lo) / budget))
    # An explicit count rather than np.arange(lo, hi + step, step), whose
    # length depends on floating-point rounding at the end point.
    count = int(np.ceil((hi - lo) / step - 1e-9)) + 1
    return lo + step * np.arange(count)


def _reporting_aligned_step(step: float) -> float:
    """
    The smallest calendar step >= `step` that tiles REPORTING_STEP_YEARS
    exactly -- a divisor of it below, a multiple of it above.

    The raw step is (range / point budget), 1.015 years on a typical model,
    and a 5-year reporting bin then holds four grid cells or five depending
    on where it falls. That is not cosmetic: the mass per bin swung between
    0.81 and 1.04 of its true value on a smooth posterior, and an HPD region
    built by ranking bins was picking them on that sawtooth. _reported_ranges
    now integrates the density exactly, which removes the error whatever the
    step; aligning the step as well keeps the internal cells and the reported
    bins nested, so a bin is always a whole number of cells.
    """
    reporting = REPORTING_STEP_YEARS
    if step >= reporting:
        return float(np.ceil(step / reporting - 1e-9) * reporting)
    return float(reporting / np.floor(reporting / step + 1e-9))


def _rescaled(log_weights: np.ndarray) -> np.ndarray:
    """Shift log weights by their own finite maximum, ready to exponentiate.

    Per-array rather than against a shared constant: dividing a joint weight by
    one date's interval integral can raise it by hundreds of nats, which
    overflows a shared shift.
    """
    finite = np.isfinite(log_weights)
    if not np.any(finite):
        return np.where(finite, log_weights, -np.inf)
    return np.where(finite, log_weights - log_weights[finite].max(), -np.inf)


def _interval_integral(cumulative: np.ndarray, density: np.ndarray,
                       step: float) -> np.ndarray:
    """
    F[y, o] = integral of one date's likelihood over [grid[y], grid[o]],
    TRAPEZOIDALLY -- half weight on each endpoint cell.

    The half-weights are the difference between right and wrong here, because
    of the (span)^(-K) factor this gets multiplied by. A cumulative difference
    alone sums cells y THROUGH o inclusive, so an interval spanning k*step
    receives (k+1) cells of mass while the span prior uses k*step -- too large
    by a factor of two PER DATE at the smallest span, 2^K over a group, landing
    squarely on the near-coincident configurations the prior already favours.

    Against a continuum reference with no quadrature at all (three Gaussian
    likelihoods, so the integrals are exact error functions; truth 181.5 years):

        grid step      cum[o] - cum[y-1]      trapezoidal
           10.0                   126.4            186.3
            5.0                   141.5            183.8
            2.5                   155.2            182.6
            1.0                   167.8            181.9

    The plain difference is biased low by 30% at a ten-year step and converges
    only as the step vanishes; the trapezoidal form is within 3% at ten years
    and makes the answer grid-independent.

    `density` and `step` are required rather than optional, because the
    uncorrected form is wrong wherever it is used.
    """
    padded = np.concatenate([[0.0], cumulative])
    half = 0.5 * step * density
    F = (padded[None, 1:] - padded[:-1, None]) - half[None, :] - half[:, None]
    return np.clip(F, 0.0, None)


def _log_interval_integral(cumulative: np.ndarray, density: np.ndarray,
                           step: float) -> np.ndarray:
    F = _interval_integral(cumulative, density, step)
    with np.errstate(divide='ignore'):
        return np.where(F > 0, np.log(np.where(F > 0, F, 1.0)), -np.inf)


def log_interior_placements(span_cells: np.ndarray, num_interior: int) -> np.ndarray:
    """
    log of the number of ways `num_interior` ordered boundaries fit strictly
    inside a span of `span_cells` grid cells, i.e. log C(span_cells - 1,
    num_interior). -inf where the span is too narrow to hold them at all.

    The exact discrete version of the volume the UniformSpanPrior correction
    divides out, and a COUNT rather than a power -- see _span_prior_correction
    for why a power is wrong on a grid.

    Shared with services/gibbs.py so the two solvers cannot drift apart. Defined
    only up to a constant factor, like the correction itself: the
    1/num_interior! of the continuum expression is absorbed into the posterior's
    normalisation.
    """
    j = np.asarray(span_cells, dtype=float)
    k = float(num_interior)
    out = np.full(np.shape(j), -np.inf)
    room = j >= k + 1.0
    out[room] = gammaln(j[room]) - gammaln(k + 1.0) - gammaln(j[room] - k)
    return out


def boundary_limits(element_bounds: Dict, boundary_id: str) -> Tuple[float, float]:
    """
    (oldest, youngest) calendar age a boundary may take, in cal BP, as infinities
    where nothing constrains it.

    The llim/ulim of Bronk Ramsey 2008 A.30-A.31. Only REAL constraints count --
    a TPQ or TAQ on the Boundary or on an element enclosing it. The solver's
    grid is not used even though it bounds the answer numerically: it is padded
    by six times the dates' own width for the boundaries' benefit, so treating
    its edges as model statements makes the factor an artefact of that padding.
    Measured on a single unconstrained phase, the effect halved with each
    doubling of the window (+1.20 years at 600, +0.13 at 4,800), heading for
    nothing.

    An unconstrained boundary is unbounded, so g is infinite, 1/g is constant in
    the span, and it drops out of the posterior -- which is why an unconstrained
    model gets no limits factor rather than a small arbitrary one.
    """
    taq, tpq = element_bounds.get(boundary_id, (None, None))
    oldest = float(tpq) if tpq is not None else np.inf
    youngest = float(taq) if taq is not None else -np.inf
    return oldest, youngest


def log_limits_factor_of_span(spans: np.ndarray,
                              oldest_limits: Tuple[float, float],
                              youngest_limits: Tuple[float, float],
                              ) -> Optional[np.ndarray]:
    """
    The limits factor as a function of the span alone, for `spans` of any shape;
    see _log_limits_factor for the derivation and the four terms.

    Split out so services/gibbs.py can ask for a 1-D row indexed by span without
    allocating the M x M matrix the exact solver needs, while both share one
    definition of the terms and their sign.
    """
    hi_old, lo_old = oldest_limits
    hi_yng, lo_yng = youngest_limits
    spans = np.asarray(spans, dtype=float)

    terms = []
    if np.isfinite(hi_old) and np.isfinite(lo_yng):
        terms.append((hi_old - lo_yng) - spans)
    if np.isfinite(lo_old) and np.isfinite(hi_yng):
        terms.append(spans - (lo_old - hi_yng))
    if not terms:
        return None                                   # nothing depends on the span
    for constant in (hi_old - lo_old, hi_yng - lo_yng):
        if np.isfinite(constant):
            terms.append(np.full_like(spans, constant))

    g = np.stack(terms).min(axis=0)
    # g <= 0 means the derived limits contradict each other at this span, which
    # happens when the dates' supports do not overlap and containment already
    # forbids a span that short. The factor counts configurations to normalise a
    # prior, so a non-positive count says something about the LIMITS, not the
    # model, and must not veto the state: weight 1 there, not zero. Returning
    # -inf instead makes the sampler's starting state infeasible on a model
    # with a stratigraphic inversion.
    out = np.zeros(g.shape)
    positive = g > 0
    out[positive] = -np.log(g[positive])
    return out


def containment_limits(grid: np.ndarray, inliers: Dict[str, np.ndarray],
                       date_ids: Sequence[str]) -> Tuple[float, float]:
    """
    How far the enclosing pair of boundaries is pinned by the dates between them:
    (youngest the OLDER boundary may be, oldest the YOUNGER boundary may be), cal BP.

    The llim/ulim of Bronk Ramsey 2008 A.31, derived as OxCal derives them: each
    date is bounded by its own likelihood's extent, and each boundary then by
    t_start < t_i < t_end. So the older boundary cannot be younger than the
    youngest end of the OLDEST date's support, and symmetrically for the younger.

    Reproducing OxCal's numbers needs a threshold, and SUPPORT_THRESHOLD gets the
    DIFFERENCES between dates exactly right with a fixed offset per side. The
    answer barely depends on it: what reaches the posterior is the gap between
    the two bounds, 123 years against OxCal's 129, and using OxCal's own value
    moves the modelled span by 0.11 years.

    The INLIER likelihood, not the outlier mixture: a flagged date's broad
    component runs for thousands of years, which says where its event might be
    if the sample is residual, not what the parameter is confined to.
    """
    youngest_of_oldest, oldest_of_youngest = -np.inf, np.inf
    for date_id in date_ids:
        density = inliers.get(date_id)
        if density is None:
            continue
        support = np.where(density >= density.max() * SUPPORT_THRESHOLD)[0]
        if not len(support):
            continue
        youngest_of_oldest = max(youngest_of_oldest, float(grid[support[0]]))
        oldest_of_youngest = min(oldest_of_youngest, float(grid[support[-1]]))
    return youngest_of_oldest, oldest_of_youngest


def pair_limits(element_bounds: Dict, oldest_id: str, youngest_id: str,
                grid: np.ndarray, inliers: Dict[str, np.ndarray],
                date_ids: Sequence[str],
                ) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """
    The four limits A.31 needs for one pair of boundaries: each one's own explicit
    TPQ/TAQ where it has them, tightened by what the dates between them imply.
    """
    young_bound, old_bound = containment_limits(grid, inliers, date_ids)
    oldest = boundary_limits(element_bounds, oldest_id)
    youngest = boundary_limits(element_bounds, youngest_id)
    return ((oldest[0], max(oldest[1], young_bound)),
            (min(youngest[0], old_bound), youngest[1]))


def _log_limits_factor(grid: np.ndarray, oldest_limits: Tuple[float, float],
                       youngest_limits: Tuple[float, float]) -> Optional[np.ndarray]:
    """
    log of the factor 1/g from Bronk Ramsey 2008 A.31 (= 2009a eq 29), as a matrix
    indexed [youngest boundary, oldest boundary]. None when g does not depend on
    the span, in which case the factor is a constant and contributes nothing.

    The paper is written in AD, where the phase runs from t_a up to t_m with
    llim_a <= t_a <= ulim_a and llim_m <= t_m <= ulim_m. In cal BP everything
    reverses, so writing (hi, lo) for the oldest and youngest age each boundary may
    take, A.31 becomes

        g = min( (hi_old - lo_yng) - span,        <- needs a TPQ on the oldest
                 span - (lo_old - hi_yng),        <-   and a TAQ on the youngest
                 hi_old - lo_old,                 <- the oldest's own range
                 hi_yng - lo_yng )                <- the youngest's own range

    WHICH TERM IS SMALLEST is the character of this factor and why its sign is
    easy to get backwards: the first falls with the span, so 1/g rises and
    favours a LONGER one, while the second rises with it and favours a SHORTER.
    The last two are constants that only cap it.

    Only the first two depend on the span, and each needs both of its limits to be
    real; with either infinite that term cannot be the minimum. If neither
    survives, the factor is constant and None is returned.
    """
    span = grid[None, :] - grid[:, None]              # [youngest, oldest]
    return log_limits_factor_of_span(span, oldest_limits, youngest_limits)


def _span_prior_correction(grid: np.ndarray, num_boundaries: int, enabled: bool,
                           oldest_limits: Optional[Tuple[float, float]] = None,
                           youngest_limits: Optional[Tuple[float, float]] = None,
                           ) -> Optional[np.ndarray]:
    """
    OxCal's UniformSpanPrior factor over the outermost pair, as a matrix
    indexed [youngest boundary, oldest boundary], or None when it would do
    nothing and the cheap chain can be used.

        1 / C(span in cells - 1, n - 2)

    Placing n boundaries in order across a span leaves the n-2 interior ones
    uniform within it, so the induced prior carries a factor for that freedom
    and favours long sequences purely for having more room to arrange them in.
    Dividing it out makes the span prior independent of how finely the sequence
    is subdivided -- Nicholls and Jones (2001), and OxCal's default.

    WHY A BINOMIAL AND NOT (b_first - b_last)^-(n-2). The continuum factor is
    span^(n-2), but the model this solver integrates is discrete, and on a grid
    the number of ways to place k ordered interior boundaries strictly inside a
    span of j cells is C(j-1, k), not (j*step)^k. The two agree only as j grows:
    at k = 1 they are 1/(j-1) against 1/j, a first-order error, and the span
    prior concentrates mass at SMALL j where it is largest.

    Measured with the likelihoods switched off, so the reported posterior IS the
    prior and the answer is known in closed form. Total-variation distance of the
    oldest boundary's marginal from that, at a step of 1.33 years, power ->
    binomial: 0.39% -> 0.00% at n = 3, 0.93% -> 0.00% at n = 4, 1.56% -> 0.00%
    at n = 5. The error was first-order in the step and so shrank under
    refinement, which is why it looked like a modelling difference from OxCal.

    THE LIMITS FACTOR. OxCal's UniformSpanPrior also carries 1/g of Bronk Ramsey
    2008 A.31 (2009a eq 29-30), and it is not negligible: running OxCal with the
    option off on eight overlapping dates in one phase gives a span of 31.83
    against 26.26 with it on, so the term is worth 18% of the span, and it
    NARROWS. Its ON/OFF ratio is about the same on every model tried -- 0.825
    here, 0.847 on the `ordered` fixture, 0.792 on the charcoal one.

        g = min( (ulim_m - llim_a) - (t_m - t_a),
                 (t_m - t_a) - (llim_m - ulim_a),
                 ulim_a - llim_a,
                 ulim_m - llim_m )

    Which of the four is smallest decides which WAY it biases the span, which is
    the whole subtlety. Read llim/ulim as the wide computational window and the
    first term wins, giving 1/(window - span), which WIDENS -- and cannot be
    what OxCal does. With per-boundary limits tight enough for the second term
    to win, 1/g goes as 1/(span + c), the right sign and roughly the right size.
    See _log_limits_factor for the expression and pair_limits for where the
    limits come from; two things that do NOT work are using the solver's grid
    edges in place of a missing constraint (which makes the factor an artefact
    of _build_grid's padding) and treating an unconstrained boundary as
    unbounded (the DATES bound the boundaries enclosing them, which is how OxCal
    derives these and what it prints).

    Against OxCal, rms of the modelled dates and then the span, without the
    limits factor -> with it:

        oxcal/unordered         0.20 -> 0.13     34.9 -> 30.4
        oxcal/ordered           2.42 -> 2.05     36.7 -> 31.5
        oxcal/shared_boundary   0.76 -> 0.30     36.7 -> 31.5
        outlier/Shag            0.18 -> 0.16     20.7 -> 18.5
        charcoal/Nenthorn      10.12 -> 8.37     51.4 -> 40.3

    WHAT IS STILL OPEN. On one phase of eight overlapping dates the span is 29.2
    against OxCal's 26.3, and 54.8 against 48.9 on four -- a constant proportion
    rather than a constant offset, localised to the two BOUNDARIES, whose
    marginals carry about 45% more mass far from the dates while the dates
    themselves agree to 0.1-0.2 years in sd.

    Ruled out by measurement: the limits (feeding OxCal's own llim/ulim in moves
    the answer 0.11 years), the grid (refining sixfold moves the span 0.38), the
    interior-boundary factor (exact against a closed form), the likelihoods
    (within 0.5% of OxCal's in width), and any extra exponent. The difference
    also CHANGES SIGN with the number of dates -- ours/OxCal 54.76/48.90 at four,
    29.12/26.26 at eight, 22.39/23.86 at sixteen -- which rules out both a
    constant prior difference and the depleted-far-tail hypothesis, since a
    depleted tail predicts wider still at sixteen. OxCal is not under-converged
    either, reporting C of 97.6-99.8.

    Left open deliberately: every remaining lead is a guess about OxCal's
    internals. In practice the dates agree to a year or two on every fixture and
    a phase's span differs by up to about a tenth.

    At n = 2 the exponent is zero, so a single Phase between two Boundaries --
    the app's default shape -- is untouched by the interior-boundary term, and
    the O(M^2) chain is kept unless the limits factor applies.

    It is not optional: on the fixture's two-group model the group separation is
    14.7 years against OxCal's 13.2 with it and 26.7 without, and the dates 0.76
    years rms against 6.67. It couples the two ends of the chain, so the solve
    stops factorising and goes from O(M^2) to O(M^3) per group -- worth paying.

    About 1.5 years of extra separation remains, and is not this factor's doing:
    it survives refining the grid fourfold, appears whenever two groups are
    ORDERED whether the middle Boundary is written or implied, and is absent
    when nothing orders them. See tests/nenthorn_charcoal_fixture's WHAT IS LEFT
    note.
    """
    if not enabled:
        return None
    exponent = num_boundaries - 2
    # The LOG of the factor, so both parts enter with one sign and -inf means
    # this pair of boundaries is impossible.
    total = np.zeros((len(grid), len(grid)))       # [youngest, oldest]
    has_limits = False

    if exponent > 0:
        index = np.arange(len(grid))
        cells = index[None, :] - index[:, None]
        # 1 / (ways the interior boundaries fit) -- A.29, 2001 eq 6.
        log_count = log_interior_placements(cells, exponent)
        total = np.where(np.isfinite(log_count), total - log_count, -np.inf)

    if oldest_limits is not None and youngest_limits is not None:
        # 1 / g -- A.31, 2001 eq 7. Applies at ANY number of boundaries, which
        # is why this can return something at two; None when nothing constrains
        # them, the usual case.
        limits = _log_limits_factor(grid, oldest_limits, youngest_limits)
        if limits is not None:
            total = np.where(np.isfinite(limits) & np.isfinite(total),
                             total + limits, -np.inf)
            has_limits = True

    if exponent <= 0 and not has_limits:
        return None                                # nothing to apply
    if not np.any(np.isfinite(total)):
        return None
    return total


def _sample_indices(rng, weights: np.ndarray, count: int) -> np.ndarray:
    """`count` inverse-CDF draws of an index into a 1-D weight array."""
    total = float(weights.sum())
    if not np.isfinite(total) or total <= 0:
        raise ValueError('a posterior came out empty; the model may be contradictory')
    cdf = np.cumsum(weights)
    return np.clip(np.searchsorted(cdf, rng.random(count) * total),
                   0, len(weights) - 1)


def _sample_columns(rng, weights: np.ndarray) -> np.ndarray:
    """
    One column index per ROW of a (rows x M) weight matrix -- a batch of
    independent inverse-CDF draws, each from its own row.
    """
    cumulative = np.cumsum(weights, axis=1)
    totals = cumulative[:, -1]
    if np.any(totals <= 0) or not np.all(np.isfinite(totals)):
        raise ValueError('a conditional came out empty while drawing from the '
                         'joint posterior; the model may be contradictory')
    draw = rng.random(len(totals)) * totals
    # One searchsorted over every row at once: each row's cumulative is lifted
    # by its row number times a step larger than any total, so the flattened
    # array is monotone and a row's draw, lifted the same way, lands in it.
    rows, width = cumulative.shape
    lift = (np.arange(rows) * (2.0 * float(totals.max()) + 1.0))
    flat = (cumulative + lift[:, None]).ravel()
    found = np.searchsorted(flat, draw + lift, side='left') - np.arange(rows) * width
    return np.clip(found, 0, width - 1)


# Draws taken at a time when sampling the joint posterior. One block holds a
# (block x M) float matrix, so this is a memory knob rather than a statistical
# one: 512 x 2000 doubles is 8 MB.
JOINT_DRAW_BLOCK = 512


def _messages(chain: Chain, allowed: Dict, transfer):
    """
    Forward/backward over the boundaries, the O(M^2) case.

    Returns (pair_weight, boundary_density, draw_boundaries): pair_weight(k) is
    everything outside group k as a matrix indexed [younger boundary, older
    boundary], boundary_density(i) is boundary i's unnormalised marginal, and
    draw_boundaries(rng, count) returns joint draws -- see below.
    """
    alpha = [np.where(allowed[chain.boundary_ids[0]], 1.0, 0.0)]
    for i in range(len(chain.groups)):
        alpha.append((alpha[i][None, :] * transfer(i)).sum(axis=1))
    beta = [None] * len(chain.boundary_ids)
    beta[-1] = np.where(allowed[chain.boundary_ids[-1]], 1.0, 0.0)
    for i in range(len(chain.groups) - 1, -1, -1):
        beta[i] = (transfer(i) * beta[i + 1][:, None]).sum(axis=0)

    def draw_boundaries(rng, count: int) -> np.ndarray:
        """
        `count` INDEPENDENT draws of every boundary at once, as grid indices,
        shaped (count, number of boundaries) with the oldest boundary first.

        Forward-filter/backward-sample: the messages above are the forward
        filter and the chain is Markov in the boundaries, so drawing the
        youngest from its own marginal and each earlier one from
        alpha_i(b_i) T_i(b_i, b_(i+1)) given the one after it is an exact draw
        from the joint. No MCMC and no burn-in.

        This is what services/intervals.py needs and the marginals cannot
        supply: the two boundaries either side of a phase are strongly
        correlated, so a duration built by subtracting their marginals would be
        far too wide.
        """
        num_groups = len(chain.groups)
        indices = np.empty((count, num_groups + 1), dtype=np.int64)
        indices[:, num_groups] = _sample_indices(
            rng, alpha[num_groups] * beta[num_groups], count)
        for i in range(num_groups - 1, -1, -1):
            matrix = transfer(i)                       # [younger, older]
            for start in range(0, count, JOINT_DRAW_BLOCK):
                stop = min(start + JOINT_DRAW_BLOCK, count)
                rows = matrix[indices[start:stop, i + 1]] * alpha[i][None, :]
                indices[start:stop, i] = _sample_columns(rng, rows)
        return indices

    return (lambda k: alpha[k][None, :] * beta[k + 1][:, None],
            lambda i: alpha[i] * beta[i],
            draw_boundaries)


def _normalised(matrix: np.ndarray) -> np.ndarray:
    """Scale a matrix by its own peak, so a chain of matrix products does not
    underflow. Everything built from these is normalised per-distribution at
    the end, so the discarded factors do not matter."""
    peak = matrix.max()
    return matrix / peak if peak > 0 else matrix


def _messages_with_span_prior(chain: Chain, allowed: Dict, transfer,
                              correction: np.ndarray):
    """
    The same, once the span correction couples the first boundary to the last.
    That breaks the forward/backward recursion -- a message can no longer be a
    vector, because everything downstream still depends on where b_first was --
    so each message becomes a MATRIX and each step a matrix product.

    With S_i[older, younger] = T_i:
        A_k[b_first, b_k]  = (S_0 ... S_(k-1))     everything before group k
        D_k[b_k, b_last]   = (S_k ... S_(n-2))     from b_k to the end
    and the weight outside group k, with the correction C[b_last, b_first]:
        P_k[b_k, b_(k+1)] = A_k^T C^T D_(k+1)^T

    O(M^3) per group instead of O(M^2), which is why it is only built when
    the correction is actually going to change something.

    Both P_k and the boundary marginals share the left factor A_k^T C, so it
    is formed once per boundary here and reused; the marginal is then the
    diagonal of (A_i^T C) D_i^T, which is a row-wise dot product and costs
    O(M^2) rather than the O(M^3) of forming the product and discarding all
    but its diagonal. Measured on the shared-boundary Nenthorn fixture the
    solve went from 5.1 s to about 2 s for identical output.
    """
    num_groups = len(chain.groups)
    first_ok = np.where(allowed[chain.boundary_ids[0]], 1.0, 0.0)
    last_ok = np.where(allowed[chain.boundary_ids[-1]], 1.0, 0.0)
    # C indexed [oldest, youngest], restricted to allowed ends.
    C = _normalised(correction.T * first_ok[:, None] * last_ok[None, :])

    # A[k]: from the first boundary to boundary k. A[0] is the identity, so
    # that a chain of one group reduces to the plain case.
    identity = np.diag(first_ok)
    A = [identity]
    for i in range(num_groups):
        A.append(_normalised(A[i] @ transfer(i).T))
    # D[k]: from boundary k to the last.
    D = [None] * (num_groups + 1)
    D[num_groups] = np.diag(last_ok)
    for i in range(num_groups - 1, -1, -1):
        D[i] = _normalised(transfer(i).T @ D[i + 1])

    # L[k] = A[k]^T C, indexed [b_k, b_last]: everything before boundary k
    # with the end-to-end correction folded in. Only A[n] is needed after
    # this (by draw_boundaries), so the rest of A is released.
    L = [A[k].T @ C for k in range(num_groups + 1)]
    A_last = A[num_groups]
    del A

    def pair_weight(k):
        # [older, younger] -> transposed to the [younger, older] the caller uses.
        return (L[k] @ D[k + 1].T).T

    def boundary_density(i):
        # diag(L[i] @ D[i].T) without forming the product.
        return np.einsum('ij,ij->i', L[i], D[i])

    def draw_boundaries(rng, count: int) -> np.ndarray:
        """
        Joint draws, as in the plain case, but the correction makes the chain
        non-Markov so the sampling order changes.

        C couples b_first to b_last, so there is no backward pass over single
        boundaries. CONDITIONAL on both ends, though, C is a constant and the
        rest is an ordinary chain again: the two ends are drawn together from
        their joint C * A[n], and the interior filled in forwards, each boundary
        from T_i(b_i, b_(i+1)) D_(i+1)(b_(i+1), b_last) given the one before it
        and the far end.

        The last step needs no special case: D[n] is diagonal on the allowed
        last boundary, so the final draw can only land on the b_last already
        drawn, which is the consistency check falling out of the maths rather
        than being imposed on it.
        """
        num_groups = len(chain.groups)
        joint = C * A_last                            # [b_first, b_last]
        width = joint.shape[1]
        flat = _sample_indices(rng, joint.ravel(), count)
        indices = np.empty((count, num_groups + 1), dtype=np.int64)
        indices[:, 0] = flat // width
        indices[:, num_groups] = flat % width
        for i in range(num_groups - 1):
            older_to_younger = transfer(i).T          # [older, younger]
            reach = D[i + 1].T                        # [b_last, b_(i+1)]
            for start in range(0, count, JOINT_DRAW_BLOCK):
                stop = min(start + JOINT_DRAW_BLOCK, count)
                rows = (older_to_younger[indices[start:stop, i]]
                        * reach[indices[start:stop, num_groups]])
                indices[start:stop, i + 1] = _sample_columns(rng, rows)
        return indices

    return pair_weight, boundary_density, draw_boundaries


def _draw_dates(rng, chain: Chain, cumulative: Dict[str, np.ndarray],
                boundary_draws: np.ndarray) -> Dict[str, np.ndarray]:
    """
    A calendar index for every date, one per joint boundary draw.

    Conditional on its enclosing boundaries a date is its own likelihood
    truncated to that interval -- that IS the phase model -- and a group's dates
    are then conditionally independent, so each is an inverse-CDF draw on the
    interval.

    The truncation uses the plain cumulative rather than the trapezoidal
    _interval_integral the solver's weights use. The two differ by half a grid
    cell at each end, which moves a drawn date by at most one cell, far below
    the five-year step these draws are reported at.
    """
    draws: Dict[str, np.ndarray] = {}
    for index, date_ids in enumerate(chain.groups):
        older = boundary_draws[:, index]
        younger = boundary_draws[:, index + 1]
        low = np.minimum(older, younger)
        high = np.maximum(older, younger)
        for date_id in date_ids:
            cum = cumulative[date_id]
            below = np.concatenate([[0.0], cum])
            floor, ceiling = below[low], cum[high]
            uniform = rng.random(len(low))
            picked = np.searchsorted(cum, floor + uniform * (ceiling - floor))
            # An interval holding no likelihood at all (a date whose support
            # lies entirely outside where its boundaries landed) still has to
            # yield a value inside it rather than an out-of-range index.
            empty = ceiling <= floor
            if np.any(empty):
                spread = (low + uniform * (high - low + 1)).astype(np.int64)
                picked = np.where(empty, spread, picked)
            draws[date_id] = np.clip(picked, low, high)
    return draws


def solve(measurements: List[Dict], root: Dict, chain: Chain,
          num_samples: int = 4000, seed: int = 42,
          uniform_span_prior: bool = True,
          estimate_durations: bool = False,
          dispersal: Optional[Dict] = None,
          interval_draws: int = intervals.DEFAULT_DRAWS) -> Dict:
    """
    Exact marginal posteriors for every date and every boundary in `chain`.

    Returns the same shape services/gibbs.py's solve does, so callers need not
    know which produced it -- including `posterior_samples`, drawn from each
    exact marginal by inverse CDF so plotting and export code is unchanged.

    Those draws are from the MARGINALS and carry no joint correlation between
    dates. Nothing reads joint structure off them: Amodel and
    `estimate_durations` both need it, and both read the separate joint draws
    this takes from the message-passed posterior -- the dates' half of which
    is returned as `joint_draws`, {date_id: calendar ages}, one per draw.

    `uniform_span_prior` applies OxCal's correction of the same name, on by
    default there too; see _span_prior_correction.

    `interval_draws` joint draws are taken whatever was asked for, since Amodel
    needs them; they use their own generator, so every other number here is
    bit-for-bit what it was before they were collected.

    `estimate_durations` adds `intervals` (services/intervals.py), read off
    those same draws. Off by default because a duration is a claim about a
    phase's extent not every analysis wants to make.

    `dispersal` asks for the spatial section, and needs the same joint draws
    for the same reason -- so it is a second reason to take them, independent
    of whether durations were wanted. See intervals.report for its shape.
    """
    element_bounds = inherited_bounds(root)
    by_id = {m['id']: m for m in measurements}
    grid = _build_grid(measurements, element_bounds, len(chain.groups))
    dt = float(grid[1] - grid[0])
    M = len(grid)

    likelihood, cumulative, outlier_parts = date_tables(
        grid, measurements, element_bounds)

    span = grid[None, :] - grid[:, None]              # [younger, older]
    valid = span > 0

    # Each boundary may carry its own TPQ/TAQ, which restricts where it can sit.
    allowed = {}
    for bid in chain.boundary_ids:
        taq, tpq = element_bounds.get(bid, (None, None))
        ok = np.ones(M, dtype=bool)
        if taq is not None:
            ok &= grid >= taq
        if tpq is not None:
            ok &= grid <= tpq
        allowed[bid] = ok

    # T_i over each group, dates integrated out.
    log_T = []
    for index, date_ids in enumerate(chain.groups):
        K = len(date_ids)
        with np.errstate(divide='ignore', invalid='ignore'):
            term = np.where(valid, -K * np.log(np.where(valid, span, 1.0)), -np.inf)
        for date_id in date_ids:
            term = term + _log_interval_integral(
                cumulative[date_id], likelihood[date_id], dt)
        older_ok = allowed[chain.boundary_ids[index]]
        younger_ok = allowed[chain.boundary_ids[index + 1]]
        term = np.where(younger_ok[:, None] & older_ok[None, :], term, -np.inf)
        log_T.append(term)

    def transfer(i):
        """T_i, exponentiated on demand rather than kept alongside log_T."""
        out = np.exp(_rescaled(log_T[i]))
        return np.where(np.isfinite(out), out, 0.0)

    all_dates = [d for group in chain.groups for d in group]
    inliers = {d: (outlier_parts[d][0] if d in outlier_parts else likelihood[d])
               for d in all_dates}
    oldest_limits, youngest_limits = pair_limits(
        element_bounds, chain.boundary_ids[0], chain.boundary_ids[-1],
        grid, inliers, all_dates)
    log_correction = _span_prior_correction(
        grid, len(chain.boundary_ids), uniform_span_prior,
        oldest_limits, youngest_limits)
    correction = None
    if log_correction is not None:
        if len(chain.groups) == 1:
            # With one group the correction's boundaries ARE this group's, so
            # it folds into the transfer matrix and costs nothing. Worth the
            # special case: it is the app's default shape, and the limits
            # factor applies to it where the (n-2) factor does not, so without
            # this every single-phase model would take the O(M^3) path.
            log_T[0] = log_T[0] + np.where(np.isfinite(log_correction),
                                           log_correction, -np.inf)
            pair_weight, boundary_density, draw_boundaries = _messages(
                chain, allowed, transfer)
        else:
            correction = np.where(np.isfinite(log_correction),
                                  np.exp(_rescaled(log_correction)), 0.0)
            correction = np.where(np.isfinite(correction), correction, 0.0)
            pair_weight, boundary_density, draw_boundaries = _messages_with_span_prior(
                chain, allowed, transfer, correction)
    else:
        pair_weight, boundary_density, draw_boundaries = _messages(
            chain, allowed, transfer)

    rng = np.random.default_rng(seed)
    results = {'measurements': {}, 'boundaries': {}}
    densities: List[np.ndarray] = []

    for index, date_ids in enumerate(chain.groups):
        # Everything outside this group, once per group: on the span-prior
        # path it is two M x M matrix products, and every date in the group
        # shares it.
        outside = pair_weight(index)
        for date_id in date_ids:
            # This date's own integral divided back out, leaving it free.
            with np.errstate(invalid='ignore'):
                weights = np.where(np.isfinite(log_T[index]),
                                   log_T[index] - _log_interval_integral(
                                       cumulative[date_id], likelihood[date_id], dt),
                                   -np.inf)
            G = np.exp(_rescaled(weights))
            G = np.where(np.isfinite(G), G, 0.0)
            W = G * outside
            # Sum over {older boundary >= t} x {younger boundary <= t}, which
            # a pair of cumulative sums leaves on the diagonal. Both in place:
            # at M = 2000 each M x M temporary is 32 MB, and the sums are the
            # same whether or not they overwrite their input.
            np.cumsum(W, axis=0, out=W)
            reversed_view = W[:, ::-1]
            np.cumsum(reversed_view, axis=1, out=reversed_view)
            density = likelihood[date_id] * np.diagonal(W)
            densities.append(density)
            summary = _summarize(grid, density, rng, num_samples)
            if date_id in outlier_parts:
                summary['outlier_probability'] = _outlier_probability(
                    density, likelihood[date_id], *outlier_parts[date_id])
            results['measurements'][date_id] = summary

    for i, bid in enumerate(chain.boundary_ids):
        density = boundary_density(i)
        if density.sum() <= 0:
            continue
        densities.append(density)
        results['boundaries'][bid] = {
            'name': chain.boundary_names.get(bid, 'Boundary'),
            'posterior': _summarize(grid, density, rng, num_samples),
        }

    # Joint draws: taken always, because Amodel -- the whole model's agreement
    # index -- is a function of every date at once and is always reported.
    # The dates' draws are returned as `joint_draws` for services/agreement.py
    # to read, rather than scored here: the diagnostic needs each date's
    # posterior outlier probability as well, which is a finished result, and
    # keeping the scoring out of the solvers keeps it one definition for both.
    # Durations and a dispersal fit read the SAME draws when they were asked
    # for, so the sections cannot describe different states of the model.
    #
    # Seeded apart from the generator above, so every other number here stays
    # bit-for-bit identical to what it was before any of this was collected.
    joint_rng = np.random.default_rng(seed + 1)
    boundary_draws = draw_boundaries(joint_rng, interval_draws)
    date_draws = _draw_dates(joint_rng, chain, cumulative, boundary_draws)
    joint = {bid: grid[boundary_draws[:, i]]
             for i, bid in enumerate(chain.boundary_ids)}
    joint.update({date_id: grid[idx] for date_id, idx in date_draws.items()})
    results['joint_draws'] = {date_id: grid[idx] for date_id, idx in date_draws.items()}
    # The Boundaries in the same states, for a per-phase KDE to reflect at
    # (services/kde.report_phases).
    results['joint_boundaries'] = {bid: joint[bid] for bid in chain.boundary_ids}
    if estimate_durations or dispersal is not None:
        results['intervals'] = intervals.report(
            root, measurements, joint, independent=True,
            durations=estimate_durations, dispersal=dispersal)

    results['method'] = 'exact'
    results['uniform_span_prior'] = log_correction is not None
    results['grid'] = {'lower': float(grid[0]), 'upper': float(grid[-1]), 'step': dt}
    results['edge_mass'] = _edge_mass(densities, grid)
    return results


def _outlier_probability(density: np.ndarray, mixture: np.ndarray,
                         inlier: np.ndarray, broad: np.ndarray,
                         prior: float) -> float:
    """
    P(this date is an outlier | all the data).

    Given the date landed at t, its probability of being an outlier is the
    responsibility of the outlier component there, p*L_out(t)/L_mix(t).
    Averaging that over the date's own posterior marginalises t out:

        P(outlier) = integral of posterior(t) * p*L_out(t)/L_mix(t) dt

    Both pieces are already computed, so this costs two array products.
    """
    weight = density.sum()
    if weight <= 0:
        return 0.0
    responsibility = np.where(mixture > 0, prior * broad / np.maximum(mixture, 1e-300), 0.0)
    return float((density / weight * responsibility).sum())


def _edge_mass(densities: List[np.ndarray], grid: np.ndarray) -> float:
    """
    The most probability any single distribution leaves in the outermost 1% of
    the grid. A boundary's tail is genuinely heavy, so a non-trivial value means
    the window clipped something real and should be widened.
    """
    edge = max(1, len(grid) // 100)
    worst = 0.0
    for dens in densities:
        total = dens.sum()
        if total > 0:
            worst = max(worst, float((dens[:edge].sum() + dens[-edge:].sum()) / total))
    return worst


def _reported_ranges(grid: np.ndarray, mass: np.ndarray, confidence: float) -> List[Dict]:
    """
    HPD ranges at REPORTING_STEP_YEARS rather than at the solver's internal
    grid step.

    Two reasons. The internal step is chosen from the date range and the number
    of groups, so reporting at it would make a published range depend on an
    implementation detail -- the same dates in a longer sequence would come back
    with different widths. And a modelled posterior on a wiggly curve is
    genuinely multi-peaked, so a fine grid picks out the peak tops and skips the
    dips: at 1 year Wk-51189's 68.3% region came back as three fragments
    totalling 24 years where OxCal reports 35, and rebinned to 5 years it gives
    30.

    Five years is OxCal's own default reporting resolution and about as fine as
    a calibrated date is meaningful. The full-resolution density is still what
    gets plotted and what the point estimates come from.
    """
    step = REPORTING_STEP_YEARS
    dt = float(grid[1] - grid[0])
    # Each grid point stands for the cell [t - dt/2, t + dt/2]; the mass in a
    # reporting bin is the cumulative mass between its edges, read off the
    # cell edges by interpolation. Exact for a density that is constant
    # within a cell, and -- unlike np.histogram on the points -- unaffected
    # by how many grid points happen to fall in each bin, which for a 1.015
    # year step is four or five and put a +/-20% sawtooth on the bin masses.
    cell_edges = np.concatenate([[grid[0] - dt / 2.0], grid + dt / 2.0])
    cdf = np.concatenate([[0.0], np.cumsum(mass)])
    lo = np.floor(cell_edges[0] / step) * step
    hi = np.ceil(cell_edges[-1] / step) * step
    edges = np.arange(lo, hi + step, step)
    binned = np.clip(np.diff(np.interp(edges, cell_edges, cdf)), 0.0, None)
    centres = (edges[:-1] + edges[1:]) / 2.0

    keep = binned > 0
    if not np.any(keep):
        return [{'min': float(grid[0]), 'max': float(grid[-1]),
                 'probability_percent': 100.0}]
    ranges = hpd_ranges_from_distribution(centres[keep], binned[keep], confidence)
    for r in ranges:
        r['min'] -= step / 2.0
        r['max'] += step / 2.0
    return ranges


def _plottable(grid: np.ndarray, density: np.ndarray) -> List[List[float]]:
    """
    The density trimmed to where it actually has mass.

    The solver's grid runs far wider than the dates because the boundaries need
    room -- 2031 years on the OxCal fixture against a posterior occupying about
    100 -- so handing the whole grid to the plot would draw every modelled date
    as a spike in a mostly-empty axis.
    """
    peak = density.max()
    if peak <= 0:
        return [[float(a), float(p)] for a, p in zip(grid, density)]
    significant = np.where(density >= peak * DISPLAY_THRESHOLD)[0]
    margin = max(1, int(round(PLOT_MARGIN_YEARS / (grid[1] - grid[0]))))
    lo = max(0, int(significant[0]) - margin)
    hi = min(len(grid), int(significant[-1]) + margin + 1)
    return [[float(a), float(p)] for a, p in zip(grid[lo:hi], density[lo:hi])]


def _summarize(grid: np.ndarray, density: np.ndarray, rng, num_samples: int) -> Dict:
    """
    An exact density in the summary shape the rest of the app expects. Point
    estimates come from the density itself rather than from draws, so they carry
    no Monte Carlo error; the draws exist only for downstream code that wants
    samples.
    """
    dt = float(grid[1] - grid[0])
    total = density.sum() * dt
    if total <= 0:
        raise ValueError('a posterior came out empty; the model may be contradictory')
    density = density / total
    mass = density * dt

    mean = float((grid * mass).sum())
    variance = float(((grid - mean) ** 2 * mass).sum())
    cdf = np.cumsum(mass)
    median = float(np.interp(0.5, cdf, grid))

    hpd_68 = _reported_ranges(grid, mass, SIGMA_1)
    hpd_95 = _reported_ranges(grid, mass, SIGMA_2)

    draws = np.interp(rng.random(num_samples), cdf, grid)

    return {
        'posterior_mean': mean,
        'posterior_median': median,
        'posterior_std': float(np.sqrt(max(variance, 0.0))),
        'hpd_68': (min(r['min'] for r in hpd_68), max(r['max'] for r in hpd_68)),
        'hpd_95': (min(r['min'] for r in hpd_95), max(r['max'] for r in hpd_95)),
        'hpd_68_ranges': hpd_68,
        'hpd_95_ranges': hpd_95,
        'probability_distribution': _plottable(grid, density),
        'posterior_samples': draws.tolist(),
    }
