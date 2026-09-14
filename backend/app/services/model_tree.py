"""
The explicit model structure -- OxCal's Sequence / Phase / Boundary / Hiatus
as a nested, ordered tree -- and the translation of that tree into the
pieces the solvers in services/ need to turn it into a posterior.

Supersedes the flat arrangement of Contexts plus pairwise above/below
relationships, which could not express a deliberately-placed Boundary, a
site-wide Sequence, or any nesting, and made every context's boundaries
independent of its neighbours' where OxCal's are one shared transition.

Semantics, following the OxCal documentation
(https://c14.arch.ox.ac.uk/oxcalhelp/hlp_analysis_oper.html):

  sequence  Children are in chronological order, OLDEST FIRST (ascending
            `position`, matching OxCal's reading order and the Harris matrix
            convention of oldest at the bottom). Everything in one child is
            older than everything in the next.
  phase     Children are unordered with respect to each other. Dates attach
            here. Nest sequences inside a phase to model parallel branches
            that are unordered relative to one another.
  boundary  A single latent transition parameter, and an ordinary ordered
            element of its parent sequence. A boundary placed BETWEEN two
            phases is therefore ONE shared parameter -- simultaneously the
            end of the earlier phase and the start of the later one -- not
            two independent ones.
  hiatus    Marks a deliberate gap. It carries no parameter of its own: a
            contiguous transition is one Boundary between two phases, and a
            gap is two Boundaries with a `hiatus` between them, so the maths
            is identical and only the element count differs.

Nesting follows the documentation's rule that "the outer boundaries of the
inner group are treated in exactly the same way as any other elements within
the outer group": an inner sequence's boundaries take part in the outer
sequence's ordering like anything else. Each date belongs to exactly ONE
bounded group -- the innermost pair of boundaries enclosing it -- so a
nested group's dates are not also counted in its parent's span term.

Sequences with a measured ordinate
----------------------------------
Two more element types order their members by a distance measured on an axis
OUTSIDE the model rather than by stratigraphy alone. They are the same idea
with two solvers behind them (docs/AGE_DEPTH_AND_WIGGLE_MATCH_PLAN.md):

  wiggle_sequence      OxCal's D_Sequence / V_Sequence. Its members are
                       separated by KNOWN gaps -- tree rings, varves, any
                       sampled series -- so the whole series has ONE free
                       parameter, the calendar age at the reference point, and
                       every member's age is that plus its own `ring_offset`.
                       Solved in closed form by services/wiggle_match.py.
  deposition_sequence  OxCal's P_Sequence. Its members are separated by DEPTH,
                       and deposition is modelled as a Poisson process along
                       it, so the intervals carry their own prior with a
                       rigidity `k`. Every member is a free parameter; solved
                       by services/gibbs.py.
  interpolated_depth   A level inside a deposition_sequence with no
                       measurement -- "how old is the level at 47 cm". A
                       parameter whose likelihood table is flat, which is
                       precisely the right answer, so it needs no special case
                       in the sampler.

Dates attach to a wiggle_sequence and a deposition_sequence DIRECTLY, as they
do to a phase, because in both the ordinate is a property of the date and
inserting a phase per member would say something the model does not.

THE ONE THING BOTH GET WRONG QUIETLY. A deposition_sequence supplies its own
prior on the intervals between its members, and a wiggle_sequence fixes them
outright. Neither set of dates may ALSO be treated as a uniform scatter
between the enclosing Boundaries -- two priors over the same intervals would
not crash and would not look wrong. So they bubble up as CONTAINED rather than
as members of the span term; see SpanGroup.
"""
import itertools
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

ELEMENT_TYPES = (
    'sequence', 'phase', 'boundary', 'hiatus',
    'wiggle_sequence', 'deposition_sequence', 'interpolated_depth',
)

# Element types dates may be attached to. A date is a measurement of one
# event, and these are the elements that say where that event sits.
DATE_HOLDERS = ('phase', 'wiggle_sequence', 'deposition_sequence')

# What an unnamed element of each type is called on screen and in the
# exports. Only the ones that do not title-case into English need an entry.
ELEMENT_LABELS = {
    'wiggle_sequence': 'Wiggle match',
    'deposition_sequence': 'Deposition sequence',
    'interpolated_depth': 'Undated level',
}


def element_label(element: Dict) -> str:
    """An element's name, or a readable default for its type."""
    etype = element.get('element_type') or 'element'
    return element.get('name') or ELEMENT_LABELS.get(etype) or etype.title()


# What ModelElement.params holds, per element_type, with the default for each
# setting. Documented here rather than in models.py because this module is
# what reads it; models.py points at this name.
ELEMENT_PARAMS = {
    'wiggle_sequence': {
        # 'rings' | 'varves' | 'years' -- what a gap is counted in. Rings are
        # the overwhelming case, but varve counts and sampled sequences are
        # the same maths in different units, and carrying this makes the axis
        # label honest rather than calling everything "rings".
        'gap_units': 'rings',
        # Which member `ring_offset` 0 refers to. The solved parameter is the
        # age AT offset zero whether or not a member sits there, so this is
        # for labelling and for the entry helper that turns ring numbers into
        # offsets -- but it is recorded now, so that adding a sapwood
        # allowance later is not a migration of everything.
        'reference_sample_id': None,
    },
    'deposition_sequence': {
        # Rigidity, in events per unit depth. Large k drives the ages onto a
        # straight line in depth; small k leaves little but the ordering. The
        # run infers it over the two bounds below unless `k_variable` is
        # false, because a fixed k chosen badly dominates the answer.
        'k': None,
        'k_variable': True,
        'log_k_min': -2.0,
        'log_k_max': 2.0,
        'depth_units': 'cm',
    },
    'interpolated_depth': {
        # The depth this undated level sits at, in the sequence's own units.
        'depth_cm': None,
    },
}

