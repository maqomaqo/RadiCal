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
"""
import itertools
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

ELEMENT_TYPES = ('sequence', 'phase', 'boundary', 'hiatus')

# A term carrying a calendar age in the model: either a dated sample or a
# boundary parameter. Ordering constraints are expressed over these.
Term = Tuple[str, str]  # ('date' | 'boundary', id)


class SpanGroup:
    """
    A set of dates enclosed by a consecutive pair of Boundary siblings, which
    together contribute the uniform-scatter density (older - younger)^(-K):
    conditional on its boundaries, each of the K dates is uniform between
    them (OxCal's Phase model).
    """

    def __init__(self, older_boundary_id: str, younger_boundary_id: str, date_ids: List[str]):
        self.older_boundary_id = older_boundary_id
        self.younger_boundary_id = younger_boundary_id
        self.date_ids = date_ids

    def __repr__(self):
        return (f"SpanGroup({self.older_boundary_id} -> {self.younger_boundary_id}, "
                f"{len(self.date_ids)} dates)")


class ModelPlan:
    """Everything a solver needs to turn a tree into a posterior."""

    def __init__(
        self,
        ordering: List[Tuple[List[Term], List[Term]]],
        span_groups: List[SpanGroup],
        marginalized_boundaries: Set[str],
        sampled_boundaries: List[str],
        boundary_names: Dict[str, str],
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


def validate_tree(root: Optional[Dict], measurements: List[Dict]) -> None:
    """
    Reject a structurally impossible model before any run, as
    BayesianRequest's validators do. Raises ValueError. No cycle check: a tree
    cannot contain one.
    """
    if root is None:
        raise ValueError("model tree is empty")
    if root.get('element_type') not in ('sequence', 'phase'):
        raise ValueError("the root element must be a sequence or a phase")

    seen_ids: Set[str] = set()
    element_types: Dict[str, str] = {}

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
        if etype in ('boundary', 'hiatus') and children:
            raise ValueError(f"a {etype} cannot contain other elements (element '{eid}')")

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

    # Every date must live in a phase.
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
        if element_types[element_id] != 'phase':
            raise ValueError(
                f"sample '{m.get('sample_name', m.get('id'))}' is attached to a "
                f"{element_types[element_id]}; dates belong to a phase"
            )


# --------------------------------------------------------------------------
# Tree -> model plan
# --------------------------------------------------------------------------

def _subtree_terms(element: Dict, dates_by_element: Dict[str, List[str]]) -> List[Term]:
    """Every age-bearing term in this subtree: its dates and its boundaries."""
    terms: List[Term] = []
    for node in iter_elements(element):
        if node.get('element_type') == 'boundary':
            terms.append(('boundary', node['id']))
        for date_id in dates_by_element.get(node['id'], []):
            terms.append(('date', date_id))
    return terms


def _plan_element(
    element: Dict,
    dates_by_element: Dict[str, List[str]],
    ordering: List[Tuple[List[Term], List[Term]]],
    span_groups: List[SpanGroup],
) -> List[str]:
    """
    Plan one element. Returns the ids of dates in this subtree not yet
    enclosed by any boundary pair, so they bubble up to an enclosing group --
    or, unclaimed, get no uniform-scatter term, matching OxCal, where a date
    outside every boundary is unconstrained by a phase prior.
    """
    etype = element.get('element_type')
    children = _children(element)

    if etype in ('boundary', 'hiatus'):
        return []

    # Plan children first so nested groups claim their own dates before an
    # outer group gets a chance to.
    unclaimed_by_child: List[List[str]] = [
        _plan_element(child, dates_by_element, ordering, span_groups) for child in children
    ]
    own_dates = list(dates_by_element.get(element['id'], []))

    if etype == 'phase':
        # Unordered: no constraints between children. Everything bubbles up.
        bubbled = list(own_dates)
        for child_unclaimed in unclaimed_by_child:
            bubbled.extend(child_unclaimed)
        return bubbled

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
        enclosed: List[str] = []
        for i in range(older_idx + 1, younger_idx):
            enclosed.extend(unclaimed_by_child[i])
        span_groups.append(SpanGroup(
            older_boundary_id=children[older_idx]['id'],
            younger_boundary_id=children[younger_idx]['id'],
            date_ids=enclosed,
        ))
        claimed.update(enclosed)

    bubbled = list(own_dates)
    for child_unclaimed in unclaimed_by_child:
        bubbled.extend(d for d in child_unclaimed if d not in claimed)
    return bubbled


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

    ordering: List[Tuple[List[Term], List[Term]]] = []
    span_groups: List[SpanGroup] = []
    _plan_element(root, dates_by_element, ordering, span_groups)

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
    )


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
        name = element.get('name') or (element.get('element_type') or 'Element').title()
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
