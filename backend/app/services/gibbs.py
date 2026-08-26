"""
OxCal's sampler: a Metropolis-Hastings / Gibbs hybrid over the model's dates
and boundaries, for models services/exact_chain.py cannot integrate.

No annealing path
-----------------
This targets the true posterior from the first sweep, which is what lets
constraints be HARD (an interval truncation) rather than a soft penalty, and
lets the (older - younger)^(-K) span density go unfloored. Soft containment
would break the cancellation that makes the posterior proper: squeezing a
group's boundaries together must shrink its dates' available volume as eps^K to
kill the eps^(-K) blow-up, and slack dates do not. Here the cancellation is
automatic, because a date's allowed interval IS the gap between its boundaries.

Discretisation, and staying in step with exact_chain
---------------------------------------------------
Both solvers integrate a date over its group's interval, and they have to do it
the SAME way or they answer different questions. exact_chain does it in closed
form (_interval_integral); here it is implicit in where a date is allowed to sit.
The rule in both is trapezoidal -- half weight on the two endpoint cells -- and in
this file that is Model.endpoint_weight, applied in draw_date, draw_boundary and
log_posterior alike.

It is not a refinement. Without it a date confined between boundaries k cells
apart may occupy k+1 grid CELLS while the (span)^(-K) factor charges only k*step
of span, so near-coincident configurations -- exactly the ones the span prior
already favours -- gain ((k+1)/k)^K, which is 2^K at the smallest span and 256
for a group of eight. The chain parks there and every modelled phase comes out
several times too narrow.

tests/test_gibbs.py's TestTheEndpointWeighting pins it from both directions: the
span against exact_chain's, and grid refinement, which no step-dependent error
survives.

The maths
---------
The posterior is

    p(t, b) proportional to  prod_dates L_d(t_d)
                           * prod_groups (b_older,g - b_younger,g)^(-K_g)
                           * c(t, b)

with c the indicator that every ordering constraint and every containment holds
(OxCal's `p(t)c(t)`, Buck et al. 1992). Two things make each full conditional
one-dimensional and exactly samplable:

  * A DATE's conditional is just its own likelihood over the interval its
    neighbours and its enclosing boundaries allow. The span factor does not
    appear: conditional on the boundaries, the dates are uniform between them,
    so (b_older - b_younger)^(-K) does not depend on where within them a date
    sits. Drawn by inverse CDF on the precomputed cumulative table, with the
    endpoint cells at half weight (see Discretisation above).
  * A BOUNDARY's conditional is the product of the span factors of the (at most
    two) groups it bounds, over its allowed interval. It carries no likelihood
    of its own -- only the half weight it imposes on any date sitting exactly on
    it. Evaluated in logs over the interval and drawn the same way.
  * An OUTLIER MODEL's offset scale is one more such conditional. OxCal shares
    that scale across every date using the model and lets the data constrain it,
    which does not factorise along exact_chain's message passing but is nothing
    to a sampler -- see Model._build_outlier_tables.

Where this deliberately differs from OxCal
------------------------------------------
OxCal draws each conditional by REJECTION, which is exact but unbounded in cost:
a conditional peaked relative to its interval -- a sharp calibration lobe inside
a wide phase, the normal case -- rejects many times per draw. This uses
inverse-CDF sampling on the cumulative table: the same target distribution, one
searchsorted per draw, and it vectorises, which is what makes the blocked update
below worth doing.

Blocked updates
---------------
Two dates that never appear on opposite sides of an ordering constraint are
conditionally independent given the boundaries, so they can be drawn at once. A
phase's dates are unordered among themselves, so in practice a whole phase
updates together: fewer Python-level steps and better mixing, since the block
moves as a group rather than each member being pinned by the ones already moved.
Blocks come from a greedy colouring of the "appears on the opposite side of a
constraint from" graph.

Group moves
-----------
Single-site Gibbs cannot move a tightly-constrained group between calibration
lobes: every member would have to cross at once, and each alone is blocked by
the others -- the classic sticky behaviour of constrained order statistics, and
what OxCal's whole-group moves exist for. `shift` translates a whole set of
parameters and accepts on the exact MH ratio from the full log posterior;
`move_sets` decides which sets are worth offering.

Validation status
-----------------
Measured against exact_chain on chain-shaped models, where the exact marginals
are ground truth, and on branching models against two INDEPENDENT branches that
exact_chain can solve one at a time.

The outlier model is held to two real OxCal runs, and is where the sampler and
the exact solver deliberately part company. On tests/fixtures/oxcal_outlier
sampling the shared scale lands within 0.0016 of OxCal's outlier probabilities
where marginalising it per date sits at 0.0166. On tests/fixtures/oxcal_charcoal
it recovers the scale OxCal inferred -- log10 1.82 against 1.79 -- from only the
U(0,3) prior, and the modelled dates land 6.3 years rms from OxCal against the
exact solver's 9.9.

See tests/test_gibbs.py and tests/test_charcoal_outlier.py, and _span_priors for
how the UniformSpanPrior correction is grouped so it reduces to exact_chain's on
a chain.
"""
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from app.services import intervals
from app.services.exact_chain import (
    DEFAULT_OUTLIER_MODEL,
    OUTLIER_KERNEL_HALF_WIDTH_YEARS,
    _build_grid,
    _outlier_kernel_at_scale,
    _summarize,
    convolve_on_grid,
    date_tables,
    log_interior_placements,
    log_limits_factor_of_span,
    pair_limits,
    scale_nodes,
)
from app.services.model_tree import inherited_bounds, plan_model
from app.services.stratigraphy import effective_bounds

# Sweeps discarded before anything is accumulated, as a fraction of the total.
# A sweep starts from a repaired-but-arbitrary state, so the early ones say more
# about where the chain began than about the posterior.
BURN_IN_FRACTION = 0.2
# Starting scale of a translation proposal, in years. Comparable to a
# measurement error, so a proposal can leave the current calibration lobe; it is
# then adapted to hit TARGET_ACCEPTANCE during burn-in.
GROUP_SHIFT_YEARS = 60.0
# Translation proposals per move set per sweep.
MOVES_PER_SWEEP = 2
# Acceptance rate the proposal scales are tuned towards during burn-in. The
# usual random-walk target; the exact value matters much less than not sitting
# at 1% (too wide) or 95% (too narrow to go anywhere).
TARGET_ACCEPTANCE = 0.3
# Proposals per move set between scale adjustments.
ADAPT_INTERVAL = 50
# Fraction of translation proposals drawn from a deliberately WIDE kernel
# instead of the adapted one, and its scale in years.
#
# Adaptation tunes the scale to ~30% acceptance, which on these targets is a
# step of a few years, so moving between calibration lobes 40 years apart
# happens only by diffusion and the lobe weights converge far too slowly.
#
# A mixture of two symmetric kernels is still symmetric, so no correction to the
# acceptance ratio is needed. The wide scale is set from the spacing of
# calibration wiggles rather than tuned: its job is to propose the jump a tuned
# kernel never will, so a low acceptance rate on those is expected.
WIDE_MOVE_FRACTION = 0.3
WIDE_MOVE_YEARS = 120.0
# Sweeps the run starts with, and the ceiling it will extend to when the
# convergence measure is not met. See run() for why the length is not fixed.
DEFAULT_SWEEPS = 20000
MAX_SWEEPS = 200000
# OxCal's own target for its convergence measure.
TARGET_CONVERGENCE = 0.95
# Rounds the accumulation is split into, which sets both the granularity of the
# first-half/second-half comparison and how often the run can stop.
ROUNDS = 8