# A term carrying a calendar age in the model: either a dated sample or a
# boundary parameter. Ordering constraints are expressed over these. An
# interpolated level is a 'date' too -- it is a free calendar parameter with a
# flat likelihood, and every ordering rule that applies to a date applies to
# it unchanged.
Term = Tuple[str, str]  # ('date' | 'boundary', id)


class SpanGroup:
    """
    A set of parameters enclosed by a consecutive pair of Boundary siblings.

    `date_ids` are the UNIFORM SCATTER: conditional on its boundaries each of
    those K dates is uniform between them, which together contribute OxCal's
    Phase density (older - younger)^(-K).

    `contained_ids` must also lie between the two boundaries but are NOT part
    of that term, because something else already prices the intervals between
    them -- a deposition sequence's Poisson prior, or a wiggle sequence's
    fixed gaps. Counting them twice would leave phase widths wrong by a factor
    nobody could eyeball, so the two are kept apart at the point the structure
    is planned rather than at the point it is solved.
    """

    def __init__(self, older_boundary_id: str, younger_boundary_id: str,
                 date_ids: List[str], contained_ids: Optional[List[str]] = None):
        self.older_boundary_id = older_boundary_id
        self.younger_boundary_id = younger_boundary_id
        self.date_ids = date_ids
        self.contained_ids = list(contained_ids or [])

    def __repr__(self):
        extra = (f", {len(self.contained_ids)} contained"
                 if self.contained_ids else '')
        return (f"SpanGroup({self.older_boundary_id} -> {self.younger_boundary_id}, "
                f"{len(self.date_ids)} dates{extra})")


class WiggleGroup:
    """
    One wiggle_sequence: a series whose members are separated by known gaps,
    and therefore ONE free calendar parameter.

    `members` are (date_id, ring_offset, ring_offset_error) ordered YOUNGEST
    FIRST -- ascending offset, since an offset counts years BEFORE the
    reference and so increases into the past. `element_id` names the
    parameter itself: the calendar age at offset zero.
    """

    def __init__(self, element_id: str, name: str,
                 members: List[Tuple[str, float, float]],
                 gap_units: str = 'rings',
                 reference_sample_id: Optional[str] = None):
        self.element_id = element_id
        self.name = name
        self.members = members
        self.gap_units = gap_units
        self.reference_sample_id = reference_sample_id

    @property
    def date_ids(self) -> List[str]:
        return [date_id for date_id, _offset, _error in self.members]

    def __repr__(self):
        return f"WiggleGroup({self.element_id}, {len(self.members)} members)"


class DepositionGroup:
    """
    One deposition_sequence: levels at measured depths whose ages are coupled
    by a Poisson deposition model rather than only by their ordering.

    `levels` are (parameter_id, depth, is_dated) ordered OLDEST FIRST --
    descending depth, since deeper is older, which is the same convention
    `position` uses for a Sequence's children. A level with is_dated False is
    an interpolated_depth: a free parameter with a flat likelihood.

    `k` is the rigidity: a number when the model fixes it, None when the run
    should infer it over [log_k_min, log_k_max].
    """

    def __init__(self, element_id: str, name: str,
                 levels: List[Tuple[str, float, bool]],
                 k: Optional[float] = None, k_variable: bool = True,
                 log_k_min: float = -2.0, log_k_max: float = 2.0,
                 depth_units: str = 'cm',
                 level_names: Optional[Dict[str, str]] = None):
        self.element_id = element_id
        self.name = name
        self.levels = levels
        self.k = k
        self.k_variable = k_variable
        self.log_k_min = log_k_min
        self.log_k_max = log_k_max
        self.depth_units = depth_units
        # An undated level's own name where it was given one, so a reported
        # posterior can say "Tephra layer" rather than only "47 cm".
        self.level_names = dict(level_names or {})

    @property
    def parameter_ids(self) -> List[str]:
        return [pid for pid, _depth, _dated in self.levels]

    def __repr__(self):
        return (f"DepositionGroup({self.element_id}, {len(self.levels)} levels, "
                f"k={'variable' if self.k is None else self.k})")


class ModelPlan:
    """Everything a solver needs to turn a tree into a posterior."""

    def __init__(
        self,
        ordering: List[Tuple[List[Term], List[Term]]],
        span_groups: List[SpanGroup],
        marginalized_boundaries: Set[str],
        sampled_boundaries: List[str],
        boundary_names: Dict[str, str],
        wiggle_groups: Optional[List[WiggleGroup]] = None,
        deposition_groups: Optional[List[DepositionGroup]] = None,
    ):
        # Each entry is (older_terms, younger_terms): every term on the left
        # must be older (larger cal BP) than every term on the right. One
        # constraint per adjacent pair of sequence children rather than the
        # full cross-product, which min(older) >= max(younger) makes equivalent.
        self.ordering = ordering
        self.span_groups = span_groups
        # Boundaries integrated out analytically rather than sampled; see
        # classify_boundaries.
        self.marginalized_boundaries = marginalized_boundaries
        self.sampled_boundaries = sampled_boundaries
        self.boundary_names = boundary_names
        self.wiggle_groups = list(wiggle_groups or [])
        self.deposition_groups = list(deposition_groups or [])


# --------------------------------------------------------------------------
# Tree walking
# --------------------------------------------------------------------------

def iter_elements(root: Dict):
    """Depth-first walk yielding every element in the tree."""
    stack = [root]
    while stack:
        element = stack.pop()
        yield element
        stack.extend(reversed(element.get('children') or []))


def _children(element: Dict) -> List[Dict]:
    """Children in chronological order (oldest first)."""
    return list(element.get('children') or [])


