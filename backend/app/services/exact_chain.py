"""
Exact solver for chain-shaped chronological models -- a Sequence of Phases
separated by Boundaries, which is the ordinary shape of an archaeological
model and the one OxCal's documentation recommends.

Why this exists instead of just sampling the model
--------------------------------------------------
For this shape the posterior can be integrated exactly, so there is no
sampler to converge and no mode to miss. That matters specifically because
calibration curves wiggle: a single radiocarbon age routinely calibrates to
two or more separated calendar ranges, and resolving which one is right is
much of the reason to build a Bayesian model at all.

pm.sample_smc gets that wrong here, and the reason is structural. PyMC splits
a model into `varlogp` (free variables) and `datalogp`, and SMC tempers the
second: it anneals p(x) * L(x)^beta from beta = 0 to 1. But `datalogp` is
`observedlogp + potentiallogp`, and the PyMC model this replaced expressed the
entire model structure -- ordering constraints and uniform-scatter priors alike
-- as pm.Potential. So at beta = 0 the particles are drawn from unconstrained
uniform priors thousands of years wide, with no ordering and no phase prior
at all, and the constraints only switch on as beta approaches 1.

At intermediate beta each date has settled into one of its own calibration
lobes more or less independently of the others. Configurations that put
different groups in different lobes vastly outnumber the correlated one where
the whole model sits in the same lobe, so by the time the ordering constraint
is fully on, the correct arrangement has too few surviving particles to be
weighted properly. Measured against the OxCal 4.4 run in tests/fixtures/oxcal
(eight bimodal SHCal20 dates in two ordered groups), SMC separated the two
groups by 57 years where the exact answer is 19 and OxCal reports 13 --
manufacturing a chronological break at AD 1350 that is an artefact of the
curve. It reported r_hat 1.01 throughout: all four chains agreed on the same
wrong answer. Neither a better kernel, more tempering stages nor a narrower
prior fixed it (57 -> 30 at best), because none of them changes the annealing
path that causes it.

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
scale shared by every date using it, constrained by the data; this solver
marginalises that scale independently per date over its prior instead, because a
shared hyperparameter couples the dates and the message passing above needs each
to enter through its own likelihood alone. Deliberately left that way: the
approximation is what makes the exact integration possible at all, and
services/gibbs.py samples the shared scale properly for callers who need it
(bayesian_config.solver = "gibbs").

What it costs, measured: on tests/fixtures/oxcal_outlier the per-date
marginalisation puts the reported outlier probabilities 0.0166 from OxCal's
where the sampler gets to 0.0016, and on tests/fixtures/oxcal_charcoal -- three
dates declared residual outright, the case that exposes it -- the modelled dates
land 9.9 years rms from OxCal against the sampler's 6.3. So it matters most
exactly where an outlier prior is large, and hardly at all where it is the usual
few percent.

PHASE WIDTH used to differ from OxCal by a factor of several, and the cause was a
first-order error in the interval quadrature -- see _interval_integral, which
carries the measurements. Correcting it took a single phase of sixteen dates from
a span of 4.2 years against OxCal's 19.3 to 20.7, and the modelled dates on the
eight-date fixture from 5.11 years rms of OxCal to 0.20.

What remains is on models with a date declared an outright outlier
(tests/fixtures/oxcal_charcoal), where the span now OVERSHOOTS -- 54.9 years
against OxCal's 29.2. That is the separate degenerate mode described in
tests/nenthorn_charcoal_fixture's PHASE WIDTH note, not the phase model.

services/gibbs.py carries the same rule, as Model.endpoint_weight -- it has no
interval integrals to correct, but the mismatch between a date's available CELLS
and the span it is charged for is identical there. The two agree to about 0.6
years on the chain fixtures; while only this file had the rule they were 5-6 years
apart, which is what those comparisons are set to catch.

`layout()` returns None for anything that is not a single chain, and the
caller falls back to the sampler. In particular a Sequence that orders two groups
with NO boundary between them is not a chain: its dates share one enclosing
boundary pair while an extra constraint orders them against each other, and
that constraint does not factorise along the chain. Adding an explicit
Boundary between such groups makes the model solvable here, and is better
practice anyway.
"""
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.signal import fftconvolve
from scipy.special import gammaln

