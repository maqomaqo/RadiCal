"""
Exact solver for a Sequence whose stages may hold areas side by side: a site
model with trenches or areas, each its own Sequence of Phases and Boundaries,
between Boundaries the whole site shares.

    Sequence("Site") {
      Boundary("Start");
      Phase("Early");                      <- an ordinary stage
      Boundary("Transition");
      Phase("Areas") {                     <- a stage holding areas side by side
        Sequence("Area A") { Boundary; Phase; Boundary; Phase; Boundary };
        Sequence("Area B") { Boundary; Phase; Boundary };
      };
      Boundary("End");
    };

services/exact_chain.py integrates a single line of Boundaries and declines
this ("the model branches"), so it used to go to the sampler. It is still a
chain, one level up: given the two Boundaries either side of a stage, its
areas are independent of each other and of everything else, so each enters
the outer chain through one matrix over that pair.

The maths
---------
Outer Boundaries B_0 > ... > B_n, oldest first; stage k lies between
S = B_k and E = B_(k+1). An area is an exact_chain Chain with first Boundary f
and last Boundary l, and its transfer matrices are built exactly as a chain's
are, so

    J(f, l)  = the area's weight with its ends held: the product of its
               transfer matrices (exact_chain._ChainProducts.end_to_end)
    Z(S, E)  = sum over f <= S and l >= E of J(f, l)         (within)

with f on S and l on E each a tie between neighbouring Boundaries at
BOUNDARY_TIE_WEIGHT, as everywhere else. The stage's transfer matrix is then
its own dates' term, as for any group, times the product of its areas' Z, and
the outer chain is solved by exact_chain's own message passing.

The span correction counts EVERY Boundary in the model, the areas' included,
over the outermost pair (B_0, B_n). That is what the sampler applies -- one
correction per connected component of the Boundary ordering, see
gibbs.Model._span_priors -- and what keeps the two answering the same model.

S and E themselves are not neighbouring Boundaries when an area lies between
them, so a stage holding areas gives their meeting no tie weight of its own:
the areas' Boundaries, all in that one cell, carry it (gibbs.Model's
_neighbouring_boundaries).

Each area is then solved given everything outside it. The weight on its ends
is

    W(f, l)  = sum over S >= f and E <= l of
               outside(S, E) * own(S, E) * product of the other areas' Z(S, E)
                                                                   (outside)

where outside is exact_chain's pair weight for the stage -- everything beyond
S and E -- and own the stage's own dates' term. W is exactly what the span
correction is on a chain: a factor coupling the area's two ends. So the area
is solved by exact_chain's _messages_with_end_factor with W in its place,
which gives its Boundaries' marginals and the pair weights its dates'
marginals are read from, as on a chain.

Joint draws follow the same order: the outer Boundaries from the outer chain;
each area's first Boundary given its stage's (S, E), from the sum of J over
l >= E; its last given that and E, from J; its interior given both ends by
exact_chain's own interior fill; and every date given its enclosing pair.

What it does NOT cover
----------------------
layout_with_reason returns None, with a reason, for a model whose areas are
side by side but which is not this shape: areas with no Boundary before and
after them that they share (separate models, in effect), an area that is not
itself a single line, dates outside every Boundary, areas inside an area, or
anything ordering one area against another. Those are sampled.

The outlier scale is marginalised per date, as on a chain; see exact_chain's
module docstring.
"""
import copy
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

from app.services import exact_chain, intervals
from app.services.exact_chain import BOUNDARY_TIE_WEIGHT, REPORTING_STEP_YEARS, Chain
from app.services.model_tree import inherited_bounds, plan_model


# --------------------------------------------------------------------------
# Tree helpers, shared with services/cross_check.py
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Sums over an area's ends, shared with services/cross_check.py
# --------------------------------------------------------------------------

def tied_cumsum(matrix: np.ndarray, axis: int) -> np.ndarray:
    """Cumulative sum along `axis` with the last term at the tie's weight:
    sum over i < n, plus a half of term n."""
    return np.cumsum(matrix, axis=axis) - (1.0 - BOUNDARY_TIE_WEIGHT) * matrix


def within(J: np.ndarray) -> np.ndarray:
    """
    Z[S, E] = sum over first <= S and last >= E of J[first, last]: an area
    with its ends inside the pair (S, E), an end on S or on E at the half a
    tie between neighbouring Boundaries carries.
    """
    below_s = tied_cumsum(J, axis=0)
    return tied_cumsum(below_s[:, ::-1], axis=1)[:, ::-1]


