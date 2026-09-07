"""
Reading and cancelling background work.

A run is queued rather than held open in the request that asked for it (see
services/jobs.py and docs/ASYNC_JOBS_PLAN.md); this is how a client finds out
what happened to it.

Polling rather than a push channel: a poll a second is finer than anyone reads
a progress bar, and a WebSocket would add a connection to hold open and a
reconnect to get right for the same three facts.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.auth import current_user, owned_or_404
from app.database import get_db
from app.models import Job, User
from app.services import jobs

router = APIRouter()


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """
    One job's state, progress and detail.

    `state` is 'queued', 'running', 'completed', 'failed' or 'cancelled'.
    Stop polling on the last three.

    `progress` is a fraction, and for a sampled model an UPPER BOUND: the
    sampler stops as soon as it converges, so most runs succeed well short of
    1.0. `detail.convergence` says how well, and is the figure to show beside
    a bar about to jump from 0.3 to done.
    """
    row = owned_or_404(
        db.query(Job).filter(Job.id == job_id).first(), user, 'That job')
    return {"success": True, "data": jobs.as_dict(row)}


@router.get("/jobs")
def list_jobs(
    target_id: Optional[str] = Query(None, description="Analysis or project id"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """
    Recent jobs, newest first, optionally about one analysis or project.

    `target_id` is what a bookmarked page uses: it has an analysis id and no
    job id, and still needs to know whether something is running.
    """
    # Filtered rather than checked per row: a job carries its owner, and
    # filtering in the query is what stops a new job kind leaking by someone
    # forgetting a check inside the loop below.
    query = db.query(Job).filter(Job.owner_id == user.id)
    if target_id:
        query = query.filter(Job.target_id == target_id)
    rows = query.order_by(Job.created_at.desc()).limit(limit).all()
    return {"success": True, "data": {"jobs": [jobs.as_dict(r) for r in rows]}}


@router.delete("/jobs/{job_id}")
def cancel_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """
    Ask a job to stop.

    Cooperative and so not instant: a queued job is dropped before it starts,
    a running one stops at its next checkpoint. The response says the request
    was accepted, not that the job stopped -- poll for `state` to become
    'cancelled'. A cancelled run writes nothing, so the analysis keeps the
    results it had: a stop, not an undo.
    """
    row = owned_or_404(
        db.query(Job).filter(Job.id == job_id).first(), user, 'That job')
    if row.state not in ('queued', 'running'):
        raise HTTPException(
            status_code=409,
            detail=f"This job has already {row.state}; there is nothing to cancel.",
        )
    jobs.cancel(job_id)
    return {"success": True, "data": {"job_id": job_id, "cancellation_requested": True}}
