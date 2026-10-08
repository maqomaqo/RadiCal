"""
A direct calculation of a sampled model, to check services/gibbs.py against.

Why a sampled answer needs one
------------------------------
The sampler's own diagnostics are both comparisons of the sampler with itself:
the first half of a run against the second, and (with several chains) one
chain against another. Neither can see a calibration lobe that no chain is in,
and neither can see a target that is wrong in the same way for every chain.
A site model of parallel sequences had both: boundaries allowed to share a grid cell
packed every chain into a late lobe holding under 1% of the posterior, and
four chains agreed on it at R-hat 1.001. Only a calculation that does not
sample can catch that, so this module makes one wherever it is cheap enough
and the results page reports whether the two agree.

Shapes
------
Three, which between them cover what the sampler is most often given:

  * A CHAIN that exact_chain.layout does not recognise because it is written
    with nested Sequences -- one area then another, a Sequence of two Sequences.
    Spelled out in place it is one line of Boundaries and Phases.

  * PARALLEL CHAINS inside an outer pair: a Sequence of Boundary S, a Phase
    whose children are Sequences that are each a chain, and Boundary E -- a
    site's Start and End around several areas. Given S and E the branches are
    independent, so

        p(S, E)  proportional to  C(S, E) T0(S, E) prod_b Z_b(S, E)

    where C is the span correction over the outer pair, T0 the span factor and
    likelihoods of any dates sitting directly in the Phase, and Z_b(S, E) the
    integral of branch b with its first Boundary younger than S and its last
    older than E, a tie with either at half weight. Z_b is a cumulative sum of the branch's
    end-to-end transfer product, so the whole thing is a few matrix products
    per branch. Each branch's interior is then exact_chain's own chain with an
    end-to-end factor W_b in place of its span correction.

  * Either of those with ORDERED BLOCKS of dates inside a pair of Boundaries:
    Phases or Sequences in a Sequence with no Boundary between them, so that
    every date of one is older than every date of the next -- the shape of the
    Nenthorn reference model (tests/oxcal_fixture.py), which the exact solver
    declines ("two groups are ordered
    against each other with no Boundary between them"). The blocks are read off
    the sampler's own ordering (_blocks), and the group's integral over its
    dates (_log_ordered) replaces the product of interval integrals: cut the
    order at the oldest date of each block, and the sum over placements is a
    chain over those cuts, a product of matrices like the one over Boundaries.
    Dates in the same block are unordered, and one block alone is the plain
    unordered group.

  * Any of those with dates OUTSIDE EVERY BOUNDARY at either end: Phases or
    Sequences of dates before the first Boundary of the model's Sequence, or
    after its last. They are ordered against that Boundary but have no span
    term -- their prior is flat, as OxCal's is for a date outside every
    Boundary -- so given the end Boundary F they are independent of the rest,
    and enter as a weight on F alone:

        g(F) = the integral of their likelihoods over placements older than F
               (younger, at the other end), in their own order (_blocks)

    which is the ordered-group integral above with no span factor and the
    grid's edge as the other end. Their outlier components, if they carried
    any, would reach past this module's grid where the sampler's goes further,
    so a date out there with an outlier model is not checked.

Anything else is reported as not checked, with the reason.

The same model, not a neighbour
-------------------------------
A cross-check against a different model reports the difference between the
models, which is not what anyone wants to read. So nothing that defines the
target is written again here: the likelihood at every outlier scale, the
Boundaries' windows and the span-correction rows all come from a gibbs.Model
built on this module's grid, and the interval integrals are exact_chain's,
which the sampler's endpoint weighting is pinned to.

The one thing exact_chain cannot do -- an outlier model's SHARED offset scale,
which does not factorise over dates -- is handled by conditioning on it. Given
every outlier model's scale the model is an ordinary one, so each combination
of scale nodes is integrated and the results mixed by their evidence under the
uniform prior the sampler draws the scale from. Two outlier models of 24 nodes
each is 576 combinations; the evidence alone is cheap for all of them, and the
marginals are formed only for the combinations that hold almost all of it.

What is compared
----------------
The Boundaries. A group in the wrong lobe takes its Boundaries with it, and a
Boundary's marginal is what the transfer products give without integrating
any date back out. Each is compared on REPORTING_STEP_YEARS bins by total
variation distance, which reads directly as the probability mass in the wrong
place.
"""
import copy
import itertools
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.services import exact_chain, gibbs
from app.services.exact_chain import REPORTING_STEP_YEARS, _log_interval_integral
from app.services.model_tree import inherited_bounds