def element_params(element: Dict) -> Dict:
    """
    One element's `params`, with the defaults for its type filled in.

    Read through this rather than off the dict, so a tree written before a
    setting existed reads as that setting's default rather than as None --
    which is what lets ELEMENT_PARAMS gain an entry without a migration.
    """
    defaults = ELEMENT_PARAMS.get(element.get('element_type')) or {}
    merged = dict(defaults)
    for key, value in (element.get('params') or {}).items():
        if value is not None or key in defaults:
            merged[key] = value
    return merged


def validate_tree(root: Optional[Dict], measurements: List[Dict]) -> None:
    """
    Reject a structurally impossible model before any run, as
    BayesianRequest's validators do. Raises ValueError. No cycle check: a tree
    cannot contain one.

    Structure only. Whether each date carries the ORDINATE its element is
    built on -- a ring offset, a depth -- is validate_ordinates' job, because
    most callers of this function have only the ids and the element
    assignments to hand.
    """
    if root is None:
        raise ValueError("model tree is empty")
    if root.get('element_type') not in ('sequence', 'phase',
                                        'wiggle_sequence', 'deposition_sequence'):
        raise ValueError(
            "the root element must be a sequence, a phase, a wiggle match or "
            "a deposition sequence")

    seen_ids: Set[str] = set()
    element_types: Dict[str, str] = {}
    dates_per_element: Dict[str, int] = defaultdict(int)
    for m in measurements:
        if m.get('element_id'):
            dates_per_element[m['element_id']] += 1

    for element in iter_elements(root):
        eid = element.get('id')
        if not eid:
            raise ValueError("every model element needs an id")
        if eid in seen_ids:
            raise ValueError(f"model element id '{eid}' appears more than once (a tree cannot revisit a node)")
        seen_ids.add(eid)

        etype = element.get('element_type')
        if etype not in ELEMENT_TYPES:
            raise ValueError(f"unknown element_type '{etype}' on element '{eid}'")
        element_types[eid] = etype

        children = _children(element)
        if etype in ('boundary', 'hiatus', 'interpolated_depth') and children:
            raise ValueError(f"a {etype} cannot contain other elements (element '{eid}')")

        if etype == 'wiggle_sequence':
            if children:
                raise ValueError(
                    f"a wiggle match holds dates and nothing else (element "
                    f"'{eid}') -- its members are separated by measured gaps, "
                    "so there is no position in it for a phase or a boundary"
                )
            if not dates_per_element.get(eid):
                raise ValueError(
                    f"the wiggle match '{element.get('name') or eid}' has no "
                    "dates in it; a series of gaps with nothing measured at "
                    "them says nothing about the calendar"
                )

        if etype == 'deposition_sequence':
            for child in children:
                if child.get('element_type') != 'interpolated_depth':
                    raise ValueError(
                        f"a deposition sequence holds dates and undated levels "
                        f"only (element '{eid}' contains a "
                        f"{child.get('element_type')})"
                    )
            if not dates_per_element.get(eid):
                raise ValueError(
                    f"the deposition sequence '{element.get('name') or eid}' "
                    "has no dates in it; deposition cannot be modelled against "
                    "depth with nothing measured at any level"
                )
            if dates_per_element[eid] + len(children) < 2:
                raise ValueError(
                    f"the deposition sequence '{element.get('name') or eid}' "
                    "has one level in it; a deposition rate is a statement "
                    "about the interval between two, so a single level has "
                    "nothing to model"
                )

        if etype == 'interpolated_depth':
            if dates_per_element.get(eid):
                raise ValueError(
                    f"the undated level '{element.get('name') or eid}' has a "
                    "date attached to it. It exists to report an age where "
                    "nothing was dated; a level with a measurement is an "
                    "ordinary date at that depth"
                )
            if element_params(element).get('depth_cm') is None:
                raise ValueError(
                    f"the undated level '{element.get('name') or eid}' has no "
                    "depth. A level with no depth has no place in a deposition "
                    "model and no age to report"
                )

        if etype == 'phase':
            for child in children:
                if child.get('element_type') == 'boundary':
                    raise ValueError(
                        f"a boundary cannot sit directly inside a phase (element '{eid}') -- "
                        "a phase is unordered, so there is no position for a transition to "
                        "mark. Put the boundary in a sequence."
                    )

        # A hiatus only means anything between two boundaries.
        if etype == 'sequence':
            types = [c.get('element_type') for c in children]
            for i, t in enumerate(types):
                if t != 'hiatus':
                    continue
                before = types[i - 1] if i > 0 else None
                after = types[i + 1] if i + 1 < len(types) else None
                if before != 'boundary' or after != 'boundary':
                    raise ValueError(
                        f"a hiatus must sit between two boundaries (element "
                        f"'{children[i].get('id')}') -- it marks a gap between the end of one "
                        "group and the start of the next"
                    )

        tpq, taq = element.get('tpq_age_bp'), element.get('taq_age_bp')
        if tpq is not None and taq is not None and taq > tpq:
            raise ValueError(
                f"element '{eid}' has taq_age_bp ({taq}) greater than tpq_age_bp ({tpq}); "
                "the valid range would be empty"
            )

    # Every date must live somewhere that gives its event a position.
    for m in measurements:
        element_id = m.get('element_id')
        if not element_id:
            raise ValueError(
                f"sample '{m.get('sample_name', m.get('id'))}' is not assigned to any model element"
            )
        if element_id not in seen_ids:
            raise ValueError(
                f"sample '{m.get('sample_name', m.get('id'))}' references unknown model element "
                f"'{element_id}'"
            )
        if element_types[element_id] not in DATE_HOLDERS:
            raise ValueError(
                f"sample '{m.get('sample_name', m.get('id'))}' is attached to a "
                f"{element_types[element_id]}; dates belong to a phase, a "
                "wiggle match or a deposition sequence"
            )


