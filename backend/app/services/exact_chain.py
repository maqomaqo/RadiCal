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
`observedlogp + potentiallogp`, and services/bayesian.py expresses the entire
model structure -- ordering constraints and uniform-scatter priors alike -- as
pm.Potential. So at beta = 0 the particles are drawn from unconstrained
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
`layout()` returns None for anything that is not a single chain, and the
caller falls back to MCMC. In particular a Sequence that orders two groups
with NO boundary between them is not a chain: its dates share one enclosing
boundary pair while an extra constraint orders them against each other, and
that constraint does not factorise along the chain. Adding an explicit
Boundary between such groups makes the model solvable here, and is better
practice anyway.
"""
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

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
# material formed some years BEFORE the context it was found in, never
# after. Exponential with this mean, following OxCal's Charcoal_Outlier.
CHARCOAL_TAU_YEARS = 200.0
CHARCOAL_KERNEL_SPAN_TAUS = 8.0
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

    "charcoal" -- a one-sided exponential: the dated material formed a gap
        g ~ Exp(tau) BEFORE the context, never after. This is old wood and
        reworked material, where the error has a known direction, and
        saying so is worth a lot. Measured on an injected residual date, it
        recovered the model's Start boundary to AD 1292 against the general
        model's 1201 (clean value 1339) and put the outlier probability at
        90% rather than 66%, because a one-sided model does not have to
        spend prior mass on the direction the material cannot be wrong in.
    """
    if model == 'charcoal':
        # One-sided, and the side matters. The MATERIAL is older than the
        # CONTEXT, so the context's calendar age is YOUNGER -- lower cal BP,
        # a lower grid index. The kernel therefore carries its mass at
        # negative lag; putting it at positive lag says the context predates
        # the wood found in it, which is residuality backwards and made a
        # residual date look MORE consistent rather than less.
        span = CHARCOAL_KERNEL_SPAN_TAUS * CHARCOAL_TAU_YEARS
        lags = np.arange(0.0, span + step, step)
        density = np.exp(-lags / CHARCOAL_TAU_YEARS)
        kernel = np.concatenate([density[::-1], np.zeros(len(lags) - 1)])
        return kernel / kernel.sum()

    lags = np.arange(-half_width, half_width + step, step)
    total = np.zeros_like(lags)
    for u in np.linspace(0.0, OUTLIER_LOG_SCALE_MAX, OUTLIER_SCALE_NODES):
        x = lags / (10.0 ** u)
        density = (1 + x * x / OUTLIER_DF) ** (-(OUTLIER_DF + 1) / 2.0)
        total += density / density.sum()
    return total / total.sum()


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
    full = np.convolve(inlier, kernel)
    offset = (len(kernel) - 1) // 2
    broad = full[offset:offset + len(inlier)]
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


def _interval_integral(cumulative: np.ndarray) -> np.ndarray:
    """F[y, o] = integral of one date's likelihood over [grid[y], grid[o]]."""
    padded = np.concatenate([[0.0], cumulative])
    return np.clip(padded[None, 1:] - padded[:-1, None], 0.0, None)


def _log_interval_integral(cumulative: np.ndarray) -> np.ndarray:
    F = _interval_integral(cumulative)
    with np.errstate(divide='ignore'):
        return np.where(F > 0, np.log(np.where(F > 0, F, 1.0)), -np.inf)


def _span_prior_correction(grid: np.ndarray, num_boundaries: int,
                           enabled: bool) -> Optional[np.ndarray]:
    """
    OxCal's UniformSpanPrior factor over the outermost pair, as a matrix
    indexed [youngest boundary, oldest boundary], or None when it would do
    nothing and the cheap chain can be used.

        1 / (b_first - b_last)^(n - 2)

    Placing n boundaries in order across a span leaves the n-2 interior ones
    uniform within it, so the induced prior on the overall span carries a
    factor span^(n-2) -- it favours long sequences purely because a longer
    one has more room to arrange its interior boundaries in. Dividing that
    out makes the span prior independent of how finely the sequence is
    subdivided, which is Nicholls and Jones (2001)'s recommendation and
    OxCal's default.

    Two things this deliberately does NOT do, both measured on the OxCal
    fixture rather than assumed:

    * There is no separate limit correction. OxCal also divides by
      ((ulim - llim) - span) to keep the span prior flat inside its finite
      computational window, but a real span (tens of years) is negligible
      against any sane window, so that factor is a constant: identical
      results at windows of 2,031 / 3,000 / 10,000 / 55,000 years.
    * At n = 2 the exponent is zero, so a single Phase between two
      Boundaries -- the app's default shape -- is untouched. Nothing to
      apply, and the O(M^2) chain is kept.

    What it costs, on the fixture's two-group model: the outermost boundary
    ranges improve markedly (Start 95.4% AD 1222-1390 -> 1294-1390 against
    OxCal's 1277-1394) while the dates move slightly the wrong way (3.4 ->
    4.7 years rms from OxCal, group separation 17.7 -> 6.5 against OxCal's
    13.2). It also couples the two ends of the chain, so the solve stops
    factorising and goes from O(M^2) to O(M^3) per group.
    """
    exponent = num_boundaries - 2
    if not enabled or exponent <= 0:
        return None
    outer = grid[None, :] - grid[:, None]          # [youngest, oldest]
    with np.errstate(divide='ignore', invalid='ignore'):
        corr = np.where(outer > 0, np.where(outer > 0, outer, 1.0) ** -exponent, 0.0)
    return np.where(np.isfinite(corr), corr, 0.0)


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

    Returns the same shape BayesianChronology.extract_results does, so callers
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

    # Per-date likelihood, normalised so every interval integral lands in
    # [0, 1] and the products below cannot underflow.
    likelihood, cumulative = {}, {}
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
            term = term + _log_interval_integral(cumulative[date_id])
        older_ok = allowed[chain.boundary_ids[index]]
        younger_ok = allowed[chain.boundary_ids[index + 1]]
        term = np.where(younger_ok[:, None] & older_ok[None, :], term, -np.inf)
        log_T.append(term)

    def transfer(i):
        """T_i, exponentiated on demand rather than kept alongside log_T."""
        out = np.exp(_rescaled(log_T[i]))
        return np.where(np.isfinite(out), out, 0.0)

    correction = _span_prior_correction(grid, len(chain.boundary_ids),
                                        uniform_span_prior)
    if correction is None:
        pair_weight, boundary_density = _messages(chain, allowed, transfer)
    else:
        pair_weight, boundary_density = _messages_with_span_prior(
            chain, allowed, transfer, correction)

    rng = np.random.default_rng(seed)
    results = {'measurements': {}, 'boundaries': {}}
    densities: List[np.ndarray] = []

    for index, date_ids in enumerate(chain.groups):
        for date_id in date_ids:
            # This date's own integral divided back out, leaving it free.
            with np.errstate(invalid='ignore'):
                weights = np.where(np.isfinite(log_T[index]),
                                   log_T[index] - _log_interval_integral(cumulative[date_id]),
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
    results['uniform_span_prior'] = correction is not None
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