# Total variation distance between a sampled Boundary's marginal and the direct
# one above which the two are reported as differing. Agreeing runs on the
# test models sit under 0.08 -- Monte Carlo noise plus the grid -- and
# the lobe failure this exists for sat at 0.6 to 1.0.
DISAGREEMENT = 0.15
# The largest difference, over every calendar year, in the probability of a
# Boundary being older than that year, above which the two are also reported
# as differing. Total variation cannot see a small calibration lobe the
# sampler under-weights: on Petchey et al.'s dune model of Layers 11-4 the
# sampler put P(Start before AD 1360) at 0.00-0.03 against the direct 0.08, at a
# total variation of 0.08, under 0.15. This is that error read directly, as the
# probability an analyst would quote. Runs that mix well sat at 0.027 or less
# on thirteen test models and two seeds each; the failures at 0.06-0.08.
# Applied only to runs of several chains: one short chain's own Monte Carlo
# noise reaches it (0.069 on a single 8,000-sweep chain, 0.022 or less on the
# default four).
THRESHOLD_DISAGREEMENT = 0.05
# Most cells on the direct calculation's grid. The cost is a few M x M matrix
# products per group per scale combination, so it goes as M^3.
MAX_CELLS = 800
# Room left beyond the dates' calibrated ranges for the outermost Boundaries,
# in years, and as a fraction of the dates' own spread when that is larger.
PAD_YEARS = 300.0
# Most combinations of outlier-scale nodes the evidence is computed over; a
# model past it is not checked.
MAX_SCALE_COMBINATIONS = 1024
# The marginals are formed for the most probable combinations until they hold
# this much of the evidence, and for no more than MAX_MARGINAL_COMBINATIONS.
SCALE_MASS = 0.995
MAX_MARGINAL_COMBINATIONS = 48
# Posterior mass a Boundary may have in the outermost cells of the grid before
# the grid is judged to have cut it off.
EDGE_MASS = 1e-3


class NotChecked(Exception):
    """The model is not one this module can integrate; the message says why."""


def _flattened(element: Dict) -> Dict:
    """A copy of `element` with every nested Sequence spelled out in place."""
    out = copy.copy(element)
    children = []
    for child in element.get('children') or []:
        if child.get('element_type') == 'sequence':
            children.extend(_flattened(child)['children'])
        else:
            children.append(child)
    out['children'] = children
    return out


def _ids(element: Dict, found: Optional[set] = None) -> set:
    found = set() if found is None else found
    found.add(element['id'])
    for child in element.get('children') or []:
        _ids(child, found)
    return found


Tails = Tuple[List[str], List[str]]


def _has_boundary(element: Dict) -> bool:
    return element.get('element_type') == 'boundary' or any(
        _has_boundary(child) for child in element.get('children') or [])


def _tails(flat: Dict, measurements: List[Dict]) -> Tuple[Dict, Tails]:
    """
    The flattened model with any dates OUTSIDE EVERY BOUNDARY at its two ends
    taken off -- the children before its first Boundary and after its last --
    and those dates, (older end, younger end). See the module docstring.
    """
    children = flat.get('children') or []
    positions = [i for i, c in enumerate(children) if _has_boundary(c)]
    if not positions:
        raise NotChecked('the model has no Boundary to check')
    first, last = positions[0], positions[-1]

    def dates_of(elements: List[Dict]) -> List[str]:
        inside = set()
        for element in elements:
            inside |= _ids(element)
        return [m['id'] for m in measurements if m.get('element_id') in inside]

    older, younger = dates_of(children[:first]), dates_of(children[last + 1:])
    flagged = [m for m in measurements if m['id'] in set(older) | set(younger)
               and (m.get('outlier_prior') or 0) > 0]
    if flagged:
        raise NotChecked('a date outside every Boundary carries an outlier model')
    trimmed = dict(flat)
    trimmed['children'] = children[first:last + 1]
    return trimmed, (older, younger)


def shape(root: Dict, measurements: List[Dict]
          ) -> Tuple[Optional[Tuple[str, str, List[str]]], List[exact_chain.Chain], Tails]:
    """
    (outer pair, branches, tails): the outer pair as (S, E, dates directly
    between them), or None for a single chain; the branches as exact_chain
    Chains; and the dates outside every Boundary at the model's older and
    younger ends (_tails). Raises NotChecked for any other shape.
    """
    if root.get('element_type') != 'sequence':
        raise NotChecked('the model is not a Sequence')
    flat, tails = _tails(_flattened(root), measurements)
    inside = [m for m in measurements if m['id'] not in set(tails[0]) | set(tails[1])]
    measurements = inside
    chain = exact_chain.layout(flat, measurements, allow_inner_order=True)
    if chain is not None:
        return None, [chain], tails

    children = flat['children']
    kinds = [c.get('element_type') for c in children]
    if kinds != ['boundary', 'phase', 'boundary']:
        raise NotChecked('the model is neither one chain nor parallel chains '
                         'between two Boundaries')
    outer_phase = children[1]
    branches = []
    covered = {outer_phase['id']}
    for child in outer_phase.get('children') or []:
        if child.get('element_type') != 'sequence':
            raise NotChecked(f"'{child.get('name')}' inside "
                             f"'{outer_phase.get('name')}' is not a Sequence")
        inside = _ids(child)
        members = [m for m in measurements if m.get('element_id') in inside]
        branch = exact_chain.layout(_flattened(child), members, allow_inner_order=True)
        if branch is None:
            raise NotChecked(f"'{child.get('name')}' is not a chain")
        branches.append(branch)
        covered |= inside
    if not branches:
        raise NotChecked('the outer Phase holds no Sequences')
    stray = [m for m in measurements if m.get('element_id') not in covered]
    if stray:
        raise NotChecked('some dates sit outside every branch')
    loose = [m['id'] for m in measurements if m.get('element_id') == outer_phase['id']]
    return (children[0]['id'], children[2]['id'], loose), branches, tails