def validate_ordinates(root: Optional[Dict], measurements: List[Dict]) -> None:
    """
    What each element requires of the DATES inside it: the ordinate it is
    built on, and the settings it can and cannot honour. Raises ValueError.

    Separate from validate_tree because all of this lives on the date rather
    than on the element, and most callers of validate_tree hand it only ids
    and element assignments. Called wherever the full measurements are to hand
    -- the request schema, the importer, the model-tree editor's endpoint and
    the solvers -- so a model that cannot be solved is refused before anything
    is written rather than minutes into a run.
    """
    if root is None:
        return
    by_element: Dict[str, List[Dict]] = defaultdict(list)
    for m in measurements:
        if m.get('element_id'):
            by_element[m['element_id']].append(m)

    def label(m: Dict) -> str:
        return str(m.get('sample_name') or m.get('id'))

    for element in iter_elements(root):
        etype = element.get('element_type')
        name = element.get('name') or element.get('id')
        members = by_element.get(element.get('id'), [])

        if etype == 'wiggle_sequence':
            missing = [label(m) for m in members if m.get('ring_offset') is None]
            if missing:
                raise ValueError(
                    f"the wiggle match '{name}' has dates with no gap recorded "
                    f"({', '.join(sorted(missing)[:4])}). A wiggle match IS the "
                    "series of gaps: without one, a date says no more than it "
                    "would on its own"
                )
            offsets = [float(m['ring_offset']) for m in members]
            if len(set(offsets)) != len(offsets):
                raise ValueError(
                    f"the wiggle match '{name}' has two dates at the same gap. "
                    "Two samples from the same ring are one event measured "
                    "twice, not two members of a series"
                )
            for m in members:
                error = m.get('ring_offset_error')
                if error is not None and error < 0:
                    raise ValueError(
                        f"sample '{label(m)}' has a negative gap uncertainty"
                    )
            reference = element_params(element).get('reference_sample_id')
            if reference is not None and reference not in {m['id'] for m in members}:
                raise ValueError(
                    f"the wiggle match '{name}' names a reference sample that "
                    "is not one of its members"
                )
            # REFUSED, not ignored. A wiggle match has one free parameter and
            # no room for a date to sit apart from the others: the gaps are
            # asserted, so a member that does not belong cannot be absorbed --
            # which is the whole reason the combination agreement index is a
            # required output here rather than a nicety. Accepting the setting
            # and dropping it would be the silent substitution this app
            # refuses everywhere else.
            flagged = [label(m) for m in members if (m.get('outlier_prior') or 0) > 0]
            if flagged:
                raise ValueError(
                    f"the wiggle match '{name}' has dates carrying an outlier "
                    f"model ({', '.join(sorted(flagged)[:4])}), which it cannot "
                    "apply. Its gaps are asserted, so there is nothing for a "
                    "date to be an outlier from -- a member that does not "
                    "belong shows up in the agreement index instead. Turn the "
                    "outlier setting off on those dates, or model them outside "
                    "the series"
                )

        if etype == 'deposition_sequence':
            missing = [label(m) for m in members if m.get('depth_cm') is None]
            if missing:
                raise ValueError(
                    f"the deposition sequence '{name}' has dates with no depth "
                    f"({', '.join(sorted(missing)[:4])}). Depth is the axis the "
                    "deposition model runs along, so a date without one cannot "
                    "take part in it"
                )
            depths = [float(m['depth_cm']) for m in members]
            depths += [float(element_params(c)['depth_cm'])
                       for c in _children(element)
                       if element_params(c).get('depth_cm') is not None]
            if len(set(depths)) != len(depths):
                raise ValueError(
                    f"the deposition sequence '{name}' has two levels at the "
                    "same depth. The model prices the interval between "
                    "successive depths, and a zero interval has no rate"
                )
            params = element_params(element)
            k = params.get('k')
            if k is not None and float(k) <= 0:
                raise ValueError(
                    f"the deposition sequence '{name}' has a rigidity of {k}. "
                    "k is events per unit depth and must be positive"
                )
            if (params.get('log_k_min') is not None
                    and params.get('log_k_max') is not None
                    and float(params['log_k_min']) >= float(params['log_k_max'])):
                raise ValueError(
                    f"the deposition sequence '{name}' has an empty range for "
                    "its rigidity: log_k_min is not below log_k_max"
                )


# --------------------------------------------------------------------------
# Tree -> model plan
# --------------------------------------------------------------------------

def _subtree_terms(element: Dict, dates_by_element: Dict[str, List[str]]) -> List[Term]:
    """
    Every age-bearing term in this subtree: its dates and its boundaries.

    An interpolated level counts as a date. It is a free calendar parameter
    with a flat likelihood, so every ordering rule that applies to a date
    applies to it -- and leaving it out would let a Sequence order the
    deposition sequence around it while the level itself floated free.
    """
    terms: List[Term] = []
    for node in iter_elements(element):
        etype = node.get('element_type')
        if etype == 'boundary':
            terms.append(('boundary', node['id']))
        elif etype == 'interpolated_depth':
            terms.append(('date', node['id']))
        for date_id in dates_by_element.get(node['id'], []):
            terms.append(('date', date_id))
    return terms


# What an element hands up to whatever encloses it: parameters that are a
# UNIFORM scatter between the enclosing boundaries, and parameters that must
# merely be CONTAINED by them because their spacing is already priced
# elsewhere. See SpanGroup.
Unclaimed = Tuple[List[str], List[str]]


