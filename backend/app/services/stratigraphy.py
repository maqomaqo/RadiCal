"""
Turning samples, groups and relationships into the pairwise sample constraints
a Bayesian model needs, and catching stratigraphically impossible input before
it reaches a solver.

A relationship's endpoints can each name a sample or a group, per
from_type/to_type. This module expands a group reference into the full
cross-product of its members, so the rest of the codebase only deals with
sample-to-sample edges.
"""
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

# A directed edge (u, v) means "u must be strictly OLDER than v", i.e.
# age_u > age_v in cal BP. Both relationship types are normalised to this one
# direction so the rest of this module reasons about a single kind of edge.
Edge = Tuple[str, str]


def group_members(measurements: List[Dict], groups: List[Dict]) -> Dict[str, List[str]]:
    """Map each group id to the ids of its measurements. A group with no
    members maps to an empty list."""
    members: Dict[str, List[str]] = {g['id']: [] for g in groups}
    for m in measurements:
        gid = m.get('group_id')
        if gid:
            members.setdefault(gid, []).append(m['id'])
    return members


def expand_relationships(
    measurements: List[Dict],
    groups: List[Dict],
    relationships: List[Dict],
) -> Tuple[List[Edge], List[Edge]]:
    """
    Expand every relationship (which may reference a sample OR a group on
    either side) into plain sample-to-sample pairs.

    Returns (older_younger_edges, equal_pairs), both deduplicated: (u, v)
    meaning u is older than v, and (a, b) with a < b lexicographically.

    Direction follows the law of superposition: a sample stratigraphically
    ABOVE another was deposited later and is YOUNGER, one BELOW is OLDER.

    A relationship whose endpoint resolves to no samples -- an empty group --
    contributes no edges. That is a modelling no-op rather than an error;
    emptiness is warned about client-side.
    """
    members = group_members(measurements, groups)

    def resolve(node_id: str, node_type: str) -> List[str]:
        if node_type == 'group':
            return members.get(node_id, [])
        return [node_id]

    older_younger: set = set()
    equal: set = set()

    for r in relationships:
        rel = r['relationship']
        from_members = resolve(r['from_sample_id'], r.get('from_type', 'sample'))
        to_members = resolve(r['to_sample_id'], r.get('to_type', 'sample'))

        for a in from_members:
            for b in to_members:
                if a == b:
                    # A self-relationship, or a group holding both endpoints
                    # of its own. Skipped rather than made a self-cycle.
                    continue
                if rel == 'above':
                    # from is above to, so from is younger and to is older.
                    older_younger.add((b, a))
                elif rel == 'below':
                    # from is below to, so from is older.
                    older_younger.add((a, b))
                elif rel == 'equal':
                    equal.add((a, b) if a < b else (b, a))

    return list(older_younger), list(equal)


def find_cycle(edges: List[Edge]) -> Optional[List[str]]:
    """
    Detect a cycle in the "older than" graph by Kahn's topological sort.
    Returns the node ids left once everything with no remaining incoming edge
    is peeled away -- what is in or feeds into a cycle -- or None for a DAG.

    A cycle means no set of calendar ages can satisfy every constraint at once,
    which a solver would only surface as a failure or a hang minutes into a run.
    """
    if not edges:
        return None

    adj: Dict[str, List[str]] = defaultdict(list)
    indeg: Dict[str, int] = defaultdict(int)
    nodes: set = set()
    for u, v in edges:
        nodes.add(u)
        nodes.add(v)
        adj[u].append(v)
        indeg[v] += 1
        indeg.setdefault(u, indeg.get(u, 0))

    queue = deque(n for n in nodes if indeg.get(n, 0) == 0)
    visited = 0
    while queue:
        n = queue.popleft()
        visited += 1
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)

    if visited == len(nodes):
        return None

    return sorted(n for n in nodes if indeg.get(n, 0) > 0)


def effective_bounds(
    node: Dict,
    group: Optional[Dict],
) -> Tuple[Optional[float], Optional[float]]:
    """
    A sample's own TPQ/TAQ combined with its group's, tightest bound winning.
    Returns (taq_age_bp, tpq_age_bp) -- (min, max) -- either possibly None,
    meaning unbounded on that side.
    """
    taq_candidates = [v for v in (node.get('taq_age_bp'), group.get('taq_age_bp') if group else None) if v is not None]
    tpq_candidates = [v for v in (node.get('tpq_age_bp'), group.get('tpq_age_bp') if group else None) if v is not None]
    taq = max(taq_candidates) if taq_candidates else None  # tightest = largest minimum
    tpq = min(tpq_candidates) if tpq_candidates else None  # tightest = smallest maximum
    return taq, tpq