from app.services.calibration import get_curve, hpd_ranges_from_distribution
from app.services.model_tree import inherited_bounds, plan_model
from app.services.stratigraphy import effective_bounds

# Largest calendar grid the solver will build. Every step of the algorithm is
# O(M^2) in space as well as time, so this caps one intermediate array at
# about 30 MB and the whole solve at a few seconds.
MAX_GRID_POINTS = 2000
# How many groups fit at that point count before the grid starts coarsening.
# One M x M array of doubles is 32 MB at M = 2000 and one is held per group,
# so this is the memory budget in disguise: a long sequence trades calendar
# resolution for staying inside it rather than being killed.
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
# drawn as 10^u over three decades, exactly as the General model draws its own
# scale over four. Integrating that scale is not a refinement: with a single
# fixed scale instead (this used one 200-year exponential) only 12% of the
# offset mass lands within 20 years, against 55% for OxCal, so a flagged date
# stops saying anything about where its context sits and drifts onto its
# neighbours instead of pulling the group towards itself. Measured on a real
# OxCal run of eleven Nenthorn dates with three at Outlier("Charcoal", 1.00),
# integrating the scale moved the modelled dates from 16.1 to 9.9 years rms of
# OxCal's own answer, and by a similar 6 years on every model structure tried.
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

    Deliberately semantic rather than syntactic: it works from plan_model's
    output, so nesting that adds no ordering of its own (a Sequence wrapping a
    single Phase, a Phase holding two parallel Phases) is still recognised as
    a chain.
    """
    return layout_with_reason(root, measurements)[0]


def layout_with_reason(
    root: Dict, measurements: List[Dict]
) -> Tuple[Optional[Chain], Optional[str]]:
    """
    layout(), plus a plain-English reason when the model is not a chain.

    The reason exists so the API can tell the user WHY their model fell back
    to sampling rather than only that it did -- a silent fallback is the whole
    problem, and "this shape is not exactly solvable" is not actionable on its
    own. It is produced here, at the point of decision, rather than by a
    separate diagnostic that inspects the tree afterwards: a second
    implementation of these rules would drift from this one, and would then
    confidently explain a fallback that did not happen for that reason.

    Phrase each reason as what the model does, and where practical what to
    change -- these strings are shown to an archaeologist, not a developer.
    """
    if root is None:
        return None, 'the analysis has no model structure'
    plan = plan_model(root, measurements)
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

    # Finally: the chain must already imply every ordering constraint the tree
    # asks for. Rank boundary i at 2i and group i at 2i+1, oldest first; a
    # constraint is implied when everything on its older side outranks
    # everything on its younger side. Anything else -- most importantly two
    # groups ordered against each other INSIDE one boundary pair -- is extra
    # structure this solver cannot express.
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

    Two models, because the two ways a date goes wrong are not the same
    shape:

    "general" -- OxCal's `Outlier_Model("General", T(5), U(0,4), "t")`
        (Bronk Ramsey 2009): a Student-t offset scaled by 10^u with u
        uniform over four decades. Symmetric and very heavy-tailed, so a
        flagged date says almost nothing about where it sits. The right
        model when you do not know WHY a date is wrong -- lab error, a
        mislabelled bag, contamination in either direction.

    "charcoal" -- OxCal's `Outlier_Model("Charcoal", Exp(1,-10,0), U(0,3),
        "t")`: a ONE-SIDED exponential gap, the dated material having formed
        some years BEFORE the context and never after, with its scale drawn
        over three decades the same way the general model draws its own. This
        is old wood and reworked material, where the error has a known
        direction, and saying so is worth a lot. Measured on an injected
        residual date, it recovered the model's Start boundary to AD 1292
        against the general model's 1201 (clean value 1339) and put the
        outlier probability at 90% rather than 66%, because a one-sided model
        does not have to spend prior mass on the direction the material
        cannot be wrong in.

    Both are summed over their scale here, which is an APPROXIMATION to what
    OxCal does: OxCal treats the scale as one hyperparameter SHARED by every date
    using that model and lets the data constrain it, where summing marginalises it
    independently per date over the whole prior. services/gibbs.py samples the
    shared scale properly; see scale_nodes below, and that module's
    Model._build_outlier_tables for what the difference is worth.
    """
    total = None
    for log_scale in scale_nodes(model):
        component = _outlier_kernel_at_scale(step, half_width, model, log_scale)
        total = component if total is None else total + component
    return total / total.sum()