class _State:
    """
    Current position of every parameter, as an index into the calendar grid.

    Indices rather than years throughout: an allowed interval is then a pair of
    slice bounds, a conditional draw is a searchsorted on a precomputed
    cumulative row, and there is no repeated float comparison to get the
    inclusive/exclusive convention wrong on.

    `scale` holds, per outlier model in use, the index of that model's current
    offset scale on its own node grid -- one shared value per model rather than
    one per date, which is the whole point of it being here (see
    Model._build_outlier_tables).
    """

    def __init__(self, index: Dict[str, int], scale: Optional[Dict[str, int]] = None):
        self.index = index
        self.scale = dict(scale or {})

    def copy(self) -> '_State':
        return _State(dict(self.index), self.scale)


class Model:
    """
    The posterior, flattened into what the sampler needs: per-date likelihood
    tables, per-group span terms, and for every parameter the hard bounds and
    the list of other parameters constraining it.

    Built from plan_model's output, which is already general -- it emits
    ordering and span groups for an arbitrary tree. Only exact_chain's layout()
    is restricted to chains, so nothing here needs the model to be one.
    """

    def __init__(self, measurements: List[Dict], root: Dict,
                 grid: Optional[np.ndarray] = None,
                 uniform_span_prior: bool = True):
        self.measurements = measurements
        self.plan = plan_model(root, measurements)
        self.element_bounds = inherited_bounds(root)
        self.uniform_span_prior = uniform_span_prior

        num_groups = max(1, len(self.plan.span_groups))
        # num_groups=1 deliberately: exact_chain coarsens the grid as the model
        # grows because it holds an M x M transfer matrix per group, and this
        # sampler holds nothing bigger than a row. Callers comparing the two
        # pass exact_chain's grid in explicitly.
        self.grid = _build_grid(measurements, self.element_bounds, 1) if grid is None else grid
        self.num_groups = num_groups
        self.dt = float(self.grid[1] - self.grid[0])
        self.M = len(self.grid)

        self.likelihood, _cumulative, self.outlier_parts = date_tables(
            self.grid, measurements, self.element_bounds)
        # Cumulative sums of the likelihood ROWS, for inverse-CDF draws over an
        # arbitrary sub-interval: mass in [a, b] is cdf[b] - cdf[a-1].
        self.log_likelihood = {
            did: np.log(np.maximum(lik, 1e-300)) for did, lik in self.likelihood.items()
        }
        self.cdf = {did: np.cumsum(lik) for did, lik in self.likelihood.items()}
        self._build_outlier_tables()

        self.date_ids = [m['id'] for m in measurements]
        self.boundary_ids = list(self.plan.boundary_names)
        # Every boundary is sampled, including the outermost ones exact_chain
        # marginalises: a Gibbs draw from a truncated power law is exact
        # however heavy the tail.
        self.params = self.date_ids + self.boundary_ids

        self._build_constraints()
        self._build_blocks()

    # -- the shared outlier scale ------------------------------------------

    def _build_outlier_tables(self):
        """
        For every flagged date, its likelihood at each candidate offset SCALE,
        plus one shared scale parameter per outlier model which the sampler
        draws from its own conditional like anything else.

        OxCal's Outlier_Model carries ONE scale for all the dates using it --
        U(0,4) for General, U(0,3) for Charcoal -- and lets the data constrain
        it. exact_chain instead marginalises that scale independently per date,
        which is a different model: each flagged date is then free to take its
        own offset with no reference to the others.

        It matters. On tests/fixtures/oxcal_charcoal, marginalising per date
        lets the three flagged dates slide until they sit on the well-dated
        ones, welding all eleven into one clump; a clump has three places to
        sit on a wiggly curve, so 20% of the posterior lands in a late lobe
        OxCal gives 1.7%.

        A shared scale does not factorise over dates, which is why exact_chain
        cannot do it -- its message passing needs each date to enter through
        its own likelihood alone.
        """
        # Only dates date_tables gave an outlier component appear here, so an
        # unused model -- or a zero prior -- costs nothing.
        self.outlier_models = sorted({
            m.get('outlier_model') or DEFAULT_OUTLIER_MODEL
            for m in self.measurements if m['id'] in self.outlier_parts
        })
        self.scale_grid: Dict[str, np.ndarray] = {
            model: scale_nodes(model) for model in self.outlier_models
        }
        # Which flagged dates belong to each model.
        self.scaled_dates: Dict[str, List[str]] = {m: [] for m in self.outlier_models}
        for m in self.measurements:
            if m['id'] in self.outlier_parts:
                self.scaled_dates[m.get('outlier_model') or DEFAULT_OUTLIER_MODEL].append(m['id'])

        # Per flagged date: the mixture likelihood at each scale, its
        # cumulative sum for inverse-CDF draws, and the outlier component's
        # responsibility, for reporting P(outlier) later.
        self.likelihood_at: Dict[str, np.ndarray] = {}
        self.log_likelihood_at: Dict[str, np.ndarray] = {}
        self.cdf_at: Dict[str, np.ndarray] = {}
        self.responsibility_at: Dict[str, np.ndarray] = {}
        half_width = max(OUTLIER_KERNEL_HALF_WIDTH_YEARS,
                         float(self.grid[-1] - self.grid[0]))

        for model in self.outlier_models:
            nodes = self.scale_grid[model]
            for date_id in self.scaled_dates[model]:
                inlier, _broad_marginal, prior = self.outlier_parts[date_id]
                mixtures = np.empty((len(nodes), self.M))
                responsibility = np.empty((len(nodes), self.M))
                for index, log_scale in enumerate(nodes):
                    kernel = _outlier_kernel_at_scale(self.dt, half_width, model, log_scale)
                    broad = convolve_on_grid(inlier, kernel)
                    mixture = (1.0 - prior) * inlier + prior * broad
                    mixtures[index] = mixture
                    responsibility[index] = np.where(
                        mixture > 0, prior * broad / np.maximum(mixture, 1e-300), 0.0)
                self.likelihood_at[date_id] = mixtures
                self.log_likelihood_at[date_id] = np.log(np.maximum(mixtures, 1e-300))
                self.cdf_at[date_id] = np.cumsum(mixtures, axis=1)
                self.responsibility_at[date_id] = responsibility

    def _scale_of(self, date_id: str, state: _State) -> Optional[int]:
        """The node this date's outlier model currently sits at, or None if the
        date carries no outlier model."""
        if date_id not in self.likelihood_at:
            return None
        for model, dates in self.scaled_dates.items():
            if date_id in dates:
                return state.scale.get(model, 0)
        return None

    def draw_scale(self, model: str, state: _State, u: float) -> int:
        """
        Exact draw of one outlier model's shared scale from its full
        conditional: the product of its dates' likelihoods at their current
        positions, over a uniform prior on the log scale. One-dimensional and
        tabulated, so a discrete inverse-CDF draw rather than an approximation.
        """
        dates = self.scaled_dates.get(model) or []
        nodes = self.scale_grid[model]
        if not dates:
            return state.scale.get(model, 0)
        log_weights = np.zeros(len(nodes))
        for date_id in dates:
            log_weights += self.log_likelihood_at[date_id][:, state.index[date_id]]
        log_weights -= log_weights.max()
        weights = np.exp(log_weights)
        total = weights.sum()
        if not np.isfinite(total) or total <= 0:
            return state.scale.get(model, 0)
        return int(np.searchsorted(np.cumsum(weights) / total, u, side='left')
                   .clip(0, len(nodes) - 1))

    # -- structure ---------------------------------------------------------

    def _own_window(self, param: str, is_date: bool) -> Tuple[int, int]:
        """Index range this parameter's own TPQ/TAQ limiters allow."""
        if is_date:
            m = next(x for x in self.measurements if x['id'] == param)
            el_taq, el_tpq = self.element_bounds.get(m.get('element_id'), (None, None))
            taq, tpq = effective_bounds(m, {'taq_age_bp': el_taq, 'tpq_age_bp': el_tpq})
        else:
            taq, tpq = self.element_bounds.get(param, (None, None))
        lo, hi = 0, self.M - 1
        if taq is not None:
            lo = max(lo, int(np.searchsorted(self.grid, taq, side='left')))
        if tpq is not None:
            hi = min(hi, int(np.searchsorted(self.grid, tpq, side='right')) - 1)
        return lo, hi

    def _build_constraints(self):
        """
        For each parameter: which others must be older than it (so it sits at or
        below their minimum) and which must be younger (so it sits at or above
        their maximum). Cal BP throughout, so "older" is a LARGER value.
        """
        self.below: Dict[str, List[str]] = {p: [] for p in self.params}
        self.above: Dict[str, List[str]] = {p: [] for p in self.params}

        known = set(self.params)

        def add(younger: str, older: str):
            if younger in known and older in known:
                self.below[younger].append(older)
                self.above[older].append(younger)

        for older_terms, younger_terms in self.plan.ordering:
            for _kind_o, older in older_terms:
                for _kind_y, younger in younger_terms:
                    add(younger, older)

        # Containment: a group's dates sit between its two boundaries. Hard,
        # which is what makes the span density proper without a floor (see the
        # module docstring).
        self.group_dates: List[Tuple[str, str, List[str]]] = []
        # The same relation from the date's side, so endpoint_weight does not
        # have to scan every group on every draw.
        self.date_groups: Dict[str, List[Tuple[str, str]]] = {}
        for group in self.plan.span_groups:
            dates = [d for d in group.date_ids if d in self.likelihood]
            self.group_dates.append(
                (group.older_boundary_id, group.younger_boundary_id, dates))
            for date_id in dates:
                add(date_id, group.older_boundary_id)
                add(group.younger_boundary_id, date_id)
                self.date_groups.setdefault(date_id, []).append(
                    (group.older_boundary_id, group.younger_boundary_id))

        self.window = {p: self._own_window(p, p in self.likelihood) for p in self.params}

        self.span_priors = self._span_priors() if self.uniform_span_prior else []
        self.limits_tables = self._limits_factor_tables()

    def _younger_reachable(self) -> Dict[str, set]:
        """
        For every parameter, everything the ordering forces to be YOUNGER than
        it, transitively.

        Transitively, because a sequence's ordering is emitted between adjacent
        children: the two boundaries either side of a phase relate only through
        that phase's dates, so direct edges alone would see a chain's
        boundaries as unrelated.
        """
        reachable: Dict[str, set] = {}

        def walk(param: str) -> set:
            if param in reachable:
                return reachable[param]
            reachable[param] = set()          # guards against revisiting
            found = set()
            for younger in self.above[param]:
                found.add(younger)
                found |= walk(younger)
            reachable[param] = found
            return found

        for param in self.params:
            walk(param)
        return reachable

    def _span_priors(self) -> List[Tuple[str, str, int]]:
        """
        The UniformSpanPrior corrections, as (oldest boundary, youngest
        boundary, exponent) -- one per group of boundaries sharing a span.

        Placing n ordered boundaries across a span leaves n-2 free INSIDE it, so
        the induced prior carries a factor span^(n-2) and depends on how finely
        the sequence was subdivided (Nicholls and Jones 2001). Dividing that out
        needs the count of free interior boundaries and the pair spanning them.

        Grouped by connected component of the boundary ordering rather than by
        Sequence element, because the exponent counts free interior parameters
        and an interior boundary is free within the span whether or not it is
        ordered against the other interior ones. Two parallel branches inside
        one outer pair therefore take a single correction; two INDEPENDENT
        branches take one each. On a chain this reduces exactly to
        exact_chain._span_prior_correction.

        A component with no unique outermost pair is skipped: there is no single
        span for the correction to be about.
        """
        # Two is enough: the interior-boundary factor needs three, but the
        # limits factor applies to any pair with a span between them.
        if len(self.boundary_ids) < 2:
            return []

        reachable = self._younger_reachable()
        younger_boundaries = {
            b: reachable[b] & set(self.boundary_ids) for b in self.boundary_ids
        }

        # Connected components of "is comparable to", by union-find.
        parent = {b: b for b in self.boundary_ids}

        def find(b):
            while parent[b] != b:
                parent[b] = parent[parent[b]]
                b = parent[b]
            return b

        for b in self.boundary_ids:
            for other in younger_boundaries[b]:
                parent[find(other)] = find(b)

        components: Dict[str, List[str]] = {}
        for b in self.boundary_ids:
            components.setdefault(find(b), []).append(b)

        priors: List[Tuple[str, str, int]] = []
        for members in components.values():
            exponent = len(members) - 2
            # Exponent 0 is not skipped: a component of two boundaries still
            # carries the limits factor, which does not depend on the interior
            # count. Fewer than two has no span at all.
            if exponent < 0:
                continue
            rest = set(members)
            oldest = [b for b in members if rest - {b} <= younger_boundaries[b]]
            youngest = [b for b in members
                        if all(b in younger_boundaries[o] for o in rest - {b})]
            if len(oldest) == 1 and len(youngest) == 1 and oldest[0] != youngest[0]:
                priors.append((oldest[0], youngest[0], exponent))
        return priors

    def _limits_factor_tables(self):
        """
        -log g of Bronk Ramsey 2008 A.31 per span prior, as a lookup over the
        span in CELLS -- one row per (oldest, youngest) pair in self.span_priors.

        Precomputed rather than evaluated per draw, since g depends only on the
        span and each boundary's limits, both fixed for the run. The limits are
        each boundary's own TPQ/TAQ, tightened by the extent of the dates it
        encloses -- see exact_chain.pair_limits and boundary_limits.

        The terms and their sign come from exact_chain rather than being written
        out again, since which is smallest decides which way the factor pushes
        the span.
        """
        tables = {}
        spans = np.arange(self.M, dtype=float) * self.dt
        reachable = self._younger_reachable()
        inliers = {d: (self.outlier_parts[d][0] if d in self.outlier_parts
                       else self.likelihood[d]) for d in self.date_ids}
        for oldest_id, youngest_id, _exponent in self.span_priors:
            # A date is enclosed when the older boundary is above it in the
            # ordering and the younger below -- not all of them, on a model of
            # two independent branches.
            enclosed = [d for d in self.date_ids
                        if d in reachable[oldest_id] and youngest_id in reachable[d]]
            oldest_limits, youngest_limits = pair_limits(
                self.element_bounds, oldest_id, youngest_id,
                self.grid, inliers, enclosed)
            factor = log_limits_factor_of_span(spans, oldest_limits, youngest_limits)
            if factor is not None:
                tables[(oldest_id, youngest_id)] = factor
        return tables

    def _build_blocks(self):
        """
        Dates grouped so no two in a block constrain each other, by greedy
        colouring of the conflict graph, highest degree first. A block's
        conditionals are independent given everything outside it, so all its
        dates can be drawn at once.
        """
        conflicts: Dict[str, set] = {d: set() for d in self.date_ids}
        for date_id in self.date_ids:
            for other in self.below[date_id] + self.above[date_id]:
                if other in conflicts:
                    conflicts[date_id].add(other)
                    conflicts[other].add(date_id)

        colour: Dict[str, int] = {}
        for date_id in sorted(self.date_ids, key=lambda d: -len(conflicts[d])):
            taken = {colour[n] for n in conflicts[date_id] if n in colour}
            colour[date_id] = next(c for c in range(len(self.date_ids) + 1) if c not in taken)

        blocks: Dict[int, List[str]] = {}
        for date_id, c in colour.items():
            blocks.setdefault(c, []).append(date_id)
        self.blocks = [blocks[c] for c in sorted(blocks)]

    # -- conditionals ------------------------------------------------------

    def allowed(self, param: str, state: _State) -> Tuple[int, int]:
        """The index range `param` may currently occupy, constraints included."""
        lo, hi = self.window[param]
        for older in self.below[param]:
            hi = min(hi, state.index[older])
        for younger in self.above[param]:
            lo = max(lo, state.index[younger])
        return lo, hi

    def endpoint_weight(self, date_id: str, state: _State) -> float:
        """
        The weight a date carries for sitting exactly ON one of its group's
        boundaries: a half rather than a whole.

        The sampler's half of the correction in exact_chain._interval_integral.
        A date confined between boundaries k cells apart can occupy k+1 grid
        CELLS while the (span)^(-K) factor charges only k*step of span, so
        unhalved the available volume exceeds the span charged for by
        ((k+1)/k)^K and the posterior piles onto near-coincident configurations.

        Halving the endpoint cells makes the sum over a date's positions equal
        the trapezoidal integral the exact solver uses, so the two agree by
        construction.
        """
        weight = 1.0
        index = state.index[date_id]
        for older_id, younger_id in self.date_groups.get(date_id, ()):
            if index == state.index[older_id] or index == state.index[younger_id]:
                weight *= 0.5
        return weight

    def draw_date(self, date_id: str, state: _State, u: float) -> int:
        """
        Exact draw from a date's full conditional: its own likelihood over the
        allowed interval, with the endpoint cells at half weight (see
        endpoint_weight), by inverse CDF.

        The half weights are applied by splitting the draw rather than
        rebuilding a cumulative table per sweep: the interior comes from the
        precomputed cumulative in one searchsorted and the endpoints are their
        own outcomes. Same distribution, still O(log M).
        """
        lo, hi = self.allowed(date_id, state)
        if hi <= lo:
            return max(lo, min(hi, state.index[date_id])) if hi >= 0 else state.index[date_id]
        node = self._scale_of(date_id, state)
        cdf = self.cdf[date_id] if node is None else self.cdf_at[date_id][node]
        density = (self.likelihood[date_id] if node is None
                   else self.likelihood_at[date_id][node])

        # Interior cells lo+1 .. hi-1 at full weight, the two endpoints at half.
        interior = float(cdf[hi - 1] - cdf[lo]) if hi - 1 >= lo + 1 else 0.0
        low_end = 0.5 * float(density[lo])
        high_end = 0.5 * float(density[hi])
        total = interior + low_end + high_end
        if total <= 0:
            # No likelihood anywhere it is allowed to be. Uniform keeps the
            # chain moving rather than freezing at an arbitrary index; the group
            # moves exist to get out of such a state.
            return int(lo + u * (hi - lo))

        target = u * total
        if target < low_end:
            return lo
        if target < low_end + interior:
            base = float(cdf[lo])
            return int(np.clip(np.searchsorted(cdf, base + (target - low_end)),
                               lo + 1, hi - 1))
        return hi

    def draw_boundary(self, boundary_id: str, state: _State, u: float) -> int:
        """
        Exact draw from a boundary's full conditional: the span factors of the
        groups it bounds, over its allowed interval. It has no likelihood of its
        own, so on a group with no dates this is simply uniform.
        """
        lo, hi = self.allowed(boundary_id, state)
        if hi <= lo:
            return state.index[boundary_id]
        candidates = self.grid[lo:hi + 1]
        log_dens = np.zeros(hi - lo + 1)

        for older_id, younger_id, dates in self.group_dates:
            K = len(dates)
            if K == 0:
                continue
            if older_id == boundary_id:
                span = candidates - self.grid[state.index[younger_id]]
            elif younger_id == boundary_id:
                span = self.grid[state.index[older_id]] - candidates
            else:
                continue
            with np.errstate(divide='ignore', invalid='ignore'):
                log_dens += np.where(span > 0, -K * np.log(np.where(span > 0, span, 1.0)), -np.inf)

            # A date sitting exactly ON the candidate is an endpoint there, so
            # it counts a half (endpoint_weight). Only coincidences with THIS
            # boundary vary with the candidate; the other boundary's cancel in
            # the normalisation below.
            on_candidate = np.zeros(hi - lo + 1)
            for date_id in dates:
                index = state.index[date_id]
                if lo <= index <= hi:
                    on_candidate[index - lo] += 1.0
            log_dens -= np.log(2.0) * on_candidate

        candidate_indices = np.arange(lo, hi + 1)
        for oldest_id, youngest_id, exponent in self.span_priors:
            if boundary_id == oldest_id:
                cells = candidate_indices - state.index[youngest_id]
            elif boundary_id == youngest_id:
                cells = state.index[oldest_id] - candidate_indices
            else:
                continue
            # Counted in CELLS and as a binomial rather than a power -- see
            # exact_chain._span_prior_correction for why a power is wrong on a
            # grid. This is the log of the factor itself, so both parts enter
            # with one sign: 1/(ways the interior boundaries fit) times 1/g.
            log_count = log_interior_placements(cells, exponent)
            factor = np.where(np.isfinite(log_count), -log_count, -np.inf)
            limits = self.limits_tables.get((oldest_id, youngest_id))
            if limits is not None:
                safe = np.clip(cells, 0, self.M - 1)
                factor = factor + np.where((cells >= 0) & (cells < self.M),
                                           limits[safe], -np.inf)
            log_dens = log_dens + factor

        finite = np.isfinite(log_dens)
        if not np.any(finite):
            return state.index[boundary_id]
        dens = np.where(finite, np.exp(log_dens - log_dens[finite].max()), 0.0)
        total = dens.sum()
        if total <= 0:
            return state.index[boundary_id]
        cdf = np.cumsum(dens) / total
        return int(lo + np.clip(np.searchsorted(cdf, u), 0, hi - lo))

    def log_posterior(self, state: _State) -> float:
        """
        The full log posterior, used for the MH acceptance ratio of the group
        moves. -inf when any hard constraint is violated, which is how a
        proposal that breaks the ordering is rejected.
        """
        total = 0.0
        for date_id in self.date_ids:
            node = self._scale_of(date_id, state)
            table = (self.log_likelihood[date_id] if node is None
                     else self.log_likelihood_at[date_id][node])
            total += float(table[state.index[date_id]])
            total += float(np.log(self.endpoint_weight(date_id, state)))

        for param in self.params:
            lo, hi = self.window[param]
            if not lo <= state.index[param] <= hi:
                return -np.inf
            for older in self.below[param]:
                if state.index[param] > state.index[older]:
                    return -np.inf

        for older_id, younger_id, dates in self.group_dates:
            K = len(dates)
            if K == 0:
                continue
            span = self.grid[state.index[older_id]] - self.grid[state.index[younger_id]]
            if span <= 0:
                return -np.inf
            total -= K * float(np.log(span))

        for oldest_id, youngest_id, exponent in self.span_priors:
            cells = state.index[oldest_id] - state.index[youngest_id]
            if not 0 <= cells < self.M:
                return -np.inf
            log_count = float(log_interior_placements(
                np.array([cells], dtype=float), exponent)[0])
            if not np.isfinite(log_count):
                return -np.inf
            total -= log_count
            row = self.limits_tables.get((oldest_id, youngest_id))
            if row is not None:
                if not np.isfinite(row[cells]):
                    return -np.inf
                total += float(row[cells])

        return total

    def initial_state(self) -> _State:
        """
        A state satisfying every hard constraint, built by walking the ordering
        from oldest to youngest and giving each parameter the youngest position
        it prefers that is still strictly older than everything below it.

        Feasibility cannot be reached by local repair. Raw radiocarbon ages
        routinely contradict the stratigraphy -- resolving that is much of why
        the model exists -- so the naive "every date at its likelihood mode"
        state is usually infeasible, and with HARD constraints every conditional
        then collapses to a single allowed index and the chain cannot move.

        Topological order fixes it by construction: each step leaves at least
        one grid cell of separation, so every span is positive and the starting
        log posterior finite.
        """
        # Longest chain of "must be older than" above each parameter, so every
        # constraint on it is already placed by the time it is.
        depth: Dict[str, int] = {}

        def compute_depth(param: str, seen: Tuple[str, ...] = ()) -> int:
            if param in depth:
                return depth[param]
            if param in seen:
                raise ValueError('the model structure is circular')
            value = 0
            for older in self.below[param]:
                value = max(value, 1 + compute_depth(older, seen + (param,)))
            depth[param] = value
            return value

        for param in self.params:
            compute_depth(param)

        preferred: Dict[str, int] = {}
        for date_id in self.date_ids:
            preferred[date_id] = int(np.argmax(self.likelihood[date_id]))
        for boundary_id in self.boundary_ids:
            # Near the dates it bounds, rather than out in the empty part of
            # the window.
            neighbours = [preferred[d] for older, younger, dates in self.group_dates
                          if boundary_id in (older, younger) for d in dates]
            preferred[boundary_id] = (int(np.mean(neighbours)) if neighbours
                                      else self.M // 2)

        index: Dict[str, int] = {}
        for param in sorted(self.params, key=lambda p: depth[p]):
            lo, hi = self.window[param]
            for older in self.below[param]:
                hi = min(hi, index[older] - 1)
            if hi < lo:
                raise ValueError(
                    'the model has no state satisfying all of its constraints: '
                    f"'{param}' must be older than {lo} and younger than {hi} "
                    'on the calendar grid at once'
                )
            index[param] = int(np.clip(preferred[param], lo, hi))

        # Mid-prior: with u uniform on its range there is no better guess, and
        # its conditional is drawn exactly on the first sweep.
        scale = {model: len(self.scale_grid[model]) // 2
                 for model in self.outlier_models}
        state = _State(index, scale)
        if not np.isfinite(self.log_posterior(state)):
            raise ValueError('could not find a feasible starting state for this model')
        return state

    def move_sets(self) -> List[List[str]]:
        """
        The parameter sets a translation proposal is offered over, coarsest
        first: the whole model, then each span group with its enclosing
        boundaries.

        The whole-model set is the move the sampler cannot do without. An
        absolute translation leaves every span, and so the whole phase prior,
        unchanged, so nothing resists it but the likelihoods -- and single-site
        Gibbs explores that mode one date at a time against the containment of
        the others. On the Nenthorn fixture, adding it took convergence at
        20,000 sweeps from 0.62 to over 0.95.

        A group's set includes its boundaries so it can move without being
        stopped by its own containment, and where a boundary is shared, the
        neighbouring group's dates come too -- otherwise the proposal breaks
        that group's containment and is rejected 99% of the time.
        """
        sets: List[List[str]] = [list(self.params)]
        for older_id, younger_id, dates in self.group_dates:
            if not dates:
                continue
            members = set(dates) | {older_id, younger_id}
            # Anything sharing a boundary with this group has to come along.
            for other_older, other_younger, other_dates in self.group_dates:
                if {other_older, other_younger} & {older_id, younger_id}:
                    members |= set(other_dates) | {other_older, other_younger}
            sets.append(sorted(members))

            # One FLAGGED date with the boundary that pens it in, per side.
            #
            # An ambiguous outlier has two plausible positions hundreds of years
            # apart -- with the group as a flagged date, or out at its own
            # likelihood as a sound one -- and the second needs the enclosing
            # boundary to come out past it. Neither can move alone, so moving
            # the pair is the only way across. On a residual date 400 years too
            # old this took the spread of its outlier probability from 0.09 to
            # 0.01 around the exact answer.
            #
            # Only for flagged dates: the broad outlier component is what
            # creates the second mode, and offering these sets for every date
            # made a 16-date model 2.8x slower for no gain.
            #
            # ONE boundary at a time, since translating the date with both drags
            # the far boundary past the group's other dates and breaks their
            # containment.
            if len(dates) > 1:
                for date_id in dates:
                    if date_id not in self.outlier_parts:
                        continue
                    sets.append(sorted({date_id, older_id}))
                    sets.append(sorted({date_id, younger_id}))
        return sets

    def shift(self, state: _State, rng, params: List[str], scale: float,
              current_lp: Optional[float] = None
              ) -> Tuple[_State, float, bool, bool]:
        """
        Propose translating `params` by a common offset and accept on the exact
        MH ratio. The proposal is a symmetric lattice random walk, so the
        Hastings correction is one and the ratio is the posterior ratio alone.

        Returns (state, its log posterior, whether the move was taken, whether
        the WIDE kernel was used). The log posterior comes back so a caller
        sweeping several moves need not recompute it; the wide flag so scale
        adaptation can ignore proposals that are expected to be rejected often
        and would drag the local scale down.
        """
        if current_lp is None:
            current_lp = self.log_posterior(state)
        # Two-component symmetric proposal: the adapted local kernel most of
        # the time, a wide one otherwise so lobe-to-lobe jumps get proposed at
        # all (see WIDE_MOVE_FRACTION).
        wide = rng.random() < WIDE_MOVE_FRACTION
        if wide:
            scale = WIDE_MOVE_YEARS / self.dt
        steps = int(round(rng.normal(0.0, max(scale, 1e-9))))
        if steps == 0:
            return state, current_lp, False, wide

        proposal = state.copy()
        for param in params:
            moved = state.index[param] + steps
            if not 0 <= moved < self.M:
                return state, current_lp, False, wide
            proposal.index[param] = moved

        proposed_lp = self.log_posterior(proposal)
        if proposed_lp == -np.inf:
            return state, current_lp, False, wide
        if proposed_lp >= current_lp or rng.random() < np.exp(proposed_lp - current_lp):
            return proposal, proposed_lp, True, wide
        return state, current_lp, False, wide


class Cancelled(Exception):
    """Raised out of run() when on_progress asked it to stop."""


def run(model: Model, sweeps: int = DEFAULT_SWEEPS, seed: int = 42,
        burn_in: Optional[int] = None,
        target_convergence: float = TARGET_CONVERGENCE,
        max_sweeps: Optional[int] = None,
        collect_draws: int = 0,
        on_progress: Optional[Callable[[Dict], bool]] = None) -> Dict:
    """
    Sweep the sampler and accumulate each parameter's marginal as a histogram
    over the calendar grid, extending the run until it has converged.

    Histograms rather than stored draw arrays: the grid is the natural
    resolution of the whole problem, a count per cell is all _summarize needs,
    and it makes the memory cost independent of how long the chain runs.

    The run EXTENDS rather than being a fixed length because how many sweeps a
    model needs does not follow from its size. Measured against exact answers, 9
    dates in 3 phases reached 0.65 years at 20,000 sweeps while 30 dates in 6
    phases was still 32 years out and needed 100,000 -- and 50 dates in 10
    phases did BETTER than the 30-date model at the same length. It depends on
    how tightly the structure couples the parameters, so a fixed default would
    be wasteful on small models or quietly wrong on awkward ones.

    Stopping when the convergence measure is met is data-dependent and so not
    perfectly unbiased in principle, but it is what OxCal does and the effect is
    far below the Monte Carlo error it controls. `max_sweeps` bounds the work
    regardless, and the achieved convergence is always reported.

    `on_progress`, when given, is called with
    {'phase', 'sweeps', 'max_sweeps', 'convergence'} after burn-in and after
    every round, and RETURNING FALSE STOPS THE RUN by raising Cancelled. Between
    rounds is the honest place to stop: the histograms are discarded whole, so
    nothing half-written survives.

    `convergence` there is the WORST parameter's, the one the stopping rule
    uses; an average would tick along while the parameter holding the run up sat
    still. Progress against `max_sweeps` is an upper bound rather than a
    prediction, which is why the achieved convergence is reported alongside.

    `collect_draws`, when non-zero, also keeps roughly that many WHOLE STATES --
    every parameter's position at the same sweep -- thinned evenly through the
    run and returned as `draws`. The histograms are marginals and a duration is
    not (see services/intervals.py), so "how long did this phase last" can only
    be read off joint states. They fold exactly as the histograms do, so memory
    stays flat however long the run extends. Unlike exact_chain's, these draws
    are an MCMC chain: consecutive ones are correlated.

    Returns {'histograms', 'sweeps', 'sweeps_kept', 'convergence', 'acceptance',
    ...} -- convergence being OxCal's own diagnostic, per parameter.
    """
    rng = np.random.default_rng(seed)
    state = model.initial_state()
    if burn_in is None:
        burn_in = int(sweeps * BURN_IN_FRACTION)
    if max_sweeps is None:
        max_sweeps = max(sweeps, MAX_SWEEPS)

    move_sets = model.move_sets()
    # One proposal scale per move set, in grid steps, adapted during burn-in
    # only: after that the kernel is fixed, so the accumulated chain is a valid
    # MCMC run rather than one whose transition rule kept changing.
    scales = [max(1.0, GROUP_SHIFT_YEARS / model.dt) for _ in move_sets]
    proposed = [0] * len(move_sets)
    accepted = [0] * len(move_sets)
    since_adapt = [0] * len(move_sets)
    took_since_adapt = [0] * len(move_sets)
    swept = 0

    # Accumulated in rounds rather than one total, so the run can be EXTENDED
    # and the first-half/second-half convergence comparison recomputed over
    # whatever length it ended up being. Halving pairs of rounds when there are
    # too many keeps the memory flat however long it runs.
    rounds: List[Dict[str, np.ndarray]] = []
    # How often each shared scale node was visited, which IS its posterior --
    # directly comparable to the U(0,n) posterior OxCal exports. Not split into
    # rounds: it plays no part in the convergence measure, whose job is the
    # calendar parameters.
    scale_counts = {m: np.zeros(len(model.scale_grid[m])) for m in model.outlier_models}
    # P(outlier) accumulated as a running mean of the outlier component's
    # responsibility. With the scale sampled, that responsibility depends on it,
    # so it has to be read while the state is known rather than derived from the
    # reported density afterwards.
    outlier_total = {d: 0.0 for d in model.likelihood_at}
    accumulated = 0
    # Whole states, one array per round so they fold alongside the histograms.
    # Indices rather than years: that is what the state already holds, and an
    # int32 per parameter per draw keeps this a few megabytes at the sizes
    # involved.
    per_round_draws = max(1, collect_draws // ROUNDS) if collect_draws else 0
    round_draws: List[np.ndarray] = []

    def new_round() -> Dict[str, np.ndarray]:
        return {p: np.zeros(model.M) for p in model.params}

    def sweep_once():
        # `state` is rebound by the translation moves below, not just mutated.
        nonlocal swept, state
        # Dates, one blocked update per colour class.
        for block in model.blocks:
            draws = rng.random(len(block))
            for date_id, u in zip(block, draws):
                state.index[date_id] = model.draw_date(date_id, state, float(u))

        # Boundaries, singly: adjacent ones constrain each other directly, so
        # they are never in the same conditionally-independent block.
        for boundary_id in model.boundary_ids:
            state.index[boundary_id] = model.draw_boundary(
                boundary_id, state, float(rng.random()))

        # Each outlier model's shared offset scale, from its own exact
        # conditional -- the parameter exact_chain cannot have.
        for outlier_model in model.outlier_models:
            state.scale[outlier_model] = model.draw_scale(
                outlier_model, state, float(rng.random()))

        # Translation moves. The Gibbs sweep above cannot move a correlated
        # block as a unit -- see Model.move_sets.
        current_lp = None
        for index, params in enumerate(move_sets):
            for _ in range(MOVES_PER_SWEEP):
                state, current_lp, took, wide = model.shift(
                    state, rng, params, scales[index], current_lp)
                proposed[index] += 1
                accepted[index] += int(took)
                if not wide:
                    since_adapt[index] += 1
                    took_since_adapt[index] += int(took)

            if swept < burn_in and since_adapt[index] >= ADAPT_INTERVAL:
                rate = took_since_adapt[index] / since_adapt[index]
                # Standard random-walk tuning, floored at one grid step since
                # a proposal rounding to zero can never move anything.
                scales[index] = max(1.0, scales[index] * (
                    1.3 if rate > TARGET_ACCEPTANCE else 0.75))
                since_adapt[index] = took_since_adapt[index] = 0
        swept += 1

    def report(phase: str, convergence: float) -> None:
        if on_progress is None:
            return
        if not on_progress({'phase': phase, 'sweeps': swept,
                            'max_sweeps': max_sweeps, 'convergence': convergence}):
            raise Cancelled()

    round_length = max(1, (sweeps - burn_in) // ROUNDS)
    # Reported in chunks rather than as one silent block: on a slow model
    # burn-in is several seconds during which the bar would sit at zero and a
    # cancel would be ignored.
    burn_chunk = max(1, burn_in // 10)
    for done in range(burn_in):
        sweep_once()
        if (done + 1) % burn_chunk == 0:
            report('burn_in', 0.0)

    while True:
        current = new_round()
        # Recomputed each time because round_length doubles whenever rounds
        # are folded, so a round keeps about per_round_draws states however
        # long the run gets.
        stride = max(1, round_length // per_round_draws) if per_round_draws else 0
        kept_states: List[List[int]] = []
        for step in range(round_length):
            sweep_once()
            for param in model.params:
                current[param][state.index[param]] += 1.0
            for outlier_model in model.outlier_models:
                scale_counts[outlier_model][state.scale[outlier_model]] += 1.0
            for date_id in outlier_total:
                node = model._scale_of(date_id, state)
                outlier_total[date_id] += float(
                    model.responsibility_at[date_id][node][state.index[date_id]])
            if stride and step % stride == 0:
                kept_states.append([state.index[p] for p in model.params])
            accumulated += 1
        rounds.append(current)
        if per_round_draws:
            round_draws.append(np.array(kept_states, dtype=np.int32).reshape(
                len(kept_states), len(model.params)))

        if len(rounds) > 2 * ROUNDS:
            # Fold adjacent pairs: same accumulated distribution, half the
            # bookkeeping, and the halves stay comparable.
            merged = []
            for i in range(0, len(rounds) - 1, 2):
                for param in model.params:
                    rounds[i][param] += rounds[i + 1][param]
                merged.append(rounds[i])
            if len(rounds) % 2:
                merged.append(rounds[-1])
            rounds = merged
            if per_round_draws:
                # The same fold, thinned back down so a folded round holds no
                # more states than an unfolded one.
                folded = []
                for i in range(0, len(round_draws) - 1, 2):
                    folded.append(np.concatenate(
                        [round_draws[i], round_draws[i + 1]])[::2])
                if len(round_draws) % 2:
                    folded.append(round_draws[-1])
                round_draws = folded
            round_length *= 2

        kept_rounds = _after_burn_in(rounds)
        convergence = _convergence(kept_rounds, model.params)
        worst = min(convergence.values()) if convergence else 0.0
        report('sampling', worst)
        if len(kept_rounds) < 2:
            continue
        if worst >= target_convergence or swept >= max_sweeps:
            break

    kept_rounds = _after_burn_in(rounds)
    counts = {p: sum(r[p] for r in kept_rounds) for p in model.params}
    # The same rounds the histograms come from, so the two views cannot
    # describe different stretches of the run.
    kept_draws = round_draws[_burn_in_start(len(rounds)):] if round_draws else []
    return {
        'histograms': counts,
        'draws': (np.concatenate([d for d in kept_draws if len(d)])
                  if any(len(d) for d in kept_draws) else None),
        'draw_params': list(model.params),
        'scale_histograms': scale_counts,
        'outlier_probability': {d: (v / accumulated if accumulated else 0.0)
                                for d, v in outlier_total.items()},
        'sweeps': swept,
        'sweeps_kept': int(sum(counts[model.params[0]])) if model.params else 0,
        'proposed_moves': sum(proposed),
        'accepted_moves': sum(accepted),
        # Per move set, so a set that never gets accepted is visible rather
        # than averaged away.
        'acceptance': [a / p if p else 0.0 for a, p in zip(accepted, proposed)],
        'scales': scales,
        'convergence': convergence,
    }


def _after_burn_in(
    rounds: List[Dict[str, np.ndarray]],
) -> List[Dict[str, np.ndarray]]:
    """
    The rounds to report from, dropping the earliest as burn-in.

    A FRACTION OF ROUNDS rather than a fixed number of sweeps, so burn-in
    scales with however long the run turned out to be. The sweeps up front are
    fixed before the length is known and exist to tune the proposal scales; on
    a slowly-mixing model they are nowhere near enough -- the 30-date fixture
    came out 6.17 years from the exact answer after 214,000 sweeps, worse than
    1.46 at a fixed 100,000.
    """
    return rounds[_burn_in_start(len(rounds)):]


def _burn_in_start(count: int) -> int:
    """
    Index of the first round to report from. Split out of _after_burn_in so the
    joint draws are cut at exactly the same place; two burn-in rules would let
    the marginals and the durations describe different stretches of one run.
    """
    return 0 if count < 3 else int(count * BURN_IN_FRACTION)


def _convergence(rounds: List[Dict[str, np.ndarray]],
                 params: List[str]) -> Dict[str, float]:
    """
    OxCal's convergence measure per parameter: the overlap integral between the
    distribution accumulated over the first half of the run and the second half.
    1.0 is perfect agreement, and OxCal's usual target is 0.95.

    OxCal's version compares each pass against the running total; halves are
    the same idea and are what a run assembled from rounds gives directly.
    """
    if len(rounds) < 2:
        return {p: 0.0 for p in params}
    split = len(rounds) // 2
    out = {}
    for param in params:
        early = sum(r[param] for r in rounds[:split])
        late = sum(r[param] for r in rounds[split:])
        a, b = early.sum(), late.sum()
        out[param] = float(np.minimum(early / a, late / b).sum()) if a > 0 and b > 0 else 0.0
    return out


def solve(measurements: List[Dict], root: Dict, sweeps: int = DEFAULT_SWEEPS,
          num_samples: int = 4000, seed: int = 42,
          uniform_span_prior: bool = True,
          grid: Optional[np.ndarray] = None,
          target_convergence: float = TARGET_CONVERGENCE,
          max_sweeps: Optional[int] = None,
          estimate_durations: bool = False,
          interval_draws: int = intervals.DEFAULT_DRAWS,
          on_progress: Optional[Callable[[Dict], bool]] = None) -> Dict:
    """
    Posterior summaries for every date and boundary, in the same shape
    exact_chain.solve returns -- so a caller does not need to know which
    produced it.

    `sweeps` is where the run starts, not where it ends: it extends until the
    convergence measure reaches `target_convergence` or `max_sweeps` (see run).
    Both the achieved convergence and the length are reported, so a run that ran
    out of budget is visible rather than silently under-converged.

    `grid` is accepted so a caller can force exact_chain's own grid and compare
    the two solvers without a resolution difference confounding the answer.

    `estimate_durations` adds `intervals` as exact_chain.solve's flag does, from
    `interval_draws` whole states kept jointly through the run. Off by default
    for the same reasons, plus one here: those states are an MCMC chain rather
    than independent draws, so a duration read off them converges more slowly
    than the marginals beside it and the run is not extended on its account.
    """
    model = Model(measurements, root, grid=grid,
                  uniform_span_prior=uniform_span_prior)
    chain = run(model, sweeps=sweeps, seed=seed,
                target_convergence=target_convergence, max_sweeps=max_sweeps,
                collect_draws=interval_draws if estimate_durations else 0,
                on_progress=on_progress)
    rng = np.random.default_rng(seed)

    results: Dict = {'measurements': {}, 'boundaries': {}}
    for date_id in model.date_ids:
        density = chain['histograms'][date_id]
        summary = _summarize(model.grid, density, rng, num_samples)
        if date_id in model.outlier_parts:
            # Taken from the run rather than recomputed: the responsibility
            # depends on the shared scale, so it must be read while that is
            # known.
            summary['outlier_probability'] = chain['outlier_probability'][date_id]
        results['measurements'][date_id] = summary

    for boundary_id in model.boundary_ids:
        density = chain['histograms'][boundary_id]
        if density.sum() <= 0:
            continue
        results['boundaries'][boundary_id] = {
            'name': model.plan.boundary_names.get(boundary_id, 'Boundary'),
            'posterior': _summarize(model.grid, density, rng, num_samples),
        }

    results['method'] = 'gibbs'
    results['uniform_span_prior'] = bool(model.span_priors)
    results['sweeps'] = chain['sweeps']
    # The WORST parameter's, not the average: one badly-mixed boundary is
    # exactly what a mean would hide.
    results['convergence'] = (min(chain['convergence'].values())
                              if chain['convergence'] else 0.0)
    results['converged'] = results['convergence'] >= target_convergence
    results['acceptance'] = chain['acceptance']
    # Each outlier model's shared offset scale, in the log10(years) OxCal
    # reports its U(0,n) posterior in, so the two compare directly.
    results['outlier_scale'] = {}
    for outlier_model, visits in chain['scale_histograms'].items():
        total = visits.sum()
        if total <= 0:
            continue
        nodes = model.scale_grid[outlier_model]
        weights = visits / total
        mean = float((nodes * weights).sum())
        cdf = np.cumsum(weights)
        results['outlier_scale'][outlier_model] = {
            'log10_mean': mean,
            'log10_hpd_68': (float(np.interp(0.16, cdf, nodes)),
                             float(np.interp(0.84, cdf, nodes))),
            'years_mean': float(10.0 ** mean),
        }
    results['grid'] = {'lower': float(model.grid[0]), 'upper': float(model.grid[-1]),
                       'step': model.dt}
    if estimate_durations and chain['draws'] is not None:
        joint = {param: model.grid[chain['draws'][:, column]]
                 for column, param in enumerate(chain['draw_params'])}
        results['intervals'] = intervals.report(
            measurements=measurements, root=root, draws=joint, independent=False)
    return results
