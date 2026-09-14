"""
The marine reservoir snapshots this app has, and how one gets here.

Three rules, enforced rather than merely documented:

  **A snapshot's numbers never change.** There is no PATCH on this router and
  there will not be one. Correcting a reservoir table means ingesting a new
  snapshot; an analysis run last month has to keep meaning what it said.

  **A snapshot in use cannot be deleted.** The refusal says how many dates
  cite it. Unlike a stored curve there is no archive flag to fall back on,
  because there are no pickers for a snapshot to clutter -- the lookup names
  one and that is that.

  **Ingesting requires an account, and the result belongs to it.** A snapshot
  someone pulled down this morning has nobody vouching for it, which is the
  argument api/curves.py already makes about an imported curve.

The ingest is the only code in this app that talks to the internet, and it
runs when a person asks. Never on a timer, never on startup, never as a side
effect of a lookup. It issues ONE query against a courtesy interface on
somebody else's Apache/PHP host -- see services/reservoir_ingest.py.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.auth import current_user
from app.models import MarineReservoirSnapshot, User
from app.services import reservoir_registry
from app.services import reservoir_ingest

router = APIRouter()


@router.get("/reservoir/snapshots")
def list_snapshots(user: User = Depends(current_user)):
    """
    Every snapshot this account may use: the bundled ones, plus its own.

    Someone else's ingested snapshot is not listed and not resolvable, exactly
    as their imported curves are not. The bundled snapshot belongs to the
    install and is listed for everyone.
    """
    specs = reservoir_registry.list_specs(owner_id=user.id)
    return {
        "success": True,
        "data": {
            "items": [spec.as_dict() for spec in specs],
            # Said out loud because it changes what the lookup can do at all.
            # Until op 0 of the plan is answered there is no bundled snapshot
            # to ship, so a fresh install has nothing until someone ingests.
            "has_bundled": any(s.kind == 'bundled' for s in specs),
        },
    }


@router.get("/reservoir/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: str, user: User = Depends(current_user)):
    """
    One snapshot's metadata, and whether it still verifies.

    The verification is done here rather than reported from the row because a
    row can only say what the hash was when it was written. Loading is what
    checks it, and someone looking at this page is usually asking exactly
    that question.
    """
    if not reservoir_registry.visible_to(snapshot_id, owner_id=user.id):
        # A 404 rather than a 403, as everywhere else: an id must not be
        # usable to discover that someone else's snapshot exists.
        raise HTTPException(status_code=404, detail="That snapshot was not found")

    spec = reservoir_registry.get_spec(snapshot_id)
    payload = spec.as_dict()
    try:
        rows = reservoir_registry.resolve(snapshot_id)
        payload['verified'] = True
        payload['loaded_count'] = len(rows)
    except reservoir_registry.SnapshotCorrupt as exc:
        payload['verified'] = False
        payload['problem'] = str(exc)
    except reservoir_registry.SnapshotNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    payload['in_use_by'] = reservoir_registry.in_use(snapshot_id)
    return {"success": True, "data": payload}


@router.post("/reservoir/snapshots")
def create_snapshot(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """
    Fetch the marine reservoir database and store what comes back.

    All-or-nothing: a fetch that does not parse cleanly writes nothing, and
    the report lists every problem rather than the first. The report is the
    response whether or not it succeeded, because the counts in it -- rows
    with no uncertainty, cp1252 fallbacks, feeding spellings nobody has seen
    -- are what a person needs to judge the source, not just the fetch.

    503 when calib.org cannot be reached, which is a different thing from a
    response that will not parse: one is a network to try again on, the other
    is a source that has changed shape and needs a person to look at it.
    """
    try:
        records, declared = reservoir_ingest.fetch_records()
    except reservoir_ingest.IngestUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except reservoir_ingest.IngestError as exc:
        # The host answered, but with something that is not this database.
        raise HTTPException(status_code=502, detail=str(exc))

    rows, report = reservoir_ingest.records_to_rows(records, declared=declared)
    if not report.ok:
        # A 200 carrying the problems, as api/imports.py answers an unreadable
        # file: the caller asked a reasonable question and the answer is what
        # was wrong with the source.
        return {"success": False, "data": report.as_dict()}

    try:
        snapshot = reservoir_ingest.store(
            db, rows, report,
            source='calib.org/marine (CHRONO Marine20 Reservoir Database)',
            created_by=user.id,
            citation='Reimer and Reimer 2001, Radiocarbon 43:461-3',
        )
    except reservoir_ingest.IngestError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return {
        "success": True,
        "data": {
            "snapshot": reservoir_registry.get_spec(snapshot.id).as_dict(),
            "report": report.as_dict(),
        },
    }


@router.delete("/reservoir/snapshots/{snapshot_id}")
def delete_snapshot(
    snapshot_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """
    Remove an ingested snapshot, if nothing is citing it.

    A bundled snapshot cannot be deleted at all -- it is a file that ships
    with the app, and a route that appeared to delete one would be lying.
    """
    if not reservoir_registry.visible_to(snapshot_id, owner_id=user.id):
        raise HTTPException(status_code=404, detail="That snapshot was not found")

    row = (
        db.query(MarineReservoirSnapshot)
        .filter(MarineReservoirSnapshot.id == snapshot_id)
        .filter(MarineReservoirSnapshot.created_by == user.id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{snapshot_id}' ships with the app and cannot be deleted. "
                "It is a file rather than a row."
            ),
        )

    citing = reservoir_registry.in_use(snapshot_id)
    if citing:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{citing} date{'s' if citing != 1 else ''} record"
                f"{'' if citing != 1 else 's'} a ΔR derived from "
                f"'{snapshot_id}'. Deleting it would leave those dates citing "
                "a snapshot that no longer exists, and the offsets they "
                "carry could no longer be reproduced."
            ),
        )

    db.delete(row)
    db.commit()
    return {"success": True, "data": {"deleted_snapshot_id": snapshot_id}}