def _plan_element(
    element: Dict,
    dates_by_element: Dict[str, List[str]],
    ordering: List[Tuple[List[Term], List[Term]]],
    span_groups: List[SpanGroup],
    measurements_by_id: Dict[str, Dict],
    wiggle_groups: List[WiggleGroup],
    deposition_groups: List[DepositionGroup],
) -> Unclaimed:
    """
    Plan one element. Returns the parameters in this subtree not yet enclosed
    by any boundary pair, as (uniform, contained), so they bubble up to an
    enclosing group -- or, unclaimed, get no uniform-scatter term, matching
    OxCal, where a date outside every boundary is unconstrained by a phase
    prior.
    """
    etype = element.get('element_type')
    children = _children(element)

    if etype in ('boundary', 'hiatus'):
        return [], []
    if etype == 'interpolated_depth':
        # Its own deposition sequence records it and orders it. It hands
        # itself up as CONTAINED so an enclosing Boundary pair still pens it
        # in without pricing its spacing a second time.
        return [], [element['id']]

    if etype == 'wiggle_sequence':
        group = _wiggle_group(element, dates_by_element, measurements_by_id)
        wiggle_groups.append(group)
        # Ordered among themselves by construction -- the gaps ARE the
        # ordering -- so no ordering constraints are emitted for them. Their
        # spacing is fixed, so they are contained rather than uniform.
        return [], list(group.date_ids)

    if etype == 'deposition_sequence':
        group = _deposition_group(element, dates_by_element, measurements_by_id)
        deposition_groups.append(group)
        # Deeper is older, and the deposition prior is written over ADJACENT
        # levels, so the ordering is emitted pairwise here rather than left to
        # a Sequence that is not there.
        ids = group.parameter_ids
        for older, younger in zip(ids, ids[1:]):
            ordering.append(([('date', older)], [('date', younger)]))
        return [], list(ids)

    # Plan children first so nested groups claim their own dates before an
    # outer group gets a chance to.
    unclaimed_by_child: List[Unclaimed] = [
        _plan_element(child, dates_by_element, ordering, span_groups,
                      measurements_by_id, wiggle_groups, deposition_groups)
        for child in children
    ]
    own_dates = list(dates_by_element.get(element['id'], []))

    if etype == 'phase':
        # Unordered: no constraints between children. Everything bubbles up.
        uniform = list(own_dates)
        contained: List[str] = []
        for child_uniform, child_contained in unclaimed_by_child:
            uniform.extend(child_uniform)
            contained.extend(child_contained)
        return uniform, contained

    # sequence: children are ordered oldest -> youngest.
    for i in range(len(children) - 1):
        older_terms = _subtree_terms(children[i], dates_by_element)
        younger_terms = _subtree_terms(children[i + 1], dates_by_element)
        if older_terms and younger_terms:
            ordering.append((older_terms, younger_terms))

    # Bounded groups: dates between each consecutive pair of Boundary children.
    boundary_positions = [
        i for i, c in enumerate(children) if c.get('element_type') == 'boundary'
    ]
    claimed: Set[str] = set()
    for older_idx, younger_idx in zip(boundary_positions, boundary_positions[1:]):
        enclosed_uniform: List[str] = []
        enclosed_contained: List[str] = []
        for i in range(older_idx + 1, younger_idx):
            enclosed_uniform.extend(unclaimed_by_child[i][0])
            enclosed_contained.extend(unclaimed_by_child[i][1])
        span_groups.append(SpanGroup(
            older_boundary_id=children[older_idx]['id'],
            younger_boundary_id=children[younger_idx]['id'],
            date_ids=enclosed_uniform,
            contained_ids=enclosed_contained,
        ))
        claimed.update(enclosed_uniform)
        claimed.update(enclosed_contained)

    uniform = list(own_dates)
    contained = []
    for child_uniform, child_contained in unclaimed_by_child:
        uniform.extend(d for d in child_uniform if d not in claimed)
        contained.extend(d for d in child_contained if d not in claimed)
    return uniform, contained


def _wiggle_group(element: Dict, dates_by_element: Dict[str, List[str]],
                  measurements_by_id: Dict[str, Dict]) -> WiggleGroup:
    """
    One wiggle_sequence as a WiggleGroup, members ordered YOUNGEST FIRST.

    Ascending offset is youngest first, because an offset counts years BEFORE
    the reference: 0 at the reference, increasing into the past. A missing
    offset reads as zero here rather than raising -- validate_ordinates is
    where that is refused, and it can say which sample and why.
    """
    params = element_params(element)
    members = []
    for date_id in dates_by_element.get(element['id'], []):
        m = measurements_by_id.get(date_id) or {}
        members.append((
            date_id,
            float(m.get('ring_offset') or 0.0),
            float(m.get('ring_offset_error') or 0.0),
        ))
    members.sort(key=lambda entry: entry[1])
    return WiggleGroup(
        element_id=element['id'],
        name=element_label(element),
        members=members,
        gap_units=params.get('gap_units') or 'rings',
        reference_sample_id=params.get('reference_sample_id'),
    )


def _deposition_group(element: Dict, dates_by_element: Dict[str, List[str]],
                      measurements_by_id: Dict[str, Dict]) -> DepositionGroup:
    """
    One deposition_sequence as a DepositionGroup, levels ordered OLDEST FIRST.

    Descending depth is oldest first: deeper is older, which is the same
    convention `position` uses for a Sequence's children and the Harris matrix
    uses for its rows. Undated levels take their depth from their own params.
    """
    params = element_params(element)
    levels: List[Tuple[str, float, bool]] = []
    level_names: Dict[str, str] = {}
    for date_id in dates_by_element.get(element['id'], []):
        m = measurements_by_id.get(date_id) or {}
        levels.append((date_id, float(m.get('depth_cm') or 0.0), True))
    for child in _children(element):
        if child.get('element_type') != 'interpolated_depth':
            continue
        depth = element_params(child).get('depth_cm')
        levels.append((child['id'], float(depth or 0.0), False))
        if child.get('name'):
            level_names[child['id']] = child['name']
    levels.sort(key=lambda entry: -entry[1])

    k = params.get('k')
    # A fixed k only when the model asked for one: `k_variable` is what
    # decides, so a value left behind by an earlier edit cannot quietly pin
    # the rigidity of a run that meant to infer it.
    variable = bool(params.get('k_variable', True)) or k is None
    return DepositionGroup(
        element_id=element['id'],
        name=element_label(element),
        levels=levels,
        k=None if variable else float(k),
        k_variable=variable,
        log_k_min=float(params.get('log_k_min', -2.0)),
        log_k_max=float(params.get('log_k_max', 2.0)),
        depth_units=params.get('depth_units') or 'cm',
        level_names=level_names,
    )


