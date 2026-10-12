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
The OUTLIER SCALE is approximated, though no longer ignored. OxCal's
Outlier_Model carries one offset scale shared by every date using it and
constrained by the data. A shared hyperparameter couples the dates, and the
message passing above needs each to enter through its own likelihood alone; the
exact answer, one whole solve per scale mixed by evidence, costs 24 solves per
outlier model. Instead the scale is WEIGHED from the data with cheap evidence
passes on a coarse grid (_weigh_scales), and each flagged date's offset is then
mixed over that posterior. What is dropped is only the link a common scale makes
between the dates; services/gibbs.py samples that too (bayesian_config.solver =
"gibbs").

What it costs, and what it buys: 3-17% more run time on the test models. On
tests/fixtures/oxcal_outlier the outlier probabilities land 0.0016 from OxCal's
(0.0158 with the scale mixed over its prior; 0.0012 for the exact mixture;
the sampler 0.0016), and on tests/fixtures/oxcal_charcoal the modelled dates
3.5 years rms from OxCal (7.1 over the prior; the sampler 4.7), its span 29.4
years against OxCal's 29.2.

PHASE WIDTH depends on the interval quadrature being right to second order --
see _interval_integral, which carries the measurements. services/gibbs.py
carries the same rule as Model.endpoint_weight, and the two agree to about 0.6
years on the chain fixtures. The one fixture that overshot, charcoal, did so
because of the scale (tests/hillside_charcoal_fixture's PHASE WIDTH note).

`layout()` returns None for anything that is not a single chain. The caller
then tries services/exact_parallel.py, which integrates a chain whose stages
hold areas side by side with this module's own pieces, and falls back to the
sampler only when that declines too.

A Sequence that orders two groups with NO Boundary between them is a chain
too, of one group whose dates are ordered inside it as BLOCKS, one after
another: the shape OxCal runs without comment. The order does not factorise
along the Boundaries, but it does along cuts at each block's oldest date, so
the group is a small chain of its own (_OrderedGroup). Solved as written it
lands 0.56 years rms from OxCal on the Hillside `ordered` run, where
supplying the missing Boundary instead -- a different model -- gave 2.33.
Adding the Boundary is still better practice. Dates ordered inside one pair
of Boundaries in any other way -- two Sequences side by side with no
Boundaries of their own -- are not blocks, and are sampled.

A Sequence of Sequences, each with its own Boundaries, IS a chain: it is the
same model as its children written out in place, with an empty group between
one inner Sequence's last Boundary and the next one's first. See _stitched.

So is a chain with dates outside every Boundary at either end -- one group
older than the first Boundary, one younger than the last, each with nothing
ordering its dates against each other. Such a date has a flat prior and is
held only by its end Boundary, so given that Boundary it is independent of
everything else, and the whole group enters as one weight on it:

    g(b_1) = ∏_(dates beyond b_1) G_j(b_1),   G_j = L_j integrated beyond b_1

See _Tails. Several groups in a row out there are blocks, as inside a group.
A date ordered against neither end, and a date out there with an outlier
model, are not taken (tails_with_reason).
"""
import functools
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
from scipy.signal import fftconvolve
from scipy.special import gammaln

from app.services import intervals
from app.services.calibration import SIGMA_1, SIGMA_2, get_curve, hpd_ranges_from_distribution
from app.services.model_tree import SpanGroup, inherited_bounds, plan_model
from app.services.stratigraphy import effective_bounds

# Largest calendar grid the solver will build. Every step of the algorithm is
# O(M^2) in space as well as time, so this caps one intermediate array at
# about 30 MB and the whole solve at a few seconds.
MAX_GRID_POINTS = 2000
# How many groups fit at that point count before the grid coarsens. One M x M
# array of doubles is 32 MB at M = 2000 and one is held per group, so a long
# sequence trades calendar resolution for staying inside the memory budget.
GROUPS_AT_FULL_RESOLUTION = 6
# Finest calendar step the first pass uses. IntCal20/SHCal20 are themselves
# tabulated at 1-20 year intervals.
MIN_GRID_STEP_YEARS = 1.0
# The second pass (see solve and _refined_grid) may go finer, because the
# DISCRETISATION, not the curve, sets how fine is worth having: on the
# Hillside shared-boundary reference model the separation of the two ordered
# groups moved 15.2 -> 14.2 -> 13.8 -> 13.6 years at steps of 2.5, 1.0, 0.45 and
# 0.23, converging near the 13.2 OxCal gives, and still 1 year off at 1.0.
REFINED_MIN_STEP_YEARS = 0.25
# Where the second pass's grid must reach: every posterior out to this much mass
# in either tail, every date's own likelihood support, and a margin of this
# fraction of that width, or this many first-pass steps if more.
REFINE_TAIL_MASS = 1e-6
REFINE_MARGIN_FRACTION = 0.05
REFINE_MARGIN_STEPS = 3
# A second pass is only run when it is at least this much finer, and only kept
# when no distribution has more than REFINED_EDGE_MASS in the outermost 1% of
# its grid (a margin that cut a tail off would show there).
REFINE_MIN_GAIN = 1.5
REFINED_EDGE_MASS = 1e-4
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
# itself. On the Hillside charcoal fixture, integrating it moved the modelled
# dates from 16.1 to 9.9 years rms of OxCal's answer.
CHARCOAL_LOG_SCALE_MAX = 3.0
CHARCOAL_KERNEL_SPAN_TAUS = 10.0
OUTLIER_MODELS = ('general', 'charcoal')
DEFAULT_OUTLIER_MODEL = 'general'
# Resolution the reported HPD ranges are computed at, independent of the
# solver's internal grid step -- see _reported_ranges. Five years is OxCal's
# own default and about as fine as a calibrated date is meaningful.
#
# The DEFAULT: an analysis can ask for another (BayesianConfigInput.resolution,
# OxCal's Options Resolution) -- finer for a recent model whose ranges are a
# few decades wide, coarser for a Pleistocene one. Only the reporting moves;
# the internal grid stays aligned to this default whatever is asked, so a
# model's posteriors, agreement indices and draws do not depend on it.
REPORTING_STEP_YEARS = 5.0
# Density below this fraction of the peak is left off the plotted series, and
# this much padding is kept either side of what remains -- see _plottable.
DISPLAY_THRESHOLD = 1e-4
PLOT_MARGIN_YEARS = 50.0
# The weight of two neighbouring Boundaries in one grid cell: a half, the
# trapezoid's end, as for a date on a Boundary. See _boundary_pair_term.
BOUNDARY_TIE_WEIGHT = 0.5
# A limits-factor g this close to zero, in years, IS zero: rounding of two equal
# differences of grid ages, not a gap. See log_limits_factor_of_span.
LIMITS_ROUNDING_YEARS = 1e-6

# The reason given for groups ordered one after another with no Boundary
# between them where a layout cannot take them as blocks: beside areas side by
# side, in one stage of services/exact_parallel.py.
NO_BOUNDARY_BETWEEN = (
    'two groups are ordered against each other with no Boundary between '
    'them. Add a Boundary at that transition to make the model exactly '
    'solvable'
)


# The reason given for dates inside one pair of Boundaries, or outside every
# Boundary at one end, ordered against each other other than as groups one
# after another (blocks_with_reason).
NOT_ONE_AFTER_ANOTHER = (
    'some dates inside one pair of Boundaries are ordered against each other '
    'other than as one group after another -- two Sequences side by side with '
    'no Boundaries of their own, say. Give each its own Boundaries to make the '
    'model exactly solvable'
)


class Chain:
    """
    A model that is a single line of bounded groups, oldest first, and the
    dates outside every Boundary at either end: `older_tail` older than the
    first Boundary, `younger_tail` younger than the last (see _tail_weight).

    `blocks`, one list per group, holds the group's dates as BLOCKS, oldest
    first, where they are ordered against each other inside the group --
    Phases in a row with no Boundary between them (blocks_with_reason). A
    group with no such order is one block, the default; so is an empty one,
    as no blocks. `older_tail_blocks` and `younger_tail_blocks` do the same
    for the tails.
    """

    def __init__(self, boundary_ids: List[str], groups: List[List[str]],
                 boundary_names: Dict[str, str],
                 older_tail: Optional[List[str]] = None,
                 younger_tail: Optional[List[str]] = None,
                 blocks: Optional[List[List[List[str]]]] = None,
                 older_tail_blocks: Optional[List[List[str]]] = None,
                 younger_tail_blocks: Optional[List[List[str]]] = None):
        # len(boundary_ids) == len(groups) + 1
        self.boundary_ids = boundary_ids
        self.groups = groups
        self.boundary_names = boundary_names
        self.older_tail = list(older_tail or [])
        self.younger_tail = list(younger_tail or [])
        self.blocks = (blocks if blocks is not None
                       else [[list(group)] if group else [] for group in groups])
        self.older_tail_blocks = older_tail_blocks or _one_block(self.older_tail)
        self.younger_tail_blocks = younger_tail_blocks or _one_block(self.younger_tail)

    def ordered(self, index: int) -> bool:
        """Whether group `index` has dates ordered inside it."""
        return len(self.blocks[index]) > 1

    def __repr__(self):
        return f"Chain({len(self.groups)} groups, {len(self.boundary_ids)} boundaries)"


def _one_block(date_ids: Sequence[str]) -> List[List[str]]:
    return [list(date_ids)] if date_ids else []


def layout(root: Dict, measurements: List[Dict],
           allow_inner_order: bool = False, allow_tails: bool = False) -> Optional[Chain]:
    """
    The chain this model reduces to, or None if it does not reduce to one.

    Semantic rather than syntactic: it works from plan_model's output, so
    nesting that adds no ordering of its own -- a Sequence wrapping a single
    Phase, say -- is still recognised as a chain.

    `allow_inner_order` is for services/cross_check.py, which integrates
    groups whose dates are ordered against each other inside one pair of
    Boundaries. It returns the chain of Boundaries and groups without insisting
    that the chain implies every ordering the tree asks for; the caller must
    check the extra ordering is one it can integrate. This solver never asks.

    `allow_tails` lets dates outside every Boundary at the model's two ends
    in, as Chain.older_tail and younger_tail; see tails_with_reason. Off by
    default, because a caller laying out PART of a model -- an area of a site
    (services/exact_parallel.py), or the cross-check's branches -- has dates
    outside that part's Boundaries that are not outside the model's.
    """
    return layout_with_reason(root, measurements, allow_inner_order, allow_tails)[0]


def layout_with_reason(
    root: Dict, measurements: List[Dict], allow_inner_order: bool = False,
    allow_tails: bool = False,
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
    coupled = coupled_dates_reason(plan)
    if coupled is not None:
        return None, coupled

    if not plan.span_groups:
        count = len(plan.boundary_names)
        if count == 0:
            what = 'a Boundary at the start and end of the Sequence'
        elif count == 1:
            what = ('another Boundary, so that two of them enclose the dated '
                    'Phases')
        else:
            what = 'Boundaries either side of the dated Phases'
        return None, (f'no pair of Boundaries encloses any date. Add {what} to '
                      'make the model exactly solvable')

    # Link the groups into paths: each group's younger boundary must be the
    # next group's older boundary, and no boundary may start or end two groups.
    branched = (
        'the model branches: it has parallel groups rather than one line of '
        'phases, so the phases do not share a single chain of Boundaries'
    )
    by_older, by_younger = {}, set()
    for group in plan.span_groups:
        if (group.older_boundary_id in by_older
                or group.younger_boundary_id in by_younger):
            return None, branched            # a boundary starting/ending two groups
        by_older[group.older_boundary_id] = group
        by_younger.add(group.younger_boundary_id)
    heads = [g for g in plan.span_groups if g.older_boundary_id not in by_younger]
    if not heads:
        return None, branched                # every group is in a cycle

    segments, seen = [], set()
    for head in heads:
        segment, current = [], head
        while current is not None:
            if id(current) in seen:
                return None, branched
            seen.add(id(current))
            segment.append(current)
            current = by_older.get(current.younger_boundary_id)
        segments.append(segment)
    if len(seen) != len(plan.span_groups):
        return None, branched                # a cycle hanging off a path

    ordered_groups = _stitched(segments, plan)
    if ordered_groups is None:
        return None, branched                # paths not ordered against each other

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
    unenclosed = [m['id'] for m in measurements if m['id'] not in placed]
    older_blocks: List[List[str]] = []
    younger_blocks: List[List[str]] = []
    if unenclosed:
        if not allow_tails:
            return None, outside_every_boundary_reason(unenclosed, measurements)
        (older_blocks, younger_blocks), reason = tails_with_reason(
            plan, measurements, unenclosed, boundary_ids[0], boundary_ids[-1])
        if reason is not None:
            return None, reason
    older_tail = [d for block in older_blocks for d in block]
    younger_tail = [d for block in younger_blocks for d in block]

    # The chain must already imply every ordering constraint the tree asks
    # for, but for dates ordered against each other INSIDE one boundary pair,
    # which are that group's blocks. Rank boundary i at 2i and group i at
    # 2i+1, oldest first; a constraint is implied when everything on its
    # older side outranks everything on its younger side.
    rank_of_boundary = {bid: 2 * i for i, bid in enumerate(boundary_ids)}
    # A tail sits beyond its end Boundary, one rank further out, and its
    # dates share that rank, as a group's do.
    tail_rank = {**{d: -1 for d in older_tail},
                 **{d: 2 * len(boundary_ids) - 1 for d in younger_tail}}

    def rank(kind: str, tid: str) -> Optional[int]:
        if kind == 'boundary':
            return rank_of_boundary.get(tid)
        if tid in tail_rank:
            return tail_rank[tid]
        index = placed.get(tid)
        return None if index is None else 2 * index + 1

    inner_order = bool(older_blocks and len(older_blocks) > 1
                       or younger_blocks and len(younger_blocks) > 1)
    for older_terms, younger_terms in plan.ordering:
        older_ranks = [rank(k, t) for k, t in older_terms]
        younger_ranks = [rank(k, t) for k, t in younger_terms]
        if any(r is None for r in older_ranks + younger_ranks):
            return None, branched
        if max(older_ranks) >= min(younger_ranks):
            inner_order = True
    groups = [list(g.date_ids) for g in ordered_groups]
    if allow_inner_order or not inner_order:
        return Chain(boundary_ids, groups, dict(plan.boundary_names),
                     older_tail, younger_tail), None

    # Order inside a group: its dates as blocks, and then the whole layout --
    # the chain, each group's blocks, the tails -- held to the model as
    # written, so integrating it cannot answer a different model.
    closed = _closure(written_order(plan))
    blocks = []
    for group in groups:
        group_blocks, reason = blocks_with_reason(group, closed)
        if reason is not None:
            return None, reason
        blocks.append(group_blocks)
    laid: Dict[str, Set[str]] = {}
    _order_chain(laid, boundary_ids, blocks)
    _order_tails(laid, boundary_ids[0], boundary_ids[-1], older_blocks, younger_blocks)
    if _closure(laid) != closed:
        return None, (
            'its dates are ordered in a way one line of phases between '
            'Boundaries cannot express'
        )
    return Chain(boundary_ids, groups, dict(plan.boundary_names), older_tail,
                 younger_tail, blocks, older_blocks, younger_blocks), None


def blocks_with_reason(date_ids: Sequence[str], closed: Dict[str, Set[str]]
                       ) -> Tuple[Optional[List[List[str]]], Optional[str]]:
    """
    (blocks, None): `date_ids` as BLOCKS, oldest first, given `closed`,
    everything each parameter is older than (_closure). Dates in one block
    are not ordered against each other, and every date of a block is older
    than every date of the next: Phases in a row with no Boundary between
    them, or a Sequence of Phases with none, inside one pair of Boundaries.
    One block when nothing orders them. (None, reason) for any other order --
    two such Sequences side by side, say -- which does not reduce to blocks.

    services/cross_check.py's _blocks reads the same structure off the
    sampler's ordering.
    """
    members = list(date_ids)
    if len(members) < 2:
        return _one_block(members), None

    def ordered(a, b):
        return b in closed.get(a, ()) or a in closed.get(b, ())

    parent = {d: d for d in members}

    def find(d):
        while parent[d] != d:
            parent[d] = parent[parent[d]]
            d = parent[d]
        return d

    for i, a in enumerate(members):
        for b in members[i + 1:]:
            if not ordered(a, b):
                parent[find(a)] = find(b)
    found: Dict[str, List[str]] = {}
    for d in members:
        found.setdefault(find(d), []).append(d)
    blocks = list(found.values())
    for block in blocks:
        if any(ordered(a, b) for i, a in enumerate(block) for b in block[i + 1:]):
            return None, NOT_ONE_AFTER_ANOTHER
    # Oldest first: a block is older than every block its first date precedes.
    older_than = {id(block): sum(1 for other in blocks
                                 if other is not block and other[0] in closed.get(block[0], ()))
                  for block in blocks}
    blocks.sort(key=lambda block: -older_than[id(block)])
    for i, older in enumerate(blocks):
        for younger in blocks[i + 1:]:
            if not all(y in closed.get(o, ()) for o in older for y in younger):
                return None, NOT_ONE_AFTER_ANOTHER
    return blocks, None


def _edge(order: Dict[str, Set[str]], older: str, younger: str) -> None:
    order.setdefault(older, set()).add(younger)
    order.setdefault(younger, set())


def _order_blocks(order: Dict[str, Set[str]], blocks: List[List[str]]) -> None:
    """Each block's dates older than every date of the next."""
    for older, younger in zip(blocks, blocks[1:]):
        for o in older:
            for y in younger:
                _edge(order, o, y)


def _order_chain(order: Dict[str, Set[str]], boundary_ids: List[str],
                 blocks: List[List[List[str]]]) -> None:
    """A chain's ordering: each group between its two Boundaries, in blocks."""
    for index, group_blocks in enumerate(blocks):
        older, younger = boundary_ids[index], boundary_ids[index + 1]
        _edge(order, older, younger)
        for block in group_blocks:
            for date_id in block:
                _edge(order, older, date_id)
                _edge(order, date_id, younger)
        _order_blocks(order, group_blocks)


def _order_tails(order: Dict[str, Set[str]], first_id: str, last_id: str,
                 older_blocks: List[List[str]], younger_blocks: List[List[str]]) -> None:
    """Tails' ordering: beyond their end Boundary, in blocks."""
    for block in older_blocks:
        for date_id in block:
            _edge(order, date_id, first_id)
    for block in younger_blocks:
        for date_id in block:
            _edge(order, last_id, date_id)
    _order_blocks(order, older_blocks)
    _order_blocks(order, younger_blocks)


def outside_every_boundary_reason(date_ids: List[str], measurements: List[Dict]) -> str:
    """The reason given for dates no Boundary pair encloses, naming them."""
    by_id = {m['id']: m for m in measurements}
    names = ', '.join(sorted(by_id[d].get('sample_name') or d for d in date_ids[:4]))
    if len(date_ids) > 4:
        names += f' and {len(date_ids) - 4} more'
    return (
        f'{len(date_ids)} date(s) sit outside every Boundary ({names}). Add a '
        'Boundary either side of the phases holding them so they fall inside '
        'the chain'
    )


def tails_with_reason(plan, measurements: List[Dict], date_ids: List[str],
                      first_id: str, last_id: str
                      ) -> Tuple[Tuple[List[List[str]], List[List[str]]], Optional[str]]:
    """
    ((older tail, younger tail), None), each tail as its blocks oldest first
    (blocks_with_reason), for dates outside every Boundary that this solver
    can take, else (([], []), why not).

    A date outside every Boundary gets no span term -- its prior is flat, as
    OxCal's is -- and is held only by its order against the model's first or
    last Boundary. So it must be ordered older than the first or younger than
    the last; one ordered against neither floats free of the model and is
    left to the sampler. One carrying an outlier model is too: nothing holds
    its outlier component, so its answer would depend on how far the
    calculation window happens to reach. Dates at one end ordered against
    each other must be groups one after another, as inside a group.
    """
    by_id = {m['id']: m for m in measurements}
    closed = _closure(written_order(plan))
    older, younger, loose = [], [], []
    for date_id in date_ids:
        if first_id in closed.get(date_id, set()):
            older.append(date_id)
        elif date_id in closed.get(last_id, set()):
            younger.append(date_id)
        else:
            loose.append(date_id)
    if loose:
        return ([], []), outside_every_boundary_reason(loose, measurements)
    tails = []
    for tail in (older, younger):
        blocks, reason = blocks_with_reason(tail, closed)
        if reason is not None:
            return ([], []), reason
        tails.append(blocks)
    flagged = [d for d in older + younger if (by_id[d].get('outlier_prior') or 0) > 0]
    if flagged:
        names = ', '.join(sorted(by_id[d].get('sample_name') or d for d in flagged[:4]))
        return ([], []), (
            f'{len(flagged)} date(s) outside every Boundary carry an outlier model '
            f'({names}). With nothing on their far side to hold it, the outlier '
            'component reaches the edge of the calculation, so they are sampled'
        )
    return (tails[0], tails[1]), None


def written_order(plan) -> Dict[str, Set[str]]:
    """
    {parameter: the parameters the model as written orders directly younger
    than it}: plan_model's ordering, and each group's containment of its dates
    between its two Boundaries.
    """
    order: Dict[str, Set[str]] = {}

    def edge(older: str, younger: str):
        order.setdefault(older, set()).add(younger)
        order.setdefault(younger, set())

    for older_terms, younger_terms in plan.ordering:
        for _ko, older in older_terms:
            for _ky, younger in younger_terms:
                edge(older, younger)
    for group in plan.span_groups:
        edge(group.older_boundary_id, group.younger_boundary_id)
        for date_id in list(group.date_ids) + list(group.contained_ids):
            edge(group.older_boundary_id, date_id)
            edge(date_id, group.younger_boundary_id)
    return order


def _closure(order: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
    """
    Everything each parameter is older than, transitively. Iterative, youngest
    first, so a long model cannot reach Python's recursion limit; a cycle,
    which no well-formed model has, leaves its members unclosed and so unequal
    to anything a layout gives.
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


def coupled_dates_reason(plan) -> Optional[str]:
    """
    Why `plan` cannot be integrated exactly whatever its shape, or None.

    Both of these couple the dates to EACH OTHER rather than only through
    their Boundaries, which is the one thing the message passing below cannot
    express -- the same obstruction the module docstring records for the
    outlier scale, and with the same resolution: say so, and let the caller
    take the sampler or the closed form. Shared with services/exact_parallel.py.
    """
    if plan.deposition_groups:
        return (
            'this model contains a deposition sequence, whose ages are coupled '
            'to each other by the deposition rate rather than only through '
            'their Boundaries, so it cannot be integrated exactly'
        )
    if plan.wiggle_groups:
        return (
            'this model contains a wiggle match, whose dates are one calendar '
            'age with the measured gaps added to it rather than separate '
            'parameters, so it is not a chain of Boundaries. It is solved in '
            'closed form instead (services/wiggle_match.py)'
        )
    return None


def _stitched(segments: List[List[SpanGroup]], plan) -> Optional[List[SpanGroup]]:
    """
    Paths of bounded groups joined into one line, oldest first, or None when
    the tree does not order them one after another.

    A Sequence of Sequences, each bracketed by its own Boundaries, plans as one
    path per inner Sequence: nothing encloses the gap between one's last
    Boundary and the next one's first, only an ordering constraint across it.
    That is exactly an EMPTY group there -- (span)^0 = 1 and the ordering, kept
    strict like every other pair of Boundaries -- which is also what writing
    the inner children out in place plans to. So the paths are joined by one,
    but only where the tree's ordering already forces the earlier path's last
    Boundary older than the later one's first. Anything less would ADD an
    ordering: two independently bracketed branches inside a Phase are paths
    too, and they must stay unchained.

    The gibbs Model sees the same prior from the unstitched plan: its span
    correction groups Boundaries by what the ordering makes comparable, which
    puts every Boundary of the stitched line in one component with the same
    outermost pair, and an empty group contributes nothing to it.
    """
    if len(segments) == 1:
        return segments[0]

    # Everything forced younger than each term, through the ordering and
    # through each group's containment of its dates.
    younger_of: Dict[str, List[str]] = {}

    def edge(older: str, younger: str):
        younger_of.setdefault(older, []).append(younger)

    for older_terms, younger_terms in plan.ordering:
        for _ko, older in older_terms:
            for _ky, younger in younger_terms:
                edge(older, younger)
    for group in plan.span_groups:
        for date_id in list(group.date_ids) + list(group.contained_ids):
            edge(group.older_boundary_id, date_id)
            edge(date_id, group.younger_boundary_id)

    def reachable(start: str) -> set:
        found, stack = set(), [start]
        while stack:
            for nxt in younger_of.get(stack.pop(), ()):
                if nxt not in found:
                    found.add(nxt)
                    stack.append(nxt)
        return found

    below = [reachable(seg[-1].younger_boundary_id) for seg in segments]
    heads = [seg[0].older_boundary_id for seg in segments]
    # A path forced older than k others belongs k places from the end; the
    # check below catches any pair the ordering leaves unrelated.
    order = sorted(range(len(segments)),
                   key=lambda i: -sum(h in below[i] for h in heads))
    joined = list(segments[order[0]])
    for earlier, later in zip(order, order[1:]):
        if heads[later] not in below[earlier]:
            return None
        joined.append(SpanGroup(segments[earlier][-1].younger_boundary_id,
                                heads[later], []))
        joined.extend(segments[later])
    return joined


# --------------------------------------------------------------------------
# Solving
# --------------------------------------------------------------------------

# Inbuilt age (feature spec M1): how much older than its event a sample's
# material may be. Charcoal from heartwood dates the wood, not the fire. A
# date's allowance is `inbuilt_years` and `inbuilt_shape`:
#
#   uniform      any offset from 0 to inbuilt_years, equally likely, never more
#                -- a published MAXIMUM inbuilt age, as a published source can give.
#   exponential  offsets with mean inbuilt_years, small ones likeliest, a long
#                tail and no ceiling -- a TYPICAL age with no maximum known.
#
# The offset touches only its own date, so it is integrated out of that date's
# likelihood here, as the outlier kernels are, and both solvers, the direct
# cross-check and the agreement index see the same term.
INBUILT_SHAPES = ('uniform', 'exponential')
# How far an exponential allowance is followed, in means: 10 keeps all but
# 0.005% of it.
INBUILT_EXPONENTIAL_REACH = 10.0


def inbuilt_reach(measurement: Dict) -> float:
    """How much younger, in years, a date's event may be than its material."""
    years = float(measurement.get('inbuilt_years') or 0.0)
    if years <= 0:
        return 0.0
    if (measurement.get('inbuilt_shape') or 'uniform') == 'exponential':
        return INBUILT_EXPONENTIAL_REACH * years
    return years


def inbuilt_kernel(step: float, measurement: Dict) -> Optional[np.ndarray]:
    """
    A date's inbuilt-age allowance as a convolution kernel on a grid of `step`
    years, or None for a date with none.

    The event is the material's age MINUS the offset, so an event at t is
    supported by the material's likelihood at t + s:

        L_event(t) = integral k(s) L(t + s) ds,    s >= 0

    which puts the kernel's mass at NEGATIVE lag in convolve_on_grid's
    convention -- the side the Charcoal outlier kernel uses, for the same
    reason. Each lag cell takes the allowance's probability over that cell
    exactly (from its CDF), so an allowance that is not a whole number of
    steps, or is shorter than one, still has the right mean.
    """
    years = float(measurement.get('inbuilt_years') or 0.0)
    if years <= 0:
        return None
    shape = measurement.get('inbuilt_shape') or 'uniform'
    if shape not in INBUILT_SHAPES:
        raise ValueError(
            f"sample '{measurement.get('sample_name', measurement.get('id'))}' has unknown "
            f"inbuilt-age shape '{shape}'; expected one of {', '.join(INBUILT_SHAPES)}")
    reach = inbuilt_reach(measurement)
    lags = np.arange(0.0, reach + step, step)
    lo = np.clip(lags - step / 2.0, 0.0, reach)
    hi = np.clip(lags + step / 2.0, 0.0, reach)
    if shape == 'uniform':
        weights = (hi - lo) / years
    else:
        weights = np.exp(-lo / years) - np.exp(-hi / years)
    keep = weights > 0
    lags_used = int(np.nonzero(keep)[0][-1]) + 1
    weights = weights[:lags_used]
    weights = weights / weights.sum()
    return np.concatenate([weights[::-1], np.zeros(len(weights) - 1)])


def _calibration_likelihood(points: np.ndarray, measurement: Dict) -> np.ndarray:
    """The measurement's own likelihood at each calendar age: the age of the
    MATERIAL, with its reservoir offset applied."""
    curve = get_curve(measurement.get('curve_id') or 'intcal20')
    delta_r = measurement.get('delta_r') or 0.0
    delta_r_error = measurement.get('delta_r_error') or 0.0

    c14, curve_error = curve.interpolate(points)
    sigma2 = curve_error ** 2 + measurement['error_1sigma'] ** 2 + delta_r_error ** 2
    lik = np.exp(-0.5 * (c14 - (measurement['age_bp'] - delta_r)) ** 2 / sigma2) / np.sqrt(sigma2)
    # interpolate() CLAMPS beyond the curve's ends rather than extrapolating,
    # which would give a grid point past the curve the finite likelihood of
    # its last point. The curve says nothing there, so neither does the date.
    return np.where((points >= curve.cal_ages[0]) & (points <= curve.cal_ages[-1]), lik, 0.0)


def event_likelihood(grid: np.ndarray, measurement: Dict) -> np.ndarray:
    """
    The likelihood of the EVENT a date stands for, at each age on a regular
    `grid`: its calibration, averaged over its inbuilt-age allowance where it
    has one (inbuilt_kernel).

    The material may be older than anything on the grid, so the calibration
    is evaluated past the grid's old end by the allowance's reach before it is
    smoothed, and only then cut back to the grid.
    """
    grid = np.asarray(grid, dtype=float)
    step = float(grid[1] - grid[0]) if len(grid) > 1 else 1.0
    kernel = inbuilt_kernel(step, measurement)
    if kernel is None:
        return _calibration_likelihood(grid, measurement)
    extra = int(np.ceil(inbuilt_reach(measurement) / step)) + 1
    wide = grid[0] + step * np.arange(len(grid) + extra)
    smoothed = convolve_on_grid(_calibration_likelihood(wide, measurement), kernel)
    return smoothed[:len(grid)]


def _date_likelihood(grid: np.ndarray, measurement: Dict,
                     bounds: Tuple[Optional[float], Optional[float]]) -> np.ndarray:
    """
    One date's likelihood on `grid`: its calibration, with its reservoir
    offset applied and its inbuilt-age allowance integrated out
    (event_likelihood), and its TPQ/TAQ limiters imposed as a hard truncation.
    The limiters bound the EVENT, so they cut after the allowance, not before.
    """
    lik = event_likelihood(grid, measurement)

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


@functools.lru_cache(maxsize=4)
def _scale_kernels(step: float, half_width: float, model: str) -> np.ndarray:
    """Every scale node's kernel for `model`, one row each, on the shared lag
    axis (_outlier_kernel_at_scale). Cached: the scale weighing (_weigh_scales)
    asks for the same grid's kernels once per pass. Read-only."""
    rows = np.array([_outlier_kernel_at_scale(step, half_width, model, log_scale)
                     for log_scale in scale_nodes(model)])
    rows.setflags(write=False)
    return rows


def _weighted_kernel(step: float, half_width: float, model: str,
                     weights: np.ndarray) -> np.ndarray:
    """The offset kernel with the scale mixed over `weights`, one per node of
    scale_nodes(model): its posterior once weighed (_weigh_scales), where
    _outlier_kernel mixes over the prior."""
    kernel = np.asarray(weights, dtype=float) @ _scale_kernels(step, half_width, model)
    return kernel / kernel.sum()


def _with_outlier_component(
    grid: np.ndarray, inlier: np.ndarray, prior: float,
    model: str = DEFAULT_OUTLIER_MODEL, scale_weights: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    (mixture, outlier_component) for one date, both normalised on `grid`.
    `scale_weights`, one per node of scale_nodes(model), mixes the offset
    over the scale's posterior rather than its prior (_weigh_scales).

    Integrating out a Bernoulli outlier indicator is exact and costs the
    solver nothing, because the indicator touches only this date's own
    likelihood:

        L_mix(t) = (1 - p) * L_inlier(t)  +  p * L_outlier(t)

    Every date enters the chain through its likelihood alone, so the message
    passing is untouched -- the outlier model is a property of a date, not of
    the inference.
    """
    step = float(grid[1] - grid[0])
    half_width = max(OUTLIER_KERNEL_HALF_WIDTH_YEARS, float(grid[-1] - grid[0]))
    if scale_weights is None:
        kernel = _outlier_kernel(step, half_width, model)
    else:
        kernel = _weighted_kernel(step, half_width, model, scale_weights)
    broad = convolve_on_grid(inlier, kernel)
    # NOT renormalised over the grid. The convolution of two normalised
    # densities is already proper on the real line, and most of its mass lies
    # off this grid -- which is exactly the statement that a flagged date's
    # calendar age could be anywhere. Rescaling what remains to one would
    # inflate the component by the reciprocal of the retained mass and make the
    # outlier probability depend on the grid width: it put the fifteen sound
    # Dune Section dates at 9-14% against OxCal's 0.6-1.5%. The mixture is
    # therefore sub-unit here, the missing mass being the outlier possibility
    # the window does not cover.
    return (1.0 - prior) * inlier + prior * broad, broad


def _effective_date_bounds(measurement: Dict, element_bounds: Dict) -> Tuple:
    el_taq, el_tpq = element_bounds.get(measurement.get('element_id'), (None, None))
    return effective_bounds(measurement, {'taq_age_bp': el_taq, 'tpq_age_bp': el_tpq})


def date_tables(
    grid: np.ndarray, measurements: List[Dict], element_bounds: Dict,
    scale_weights: Optional[Dict[str, np.ndarray]] = None,
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

    `scale_weights`, {outlier model: weight per node of scale_nodes(model)},
    mixes each flagged date's offset over its model's scale weighed from the
    data (_weigh_scales) instead of over the prior. The sampler draws the
    scale itself and never passes it.
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
            mixture, broad = _with_outlier_component(
                grid, lik, prior, model_name,
                None if scale_weights is None else scale_weights.get(model_name))
            outlier_parts[m['id']] = (lik, broad, float(prior))
            lik = mixture
        likelihood[m['id']] = lik
        cumulative[m['id']] = np.cumsum(lik) * dt

    return likelihood, cumulative, outlier_parts


def _dates_extent(measurements: List[Dict], element_bounds: Dict
                  ) -> Tuple[float, float, float, float]:
    """
    (youngest, oldest) calendar age any date's likelihood reaches, cal BP, and
    the (youngest, oldest) the curves in use cover.
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
            # An inbuilt-age allowance lets the event be younger than its
            # material, so the grid reaches that much further on the young side.
            lo = min(lo, float(cal[significant[0]]) - inbuilt_reach(m))
            hi = max(hi, float(cal[significant[-1]]))
        taq, tpq = _effective_date_bounds(m, element_bounds)
        if taq is not None:
            lo = max(lo, taq)
        if tpq is not None:
            hi = min(hi, tpq)

    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        raise ValueError('the dates have no overlapping calendar range to solve over')
    return lo, hi, curve_lo, curve_hi


def _point_budget(num_groups: int) -> float:
    """Grid points the memory budget allows for a chain of `num_groups` groups:
    the full count up to GROUPS_AT_FULL_RESOLUTION, fewer beyond."""
    return MAX_GRID_POINTS / max(1.0, np.sqrt(num_groups / GROUPS_AT_FULL_RESOLUTION))


def _build_grid(measurements: List[Dict], element_bounds: Dict,
                num_groups: int = 1) -> np.ndarray:
    """
    A calendar grid wide enough for the dates and the boundaries either side of
    them, at the finest step the memory budget allows.

    One M x M array of doubles is 32 MB at M = 2000 and a transfer matrix is
    held per group, so the point count scales down as the model grows: a long
    sequence gets a coarser calendar step rather than an out-of-memory kill.

    This is the FIRST pass's grid, padded blind for the boundaries' tails;
    solve() then narrows it to where the posterior is (_refined_grid).
    """
    lo, hi, curve_lo, curve_hi = _dates_extent(measurements, element_bounds)

    pad = max(MIN_BOUNDARY_PAD_YEARS, BOUNDARY_WINDOW_FACTOR * (hi - lo))
    lo = max(curve_lo, lo - pad)
    hi = min(curve_hi, hi + pad)

    # Up to GROUPS_AT_FULL_RESOLUTION groups the full point count fits.
    budget = _point_budget(num_groups)
    step = _reporting_aligned_step(max(MIN_GRID_STEP_YEARS, (hi - lo) / budget))
    # An explicit count rather than np.arange(lo, hi + step, step), whose
    # length depends on floating-point rounding at the end point.
    count = int(np.ceil((hi - lo) / step - 1e-9)) + 1
    return lo + step * np.arange(count)


def _refined_grid(grid: np.ndarray, densities: List[np.ndarray],
                  measurements: List[Dict], element_bounds: Dict,
                  num_groups: int) -> Optional[np.ndarray]:
    """
    The second pass's grid, or None when it would not be meaningfully finer.

    The first pass pads the dates by six times their own width for the
    boundaries' tails, blind, so on a long model most of its points lie where
    no posterior is: a twenty-phase sequence over 1,500 years was solved at a
    15-year step across 14,800 years. Having solved it once, the tails are
    known. This grid covers every first-pass posterior in `densities` out to
    REFINE_TAIL_MASS in either tail and every date's own likelihood support,
    with a margin, at the finest step the same point budget allows. solve()
    passes the Boundaries' alone: every date lies between the outermost two,
    so no date's tail reaches past theirs.

    The dates' supports are included so each date's likelihood is normalised,
    and its outlier component convolved, over all of it, exactly as on the
    first grid; only the empty padding goes.
    """
    lo, hi = np.inf, -np.inf
    for density in densities:
        total = float(density.sum())
        if total <= 0:
            continue
        cdf = np.cumsum(density) / total
        lo = min(lo, float(grid[min(len(grid) - 1, np.searchsorted(cdf, REFINE_TAIL_MASS))]))
        hi = max(hi, float(grid[min(len(grid) - 1,
                                    np.searchsorted(cdf, 1.0 - REFINE_TAIL_MASS))]))
    dates_lo, dates_hi, _curve_lo, _curve_hi = _dates_extent(measurements, element_bounds)
    lo, hi = min(lo, dates_lo), max(hi, dates_hi)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return None
    first_step = float(grid[1] - grid[0])
    margin = max(REFINE_MARGIN_STEPS * first_step, REFINE_MARGIN_FRACTION * (hi - lo))
    lo, hi = max(float(grid[0]), lo - margin), min(float(grid[-1]), hi + margin)
    step = _reporting_aligned_step(max(REFINED_MIN_STEP_YEARS,
                                       (hi - lo) / _point_budget(num_groups)))
    if step * REFINE_MIN_GAIN > first_step:
        return None
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


def _finite_peak(log_weights: np.ndarray) -> float:
    """The finite maximum _rescaled takes out, 0 when there is none."""
    finite = np.isfinite(log_weights)
    if not np.any(finite):
        return 0.0
    # where= rather than log_weights[finite]: no copy of an M x M array.
    return float(np.max(log_weights, where=finite, initial=-np.inf))


def _rescaled(log_weights: np.ndarray) -> np.ndarray:
    """Shift log weights by their own finite maximum, ready to exponentiate.

    Per-array rather than against a shared constant: dividing a joint weight by
    one date's interval integral can raise it by hundreds of nats, which
    overflows a shared shift.
    """
    finite = np.isfinite(log_weights)
    if not np.any(finite):
        return np.where(finite, log_weights, -np.inf)
    return np.where(finite, log_weights - _finite_peak(log_weights), -np.inf)


def _interval_integral(cumulative: np.ndarray, density: np.ndarray,
                       step: float, out: Optional[np.ndarray] = None) -> np.ndarray:
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

    `out`, an M x M array, takes the result in place of a new one: the solver
    forms this once per date per group and again per date marginal, and at
    M = 2000 every fresh M x M array is 32 MB to allocate and fault in.
    """
    padded = np.concatenate([[0.0], cumulative])
    half = 0.5 * step * density
    # (C[o] - h[o]) - (C[y-1] + h[y]): each end's half folded into its own
    # vector first, so the matrix is one subtraction rather than four passes.
    F = np.subtract((padded[1:] - half)[None, :], (padded[:-1] + half)[:, None], out=out)
    return np.maximum(F, 0.0, out=F)


def _log_interval_integral(cumulative: np.ndarray, density: np.ndarray,
                           step: float, out: Optional[np.ndarray] = None) -> np.ndarray:
    F = _interval_integral(cumulative, density, step, out=out)
    with np.errstate(divide='ignore'):
        return np.log(F, out=F)                  # an empty interval is -inf


def _log_date_factor(cumulative: np.ndarray, density: np.ndarray,
                     step: float, out: Optional[np.ndarray] = None) -> np.ndarray:
    """
    One date's factor in its group's transfer matrix, in the log, indexed
    [younger, older]: its interval integral F where the boundaries are apart,
    and its likelihood where they share a cell.

    The second is the continuum's limit. A group of K dates enters as
    span^(-K) prod F_j, and F_j / span tends to L_j(b) as the span closes, so
    two boundaries meeting at b leave the dates' likelihoods at b -- times the
    tie's own weight, which _boundary_pair_term carries. Kept as one matrix
    per date so a date's own factor can be divided back out of the transfer
    (the date marginals in _solve_on_grid) on and off the diagonal alike --
    which needs it formed by this one function both times, so a cell that is
    -inf in the factor is -inf in the transfer too.
    """
    log_f = _log_interval_integral(cumulative, density, step, out=out)
    with np.errstate(divide='ignore'):
        np.fill_diagonal(log_f, np.log(density))
    return log_f


def _boundary_pair_term(grid: np.ndarray, num_dates: int) -> np.ndarray:
    """
    A group's span factor, in the log, indexed [younger, older]: -K log(span)
    where the older boundary is older, log BOUNDARY_TIE_WEIGHT where the two
    share a cell, -inf where they cross.

    Two boundaries may share a cell at half weight. In the continuum their
    meeting is a set of measure zero, and on a grid the sum over ordered pairs
    is the integral over a triangle, whose diagonal the trapezoid rule weighs
    by a half -- the same rule as a date on a boundary (_interval_integral).
    Keeping them a cell apart instead drops that edge altogether, an error of
    first order in the step that grows with the number of boundaries: on a
    test sequence of ten, the modelled Boundaries moved 0.035 in the
    probability of being older than a given year between a 1.25-year step and
    the converged answer, 0.0065 with the half. Counting the tie whole is as
    wrong the other way. The span correction counts arrangements the same way
    (log_interior_placements), so ties add nothing it does not divide out --
    the reason they were once forbidden.

    The span factor at a shared cell is the tie weight alone: the dates'
    likelihoods there stand in for span^(-K) prod F (_log_date_factor).
    """
    span = grid[None, :] - grid[:, None]               # [younger, older]
    apart = span > 0
    with np.errstate(divide='ignore', invalid='ignore'):
        term = np.where(apart, -num_dates * np.log(np.where(apart, span, 1.0)), -np.inf)
    np.fill_diagonal(term, np.log(BOUNDARY_TIE_WEIGHT))
    return term


def log_interior_placements(span_cells: np.ndarray, num_interior: int) -> np.ndarray:
    """
    log of the number of ways `num_interior` ordered boundaries fit inside a
    span of `span_cells` grid cells, each TIE between neighbours -- two
    boundaries in one cell, an interior one on an end included -- counting a
    half (BOUNDARY_TIE_WEIGHT). -inf for a negative span.

    The exact discrete version of the volume the UniformSpanPrior correction
    divides out, and a COUNT rather than a power -- see _span_prior_correction
    for why a power is wrong on a grid. Sorting the arrangements by how many of
    the k + 1 gaps between neighbours are empty, z of them:

        W(j, k) = sum over z of  C(k + 1, z) 2^-z C(j - 1, k - z)     j >= 1
        W(0, k) = 2^-k

    W(0, k) is every boundary in one cell, k + 1 ties, counted at twice their
    weight: the outer pair's own tie is the end of the trapezoid over the span,
    and dividing by it would undo that half. Brute-forced in
    tests/test_exact_chain.py.

    Counted with half-weight ties because that is how the solvers place
    boundaries (see _boundary_pair_term): the count has to be of the same
    arrangements the posterior sums over, or the correction leaves the excess
    in. Strictly separated boundaries -- C(j - 1, k) -- converge on the
    continuum's j^k / k! only to first order in the step, and that error
    reached the answers: on a test sequence of ten Boundaries the modelled
    Boundaries moved 0.035 in the probability of being older than a given year
    between a 1.25-year step and the converged answer. Half-weight ties make
    it second order (0.0065 there).

    Shared with services/gibbs.py so the two solvers cannot drift apart. Defined
    only up to a constant factor, like the correction itself: the
    1/num_interior! of the continuum expression is absorbed into the posterior's
    normalisation.
    """
    j = np.asarray(span_cells, dtype=float)
    k = int(num_interior)
    out = np.full(np.shape(j), -np.inf)
    out[j == 0] = -k * np.log(2.0)
    some = j >= 1
    if some.any():
        terms = []
        for z in range(k + 1):
            m = k - z                                  # gaps left to fill
            term = np.full(int(some.sum()), -np.inf)
            fits = j[some] - 1 >= m
            js = j[some][fits]
            term[fits] = (gammaln(k + 2.0) - gammaln(z + 1.0) - gammaln(k + 2.0 - z)
                          + z * np.log(BOUNDARY_TIE_WEIGHT)
                          + gammaln(js) - gammaln(m + 1.0) - gammaln(js - m))
            terms.append(term)
        out[some] = np.logaddexp.reduce(np.stack(terms), axis=0)
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
    #
    # "Non-positive" has to allow for rounding. The span and the limits are
    # both differences of grid ages, so where they are equal g is zero only in
    # exact arithmetic. On a step binary cannot represent -- the 5/3 or 5/6
    # years _reporting_aligned_step often picks -- it comes out as +/-1e-13,
    # and the positive half of that weighted one span by 1e13. On an
    # eleven-date phase whose outermost dates do not overlap, that span then
    # carried the posterior: Start came back with a mean of 3882 cal BP rather
    # than 4071, which the same model on a 1.25-year step agrees with, and the
    # dates moved up to 158 years.
    out = np.zeros(g.shape)
    positive = g > LIMITS_ROUNDING_YEARS
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

        1 / W(span in cells, n - 2)

    Placing n boundaries in order across a span leaves the n-2 interior ones
    uniform within it, so the induced prior carries a factor for that freedom
    and favours long sequences purely for having more room to arrange them in.
    Dividing it out makes the span prior independent of how finely the sequence
    is subdivided -- Nicholls and Jones (2001), and OxCal's default.

    WHY A COUNT AND NOT (b_first - b_last)^-(n-2). The continuum factor is
    span^(n-2), but the model this solver integrates is discrete, and what has
    to be divided out is the number of ways the grid lets k ordered interior
    boundaries sit across a span of j cells -- W(j, k) of
    log_interior_placements, which counts a tie between neighbours at the half
    the transfer matrices give it -- not (j*step)^k. The power is wrong most at
    SMALL j, where the span prior concentrates its mass.

    Measured with the likelihoods switched off, so the reported posterior IS the
    prior and the answer is known in closed form. Total-variation distance of the
    oldest boundary's marginal from that, at a step of 1.33 years, power ->
    count: 0.39% -> 0.00% at n = 3, 0.93% -> 0.00% at n = 4, 1.56% -> 0.00%
    at n = 5 (with boundaries then kept a cell apart, and the count C(j-1, k)
    to match). The error was first-order in the step and so shrank under
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
        outlier/Dune            0.18 -> 0.16     20.7 -> 18.5
        charcoal/Hillside      10.12 -> 8.37     51.4 -> 40.3

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
    when nothing orders them. See tests/hillside_charcoal_fixture's WHAT IS LEFT
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
        # 1 / (ways the interior boundaries fit) -- A.29, 2001 eq 6. A function
        # of the span in cells alone, so evaluated once per span and gathered.
        by_span = log_interior_placements(index, exponent)
        log_count = np.where(cells >= 0, by_span[np.maximum(cells, 0)], -np.inf)
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


# Distinct conditioning rows formed at a time when sampling the joint
# posterior. One block holds a (block x M) float matrix, so this is a memory
# knob rather than a statistical one: 512 x 2000 doubles is 8 MB.
JOINT_DRAW_BLOCK = 512


def _draw_from_rows(rng, rows_for, keys: np.ndarray) -> np.ndarray:
    """
    One column index per draw, draw k an inverse-CDF draw from the weights of
    row keys[k]; rows_for(distinct keys) returns those rows as a (len x M)
    matrix.

    Draws that condition on the same row share its cumulative sum. There are
    far fewer distinct rows than draws -- under 1% of the 20,000 joint draws
    on a plain chain, and 5-44% on the span-prior path, where a row is a
    (boundary, last boundary) pair -- and one cumulative per DRAW was half of
    a twenty-phase solve.

    Each row is searched against its own cumulative only, so rows on wildly
    different scales cannot interfere. They do differ: a conditional's rows
    are products of message entries that run from 1 down to 1e-60. This used
    to lift every row by its row number times the largest total and run one
    searchsorted over the flattened lot, and a row totalling 1e-13 beside one
    totalling 19 vanished into the lift's rounding: its draw landed on column
    0, a cell of zero weight, so a boundary was drawn where it cannot be. On
    the span-prior path the next conditional then came out empty; on the plain
    path the impossible draw went through silently.

    The uniforms are taken one per draw in draw order, and each row's weights
    and cumulative are formed exactly as a row per draw would form them, so
    the draws are exactly those of a row-per-draw loop on the same generator:
    grouping changes the cost and nothing else.
    """
    count = len(keys)
    uniforms = rng.random(count)
    picked = np.empty(count, dtype=np.int64)
    distinct, which = np.unique(keys, return_inverse=True)
    which = which.ravel()
    order = np.argsort(which, kind='stable')
    starts = np.searchsorted(which[order], np.arange(len(distinct) + 1))
    for first in range(0, len(distinct), JOINT_DRAW_BLOCK):
        last = min(first + JOINT_DRAW_BLOCK, len(distinct))
        weights = rows_for(distinct[first:last])
        cumulative = np.cumsum(weights, axis=1)
        totals = cumulative[:, -1]
        if np.any(totals <= 0) or not np.all(np.isfinite(totals)):
            raise ValueError('a conditional came out empty while drawing from the '
                             'joint posterior; the model may be contradictory')
        # u * total < total in exact arithmetic, but can round up to it; the
        # last column with weight is where such a draw belongs.
        last_with_weight = weights.shape[1] - 1 - np.argmax(weights[:, ::-1] > 0, axis=1)
        for row in range(last - first):
            draws = order[starts[first + row]:starts[first + row + 1]]
            # The first column whose cumulative EXCEEDS the target, which
            # always carries weight: a zero cell repeats its predecessor's
            # cumulative, so it cannot be the first to exceed anything.
            found = np.searchsorted(cumulative[row], uniforms[draws] * totals[row],
                                    side='right')
            picked[draws] = np.minimum(found, last_with_weight[row])
    return picked


def _messages(chain: Chain, allowed: Dict, transfer,
              end_weights: Optional[Dict[str, np.ndarray]] = None):
    """
    Forward/backward over the boundaries, the O(M^2) case.

    `end_weights`, {boundary id: weight over the grid}, multiplies the first
    or last boundary's allowed window: the weight dates outside every
    Boundary put on it (_tail_weight).

    Returns (pair_weight, boundary_density, draw_boundaries, log_mass):
    pair_weight(k) is everything outside group k as a matrix indexed [younger
    boundary, older boundary], boundary_density(i) is boundary i's unnormalised
    marginal, draw_boundaries(rng, count) returns joint draws -- see below --
    and log_mass is the log of the total weight of the chain as `transfer`
    scales it, which solve turns into the model's evidence.

    Each message is scaled to a peak of one as it is formed, and the scales
    kept in the log, so a long chain can neither underflow nor overflow and the
    total survives the rescaling.
    """
    log_scale = 0.0
    weights = end_weights or {}
    alpha = [np.where(allowed[chain.boundary_ids[0]], 1.0, 0.0)
             * weights.get(chain.boundary_ids[0], 1.0)]
    for i in range(len(chain.groups)):
        message = transfer(i) @ alpha[i]
        peak = float(message.max())
        if peak > 0:
            message = message / peak
            log_scale += np.log(peak)
        alpha.append(message)
    beta = [None] * len(chain.boundary_ids)
    beta[-1] = (np.where(allowed[chain.boundary_ids[-1]], 1.0, 0.0)
                * weights.get(chain.boundary_ids[-1], 1.0))
    for i in range(len(chain.groups) - 1, -1, -1):
        message = beta[i + 1] @ transfer(i)
        peak = float(message.max())
        beta[i] = message / peak if peak > 0 else message
    total = float((alpha[-1] * beta[-1]).sum())
    log_mass = (np.log(total) + log_scale) if total > 0 else -np.inf

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
            indices[:, i] = _draw_from_rows(
                rng, lambda rows, m=matrix, a=alpha[i]: m[rows] * a[None, :],
                indices[:, i + 1])
        return indices

    return (lambda k: alpha[k][None, :] * beta[k + 1][:, None],
            lambda i: alpha[i] * beta[i],
            draw_boundaries, log_mass)


def _normalised(matrix: np.ndarray) -> np.ndarray:
    """Scale a matrix by its own peak, so a chain of matrix products does not
    underflow. Everything built from these is normalised per-distribution at
    the end, so the discarded factors do not matter."""
    return _scaled_by_peak(matrix)[0]


def _scaled_by_peak(matrix: np.ndarray) -> Tuple[np.ndarray, float]:
    """(matrix scaled to a peak of one, log of the peak), for a caller that
    needs the discarded factor back -- the evidence does."""
    peak = float(matrix.max())
    return (matrix / peak, float(np.log(peak))) if peak > 0 else (matrix, 0.0)


class _ChainProducts:
    """
    The part of _messages_with_span_prior that does not depend on the factor
    coupling a chain's two ends: the prefix products A and suffix products D
    over its transfer matrices (see that function for their definitions).

    Formed once per chain. services/exact_parallel.py needs a chain's
    end-to-end product before it knows the chain's end factor, which there is
    the weight of everything outside the chain and depends on that product
    through the rest of the model; _messages_with_end_factor folds the factor
    in afterwards and consumes these.
    """

    def __init__(self, chain: Chain, allowed: Dict, transfer):
        self.chain = chain
        self.transfer = transfer
        num_groups = len(chain.groups)
        self.first_ok = np.where(allowed[chain.boundary_ids[0]], 1.0, 0.0)
        self.last_ok = np.where(allowed[chain.boundary_ids[-1]], 1.0, 0.0)
        # A[k]: from the first boundary to boundary k. A[0] is the identity, so
        # that a chain of one group reduces to the plain case.
        self.log_scale = 0.0
        A = [np.diag(self.first_ok)]
        for i in range(num_groups):
            product, factor = _scaled_by_peak(A[i] @ transfer(i).T)
            A.append(product)
            self.log_scale += factor
        # D[k]: from boundary k to the last.
        D = [None] * (num_groups + 1)
        D[num_groups] = np.diag(self.last_ok)
        for i in range(num_groups - 1, -1, -1):
            D[i] = _normalised(transfer(i).T @ D[i + 1])
        self.A, self.D = A, D

    @property
    def end_to_end(self) -> np.ndarray:
        """A[n], indexed [first boundary, last boundary]: the chain's weight
        with both ends held, scaled down by exp(log_scale)."""
        return self.A[-1]


def _messages_with_span_prior(chain: Chain, allowed: Dict, transfer,
                              correction: np.ndarray,
                              end_weights: Optional[Dict[str, np.ndarray]] = None):
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
    but its diagonal. Measured on the shared-boundary Hillside fixture the
    solve went from 5.1 s to about 2 s for identical output.
    """
    if end_weights:
        # Into the end factor, which couples the two ends anyway, and only
        # there: the products already carry each end's 0/1 window, and a
        # weight there as well would count it twice.
        size = correction.shape[0]
        first = np.broadcast_to(end_weights.get(chain.boundary_ids[0], 1.0), (size,))
        last = np.broadcast_to(end_weights.get(chain.boundary_ids[-1], 1.0), (size,))
        correction = correction * last[:, None] * first[None, :]   # [last, first]
    return _messages_with_end_factor(_ChainProducts(chain, allowed, transfer), correction)


def _messages_with_end_factor(products: _ChainProducts, correction: np.ndarray):
    """
    _messages_with_span_prior's results from a chain's products and the factor
    coupling its ends, `correction`, indexed [last boundary, first boundary]:
    the span correction on a chain, the weight of everything outside an area
    in services/exact_parallel.py. Consumes `products`.
    """
    chain, transfer, D = products.chain, products.transfer, products.D
    num_groups = len(chain.groups)
    # C indexed [oldest, youngest], restricted to allowed ends.
    C, log_scale = _scaled_by_peak(
        correction.T * products.first_ok[:, None] * products.last_ok[None, :])
    log_scale += products.log_scale

    # L[k] = A[k]^T C, indexed [b_k, b_last]: everything before boundary k
    # with the end-to-end factor folded in. Only A[n] is needed after this
    # (by draw_boundaries), so the rest of A is released.
    A = products.A
    A_last = A[num_groups]
    L = []
    for k in range(num_groups + 1):
        L.append(A[k].T @ C)
        A[k] = None                       # never both lists whole at once
    products.A = None
    total = float((C * A_last).sum())
    log_mass = (np.log(total) + log_scale) if total > 0 else -np.inf

    def pair_weight(k):
        # [younger, older], formed that way round rather than transposed after.
        return D[k + 1] @ L[k].T

    def boundary_density(i):
        # diag(L[i] @ D[i].T) without forming the product.
        return np.einsum('ij,ij->i', L[i], D[i])

    def draw_boundaries(rng, count: int,
                        ends: Optional[Tuple[np.ndarray, np.ndarray]] = None) -> np.ndarray:
        """
        Joint draws, as in the plain case, but the end factor makes the chain
        non-Markov so the sampling order changes.

        C couples b_first to b_last, so there is no backward pass over single
        boundaries. CONDITIONAL on both ends, though, C is a constant and the
        rest is an ordinary chain again: the two ends are drawn together from
        their joint C * A[n], and the interior filled in forwards, each boundary
        from T_i(b_i, b_(i+1)) D_(i+1)(b_(i+1), b_last) given the one before it
        and the far end.

        `ends`, as (first, last) grid indices one per draw, skips the first
        step: services/exact_parallel.py draws an area's ends from the rest of
        the model and has only its interior filled in here.

        The last step needs no special case: D[n] is diagonal on the allowed
        last boundary, so the final draw can only land on the b_last already
        drawn, which is the consistency check falling out of the maths rather
        than being imposed on it.
        """
        indices = np.empty((count, num_groups + 1), dtype=np.int64)
        if ends is None:
            joint = C * A_last                        # [b_first, b_last]
            width = joint.shape[1]
            flat = _sample_indices(rng, joint.ravel(), count)
            indices[:, 0] = flat // width
            indices[:, num_groups] = flat % width
        else:
            indices[:, 0], indices[:, num_groups] = ends
        for i in range(num_groups - 1):
            # Contiguous copies, so each row gathered from them is one read
            # rather than M reads a matrix-width apart.
            older_to_younger = np.ascontiguousarray(transfer(i).T)   # [older, younger]
            reach = np.ascontiguousarray(D[i + 1].T)                 # [b_last, b_(i+1)]
            # The row a draw conditions on is the PAIR (b_i, b_last).
            width = reach.shape[0]
            indices[:, i + 1] = _draw_from_rows(
                rng,
                lambda pairs, a=older_to_younger, r=reach, w=width: a[pairs // w] * r[pairs % w],
                indices[:, i] * width + indices[:, num_groups])
        return indices

    return pair_weight, boundary_density, draw_boundaries, log_mass


def _draw_dates(rng, chain: Chain, cumulative: Dict[str, np.ndarray],
                boundary_draws: np.ndarray,
                likelihood: Optional[Dict[str, np.ndarray]] = None,
                step: Optional[float] = None) -> Dict[str, np.ndarray]:
    """
    A calendar index for every date, one per joint boundary draw.

    Conditional on its enclosing boundaries a date is its own likelihood
    truncated to that interval -- that IS the phase model -- and a group's dates
    are then conditionally independent, so each is an inverse-CDF draw on the
    interval.

    With `likelihood` and `step` the two end cells, where the date sits on a
    boundary, count half, as in the interval integral the solver's weights use
    (_interval_integral) and in the date marginals, so the draws come from the
    same joint posterior the marginals describe. Without them every cell of the
    interval counts whole, which moves a drawn date by at most a cell.
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
            uniform = rng.random(len(low))
            if likelihood is not None and step is not None:
                picked, empty = _pick_in(cum, likelihood[date_id], low, high, step,
                                         uniform, high_half=True)
            else:
                floor, ceiling = below[low], cum[high]
                picked = np.searchsorted(cum, floor + uniform * (ceiling - floor))
                empty = ceiling <= floor
            # An interval holding no likelihood at all (a date whose support
            # lies entirely outside where its boundaries landed) still has to
            # yield a value inside it rather than an out-of-range index.
            if np.any(empty):
                spread = (low + uniform * (high - low + 1)).astype(np.int64)
                picked = np.where(empty, spread, picked)
            draws[date_id] = np.clip(picked, low, high)
    return draws


def _pick_in(cum: np.ndarray, density: np.ndarray, low: np.ndarray, high: np.ndarray,
             step: float, uniform: np.ndarray, high_half: bool
             ) -> Tuple[np.ndarray, np.ndarray]:
    """
    (cell, empty) per draw: an inverse-CDF draw at `uniform` from `density`
    over cells low..high, half weight at `low`, and at `high` too with
    `high_half`; `empty` where the interval holds none.
    """
    low_end = 0.5 * step * density[low]
    high_end = (0.5 if high_half else 1.0) * step * density[high]
    if not high_half:
        high_end = np.where(high > low, high_end, 0.0)
    # Interior cells low+1 .. high-1, whole.
    interior = np.where(high > low + 1, cum[np.maximum(high - 1, 0)] - cum[low], 0.0)
    total = low_end + interior + high_end
    target = uniform * total
    inside = np.searchsorted(cum, cum[low] + (target - low_end), side='right')
    picked = np.where(target < low_end, low,
                      np.where(target < low_end + interior,
                               np.clip(inside, low + 1, np.maximum(high - 1, low + 1)),
                               high))
    return picked, total <= 0


def _draw_in(rng, cum: np.ndarray, density: np.ndarray, low: np.ndarray,
             high: np.ndarray, step: float, high_half: bool) -> np.ndarray:
    """_pick_in with its own uniforms, and a cell inside the interval where it
    holds no likelihood, as _draw_dates does."""
    uniform = rng.random(len(low))
    picked, empty = _pick_in(cum, density, low, high, step, uniform, high_half)
    if np.any(empty):
        spread = (low + uniform * (high - low + 1)).astype(np.int64)
        picked = np.where(empty, spread, picked)
    return np.clip(picked, low, high)


def solve(measurements: List[Dict], root: Dict, chain: Chain,
          num_samples: int = 4000, seed: int = 42,
          uniform_span_prior: bool = True,
          estimate_durations: bool = False,
          dispersal: Optional[Dict] = None,
          interval_draws: int = intervals.DEFAULT_DRAWS,
          resolution: float = REPORTING_STEP_YEARS) -> Dict:
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

    `resolution` is the calendar step, in years, the HPD ranges and any
    durations are reported at -- see REPORTING_STEP_YEARS.

    Solved twice where it pays: the Boundaries alone on a grid padded blind
    for their tails, then everything on one covering only where they found
    posterior mass, at a finer step (_refined_grid). `grid` reports the one
    used, with `first_pass_step` when it was refined. `log_evidence` is the
    model's evidence on that grid, for comparing solves of the same dates on
    the same grid.
    """
    element_bounds = inherited_bounds(root)
    options = dict(num_samples=num_samples, seed=seed,
                   uniform_span_prior=uniform_span_prior,
                   estimate_durations=estimate_durations, dispersal=dispersal,
                   interval_draws=interval_draws, resolution=resolution)
    # Two passes. The first grid is padded blind for the boundaries' tails;
    # the second covers only where the first found posterior mass, at the
    # finer step the same point budget then allows (_refined_grid). Kept only
    # if nothing runs off its edge, else the first pass stands.
    #
    # The first pass solves the Boundaries alone. Every date lies between the
    # outermost two, so their tails reach at least as far as any date's and
    # place the second grid by themselves. Its date marginals, summaries and
    # joint draws were 33-48% of a solve, discarded whenever the second pass
    # was kept, so they are formed only when the first pass is the answer.
    return _two_pass(
        lambda grid, boundaries_only, **extra: _solve_on_grid(
            grid, measurements, root, chain, element_bounds,
            boundaries_only=boundaries_only, **extra, **options),
        measurements, element_bounds, len(chain.groups), seed)


def _two_pass(solve_on_grid, measurements: List[Dict], element_bounds: Dict,
              num_groups: int, seed: int = 42) -> Dict:
    """
    solve()'s two passes for any solver of this shape: `solve_on_grid(grid,
    boundaries_only, scale_weights=None, evidence_only=False)` returns
    (results, densities) as _solve_on_grid does, with `boundaries_only` the
    densities of Boundaries that between them bound every date -- all a first
    pass needs to place the second grid -- and with `evidence_only` the log
    evidence alone. Dates outside every Boundary are not bound by them, but the
    second grid covers every date's own likelihood support too
    (_refined_grid), so it holds them.

    Between the passes, where any date carries an outlier model, each model's
    shared offset scale is weighed from the data over the second grid's window
    (_weigh_scales), and the answer is solved with each flagged date's offset
    mixed over that posterior rather than the prior.
    """
    grid = _build_grid(measurements, element_bounds, num_groups)
    _results, boundary_densities = solve_on_grid(grid, True)
    refined = _refined_grid(grid, boundary_densities, measurements, element_bounds,
                            num_groups)
    models = sorted({m.get('outlier_model') or DEFAULT_OUTLIER_MODEL
                     for m in measurements if (m.get('outlier_prior') or 0) > 0})
    weights = None
    if models:
        window = refined if refined is not None else grid
        weights = _weigh_scales(
            lambda coarse, trial: solve_on_grid(coarse, False, scale_weights=trial,
                                                evidence_only=True),
            models, float(window[0]), float(window[-1]))
    results = None
    if refined is not None:
        second, _densities = solve_on_grid(refined, False, scale_weights=weights)
        if second['edge_mass'] <= REFINED_EDGE_MASS:
            second['grid']['first_pass_step'] = float(grid[1] - grid[0])
            results = second
    if results is None:
        results, _densities = solve_on_grid(grid, False, scale_weights=weights)
    if weights is not None:
        _report_scales(results, weights, seed)
    return results


# The shared outlier scale is weighed on this many points across the second
# grid's window. Against 800 across the first pass's much wider one, 400 there
# came within 0.003-0.018 total variation of the weights; the second grid's
# window is narrower, and 200 across it matched 300 to 0.04 years in the dates
# and 0.0001 in the outlier probabilities on the test models.
SCALE_WEIGHING_POINTS = 200
# Every second scale node is solved for and the log evidence interpolated
# between them: the scale's posterior is smooth in the log, and on the test
# models 12 nodes matched 24 within 0.3 years and 0.0003 in outlier
# probability, at half the cost. The last node is always solved too.
SCALE_WEIGHING_STRIDE = 2
# With several outlier models each is weighed in turn, the others held at
# their weights so far, twice round. On the charcoal fixture's two the joint
# weights are within 0.001 total variation of the product of each model's own.
SCALE_WEIGHING_SWEEPS = 2


def _weigh_scales(evidence, models: List[str], lower: float, upper: float
                  ) -> Dict[str, np.ndarray]:
    """
    {outlier model: posterior weight per node of scale_nodes(model)}: the
    shared offset scale weighed from the data.

    OxCal's Outlier_Model carries ONE scale for all the dates using it, and
    the data constrain it; mixing each date's offset over the scale's prior
    on its own, as date_tables does by default, is a different model. The
    exact answer mixes whole solves, one per scale, by their evidence -- 24
    solves per model, 576 for two. Instead `evidence(grid, weights)`, the log
    evidence on a coarse grid over [lower, upper] with each model's offset at
    the given weights, is taken at each node, and the solve that follows mixes
    each flagged date's offset over the posterior those give. That keeps what
    the data say about the scale and drops only the link a common scale makes
    between the dates. On the dune fixture the outlier probabilities came to
    0.0014 from OxCal's (0.0158 over the prior; 0.0012 for the exact mixture),
    and on the charcoal fixture the dates to 3.4 years rms (7.1; the sampler
    4.7), for 3-17% more run time on the test models.
    """
    grid = np.linspace(lower, upper, SCALE_WEIGHING_POINTS)
    count = OUTLIER_SCALE_NODES
    solved_at = np.unique(np.r_[np.arange(0, count, SCALE_WEIGHING_STRIDE), count - 1])
    weights = {model: np.full(count, 1.0 / count) for model in models}
    for _sweep in range(1 if len(models) == 1 else SCALE_WEIGHING_SWEEPS):
        for model in models:
            log_evidence = []
            for node in solved_at:
                alone = np.zeros(count)
                alone[node] = 1.0
                log_evidence.append(evidence(grid, {**weights, model: alone}))
            log_evidence = np.array(log_evidence)
            finite = np.isfinite(log_evidence)
            if not finite.any():
                continue                      # nothing to weigh by: keep the prior
            filled = np.where(finite, log_evidence, log_evidence[finite].min() - 50.0)
            every = np.interp(np.arange(count), solved_at, filled)
            posterior = np.exp(every - every.max())
            weights[model] = posterior / posterior.sum()
    return weights


def _report_scales(results: Dict, weights: Dict[str, np.ndarray], seed: int) -> None:
    """
    Each outlier model's weighed scale in `results`, in the sampler's shape
    (services/gibbs.py): `outlier_scale`, its posterior mean and 68% range in
    log10 years, and `joint_scales`, one scale per joint draw drawn from it,
    so services/agreement.py scores a flagged date under a scale as it does a
    sampled run's. Seeded apart from everything else in the results.
    """
    rng = np.random.default_rng(seed + 2)
    joint = results.get('joint_draws') or {}
    count = len(next(iter(joint.values()))) if joint else 0
    results['outlier_scale'] = {}
    results['joint_scales'] = {}
    for model, weight in weights.items():
        nodes = scale_nodes(model)
        mean = float((nodes * weight).sum())
        cdf = np.cumsum(weight)
        results['outlier_scale'][model] = {
            'log10_mean': mean,
            'log10_hpd_68': (float(np.interp(0.16, cdf, nodes)),
                             float(np.interp(0.84, cdf, nodes))),
            'years_mean': float(10.0 ** mean),
        }
        results['joint_scales'][model] = nodes[rng.choice(len(nodes), size=count,
                                                          p=weight / weight.sum())]


def _solve_on_grid(grid: np.ndarray, measurements: List[Dict], root: Dict,
                   chain: Chain, element_bounds: Dict, num_samples: int,
                   seed: int, uniform_span_prior: bool, estimate_durations: bool,
                   dispersal: Optional[Dict], interval_draws: int,
                   resolution: float, boundaries_only: bool = False,
                   scale_weights: Optional[Dict[str, np.ndarray]] = None,
                   evidence_only: bool = False,
                   ):
    """
    solve() on one grid: (results, every posterior density it reported).

    With `boundaries_only`, (None, the Boundaries' densities) and nothing
    else: what solve()'s first pass needs to place the second grid. With
    `evidence_only`, the log evidence alone, as soon as the messages give it:
    what _weigh_scales asks for. `scale_weights` is date_tables'.
    """
    dt = float(grid[1] - grid[0])
    M = len(grid)
    # One M x M scratch array for every date factor below, rather than a fresh
    # 32 MB allocation per date per use.
    scratch = np.empty((M, M))

    likelihood, cumulative, outlier_parts = date_tables(
        grid, measurements, element_bounds, scale_weights)
    allowed = _allowed_windows(grid, element_bounds, chain.boundary_ids)
    log_T, ordered = _chain_log_transfers(grid, chain, cumulative, likelihood,
                                          allowed, scratch)
    transfer = _exponentiated(log_T)

    # Dates outside every Boundary: one weight each on the first and last
    # Boundary (_Tails). Not enclosed by the outer pair, so not part of the
    # limits factor below either, as in the sampler.
    tails = _Tails(chain, grid, cumulative, likelihood)
    end_weights = tails.end_weights

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
            pair_weight, boundary_density, draw_boundaries, log_mass = _messages(
                chain, allowed, transfer, end_weights)
            correction_offset = 0.0
        else:
            correction_offset = _finite_peak(log_correction)
            correction = np.exp(_rescaled(log_correction))     # exp(-inf) is 0
            pair_weight, boundary_density, draw_boundaries, log_mass = \
                _messages_with_span_prior(chain, allowed, transfer, correction,
                                          end_weights)
    else:
        pair_weight, boundary_density, draw_boundaries, log_mass = _messages(
            chain, allowed, transfer, end_weights)
        correction_offset = 0.0
    # The model's evidence, p(data | model), in the log: the chain's total
    # weight with every factor the rescaling took out put back. Up to a constant
    # that depends only on the dates and the grid -- each likelihood is
    # normalised over the grid (date_tables) and the boundaries' prior is flat
    # over it -- so it compares solves of the SAME dates on the SAME grid, such
    # as one per value of a shared outlier scale, not models with different
    # windows.
    log_evidence = (log_mass + correction_offset + tails.log_scale
                    + sum(_finite_peak(term) for term in log_T))
    if evidence_only:
        return log_evidence
    span_prior_applied = log_correction is not None
    # The messages hold what they need of these; nothing below reads them.
    log_correction = correction = None

    if boundaries_only:
        boundary_densities = [boundary_density(i) for i in range(len(chain.boundary_ids))]
        return None, [density for density in boundary_densities if density.sum() > 0]

    boundary_densities = {bid: boundary_density(i)
                          for i, bid in enumerate(chain.boundary_ids)}
    date_densities = _chain_date_marginals(chain, ordered, log_T, pair_weight,
                                           cumulative, likelihood, dt, scratch)
    date_densities.update(tails.marginals(boundary_densities[chain.boundary_ids[0]],
                                          boundary_densities[chain.boundary_ids[-1]],
                                          likelihood))

    def draw_joint(rng, count):
        boundary_draws = draw_boundaries(rng, count)
        date_draws = _chain_draw_dates(rng, chain, ordered, cumulative, boundary_draws,
                                       likelihood, dt)
        date_draws.update(tails.draw(rng, boundary_draws[:, 0], boundary_draws[:, -1],
                                     cumulative, likelihood, dt))
        return ({bid: boundary_draws[:, i] for i, bid in enumerate(chain.boundary_ids)},
                date_draws)

    return _results(grid, root, measurements, date_densities, boundary_densities,
                    chain.boundary_names, likelihood, outlier_parts, draw_joint,
                    num_samples=num_samples, seed=seed,
                    estimate_durations=estimate_durations, dispersal=dispersal,
                    interval_draws=interval_draws, resolution=resolution,
                    log_evidence=log_evidence, span_prior_applied=span_prior_applied)


def _allowed_windows(grid: np.ndarray, element_bounds: Dict,
                     boundary_ids: Sequence[str]) -> Dict[str, np.ndarray]:
    """Where each boundary may sit: inside its own TPQ/TAQ, if it has them."""
    allowed = {}
    for bid in boundary_ids:
        taq, tpq = element_bounds.get(bid, (None, None))
        ok = np.ones(len(grid), dtype=bool)
        if taq is not None:
            ok &= grid >= taq
        if tpq is not None:
            ok &= grid <= tpq
        allowed[bid] = ok
    return allowed


def _group_log_transfer(grid: np.ndarray, date_ids: Sequence[str],
                        cumulative: Dict[str, np.ndarray],
                        likelihood: Dict[str, np.ndarray],
                        older_ok: np.ndarray, younger_ok: np.ndarray,
                        scratch: np.ndarray) -> np.ndarray:
    """
    T_i over one group, dates integrated out, in the log and indexed
    [younger, older]. Its two boundaries may share a cell, at half weight
    (_boundary_pair_term); -inf where either boundary may not sit.
    """
    dt = float(grid[1] - grid[0])
    term = _boundary_pair_term(grid, len(date_ids))
    for date_id in date_ids:
        term += _log_date_factor(cumulative[date_id], likelihood[date_id], dt,
                                 out=scratch)
    if not (older_ok.all() and younger_ok.all()):
        term[~(younger_ok[:, None] & older_ok[None, :])] = -np.inf
    return term


def _chain_log_transfers(grid: np.ndarray, chain: Chain,
                         cumulative: Dict[str, np.ndarray],
                         likelihood: Dict[str, np.ndarray], allowed: Dict,
                         scratch: np.ndarray
                         ) -> Tuple[List[np.ndarray], Dict[int, '_OrderedGroup']]:
    """
    Every group's log transfer, in order, and {group index: _OrderedGroup}
    for the groups whose dates are ordered inside them (Chain.blocks).
    """
    log_T: List[np.ndarray] = []
    ordered: Dict[int, _OrderedGroup] = {}
    for index, date_ids in enumerate(chain.groups):
        older_ok = allowed[chain.boundary_ids[index]]
        younger_ok = allowed[chain.boundary_ids[index + 1]]
        if chain.ordered(index):
            ordered[index] = _OrderedGroup(chain.blocks[index], grid, cumulative, likelihood)
            log_T.append(_ordered_log_transfer(ordered[index], grid, older_ok, younger_ok))
        else:
            log_T.append(_group_log_transfer(grid, date_ids, cumulative, likelihood,
                                             older_ok, younger_ok, scratch))
    return log_T, ordered


def _chain_date_marginals(chain: Chain, ordered: Dict[int, '_OrderedGroup'],
                          log_T: List[np.ndarray], pair_weight,
                          cumulative: Dict[str, np.ndarray],
                          likelihood: Dict[str, np.ndarray], dt: float,
                          scratch: np.ndarray) -> Dict[str, np.ndarray]:
    """
    _date_marginals for a chain that may hold ordered groups, in the chain's
    order. An ordered group's dates come from its own chain over its cuts,
    given everything outside it: the pair weight, and whatever of its log
    transfer is not the sum over its cuts -- the span factor, the tie, and the
    span correction where one group carries it.
    """
    plain = [[] if index in ordered else group for index, group in enumerate(chain.groups)]
    found = _date_marginals(plain, log_T, pair_weight, cumulative, likelihood, dt, scratch)
    for index, group in ordered.items():
        with np.errstate(divide='ignore', invalid='ignore'):
            log_pair = np.log(pair_weight(index))
            log_end = np.fmax(log_pair + (log_T[index] - group.log_inner), -np.inf)
        # The ends in one cell are the tie, weighed on its own.
        log_tie = np.diagonal(log_pair) + np.diagonal(log_T[index])
        np.fill_diagonal(log_end, -np.inf)
        group.solve(log_end, log_tie)
        found.update(group.marginals())
    return {date_id: found[date_id] for group in chain.groups for date_id in group}


def _chain_draw_dates(rng, chain: Chain, ordered: Dict[int, '_OrderedGroup'],
                      cumulative: Dict[str, np.ndarray], boundary_draws: np.ndarray,
                      likelihood: Dict[str, np.ndarray], step: float
                      ) -> Dict[str, np.ndarray]:
    """_draw_dates for a chain that may hold ordered groups, whose dates come
    from the group given its two Boundaries drawn (_OrderedGroup.draw)."""
    plain = Chain(chain.boundary_ids,
                  [[] if index in ordered else group
                   for index, group in enumerate(chain.groups)], chain.boundary_names)
    out = _draw_dates(rng, plain, cumulative, boundary_draws, likelihood, step)
    for index, group in ordered.items():
        out.update(group.draw(rng, boundary_draws[:, index], boundary_draws[:, index + 1]))
    return out


def _exponentiated(log_T: List[np.ndarray]):
    """transfer(i): T_i, exponentiated on demand rather than kept alongside
    log_T."""
    def transfer(i):
        weights = _rescaled(log_T[i])
        return np.exp(weights, out=weights)            # exp(-inf) is 0
    return transfer


def _date_marginals(groups: List[List[str]], log_T: List[np.ndarray], pair_weight,
                    cumulative: Dict[str, np.ndarray], likelihood: Dict[str, np.ndarray],
                    dt: float, scratch: np.ndarray) -> Dict[str, np.ndarray]:
    """
    {date id: unnormalised marginal density} for every date in `groups`, in
    their order, from each group's log transfer and pair_weight(k), the weight
    of everything outside group k indexed [younger, older].
    """
    densities: Dict[str, np.ndarray] = {}
    for index, date_ids in enumerate(groups):
        if not date_ids:
            continue
        # Everything about this group's two boundaries, the weight outside the
        # group and the transfer inside it, in the log and once per group: on
        # the span-prior path the outside is two M x M matrix products, and
        # every date in the group shares it.
        with np.errstate(divide='ignore'):
            log_pair = np.log(pair_weight(index))
        log_pair += log_T[index]
        for date_id in date_ids:
            # This date's own factor divided back out, leaving it free: its
            # integral where the boundaries are apart, its likelihood where
            # they share a cell (_log_date_factor). Where the date has no
            # likelihood that factor is -inf, and so is log_T, which was summed
            # from the same factor; -inf minus -inf is NaN, which fmax turns
            # back into -inf.
            W = _log_date_factor(cumulative[date_id], likelihood[date_id], dt,
                                 out=scratch)
            with np.errstate(invalid='ignore'):
                np.subtract(log_pair, W, out=W)
            np.fmax(W, -np.inf, out=W)
            peak = float(W.max())
            if np.isfinite(peak):
                W -= peak
                np.exp(W, out=W)
            else:
                W.fill(0.0)
            # The weight with the date ON the younger boundary (row t: the
            # younger boundary at t, any older one) and ON the older (column t).
            # W is zero wherever the older boundary is younger, so a whole row
            # or column sum is exactly that -- the diagonal, both boundaries at
            # t, included in each.
            on_younger = W.sum(axis=1)
            on_older = W.sum(axis=0)
            on_both = np.diagonal(W).copy()
            # A date sitting on one of its boundaries counts HALF, as it does in
            # the interval integral T was built from (_interval_integral) and in
            # the sampler (gibbs.Model.endpoint_weight). Counted whole, the
            # date's marginal was not the marginal of the joint the boundaries
            # come from: modelled dates moved up to 3.7 years on the test models,
            # and on the unordered Hillside reference run they were 0.64 years rms
            # from the posteriors OxCal gave against 0.17 with the half.
            #
            # With both boundaries at t the date can only be at t too, and the
            # whole of that weight is its: the half on each side would take it
            # twice, so it is put back once -- divided by the step. Everywhere
            # else W is the pair's weight over the date's interval integral,
            # so L * W is a density; on the diagonal the date's own factor was
            # its likelihood, so L * W there is the tie's whole weight, a mass
            # in one cell, and a density needs it over the cell's width.
            # Counted as it was, the result depended on the unit of the grid:
            # right at one year, and on a 32-year test grid 0.07 from the
            # sampler's answer for a two-date phase where this gets 0.001.
            enclosed = (_straddling(on_younger, on_older)
                        - 0.5 * (on_younger + on_older) + on_both / dt)
            density = likelihood[date_id] * np.clip(enclosed, 0.0, None)
            densities[date_id] = density
    return densities


def _results(grid: np.ndarray, root: Dict, measurements: List[Dict],
             date_densities: Dict[str, np.ndarray],
             boundary_densities: Dict[str, np.ndarray],
             boundary_names: Dict[str, str], likelihood: Dict[str, np.ndarray],
             outlier_parts: Dict, draw_joint, *, num_samples: int, seed: int,
             estimate_durations: bool, dispersal: Optional[Dict],
             interval_draws: int, resolution: float, log_evidence: float,
             span_prior_applied: bool) -> Tuple[Dict, List[np.ndarray]]:
    """
    A solve's results from its marginals, in the shape services/gibbs.py
    returns: every date's then every Boundary's summary, in the order given,
    and the joint draws `draw_joint(rng, count)` takes -- ({boundary id: grid
    indices}, {date id: grid indices}), one per draw. (results, densities).
    """
    dt = float(grid[1] - grid[0])
    rng = np.random.default_rng(seed)
    results = {'measurements': {}, 'boundaries': {}}
    densities: List[np.ndarray] = []
    for date_id, density in date_densities.items():
        densities.append(density)
        summary = _summarize(grid, density, rng, num_samples, resolution)
        if date_id in outlier_parts:
            summary['outlier_probability'] = _outlier_probability(
                density, likelihood[date_id], *outlier_parts[date_id])
        results['measurements'][date_id] = summary

    for bid, density in boundary_densities.items():
        if density.sum() <= 0:
            continue
        densities.append(density)
        results['boundaries'][bid] = {
            'name': boundary_names.get(bid, 'Boundary'),
            'posterior': _summarize(grid, density, rng, num_samples, resolution),
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
    boundary_draws, date_draws = draw_joint(joint_rng, interval_draws)
    joint = {bid: grid[idx] for bid, idx in boundary_draws.items()}
    joint.update({date_id: grid[idx] for date_id, idx in date_draws.items()})
    results['joint_draws'] = {date_id: grid[idx] for date_id, idx in date_draws.items()}
    # The Boundaries in the same states, for a per-phase KDE to reflect at
    # (services/kde.report_phases).
    results['joint_boundaries'] = {bid: joint[bid] for bid in boundary_draws}
    if estimate_durations or dispersal is not None:
        results['intervals'] = intervals.report(
            root, measurements, joint, independent=True,
            durations=estimate_durations, dispersal=dispersal, step=resolution)

    results['method'] = 'exact'
    results['log_evidence'] = float(log_evidence)
    results['uniform_span_prior'] = span_prior_applied
    results['grid'] = {'lower': float(grid[0]), 'upper': float(grid[-1]), 'step': dt}
    results['edge_mass'] = _edge_mass(densities, grid)
    return results, densities


class _OrderedGroup:
    """
    Dates in BLOCKS, oldest first, between an older end and a younger end on
    one grid: Phases in a row with no Boundary between them, inside one pair of
    Boundaries or beyond an end one (Chain.blocks). The dates of a block are
    unordered and every one is older than every date of the next block.

    Their integral over every placement is cut at v_i, the oldest date of
    block i for i >= 2. Given the cuts the blocks are independent, so the
    group is a small chain over the nodes (older end, v_2, ..., v_n, younger
    end), one transfer per block, indexed [younger, older] as everywhere here:

        block 1:    prod_m F_m(v_2, S)
        block i>1:  T_i(l, u) = sum_k L_k(u) dt prod_(m != k) F_m(l, u)

    with F the trapezoidal interval integral, l the next cut or the younger
    end, and T_i the derivative in u of block i's prod F: its dates in [l, u]
    with the oldest AT u. Two neighbouring nodes in one cell count half, as
    two Boundaries do (_boundary_pair_term) -- which leaves only a block of one
    date there, since a block's F over no width is nothing.

    This is the trapezoid rule for the continuum integral, cut by cut, and it
    converges as the step does. Following the sampler's grid rule instead --
    half per tied ordering edge, gibbs.Model.endpoint_weight -- would need
    every block's count of dates at each cell as state, and the cheaper
    imitations of it converge worse. One that weighed the date at a block's
    oldest cell whole put the Hillside `ordered` model's Start 7.5 years
    young at a 2-year step and 0.8 at an eighth of a year; this rule puts it
    2.4 old and 0.1 (test_exact_ordered.py, TestConvergence). Both part from
    the sampler's by a term of first order in the step, and
    test_exact_ordered.py holds this one to the sampler's dates.

    The chain's products are exact_chain's own (_ChainProducts), so the group's
    weight over its ends, its cuts' marginals given everything outside it, and
    their draws come from the code that does the same for Boundaries. The two
    ends in one cell -- every date there -- is the group's tie, which the
    caller weighs (_ordered_log_transfer) and hands back to marginals().
    """

    def __init__(self, blocks: List[List[str]], grid: np.ndarray,
                 cumulative: Dict[str, np.ndarray], likelihood: Dict[str, np.ndarray]):
        self.blocks = blocks
        self.dt = float(grid[1] - grid[0])
        self.M = M = len(grid)
        self.cumulative, self.likelihood = cumulative, likelihood
        index = np.arange(M)
        self._upper = index[None, :] >= index[:, None]          # [l, u]: u >= l
        self.log_links = [self._first_block(blocks[0])] + [
            self._oldest_at(block) for block in blocks[1:]]
        ids = ['_older'] + [f'_cut{i}' for i in range(2, len(blocks) + 1)] + ['_younger']
        self.chain = Chain(ids, [[] for _ in blocks], {})
        ones = np.ones(M, dtype=bool)
        self.products = _ChainProducts(self.chain, {b: ones for b in ids},
                                       _exponentiated(self.log_links))
        self.link_scale = sum(_finite_peak(link) for link in self.log_links)
        with np.errstate(divide='ignore'):
            inner = np.log(self.products.end_to_end.T)          # [younger, older]
        inner += self.products.log_scale + self.link_scale
        self.log_inner = inner
        self.messages = None

    # -- the transfers --------------------------------------------------------

    def _log_F(self, date_id: str) -> np.ndarray:
        return _log_interval_integral(self.cumulative[date_id], self.likelihood[date_id],
                                      self.dt)

    def _first_block(self, block: List[str]) -> np.ndarray:
        term = np.zeros((self.M, self.M))
        for date_id in block:
            term += self._log_F(date_id)
        return term

    def _log_top(self, date_id: str) -> np.ndarray:
        """log of L(u) dt, by column u."""
        with np.errstate(divide='ignore'):
            return np.log(self.dt * self.likelihood[date_id])[None, :]

    def _chunks(self, size: int):
        """Row slices of [l, u] small enough that a block's per-date terms for
        one of them fit in about 64 MB."""
        step = max(1, int(64e6 / (16 * max(size, 1) * self.M)))
        return [slice(start, min(start + step, self.M)) for start in range(0, self.M, step)]

    def _leave_one_out(self, log_F: Dict[str, np.ndarray], members: List[str],
                       rows: slice):
        """
        For rows `rows` of [l, u], each member j in turn as (j, log prod over
        the others of F, log T over the others) -- T the derivative in u of
        prod F, sum_k L_k(u) dt prod_(m != k) F_m. From one backward and one
        forward pass of the product rule in the log, so no F is ever divided
        out and a zero one stays a zero.
        """
        a = [log_F[d][rows] for d in members]
        b = [self._log_top(d) for d in members]
        K = len(members)
        Q: List[np.ndarray] = [None] * K       # log prod F over the members after j
        U: List[np.ndarray] = [None] * K       # log T over the members after j
        q = np.zeros_like(a[0])
        u = np.full_like(a[0], -np.inf)
        for j in range(K - 1, -1, -1):
            Q[j], U[j] = q, u
            u = np.logaddexp(u + a[j], q + b[j])
            q = q + a[j]
        p = np.zeros_like(a[0])
        r = np.full_like(a[0], -np.inf)
        for j in range(K):
            yield j, p + Q[j], np.logaddexp(r + Q[j], p + U[j])
            r = np.logaddexp(r + a[j], p + b[j])
            p = p + a[j]

    def _oldest_at(self, block: List[str]) -> np.ndarray:
        """log T over `block`, [l, u]: the derivative of its prod F in u, a
        half where l = u (a block of one date only)."""
        # The product rule forwards: r the derivative so far, p the product.
        p = np.zeros((self.M, self.M))
        out = np.full((self.M, self.M), -np.inf)
        for date_id in block:
            log_F = self._log_F(date_id)
            out = np.logaddexp(out + log_F, p + self._log_top(date_id))
            p += log_F
        out[~self._upper] = -np.inf
        diagonal = np.diagonal(out) + np.log(BOUNDARY_TIE_WEIGHT)
        np.fill_diagonal(out, diagonal)
        return out

    # -- with the rest of the model -------------------------------------------

    def solve(self, log_end: np.ndarray, log_tie: Optional[np.ndarray] = None) -> None:
        """
        Fold in `log_end`, [younger end, older end], the log weight of
        everything outside the group -- zero where the ends share a cell -- and
        `log_tie`, by cell, the whole weight of the ends and every date in
        that one cell. marginals() and draw() read the result. Consumes the
        products.
        """
        peak = _finite_peak(log_end)
        self.messages = _messages_with_end_factor(self.products, np.exp(_rescaled(log_end)))
        self.products = None
        self.log_total = self.messages[3] + peak + self.link_scale
        self.log_tie = log_tie

    def marginals(self) -> Dict[str, np.ndarray]:
        """{date id: unnormalised marginal}, given solve()'s weights: the
        group's placements with its ends apart, each date's normalised to
        their total, and the tie's mass in its cell."""
        pair_weight = self.messages[0]
        out: Dict[str, np.ndarray] = {}
        for index, block in enumerate(self.blocks):
            with np.errstate(divide='ignore'):
                log_q = np.log(pair_weight(index))              # [younger, older]
            log_F = {d: self._log_F(d) for d in block}
            if index == 0:
                log_q += self.log_links[0]
                for date_id in block:
                    with np.errstate(invalid='ignore'):
                        W = np.fmax(log_q - log_F[date_id], -np.inf)
                    np.fill_diagonal(W, -np.inf)
                    W = np.exp(_rescaled(W))
                    rows, cols = W.sum(axis=1), W.sum(axis=0)
                    out[date_id] = self.likelihood[date_id] * np.clip(
                        _straddling(rows, cols) - 0.5 * (rows + cols), 0.0, None)
                continue
            # Per date, as log sums: the date at the block's oldest cell u (a
            # mass there, by u), and below u under its own trapezoid with
            # another date at u (by row l and by column u, for _straddling).
            K = len(block)
            top = np.full((K, self.M), -np.inf)
            rows_V = np.full((K, self.M), -np.inf)
            cols_V = np.full((K, self.M), -np.inf)
            for rows in self._chunks(K):
                q = log_q[rows]
                above = self._upper[rows]
                span = np.arange(rows.start, rows.stop)
                on_diagonal = np.zeros_like(above)
                on_diagonal[span - rows.start, span] = True
                for j, others, rest in self._leave_one_out(log_F, block, rows):
                    at_top = q + self._log_top(block[j]) + others
                    at_top[~above] = -np.inf
                    if K == 1:
                        at_top[on_diagonal] += np.log(BOUNDARY_TIE_WEIGHT)
                    else:
                        at_top[on_diagonal] = -np.inf
                    V = q + rest
                    V[~above | on_diagonal] = -np.inf
                    top[j] = np.logaddexp(top[j], _log_sum(at_top, axis=0))
                    cols_V[j] = np.logaddexp(cols_V[j], _log_sum(V, axis=0))
                    rows_V[j, rows] = _log_sum(V, axis=1)
            for j, date_id in enumerate(block):
                peak = max(_finite_peak(top[j]), _finite_peak(rows_V[j]),
                           _finite_peak(cols_V[j]))
                rows_j, cols_j = np.exp(rows_V[j] - peak), np.exp(cols_V[j] - peak)
                below = _straddling(rows_j, cols_j) - 0.5 * (rows_j + cols_j)
                out[date_id] = (self.likelihood[date_id] * np.clip(below, 0.0, None)
                                + np.exp(top[j] - peak) / self.dt)
        # Each date's density sums, times the step, to the weight of every
        # placement with the ends apart; the tie adds its own in one cell.
        log_tie = self.log_tie
        ref = max(self.log_total, _finite_peak(log_tie) if log_tie is not None else -np.inf)
        apart = np.exp(self.log_total - ref) if np.isfinite(self.log_total) else 0.0
        tie = (np.exp(log_tie - ref) / self.dt if log_tie is not None
               else np.zeros(self.M))
        for date_id, density in out.items():
            mass = density.sum() * self.dt
            out[date_id] = (density * (apart / mass) if mass > 0 else 0.0 * density) + tie
        return out

    def draw(self, rng, older: np.ndarray, younger: np.ndarray) -> Dict[str, np.ndarray]:
        """A grid index per date per draw, given the group's two ends drawn:
        every date in the ends' cell where they share one."""
        count = len(older)
        apart = older != younger
        out = {d: older.copy() for block in self.blocks for d in block}
        if not apart.any():
            return out
        cuts = self.messages[2](rng, int(apart.sum()), ends=(older[apart], younger[apart]))
        drawn: Dict[str, np.ndarray] = {}
        first = Chain(['_o', '_y'], [self.blocks[0]], {})
        drawn.update(_draw_dates(rng, first, self.cumulative, cuts[:, :2],
                                 self.likelihood, self.dt))
        for index in range(1, len(self.blocks)):
            drawn.update(self._draw_block(rng, self.blocks[index], cuts[:, index],
                                          cuts[:, index + 1]))
        for date_id, values in drawn.items():
            out[date_id][apart] = values
        return out

    def _draw_block(self, rng, block: List[str], top: np.ndarray,
                    low: np.ndarray) -> Dict[str, np.ndarray]:
        """
        A block's dates given its oldest cell `top` and its lower end `low`:
        which date sits at the top, by its term in T, then the rest from
        [low, top] under their trapezoids -- exactly the block's conditional,
        with no M x M table. Where top = low the block is one date, there.
        """
        dt = self.dt
        log_F, log_L = [], []
        for date_id in block:
            padded = np.concatenate([[0.0], self.cumulative[date_id]])
            L = self.likelihood[date_id]
            F = (padded[top + 1] - 0.5 * dt * L[top]) - (padded[low] + 0.5 * dt * L[low])
            with np.errstate(divide='ignore'):
                log_F.append(np.where(top > low, np.log(np.maximum(F, 0.0)), -np.inf))
                log_L.append(np.log(dt * L[top]))
        log_F, log_L = np.array(log_F), np.array(log_L)
        finite = np.isfinite(log_F)
        total = np.where(finite, log_F, 0.0).sum(axis=0)
        zeros = (~finite).sum(axis=0)
        with np.errstate(invalid='ignore'):
            others = np.where(finite, np.where(zeros == 0, total - log_F, -np.inf),
                              np.where(zeros == 1, total, -np.inf))
        weights = log_L + others                                       # [k, draw]
        weights = np.nan_to_num(np.exp(weights - np.max(weights, axis=0, initial=-np.inf)))
        cdf = np.cumsum(weights, axis=0)
        uniform = rng.random(len(top)) * cdf[-1]
        chosen = np.minimum((cdf < uniform[None, :]).sum(axis=0), len(block) - 1)
        out = {}
        for k, date_id in enumerate(block):
            within = _draw_in(rng, self.cumulative[date_id], self.likelihood[date_id],
                              low, top, dt, high_half=True)
            out[date_id] = np.where(chosen == k, top, within)
        return out


def _log_sum(log_weights: np.ndarray, axis: int) -> np.ndarray:
    """log of the sum of exp(log_weights) along `axis`, -inf where all are."""
    peak = np.max(log_weights, axis=axis, keepdims=True, initial=-np.inf)
    safe = np.where(np.isfinite(peak), peak, 0.0)
    with np.errstate(divide='ignore'):
        out = np.log(np.exp(log_weights - safe).sum(axis=axis)) + np.squeeze(safe, axis)
    return np.where(np.isfinite(np.squeeze(peak, axis)), out, -np.inf)


def _ordered_log_transfer(group: _OrderedGroup, grid: np.ndarray,
                          older_ok: np.ndarray, younger_ok: np.ndarray) -> np.ndarray:
    """
    An ordered group's T over its two Boundaries, in the log and indexed
    [younger, older]: the span factor over all its K dates times the sum over
    its cuts. Where the two Boundaries share
    a cell every date is in it: the tie's half times the dates' likelihoods,
    as for any group (_log_date_factor), and a half more for each pair of
    dates in neighbouring blocks, a tied ordering edge each, as the sampler
    weighs them.
    """
    sizes = [len(block) for block in group.blocks]
    ties = sum(a * b for a, b in zip(sizes, sizes[1:]))
    term = _boundary_pair_term(grid, sum(sizes)) + group.log_inner
    with np.errstate(divide='ignore'):
        tie = (np.log(BOUNDARY_TIE_WEIGHT) - ties * np.log(2.0)
               + sum(np.log(group.likelihood[d]) for block in group.blocks for d in block))
    np.fill_diagonal(term, tie)
    if not (older_ok.all() and younger_ok.all()):
        term[~(younger_ok[:, None] & older_ok[None, :])] = -np.inf
    return term


class _Tails:
    """
    A chain's dates outside every Boundary on one grid: each one's integral
    beyond its end Boundary (_tail_integrals), and the weight the tail at each
    end puts on that Boundary (_tail_weight), as `end_weights` for _messages
    and _messages_with_span_prior, with `log_scale` the log of what scaling
    the weights took out. Empty for a chain without tails, whose solve it
    then leaves untouched.
    """

    def __init__(self, chain: Chain, grid: np.ndarray,
                 cumulative: Dict[str, np.ndarray], likelihood: Dict[str, np.ndarray]):
        self.chain = chain
        self.M = len(grid)
        self.end_weights: Dict[str, np.ndarray] = {}
        self.log_scale = 0.0
        # A tail of several blocks, ordered one after another, is an ordered
        # group with one end held at the grid's edge and no span factor, and
        # its weight on the end Boundary is a row or column of the group's.
        self.older_group = self.younger_group = None
        self.older = self.younger = {}
        if len(chain.older_tail_blocks) > 1:
            self.older_group = _OrderedGroup(chain.older_tail_blocks, grid, cumulative,
                                             likelihood)
            self._weigh(chain.boundary_ids[0], self.older_group.log_inner[:, -1])
        else:
            self.older = _tail_integrals(grid, cumulative, likelihood, chain.older_tail,
                                         True)
        if len(chain.younger_tail_blocks) > 1:
            self.younger_group = _OrderedGroup(chain.younger_tail_blocks, grid,
                                               cumulative, likelihood)
            self._weigh(chain.boundary_ids[-1], self.younger_group.log_inner[0, :])
        else:
            self.younger = _tail_integrals(grid, cumulative, likelihood,
                                           chain.younger_tail, False)
        for bid, integrals in ((chain.boundary_ids[0], self.older),
                               (chain.boundary_ids[-1], self.younger)):
            if integrals:
                self.end_weights[bid], peak = _tail_weight(integrals)
                self.log_scale += peak

    def _weigh(self, bid: str, log_g: np.ndarray) -> None:
        peak = _finite_peak(log_g)
        self.end_weights[bid] = np.exp(np.where(np.isfinite(log_g), log_g - peak, -np.inf))
        self.log_scale += peak

    def _solve_group(self, group: '_OrderedGroup', bid: str, density: np.ndarray,
                     older_end: bool) -> Dict[str, np.ndarray]:
        """An ordered tail's marginals: its end Boundary's own, with the
        tail's weight on it divided out, as the weight of everything else."""
        weight = self.end_weights[bid]
        with np.errstate(divide='ignore', invalid='ignore'):
            outside = np.where(weight > 0, np.log(density) - np.log(weight), -np.inf)
        log_end = np.full((self.M, self.M), -np.inf)        # [younger, older]
        if older_end:
            log_end[:, -1] = outside
        else:
            log_end[0, :] = outside
        group.solve(log_end)
        return group.marginals()

    def marginals(self, first_density: np.ndarray, last_density: np.ndarray,
                  likelihood: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
        """Every tail date's marginal, from its end Boundary's (_tail_marginals)."""
        out = {**_tail_marginals(first_density, self.older, likelihood, True),
               **_tail_marginals(last_density, self.younger, likelihood, False)}
        if self.older_group is not None:
            out.update(self._solve_group(self.older_group, self.chain.boundary_ids[0],
                                         first_density, True))
        if self.younger_group is not None:
            out.update(self._solve_group(self.younger_group, self.chain.boundary_ids[-1],
                                         last_density, False))
        return out

    def draw(self, rng, first_draws: np.ndarray, last_draws: np.ndarray,
             cumulative: Dict[str, np.ndarray], likelihood: Dict[str, np.ndarray],
             step: float) -> Dict[str, np.ndarray]:
        """Every tail date's draws, given its end Boundary's (_draw_tail)."""
        out = {}
        if self.older_group is not None:
            out.update(self.older_group.draw(
                rng, np.full(len(first_draws), self.M - 1, dtype=np.int64), first_draws))
        elif self.chain.older_tail:
            out.update(_draw_tail(rng, self.chain.older_tail, first_draws, True,
                                  cumulative, likelihood, step))
        if self.younger_group is not None:
            out.update(self.younger_group.draw(
                rng, last_draws, np.zeros(len(last_draws), dtype=np.int64)))
        elif self.chain.younger_tail:
            out.update(_draw_tail(rng, self.chain.younger_tail, last_draws, False,
                                  cumulative, likelihood, step))
        return out


def _tail_integrals(grid: np.ndarray, cumulative: Dict[str, np.ndarray],
                    likelihood: Dict[str, np.ndarray], date_ids: Sequence[str],
                    older: bool) -> Dict[str, np.ndarray]:
    """
    {date id: G(F)} for dates outside every Boundary: each one's trapezoidal
    integral from an end Boundary at F out to the grid's edge -- beyond F for
    an older tail, below it for a younger. The entries of _interval_integral
    with one end pinned at the edge, formed the same way and so equal to them;
    services/cross_check.py's _tail_weight sums the same integral.
    """
    dt = float(grid[1] - grid[0])
    out = {}
    for date_id in date_ids:
        padded = np.concatenate([[0.0], cumulative[date_id]])
        half = 0.5 * dt * likelihood[date_id]
        if older:      # [F, top]: (C[top] - h[top]) - (C[F-1] + h[F])
            G = (padded[-1] - half[-1]) - (padded[:-1] + half)
        else:          # [bottom, F]: (C[F] - h[F]) - (C[-1] + h[0])
            G = (padded[1:] - half) - (padded[0] + half[0])
        out[date_id] = np.maximum(G, 0.0)
    return out


def _tail_weight(integrals: Dict[str, np.ndarray]) -> Tuple[np.ndarray, float]:
    """
    The weight a tail of dates outside every Boundary puts on its end
    Boundary: the product of their integrals beyond it, scaled to a peak of
    one, with the log of that peak.

    No span factor: a date outside every Boundary has a flat prior, as in
    OxCal, so given the end Boundary the dates are independent of each other
    and of the rest of the model, and this one vector is all of them the rest
    of the model sees. A date ON the Boundary counts half, as everywhere.
    """
    with np.errstate(divide='ignore'):
        log_g = sum(np.log(G) for G in integrals.values())
    peak = _finite_peak(log_g)
    return np.exp(log_g - peak), peak


def _tail_marginals(end_density: np.ndarray, integrals: Dict[str, np.ndarray],
                    likelihood: Dict[str, np.ndarray], older: bool
                    ) -> Dict[str, np.ndarray]:
    """
    {date id: unnormalised marginal} for a tail, from its end Boundary's
    marginal P(F), which already carries every tail date's integral.

    Given F, date j sits at t with weight L_j(t) h(t) / G_j(F) over the tail's
    side of F, h a half at F and at the grid's edge, as G_j integrates it.
    So p_j(t) = L_j(t) * sum over F on the near side of t of h P(F) / G_j(F):
    one cumulative sum.
    """
    out = {}
    for date_id, G in integrals.items():
        per_end = np.where(G > 0, end_density / np.where(G > 0, G, 1.0), 0.0)
        if older:      # t >= F
            reach = np.cumsum(per_end) - 0.5 * per_end
            edge = -1
        else:          # t <= F
            reach = np.cumsum(per_end[::-1])[::-1] - 0.5 * per_end
            edge = 0
        density = likelihood[date_id] * np.clip(reach, 0.0, None)
        density[edge] *= 0.5
        out[date_id] = density
    return out


def _draw_tail(rng, date_ids: List[str], end_draws: np.ndarray, older: bool,
               cumulative: Dict[str, np.ndarray], likelihood: Dict[str, np.ndarray],
               step: float) -> Dict[str, np.ndarray]:
    """
    A grid index per tail date per draw, given its end Boundary's drawn cell:
    its likelihood over the tail's side of that cell, half at each end, as
    _draw_dates draws a date between two Boundaries -- here one of them is the
    grid's edge.
    """
    M = len(next(iter(likelihood.values())))
    edge = np.full(len(end_draws), M - 1 if older else 0, dtype=np.int64)
    pair = np.column_stack([edge, end_draws] if older else [end_draws, edge])
    pseudo = Chain(['_older', '_younger'], [list(date_ids)], {})
    return _draw_dates(rng, pseudo, cumulative, pair, likelihood, step)


def _straddling(on_younger: np.ndarray, on_older: np.ndarray) -> np.ndarray:
    """
    For each cell t, the total weight of the boundary pairs that straddle it,
    younger boundary at or below t and older at or above, from the weight
    matrix's row sums (`on_younger`, by younger boundary) and column sums
    (`on_older`, by older boundary) alone.

    The matrix is zero below its diagonal -- an older boundary is never the
    younger -- so a pair with its younger boundary at or below t straddles t
    unless its older boundary is below t as well:

        S(t) = sum_{y <= t} rows(y) - sum_{o < t} cols(o)
             = sum_{o >= t} cols(o) - sum_{y > t} rows(y)

    This used to be read off the diagonal of two M x M cumulative sums per
    date: 15 ms each at M = 1,800, a fifth of the solve on a single phase of
    thirty dates. Each form above subtracts two partial sums, which are small
    on the young side for the first and on the old side for the second, so
    each is used on its own side and neither cancels two large numbers.
    """
    rows_to = np.cumsum(on_younger)
    from_young = rows_to - np.cumsum(on_older) + on_older
    rows_after = np.cumsum(on_younger[::-1])[::-1] - on_younger
    from_old = np.cumsum(on_older[::-1])[::-1] - rows_after
    return np.where(rows_to <= 0.5 * rows_to[-1], from_young, from_old)


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


def _reported_ranges(grid: np.ndarray, mass: np.ndarray, confidence: float,
                     step: float = REPORTING_STEP_YEARS) -> List[Dict]:
    """
    HPD ranges at `step` -- REPORTING_STEP_YEARS unless the analysis asked for
    another -- rather than at the solver's internal grid step.

    Two reasons. The internal step is chosen from the date range and the number
    of groups, so reporting at it would make a published range depend on an
    implementation detail -- the same dates in a longer sequence would come back
    with different widths. And a modelled posterior on a wiggly curve is
    genuinely multi-peaked, so a fine grid picks out the peak tops and skips the
    dips: at 1 year Ld-10012's 68.3% region came back as three fragments
    totalling 24 years where OxCal reports 35, and rebinned to 5 years it gives
    30.

    Five years is OxCal's own default reporting resolution and about as fine as
    a calibrated date is meaningful. The full-resolution density is still what
    gets plotted and what the point estimates come from.
    """
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


def _summarize(grid: np.ndarray, density: np.ndarray, rng, num_samples: int,
               step: float = REPORTING_STEP_YEARS) -> Dict:
    """
    An exact density in the summary shape the rest of the app expects. Point
    estimates come from the density itself rather than from draws, so they carry
    no Monte Carlo error; the draws exist only for downstream code that wants
    samples. `step` is the calendar step the HPD ranges are reported at.
    """
    dt = float(grid[1] - grid[0])
    total = density.sum() * dt
    if total <= 0:
        raise ValueError('a posterior came out empty; the model may be contradictory')
    density = density / total
    mass = density * dt

    mean = float((grid * mass).sum())
    variance = float(((grid - mean) ** 2 * mass).sum())
    # Each grid point stands for the cell [t - dt/2, t + dt/2], so the
    # cumulative mass THROUGH a cell is reached at its far edge, not its centre.
    # Read against the centres, the median and every draw came out half a cell
    # young: 22.5 for a density symmetric about 25 on a five-year grid.
    cell_edges = np.concatenate([[grid[0] - dt / 2.0], grid + dt / 2.0])
    cdf = np.concatenate([[0.0], np.cumsum(mass)])
    median = float(np.interp(0.5, cdf, cell_edges))

    hpd_68 = _reported_ranges(grid, mass, SIGMA_1, step)
    hpd_95 = _reported_ranges(grid, mass, SIGMA_2, step)

    draws = np.interp(rng.random(num_samples), cdf, cell_edges)

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
