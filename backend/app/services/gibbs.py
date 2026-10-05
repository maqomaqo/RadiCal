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

The same counting argument keeps two BOUNDARIES at least a cell apart (see
Model.allowed): the span correction divides by the placements strictly inside
a span, so boundaries that may meet have arrangements it never divides out.
TestBoundariesNeverShareACell pins that one.

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

Age-depth: the deposition model
-------------------------------
A `deposition_sequence` (OxCal's P_Sequence, Bronk Ramsey 2008) models
deposition as a Poisson process running down a depth axis. Between two levels
separated by dz the process contributes k*dz events, so over an ordered set of
depths the elapsed times are Dirichlet in the normalised increments:

    p(t_1 ... t_n | k)  =  Gamma(A) / prod Gamma(a_i)
                           * prod (dt_i)^(a_i - 1)
                           * T^(-(A - 1))

with a_i = k*dz_i, A = sum a_i, dt_i = t_i - t_(i+1) > 0 and T = sum dt_i.

Less changes here than the maths suggests. Hold everything else fixed and one
level's full conditional is its own likelihood times TWO power terms, one per
neighbour -- still one-dimensional, still tabulated on the calendar grid,
still drawn by one searchsorted. That is structurally what draw_boundary
already evaluates as a product of at most two span factors.

Three things are worth stating because they are where it would go quietly
wrong:

  * THE SPAN PRIOR MUST NOT ALSO APPLY. A deposition sequence supplies its own
    prior over the intervals between its members, so those members must not
    ALSO be a uniform scatter between the enclosing Boundaries. Two priors
    over the same intervals would not crash and would not look wrong; they
    would give phase widths wrong by a factor nobody could eyeball. The
    separation is made in services/model_tree.py, where such levels bubble up
    as CONTAINED rather than as members of the span term (SpanGroup), and this
    module simply never sees them in group_dates.

  * THE NORMALISATION IS NOT OPTIONAL, because k is a parameter. With k fixed,
    Gamma(A)/prod Gamma(a_i) and the T exponent are constants and could be
    dropped. With k drawn from its own conditional they are functions of it,
    and dropping them would let the data choose a rigidity by the wrong rule.

  * dt = 0 IS FORBIDDEN rather than floored. The exponent a_i - 1 is negative
    wherever k*dz_i < 1, which is the ordinary case, so coincident levels are
    a genuine divergence of the continuum density and not a numerical
    nuisance. Adjacent levels are therefore required to differ by at least one
    grid cell, and the remaining error is an ordinary discretisation error --
    checked by refining the grid rather than argued about.

`k` itself is a SHARED HYPERPARAMETER over one sequence, which is exactly the
shape draw_scale already handles for the outlier models: tabulate the
conditional over a grid of log10 k, accumulate each sequence's contribution at
its current positions, draw by inverse CDF. That is most of why P_Sequence is
tractable here at all.

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
import itertools
import multiprocessing
import os
import threading
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.special import gammaln

from app.services import calibration, intervals
from app.services.exact_chain import (
    DEFAULT_OUTLIER_MODEL,
    OUTLIER_KERNEL_HALF_WIDTH_YEARS,
    REPORTING_STEP_YEARS,
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
# Of the wide proposals, the fraction that jump by one of the distances
# between the members' own calibration peaks rather than by a draw from the
# wide kernel (see Model.peak_gaps), and how much the jump is blurred, in years.
PEAK_JUMP_FRACTION = 0.5
PEAK_JUMP_JITTER_YEARS = 5.0
# A calibration peak counts when it stands this far above the valleys either
# side of it, as a fraction of the date's highest point.
PEAK_PROMINENCE = 0.05
# Most distinct peak gaps kept per move set, the most common first.
MAX_PEAK_GAPS = 12
# A calibration lobe is somewhere a chain is worth starting once it holds this
# share of its date's (inlier) likelihood. Below it the lobe is too light to
# matter to any marginal, and would crowd out the lobes that do, since there
# are only a few chains to start (see Model.start_lobes).
LOBE_MIN_MASS = 0.05
# A lobe still counts as open to a date's starting cell when the window its
# neighbours leave reaches this fraction of the way up the lobe's peak.
LOBE_HELD_FRACTION = 0.25
# How a chain that does not start at the modes chooses a lobe for each date
# that has several (Model.start_lobes). 'quantile' leaves them to the shared
# quantile alone; the rest are in start_lobes. 'quantile' stays the default
# until tests/manual/start_study.py shows another detecting a missed lobe more
# often: the first pilot found no difference in detection or accuracy.
START_STRATEGIES = ('quantile', 'position', 'calendar', 'equalised', 'random')
DEFAULT_START_STRATEGY = 'quantile'
# How far into a lobe the 'equalised' strategy keeps its start from the lobe's
# edges, as a share of the lobe's own mass: the valley between two lobes is
# in neither.
EQUALISED_EDGE = 0.15
# Sweeps the run starts with, and the ceiling it will extend to when the
# convergence measure is not met. See run() for why the length is not fixed.
DEFAULT_SWEEPS = 20000
MAX_SWEEPS = 200000
# OxCal's own target for its convergence measure.
TARGET_CONVERGENCE = 0.95
# Rounds the accumulation is split into, which sets both the granularity of the
# first-half/second-half comparison and how often the run can stop.
ROUNDS = 8
# Independent chains a solve runs by default, each in its own process (see
# solve_chains). Four is the usual number for a between-chain diagnostic:
# enough that a chain stuck in one calibration lobe while the others found
# another shows up as disagreement, few enough to leave cores for the rest of
# the machine. Only if they START apart (start_quantiles): four chains from
# one state can settle in one lobe together and agree perfectly.
DEFAULT_CHAINS = 4
# Most worker processes the chain pool holds, whatever is asked for.
MAX_CHAIN_WORKERS = 8
# Gelman-Rubin R-hat the chains must agree to before a pooled run may stop.
# 1.05 is the conventional threshold (Gelman et al.); a chain settled in a
# calibration lobe the others left gives values in the tens, so what this
# guards against is unmistakable at any threshold in the usual range. What it
# cannot see is a lobe NO chain is in.
TARGET_RHAT = 1.05
# The span of the dates' likelihoods the extra chains start across (see
# start_quantiles). Short of the extreme tails, where an outlier model's broad
# component puts a date centuries from anything it is plausibly near.
START_QUANTILE_RANGE = (0.025, 0.975)
# Nodes the deposition rigidity `k` is tabulated over, log-uniform between the
# sequence's own bounds. Twenty-four is what the outlier scale uses over four
# decades and is more than enough here: k enters the answer only through
# k*dz, and the posterior on it is broad on any real core.
DEPOSITION_K_NODES = 24


class _Deposition:
    """
    One deposition_sequence, in the form the sampler works in: parameter ids
    oldest first, the DEPTH GAPS between adjacent levels, and the grid of
    rigidities `k` may take.

    Depth gaps rather than depths, because nothing downstream wants a depth:
    every term in the prior is a function of `k * dz` for one adjacent pair.
    """

    def __init__(self, element_id: str, name: str, ids: List[str],
                 depth_gaps: np.ndarray, k: Optional[float],
                 log_k_min: float, log_k_max: float, depth_units: str):
        self.element_id = element_id
        self.name = name
        self.ids = ids
        self.depth_gaps = depth_gaps
        # None means the run infers it; see Model.draw_deposition_rate.
        self.fixed_k = k
        self.k_nodes = (np.array([k]) if k is not None
                        else 10.0 ** np.linspace(log_k_min, log_k_max,
                                                 DEPOSITION_K_NODES))
        self.log_k_min = log_k_min
        self.log_k_max = log_k_max
        self.depth_units = depth_units

    @property
    def variable(self) -> bool:
        return self.fixed_k is None

    def shape_parameters(self, k: float) -> np.ndarray:
        """a_i = k * dz_i, the Dirichlet shape of each interval."""
        return k * self.depth_gaps

    def __repr__(self):
        return f"_Deposition({self.element_id}, {len(self.ids)} levels)"


def _log_deposition_density(gaps: np.ndarray, alpha: np.ndarray) -> float:
    """
    log p(dt | k) for one deposition sequence, NORMALISED.

    `gaps` are the elapsed times between adjacent levels in years, oldest
    first; `alpha` their Dirichlet shapes k*dz. -inf where any interval has
    collapsed, which is a real divergence of the continuum density wherever
    k*dz < 1 rather than a numerical inconvenience -- see the module
    docstring.

    The normalisation is carried rather than dropped because `k` is a
    parameter: with it fixed these terms are constants, but with it drawn from
    its own conditional they are functions of it, and leaving them out would
    let the data pick a rigidity by the wrong rule.
    """
    if np.any(gaps <= 0):
        return -np.inf
    total = float(gaps.sum())
    A = float(alpha.sum())
    return float(
        gammaln(A) - gammaln(alpha).sum()
        + ((alpha - 1.0) * np.log(gaps)).sum()
        - (A - 1.0) * np.log(total)
    )


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

    def __init__(self, index: Dict[str, int],
                 scale: Optional[Dict[str, int]] = None,
                 rate: Optional[Dict[str, int]] = None):
        self.index = index
        self.scale = dict(scale or {})
        # Per deposition sequence, the index of its current rigidity on that
        # sequence's own grid of k -- one shared value for the whole sequence,
        # exactly as `scale` is one per outlier model.
        self.rate = dict(rate or {})

    def copy(self) -> '_State':
        return _State(dict(self.index), self.scale, self.rate)


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
        self.date_ids = [m['id'] for m in measurements]
        self._build_deposition()
        # Cumulative sums of the likelihood ROWS, for inverse-CDF draws over an
        # arbitrary sub-interval: mass in [a, b] is cdf[b] - cdf[a-1].
        self.log_likelihood = {
            did: np.log(np.maximum(lik, 1e-300)) for did, lik in self.likelihood.items()
        }
        self.cdf = {did: np.cumsum(lik) for did, lik in self.likelihood.items()}
        self._build_outlier_tables()

        self.boundary_ids = list(self.plan.boundary_names)
        self.boundary_set = set(self.boundary_ids)
        # Every boundary is sampled, including the outermost ones exact_chain
        # marginalises: a Gibbs draw from a truncated power law is exact
        # however heavy the tail.
        self.drawable_ids = self.date_ids + self.level_ids
        self.params = self.drawable_ids + self.boundary_ids
        self.param_row = {p: i for i, p in enumerate(self.params)}
        # Which outlier model each flagged date uses, so a draw does not have
        # to scan every model's member list to find out.
        self.model_of_date: Dict[str, str] = {
            d: model for model, dates in self.scaled_dates.items() for d in dates}

        self._build_constraints()
        self._build_blocks()

    # -- deposition sequences ----------------------------------------------

    def _build_deposition(self):
        """
        Each deposition_sequence as a _Deposition, plus a flat likelihood for
        every UNDATED level in one.

        An undated level -- "how old is 47 cm" -- is a parameter whose
        likelihood table is flat. Nothing else about it is special: a uniform
        table drawn over the interval its neighbours allow, weighted by the
        two deposition terms, is precisely the right answer, so draw_date
        needs no branch for it.
        """
        self.depositions: List[_Deposition] = []
        self.level_ids: List[str] = []
        # What an undated level is called when its posterior is reported. Its
        # depth is in the name where it has no other, since "the age at 47 cm"
        # is the question it exists to answer.
        self.level_names: Dict[str, str] = {}
        # Which sequence each level belongs to, and where in it, so a draw
        # does not have to scan every sequence.
        self.deposition_of: Dict[str, Tuple[_Deposition, int]] = {}

        flat = np.full(self.M, 1.0 / (self.M * self.dt))
        for group in self.plan.deposition_groups:
            depths = np.array([depth for _pid, depth, _dated in group.levels],
                              dtype=float)
            depth_gaps = np.abs(np.diff(depths))
            if np.any(depth_gaps <= 0):
                # model_tree.validate_ordinates refuses this at every edge the
                # app has; restated here because a zero gap gives a Dirichlet
                # shape of zero, and the failure it produces further in is
                # "could not find a feasible starting state", which says
                # nothing about depths.
                raise ValueError(
                    f"the deposition sequence '{group.name}' has two levels at "
                    'the same depth. The model prices the interval between '
                    'successive depths, and a zero interval has no rate'
                )
            deposition = _Deposition(
                element_id=group.element_id,
                name=group.name,
                ids=[pid for pid, _d, _dated in group.levels],
                depth_gaps=depth_gaps,
                k=group.k,
                log_k_min=group.log_k_min,
                log_k_max=group.log_k_max,
                depth_units=group.depth_units,
            )
            self.depositions.append(deposition)
            for position, (pid, depth, dated) in enumerate(group.levels):
                self.deposition_of[pid] = (deposition, position)
                if dated:
                    continue
                self.level_ids.append(pid)
                self.level_names[pid] = group.level_names.get(pid) or (
                    f'{depth:g} {group.depth_units}')
                self.likelihood[pid] = flat.copy()

    def _deposition_gaps(self, deposition: _Deposition,
                         state: _State) -> np.ndarray:
        """Elapsed years between adjacent levels, oldest first."""
        ages = self.grid[[state.index[pid] for pid in deposition.ids]]
        return -np.diff(ages)

    def _alpha_of(self, deposition: _Deposition, state: _State) -> np.ndarray:
        node = state.rate.get(deposition.element_id, 0)
        k = float(deposition.k_nodes[min(node, len(deposition.k_nodes) - 1)])
        return deposition.shape_parameters(k)

    def _log_deposition_terms(self, param: str, state: _State,
                              candidates: np.ndarray) -> Optional[np.ndarray]:
        """
        The deposition prior as a function of where `param` sits, over
        `candidates` (grid INDICES), or None if it is not in one.

        Everything not involving this level is a constant across the
        candidates and cancels in the normalisation, so only the intervals it
        touches -- and, at either end of the sequence, the total span -- have
        to be evaluated.
        """
        found = self.deposition_of.get(param)
        if found is None:
            return None
        deposition, position = found
        alpha = self._alpha_of(deposition, state)
        ids = deposition.ids
        ages = self.grid[candidates]

        log_dens = np.zeros(len(candidates))
        with np.errstate(divide='ignore', invalid='ignore'):
            if position > 0:
                older = self.grid[state.index[ids[position - 1]]]
                gap = older - ages
                log_dens += np.where(
                    gap > 0,
                    (alpha[position - 1] - 1.0) * np.log(np.where(gap > 0, gap, 1.0)),
                    -np.inf)
            if position < len(ids) - 1:
                younger = self.grid[state.index[ids[position + 1]]]
                gap = ages - younger
                log_dens += np.where(
                    gap > 0,
                    (alpha[position] - 1.0) * np.log(np.where(gap > 0, gap, 1.0)),
                    -np.inf)
            # The T^-(A-1) factor varies only when this level IS an end of the
            # sequence; for an interior one the total span is fixed and the
            # factor cancels.
            if position in (0, len(ids) - 1):
                # Written so the span is positive when the ordering holds and
                # non-positive when it does not, rather than taking an
                # absolute value -- which would give a finite weight to a
                # candidate that has crossed the far end of the sequence.
                other = self.grid[state.index[ids[-1 if position == 0 else 0]]]
                total = (ages - other) if position == 0 else (other - ages)
                A = float(alpha.sum())
                log_dens += np.where(
                    total > 0,
                    -(A - 1.0) * np.log(np.where(total > 0, total, 1.0)),
                    -np.inf)
        return log_dens

    def draw_deposition_rate(self, deposition: _Deposition, state: _State,
                             u: float) -> int:
        """
        Exact draw of one sequence's rigidity from its full conditional: the
        NORMALISED deposition density at the levels' current positions, over a
        log-uniform prior on k.

        The same shape as draw_scale, and for the same reason -- one value
        shared by every level in the sequence, one dimension, tabulated. It is
        also why the normalisation in _log_deposition_density cannot be
        dropped: with k free, Gamma(A)/prod Gamma(a_i) is a function of the
        parameter being drawn.
        """
        if not deposition.variable:
            return 0
        gaps = self._deposition_gaps(deposition, state)
        if np.any(gaps <= 0):
            return state.rate.get(deposition.element_id, 0)
        # _log_deposition_density over every node at once: alpha is a
        # (nodes x intervals) matrix and each term reduces along the intervals.
        alpha = deposition.k_nodes[:, None] * deposition.depth_gaps[None, :]
        A = alpha.sum(axis=1)
        log_weights = (gammaln(A) - gammaln(alpha).sum(axis=1)
                       + ((alpha - 1.0) * np.log(gaps)[None, :]).sum(axis=1)
                       - (A - 1.0) * np.log(gaps.sum()))
        finite = np.isfinite(log_weights)
        if not np.any(finite):
            return state.rate.get(deposition.element_id, 0)
        weights = np.where(finite, np.exp(log_weights - log_weights[finite].max()), 0.0)
        total = weights.sum()
        if not np.isfinite(total) or total <= 0:
            return state.rate.get(deposition.element_id, 0)
        return int(np.searchsorted(np.cumsum(weights) / total, u, side='left')
                   .clip(0, len(deposition.k_nodes) - 1))

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
        model = self.model_of_date.get(date_id)
        return None if model is None else state.scale.get(model, 0)

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
        measurement = next(
            (x for x in self.measurements if x['id'] == param), None)
        if is_date and measurement is not None:
            el_taq, el_tpq = self.element_bounds.get(
                measurement.get('element_id'), (None, None))
            taq, tpq = effective_bounds(
                measurement, {'taq_age_bp': el_taq, 'tpq_age_bp': el_tpq})
        else:
            # A boundary, or an undated level -- both take their limits from
            # the element they are, and from whatever encloses it.
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
            # Contained but NOT part of the span term: a deposition
            # sequence's levels are penned in by the same boundaries while
            # their spacing is priced by the deposition model instead. They
            # get the containment and none of the (span)^(-K), which is the
            # whole of what keeps the two priors from being applied at once.
            for contained_id in group.contained_ids:
                if contained_id not in self.likelihood:
                    continue
                add(contained_id, group.older_boundary_id)
                add(group.younger_boundary_id, contained_id)

        self.window = {p: self._own_window(p, p in self.likelihood) for p in self.params}

        self.span_priors = self._span_priors() if self.uniform_span_prior else []
        self.limits_tables = self._limits_factor_tables()
        self.span_prior_rows = self._span_prior_rows()

        # Lookup tables for the two per-draw evaluations that were being
        # recomputed on every call. Every span in this sampler is a whole
        # number of cells, so -K * log(span) and the span-prior factor are
        # functions of an integer in [0, M) and can be gathered rather than
        # evaluated: log_interior_placements alone was three gammaln calls per
        # call and a fifth of the run.
        with np.errstate(divide='ignore'):
            self.log_span_of_cells = np.log(np.arange(self.M) * self.dt)

        # From the boundary's side: which groups and which span priors it is
        # an end of, and which end. draw_boundary then touches only its own
        # terms instead of scanning every group for itself.
        self.boundary_groups: Dict[str, List[Tuple[int, bool]]] = {
            b: [] for b in self.boundary_ids}
        for gi, (older_id, younger_id, dates) in enumerate(self.group_dates):
            if not dates:
                continue
            self.boundary_groups.setdefault(older_id, []).append((gi, True))
            self.boundary_groups.setdefault(younger_id, []).append((gi, False))
        self.boundary_priors: Dict[str, List[Tuple[int, bool]]] = {
            b: [] for b in self.boundary_ids}
        for pi, (oldest_id, youngest_id, _exponent) in enumerate(self.span_priors):
            self.boundary_priors.setdefault(oldest_id, []).append((pi, True))
            self.boundary_priors.setdefault(youngest_id, []).append((pi, False))

        # Ordering edges as index arrays, for a vectorised feasibility check.
        pairs = [(self.param_row[p], self.param_row[o])
                 for p in self.params for o in self.below[p]]
        self.edge_younger = np.array([a for a, _b in pairs], dtype=np.intp)
        self.edge_older = np.array([b for _a, b in pairs], dtype=np.intp)
        # How far apart each edge's ends must be, in cells: one between two
        # boundaries, none otherwise. See allowed() for why.
        boundaries = set(self.boundary_ids)
        self.edge_gap = np.array(
            [int(self.params[a] in boundaries and self.params[b] in boundaries)
             for a, b in pairs], dtype=np.int64)
        self.window_lo = np.array([self.window[p][0] for p in self.params])
        self.window_hi = np.array([self.window[p][1] for p in self.params])

    def _span_prior_rows(self) -> List[np.ndarray]:
        """
        The whole span-prior factor per prior, in the log, as a row indexed
        by the span in CELLS: -log C(cells - 1, n_interior) plus the limits
        factor where there is one, and -inf where the interior boundaries
        do not fit. What draw_boundary and log_posterior gather from; the
        two pieces are kept separately in span_priors and limits_tables for
        anything that wants to see them apart.
        """
        rows = []
        cells = np.arange(self.M, dtype=float)
        for oldest_id, youngest_id, exponent in self.span_priors:
            log_count = log_interior_placements(cells, exponent)
            row = np.where(np.isfinite(log_count), -log_count, -np.inf)
            limits = self.limits_tables.get((oldest_id, youngest_id))
            if limits is not None:
                row = row + limits
            rows.append(row)
        return rows

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
        drawable = self.date_ids + self.level_ids
        conflicts: Dict[str, set] = {d: set() for d in drawable}
        for date_id in drawable:
            for other in self.below[date_id] + self.above[date_id]:
                if other in conflicts:
                    conflicts[date_id].add(other)
                    conflicts[other].add(date_id)

        colour: Dict[str, int] = {}
        for date_id in sorted(drawable, key=lambda d: -len(conflicts[d])):
            taken = {colour[n] for n in conflicts[date_id] if n in colour}
            colour[date_id] = next(c for c in range(len(drawable) + 1) if c not in taken)

        blocks: Dict[int, List[str]] = {}
        for date_id, c in colour.items():
            blocks.setdefault(c, []).append(date_id)
        self.blocks = [blocks[c] for c in sorted(blocks)]

    # -- conditionals ------------------------------------------------------

    def allowed(self, param: str, state: _State) -> Tuple[int, int]:
        """
        The index range `param` may currently occupy, constraints included.

        Two BOUNDARIES are kept at least one cell apart; anything else may
        share a cell with what it is ordered against. The span prior counts
        the ways n - 2 interior boundaries fit inside a span of j cells as
        C(j - 1, n - 2) -- strictly inside and strictly ordered, which is also
        what exact_chain's transfer matrices integrate (span > 0 for every
        group, an empty one included). Letting boundaries coincide gives a
        span more arrangements than it is divided by, and as with the endpoint
        weighting the excess is largest at the SMALLEST spans, so the chain
        packs everything together. Wherever an empty phase or a nesting level
        separated two boundaries -- a Sequence of layers with sterile sand
        between them, or Sequences inside a Phase -- they could meet. On a
        real dune section of that kind it took the start of the sequence from
        615 to 563 cal BP, P(< 585) from 0.09 to 1.0, and every chain agreed.
        """
        lo, hi = self.window[param]
        gap = 1 if param in self.boundary_set else 0
        for older in self.below[param]:
            hi = min(hi, state.index[older] - (gap if older in self.boundary_set else 0))
        for younger in self.above[param]:
            lo = max(lo, state.index[younger] + (gap if younger in self.boundary_set else 0))
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
        if date_id in self.deposition_of:
            return self._draw_deposition_level(date_id, state, u, lo, hi)
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
            picked = int(np.searchsorted(cdf, base + (target - low_end)))
            return min(max(picked, lo + 1), hi - 1)
        return hi

    def _draw_deposition_level(self, level_id: str, state: _State, u: float,
                               lo: int, hi: int) -> int:
        """
        Exact draw from a deposition level's full conditional: its own
        likelihood times the deposition prior's two neighbour terms, over the
        allowed interval.

        Built explicitly over [lo, hi] rather than through the precomputed
        cumulative the phase model uses, because the weight now varies with
        position for a reason other than the likelihood. Still
        one-dimensional, still one searchsorted, and O(hi - lo) per draw --
        which is the whole cost of the deposition model over the plain one.

        NO endpoint half-weighting. That correction exists to keep a date's
        available volume in step with the (span)^(-K) it is charged, and a
        deposition level is charged no span term at all -- see the module
        docstring on the two priors.
        """
        candidates = np.arange(lo, hi + 1)
        node = self._scale_of(level_id, state)
        density = (self.likelihood[level_id] if node is None
                   else self.likelihood_at[level_id][node])
        with np.errstate(divide='ignore'):
            log_dens = np.log(np.maximum(density[lo:hi + 1], 1e-300))
        terms = self._log_deposition_terms(level_id, state, candidates)
        if terms is not None:
            log_dens = log_dens + terms

        finite = np.isfinite(log_dens)
        if not finite.any():
            return state.index[level_id]
        weights = np.where(finite, np.exp(log_dens - log_dens[finite].max()), 0.0)
        total = weights.sum()
        if total <= 0:
            return state.index[level_id]
        cdf = np.cumsum(weights)
        return lo + min(int(np.searchsorted(cdf, u * total)), hi - lo)

    def draw_boundary(self, boundary_id: str, state: _State, u: float) -> int:
        """
        Exact draw from a boundary's full conditional: the span factors of the
        groups it bounds, over its allowed interval. It has no likelihood of its
        own, so on a group with no dates this is simply uniform.
        """
        lo, hi = self.allowed(boundary_id, state)
        if hi <= lo:
            return state.index[boundary_id]
        candidate_indices = np.arange(lo, hi + 1)
        log_dens = np.zeros(hi - lo + 1)

        for gi, is_older in self.boundary_groups.get(boundary_id, ()):
            older_id, younger_id, dates = self.group_dates[gi]
            K = len(dates)
            # The span in cells is an integer in [0, M), so its log is a
            # gather; a non-positive span reads -inf from the table's zero
            # cell, or is masked where the candidate has crossed over.
            if is_older:
                cells = candidate_indices - state.index[younger_id]
            else:
                cells = state.index[older_id] - candidate_indices
            log_dens += np.where(
                cells > 0, -K * self.log_span_of_cells.take(cells, mode='clip'),
                -np.inf)

            # A date sitting exactly ON the candidate is an endpoint there, so
            # it counts a half (endpoint_weight). Only coincidences with THIS
            # boundary vary with the candidate; the other boundary's cancel in
            # the normalisation below.
            for date_id in dates:
                index = state.index[date_id]
                if lo <= index <= hi:
                    log_dens[index - lo] -= np.log(2.0)

        for pi, is_oldest in self.boundary_priors.get(boundary_id, ()):
            oldest_id, youngest_id, _exponent = self.span_priors[pi]
            if is_oldest:
                cells = candidate_indices - state.index[youngest_id]
            else:
                cells = state.index[oldest_id] - candidate_indices
            # Counted in CELLS and as a binomial rather than a power -- see
            # exact_chain._span_prior_correction for why a power is wrong on a
            # grid. The row holds the log of the whole factor, both parts with
            # one sign: 1/(ways the interior boundaries fit) times 1/g.
            log_dens += np.where((cells >= 0) & (cells < self.M),
                                 self.span_prior_rows[pi].take(cells, mode='clip'),
                                 -np.inf)

        finite = np.isfinite(log_dens)
        if not finite.any():
            return state.index[boundary_id]
        dens = np.where(finite, np.exp(log_dens - log_dens[finite].max()), 0.0)
        total = dens.sum()
        if total <= 0:
            return state.index[boundary_id]
        cdf = np.cumsum(dens)
        return lo + min(int(np.searchsorted(cdf, u * total)), hi - lo)

    def log_posterior(self, state: _State) -> float:
        """
        The full log posterior, used for the MH acceptance ratio of the group
        moves. -inf when any hard constraint is violated, which is how a
        proposal that breaks the ordering is rejected.
        """
        # The hard constraints first, as one vectorised check: every
        # parameter inside its own window and on the right side of every
        # ordering edge. Cheapest to fail, and the only way to fail.
        index = np.fromiter((state.index[p] for p in self.params),
                            dtype=np.int64, count=len(self.params))
        if ((index < self.window_lo) | (index > self.window_hi)).any():
            return -np.inf
        if len(self.edge_younger) and (
                index[self.edge_younger] + self.edge_gap > index[self.edge_older]).any():
            return -np.inf

        total = 0.0
        scale = state.scale
        model_of = self.model_of_date
        for date_id in self.drawable_ids:
            model = model_of.get(date_id)
            table = (self.log_likelihood[date_id] if model is None
                     else self.log_likelihood_at[date_id][scale.get(model, 0)])
            total += table[state.index[date_id]]
        # log of endpoint_weight, summed: -log 2 per boundary a date sits on.
        halves = 0
        for date_id, groups in self.date_groups.items():
            at = state.index[date_id]
            for older_id, younger_id in groups:
                if at == state.index[older_id] or at == state.index[younger_id]:
                    halves += 1
        total -= halves * np.log(2.0)

        # The deposition prior, in full and NORMALISED -- the shift moves
        # below accept on this ratio, and a term missing here would let them
        # accept translations the conditionals would never have drawn.
        for deposition in self.depositions:
            contribution = _log_deposition_density(
                self._deposition_gaps(deposition, state),
                self._alpha_of(deposition, state))
            if not np.isfinite(contribution):
                return -np.inf
            total += contribution

        for older_id, younger_id, dates in self.group_dates:
            K = len(dates)
            if K == 0:
                continue
            cells = state.index[older_id] - state.index[younger_id]
            if cells <= 0:
                return -np.inf
            total -= K * self.log_span_of_cells[cells]

        for pi, (oldest_id, youngest_id, _exponent) in enumerate(self.span_priors):
            cells = state.index[oldest_id] - state.index[youngest_id]
            if not 0 <= cells < self.M:
                return -np.inf
            value = self.span_prior_rows[pi][cells]
            if not np.isfinite(value):
                return -np.inf
            total += value

        return float(total)

    def initial_state(self, quantile: Optional[float] = None,
                      lobe: Optional[Tuple] = None) -> _State:
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

        WHERE a date prefers to be is its likelihood mode by default, and with
        `quantile` the point that fraction of the way through its likelihood
        instead -- the SAME fraction for every date. That is what makes
        between-chain R-hat mean anything (solve_chains): it only sees a chain
        stuck in one calibration range if another chain is somewhere else, and
        chains that all start at the mode all start in the same range, so the
        diagnostic passes whichever one that is. One shared fraction rather
        than one per date because the ranges that matter are a whole group's:
        a phase's dates calibrate on the same stretch of curve, and a group can
        only be found in its other range if its members start there together
        -- single-site moves cannot carry it across one date at a time.

        `lobe` is (rank, ranks[, strategy[, seed]]): the chain's place among
        the `ranks` chains that do not start at the modes, and how it chooses
        (Model.start_lobes). A quantile reaches a lobe only in
        proportion to its mass -- on a dune section with sterile layers, five
        of nine multi-lobe dates had a lobe holding a third of their
        likelihood that no chain started in -- so a date with more than one
        lobe of its own (Model.lobes) starts in the one start_lobes wishes this
        rank, and the quantile is left to dates with a single lobe.
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

        wishes = self.start_lobes(*lobe) if lobe is not None else {}
        preferred: Dict[str, int] = {}
        for date_id in self.date_ids:
            if date_id in wishes:
                (peak, _m, _f, _l), target = wishes[date_id][0]
                preferred[date_id] = peak if target is None else target
            elif quantile is None:
                preferred[date_id] = int(np.argmax(self.likelihood[date_id]))
            else:
                cdf = self.cdf[date_id]
                preferred[date_id] = int(min(self.M - 1, np.searchsorted(
                    cdf, quantile * cdf[-1])))
        for level_id in self.level_ids:
            # A flat table has no mode -- argmax would return index 0, the
            # oldest end of the whole grid, and the topological pass below
            # would then drag every dated level in the sequence up against it.
            # Start it between the dated levels either side instead.
            deposition, position = self.deposition_of[level_id]
            neighbours = [preferred[pid] for pid in deposition.ids
                          if pid in preferred]
            preferred[level_id] = (int(np.mean(neighbours)) if neighbours
                                   else self.M // 2)
        for boundary_id in self.boundary_ids:
            # Near the dates it bounds, rather than out in the empty part of
            # the window.
            neighbours = [preferred[d] for older, younger, dates in self.group_dates
                          if boundary_id in (older, younger) for d in dates]
            preferred[boundary_id] = (int(np.mean(neighbours)) if neighbours
                                      else self.M // 2)
            if wishes:
                # The mean pens the dates in. A Phase's Start at the mean of its
                # dates' wishes has half of them older than itself, and the walk
                # below then drags them into whatever lobe is younger than it
                # -- on the OxCal fixture the older lobes were never started in.
                # Just outside every date it bounds instead, where the
                # posterior puts it anyway: older than all of them for a Start,
                # younger than all of them for an End.
                sides = [(max(preferred[d] for d in dates) + 1) if boundary_id == older
                         else (min(preferred[d] for d in dates) - 1)
                         for older, younger, dates in self.group_dates
                         if dates and boundary_id in (older, younger)]
                if sides:
                    preferred[boundary_id] = int(np.mean(sides))

        # The youngest cell each parameter may take and still leave room for
        # everything that must be YOUNGER than it: one cell per link of the
        # longest chain below, from wherever that chain's own windows floor
        # it. Without this look-ahead the walk placed a date at its likelihood
        # mode even when that was the youngest cell of the grid -- a marine
        # shell at 620 BP peaks at 1950 -- and the End boundary below it then
        # had no cell to go to. Found on a regional model's youngest atoll.
        floor: Dict[str, int] = {}

        def compute_floor(param: str) -> int:
            if param in floor:
                return floor[param]
            value = self.window[param][0]
            for younger in self.above[param]:
                value = max(value, 1 + compute_floor(younger))
            floor[param] = value
            return value

        for param in self.params:
            compute_floor(param)

        # What each parameter must clear to leave the wished lobes of the dates
        # younger than it open. The walk places the oldest first and only ever
        # lowers a younger one to the cell above its older neighbour, so a
        # younger date wishing for an old lobe finds it shut unless everything
        # older has already been put at least that old: on the OxCal fixture
        # the oldest lobe of three dates was never started in for want of it.
        lobe_floor = {d: self._lobe_floor(d, wishes[d][0][0]) for d in wishes}
        clears: Dict[str, int] = {}

        def compute_clears(param: str) -> int:
            if param not in clears:
                # One cell clear of each younger neighbour, as the walk keeps
                # every link strict.
                clears[param] = max(
                    [-1] + [max(lobe_floor.get(y, -1), compute_clears(y)) + 1
                            for y in self.above[param]])
            return clears[param]

        index: Dict[str, int] = {}
        for param in sorted(self.params, key=lambda p: depth[p]):
            lo, hi = floor[param], self.window[param][1]
            for older in self.below[param]:
                hi = min(hi, index[older] - 1)
            if wishes and lo < compute_clears(param) <= hi:
                lo = compute_clears(param)
            if hi < lo:
                raise ValueError(
                    'the model has no state satisfying all of its constraints: '
                    f"'{param}' must be older than {lo} and younger than {hi} "
                    'on the calendar grid at once'
                )
            start = (self._start_in_lobe(param, wishes[param], lo, hi)
                     if param in wishes else None)
            index[param] = (start if start is not None
                            else int(np.clip(preferred[param], lo, hi)))

        self._widen_span_pairs(index)

        # Mid-prior: with u uniform on its range there is no better guess, and
        # its conditional is drawn exactly on the first sweep.
        scale = {model: len(self.scale_grid[model]) // 2
                 for model in self.outlier_models}
        rate = {d.element_id: len(d.k_nodes) // 2 for d in self.depositions}
        state = _State(index, scale, rate)
        if not np.isfinite(self.log_posterior(state)):
            raise ValueError('could not find a feasible starting state for this model')
        return state

    def _lobe_floor(self, date_id: str, lobe: Tuple[int, float, int, int]) -> int:
        """The youngest cell of `lobe` that a start there may take: the first
        the lobe reaches LOBE_HELD_FRACTION of its peak."""
        peak, _mass, first, last = lobe
        row = self._inlier_row(date_id)
        held = np.nonzero(row[first:last] >= LOBE_HELD_FRACTION * row[peak])[0]
        return first + int(held[0])

    def _start_in_lobe(self, date_id: str,
                       wished: List[Tuple[Tuple[int, float, int, int], Optional[int]]],
                       lo: int, hi: int) -> Optional[int]:
        """
        The cell in [lo, hi] a date starts at: in the first lobe of `wished`
        that the window still holds, or None if it holds none. Within the lobe
        it is the wished cell if there is one (else the lobe's peak), moved to
        the nearest cell the window allows, or the highest point the window
        holds when that cell is too low in the lobe to count.

        Merely clipping the wished peak into the window is what a stratigraphic
        order defeats: a date wishing for an old lobe, placed after neighbours
        that sit in a younger one, is dragged to the edge of the window, which
        is in no lobe at all. Taking the next lobe the window does hold keeps
        the start in a lobe, if not the wished one. A lobe counts as held when
        the window reaches LOBE_HELD_FRACTION of the way up it.
        """
        row = self._inlier_row(date_id)
        for (peak, _mass, first, last), target in wished:
            a, b = max(lo, first), min(hi, last - 1)
            if a <= b:
                inside = row[a:b + 1]
                if inside.max() >= LOBE_HELD_FRACTION * row[peak]:
                    if target is not None:
                        cell = int(np.clip(target, a, b))
                        if row[cell] >= LOBE_HELD_FRACTION * row[peak]:
                            return cell
                    return a + int(np.argmax(inside))
        return None

    def _widen_span_pairs(self, index: Dict[str, int]) -> None:
        """
        Leave every span-prior pair the cells its interior boundaries need.

        The walk in initial_state keeps one cell per link of the LONGEST chain,
        so boundaries in parallel branches share cells and an outer pair can
        come out narrower than its n - 2 interior boundaries (n - 1 cells).
        The span correction counts C(cells - 1, n - 2) placements, which is
        zero there, so the start would have a log posterior of -inf. It shows
        on a coarse grid, where a Site's whole span is few cells.

        Widened by moving the pair itself outward, the older end older and the
        younger end younger, as far as its own window and whatever is older or
        younger than it allow. Everything between stays where the walk put it,
        and a pair already wide enough is not touched, so a chain starts as it
        always did. If the grid has no room the start stays infeasible and
        initial_state says so.
        """
        for oldest_id, youngest_id, exponent in self.span_priors:
            deficit = exponent + 1 - (index[oldest_id] - index[youngest_id])
            if deficit <= 0:
                continue
            room_older = min(
                [self.window[oldest_id][1]]
                + [index[o] - 1 for o in self.below[oldest_id]]) - index[oldest_id]
            room_younger = index[youngest_id] - max(
                [self.window[youngest_id][0]]
                + [index[y] + 1 for y in self.above[youngest_id]])
            # Half each way, then whatever one end could not give from the other.
            older = min(max(room_older, 0), (deficit + 1) // 2)
            younger = min(max(room_younger, 0), deficit - older)
            older = min(max(room_older, 0), deficit - younger)
            index[oldest_id] += older
            index[youngest_id] -= younger

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

        # A whole deposition sequence, with whatever encloses it. Its levels
        # are MORE tightly coupled than a phase's dates -- the deposition
        # prior resists changing any interval, not just the ordering -- so the
        # sticky behaviour that motivated group moves is worse here, not
        # better, and single-site Gibbs would explore the sequence's position
        # one level at a time against the containment of the rest.
        for deposition in self.depositions:
            members = set(deposition.ids)
            # The boundaries penning it in come along, or the proposal breaks
            # its own containment and is rejected almost every time.
            for group in self.plan.span_groups:
                if members & set(group.contained_ids):
                    members |= {group.older_boundary_id, group.younger_boundary_id}
                    members |= set(group.date_ids)
            sets.append(sorted(members))

        for older_id, younger_id, dates in self.group_dates:
            if not dates:
                continue
            members = set(dates) | {older_id, younger_id}
            # Anything sharing a boundary with this group has to come along.
            for other_older, other_younger, other_dates in self.group_dates:
                if {other_older, other_younger} & {older_id, younger_id}:
                    members |= set(other_dates) | {other_older, other_younger}
            sets.append(sorted(members))
            sets.extend(self._with_enclosure(members))

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

    def _with_enclosure(self, members: set) -> List[List[str]]:
        """
        `members` with the nearest boundary penning it in from outside, once
        per side: the boundary just older than its oldest boundary, and the
        one just younger than its youngest.

        A group in one of several PARALLEL branches is held in by boundaries
        it does not share with any other group -- a Site's Start and End
        around two sequences -- and its own set cannot carry it past them. The
        Site's span correction keeps those boundaries hard against whatever
        they enclose, so on a real two-area site model, moving one area's
        phase between its two calibration ranges needed the Start (going
        older) or the End (going younger) to move with it. Without this the
        run converged with 0.27 of that phase in its later range against the
        exact 0.40.

        One side at a time, as for a flagged date, so the far boundary is not
        dragged across the other branch's dates.
        """
        boundaries = set(self.boundary_ids)
        own = members & boundaries
        variants = []
        for outward in (self.below, self.above):
            outside = {b for m in own for b in outward[m]
                       if b in boundaries and b not in members}
            # Nearest only: one with another of them between it and the set
            # is pinned by that one and would block the move.
            nearest = {b for b in outside
                       if not any(o in outside for o in
                                  (self.above if outward is self.below else self.below)[b])}
            if nearest:
                variants.append(sorted(members | nearest))
        return variants

    def peak_gaps(self, params: List[str]) -> np.ndarray:
        """
        The distances, in cells, between the calibration peaks of the dates in
        `params` -- the jumps that carry a group from one of its lobes to
        another. Cached per set.

        WIDE_MOVE_YEARS is a guess at the spacing of calibration wiggles, and
        a fixed Gaussian at that scale proposes a 40-year jump often and an
        80-year one rarely, wherever the lobes actually are. The dates
        themselves say where: each one's (inlier) likelihood has its peaks,
        and a phase's dates on one stretch of curve share the gaps between
        them. Each gap is a fixed distance proposed either way, so the
        proposal stays symmetric whatever state it is made from.
        """
        key = tuple(params)
        cache = self.__dict__.setdefault('_peak_gaps', {})
        if key in cache:
            return cache[key]
        from scipy.signal import find_peaks
        counts: Dict[int, int] = {}
        bin_cells = max(1, int(round(PEAK_JUMP_JITTER_YEARS / self.dt)))
        for param in params:
            if param not in self.likelihood:
                continue
            row = self._inlier_row(param)
            peak = float(row.max())
            if peak <= 0:
                continue
            found, _ = find_peaks(row, prominence=PEAK_PROMINENCE * peak)
            for a, b in itertools.combinations(found, 2):
                gap = int(round(abs(int(b) - int(a)) / bin_cells)) * bin_cells
                if gap > 2 * bin_cells:
                    counts[gap] = counts.get(gap, 0) + 1
        common = sorted(counts, key=lambda g: (-counts[g], g))[:MAX_PEAK_GAPS]
        cache[key] = np.array(sorted(common), dtype=np.int64)
        return cache[key]

    def _inlier_row(self, date_id: str) -> np.ndarray:
        """A date's likelihood without its outlier component, which is broad
        and has no lobes of its own."""
        return (self.outlier_parts[date_id][0] if date_id in self.outlier_parts
                else self.likelihood[date_id])

    @staticmethod
    def _lobes_of(row: np.ndarray) -> List[Tuple[int, float, int, int]]:
        """
        (peak cell, share of `row`'s total, first cell, one past the last) for
        each calibration lobe holding at least LOBE_MIN_MASS of it, in cell
        order. A lobe runs from the valley before its peak to the one after, so
        the shares add to one.
        """
        from scipy.signal import find_peaks
        total, top = float(row.sum()), float(row.max())
        if total <= 0 or top <= 0:
            return []
        peaks, _ = find_peaks(row, prominence=PEAK_PROMINENCE * top)
        if len(peaks) == 0:
            return []
        valleys = [int(a) + int(np.argmin(row[a:b + 1]))
                   for a, b in zip(peaks[:-1], peaks[1:])]
        edges = [0] + valleys + [len(row)]
        return [(int(peak), float(row[lo:hi].sum()) / total, lo, hi)
                for peak, lo, hi in zip(peaks, edges[:-1], edges[1:])
                if row[lo:hi].sum() / total >= LOBE_MIN_MASS]

    def lobes(self, date_id: str) -> List[Tuple[int, float, int, int]]:
        """
        The calibration lobes worth starting a chain in, as (peak cell, mass,
        first cell, one past the last), or [] unless the date has two or more
        -- one lobe is where the mode already is. Cached.
        """
        cache = self.__dict__.setdefault('_lobes', {})
        if date_id not in cache:
            found = self._lobes_of(self._inlier_row(date_id))
            cache[date_id] = found if len(found) >= 2 else []
        return cache[date_id]

    def start_lobes(self, rank: int, ranks: int,
                    strategy: str = 'position', seed: int = 0
                    ) -> Dict[str, List[Tuple[Tuple[int, float, int, int], Optional[int]]]]:
        """
        For each date that has several calibration lobes, its lobes in the order
        chain `rank` of the `ranks` that do not start at the modes would like
        to start in them, first choice first, each with the cell in it the
        chain would like (None for the lobe's peak). Chain 0 sits in the modes.

        The strategies differ in what they hold the same from date to date, which
        is what keeps a start feasible under the stratigraphic order and lets a
        phase's dates, which calibrate on the same stretch of curve, find their
        other lobe together:

        position   the same FRACTION f = (rank + 1/2) / ranks of the way along
                   every date's lobes, oldest first, then the lobes nearest it.
                   By position, not mass, so a light lobe is started in as often
                   as a heavy one; three chains cover every lobe of a date with
                   up to three.
        calendar   the same fraction of the way along the range of calendar ages
                   its GROUP's lobe peaks span, each date taking its lobe
                   nearest that age. Dates with different numbers of lobes then
                   still start together.
        equalised  position, but through a likelihood re-weighted to give every
                   lobe equal mass, so the chain starts part-way into the lobe
                   rather than at its peak.
        random     position, with each group's order of chains shifted by a
                   draw of `seed`, so different groups are in different lobes
                   in one chain: the combinations a single fraction never
                   visits. Still stratified within each group.

        Only a wish: a lobe may not be open to a date once its neighbours are
        placed, and initial_state takes the first that is.
        """
        if strategy not in START_STRATEGIES or strategy == 'quantile':
            raise ValueError(f'no lobe start for strategy {strategy!r}')
        groups = [dates for _o, _y, dates in self.group_dates]
        grouped = {d for dates in groups for d in dates}
        groups += [[d] for d in self.date_ids if d not in grouped]

        wishes = {}
        for index, dates in enumerate(groups):
            members = [d for d in dates if self.lobes(d)]
            if not members:
                continue
            f = (rank + 0.5) / ranks
            if strategy == 'random':
                shift = int(np.random.default_rng([seed, index]).integers(ranks))
                f = (((rank + shift) % ranks) + 0.5) / ranks
            if strategy == 'calendar':
                peaks = [lobe[0] for d in members for lobe in self.lobes(d)]
                when = min(peaks) + f * (max(peaks) - min(peaks))
            for date_id in members:
                lobes = self.lobes(date_id)
                if strategy == 'calendar':
                    order = sorted(range(len(lobes)),
                                   key=lambda i: (abs(lobes[i][0] - when), i))
                else:
                    first = min(len(lobes) - 1, int(len(lobes) * f))
                    order = sorted(range(len(lobes)), key=lambda i: (abs(i - first), i))
                targets = {i: None for i in order}
                if strategy == 'equalised':
                    inside = min(max(len(lobes) * f - first, EQUALISED_EDGE),
                                 1 - EQUALISED_EDGE)
                    row = self._inlier_row(date_id)
                    for i, (_p, _m, lo, hi) in enumerate(lobes):
                        cdf = np.cumsum(row[lo:hi])
                        targets[i] = lo + int(np.searchsorted(cdf, inside * cdf[-1]))
                wishes[date_id] = [(lobes[i], targets[i]) for i in order]
        return wishes

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
        gaps = self.peak_gaps(params) if wide else ()
        if len(gaps) and rng.random() < PEAK_JUMP_FRACTION:
            # A jump the size of a gap between calibration peaks, either way,
            # blurred a little: symmetric, so the ratio is still the posterior
            # ratio alone.
            jitter = int(round(PEAK_JUMP_JITTER_YEARS / self.dt))
            steps = (int(gaps[rng.integers(len(gaps))]) * (1 if rng.random() < 0.5 else -1)
                     + int(rng.integers(-jitter, jitter + 1)))
        else:
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


class _Chain:
    """
    One chain, resumable: the state run() used to keep in local variables,
    held so a caller can drive it a burn-in chunk or a sampling round at a
    time and read the histograms as they accumulate.

    Split out of run() for the shared stopping rule (solve_chains): several
    chains in worker processes each advance one round on command, and the
    PARENT decides when to stop from their pooled histograms. run() drives a
    single instance the same way and is bit-for-bit the run it was.
    """

    def __init__(self, model: Model, sweeps: int, seed: int,
                 burn_in: Optional[int], max_sweeps: Optional[int],
                 collect_draws: int, start_quantile: Optional[float] = None,
                 start_lobe: Optional[Tuple] = None):
        self.model = model
        self.rng = np.random.default_rng(seed)
        # Where in the dates' likelihoods the chain starts; None is the mode.
        # See Model.initial_state and solve_chains.
        self.state = model.initial_state(start_quantile, start_lobe)
        self.burn_in = int(sweeps * BURN_IN_FRACTION) if burn_in is None else burn_in
        self.max_sweeps = max(sweeps, MAX_SWEEPS) if max_sweeps is None else max_sweeps

        self.move_sets = model.move_sets()
        # One proposal scale per move set, in grid steps, adapted during
        # burn-in only: after that the kernel is fixed, so the accumulated
        # chain is a valid MCMC run rather than one whose transition rule
        # kept changing.
        self.scales = [max(1.0, GROUP_SHIFT_YEARS / model.dt) for _ in self.move_sets]
        self.proposed = [0] * len(self.move_sets)
        self.accepted = [0] * len(self.move_sets)
        self.since_adapt = [0] * len(self.move_sets)
        self.took_since_adapt = [0] * len(self.move_sets)
        self.swept = 0

        # Accumulated in rounds rather than one total, so the run can be
        # EXTENDED and the first-half/second-half convergence comparison
        # recomputed over whatever length it ended up being. Halving pairs of
        # rounds when there are too many keeps the memory flat however long
        # it runs.
        self.rounds: List[Dict[str, np.ndarray]] = []
        # How often each shared scale node was visited, which IS its
        # posterior -- directly comparable to the U(0,n) posterior OxCal
        # exports. Not split into rounds: it plays no part in the convergence
        # measure, whose job is the calendar parameters.
        self.scale_counts = {m: np.zeros(len(model.scale_grid[m]))
                             for m in model.outlier_models}
        # How often each deposition sequence visited each rigidity, which IS
        # k's posterior. Reported rather than kept internal: a high-k model
        # interpolates confidently straight through a plateau and reports a
        # precision the dates do not support, so the honest instrument shows
        # the rigidity it inferred and says when the prior is doing the work.
        self.rate_counts = {d.element_id: np.zeros(len(d.k_nodes))
                            for d in model.depositions}
        # P(outlier) accumulated as a running mean of the outlier component's
        # responsibility. With the scale sampled, that responsibility depends
        # on it, so it has to be read while the state is known rather than
        # derived from the reported density afterwards.
        self.outlier_total = {d: 0.0 for d in model.likelihood_at}
        self.accumulated = 0
        # Whole states, one array per round so they fold alongside the
        # histograms. Indices rather than years: that is what the state
        # already holds, and an int32 per parameter per draw keeps this a few
        # megabytes at the sizes involved.
        self.per_round_draws = max(1, collect_draws // ROUNDS) if collect_draws else 0
        self.round_draws: List[np.ndarray] = []
        self.round_length = max(1, (sweeps - self.burn_in) // ROUNDS)

    def sweep(self) -> None:
        model, state, rng = self.model, self.state, self.rng
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

        # Each deposition sequence's rigidity, the same way. A fixed k makes
        # this a no-op rather than a branch at the call site.
        for deposition in model.depositions:
            state.rate[deposition.element_id] = model.draw_deposition_rate(
                deposition, state, float(rng.random()))

        # Translation moves. The Gibbs sweep above cannot move a correlated
        # block as a unit -- see Model.move_sets. `state` is rebound by them,
        # not just mutated.
        current_lp = None
        for index, params in enumerate(self.move_sets):
            for _ in range(MOVES_PER_SWEEP):
                state, current_lp, took, wide = model.shift(
                    state, rng, params, self.scales[index], current_lp)
                self.proposed[index] += 1
                self.accepted[index] += int(took)
                if not wide:
                    self.since_adapt[index] += 1
                    self.took_since_adapt[index] += int(took)

            if self.swept < self.burn_in and self.since_adapt[index] >= ADAPT_INTERVAL:
                rate = self.took_since_adapt[index] / self.since_adapt[index]
                # Standard random-walk tuning, floored at one grid step since
                # a proposal rounding to zero can never move anything.
                self.scales[index] = max(1.0, self.scales[index] * (
                    1.3 if rate > TARGET_ACCEPTANCE else 0.75))
                self.since_adapt[index] = self.took_since_adapt[index] = 0
        self.state = state
        self.swept += 1

    def burn(self, count: int) -> None:
        """`count` sweeps with nothing accumulated."""
        for _ in range(count):
            self.sweep()

    def round(self) -> Dict[str, np.ndarray]:
        """
        One sampling round: round_length sweeps accumulated into a fresh
        histogram per parameter, which is appended to the rounds and also
        returned (as its own copy) for a caller pooling rounds across chains.
        Folds the rounds when there are too many, doubling the round length.
        """
        model, state = self.model, self.state
        current = {p: np.zeros(model.M) for p in model.params}
        # Recomputed each time because round_length doubles whenever rounds
        # are folded, so a round keeps about per_round_draws states however
        # long the run gets.
        stride = (max(1, self.round_length // self.per_round_draws)
                  if self.per_round_draws else 0)
        kept_states: List[List[int]] = []
        for step in range(self.round_length):
            self.sweep()
            state = self.state
            for param in model.params:
                current[param][state.index[param]] += 1.0
            for outlier_model in model.outlier_models:
                self.scale_counts[outlier_model][state.scale[outlier_model]] += 1.0
            for deposition in model.depositions:
                self.rate_counts[deposition.element_id][
                    state.rate[deposition.element_id]] += 1.0
            for date_id in self.outlier_total:
                node = model._scale_of(date_id, state)
                self.outlier_total[date_id] += float(
                    model.responsibility_at[date_id][node][state.index[date_id]])
            if stride and step % stride == 0:
                # Each outlier model's scale node rides along with the ages:
                # a flagged date's agreement is scored under the scale its own
                # state held (services/agreement.py).
                kept_states.append([state.index[p] for p in model.params]
                                   + [state.scale[m] for m in model.outlier_models])
            self.accumulated += 1
        self.rounds.append(current)
        if self.per_round_draws:
            self.round_draws.append(np.array(kept_states, dtype=np.int32).reshape(
                len(kept_states), len(model.params) + len(model.outlier_models)))

        if len(self.rounds) > 2 * ROUNDS:
            self.rounds = _folded(self.rounds, model.params)
            if self.per_round_draws:
                # The same fold, thinned back down so a folded round holds
                # no more states than an unfolded one.
                folded = []
                for i in range(0, len(self.round_draws) - 1, 2):
                    folded.append(np.concatenate(
                        [self.round_draws[i], self.round_draws[i + 1]])[::2])
                if len(self.round_draws) % 2:
                    folded.append(self.round_draws[-1])
                self.round_draws = folded
            self.round_length *= 2
        return {p: h.copy() for p, h in current.items()}

    def convergence(self) -> Tuple[Dict[str, float], float, int]:
        """(per-parameter measure, the worst of them, rounds it was read over)."""
        kept = _after_burn_in(self.rounds)
        measure = _convergence(kept, self.model.params)
        worst = min(measure.values()) if measure else 0.0
        return measure, worst, len(kept)

    def result(self, convergence: Dict[str, float]) -> Dict:
        model = self.model
        kept_rounds = _after_burn_in(self.rounds)
        counts = {p: sum(r[p] for r in kept_rounds) for p in model.params}
        # The same rounds the histograms come from, so the two views cannot
        # describe different stretches of the run.
        kept_draws = (self.round_draws[_burn_in_start(len(self.rounds)):]
                      if self.round_draws else [])
        accumulated = self.accumulated
        return {
            'histograms': counts,
            'rate_histograms': self.rate_counts,
            'draws': (np.concatenate([d for d in kept_draws if len(d)])
                      if any(len(d) for d in kept_draws) else None),
            'draw_params': list(model.params),
            # The columns after draw_params: one scale node per outlier model.
            'draw_scale_models': list(model.outlier_models),
            'scale_histograms': self.scale_counts,
            'outlier_probability': {d: (v / accumulated if accumulated else 0.0)
                                    for d, v in self.outlier_total.items()},
            'sweeps': self.swept,
            'sweeps_kept': int(sum(counts[model.params[0]])) if model.params else 0,
            'proposed_moves': sum(self.proposed),
            'accepted_moves': sum(self.accepted),
            # Per move set, so a set that never gets accepted is visible
            # rather than averaged away.
            'acceptance': [a / p if p else 0.0 for a, p in zip(self.accepted, self.proposed)],
            'scales': self.scales,
            'convergence': convergence,
        }


def _folded(rounds: List[Dict[str, np.ndarray]], params: List[str]
            ) -> List[Dict[str, np.ndarray]]:
    """
    Adjacent pairs of rounds added together: the same accumulated
    distribution, half the bookkeeping, and the halves stay comparable. In
    place on the even members. Shared by a chain and by the pooled rounds
    solve_chains keeps, which must fold in step with it.
    """
    merged = []
    for i in range(0, len(rounds) - 1, 2):
        for param in params:
            rounds[i][param] += rounds[i + 1][param]
        merged.append(rounds[i])
    if len(rounds) % 2:
        merged.append(rounds[-1])
    return merged


def _burn_chunks(burn_in: int) -> List[int]:
    """
    Burn-in split into ten-ish chunks, so progress is reported and a cancel
    is seen during it: on a slow model burn-in is several seconds during
    which the bar would otherwise sit at zero.
    """
    chunk = max(1, burn_in // 10)
    chunks = [chunk] * (burn_in // chunk)
    if burn_in % chunk:
        chunks.append(burn_in % chunk)
    return chunks


def run(model: Model, sweeps: int = DEFAULT_SWEEPS, seed: int = 42,
        burn_in: Optional[int] = None,
        target_convergence: float = TARGET_CONVERGENCE,
        max_sweeps: Optional[int] = None,
        collect_draws: int = 0,
        on_progress: Optional[Callable[[Dict], bool]] = None,
        start_quantile: Optional[float] = None,
        start_lobe: Optional[Tuple] = None) -> Dict:
    """
    Sweep the sampler and accumulate each parameter's marginal as a histogram
    over the calendar grid, extending the run until it has converged.

    `start_quantile` is where the chain starts (Model.initial_state); None,
    the likelihood modes, is the single-chain run and chain 0 of any wider one.
    `start_lobe` is (rank, ranks) among the chains that do not start at the
    modes, which decides the lobe it starts in for each date that has several;
    see Model.start_lobes.

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
    chain = _Chain(model, sweeps, seed, burn_in, max_sweeps, collect_draws,
                   start_quantile, start_lobe)

    def report(phase: str, convergence: float) -> None:
        if on_progress is None:
            return
        if not on_progress({'phase': phase, 'sweeps': chain.swept,
                            'max_sweeps': chain.max_sweeps, 'convergence': convergence}):
            raise Cancelled()

    burn_chunk = max(1, chain.burn_in // 10)
    for count in _burn_chunks(chain.burn_in):
        chain.burn(count)
        if chain.swept % burn_chunk == 0:
            report('burn_in', 0.0)

    while True:
        chain.round()
        convergence, worst, kept = chain.convergence()
        report('sampling', worst)
        if kept < 2:
            continue
        if worst >= target_convergence or chain.swept >= chain.max_sweeps:
            break

    return chain.result(convergence)


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


# --------------------------------------------------------------------------
# Several chains at once
# --------------------------------------------------------------------------
#
# The sampler is one Python thread doing ~160 us of work per sweep, and a
# solve is one job at a time (services/jobs.py). The machine's other cores sit
# idle for the whole run. Independent chains in separate processes use them
# with no change to the sampler: each process builds its own Model and holds a
# _Chain, which it advances one burn-in chunk or one sampling round at a time
# on command from the parent, exactly as run() advances a single one.
#
# ONE STOPPING RULE FOR ALL OF THEM. The parent pools each round across the
# chains and applies the first-half/second-half measure to the POOLED rounds,
# so the run stops when the pooled histogram has converged, not when each
# chain has on its own. The deficit in that measure is mostly Monte Carlo
# noise, which falls as 1/sqrt(draws), and four chains reach a given deficit
# in about a quarter of the sweeps each -- measured on the Nenthorn fixture as
# 54,000 sweeps on one chain against about 14,000 each on four. That is what
# turns the idle cores into wall time.
#
# The pooled measure is not weaker than the single-chain one: it compares the
# same number of draws either side, drawn from four starts rather than one
# stretch of one walk. What it cannot see is a chain stuck in a mode the
# others found, so the between-chain measures (_between_chains) are reported
# beside it and the headline convergence is the worse of the two. Pooling
# assumes every chain has mixed; a chain stuck in a minor mode contributes it
# at 1/chains weight -- wrong, but no more wrong than the single chain that
# would have reported that mode at weight one or zero, and unlike that chain
# it is REPORTED.
#
# Processes rather than threads, because the work holds the GIL; spawned
# rather than forked, because the parent is a multithreaded server (uvicorn's
# loop, the job thread) and forking one is unsafe. A worker imports numpy,
# scipy and this module once and is then reused across solves. It never
# imports the curve registry: the curves it needs come with the build command
# and go into calibration.preload_curves, so a worker never opens a database
# connection.


def _chain_server(conn) -> None:
    """
    A worker process's whole life: hold one _Chain at a time and do what the
    parent says. Module-level so the spawn start method can import it.

    Every command is short and returns, so the parent is never waiting on a
    worker it cannot interrupt: a cancel is simply the parent not sending the
    next command and dropping the chain.
    """
    chain: Optional[_Chain] = None
    while True:
        try:
            sequence, command, payload = conn.recv()
        except EOFError:
            return
        try:
            if command == 'build':
                calibration.preload_curves(payload['curves'])
                model = Model(payload['measurements'], payload['root'],
                              grid=payload['grid'],
                              uniform_span_prior=payload['uniform_span_prior'])
                chain = _Chain(model, payload['sweeps'], payload['seed'],
                               None, payload['max_sweeps'], payload['collect_draws'],
                               payload.get('start_quantile'),
                               payload.get('start_lobe'))
                reply = {'params': list(model.params), 'burn_in': chain.burn_in,
                         'max_sweeps': chain.max_sweeps}
            elif command == 'burn':
                chain.burn(payload)
                reply = chain.swept
            elif command == 'round':
                reply = {'histogram': chain.round(), 'swept': chain.swept}
            elif command == 'finish':
                measure, _worst, _kept = chain.convergence()
                reply = chain.result(measure)
                chain = None
            elif command == 'drop':
                chain = None
                reply = None
            elif command == 'exit':
                return
            else:
                raise ValueError(f'unknown chain command {command!r}')
        except BaseException as error:           # noqa: BLE001 - relayed, not swallowed
            chain = None
            try:
                conn.send((sequence, 'error', error))
            except Exception:                    # an exception that will not pickle
                conn.send((sequence, 'error',
                           RuntimeError(f'{type(error).__name__}: {error}')))
            continue
        conn.send((sequence, 'ok', reply))


class _ChainWorker:
    """
    The parent's handle on one worker process.

    Every command carries a sequence number the worker echoes, and recv()
    discards replies to anything but the command it is waiting on. The pipe
    is otherwise a strict alternation, and one missed recv -- an exception
    between a send and its recv -- would leave a stale reply at the head of
    the pipe to be read as the answer to the NEXT solve's first command.
    """

    def __init__(self, context):
        self.conn, child = context.Pipe()
        self.process = context.Process(target=_chain_server, args=(child,),
                                       name='radical-chain', daemon=True)
        self.process.start()
        child.close()
        self.busy = False
        self.sequence = 0

    def send(self, command: str, payload=None) -> None:
        self.sequence += 1
        self.conn.send((self.sequence, command, payload))

    def recv(self):
        while True:
            try:
                sequence, status, reply = self.conn.recv()
            except EOFError:
                raise RuntimeError('a sampler worker process died') from None
            if sequence != self.sequence:
                continue                         # stale: see the class docstring
            if status == 'error':
                raise reply
            return reply

    def call(self, command: str, payload=None):
        self.send(command, payload)
        return self.recv()

    @property
    def alive(self) -> bool:
        return self.process.is_alive()


_workers: List[_ChainWorker] = []
_workers_lock = threading.Condition()


def worker_cap() -> int:
    """How many chains can run at once on this machine."""
    return max(1, min(MAX_CHAIN_WORKERS, os.cpu_count() or 1))


def _acquire(count: int) -> List[_ChainWorker]:
    """
    `count` idle workers, started as needed up to worker_cap(). Blocks while
    another solve holds too many; jobs run one at a time in production, so
    that is only ever the test suite running two solves from two threads.
    """
    context = multiprocessing.get_context('spawn')
    with _workers_lock:
        while True:
            _workers[:] = [w for w in _workers if w.alive]
            idle = [w for w in _workers if not w.busy]
            while len(idle) < count and len(_workers) < worker_cap():
                worker = _ChainWorker(context)
                _workers.append(worker)
                idle.append(worker)
            if len(idle) >= count:
                taken = idle[:count]
                for worker in taken:
                    worker.busy = True
                return taken
            _workers_lock.wait()


def _release(workers: List[_ChainWorker]) -> None:
    with _workers_lock:
        for worker in workers:
            worker.busy = False
        _workers_lock.notify_all()


def _curve_payload(measurements: List[Dict]) -> Dict[str, calibration.CalibrationCurve]:
    """Every curve the measurements use, resolved HERE so a worker need not."""
    ids = {m.get('curve_id') or 'intcal20' for m in measurements}
    return {curve_id: calibration.get_curve(curve_id) for curve_id in ids}


def start_quantiles(chains: int, seed: int) -> List[Optional[float]]:
    """
    Where each chain of a multi-chain run starts, as the quantile of the
    dates' likelihoods Model.initial_state places them at: None (the mode)
    for chain 0, so it stays the single-chain run, and one draw from each of
    `chains - 1` equal strata of START_QUANTILE_RANGE for the rest.

    Stratified rather than independent so the starts are spread whatever the
    seed: with three draws left to chance, two or three of them land on the
    same side of a date's likelihood about half the time, and a calibration
    range on the other side is then found by one chain or none.
    """
    if chains <= 1:
        return [None] * chains
    rng = np.random.default_rng(seed)
    low, high = START_QUANTILE_RANGE
    width = (high - low) / (chains - 1)
    return [None] + [float(low + (c + rng.random()) * width)
                     for c in range(chains - 1)]


def chain_starts(chains: int, seed: int,
                 strategy: str = DEFAULT_START_STRATEGY) -> List[Dict]:
    """
    run()'s start arguments for each chain of a multi-chain run: chain 0 at the
    modes, the rest at a stratified quantile (start_quantiles) and, for dates
    with several calibration lobes, in a lobe chosen by `strategy`
    (Model.start_lobes; 'quantile' leaves them to the quantile).
    """
    if strategy not in START_STRATEGIES:
        raise ValueError(f'unknown start strategy {strategy!r}')
    return [{'start_quantile': quantile,
             'start_lobe': (None if c == 0 or strategy == 'quantile'
                            else (c - 1, chains - 1, strategy, seed))}
            for c, quantile in enumerate(start_quantiles(chains, seed))]


def solve_chains(measurements: List[Dict], root: Dict, chains: int,
                 sweeps: int = DEFAULT_SWEEPS, seed: int = 42,
                 uniform_span_prior: bool = True,
                 grid: Optional[np.ndarray] = None,
                 target_convergence: float = TARGET_CONVERGENCE,
                 max_sweeps: Optional[int] = None,
                 collect_draws: int = 0,
                 on_progress: Optional[Callable[[Dict], bool]] = None,
                 start_strategy: str = DEFAULT_START_STRATEGY) -> Dict:
    """
    run() over `chains` chains in parallel worker processes, under ONE
    stopping rule read off their pooled rounds, returned as one result of
    run()'s shape plus the between-chain diagnostics.

    Chain c is seeded with seed + c, so chain 0 of a multi-chain run is the
    single-chain run and every chain is reproducible on its own. The other
    chains also START elsewhere -- see start_quantiles -- since R-hat can only
    report a calibration range some chain is actually in. All chains
    advance in step -- the same burn-in, one round at a time -- and stop
    together when the pooled first-half/second-half measure reaches
    `target_convergence` or each has run `max_sweeps`.

    `chains` is capped at worker_cap(); the count actually run is reported
    as `chains`.

    `on_progress` sees the chains as one run: sweeps summed, the cap summed,
    the convergence the pooled measure's worst parameter. Returning False
    stops every chain and raises Cancelled.
    """
    if chains < 1:
        raise ValueError('chains must be at least 1')
    chains = min(chains, worker_cap())
    if max_sweeps is None:
        max_sweeps = max(sweeps, MAX_SWEEPS)
    curves = _curve_payload(measurements)

    workers = _acquire(chains)
    holding = list(workers)          # workers still holding a chain to drop

    def everyone(command: str, payloads) -> list:
        """
        The command to every worker, then every reply. Every reply is
        collected even once one has failed, so no pipe is left with an
        unread answer in it; the first failure is then raised.
        """
        for worker, payload in zip(workers, payloads):
            worker.send(command, payload)
        replies, failures = [], []
        for worker in workers:
            try:
                replies.append(worker.recv())
            except Exception as error:           # noqa: BLE001 - re-raised below
                failures.append(error)
        if failures:
            raise failures[0]
        return replies

    def report(phase: str, swept_each: List[int], convergence: float) -> None:
        if on_progress is None:
            return
        if not on_progress({'phase': phase, 'sweeps': sum(swept_each),
                            'max_sweeps': max_sweeps * chains,
                            'convergence': convergence, 'chains': chains}):
            raise Cancelled()

    try:
        built = everyone('build', [{
            'measurements': measurements, 'root': root, 'grid': grid,
            'uniform_span_prior': uniform_span_prior, 'sweeps': sweeps,
            'seed': seed + c, 'max_sweeps': max_sweeps,
            'collect_draws': collect_draws, 'curves': curves,
            **start,
        } for c, start in enumerate(chain_starts(chains, seed, start_strategy))])
        params: List[str] = built[0]['params']
        burn_in: int = built[0]['burn_in']

        burn_chunk = max(1, burn_in // 10)
        for count in _burn_chunks(burn_in):
            swept = everyone('burn', [count] * chains)
            if swept[0] % burn_chunk == 0:
                report('burn_in', swept, 0.0)

        # Each chain's rounds, mirrored here and folded in step with the
        # chain's own, so the pooled halves and the between-chain figures
        # can be read every round without shipping histories back and forth.
        per_chain: List[List[Dict[str, np.ndarray]]] = [[] for _ in range(chains)]
        while True:
            replies = everyone('round', [None] * chains)
            swept = [r['swept'] for r in replies]
            for rounds, reply in zip(per_chain, replies):
                rounds.append(reply['histogram'])
                if len(rounds) > 2 * ROUNDS:
                    rounds[:] = _folded(rounds, params)
            kept = [_after_burn_in(rounds) for rounds in per_chain]
            pooled = [{p: sum(r[p] for r in same) for p in params}
                      for same in zip(*kept)]
            convergence = _convergence(pooled, params)
            worst = min(convergence.values()) if convergence else 0.0
            report('sampling', swept, worst)
            if len(pooled) < 2:
                continue
            if min(swept) >= max_sweeps:
                break
            if worst < target_convergence:
                continue
            # Pooled halves agree; now the chains must agree with each
            # other, or a chain stuck in a lobe would be pooled in silence.
            totals = [{p: sum(r[p] for r in rounds) for p in params} for rounds in kept]
            _overlap, rhat = _between_chains(totals, params)
            if rhat is not None and rhat <= TARGET_RHAT:
                break

        results = everyone('finish', [None] * chains)
        holding = []
    finally:
        for worker in holding:
            try:
                worker.call('drop')
            except Exception:                # a dead worker is dropped below
                pass
        _release(workers)

    combined = _pool_chains(results)
    # The stopping rule's own measure, in place of the per-chain ones; each
    # chain's is kept beside it for the record.
    combined['per_chain_convergence'] = [
        min(r['convergence'].values()) if r['convergence'] else 0.0 for r in results]
    combined['convergence'] = convergence
    return combined


def _pool_chains(results: List[Dict]) -> Dict:
    """
    The chains' results as one result in run()'s shape.

    Histograms and every other count add. The outlier probabilities are
    per-chain means, so they are combined weighted by how many sweeps each
    chain kept. Acceptance rates are averaged per move set.
    """
    params = list(results[0]['histograms'])
    kept = np.array([r['sweeps_kept'] for r in results], dtype=float)
    kept_total = float(kept.sum())

    histograms = {p: sum(r['histograms'][p] for r in results) for p in params}
    scale_histograms = {m: sum(r['scale_histograms'][m] for r in results)
                        for m in results[0]['scale_histograms']}
    rate_histograms = {d: sum(r['rate_histograms'][d] for r in results)
                       for d in results[0]['rate_histograms']}
    outlier_probability = {
        d: (float(sum(r['outlier_probability'][d] * k for r, k in zip(results, kept))
                  / kept_total) if kept_total else 0.0)
        for d in results[0]['outlier_probability']
    }
    draws = [r['draws'] for r in results if r['draws'] is not None]
    within = {p: min(r['convergence'].get(p, 0.0) for r in results) for p in params}
    between, rhat = _between_chains([r['histograms'] for r in results], params)

    return {
        'histograms': histograms,
        'rate_histograms': rate_histograms,
        'draws': np.concatenate(draws) if draws else None,
        'draw_params': results[0]['draw_params'],
        'draw_scale_models': results[0]['draw_scale_models'],
        'scale_histograms': scale_histograms,
        'outlier_probability': outlier_probability,
        'sweeps': int(sum(r['sweeps'] for r in results)),
        'sweeps_per_chain': [int(r['sweeps']) for r in results],
        'sweeps_kept': int(kept_total),
        'proposed_moves': sum(r['proposed_moves'] for r in results),
        'accepted_moves': sum(r['accepted_moves'] for r in results),
        'acceptance': [float(np.mean(rates)) for rates in
                       zip(*(r['acceptance'] for r in results))],
        'scales': results[0]['scales'],
        'convergence': within,
        'chains': len(results),
        'between_chain_convergence': between,
        'rhat': rhat,
    }


def _between_chains(histograms: List[Dict[str, np.ndarray]], params: List[str]
                    ) -> Tuple[Optional[float], Optional[float]]:
    """
    How far the chains agree with each other, from one histogram per chain
    per parameter: (worst overlap of any chain's histogram with the pooled
    one, worst Gelman-Rubin R-hat), or (None, None) with a single chain.

    R-HAT IS THE GATE (TARGET_RHAT). It is the standard between/within
    variance ratio, computed from each chain's histogram mean and variance
    with its draws as n; 1.0 is agreement, and a chain settled in a
    calibration lobe the others left gives values in the tens.

    THE OVERLAP IS INFORMATION. It is _convergence's measure with a chain in
    place of a half, so it reads on the same scale -- but it is noisier by
    construction, since one chain holds a fraction of the pooled draws, so
    it cannot be held to the pooled target: at 0.95 on the pooled halves it
    sits at about 0.93-0.94 on four chains that agree perfectly by R-hat.
    Reported so the two views can be read together, not gated on.
    """
    if len(histograms) < 2:
        return None, None
    grid_index = None
    worst_overlap = 1.0
    # None once any parameter has no within-chain variance to compare
    # against: R-hat is then undefined rather than infinite, and None is what
    # survives JSON where an infinity does not.
    worst_rhat: Optional[float] = 1.0
    for p in params:
        chains = [h[p] for h in histograms]
        totals = np.array([c.sum() for c in chains])
        if np.any(totals <= 0):
            return 0.0, None
        pooled = sum(chains)
        pooled = pooled / pooled.sum()
        for c, total in zip(chains, totals):
            worst_overlap = min(worst_overlap, float(np.minimum(c / total, pooled).sum()))

        if grid_index is None:
            grid_index = np.arange(len(pooled), dtype=float)
        means = np.array([(grid_index * c).sum() / t for c, t in zip(chains, totals)])
        variances = np.array([((grid_index - m) ** 2 * c).sum() / t
                              for c, t, m in zip(chains, totals, means)])
        n = float(totals.mean())
        within = float(variances.mean())
        between = n * float(means.var(ddof=1))
        if worst_rhat is None:
            continue
        if within > 0:
            estimate = (n - 1.0) / n * within + between / n
            worst_rhat = max(worst_rhat, float(np.sqrt(estimate / within)))
        elif between > 0:
            worst_rhat = None
    return worst_overlap, worst_rhat


def _deposition_report(model: 'Model', chain: Dict) -> List[Dict]:
    """
    What each deposition sequence inferred about its own rigidity.

    Reported alongside the ages rather than kept internal. A high-k model
    interpolates confidently straight through a plateau and reports a
    precision the dates do not support, so an age-depth answer is only
    readable next to the rigidity it was produced under -- and `pinned` says
    when the posterior has run up against the prior's own bound, which is the
    case where the prior rather than the data is doing the work.
    """
    report = []
    for deposition in model.depositions:
        entry: Dict = {
            'element_id': deposition.element_id,
            'name': deposition.name,
            'depth_units': deposition.depth_units,
            'levels': len(deposition.ids),
            'variable': deposition.variable,
            'log_k_min': deposition.log_k_min,
            'log_k_max': deposition.log_k_max,
        }
        if not deposition.variable:
            entry['k_mean'] = float(deposition.fixed_k)
            entry['pinned'] = None
            report.append(entry)
            continue

        visits = chain['rate_histograms'].get(deposition.element_id)
        total = float(visits.sum()) if visits is not None else 0.0
        if total <= 0:
            report.append(entry)
            continue
        nodes = np.log10(deposition.k_nodes)
        weights = visits / total
        mean = float((nodes * weights).sum())
        cdf = np.cumsum(weights)
        entry.update({
            'log10_k_mean': mean,
            'k_mean': float(10.0 ** mean),
            'log10_k_hpd_68': (float(np.interp(0.16, cdf, nodes)),
                               float(np.interp(0.84, cdf, nodes))),
            # A tenth of the run spent in the outermost node either side means
            # the answer is leaning on where the prior was cut off.
            'pinned': bool(weights[0] > 0.1 or weights[-1] > 0.1),
        })
        report.append(entry)
    return report


def solve(measurements: List[Dict], root: Dict, sweeps: int = DEFAULT_SWEEPS,
          num_samples: int = 4000, seed: int = 42,
          uniform_span_prior: bool = True,
          grid: Optional[np.ndarray] = None,
          target_convergence: float = TARGET_CONVERGENCE,
          max_sweeps: Optional[int] = None,
          estimate_durations: bool = False,
          dispersal: Optional[Dict] = None,
          interval_draws: int = intervals.DEFAULT_DRAWS,
          on_progress: Optional[Callable[[Dict], bool]] = None,
          chains: int = 1,
          resolution: float = REPORTING_STEP_YEARS) -> Dict:
    """
    Posterior summaries for every date and boundary, in the same shape
    exact_chain.solve returns -- so a caller does not need to know which
    produced it.

    `sweeps` is where the run starts, not where it ends: it extends until the
    convergence measure reaches `target_convergence` or `max_sweeps` (see run).
    Both the achieved convergence and the length are reported, so a run that ran
    out of budget is visible rather than silently under-converged.

    `chains` > 1 runs that many INDEPENDENT chains, each in its own process
    with its own seed and its own self-extending run, and pools their
    histograms (see solve_chains). A single chain runs in this process and is
    bit-for-bit what it always was.

    `grid` is accepted so a caller can force exact_chain's own grid and compare
    the two solvers without a resolution difference confounding the answer.

    `interval_draws` whole states are kept jointly through the run whatever was
    asked for, because Amodel -- OxCal's whole-model agreement index -- is a
    function of every date at once and is always reported; the dates' half is
    returned as `joint_draws` for services/agreement.py to score. Those states
    are an MCMC chain rather than independent draws, so anything read off them
    converges more slowly than the marginals beside it; the run is not extended
    on their account, and Amodel carries the sampling error
    services/agreement.py quantifies.

    `estimate_durations` adds `intervals` as exact_chain.solve's flag does,
    from the same states. Off by default for the same reasons.

    `dispersal` asks for the spatial section and takes the same joint states,
    with the same MCMC qualification on everything read off them.

    `resolution` is the calendar step the HPD ranges and durations are
    reported at, as in exact_chain.solve. It does not touch the run.
    """
    model = Model(measurements, root, grid=grid,
                  uniform_span_prior=uniform_span_prior)
    # Joint states are always kept, because Amodel is always reported and is a
    # function of every date at once (services/agreement.model_agreement).
    # What is still optional is what ELSE is read off them: an age-depth model
    # needs them whether or not durations were asked for, its deliverable BEING
    # a function of several ages at once -- the band, and the accumulation rate
    # between one level and the next. Phase durations stay opt-in, being a
    # modelling claim of their own, and so does a dispersal fit, for the same
    # reason and independently of the other two: a front's speed is a function
    # of many modelled ages at once, and asking for it is not asking for phase
    # durations.
    wants_intervals = (estimate_durations or bool(model.depositions)
                       or dispersal is not None)
    if chains > 1:
        chain = solve_chains(
            measurements, root, chains=chains, sweeps=sweeps, seed=seed,
            uniform_span_prior=uniform_span_prior, grid=model.grid,
            target_convergence=target_convergence, max_sweeps=max_sweeps,
            collect_draws=interval_draws,
            on_progress=on_progress)
    else:
        chain = run(model, sweeps=sweeps, seed=seed,
                    target_convergence=target_convergence, max_sweeps=max_sweeps,
                    collect_draws=interval_draws,
                    on_progress=on_progress)
    rng = np.random.default_rng(seed)

    results: Dict = {'measurements': {}, 'boundaries': {}}
    for date_id in model.date_ids:
        density = chain['histograms'][date_id]
        summary = _summarize(model.grid, density, rng, num_samples, resolution)
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
            'posterior': _summarize(model.grid, density, rng, num_samples, resolution),
        }

    # Undated levels report through the same slot as boundaries: both are
    # modelled parameters with a posterior and no measurement behind them, so
    # they reach the results page, the exports and the model tree the same
    # way. This is what answers "how old is the level at 47 cm".
    for level_id in model.level_ids:
        density = chain['histograms'][level_id]
        if density.sum() <= 0:
            continue
        results['boundaries'][level_id] = {
            'name': model.level_names.get(level_id, 'Undated level'),
            'posterior': _summarize(model.grid, density, rng, num_samples, resolution),
        }

    results['method'] = 'gibbs'
    results['deposition'] = _deposition_report(model, chain)
    results['uniform_span_prior'] = bool(model.span_priors)
    results['sweeps'] = chain['sweeps']
    results['chains'] = chain.get('chains', 1)
    results['sweeps_per_chain'] = chain.get('sweeps_per_chain', [chain['sweeps']])
    # The WORST parameter's, not the average: one badly-mixed boundary is
    # exactly what a mean would hide.
    # The stopping rule's measure: OxCal's first-half/second-half overlap,
    # over the pooled rounds when several chains ran (solve_chains).
    within = (min(chain['convergence'].values()) if chain['convergence'] else 0.0)
    results['within_chain_convergence'] = within
    results['per_chain_convergence'] = chain.get('per_chain_convergence', [within])
    # With several chains, how they agreed with one another: the worst
    # overlap of any chain with the pool (information -- noisier than the
    # pooled measure by construction, see _between_chains) and Gelman-Rubin
    # R-hat, which the stopping rule held to TARGET_RHAT.
    results['between_chain_convergence'] = chain.get('between_chain_convergence')
    rhat = chain.get('rhat')
    results['rhat'] = rhat
    results['rhat_target'] = TARGET_RHAT if rhat is not None else None
    results['convergence'] = within
    # With several chains the run also had to bring R-hat under its target
    # (solve_chains); a run that hit the cap first with the chains still
    # apart is unconverged whatever its pooled overlap says.
    results['converged'] = within >= target_convergence and (
        rhat is None or rhat <= TARGET_RHAT)
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
    # The joint states, which the marginal histograms above cannot stand in
    # for: both Amodel and the duration section are functions of several
    # modelled ages AT ONCE, so they are read off the same states or they are
    # describing different runs.
    results['joint_draws'] = None
    results['joint_scales'] = None
    if chain['draws'] is not None:
        joint = {param: model.grid[chain['draws'][:, column]]
                 for column, param in enumerate(chain['draw_params'])}
        # Each outlier model's shared scale in the same states, as log10
        # years, so a flagged date can be scored under the scale its own
        # state held rather than one averaged over the run.
        first = len(chain['draw_params'])
        results['joint_scales'] = {
            outlier_model: model.scale_grid[outlier_model][chain['draws'][:, first + i]]
            for i, outlier_model in enumerate(chain['draw_scale_models'])}
        # Kept whether or not durations were asked for: Amodel is the headline
        # number a model is screened on, not an extra an analyst should have to
        # opt into. The states cost one list of indices per kept sweep. Scored
        # by services/agreement.py, as exact_chain.solve's are.
        results['joint_draws'] = {d: joint[d] for d in model.date_ids if d in joint}
        # Every other parameter in the same states -- the Boundaries among
        # them -- for a per-phase KDE to reflect at (services/kde.report_phases).
        dates = set(model.date_ids)
        results['joint_boundaries'] = {p: v for p, v in joint.items() if p not in dates}
        if wants_intervals:
            results['intervals'] = intervals.report(
                measurements=measurements, root=root, draws=joint,
                independent=False, durations=estimate_durations,
                dispersal=dispersal, step=resolution)
    return results