def with_implicit_boundaries(root: Dict, new_id=None) -> Tuple[Dict, List[str]]:
    """
    A copy of `root` with a Boundary inserted between any two adjacent
    non-Boundary children of a Sequence, plus the ids of what was inserted.

    A Sequence that orders two groups with no Boundary between them puts all
    their dates in ONE bounded group and bolts an ordering constraint on top,
    which does not factorise along a chain, so exact_chain declines it.
    Inserting the transition makes it a chain, and the evidence says OxCal
    reads it the same way: on the fixture its `ordered` and `shared_boundary`
    runs agree to 0.948 posterior overlap.

    It is a real change to the model rather than a rewriting into an equal form
    -- the uniform-scatter prior becomes one term per group instead of one
    across both -- which is why the inserted ids are returned rather than
    hidden, so the caller can say what it did.
    """
    counter = itertools.count(1)
    if new_id is None:
        def new_id():
            return f'implicit-boundary-{next(counter)}'
    inserted: List[str] = []

    def rebuild(element: Dict) -> Dict:
        node = dict(element)
        children = [rebuild(c) for c in _children(element)]
        if element.get('element_type') == 'sequence' and children:
            spaced: List[Dict] = []
            for child in children:
                if (spaced
                        and child.get('element_type') != 'boundary'
                        and spaced[-1].get('element_type') != 'boundary'):
                    bid = new_id()
                    inserted.append(bid)
                    spaced.append({
                        'id': bid, 'element_type': 'boundary', 'name': 'Transition',
                        'tpq_age_bp': None, 'taq_age_bp': None, 'children': [],
                    })
                spaced.append(child)
            children = spaced
        node['children'] = children
        return node

    return rebuild(root), inserted


def classify_boundaries(
    boundary_ids: List[str],
    span_groups: List[SpanGroup],
    ordering: List[Tuple[List[Term], List[Term]]],
) -> Tuple[Set[str], List[str]]:
    """
    Split boundaries into those that can be integrated out analytically and
    those that must be sampled.

    Only the OUTERMOST boundaries are heavy-tailed: an interior one is squeezed
    between the youngest date of the group before it and the oldest of the
    group after, while an outermost one extends into a region with no data at
    all. Appearing in only ONE span term, an outermost boundary also integrates
    out in closed form.

    "Outermost" is structural, not positional: a boundary is outermost-old if
    nothing in the model must be older than it, outermost-young if nothing must
    be younger. That covers nested and branching models, where the extreme
    boundaries need not be the root sequence's first and last children.
    """
    has_something_older: Set[str] = set()
    has_something_younger: Set[str] = set()
    for older_terms, younger_terms in ordering:
        for kind, tid in younger_terms:
            if kind == 'boundary':
                has_something_older.add(tid)
        for kind, tid in older_terms:
            if kind == 'boundary':
                has_something_younger.add(tid)

    # Only integrable out when it appears in exactly one span term; otherwise
    # it couples two groups and the integral stops factorising.
    appearances: Dict[str, int] = defaultdict(int)
    for group in span_groups:
        appearances[group.older_boundary_id] += 1
        appearances[group.younger_boundary_id] += 1

    marginalized: Set[str] = set()
    for bid in boundary_ids:
        if appearances.get(bid, 0) != 1:
            continue
        if bid not in has_something_older or bid not in has_something_younger:
            marginalized.add(bid)

    sampled = [bid for bid in boundary_ids if bid not in marginalized]
    return marginalized, sampled


def inherited_bounds(root: Dict) -> Dict[str, Tuple[Optional[float], Optional[float]]]:
    """
    The effective (taq, tpq) limiters for every element, combining its own with
    every ancestor's, tightest winning -- stratigraphy.effective_bounds's rule
    at arbitrary nesting depth. Either bound may be None, meaning unbounded.
    """
    out: Dict[str, Tuple[Optional[float], Optional[float]]] = {}

    def walk(element: Dict, taq: Optional[float], tpq: Optional[float]):
        own_taq, own_tpq = element.get('taq_age_bp'), element.get('tpq_age_bp')
        # taq is a minimum age, so the tightest is the largest; tpq is a
        # maximum age, so the tightest is the smallest.
        combined_taq = own_taq if taq is None else (taq if own_taq is None else max(taq, own_taq))
        combined_tpq = own_tpq if tpq is None else (tpq if own_tpq is None else min(tpq, own_tpq))
        out[element['id']] = (combined_taq, combined_tpq)
        for child in _children(element):
            walk(child, combined_taq, combined_tpq)

    walk(root, None, None)
    return out