def _blocks(model: gibbs.Model, dates: List[str]) -> List[List[str]]:
    """
    A group's dates as BLOCKS, oldest block first: dates within a block are not
    ordered against each other, and every date of a block is ordered against
    every date of every other block. Read from the sampler's own ordering, so
    it is the model as written, not an assumption about it.

    A Sequence of Phases with no Boundary between them is this: the Phases'
    dates share the Boundaries either side and the Sequence puts every date of
    one before every date of the next. Anything else inside a group -- two
    chains of dates side by side, say -- is a partial order that does not
    reduce to blocks, and raises NotChecked.
    """
    cache = model.__dict__.setdefault('_check_blocks', {})
    key = tuple(dates)
    if key in cache:
        return cache[key]
    reach = model._younger_reachable()
    members = list(dates)
    parent = {d: d for d in members}

    def find(d):
        while parent[d] != d:
            parent[d] = parent[parent[d]]
            d = parent[d]
        return d

    def ordered(a, b):
        return b in reach[a] or a in reach[b]

    for a, b in itertools.combinations(members, 2):
        if not ordered(a, b):
            parent[find(a)] = find(b)
    groups: Dict[str, List[str]] = {}
    for d in members:
        groups.setdefault(find(d), []).append(d)
    blocks = list(groups.values())
    for block in blocks:
        if any(ordered(a, b) for a, b in itertools.combinations(block, 2)):
            raise NotChecked('some dates in a group are ordered against each other '
                             'in a way that is not one block after another')
    # Oldest first: a block is older than the ones its dates all precede.
    found = list(blocks)               # a list being sorted reads as empty
    blocks.sort(key=lambda block: -sum(
        1 for other in found if other is not block and other[0] in reach[block[0]]))
    for i, older in enumerate(blocks):
        for younger in blocks[i + 1:]:
            if not all(y in reach[o] for o in older for y in younger):
                raise NotChecked('some dates in a group are ordered against each other '
                                 'in a way that is not one block after another')
    cache[key] = blocks
    return blocks


def _same_model(model: gibbs.Model, outer, branches: List[exact_chain.Chain],
                tails: Tails = ([], [])) -> None:
    """
    Raise NotChecked unless the shape is the model AS WRITTEN: the same span
    groups, each with the same two Boundaries and the same dates, and the
    same ordering between every pair of parameters, transitively, both ways.

    Spelling nested Sequences out in place is only a restatement when every
    inner Sequence is bracketed by Boundaries of its own. One that is not --
    a Sequence holding two Phases and one Boundary, say -- flattens into a
    chain whose groups the written model does not have, and integrating that
    would check the sampler against a different model. The Boundaries and the
    span correction can match while the groups do not, so the groups and the
    ordering are compared directly.
    """
    groups = []
    for chain in branches:
        for index, dates in enumerate(chain.groups):
            groups.append((chain.boundary_ids[index], chain.boundary_ids[index + 1],
                           frozenset(dates)))
    if outer is not None:
        groups.append((outer[0], outer[1], frozenset(outer[2])))
    written = {(older, younger, frozenset(dates))
               for older, younger, dates in model.group_dates if dates}
    if written != {g for g in groups if g[2]}:
        raise NotChecked('its groups are not the ones the written model has')

    younger_than: Dict[str, set] = {p: set() for p in model.params}

    def order(older: str, younger: str) -> None:
        younger_than[older].add(younger)

    def inner(dates: List[str]) -> None:
        # Dates ordered against each other inside one pair of Boundaries, as
        # blocks (see _blocks); the closure below then shows whether that is
        # ALL the ordering the written model has.
        blocks = _blocks(model, dates) if len(dates) > 1 else []
        for i, older_block in enumerate(blocks):
            for younger_block in blocks[i + 1:]:
                for o in older_block:
                    for y in younger_block:
                        order(o, y)

    for chain in branches:
        for index, dates in enumerate(chain.groups):
            older, younger = chain.boundary_ids[index], chain.boundary_ids[index + 1]
            order(older, younger)
            for date_id in dates:
                order(older, date_id)
                order(date_id, younger)
            inner(dates)
    if outer is not None:
        start, end, loose = outer
        for chain in branches:
            order(start, chain.boundary_ids[0])
            order(chain.boundary_ids[-1], end)
        for date_id in loose:
            order(start, date_id)
            order(date_id, end)
        inner(loose)
    # Dates outside every Boundary: older than the model's first Boundary or
    # younger than its last, and ordered among themselves as blocks.
    first = outer[0] if outer is not None else branches[0].boundary_ids[0]
    last = outer[1] if outer is not None else branches[-1].boundary_ids[-1]
    older_tail, younger_tail = tails
    for date_id in older_tail:
        order(date_id, first)
    for date_id in younger_tail:
        order(last, date_id)
    inner(older_tail)
    inner(younger_tail)

    closed: Dict[str, set] = {}

    def reach(param: str) -> set:
        if param not in closed:
            closed[param] = set()
            found = set()
            for younger in younger_than[param]:
                found |= {younger} | reach(younger)
            closed[param] = found
        return closed[param]

    implied = {p: reach(p) for p in model.params}
    if implied != model._younger_reachable():
        raise NotChecked('its ordering is not the one the written model has')


