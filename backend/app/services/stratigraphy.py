"""
Shared logic for turning a set of samples/groups/relationships into the
actual pairwise sample constraints a Bayesian model (or a validity check)
needs, and for detecting stratigraphically impossible input before it ever
reaches the MCMC sampler.

A relationship's from_sample_id/to_sample_id can each name either a single
sample or a group (a named phase containing zero or more samples), tagged
by from_type/to_type. This module expands any group reference into the
full cross-product of its member samples, so the rest of the codebase
(services/exact_chain.py, and the BayesianRequest validator in
schemas/sample.py) only ever has to deal with plain sample-to-sample
edges.
"""
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

# A directed edge (u, v) means "u must be strictly OLDER than v" -- i.e.
# age_u > age_v in cal BP. This is the same direction the solvers'
# 'above'/'below' Potentials already enforce; normalizing both relationship
# types to this one direction here means the rest of this module (and
# cycle detection) only has to reason about one kind of edge.
Edge = Tuple[str, str]


def group_members(measurements: List[Dict], groups: List[Dict]) -> Dict[str, List[str]]:
    """Map each group id to the ids of the measurements in it. Groups with
    no members yet (or that don't exist) map to an empty list."""
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

    Returns (older_younger_edges, equal_pairs):
      - older_younger_edges: deduplicated (u, v) pairs meaning u is older
        than v, from 'above'/'below' relationships.
      - equal_pairs: deduplicated (a, b) pairs (a < b lexicographically,
        just to dedupe consistently) from 'equal' relationships.

    Relationship direction follows the law of superposition: in an
    undisturbed sequence, a layer/sample that is stratigraphically ABOVE
    another one was deposited later and is therefore YOUNGER; a layer
    BELOW another is OLDER. So "from above to" means from is younger than
    to, and "from below to" means from is older than to.

    A relationship whose from/to id doesn't resolve to any samples (e.g.
    an empty group) contributes no edges -- that's a modeling no-op, not
    an error; emptiness is instead caught as a UX warning client-side.
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
                    # A group that contains both endpoints of its own
                    # relationship (or a self-relationship) -- not a
                    # meaningful constraint, skip rather than manufacture
                    # a trivial self-cycle.
                    continue
                if rel == 'above':
                    # Law of superposition: from is stratigraphically
                    # above to => from is younger => to is older.
                    older_younger.add((b, a))
                elif rel == 'below':
                    # from is stratigraphically below to => from is older.
                    older_younger.add((a, b))
                elif rel == 'equal':
                    equal.add((a, b) if a < b else (b, a))

    return list(older_younger), list(equal)


def find_cycle(edges: List[Edge]) -> Optional[List[str]]:
    """
    Detect a cycle in the "older than" directed graph using Kahn's
    topological sort. Returns the list of node ids left over once every
    node with no remaining incoming edge has been peeled away (everything
    that's in, or feeds into, a cycle) -- or None if the graph is a valid
    DAG.

    A cycle here (e.g. A above B, B above C, C above A) means no set of
    calendar ages can satisfy every constraint simultaneously: the
    sampler would see a -inf potential on every single draw and either
    fail outright or silently never converge. Catching this up front with
    a clear "these are mutually contradictory" error is far more useful
    than letting a multi-minute MCMC run fail unhelpfully.
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
    Combine a sample's own TPQ/TAQ with its group's (if any), tightest
    bound wins. Returns (taq_age_bp, tpq_age_bp) i.e. (min bound, max
    bound) -- either may be None, meaning unbounded on that side.
    """
    taq_candidates = [v for v in (node.get('taq_age_bp'), group.get('taq_age_bp') if group else None) if v is not None]
    tpq_candidates = [v for v in (node.get('tpq_age_bp'), group.get('tpq_age_bp') if group else None) if v is not None]
    taq = max(taq_candidates) if taq_candidates else None  # tightest = largest minimum
    tpq = min(tpq_candidates) if tpq_candidates else None  # tightest = smallest maximum
    return taq, tpq