def plan_model(root: Dict, measurements: List[Dict]) -> ModelPlan:
    """
    Turn a validated model tree into ordering constraints, bounded groups and
    a boundary classification. Pure structure -- no PyMC involved, so it is
    directly unit-testable without a sampler.
    """
    dates_by_element: Dict[str, List[str]] = defaultdict(list)
    for m in measurements:
        if m.get('element_id'):
            dates_by_element[m['element_id']].append(m['id'])
    measurements_by_id = {m['id']: m for m in measurements}

    ordering: List[Tuple[List[Term], List[Term]]] = []
    span_groups: List[SpanGroup] = []
    wiggle_groups: List[WiggleGroup] = []
    deposition_groups: List[DepositionGroup] = []
    _plan_element(root, dates_by_element, ordering, span_groups,
                  measurements_by_id, wiggle_groups, deposition_groups)

    boundary_ids = [
        e['id'] for e in iter_elements(root) if e.get('element_type') == 'boundary'
    ]
    boundary_names = {
        e['id']: (e.get('name') or 'Boundary')
        for e in iter_elements(root) if e.get('element_type') == 'boundary'
    }
    marginalized, sampled = classify_boundaries(boundary_ids, span_groups, ordering)

    return ModelPlan(
        ordering=ordering,
        span_groups=span_groups,
        marginalized_boundaries=marginalized,
        sampled_boundaries=sampled,
        boundary_names=boundary_names,
        wiggle_groups=wiggle_groups,
        deposition_groups=deposition_groups,
    )


# --------------------------------------------------------------------------
# Finding the ordinate-bearing sequences
# --------------------------------------------------------------------------

def elements_of_type(root: Optional[Dict], etype: str) -> List[Dict]:
    """Every element of one type, in depth-first order."""
    if root is None:
        return []
    return [e for e in iter_elements(root) if e.get('element_type') == etype]


def standalone_wiggle_match(root: Optional[Dict],
                            measurements: List[Dict]) -> Optional[Dict]:
    """
    The single wiggle_sequence this model consists of, or None.

    "Consists of" is deliberately strict: exactly one wiggle sequence, every
    date in it, and nothing else in the tree carrying an age -- no Boundary,
    no second phase with dates in it. Anything looser would be a model whose
    OTHER parameters the closed-form solver has no way to report, and the
    right answer to that is to say so rather than to solve part of it.

    A Sequence or Phase wrapping one wiggle sequence and nothing else still
    counts: the wrapper adds no ordering and no parameter of its own, and both
    the New Analysis preset and the OxCal importer build exactly that shape.
    """
    if root is None:
        return None
    wiggles = elements_of_type(root, 'wiggle_sequence')
    if len(wiggles) != 1:
        return None
    only = wiggles[0]
    if any(e.get('element_type') in ('boundary', 'deposition_sequence',
                                     'interpolated_depth')
           for e in iter_elements(root)):
        return None
    if any(m.get('element_id') != only['id'] for m in measurements):
        return None
    return only


# --------------------------------------------------------------------------
# Presentation order
# --------------------------------------------------------------------------

def flatten_structure(root: Optional[Dict], measurements: List[Dict]) -> List[Dict]:
    """
    Flatten a model tree into the order it should be READ in: depth first,
    siblings oldest first, each phase's own dates before any branch nested
    inside it. rows_to_tree already sorts siblings by position, so this walk is
    the chronological reading of the model.

    The single definition of "model order" behind the results table and every
    export, so a date cannot be filed under one phase on screen and another in
    the CSV.

    `measurements` are dicts with at least id/sample_name. A date is placed by
    element_id, falling back to group_id for a migrated tree where each phase
    reuses its context's id; anything resolving to neither is collected under a
    trailing "Unassigned" heading rather than dropped.

    Each row is {kind, element_id, name, depth, path, posterior, tpq_age_bp,
    taq_age_bp, sample}: `kind` is an element_type or 'date', and `sample` is
    the measurement dict on a date row and None otherwise.
    """
    rows: List[Dict] = []
    # No structure at all is not the same as dates missing from a structure:
    # callers fall back to a flat list rather than filing every date under a
    # misleading "Unassigned" heading.
    if root is None:
        return rows

    dates_by_element: Dict[str, List[Dict]] = {}
    for m in measurements:
        key = m.get('element_id') or m.get('group_id')
        if key:
            dates_by_element.setdefault(key, []).append(m)

    placed: set = set()

    def _date_row(m: Dict, depth: int, path: List[str], element_id: Optional[str]) -> Dict:
        placed.add(m.get('id'))
        return {
            'kind': 'date',
            'element_id': element_id,
            'name': m.get('sample_name') or m.get('id'),
            'depth': depth,
            'path': ' > '.join(path),
            'posterior': None,
            'tpq_age_bp': m.get('tpq_age_bp'),
            'taq_age_bp': m.get('taq_age_bp'),
            'sample': m,
        }

    def visit(element: Dict, depth: int, ancestors: List[str]) -> None:
        name = element_label(element)
        path = ancestors + [name]
        rows.append({
            'kind': element.get('element_type'),
            'element_id': element.get('id'),
            'name': name,
            'depth': depth,
            'path': ' > '.join(path),
            'posterior': element.get('posterior'),
            'tpq_age_bp': element.get('tpq_age_bp'),
            'taq_age_bp': element.get('taq_age_bp'),
            'sample': None,
        })
        for m in dates_by_element.get(element.get('id'), []):
            rows.append(_date_row(m, depth + 1, path, element.get('id')))
        for child in _children(element):
            visit(child, depth + 1, path)

    if root is not None:
        visit(root, 0, [])

    orphans = [m for m in measurements if m.get('id') not in placed]
    if orphans:
        rows.append({
            'kind': 'unassigned', 'element_id': None, 'name': 'Unassigned',
            'depth': 0, 'path': 'Unassigned', 'posterior': None,
            'tpq_age_bp': None, 'taq_age_bp': None, 'sample': None,
        })
        for m in orphans:
            rows.append(_date_row(m, 1, ['Unassigned'], None))

    return rows


# --------------------------------------------------------------------------
# Flat rows <-> nested tree
# --------------------------------------------------------------------------

