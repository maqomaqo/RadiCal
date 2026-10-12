"""
Edits to a model tree that the app itself no longer makes, kept for tests
that compare a model with its neighbour.
"""
import itertools
from typing import Dict, List, Tuple

from app.services.model_tree import _children


def with_implicit_boundaries(root: Dict, new_id=None) -> Tuple[Dict, List[str]]:
    """
    A copy of `root` with a Boundary inserted between any two adjacent
    non-Boundary children of a Sequence, plus the ids of what was inserted.

    A test tool. The app never adds a Boundary to a model: the exact solver
    takes a model as written or refuses it (analysis._run_bayesian). The
    tests use this to build a model's near neighbour with its transitions
    written in -- on the fixture OxCal's `ordered` and `shared_boundary` runs
    agree to 0.948 posterior overlap -- to compare solvers on.

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