def _grid(measurements: List[Dict], root: Dict) -> np.ndarray:
    """
    The direct calculation's grid: the sampler's own step where that fits in
    MAX_CELLS, over the dates' calibrated ranges plus room for the Boundaries.

    Narrower than the sampler's, which runs out to wherever an outlier model's
    broad component reaches. Dates are contained by their Boundaries, so no
    date needs the far tail, and a Boundary that does is caught by EDGE_MASS.
    """
    element_bounds = inherited_bounds(root)
    base = exact_chain._build_grid(measurements, element_bounds, 1)
    likelihood, _cumulative, outlier_parts = exact_chain.date_tables(
        base, measurements, element_bounds)
    lows, highs = [], []
    for date_id, row in likelihood.items():
        inlier = outlier_parts[date_id][0] if date_id in outlier_parts else row
        cdf = np.cumsum(inlier)
        if cdf[-1] <= 0:
            continue
        cdf = cdf / cdf[-1]
        lows.append(base[np.searchsorted(cdf, 1e-4)])
        highs.append(base[min(len(base) - 1, np.searchsorted(cdf, 1 - 1e-4))])
    if not lows:
        raise NotChecked('no date has a likelihood on the grid')
    spread = max(highs) - min(lows)
    pad = max(PAD_YEARS, spread)
    low = max(float(base[0]), min(lows) - pad)
    high = min(float(base[-1]), max(highs) + pad)
    base_step = float(base[1] - base[0])
    step = base_step * max(1, int(np.ceil((high - low) / MAX_CELLS / base_step)))
    return np.arange(low, high + step / 2, step)


