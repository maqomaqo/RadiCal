"""
OxCal's sampler: a Metropolis-Hastings / Gibbs hybrid over the model's dates
and boundaries, for models services/exact_chain.py cannot integrate.

PROTOTYPE. Not wired into the API yet -- see the validation notes at the end of
this docstring for what has and has not been measured.

Why not just keep SMC
---------------------
The SMC fallback fails for a structural reason, not a tuning one. PyMC tempers
`observedlogp + potentiallogp` from beta = 0, and services/bayesian.py expresses
the whole model structure as pm.Potential, so at beta = 0 the particles carry no
ordering and no phase prior at all. Each date settles into one of its own
calibration lobes independently, and configurations putting different groups in
different lobes vastly outnumber the correlated one, so by the time beta reaches
1 the correct arrangement has too few particles left to be weighted properly.
Measured against OxCal 4.4 on the Nenthorn fixture it separated two ordered
groups by 57 years where the answer is 19 and OxCal says 13 -- at r_hat 1.01,
all four chains agreeing on the same wrong answer.

MH/Gibbs has no annealing path. It targets the true posterior from the first
sweep, so that failure mode does not exist here. Two consequences beyond
accuracy, both of which delete a hack that only existed to survive tempering:

  * constraints are HARD (an interval truncation), not the softplus penalty
    bayesian.py needs because -inf * beta is -inf for every beta > 0.
  * the (older - younger)^(-K) span density needs no floor. bayesian.py's
    _MIN_GROUP_SPAN_YEARS exists because SOFT containment breaks the exact
    cancellation that makes the posterior proper: squeezing a group's boundaries
    together must shrink its dates' available volume as eps^K, killing the
    eps^(-K) blow-up, and a soft penalty leaves the dates slack so it does not.
    Hard containment restores it -- here the cancellation happens by
    construction, because a date's allowed interval IS the gap between its
    boundaries.

The maths
---------
The posterior is

    p(t, b) proportional to  prod_dates L_d(t_d)
                           * prod_groups (b_older,g - b_younger,g)^(-K_g)
                           * c(t, b)

with c the indicator that every ordering constraint and every containment holds
(OxCal's `p(t)c(t)`, Buck et al. 1992). Two things make each full conditional
one-dimensional and exactly samplable:

  * A DATE's conditional is just its own likelihood truncated to the interval
    its neighbours and its enclosing boundaries allow. The span factor does not
    appear: conditional on the boundaries, the dates are uniform between them,
    so (b_older - b_younger)^(-K) does not depend on where within them a date
    sits. Drawn by inverse CDF on the precomputed cumulative table.
  * A BOUNDARY's conditional is the product of the span factors of the (at most
    two) groups it bounds, over its allowed interval. It carries no likelihood
    of its own. Evaluated in logs over the interval and drawn the same way.

Where this deliberately differs from OxCal
------------------------------------------
OxCal draws each conditional by REJECTION: pick t in the interval, pick r in
[0, max p(t)], accept if p(t) > r, repeat otherwise. That is exact but its cost
is unbounded -- a conditional that is peaked relative to its interval (a sharp
calibration lobe inside a wide phase, the normal case) rejects many times per
draw. This uses inverse-CDF sampling on the cumulative table instead: the same
target distribution, one searchsorted per draw, no rejection loop. It also
vectorises, which is what makes the blocked update below worth doing.

Blocked updates
---------------
Two dates that never appear on opposite sides of an ordering constraint are
conditionally independent given the boundaries, so they can be drawn
simultaneously rather than swept one at a time. A phase's dates are unordered
with respect to each other, so in practice a whole phase updates at once: fewer
Python-level steps AND better mixing than single-site sweeping, since the block
moves as a group rather than each member being pinned by the ones already moved.
Blocks come from a greedy colouring of the "appears on the opposite side of a
constraint from" graph.

Group moves
-----------
Single-site Gibbs cannot move a tightly-constrained group between calibration
lobes: every member would have to cross at once, and each one alone is blocked
by the others. This is the classic sticky behaviour of constrained order
statistics, and it is what OxCal's "changes to the duration and timing of whole
groups" exist for. _group_shift proposes translating a whole span group -- its
dates and its boundaries together -- and accepts on the exact MH ratio computed
from the full log posterior.

Validation status
-----------------
Measured against exact_chain on chain-shaped models, where the exact marginals
are ground truth, and on branching models against the one oracle available for
those: two INDEPENDENT branches, each separately a chain, so exact_chain can
solve them one at a time while the sampler has to solve the combined model it
declines. The outlier model is held to the same OxCal 4.4 run the exact solver
is (tests/fixtures/oxcal_outlier), where the two agree to 0.0001 on every date's
outlier probability against the 0.0166 both sit from OxCal -- so the residual
difference from OxCal belongs to this app's outlier implementation, not to the
choice of solver.

See tests/test_gibbs.py for all of it, and _span_priors for how the
UniformSpanPrior correction is grouped so that it reduces exactly to
exact_chain's on a chain and to the per-branch corrections on independent
branches.
"""
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.services.exact_chain import (
    _build_grid,
    _outlier_probability,
    _summarize,
    date_tables,
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
# Without this the sampler is correct but nearly useless on the case that
# matters. Adaptation tunes the scale to ~30% acceptance, which on these targets
# means a step of a few years -- so moving between two calibration lobes 40 years
# apart happens only by diffusion, and the LOBE WEIGHTS (exactly what a wiggly
# curve makes uncertain, and exactly what SMC gets wrong) converge far too
# slowly. Measured on the Nenthorn fixture at 20,000 sweeps across five seeds,
# posterior means sat 1.5-32.6 years from the exact answer.
#
# A mixture of two symmetric kernels is still symmetric, so the acceptance ratio
# is unchanged and no correction is needed. The wide scale is set from the
# spacing of calibration wiggles rather than tuned: its job is to propose the
# jump a tuned kernel never will, and a low acceptance rate on those proposals
# is the expected outcome, not a fault to adapt away.
WIDE_MOVE_FRACTION = 0.3
WIDE_MOVE_YEARS = 120.0


class _State:
    """
    Current position of every parameter, as an index into the calendar grid.

    Indices rather than years throughout: an allowed interval is then a pair of
    slice bounds, a conditional draw is a searchsorted on a precomputed
    cumulative row, and there is no repeated float comparison to get the
    inclusive/exclusive convention wrong on.
    """

    def __init__(self, index: Dict[str, int]):
        self.index = index

    def copy(self) -> '_State':
        return _State(dict(self.index))


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

        self.date_ids = [m['id'] for m in measurements]
        self.boundary_ids = list(self.plan.boundary_names)
        # Every boundary is sampled. exact_chain marginalises the outermost
        # ones and bayesian.py has to, because an outermost boundary's tail is
        # heavy and SMC collapses on it -- but a Gibbs draw from a truncated
        # power law is exact however heavy the tail, so there is nothing to
        # avoid here.
        self.params = self.date_ids + self.boundary_ids

        self._build_constraints()
        self._build_blocks()

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

        # Containment: a group's dates sit between its two boundaries. This is
        # the hard version of what bayesian.py has to make soft, and it is what
        # makes the span density proper without a floor (see module docstring).
        self.group_dates: List[Tuple[str, str, List[str]]] = []
        for group in self.plan.span_groups:
            dates = [d for d in group.date_ids if d in self.likelihood]
            self.group_dates.append(
                (group.older_boundary_id, group.younger_boundary_id, dates))
            for date_id in dates:
                add(date_id, group.older_boundary_id)
                add(group.younger_boundary_id, date_id)

        self.window = {p: self._own_window(p, p in self.likelihood) for p in self.params}

        self.span_priors = self._span_priors() if self.uniform_span_prior else []

    def _younger_reachable(self) -> Dict[str, set]:
        """
        For every parameter, everything the ordering forces to be YOUNGER than
        it, transitively.

        Transitively matters: a sequence's ordering is emitted between adjacent
        children, so the two boundaries either side of a phase are never
        directly related to each other -- only through that phase's dates. A
        direct-edges-only view would see a chain's boundaries as unrelated.
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
        The UniformSpanPrior corrections to apply, as (oldest boundary, youngest
        boundary, exponent) -- one per group of boundaries that share a span.

        OxCal applies the correction per Sequence, not once per model. The
        derivation says why, and says what "per Sequence" has to mean here:
        placing n ordered boundaries across a span leaves n-2 of them free
        INSIDE it, so the induced prior on the span carries a factor
        span^(n-2), which is what makes it depend on how finely the sequence was
        subdivided (Nicholls and Jones 2001). Dividing it out needs the count of
        free interior boundaries and the pair spanning them.

        So the grouping is by connected component of the boundary ordering, with
        the component's own outermost pair and its own count -- not by Sequence
        element in the tree. That is the faithful reading rather than a
        convenient one: the exponent counts free interior parameters, and an
        interior boundary is free within the span whether or not it is ordered
        against the other interior ones. Two parallel branches inside one outer
        boundary pair therefore take a single correction over that outer pair,
        while two INDEPENDENT branches take one each.

        It reduces exactly to exact_chain._span_prior_correction on a chain:
        one component, the ends as the pair, exponent len(boundaries) - 2.

        A component with no unique outermost pair is skipped. There is then no
        single span for the correction to be about, and inventing one would
        change the model rather than de-bias it.
        """
        if len(self.boundary_ids) < 3:
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
            if exponent <= 0:
                continue
            rest = set(members)
            oldest = [b for b in members if rest - {b} <= younger_boundaries[b]]
            youngest = [b for b in members
                        if all(b in younger_boundaries[o] for o in rest - {b})]
            if len(oldest) == 1 and len(youngest) == 1 and oldest[0] != youngest[0]:
                priors.append((oldest[0], youngest[0], exponent))
        return priors

    def _build_blocks(self):
        """
        Dates grouped so that no two in a block constrain each other, by greedy
        colouring of the conflict graph -- highest degree first, which keeps the
        colour count near the minimum. Every date in one block can be drawn
        simultaneously because their conditionals are independent given
        everything outside the block.
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

    def draw_date(self, date_id: str, state: _State, u: float) -> int:
        """
        Exact draw from a date's full conditional: its own likelihood truncated
        to the allowed interval, by inverse CDF.
        """
        lo, hi = self.allowed(date_id, state)
        if hi <= lo:
            return max(lo, min(hi, state.index[date_id])) if hi >= 0 else state.index[date_id]
        cdf = self.cdf[date_id]
        base = cdf[lo - 1] if lo > 0 else 0.0
        total = cdf[hi] - base
        if total <= 0:
            # No likelihood anywhere it is allowed to be. Uniform keeps the
            # chain moving instead of freezing it at an arbitrary index; the
            # state is improbable either way and the group moves exist to get
            # out of it.
            return int(lo + u * (hi - lo))
        return int(np.clip(np.searchsorted(cdf, base + u * total), lo, hi))

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

        for oldest_id, youngest_id, exponent in self.span_priors:
            if boundary_id == oldest_id:
                outer = candidates - self.grid[state.index[youngest_id]]
            elif boundary_id == youngest_id:
                outer = self.grid[state.index[oldest_id]] - candidates
            else:
                continue
            with np.errstate(divide='ignore', invalid='ignore'):
                log_dens += np.where(
                    outer > 0, -exponent * np.log(np.where(outer > 0, outer, 1.0)),
                    -np.inf)

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
            total += float(self.log_likelihood[date_id][state.index[date_id]])

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
            outer = (self.grid[state.index[oldest_id]]
                     - self.grid[state.index[youngest_id]])
            if outer <= 0:
                return -np.inf
            total -= exponent * float(np.log(outer))

        return total

    def initial_state(self) -> _State:
        """
        A state that satisfies every hard constraint, built by walking the
        ordering from oldest to youngest and giving each parameter the youngest
        position it prefers that is still strictly older than everything that
        must be younger than it.

        Feasibility is not optional here and cannot be reached by local repair.
        Raw radiocarbon ages routinely contradict the stratigraphy -- resolving
        that inversion is much of why the model is being built -- so the naive
        "every date at its own likelihood mode" state is usually infeasible. A
        sampler with HARD constraints started there does not merely mix badly:
        every conditional collapses to a single allowed index and the chain
        cannot move at all. An earlier version of this swept the constraints
        locally instead, which drove whole groups onto one index and left the
        log posterior at -inf.

        Walking in topological order fixes it by construction: each step leaves
        at least one grid cell of separation, so every group span is positive
        and the starting log posterior is finite.
        """
        # Longest chain of "must be older than" above each parameter. Processing
        # in this order means every constraint on a parameter is already placed
        # by the time it is placed itself.
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
            # Near the dates it bounds, so it starts among the evidence rather
            # than out in the empty part of the window.
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

        state = _State(index)
        if not np.isfinite(self.log_posterior(state)):
            raise ValueError('could not find a feasible starting state for this model')
        return state

    def move_sets(self) -> List[List[str]]:
        """
        The parameter sets a translation proposal is offered over, coarsest
        first: the whole model, then each span group together with the
        boundaries enclosing it.

        The whole-model set is not an optimisation, it is the move the sampler
        cannot do without. Measured on the Nenthorn fixture with only per-group
        moves, every date's posterior mean was displaced by the SAME amount
        (a signed mean equal to the rms error), which is the signature of the
        entire configuration random-walking along the calendar axis: an
        absolute translation leaves every span, and therefore the whole phase
        prior, unchanged, so nothing in the model resists it except the
        likelihoods. Single-site Gibbs explores that mode one date at a time
        against the containment of the others, which is far slower than moving
        it directly. Adding the whole-model shift took the overlap-integral
        convergence at 20,000 sweeps from 0.62 to over 0.95.

        A group's set includes its boundaries so the group can move without
        being stopped by its own containment. Where a boundary is shared with
        the next group, the neighbouring group's dates come too -- otherwise
        the proposal always breaks that group's containment and is rejected
        essentially every time (measured: 1% acceptance).
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
            # This is what an ambiguous outlier needs. Such a date has two
            # plausible positions hundreds of years apart -- sitting with the
            # group as a flagged date, or out at its own likelihood as a sound
            # one -- and the second requires the enclosing boundary to come out
            # past it. Neither can move alone: the Gibbs draw confines the date
            # inside the boundaries, and the boundary's own conditional decays
            # away from the extreme date so it will not lead. Moving the pair
            # together is the only way across. Measured on a residual date 400
            # radiocarbon years too old, at 100,000 sweeps over three seeds, this
            # took the spread of its outlier probability from 0.09 to 0.01
            # around the exact answer.
            #
            # Only for dates carrying an outlier prior, because that is what
            # creates the mode: the broad outlier component is the only reason a
            # date has a second plausible position far outside its own group. It
            # is also what keeps the cost proportionate -- these sets are one per
            # flagged date per side, each costing a log-posterior evaluation, so
            # adding them for every date made a 16-date model 2.8x slower for no
            # gain on the fifteen sound ones.
            #
            # Deliberately ONE boundary at a time, not both: translating the date
            # with both drags the far boundary past the group's other dates and
            # breaks their containment, so the proposal is rejected almost every
            # time.
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
        sweeping several moves does not recompute what it already knows; the
        wide flag so scale adaptation can ignore those proposals, which are
        expected to be rejected often and would otherwise drag the local scale
        down to compensate for them.
        """
        if current_lp is None:
            current_lp = self.log_posterior(state)
        # Two-component symmetric proposal: the adapted local kernel most of the
        # time, a wide one otherwise so lobe-to-lobe jumps are actually proposed
        # (see WIDE_MOVE_FRACTION).
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


def run(model: Model, sweeps: int = 4000, seed: int = 42,
        burn_in: Optional[int] = None) -> Dict:
    """
    Sweep the sampler and accumulate each parameter's marginal as a histogram
    over the calendar grid.

    Histograms rather than stored draw arrays: the grid is the natural
    resolution of the whole problem, a count per cell is all _summarize needs,
    and it makes the memory cost independent of how long the chain runs.

    Returns {'histograms', 'accepted_group_moves', 'proposed_group_moves',
    'convergence'} -- convergence being OxCal's own diagnostic, the overlap
    integral between the first and second halves of the accumulated run (their
    "convergence %"; 95% is the usual target).
    """
    rng = np.random.default_rng(seed)
    state = model.initial_state()
    if burn_in is None:
        burn_in = int(sweeps * BURN_IN_FRACTION)
    halfway = burn_in + (sweeps - burn_in) // 2

    counts = {p: np.zeros(model.M) for p in model.params}
    first_half = {p: np.zeros(model.M) for p in model.params}
    kept = 0

    move_sets = model.move_sets()
    # One proposal scale per move set, in grid steps, adapted during burn-in
    # only -- after that the kernel is fixed, so the accumulated chain is a
    # valid MCMC run rather than one whose transition rule kept changing.
    scales = [max(1.0, GROUP_SHIFT_YEARS / model.dt) for _ in move_sets]
    proposed = [0] * len(move_sets)
    accepted = [0] * len(move_sets)
    since_adapt = [0] * len(move_sets)
    took_since_adapt = [0] * len(move_sets)

    for sweep in range(sweeps):
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

            if sweep < burn_in and since_adapt[index] >= ADAPT_INTERVAL:
                rate = took_since_adapt[index] / since_adapt[index]
                # Standard random-walk tuning: widen when accepting too often,
                # narrow when not. Floored at one grid step, since a proposal
                # rounding to zero steps can never move anything.
                scales[index] = max(1.0, scales[index] * (
                    1.3 if rate > TARGET_ACCEPTANCE else 0.75))
                since_adapt[index] = took_since_adapt[index] = 0

        if sweep >= burn_in:
            kept += 1
            for param in model.params:
                counts[param][state.index[param]] += 1.0
                if sweep < halfway:
                    first_half[param][state.index[param]] += 1.0

    convergence = {}
    for param in model.params:
        second = counts[param] - first_half[param]
        a, b = first_half[param].sum(), second.sum()
        if a > 0 and b > 0:
            convergence[param] = float(
                np.minimum(first_half[param] / a, second / b).sum())
        else:
            convergence[param] = 0.0

    return {
        'histograms': counts,
        'sweeps_kept': kept,
        'proposed_moves': sum(proposed),
        'accepted_moves': sum(accepted),
        # Per move set, so a set that never gets accepted is visible rather
        # than averaged away by the ones that do.
        'acceptance': [a / p if p else 0.0 for a, p in zip(accepted, proposed)],
        'scales': scales,
        'convergence': convergence,
    }


def solve(measurements: List[Dict], root: Dict, sweeps: int = 4000,
          num_samples: int = 4000, seed: int = 42,
          uniform_span_prior: bool = True,
          grid: Optional[np.ndarray] = None) -> Dict:
    """
    Posterior summaries for every date and boundary, in the same shape
    exact_chain.solve and BayesianChronology.extract_results return -- so a
    caller does not need to know which produced it.

    `grid` is accepted so a caller can force exact_chain's own grid and compare
    the two solvers without a resolution difference confounding the answer.
    """
    model = Model(measurements, root, grid=grid,
                  uniform_span_prior=uniform_span_prior)
    chain = run(model, sweeps=sweeps, seed=seed)
    rng = np.random.default_rng(seed)

    results: Dict = {'measurements': {}, 'boundaries': {}}
    for date_id in model.date_ids:
        density = chain['histograms'][date_id]
        summary = _summarize(model.grid, density, rng, num_samples)
        if date_id in model.outlier_parts:
            summary['outlier_probability'] = _outlier_probability(
                density, model.likelihood[date_id], *model.outlier_parts[date_id])
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
    results['sweeps'] = sweeps
    results['convergence'] = min(chain['convergence'].values()) if chain['convergence'] else 0.0
    results['acceptance'] = chain['acceptance']
    results['grid'] = {'lower': float(model.grid[0]), 'upper': float(model.grid[-1]),
                       'step': model.dt}
    return results