def scale_nodes(model: str = DEFAULT_OUTLIER_MODEL) -> np.ndarray:
    """
    The offset scales an outlier model is allowed, as log10(years).

    OxCal writes these as the second argument of Outlier_Model: U(0,4) for
    General, U(0,3) for Charcoal. Uniform on the LOG scale, so every decade is
    equally likely a priori -- which is what makes a third of the charcoal prior
    sit at ten years or less.
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
        # CONTEXT, so the context's calendar age is YOUNGER -- lower cal BP,
        # a lower grid index. The kernel therefore carries its mass at
        # negative lag; putting it at positive lag says the context predates
        # the wood found in it, which is residuality backwards and made a
        # residual date look MORE consistent rather than less.
        #
        # The lag axis runs to the WIDEST scale's reach so every scale shares
        # it, but each is truncated at its own 10 scale lengths -- OxCal's
        # Exp(1,-10,0) -- rather than at the widest one's.
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

    The kernel is trimmed to the lags that can actually reach a grid point from
    a grid point before convolving. That is exact rather than an economy -- a
    lag longer than the grid contributes to no on-grid output at all -- and it
    matters because these kernels deliberately run to 10,000-40,000 years while
    the grid is a few thousand. Trimming happens AFTER normalisation, so the
    mass the kernel throws off the grid stays thrown off; see
    _with_outlier_component for why that is the point rather than a defect.
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
    # Deliberately NOT renormalised over the grid. The convolution of two
    # normalised densities is already proper on the real line; most of its
    # mass lies off this grid, which is exactly the statement that a flagged
    # date's calendar age could be anywhere. Rescaling what remains back to
    # one would inflate the component by the reciprocal of the retained mass
    # and make the outlier probability depend on how wide the grid happens to
    # be -- measured, it put the fifteen sound Shag River dates at 9-14%
    # against OxCal's 0.6-1.5%, above their own 5% prior. The mixture is
    # therefore also sub-unit here, which is correct: the missing mass is the
    # outlier possibility that the window does not cover.
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

    Shared with services/gibbs.py rather than written out twice. Both solvers
    need the identical table for their answers to be comparable at all, and a
    second copy of the normalisation, the TPQ/TAQ truncation and the outlier
    mixture would drift -- which would show up as the two solvers disagreeing
    for a reason that has nothing to do with either one's inference.
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
    A calendar grid wide enough for both the dates and the boundaries either
    side of them, at the finest step the memory budget allows.

    One M x M array of doubles is 32 MB at M = 2000, and a transfer matrix is
    held per group, so the point count is scaled down as the model grows to
    keep the total near that of a single-group solve. A long sequence gets a
    coarser calendar step rather than an out-of-memory kill.
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

    # Only a long sequence needs coarsening: up to GROUPS_AT_FULL_RESOLUTION
    # groups the full point count fits comfortably.
    budget = MAX_GRID_POINTS / max(1.0, np.sqrt(num_groups / GROUPS_AT_FULL_RESOLUTION))
    step = max(MIN_GRID_STEP_YEARS, (hi - lo) / budget)
    return np.arange(lo, hi + step, step)


def _rescaled(log_weights: np.ndarray) -> np.ndarray:
    """Shift log weights by their own finite maximum, ready to exponentiate.

    Per-array rather than against one shared constant: dividing a joint weight
    by one date's interval integral can raise it by hundreds of nats, which
    overflows against a shared shift.
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

    The half-weights are not a refinement, they are the difference between right
    and wrong here, and the reason is the (span)^(-K) factor this gets multiplied
    by. A cumulative difference alone, cum[o] - cum[y-1], sums cells y THROUGH o
    inclusive, so an interval whose span is k*step receives (k+1) cells of mass.
    The span prior meanwhile uses k*step. At the smallest span the likelihood is
    therefore too large by a factor of two PER DATE -- 2^K over a group, which is
    256 for eight dates and 65536 for sixteen -- and that lands squarely on the
    near-coincident configurations the span prior already favours. The posterior
    piled up there: on the eight-date Nenthorn fixture 85% of the mass sat below a
    five-year span, with the median span exactly one grid cell, and a modelled
    phase came out 5.8 years wide against OxCal's 29.3.

    Measured against a continuum reference with no quadrature at all (three
    Gaussian likelihoods, so the interval integrals are exact error functions;
    truth 181.5 years):

        grid step      cum[o] - cum[y-1]      trapezoidal
           10.0                   126.4            186.3
            5.0                   141.5            183.8
            2.5                   155.2            182.6
            1.0                   167.8            181.9

    So the plain difference is biased low by 30% at a ten-year step and converges
    only as the step vanishes, while the trapezoidal form is within 3% at ten
    years. On the fixture that made this visible it takes the modelled dates from
    5.11 years rms of OxCal to 0.20, and makes the answer grid-independent
    (34.9/34.7/34.6 across a fourfold refinement, against 5.8/8.1/12.2 before).

    `density` and `step` are required rather than optional because the uncorrected
    form is wrong wherever it is used, and an optional argument invites reaching
    it by accident.
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

    This is the exact discrete version of the volume the UniformSpanPrior
    correction has to divide out, and it is deliberately a COUNT rather than a
    power -- see _span_prior_correction for why the power is wrong on a grid.

    Shared with services/gibbs.py so the two solvers cannot drift apart on it.
    Only defined up to a constant factor, like the correction itself: the
    1/num_interior! in the continuum expression is absorbed into the posterior's
    normalisation either way.
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

    These are the llim/ulim of Bronk Ramsey 2008 A.30-A.31. Only REAL constraints
    count -- a TPQ or TAQ on the Boundary or on an element enclosing it. The
    solver's grid is deliberately not used, even though it does bound the answer
    numerically: the grid is padded by six times the dates' own width for the
    boundaries' benefit (see _build_grid), so treating its edges as model
    statements would make the limits factor an artefact of that padding. Measured,
    it does exactly that -- the factor's effect on a single unconstrained phase
    came out as +1.20 years at a 600-year window, +0.56 at 1,200, +0.27 at 2,400
    and +0.13 at 4,800, halving with each doubling and heading for nothing.

    An unconstrained boundary is unbounded, and in that limit g is infinite, so
    1/g is constant in the span and drops out of the posterior. That is why an
    unconstrained model gets no limits factor at all rather than a small arbitrary
    one.
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
    The limits factor as a function of the span alone, for `spans` of any shape --
    see _log_limits_factor for the derivation and for what the four terms mean.

    Split out from it so services/gibbs.py can ask for a 1-D row indexed by span
    without allocating the M x M matrix the exact solver needs (32 MB at M = 2000),
    while both still share one definition of the terms and of their sign.
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
    out = np.full(g.shape, -np.inf)
    positive = g > 0
    out[positive] = -np.log(g[positive])
    return out


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

    WHICH TERM IS SMALLEST is the whole character of this factor, and it is why
    the sign of it is easy to get backwards. The first term falls with the span, so
    1/g rises and favours a LONGER span; the second rises with the span, so 1/g
    favours a SHORTER one. The last two are constants that merely cap it.

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
    uniform within it, so the induced prior on the overall span carries a
    factor for that freedom -- it favours long sequences purely because a
    longer one has more room to arrange its interior boundaries in. Dividing
    that out makes the span prior independent of how finely the sequence is
    subdivided, which is Nicholls and Jones (2001)'s recommendation and
    OxCal's default.

    WHY A BINOMIAL AND NOT (b_first - b_last)^-(n-2), which is what this
    computed until it was measured. The continuum factor is span^(n-2), but the
    model this solver actually integrates is discrete, and on a grid the number
    of ways to place k ordered interior boundaries strictly inside a span of j
    cells is C(j-1, k), not (j*step)^k. The two agree only as j grows: at k = 1
    they are 1/(j-1) against 1/j, a first-order error, and the span prior
    concentrates mass at SMALL j where it is largest. Same family as the
    interval-quadrature bug in _interval_integral, and found the same way.

    Measured with the likelihoods switched off, so the reported posterior IS the
    prior and the answer is known in closed form (uniform over the ordered pair,
    hence a linear marginal for each outermost boundary). Total-variation
    distance of the oldest boundary's marginal from that, at a step of 1.33
    years, before -> after:

        n = 3   0.39% ->  0.00%      mean shift +2.04y -> +0.00y
        n = 4   0.93% ->  0.00%                 +4.80y -> +0.00y
        n = 5   1.56% ->  0.00%                 +7.99y -> +0.00y

    The old error was first-order in the step and so shrank under refinement,
    which is why it survived every fixture comparison: it looked like a modelling
    difference against OxCal rather than a bug.

    Two things this deliberately does NOT do:

    * THE LIMITS FACTOR IS MISSING, AND THAT IS A REAL DEFECT -- this comment
      used to claim it was negligible, and it is not. OxCal's UniformSpanPrior
      also carries the factor of Bronk Ramsey 2009a eq 29-30, 1/g(t_a, t_m,
      llim_a, llim_m, ulim_a, ulim_m). The old argument for dropping it was that
      a real span of tens of years is nothing against the computational window,
      supported by identical results at windows of 2,031 / 3,000 / 10,000 /
      55,000 years -- which tested OUR window sensitivity and not what OxCal
      does with the term.

      Measured by running OxCal with the option off, on eight overlapping dates
      in one phase (no ordering, no outliers, likelihoods agreeing with ours to
      0.1% in width):

          OxCal, UniformSpanPrior ON     span 26.26   dates spread 3.62
          OxCal, UniformSpanPrior OFF    span 31.83   dates spread 4.92
          this solver                    span 32.53   dates spread 5.18

      So we reproduce OxCal with the refinement switched OFF, and the term is
      worth 5.6 years of span -- 18% -- on that model. OxCal's ON/OFF ratio is
      about the same on every model tried: 0.825 here, 0.847 on the `ordered`
      fixture, 0.792 on the charcoal one.

      What the term is. Bronk Ramsey 2008 appendix A.31 gives it in full (2009a
      eq 29 is the same expression):

          g = min( (ulim_m - llim_a) - (t_m - t_a),
                   (t_m - t_a) - (llim_m - ulim_a),
                   ulim_a - llim_a,
                   ulim_m - llim_m )

      with 1/g added to the prior. Which of the four terms is the smallest decides
      which WAY it biases the span, and that is the whole subtlety. Take llim/ulim
      to be the wide computational window and the first term wins, g = window -
      span, and 1/g flattens a span prior that would otherwise fall off as
      (window - span) -- worth about 3% here, and in the direction of a WIDER span.
      That reading cannot be what OxCal does: its effect is 18% and it narrows.
      With per-boundary limits tight enough for the SECOND term to win instead,
      1/g goes as 1/(span + c), which penalises long spans and is the right sign
      and roughly the right size.

      IT IS NOW IMPLEMENTED, but only from limits that are real -- see
      _log_limits_factor and boundary_limits. A Boundary carrying a TPQ or TAQ, or
      enclosed by an element that does, has genuine llim/ulim and gets the factor;
      an unconstrained one is UNBOUNDED, g is then infinite, 1/g is constant in the
      span, and the factor correctly drops out.

      That leaves OxCal's default answer unmatched, and deliberately so. OxCal
      evidently supplies limits of its own for unconstrained boundaries -- the
      second term of the min wins there, worth 18% -- and none of 2001, 2009a or
      2008 says what they are. Bracketing the model at AD 500-750 and at AD
      200-1100, a 3.6x change of window, moved OxCal by 0.57 years, so those limits
      are internal to it rather than the user's constraints. Standing in the grid's
      own edges was tried and rejected: it made the factor an artefact of
      _build_grid's padding, contributing +1.20 years at a 600-year window and
      +0.13 at 4,800 -- halving with each doubling, heading for nothing, and in the
      WRONG DIRECTION besides, widening the span where OxCal's narrows it. Fitting
      a constant to close the remaining gap is what the reverted minimum-span floor
      did.

      Consequences to expect meanwhile: a phase's span from this app is wider than
      OxCal's default answer, by more the more the prior does the work -- about 18%
      on eight tightly clustered dates, 62% on four, 7% on Shag River's sixteen.
      Both open items chased elsewhere are partly this: it accounts for 7.7 of the
      22 years by which the charcoal fixture's span overshoots, and for most of the
      ordering over-separation in TestOrderingOverSeparates (with the term off on
      both sides, OxCal's `ordered` separation is 13.47 against our 14.18, and its
      shared_boundary is 26.30 against our 26.7).
    * At n = 2 the exponent is zero, so a single Phase between two
      Boundaries -- the app's default shape -- is untouched. Nothing to
      apply, and the O(M^2) chain is kept.

    What it is worth, on the fixture's two-group model: it is not optional. With it
    the group separation is 14.7 years against OxCal's 13.2 and the dates 0.76
    years rms; without it, 26.7 and 6.67. It also couples the two ends of the
    chain, so the solve stops factorising and goes from O(M^2) to O(M^3) per group
    -- worth paying.

    About 1.5 years of extra separation remains, and it is not this factor's
    doing: it survives refining the grid fourfold (2.01 -> 1.50 -> 1.44 as the step
    halves twice), appears whenever two groups are ORDERED whether the middle
    Boundary is written or implied, and is absent when nothing orders them (the
    unordered run agrees to 0.06 years). See tests/nenthorn_charcoal_fixture's
    WHAT IS LEFT note.
    """
    if not enabled:
        return None
    exponent = num_boundaries - 2
    # The LOG OF THE FACTOR, so both parts enter with the same sign and -inf
    # means "this pair of boundaries is impossible".
    total = np.zeros((len(grid), len(grid)))       # [youngest, oldest]
    has_limits = False

    if exponent > 0:
        index = np.arange(len(grid))
        cells = index[None, :] - index[:, None]
        # 1 / (ways the interior boundaries fit) -- A.29, 2001 eq 6.
        log_count = log_interior_placements(cells, exponent)
        total = np.where(np.isfinite(log_count), total - log_count, -np.inf)

    if oldest_limits is not None and youngest_limits is not None:
        # 1 / g -- A.31, 2001 eq 7. Applies at ANY number of boundaries, which is
        # why this function can return something at two of them; it is None when
        # nothing constrains the boundaries, which is the usual case.
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


def _messages(chain: Chain, allowed: Dict, transfer):
    """
    Forward/backward over the boundaries, the O(M^2) case.

    Returns (pair_weight, boundary_density): pair_weight(k) is everything
    outside group k as a matrix indexed [younger boundary, older boundary],
    and boundary_density(i) is boundary i's unnormalised marginal.
    """
    alpha = [np.where(allowed[chain.boundary_ids[0]], 1.0, 0.0)]
    for i in range(len(chain.groups)):
        alpha.append((alpha[i][None, :] * transfer(i)).sum(axis=1))
    beta = [None] * len(chain.boundary_ids)
    beta[-1] = np.where(allowed[chain.boundary_ids[-1]], 1.0, 0.0)
    for i in range(len(chain.groups) - 1, -1, -1):
        beta[i] = (transfer(i) * beta[i + 1][:, None]).sum(axis=0)

    return (lambda k: alpha[k][None, :] * beta[k + 1][:, None],
            lambda i: alpha[i] * beta[i])


def _normalised(matrix: np.ndarray) -> np.ndarray:
    """Scale a matrix by its own peak. Every quantity built from these is
    normalised per-distribution at the end, so the discarded factors are
    irrelevant -- but without this a chain of matrix products underflows."""
    peak = matrix.max()
    return matrix / peak if peak > 0 else matrix


def _messages_with_span_prior(chain: Chain, allowed: Dict, transfer,
                              correction: np.ndarray):
    """
    The same, once the span correction couples the first boundary to the
    last. That breaks the forward/backward recursion -- a message can no
    longer be a vector, because everything downstream still depends on where
    b_first was -- so each message becomes a MATRIX carrying that dependence
    and each step becomes a matrix product.

    With S_i[older, younger] = T_i:
        A_k[b_first, b_k]  = (S_0 ... S_(k-1))     everything before group k
        D_k[b_k, b_last]   = (S_k ... S_(n-2))     from b_k to the end
    and the weight outside group k, with the correction C[b_last, b_first]:
        P_k[b_k, b_(k+1)] = A_k^T C^T D_(k+1)^T

    O(M^3) per group instead of O(M^2), which is why it is only built when
    the correction is actually going to change something.
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

    def pair_weight(k):
        # [older, younger] -> transposed to the [younger, older] the caller uses.
        return (A[k].T @ C @ D[k + 1].T).T

    def boundary_density(i):
        return np.diagonal(A[i].T @ C @ D[i].T).copy()

    return pair_weight, boundary_density