def outside(A: np.ndarray) -> np.ndarray:
    """W[first, last] = sum over S >= first and E <= last of A[S, E], a
    tie at either end at half weight."""
    older = tied_cumsum(A[::-1], axis=0)[::-1]
    return tied_cumsum(older, axis=1)


def _scaled(matrix: np.ndarray) -> Tuple[np.ndarray, float]:
    """(matrix scaled to a peak of one, log of the peak)."""
    peak = float(matrix.max())
    if not np.isfinite(peak) or peak <= 0:
        return matrix, 0.0
    return matrix / peak, float(np.log(peak))


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------

class Stage:
    """
    One link of the outer chain: the outer Boundaries either side, the dates
    lying directly between them, and any areas side by side there.
    """

    def __init__(self, older_id: str, younger_id: str, dates: List[str],
                 areas: List[Chain]):
        self.older_id = older_id
        self.younger_id = younger_id
        self.dates = list(dates)
        self.areas = list(areas)


class Stages:
    """A chain of outer Boundaries whose stages may hold areas, oldest first."""

    def __init__(self, boundary_ids: List[str], stages: List[Stage],
                 boundary_names: Dict[str, str]):
        # len(boundary_ids) == len(stages) + 1
        self.boundary_ids = boundary_ids
        self.stages = stages
        self.boundary_names = boundary_names

    @property
    def outer_chain(self) -> Chain:
        """The outer Boundaries as a Chain, each stage's own dates its group."""
        return Chain(self.boundary_ids, [stage.dates for stage in self.stages],
                     self.boundary_names)

    @property
    def all_boundary_ids(self) -> List[str]:
        """Every Boundary in the model, in model order: each outer Boundary,
        then the Boundaries of the areas in the stage after it."""
        out = []
        for index, outer in enumerate(self.boundary_ids):
            out.append(outer)
            if index < len(self.stages):
                for area in self.stages[index].areas:
                    out.extend(area.boundary_ids)
        return out

    @property
    def num_groups(self) -> int:
        """Groups of dates in the model, for the grid's point budget: every
        stage, and every group inside every area."""
        return len(self.stages) + sum(len(area.groups) for stage in self.stages
                                      for area in stage.areas)

    def __repr__(self):
        areas = sum(len(stage.areas) for stage in self.stages)
        return f"Stages({len(self.stages)} stages, {areas} areas)"


def has_areas(root: Optional[Dict]) -> bool:
    """Whether any Phase in `root` holds a Sequence: areas side by side."""
    if root is None:
        return False
    if root.get('element_type') == 'phase' and any(
            child.get('element_type') == 'sequence'
            for child in root.get('children') or []):
        return True
    return any(has_areas(child) for child in root.get('children') or [])


def layout_with_reason(root: Optional[Dict], measurements: List[Dict]
                       ) -> Tuple[Optional[Stages], Optional[str]]:
    """
    (Stages, None) for a model this module can integrate; (None, reason) for
    one whose areas sit side by side but which it cannot; (None, None) for a
    model with no areas side by side at all, whose reason, if it has one, is
    exact_chain.layout_with_reason's to give.

    Recognised from the tree, as written, and then held to the plan: the
    groups of dates and the ordering of every Boundary and date must be
    exactly the ones the model states, or integrating it would answer a
    different model. Reasons are phrased for an archaeologist, as exact_chain's
    are.
    """
    if not has_areas(root):
        return None, None
    plan = plan_model(root, measurements)
    coupled = exact_chain.coupled_dates_reason(plan)
    if coupled is not None:
        return None, coupled
    if root.get('element_type') != 'sequence':
        return None, (
            'its side-by-side Sequences have no Boundary before and after them '
            'that they share, so they are separate models rather than areas of '
            'one site. Put them in a Sequence between a Start and an End '
            'Boundary to make the model exactly solvable'
        )

    dates_of = {}
    for m in measurements:
        dates_of.setdefault(m.get('element_id'), []).append(m['id'])

    def subtree_dates(element: Dict) -> List[str]:
        inside = _ids(element)
        return [m['id'] for m in measurements if m.get('element_id') in inside]

    children = [c for c in _flattened(root).get('children') or []
                if c.get('element_type') != 'hiatus']
    positions = [i for i, c in enumerate(children) if c.get('element_type') == 'boundary']
    if len(positions) < 2:
        return None, (
            'no Boundary pair encloses its side-by-side areas. Add a Boundary '
            'before and after them'
        )
    outside_dates = [d for c in children[:positions[0]] + children[positions[-1] + 1:]
                     for d in subtree_dates(c)]
    if outside_dates:
        return None, _outside_every_boundary(outside_dates, measurements)

    stages = []
    for older, younger in zip(positions, positions[1:]):
        between = [c for c in children[older + 1:younger]
                   if subtree_dates(c) or has_areas(c)
                   or c.get('element_type') == 'sequence']
        if len(between) > 1:
            return None, (
                'two groups are ordered against each other with no Boundary '
                'between them. Add a Boundary at that transition to make the '
                'model exactly solvable'
            )
        dates: List[str] = []
        areas: List[Chain] = []
        if between:
            element = between[0]
            if element.get('element_type') != 'phase':
                return None, (
                    f"'{element.get('name') or element.get('element_type')}' is "
                    'not a Phase, so it cannot sit between two Boundaries of the '
                    'outer Sequence'
                )
            if has_areas(element):
                reason = _areas_of(element, measurements, dates_of, subtree_dates,
                                   dates, areas)
                if reason is not None:
                    return None, reason
            else:
                dates = subtree_dates(element)
        stages.append(Stage(children[older]['id'], children[younger]['id'],
                            dates, areas))

    layout = Stages([children[i]['id'] for i in positions], stages,
                    dict(plan.boundary_names))
    reason = _as_written(layout, plan, measurements)
    if reason is not None:
        return None, reason
    return layout, None