class _Direct:
    """The integration itself, on one grid, for one model."""

    def __init__(self, model: gibbs.Model, outer, branches: List[exact_chain.Chain],
                 tails: Tails = ([], [])):
        self.model = model
        self.outer = outer
        self.branches = branches
        self.M = model.M
        self.dt = model.dt
        index = np.arange(self.M)
        # cells[a, b] = a - b: how far a sits older than b.
        self.cells = index[:, None] - index[None, :]
        self.older = self.cells > 0
        self.ok = {b: (index >= model.window[b][0]) & (index <= model.window[b][1])
                   for b in model.boundary_ids}
        # The weights the dates outside every Boundary put on the model's
        # first and last Boundary (module docstring); none of them carries an
        # outlier model, so they do not change with the scales.
        older_tail, younger_tail = tails
        self.tail_older = self._tail_weight(older_tail, oldest_end=True)
        self.tail_younger = self._tail_weight(younger_tail, oldest_end=False)

    def _tail_weight(self, dates: List[str], oldest_end: bool) -> Optional[np.ndarray]:
        """
        g over the end Boundary: the dates' integral with all of them older
        than it (`oldest_end`) or all younger, in their own order, with a flat
        prior -- no span factor. Scaled to a peak of one; it is the same for
        every outlier scale, so the scale does not touch the evidence ratios.
        """
        if not dates:
            return None
        blocks = _blocks(self.model, dates) if len(dates) > 1 else [list(dates)]
        if len(blocks) > 1:
            log_z = self._log_ordered(blocks, {})
        else:
            log_z = np.zeros((self.M, self.M))
            for date_id in blocks[0]:
                row = self._likelihood(date_id, {})
                log_z = log_z + _log_interval_integral(np.cumsum(row) * self.dt, row, self.dt).T
        # [older bound, younger bound]: the grid's top edge over the Boundary,
        # or the Boundary over the grid's bottom edge.
        log_g = log_z[self.M - 1, :] if oldest_end else log_z[:, 0]
        finite = np.isfinite(log_g)
        if not finite.any():
            raise NotChecked('a date outside every Boundary has no room beside it')
        peak = float(log_g[finite].max())
        return np.where(finite, np.exp(np.where(finite, log_g, 0.0) - peak), 0.0)

    def _with_tails(self, weight: np.ndarray) -> np.ndarray:
        """`weight` over [first Boundary, last Boundary] times the tails' g."""
        if self.tail_older is not None:
            weight = weight * self.tail_older[:, None]
        if self.tail_younger is not None:
            weight = weight * self.tail_younger[None, :]
        return weight

    # -- building blocks ---------------------------------------------------

    def _likelihood(self, date_id: str, nodes: Dict[str, int]) -> np.ndarray:
        model = self.model.model_of_date.get(date_id)
        if model is None:
            return self.model.likelihood[date_id]
        return self.model.likelihood_at[date_id][nodes[model]]

    def _log_group(self, dates: List[str], nodes: Dict[str, int]) -> np.ndarray:
        """
        A group's span factor and its dates' interval integrals, in the log,
        indexed [older boundary, younger boundary]: -inf where they cross, and
        where they share a cell the tie's half times the dates' likelihoods
        there (exact_chain._boundary_pair_term, _log_date_factor), with a half
        more for each pair of the dates the sampler ties to each other --
        dates ordered inside the pair, all in that one cell, at the weight the
        sampler gives them (Model.endpoint_weight).
        """
        K = len(dates)
        with np.errstate(divide='ignore'):
            log_span = np.where(self.older,
                                np.log(np.maximum(self.cells, 1) * self.dt), -np.inf)
        term = np.where(self.older, -K * log_span if K else 0.0, -np.inf)
        blocks = _blocks(self.model, dates) if K > 1 else []
        if len(blocks) > 1:
            term = term + self._log_ordered(blocks, nodes)
        else:
            for date_id in dates:
                row = self._likelihood(date_id, nodes)
                # exact_chain's is indexed [younger, older].
                term = term + _log_interval_integral(np.cumsum(row) * self.dt, row, self.dt).T
        members = set(dates)
        tied = sum(1 for y, o in self.model.tie_pairs if y in members and o in members)
        tie = np.log(exact_chain.BOUNDARY_TIE_WEIGHT) - tied * np.log(2.0)
        with np.errstate(divide='ignore'):
            for date_id in dates:
                tie = tie + np.log(self._likelihood(date_id, nodes))
        np.fill_diagonal(term, tie)
        return term

    def _log_block_oldest(self, block: List[str], nodes: Dict[str, int]) -> np.ndarray:
        """
        T[b, a] for a block of dates all lying in [a, b] with the OLDEST of them
        at b, in the log, indexed [older, younger]: the derivative with respect
        to b of the product of the dates' interval integrals,

            sum over dates j of  L_j(b) dt  prod over k != j of F_k(b, a),

        with F the trapezoidal interval integral the dates inside a pair of
        Boundaries use (half weight on each end cell). The product of all the
        others is formed as a sum of logs with the dates whose integral is zero
        counted, so a date at the very edge of its own likelihood does not turn
        the whole thing to nan.

        The trapezoid, not whole cells, is a choice about how a continuous
        ordering is put on a grid. Two dates can share a cell, and counting
        that whole gives a configuration with several dates in one cell more
        weight than the continuum does, most where the span is a few cells;
        half weights at the ends are what the sampler's own draws use for a
        date held by a neighbouring date (Model.draw_date) and what OxCal's
        continuous model converges to. The sampler's log_posterior does NOT
        weigh a date-to-date tie that way, only a date on a Boundary; weighing
        every tie as log_posterior does moved this calculation 0.2 from the
        sampler's output on models of ordered phases, and the half weights
        brought it back to 0.03-0.1.
        """
        rows = {d: self._likelihood(d, nodes) for d in block}

        def log_f(d):
            row = rows[d]
            return _log_interval_integral(np.cumsum(row) * self.dt, row, self.dt).T

        total = np.zeros((self.M, self.M))
        zeros = np.zeros((self.M, self.M), dtype=int)
        for d in block:
            lf = log_f(d)
            finite = np.isfinite(lf)
            total += np.where(finite, lf, 0.0)
            zeros += ~finite
        out = np.full((self.M, self.M), -np.inf)
        with np.errstate(divide='ignore', invalid='ignore'):
            for d in block:
                lf = log_f(d)
                finite = np.isfinite(lf)
                others = np.where(finite, np.where(zeros == 0, total - lf, -np.inf),
                                  np.where(zeros == 1, total, -np.inf))
                log_mass = np.log(rows[d] * self.dt)[:, None]
                out = np.logaddexp(out, log_mass + others)
        return np.where(self.cells >= 0, out, -np.inf)

    def _log_ordered(self, blocks: List[List[str]], nodes: Dict[str, int]) -> np.ndarray:
        """
        The dates' integral over a group whose blocks are ordered, [older
        boundary S, younger boundary E], in the log: the sum over placements of
        every date with each block wholly older than the next.

        Cut the order at the oldest date of each block after the first, c_i of
        block i + 1. Block i's dates then lie in [c_i, c_(i-1)] with its oldest
        at c_(i-1), which is T_i(c_i, c_(i-1)) (_log_block_oldest), the last
        block's below by E itself, and the first block's oldest is under S. That
        is a chain over the c's, and its sum is a product of matrices:

            Z[S, E] = sum over c_0 < S of (T_1 T_2 ... T_n)[c_0, E]

        Endpoints take half weight as an interval integral's do: a date at S
        or at E counts half.
        """
        log_scale = 0.0
        matrices = []
        for i, block in enumerate(blocks):
            log_t = self._log_block_oldest(block, nodes)
            if i == len(blocks) - 1:
                # The last block's lower end is E itself, not a date.
                log_t = log_t + np.where(self.cells == 0, np.log(0.5), 0.0)
            matrix, peak = self._exp(log_t)
            matrices.append(matrix)
            log_scale += peak
        product = matrices[0]
        for matrix in matrices[1:]:
            product, factor = self._scaled(product @ matrix)
            log_scale += factor
        below = np.vstack([np.zeros((1, self.M)), np.cumsum(product, axis=0)[:-1]])
        Z = below + 0.5 * product
        with np.errstate(divide='ignore'):
            return np.where(Z > 0, np.log(np.where(Z > 0, Z, 1.0)), -np.inf) + log_scale

    @staticmethod
    def _exp(log_matrix: np.ndarray) -> Tuple[np.ndarray, float]:
        finite = np.isfinite(log_matrix)
        if not finite.any():
            return np.zeros_like(log_matrix), 0.0
        peak = float(log_matrix[finite].max())
        return np.where(finite, np.exp(np.where(finite, log_matrix, 0.0) - peak), 0.0), peak

    @staticmethod
    def _scaled(matrix: np.ndarray) -> Tuple[np.ndarray, float]:
        peak = float(matrix.max())
        if peak <= 0:
            return matrix, 0.0
        return matrix / peak, float(np.log(peak))

    def _span_correction(self, oldest: str, youngest: str) -> np.ndarray:
        """The span prior over a pair, in the log, [oldest, youngest]."""
        rows = [row for (o, y, _n), row in zip(self.model.span_priors,
                                               self.model.span_prior_rows)
                if (o, y) == (oldest, youngest)]
        if not rows:
            # No correction: every ordered pair counts the same.
            return np.where(self.cells >= 0, 0.0, -np.inf)
        row = rows[0]
        return np.where(self.cells >= 0, row[np.clip(self.cells, 0, self.M - 1)], -np.inf)

    def _ends_ok(self, first: str, last: str) -> np.ndarray:
        return self.ok[first][:, None] & self.ok[last][None, :]

    def _branch(self, chain: exact_chain.Chain, nodes: Dict[str, int],
                with_messages: bool) -> Dict:
        """
        A branch's end-to-end product J[first, last], its log scale, and with
        `with_messages` the prefix and suffix products its interior Boundaries'
        marginals are read from.
        """
        transfers = []
        log_scale = 0.0
        for gi, dates in enumerate(chain.groups):
            older_id, younger_id = chain.boundary_ids[gi], chain.boundary_ids[gi + 1]
            log_t = self._log_group(dates, nodes)
            log_t = np.where(self.ok[older_id][:, None] & self.ok[younger_id][None, :],
                             log_t, -np.inf)
            matrix, peak = self._exp(log_t)
            transfers.append(matrix)
            log_scale += peak
        prefix = [np.diag(self.ok[chain.boundary_ids[0]].astype(float))]
        for matrix in transfers:
            product, factor = self._scaled(prefix[-1] @ matrix)
            log_scale += factor
            prefix.append(product)
        out = {'J': prefix[-1], 'log_scale': log_scale}
        if with_messages:
            suffix = [np.diag(self.ok[chain.boundary_ids[-1]].astype(float))]
            for matrix in reversed(transfers):
                suffix.append(self._scaled(matrix @ suffix[-1])[0])
            out['prefix'] = prefix
            out['suffix'] = suffix[::-1]
        return out

    @staticmethod
    def _tied_cumsum(matrix: np.ndarray, axis: int) -> np.ndarray:
        """Cumulative sum along `axis` with the last term at the tie's weight:
        sum over i < n, plus a half of term n."""
        return (np.cumsum(matrix, axis=axis)
                - (1.0 - exact_chain.BOUNDARY_TIE_WEIGHT) * matrix)

    def _within(self, J: np.ndarray) -> Tuple[np.ndarray, float]:
        """
        Z[S, E] = sum over first <= S and last >= E of J[first, last]: the
        branch with its ends inside the outer pair, an end on S or on E at the
        half a tie between neighbouring Boundaries carries.
        """
        below_s = self._tied_cumsum(J, axis=0)
        above_e = self._tied_cumsum(below_s[:, ::-1], axis=1)[:, ::-1]
        return self._scaled(above_e)

    def _outside(self, A: np.ndarray) -> np.ndarray:
        """W[first, last] = sum over S >= first and E <= last of A[S, E], a
        tie at either end at half weight."""
        older = self._tied_cumsum(A[::-1], axis=0)[::-1]
        return self._tied_cumsum(older, axis=1)

    # -- one combination of outlier scales -----------------------------------

    def solve(self, nodes: Dict[str, int], with_marginals: bool, cache: Dict
              ) -> Tuple[float, Dict[str, np.ndarray]]:
        """(log evidence, Boundary marginals) with every outlier scale fixed."""
        def branch(chain):
            uses = sorted({self.model.model_of_date[d] for g in chain.groups for d in g
                           if d in self.model.model_of_date})
            key = (id(chain), with_marginals) + tuple(nodes[m] for m in uses)
            if key not in cache:
                if len(cache) >= 32:
                    cache.pop(next(iter(cache)))
                cache[key] = self._branch(chain, nodes, with_marginals)
            return cache[key]

        parts = [branch(chain) for chain in self.branches]
        marginals: Dict[str, np.ndarray] = {}

        if self.outer is None:
            chain, part = self.branches[0], parts[0]
            first, last = chain.boundary_ids[0], chain.boundary_ids[-1]
            log_c = np.where(self._ends_ok(first, last),
                             self._span_correction(first, last), -np.inf)
            W, w_scale = self._exp(log_c)
            W = self._with_tails(W)
            total = float((W * part['J']).sum())
            if total <= 0:
                return -np.inf, {}
            log_evidence = np.log(total) + w_scale + part['log_scale']
            weights = [W]
        else:
            S, E, loose = self.outer
            log_a = (self._span_correction(S, E) + self._log_group(loose, nodes))
            log_a = np.where(self._ends_ok(S, E), log_a, -np.inf)
            A0, a_scale = self._exp(log_a)
            A0 = self._with_tails(A0)
            within = []
            log_evidence = a_scale
            for part in parts:
                Z, z_scale = self._within(part['J'])
                within.append(Z)
                log_evidence += z_scale + part['log_scale']
            joint = A0 * np.prod(within, axis=0)
            total = float(joint.sum())
            if total <= 0:
                return -np.inf, {}
            log_evidence += np.log(total)
            if with_marginals:
                marginals[S] = joint.sum(axis=1)
                marginals[E] = joint.sum(axis=0)
                weights = []
                for b in range(len(parts)):
                    others = A0.copy()
                    for o, Z in enumerate(within):
                        if o != b:
                            others = others * Z
                    weights.append(self._outside(others))

        if with_marginals:
            for chain, part, W in zip(self.branches, parts, weights):
                for k, boundary_id in enumerate(chain.boundary_ids):
                    # p(b_k = x) = sum_{f, l} P_k[f, x] W[f, l] D_k[x, l]
                    left = part['prefix'][k].T @ W
                    marginals[boundary_id] = (left * part['suffix'][k]).sum(axis=1)
        return float(log_evidence), marginals


