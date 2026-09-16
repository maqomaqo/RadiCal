"""
The one spatial model that needs no solver run.

Everything else in services/dispersal.py is a function of many modelled ages at
once, so it can only be computed inside a run, off joint draws (§3.2). A
spatial ORDER is not: it is a question about positions, and the answer is the
same before the model has been solved as after. That is exactly what makes it
useful while a model is still being written -- the proposal is offered in the
tree editor, where there is a draft and no results at all.

**Nothing here applies anything.** A spanning tree is spatial adjacency, not
chronology; asserting its order as a Sequence asserts that everything at one
site predates everything at the next, which is a strong claim the dates may
refute. The value of the proposal is that accepting it makes that claim
TESTABLE -- run the model, read the agreement indices, and the sites that do
not fit the spatial order announce themselves. So this endpoint returns an
order and the tree it was read off, and the person decides.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import current_user
from app.models import User
from app.schemas.sample import DispersalOrigin
from app.services import dispersal

router = APIRouter()


class ProposeSequenceRequest(BaseModel):
    """
    A draft model and its dates, as the tree editor holds them.

    The tree and measurements come from the request rather than from the
    database because this is offered on a DRAFT: there may be no saved
    analysis yet, and the whole point is to propose an order before anything
    has been run.
    """
    model_tree: Dict[str, Any]
    measurements: List[Dict[str, Any]] = Field(default_factory=list)
    # Where the front is hypothesised to have started. Optional: without one
    # the walk begins at an end of the tree's longest path, which is the
    # natural start of a linear arrangement and is found rather than assumed.
    origin: Optional[DispersalOrigin] = None


@router.post("/dispersal/propose-sequence")
def propose_sequence(request: ProposeSequenceRequest,
                     user: User = Depends(current_user)):
    """
    An order for the model's sites, read off a minimum spanning tree.

    A proposal, never an application. The response carries the order, the tree
    it was derived from, and a refusal in the user's own words when the model
    has nothing to order -- fewer than two placed sites, or a phase holding
    dates from two different places, which is a modelling error this refuses
    to average over rather than silently working around.
    """
    try:
        sites = dispersal.derive_sites(
            request.model_tree, request.measurements)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f'That model could not be read as a set of sites: {exc}')

    origin = ((request.origin.lat, request.origin.lon)
              if request.origin is not None else None)
    return {"success": True, "data": dispersal.propose_sequence(sites, origin)}