def _areas_of(phase: Dict, measurements: List[Dict], dates_of: Dict[str, List[str]],
              subtree_dates, dates: List[str], areas: List[Chain]) -> Optional[str]:
    """
    Fill `dates` with the dates lying directly in a stage's Phase and `areas`
    with its Sequences as Chains, looking through Phases nested in it, which
    add no ordering. A reason when that is not possible, else None.
    """
    dates.extend(dates_of.get(phase['id'], []))
    for child in phase.get('children') or []:
        kind = child.get('element_type')
        if kind == 'sequence':
            name = child.get('name') or 'an area'
            members = [m for m in measurements if m['id'] in set(subtree_dates(child))]
            chain, reason = exact_chain.layout_with_reason(child, members)
            if chain is None:
                return f"in '{name}', {reason}"
            areas.append(chain)
        elif kind == 'phase':
            reason = _areas_of(child, measurements, dates_of, subtree_dates,
                               dates, areas)
            if reason is not None:
                return reason
        elif kind == 'hiatus':
            continue
        else:
            return (
                f"'{phase.get('name') or 'a Phase'}' holds a "
                f"{kind.replace('_', ' ')} outside any Sequence, so its areas "
                'are not each a single line of phases'
            )
    return None


def _outside_every_boundary(date_ids: List[str], measurements: List[Dict]) -> str:
    """exact_chain's wording for dates no Boundary pair encloses."""
    by_id = {m['id']: m for m in measurements}
    names = ', '.join(sorted(by_id[d].get('sample_name') or d for d in date_ids[:4]))
    if len(date_ids) > 4:
        names += f' and {len(date_ids) - 4} more'
    return (
        f'{len(date_ids)} date(s) sit outside every Boundary ({names}). Add a '
        'Boundary either side of the phases holding them so they fall inside '
        'the chain'
    )