def solve(measurements: List[Dict], root: Dict, chain: Chain,
          num_samples: int = 4000, seed: int = 42,
          uniform_span_prior: bool = True) -> Dict:
    """
    Exact marginal posteriors for every date and every boundary in `chain`.

    Returns the same shape services/gibbs.py's solve does, so callers
    do not need to know which produced it -- including `posterior_samples`,
    drawn from each exact marginal by inverse-CDF so that plotting and export
    code is unchanged.

    Those draws are from the MARGINALS, so unlike MCMC output they carry no
    joint correlation between dates. Nothing downstream uses the joint
    structure, but a future caller wanting, say, P(date A older than date B)
    must not read it off these.

    `uniform_span_prior` applies OxCal's correction of the same name (on by
    default there too); see _span_prior_correction for what it does and what
    it measurably costs.
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

    log_correction = _span_prior_correction(
        grid, len(chain.boundary_ids), uniform_span_prior,
        boundary_limits(element_bounds, chain.boundary_ids[0]),
        boundary_limits(element_bounds, chain.boundary_ids[-1]))
    correction = None
    if log_correction is not None:
        if len(chain.groups) == 1:
            # One group means the correction's two boundaries ARE this group's
            # two boundaries, so it folds straight into the transfer matrix and
            # costs nothing. Worth the special case: it is the app's default
            # shape, and the limits factor now applies to it where the older
            # (n-2) factor did not, so without this every single-phase model
            # would move to the O(M^3) path for a couple of percent.
            log_T[0] = log_T[0] + np.where(np.isfinite(log_correction),
                                           log_correction, -np.inf)
            pair_weight, boundary_density = _messages(chain, allowed, transfer)
        else:
            correction = np.where(np.isfinite(log_correction),
                                  np.exp(_rescaled(log_correction)), 0.0)
            correction = np.where(np.isfinite(correction), correction, 0.0)
            pair_weight, boundary_density = _messages_with_span_prior(
                chain, allowed, transfer, correction)
    else:
        pair_weight, boundary_density = _messages(chain, allowed, transfer)

    rng = np.random.default_rng(seed)
    results = {'measurements': {}, 'boundaries': {}}
    densities: List[np.ndarray] = []

    for index, date_ids in enumerate(chain.groups):
        for date_id in date_ids:
            # This date's own integral divided back out, leaving it free.
            with np.errstate(invalid='ignore'):
                weights = np.where(np.isfinite(log_T[index]),
                                   log_T[index] - _log_interval_integral(
                                       cumulative[date_id], likelihood[date_id], dt),
                                   -np.inf)
            G = np.exp(_rescaled(weights))
            G = np.where(np.isfinite(G), G, 0.0)
            W = G * pair_weight(index)
            # Sum over {older boundary >= t} x {younger boundary <= t}, which
            # a pair of cumulative sums leaves on the diagonal.
            quadrant = np.cumsum(W, axis=0)
            quadrant = np.cumsum(quadrant[:, ::-1], axis=1)[:, ::-1]
            density = likelihood[date_id] * np.diagonal(quadrant)
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

    Given the date landed at t, its probability of being an outlier is
    p*L_out(t)/L_mix(t) -- the responsibility of the outlier component there.
    Averaging that over the date's own posterior marginalises t out:

        P(outlier) = integral of posterior(t) * p*L_out(t)/L_mix(t) dt

    Both pieces are already computed, so detection costs two array products
    and no sampling.
    """
    weight = density.sum()
    if weight <= 0:
        return 0.0
    responsibility = np.where(mixture > 0, prior * broad / np.maximum(mixture, 1e-300), 0.0)
    return float((density / weight * responsibility).sum())


