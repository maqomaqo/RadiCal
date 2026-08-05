"""
The explicit model structure -- OxCal's Sequence / Phase / Boundary / Hiatus
as a nested, ordered tree -- and the translation of that tree into the
pieces services/bayesian.py needs to build a PyMC model.

This replaces the previous flat arrangement (a list of Contexts plus
pairwise above/below relationships, with a start/end Boundary pair silently
auto-inserted around every Context). That arrangement could not express a
deliberately-placed Boundary, a site-wide Sequence bracketing every context,
or any nesting -- and it made every context's boundaries independent of its
neighbours', where OxCal's are a single shared transition.

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
    """Everything bayesian.py needs to turn a tree into a PyMC model."""

    def __init__(
        self,
        ordering: List[Tuple[List[Term], List[Term]]],
        span_groups: List[SpanGroup],
        marginalized_boundaries: Set[str],
        sampled_boundaries: List[str],
        boundary_names: Dict[str, str],
    ):
        # Each entry is (older_terms, younger_terms): every term on the left
        # must be older (larger cal BP) than every term on the right. Emitted
        # as one constraint per adjacent pair of sequence children rather than
        # the full pairwise cross-product -- min(older) >= max(younger) is
        # equivalent and far cheaper.
        self.ordering = ordering
        self.span_groups = span_groups
        # Boundaries integrated out analytically rather than sampled; see
        # classify_boundaries below for why this matters so much here.
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
    Reject a structurally impossible model before any MCMC run, in the same
    spirit as BayesianRequest's existing validators. Raises ValueError.

    Note there is no cycle check here: a tree cannot contain one, which
    removes a whole class of error the pairwise-relationship model had to
    guard against.
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
    Recursively plan one element. Returns the ids of dates in this subtree
    that are NOT yet enclosed by any boundary pair, so they can bubble up to
    be claimed by an enclosing group (or, if nothing claims them, simply get
    no uniform-scatter term -- matching OxCal, where boundaries are explicit
    and dates outside them are unconstrained by a phase prior).
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


def classify_boundaries(
    boundary_ids: List[str],
    span_groups: List[SpanGroup],
    ordering: List[Tuple[List[Term], List[Term]]],
) -> Tuple[Set[str], List[str]]:
    """
    Split boundaries into those that can be integrated out analytically and
    those that must be sampled. This is the crux of making explicit
    boundaries work at all, so it is worth spelling out.

    services/bayesian.py documents that pm.sample_smc CANNOT sample a
    boundary parameter: its posterior is heavy-tailed and SMC's IMH kernel
    collapses it onto the extreme dates (measured E[a+b] = 2.0 against a true
    150.9, silently, at r_hat 1.00). That is why the previous implementation
    marginalized boundaries out entirely. NUTS handles that case but was
    rejected for the multimodality that calibration-curve wiggles produce,
    which is intrinsic to radiocarbon.

    The way out is that only the OUTERMOST boundaries are heavy-tailed. An
    interior boundary is squeezed between the youngest date of the group
    before it and the oldest date of the group after it -- a bounded interval
    SMC handles perfectly well. An outermost boundary extends outward into a
    region containing no data at all, which is exactly where the heavy tail
    comes from -- and, appearing in only ONE span term, it also integrates
    out in closed form.

    "Outermost" is determined structurally, not positionally: a boundary is
    outermost-old if nothing in the model is constrained to be older than it,
    and outermost-young if nothing is constrained to be younger. That covers
    nested and branching models, where the extreme boundaries need not be the
    root sequence's first and last children.
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

    # A boundary can only be integrated out if it appears in exactly one span
    # term -- otherwise it couples two groups and the integral no longer
    # factorizes.
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
    The effective (taq, tpq) age limiters for every element, combining its own
    with every ancestor's -- tightest wins, the same rule
    stratigraphy.effective_bounds applies to a sample and its group, extended
    to an arbitrary depth of nesting. Returns (min bound, max bound) per
    element id; either may be None, meaning unbounded on that side.
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
# Flat rows <-> nested tree
# --------------------------------------------------------------------------

def rows_to_tree(rows: List) -> Optional[Dict]:
    """
    Rebuild the nested tree from flat ModelElement rows (parent_id +
    position). Returns None if there are no rows. Siblings come back in
    `position` order, i.e. oldest first.

    Any row whose parent_id doesn't resolve is attached to the root rather
    than dropped, so a partially-corrupt tree still round-trips visibly
    instead of silently losing elements.
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
    Flatten a nested tree into ModelElement rows ready to persist. Ids in the
    incoming tree are preserved, so a date's element_id keeps pointing at the
    right phase across an edit.
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
# Migration from the old contexts + pairwise-relationships model
# --------------------------------------------------------------------------

def migrate_from_relationships(
    measurements: List[Dict],
    groups: List[Dict],
    relationships: List[Dict],
    new_id,
) -> Tuple[Dict, bool]:
    """
    Build an equivalent model tree from the previous representation, so
    existing saved analyses keep working.

    Returns (root, linearized, group_boundaries). `linearized` is True when
    the old relationship graph could not be expressed as a tree and had to
    be flattened into a single order -- callers should surface that, because
    a linearization is strictly MORE constrained than the graph it came from
    and the user should check it. `group_boundaries` maps each old context id
    to the (start_boundary_id, end_boundary_id) now bracketing it, so results
    can still be reported against the old per-context start/end shape.

    The result reproduces the old behaviour (each context bracketed by
    boundaries) and adds the site-level start/end boundaries that were the
    point of this work.
    """
    from app.services.stratigraphy import expand_relationships, group_members

    members = group_members(measurements, groups)
    group_ids = [g['id'] for g in groups]

    # Old edges are between individual samples; lift them to their contexts.
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

    # Was the ordering forced, or is it an arbitrary linearization of an
    # unordered/branching set? If consecutive contexts aren't actually related
    # by an edge, we've added constraints that weren't in the original.
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