def _as_written(layout: Stages, plan, measurements: List[Dict]) -> Optional[str]:
    """
    None when `layout` is the model as written: the same groups of dates, each
    between the same two Boundaries, and the same ordering between every pair
    of Boundaries and dates, transitively. Otherwise the reason it is not.
    """
    written = {(g.older_boundary_id, g.younger_boundary_id, frozenset(g.date_ids))
               for g in plan.span_groups if g.date_ids}
    laid = set()
    for stage in layout.stages:
        if stage.dates:
            laid.add((stage.older_id, stage.younger_id, frozenset(stage.dates)))
        for area in stage.areas:
            for index, group in enumerate(area.groups):
                if group:
                    laid.add((area.boundary_ids[index], area.boundary_ids[index + 1],
                              frozenset(group)))
    placed = {d for _o, _y, group in laid for d in group}
    stray = [m['id'] for m in measurements if m['id'] not in placed]
    if stray:
        return _outside_every_boundary(stray, measurements)
    if written != laid:
        return ('its groups of dates are not the ones its Boundaries enclose '
                'as side-by-side areas')

    written_order: Dict[str, Set[str]] = {}
    laid_order: Dict[str, Set[str]] = {}

    def edge(order, older, younger):
        order.setdefault(older, set()).add(younger)
        order.setdefault(younger, set())

    for older_terms, younger_terms in plan.ordering:
        for _ko, older in older_terms:
            for _ky, younger in younger_terms:
                edge(written_order, older, younger)
    for group in plan.span_groups:
        edge(written_order, group.older_boundary_id, group.younger_boundary_id)
        for date_id in list(group.date_ids) + list(group.contained_ids):
            edge(written_order, group.older_boundary_id, date_id)
            edge(written_order, date_id, group.younger_boundary_id)

    def chain_edges(boundary_ids, groups):
        for index, group in enumerate(groups):
            older, younger = boundary_ids[index], boundary_ids[index + 1]
            edge(laid_order, older, younger)
            for date_id in group:
                edge(laid_order, older, date_id)
                edge(laid_order, date_id, younger)

    chain_edges(layout.boundary_ids, [stage.dates for stage in layout.stages])
    for stage in layout.stages:
        for area in stage.areas:
            edge(laid_order, stage.older_id, area.boundary_ids[0])
            edge(laid_order, area.boundary_ids[-1], stage.younger_id)
            chain_edges(area.boundary_ids, area.groups)

    if _closure(written_order) != _closure(laid_order):
        return ('some of its areas, or dates in them, are ordered against each '
                'other, which side-by-side areas between shared Boundaries '
                'cannot express')
    return None


