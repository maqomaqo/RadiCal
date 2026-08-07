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
    if root is None:
        return None
    plan = plan_model(root, measurements)
    if not plan.span_groups:
        return None

    # Link the groups into one path: each group's younger boundary must be the
    # next group's older boundary, and no boundary may be reused elsewhere.
    by_older = {}
    for group in plan.span_groups:
        if group.older_boundary_id in by_older:
            return None                      # a boundary starting two groups
        by_older[group.older_boundary_id] = group
    younger = {g.younger_boundary_id for g in plan.span_groups}
    heads = [g for g in plan.span_groups if g.older_boundary_id not in younger]
    if len(heads) != 1:
        return None

    ordered_groups, seen = [], set()
    current = heads[0]
    while current is not None:
        if id(current) in seen:
            return None
        seen.add(id(current))
        ordered_groups.append(current)
        current = by_older.get(current.younger_boundary_id)
    if len(ordered_groups) != len(plan.span_groups):
        return None                          # more than one disjoint chain

    boundary_ids = ([g.older_boundary_id for g in ordered_groups]
                    + [ordered_groups[-1].younger_boundary_id])
    if len(set(boundary_ids)) != len(boundary_ids):
        return None
    if set(boundary_ids) != set(plan.boundary_names):
        return None                          # a boundary outside the chain

    # Every date must sit in exactly one group.
    placed: Dict[str, int] = {}
    for index, group in enumerate(ordered_groups):
        for date_id in group.date_ids:
            if date_id in placed:
                return None
            placed[date_id] = index
    if any(m['id'] not in placed for m in measurements):
        return None

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
            return None
        if max(older_ranks) >= min(younger_ranks):
            return None

    return Chain(boundary_ids, [list(g.date_ids) for g in ordered_groups],
                 dict(plan.boundary_names))


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


def solve(measurements: List[Dict], root: Dict, chain: Chain,
          num_samples: int = 4000, seed: int = 42) -> Dict:
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
    """
    element_bounds = inherited_bounds(root)
    by_id = {m['id']: m for m in measurements}
    grid = _build_grid(measurements, element_bounds, len(chain.groups))
    dt = float(grid[1] - grid[0])
    M = len(grid)

    # Per-date likelihood, normalised so every interval integral lands in
    # [0, 1] and the products below cannot underflow.
    likelihood, cumulative = {}, {}
    for m in measurements:
        lik = _date_likelihood(grid, m, _effective_date_bounds(m, element_bounds))
        total = lik.sum() * dt
        if total <= 0 or not np.isfinite(total):
            raise ValueError(
                f"sample '{m.get('sample_name', m['id'])}' has no probability inside "
                'its allowed calendar range'
            )
        likelihood[m['id']] = lik / total
        cumulative[m['id']] = np.cumsum(likelihood[m['id']]) * dt

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

    # Forward/backward over the boundaries.
    alpha = [np.where(allowed[chain.boundary_ids[0]], 1.0, 0.0)]
    for i in range(len(chain.groups)):
        alpha.append((alpha[i][None, :] * transfer(i)).sum(axis=1))
    beta = [None] * len(chain.boundary_ids)
    beta[-1] = np.where(allowed[chain.boundary_ids[-1]], 1.0, 0.0)
    for i in range(len(chain.groups) - 1, -1, -1):
        beta[i] = (transfer(i) * beta[i + 1][:, None]).sum(axis=0)

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
            W = G * alpha[index][None, :] * beta[index + 1][:, None]
            # Sum over {older boundary >= t} x {younger boundary <= t}, which
            # a pair of cumulative sums leaves on the diagonal.
            quadrant = np.cumsum(W, axis=0)
            quadrant = np.cumsum(quadrant[:, ::-1], axis=1)[:, ::-1]
            density = likelihood[date_id] * np.diagonal(quadrant)
            densities.append(density)
            results['measurements'][date_id] = _summarize(grid, density, rng, num_samples)

    for i, bid in enumerate(chain.boundary_ids):
        density = alpha[i] * beta[i]
        if density.sum() <= 0:
            continue
        densities.append(density)
        results['boundaries'][bid] = {
            'name': chain.boundary_names.get(bid, 'Boundary'),
            'posterior': _summarize(grid, density, rng, num_samples),
        }

    results['method'] = 'exact'
    results['grid'] = {'lower': float(grid[0]), 'upper': float(grid[-1]), 'step': dt}
    results['edge_mass'] = _edge_mass(densities, grid)
    return results


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


def _summarize(grid: np.ndarray, density: np.ndarray, rng, num_samples: int) -> Dict:
    """
    An exact density turned into the summary shape the rest of the app
    expects. Point estimates and HPD ranges come from the density itself
    rather than from draws, so they carry no Monte Carlo error at all; the
    draws exist only so downstream code that wants samples still works.
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

    hpd_68 = hpd_ranges_from_distribution(grid, mass, 0.683)
    hpd_95 = hpd_ranges_from_distribution(grid, mass, 0.954)
    # Half a step either side, so a single-cell range gets the width the cell
    # stands for rather than zero.
    for ranges in (hpd_68, hpd_95):
        for r in ranges:
            r['min'] -= dt / 2.0
            r['max'] += dt / 2.0

    draws = np.interp(rng.random(num_samples), cdf, grid)

    return {
        'posterior_mean': mean,
        'posterior_median': median,
        'posterior_std': float(np.sqrt(max(variance, 0.0))),
        'hpd_68': (min(r['min'] for r in hpd_68), max(r['max'] for r in hpd_68)),
        'hpd_95': (min(r['min'] for r in hpd_95), max(r['max'] for r in hpd_95)),
        'hpd_68_ranges': hpd_68,
        'hpd_95_ranges': hpd_95,
        'probability_distribution': [[float(a), float(p)] for a, p in zip(grid, density)],
        'posterior_samples': draws.tolist(),
    }