def _edge_mass(densities: List[np.ndarray], grid: np.ndarray) -> float:
    """
    The most probability any single distribution leaves in the outermost 1% of
    the grid. A boundary's tail is genuinely heavy, so a non-trivial value
    here means the window clipped something real and should be widened.
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

    Two reasons, both measured on the OxCal fixture. The internal step is
    chosen from the date range and the number of groups (1.0 to 7.7 years
    across the cases here), so reporting at it would make a published range
    depend on an implementation detail -- the same dates in a longer sequence
    would come back with different widths. And a modelled posterior on a
    wiggly curve is genuinely multi-peaked, so a fine grid picks out the tops
    of the peaks and skips the dips: at 1 year Wk-51189's 68.3% region came
    back as three fragments totalling 24 years, including two ranges split by
    a single excluded year, where OxCal reports 35 years. Rebinned to 5 years
    the same posterior gives 30, and the eight dates' 95.4% widths average
    51.2 against OxCal's 55.6.

    Five years is OxCal's own default reporting resolution and is about as
    fine as a calibrated date is meaningful, so it is the convention this
    reports in. The full-resolution density is still what gets plotted and
    what the point estimates come from.
    """
    step = REPORTING_STEP_YEARS
    lo = np.floor(grid[0] / step) * step
    hi = np.ceil(grid[-1] / step) * step + step
    edges = np.arange(lo, hi + step, step)
    binned, _ = np.histogram(grid, bins=edges, weights=mass)
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

    The solver's grid deliberately runs far wider than the dates, because the
    boundaries need room -- on the OxCal fixture it spans 2031 years while
    the posterior occupies about 100 of them. Handing the whole grid to the
    plot drew every modelled date as a spike in a mostly-empty axis, which is
    what this trim fixes. The MCMC path never had the problem because it
    histogrammed its own draws.
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
    An exact density turned into the summary shape the rest of the app
    expects. Point estimates come from the density itself rather than from
    draws, so they carry no Monte Carlo error at all; the draws exist only so
    downstream code that wants samples still works.
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

    hpd_68 = _reported_ranges(grid, mass, 0.683)
    hpd_95 = _reported_ranges(grid, mass, 0.954)

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