def reference(measurements: List[Dict], root: Dict,
              uniform_span_prior: bool = True) -> Dict:
    """
    The direct calculation: {'grid', 'shape', 'boundaries': {id: density}}.
    Raises NotChecked when the model is not one this can integrate.
    """
    outer, branches, tails = shape(root, measurements)
    grid = _grid(measurements, root)
    model = gibbs.Model(measurements, root, grid=grid,
                        uniform_span_prior=uniform_span_prior)
    if model.depositions or model.level_ids:
        raise NotChecked('the model has a deposition sequence')
    _same_model(model, outer, branches, tails)
    expected = sum(len(c.boundary_ids) for c in branches) + (2 if outer else 0)
    if set(model.boundary_ids) != (
            {b for c in branches for b in c.boundary_ids}
            | ({outer[0], outer[1]} if outer else set())) or len(model.boundary_ids) != expected:
        raise NotChecked('the Boundaries are not the ones this shape implies')
    ends = ((outer[0], outer[1]) if outer
            else (branches[0].boundary_ids[0], branches[0].boundary_ids[-1]))
    if uniform_span_prior and expected > 2 and [
            (o, y) for o, y, _n in model.span_priors] != [ends]:
        raise NotChecked('the span correction does not span the whole model')

    direct = _Direct(model, outer, branches, tails)
    outlier_models = model.outlier_models
    node_counts = [len(model.scale_grid[m]) for m in outlier_models]
    combinations = list(itertools.product(*[range(n) for n in node_counts]))
    if len(combinations) > MAX_SCALE_COMBINATIONS:
        raise NotChecked('too many outlier models to integrate over')

    cache: Dict = {}
    log_evidence = np.array([
        direct.solve(dict(zip(outlier_models, combo)), False, cache)[0]
        for combo in combinations])
    if not np.isfinite(log_evidence).any():
        raise NotChecked('the direct calculation found no posterior mass')
    weights = np.exp(log_evidence - log_evidence[np.isfinite(log_evidence)].max())
    weights = np.where(np.isfinite(weights), weights, 0.0)
    weights /= weights.sum()

    order = np.argsort(weights)[::-1]
    held = np.cumsum(weights[order])
    chosen = order[:min(MAX_MARGINAL_COMBINATIONS,
                        int(np.searchsorted(held, SCALE_MASS)) + 1)]
    # Grouped so a branch's prefix and suffix products are reused across
    # consecutive combinations that share its scales.
    chosen = sorted(chosen, key=lambda i: combinations[i])
    cache = {}
    boundaries: Dict[str, np.ndarray] = {}
    for i in chosen:
        _log_e, marginals = direct.solve(
            dict(zip(outlier_models, combinations[i])), True, cache)
        for boundary_id, density in marginals.items():
            total = density.sum()
            if total > 0:
                boundaries[boundary_id] = (boundaries.get(boundary_id, 0.0)
                                           + weights[i] * density / total)
    for boundary_id, density in boundaries.items():
        density = density / density.sum()
        if density[:2].sum() + density[-2:].sum() > EDGE_MASS:
            raise NotChecked('a Boundary runs off the edge of the calculation')
        boundaries[boundary_id] = density
    return {'grid': grid, 'shape': 'parallel' if outer else 'chain',
            'outside': len(tails[0]) + len(tails[1]),
            'boundaries': boundaries,
            'names': dict(model.plan.boundary_names)}