def _closure(order: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
    """
    Everything each parameter is older than, transitively. Iterative, youngest
    first, so a long model cannot reach Python's recursion limit; a cycle,
    which no well-formed model has, leaves its members unclosed and so unequal
    to anything the layout gives.
    """
    pending = {param: len(younger) for param, younger in order.items()}
    older_of: Dict[str, List[str]] = {}
    for param, younger in order.items():
        for y in younger:
            older_of.setdefault(y, []).append(param)
    ready = [param for param, count in pending.items() if count == 0]
    closed: Dict[str, Set[str]] = {}
    while ready:
        param = ready.pop()
        found: Set[str] = set()
        for younger in order.get(param, ()):
            found.add(younger)
            found |= closed[younger]
        closed[param] = found
        for older in older_of.get(param, ()):
            pending[older] -= 1
            if pending[older] == 0:
                ready.append(older)
    return closed


# --------------------------------------------------------------------------
# Solving
# --------------------------------------------------------------------------

def solve(measurements: List[Dict], root: Dict, stages: Stages,
          num_samples: int = 4000, seed: int = 42,
          uniform_span_prior: bool = True,
          estimate_durations: bool = False,
          dispersal: Optional[Dict] = None,
          interval_draws: int = intervals.DEFAULT_DRAWS,
          resolution: float = REPORTING_STEP_YEARS) -> Dict:
    """
    Exact marginal posteriors for every date and Boundary in `stages`, in the
    shape exact_chain.solve returns, with its joint draws and on its two
    grids. The first pass needs only the outermost Boundaries, which bound
    every date and every area.
    """
    element_bounds = inherited_bounds(root)
    options = dict(num_samples=num_samples, seed=seed,
                   uniform_span_prior=uniform_span_prior,
                   estimate_durations=estimate_durations, dispersal=dispersal,
                   interval_draws=interval_draws, resolution=resolution)
    return exact_chain._two_pass(
        lambda grid, boundaries_only: _solve_on_grid(
            grid, measurements, root, stages, element_bounds,
            boundaries_only=boundaries_only, **options),
        measurements, element_bounds, stages.num_groups)


class _Area:
    """One area's parts on one grid: its log transfer matrices, its products
    and its weight with its ends held, J, and within its stage, Z."""

    def __init__(self, chain: Chain, grid: np.ndarray, allowed: Dict,
                 cumulative: Dict, likelihood: Dict, scratch: np.ndarray):
        self.chain = chain
        self.log_T = [exact_chain._group_log_transfer(
            grid, dates, cumulative, likelihood,
            allowed[chain.boundary_ids[index]], allowed[chain.boundary_ids[index + 1]],
            scratch) for index, dates in enumerate(chain.groups)]
        self.products = exact_chain._ChainProducts(
            chain, allowed, exact_chain._exponentiated(self.log_T))
        self.J = self.products.end_to_end                      # [first, last]
        self.Z, z_factor = _scaled(within(self.J))             # [S, E]
        # log of Z's true scale: the products', every transfer's own rescaling
        # (exact_chain._rescaled), and Z's.
        self.log_Z_scale = (self.products.log_scale + z_factor
                            + sum(exact_chain._finite_peak(t) for t in self.log_T))


def _solve_on_grid(grid: np.ndarray, measurements: List[Dict], root: Dict,
                   stages: Stages, element_bounds: Dict, num_samples: int,
                   seed: int, uniform_span_prior: bool, estimate_durations: bool,
                   dispersal: Optional[Dict], interval_draws: int,
                   resolution: float, boundaries_only: bool = False,
                   ) -> Tuple[Optional[Dict], List[np.ndarray]]:
    """
    solve() on one grid: (results, every posterior density it reported), or
    with `boundaries_only` (None, the outermost Boundaries' densities).
    """
    dt = float(grid[1] - grid[0])
    M = len(grid)
    scratch = np.empty((M, M))
    likelihood, cumulative, outlier_parts = exact_chain.date_tables(
        grid, measurements, element_bounds)
    allowed = exact_chain._allowed_windows(grid, element_bounds, stages.all_boundary_ids)

    areas = [[_Area(chain, grid, allowed, cumulative, likelihood, scratch)
              for chain in stage.areas] for stage in stages.stages]

    outer = stages.outer_chain
    all_dates = [m['id'] for m in measurements]
    inliers = {d: (outlier_parts[d][0] if d in outlier_parts else likelihood[d])
               for d in all_dates}
    oldest_limits, youngest_limits = exact_chain.pair_limits(
        element_bounds, outer.boundary_ids[0], outer.boundary_ids[-1],
        grid, inliers, all_dates)
    log_correction = exact_chain._span_prior_correction(
        grid, len(stages.all_boundary_ids), uniform_span_prior,
        oldest_limits, youngest_limits)

    # Each stage's own term, [younger, older]: its dates' factors, as for any
    # group, and for a stage holding areas no tie between its two Boundaries,
    # which are not neighbours there (module docstring).
    diagonal = np.arange(M)
    own = []
    for index, stage in enumerate(stages.stages):
        term = exact_chain._group_log_transfer(
            grid, stage.dates, cumulative, likelihood,
            allowed[stage.older_id], allowed[stage.younger_id], scratch)
        if stage.areas:
            term[diagonal, diagonal] -= np.log(BOUNDARY_TIE_WEIGHT)
        own.append(term)
    span_prior_applied = log_correction is not None
    correction = None
    correction_offset = 0.0
    if log_correction is not None and len(stages.stages) == 1:
        # One stage: the correction's Boundaries are its own, so it folds into
        # its term, as exact_chain does for a single group -- and so reaches
        # the areas through that term below.
        own[0] = own[0] + np.where(np.isfinite(log_correction), log_correction, -np.inf)
    elif log_correction is not None:
        correction_offset = exact_chain._finite_peak(log_correction)
        correction = np.exp(exact_chain._rescaled(log_correction))
    log_correction = None

    log_T = []
    for index, term in enumerate(own):
        term = term.copy()
        for area in areas[index]:
            with np.errstate(divide='ignore'):
                term += np.log(area.Z.T) + area.log_Z_scale
        log_T.append(term)
    transfer = exact_chain._exponentiated(log_T)

    if correction is not None:
        pair_weight, boundary_density, draw_outer, log_mass = \
            exact_chain._messages_with_span_prior(outer, allowed, transfer, correction)
    else:
        pair_weight, boundary_density, draw_outer, log_mass = exact_chain._messages(
            outer, allowed, transfer)
    log_evidence = (log_mass + correction_offset
                    + sum(exact_chain._finite_peak(term) for term in log_T))
    correction = None

    if boundaries_only:
        ends = [boundary_density(0), boundary_density(len(outer.boundary_ids) - 1)]
        return None, [density for density in ends if density.sum() > 0]

    # Every outer stage's own dates, read off the outer chain as on any chain:
    # a stage's log_T already holds its areas' Z, so dividing a date's own
    # factor out of it works unchanged.
    stage_dates = exact_chain._date_marginals(
        outer.groups, log_T, pair_weight, cumulative, likelihood, dt, scratch)
    outer_densities = {bid: boundary_density(i) for i, bid in enumerate(outer.boundary_ids)}

    # Each area given everything outside it.
    area_messages = []
    for index, stage in enumerate(stages.stages):
        messages = []
        if areas[index]:
            with np.errstate(divide='ignore'):
                beyond = np.log(pair_weight(index)) + own[index]       # [E, S]
            beyond, _factor = _scaled(np.exp(exact_chain._rescaled(beyond)).T)  # [S, E]
            for a, area in enumerate(areas[index]):
                rest = beyond
                for o, other in enumerate(areas[index]):
                    if o != a:
                        rest, _factor = _scaled(rest * other.Z)
                W, _factor = _scaled(outside(rest))                   # [first, last]
                messages.append(exact_chain._messages_with_end_factor(area.products, W.T))
        area_messages.append(messages)

    date_densities: Dict[str, np.ndarray] = {}
    boundary_densities: Dict[str, np.ndarray] = {}
    for index, stage in enumerate(stages.stages):
        boundary_densities[stage.older_id] = outer_densities[stage.older_id]
        for date_id in stage.dates:
            date_densities[date_id] = stage_dates[date_id]
        for area, (area_pair, area_density, _draw, _mass) in zip(
                areas[index], area_messages[index]):
            date_densities.update(exact_chain._date_marginals(
                area.chain.groups, area.log_T, area_pair, cumulative, likelihood,
                dt, scratch))
            for i, bid in enumerate(area.chain.boundary_ids):
                boundary_densities[bid] = area_density(i)
    boundary_densities[outer.boundary_ids[-1]] = outer_densities[outer.boundary_ids[-1]]

    def draw_joint(rng, count):
        outer_draws = draw_outer(rng, count)
        draws = {bid: outer_draws[:, i] for i, bid in enumerate(outer.boundary_ids)}
        date_draws = exact_chain._draw_dates(rng, outer, cumulative, outer_draws,
                                             likelihood, dt)
        columns = np.arange(M)
        for index, stage in enumerate(stages.stages):
            S, E = outer_draws[:, index], outer_draws[:, index + 1]
            for area, (_pair, _density, draw_area, _mass) in zip(
                    areas[index], area_messages[index]):
                first, last = _draw_area_ends(rng, area.J, S, E, columns)
                inner = draw_area(rng, count, ends=(first, last))
                for i, bid in enumerate(area.chain.boundary_ids):
                    draws[bid] = inner[:, i]
                date_draws.update(exact_chain._draw_dates(
                    rng, area.chain, cumulative, inner, likelihood, dt))
        ordered = {bid: draws[bid] for bid in boundary_densities}
        return ordered, date_draws

    return exact_chain._results(
        grid, root, measurements, date_densities, boundary_densities,
        stages.boundary_names, likelihood, outlier_parts, draw_joint,
        num_samples=num_samples, seed=seed, estimate_durations=estimate_durations,
        dispersal=dispersal, interval_draws=interval_draws, resolution=resolution,
        log_evidence=log_evidence, span_prior_applied=span_prior_applied)


def _draw_area_ends(rng, J: np.ndarray, S: np.ndarray, E: np.ndarray,
                    columns: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    An area's (first, last) Boundaries, one per draw, given its stage's (S, E):
    from h(S, first) h(last, E) J[first, last] over first <= S and last >= E,
    a tie at either end at half weight, as within() sums it.

    In two steps, so each is a draw from one row: the first from the area's
    weight summed over every last >= E, and then the last given the first.
    """
    M = J.shape[0]
    # R[f, E] = sum over l >= E of J[f, l], the tie at l == E a half.
    by_first = np.ascontiguousarray(tied_cumsum(J[:, ::-1], axis=1)[:, ::-1].T)  # [E, f]

    def first_rows(keys):
        s, e = keys // M, keys % M
        tie = np.where(columns[None, :] < s[:, None], 1.0,
                       np.where(columns[None, :] == s[:, None], BOUNDARY_TIE_WEIGHT, 0.0))
        return by_first[e] * tie

    first = exact_chain._draw_from_rows(rng, first_rows, S * M + E)

    def last_rows(keys):
        f, e = keys // M, keys % M
        tie = np.where(columns[None, :] > e[:, None], 1.0,
                       np.where(columns[None, :] == e[:, None], BOUNDARY_TIE_WEIGHT, 0.0))
        return J[f] * tie

    last = exact_chain._draw_from_rows(rng, last_rows, first * M + E)
    return first, last