def rows_to_tree(rows: List) -> Optional[Dict]:
    """
    Rebuild the nested tree from flat ModelElement rows (parent_id +
    position), siblings in `position` order, oldest first. None if there are no
    rows.

    A row whose parent_id does not resolve is attached to the root rather than
    dropped, so a partially-corrupt tree round-trips visibly.
    """
    if not rows:
        return None

    nodes: Dict[str, Dict] = {}
    for row in rows:
        nodes[row.id] = {
            'id': row.id,
            'element_type': row.element_type,
            'name': row.name,
            'tpq_age_bp': row.tpq_age_bp,
            'taq_age_bp': row.taq_age_bp,
            'params': getattr(row, 'params', None),
            'posterior': row.posterior,
            'children': [],
            '_position': row.position or 0,
        }

    roots = [r for r in rows if not r.parent_id or r.parent_id not in nodes]
    if not roots:
        return None
    root_row = roots[0]

    for row in rows:
        if row.id == root_row.id:
            continue
        parent = nodes.get(row.parent_id) or nodes[root_row.id]
        parent['children'].append(nodes[row.id])

    def sort_children(node: Dict):
        node['children'].sort(key=lambda c: c['_position'])
        for child in node['children']:
            sort_children(child)
        node.pop('_position', None)

    root = nodes[root_row.id]
    sort_children(root)
    root.pop('_position', None)
    return root


def tree_to_rows(root: Dict, analysis_id: str, model_element_cls) -> List:
    """
    Flatten a nested tree into ModelElement rows ready to persist. Incoming ids
    are preserved, so a date's element_id keeps pointing at the right phase
    across an edit.
    """
    rows = []

    def walk(element: Dict, parent_id: Optional[str]):
        rows.append(model_element_cls(
            id=element['id'],
            analysis_id=analysis_id,
            parent_id=parent_id,
            position=element.get('_position', 0),
            element_type=element['element_type'],
            name=element.get('name'),
            tpq_age_bp=element.get('tpq_age_bp'),
            taq_age_bp=element.get('taq_age_bp'),
            params=element.get('params'),
            posterior=element.get('posterior'),
        ))
        for position, child in enumerate(_children(element)):
            child['_position'] = position
            walk(child, element['id'])

    root['_position'] = 0
    walk(root, None)
    return rows


# --------------------------------------------------------------------------
# Migration from contexts + pairwise relationships
# --------------------------------------------------------------------------

def migrate_from_relationships(
    measurements: List[Dict],
    groups: List[Dict],
    relationships: List[Dict],
    new_id,
) -> Tuple[Dict, bool, Dict[str, Tuple[str, str]]]:
    """
    Build an equivalent model tree from the contexts + relationships
    representation, so saved analyses keep working.

    Returns (root, linearized, group_boundaries). `linearized` is True when the
    relationship graph could not be expressed as a tree and had to be flattened
    into one order; callers should surface that, since a linearization is
    strictly MORE constrained than the graph it came from. `group_boundaries`
    maps each context id to the (start, end) boundaries now bracketing it, so
    results can still be reported per context.
    """
    from app.services.stratigraphy import expand_relationships, group_members

    members = group_members(measurements, groups)
    group_ids = [g['id'] for g in groups]

    # Edges are between individual samples; lift them to their contexts.
    older_younger, _equal = expand_relationships(measurements, groups, relationships)
    context_of = {}
    for gid, member_ids in members.items():
        for mid in member_ids:
            context_of[mid] = gid

    context_edges: Set[Tuple[str, str]] = set()
    for older, younger in older_younger:
        a, b = context_of.get(older), context_of.get(younger)
        if a and b and a != b:
            context_edges.add((a, b))

    # Topological order, oldest first.
    indeg = {gid: 0 for gid in group_ids}
    adj = defaultdict(list)
    for a, b in context_edges:
        adj[a].append(b)
        indeg[b] = indeg.get(b, 0) + 1

    ordered: List[str] = []
    queue = sorted([g for g in group_ids if indeg.get(g, 0) == 0])
    while queue:
        node = queue.pop(0)
        ordered.append(node)
        for nxt in sorted(adj[node]):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    # Anything left was in a cycle; append it so no context is silently lost.
    leftover = [g for g in group_ids if g not in ordered]
    ordered.extend(leftover)

    # Was the ordering forced, or an arbitrary linearization of a branching
    # set? Consecutive contexts not related by an edge mean constraints were
    # added that the original did not have.
    linearized = bool(leftover) or any(
        (ordered[i], ordered[i + 1]) not in context_edges for i in range(len(ordered) - 1)
    )

    groups_by_id = {g['id']: g for g in groups}
    start_id = new_id()
    children: List[Dict] = [{
        'id': start_id, 'element_type': 'boundary', 'name': 'Start of sequence',
        'tpq_age_bp': None, 'taq_age_bp': None, 'children': [],
    }]
    group_boundaries: Dict[str, Tuple[str, str]] = {}
    previous_boundary = start_id
    for gid in ordered:
        group = groups_by_id.get(gid, {})
        children.append({
            'id': gid,
            'element_type': 'phase',
            'name': group.get('name') or 'Context',
            'tpq_age_bp': group.get('tpq_age_bp'),
            'taq_age_bp': group.get('taq_age_bp'),
            'children': [],
        })
        end_id = new_id()
        children.append({
            'id': end_id, 'element_type': 'boundary',
            'name': f"End of {group.get('name') or 'context'}",
            'tpq_age_bp': None, 'taq_age_bp': None, 'children': [],
        })
        group_boundaries[gid] = (previous_boundary, end_id)
        previous_boundary = end_id
    if len(children) > 1:
        children[-1]['name'] = 'End of sequence'

    root = {
        'id': new_id(),
        'element_type': 'sequence',
        'name': 'Site sequence',
        'tpq_age_bp': None,
        'taq_age_bp': None,
        'children': children,
    }
    return root, linearized, group_boundaries