def _binned(xs: np.ndarray, mass: np.ndarray, low: float, count: int) -> np.ndarray:
    bins = np.floor((xs - low) / REPORTING_STEP_YEARS).astype(int)
    inside = (bins >= 0) & (bins < count)
    return np.bincount(bins[inside], weights=mass[inside], minlength=count)


def compare(direct: Dict, sampled_boundaries: Dict[str, Dict]) -> List[Dict]:
    """
    Per Boundary, the total variation distance between the sampled marginal
    and the direct one on REPORTING_STEP_YEARS bins, worst first, and the
    largest difference between them in the probability of being older than a
    given year (`threshold_gap`).

    The sampled side is the results' plotted density, which drops only cells
    under exact_chain.DISPLAY_THRESHOLD of the peak. Mass it puts outside the
    direct grid is mass in the wrong place, and counts as such.
    """
    grid = direct['grid']
    low = float(grid[0])
    count = int(np.ceil((grid[-1] - low) / REPORTING_STEP_YEARS)) + 1
    rows = []
    for boundary_id, density in direct['boundaries'].items():
        sampled = (sampled_boundaries.get(boundary_id) or {}).get('posterior') or {}
        points = np.array(sampled.get('probability_distribution') or [], dtype=float)
        if points.size == 0:
            continue
        xs, ps = points[:, 0], points[:, 1]
        step = float(np.median(np.diff(xs))) if len(xs) > 1 else 1.0
        mass = ps * step
        if mass.sum() <= 0:
            continue
        theirs = _binned(xs, mass / mass.sum(), low, count)
        ours = _binned(grid, density, low, count)
        distance = 0.5 * (float(np.abs(theirs - ours).sum()) + max(0.0, 1.0 - theirs.sum()))
        threshold_gap = float(np.abs(np.cumsum(theirs) - np.cumsum(ours)).max())
        rows.append({
            'id': boundary_id,
            'name': direct['names'].get(boundary_id, 'Boundary'),
            'distance': distance,
            'threshold_gap': threshold_gap,
            'sampled_mean': float((xs * mass).sum() / mass.sum()),
            'direct_mean': float((grid * density).sum()),
        })
    rows.sort(key=lambda r: r['distance'], reverse=True)
    return rows


def check(measurements: List[Dict], root: Dict, sampled: Dict,
          uniform_span_prior: bool = True) -> Dict:
    """
    The solver record's `cross_check`:

        {'status': 'agrees' | 'differs' | 'not_checked',
         'reason': why not checked, else None,
         'shape': 'chain' | 'parallel' | None,
         'threshold': DISAGREEMENT,
         'threshold_gap_limit': THRESHOLD_DISAGREEMENT, or None on a run of one
                   chain, where only total variation is held to a limit,
         'checked': how many Boundaries were compared,
         'worst': the worst row of compare(), or None,
         'differing': every row over either limit,
         'seconds': how long the direct calculation took}
    """
    started = time.perf_counter()
    record = {'status': 'not_checked', 'reason': None, 'shape': None,
              'threshold': DISAGREEMENT, 'threshold_gap_limit': THRESHOLD_DISAGREEMENT,
              'checked': 0, 'worst': None,
              'differing': [], 'seconds': None}
    try:
        direct = reference(measurements, root, uniform_span_prior)
    except NotChecked as reason:
        record['reason'] = str(reason)
        record['seconds'] = time.perf_counter() - started
        return record
    rows = compare(direct, sampled.get('boundaries') or {})
    gap_limit = THRESHOLD_DISAGREEMENT if (sampled.get('chains') or 1) > 1 else None
    record['threshold_gap_limit'] = gap_limit
    record.update({
        'shape': direct['shape'],
        'checked': len(rows),
        'worst': rows[0] if rows else None,
        'differing': [r for r in rows if r['distance'] > DISAGREEMENT
                      or (gap_limit is not None and r['threshold_gap'] > gap_limit)],
        'seconds': time.perf_counter() - started,
    })
    record['status'] = ('differs' if record['differing']
                        else 'agrees' if rows else 'not_checked')
    if not rows:
        record['reason'] = 'no Boundary to compare'
    return record
