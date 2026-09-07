"""
Work that runs outside the request that asked for it.

A Bayesian run takes 12.7 s for eight dates, 43.4 s for sixty, and about five
minutes at the sweep cap; "Re-run All" is that once per analysis in a project.
Held open as an HTTP request that is past any reverse proxy's default timeout.
docs/ASYNC_JOBS_PLAN.md has the measurements.

A thread pool rather than Celery or RQ, because a broker buys work that
survives a restart and several runs at once. This app is single-user and local,
so the first is worth a startup reconciliation rather than a Redis container
(see reconcile_orphans) and the second is not wanted at all. The seam is
`submit(kind, target_id, work)`, which a distributed backend could replace
without touching a caller.

ONE worker, because gibbs's sweep loop is pure Python and holds the GIL: two
concurrent runs would each take roughly twice as long and neither would finish
sooner than running them in order. It also gives a real queue, where "N ahead
of you" is a fact rather than an estimate.

Sessions: a job runs long after `get_db` closed the request's session, so each
opens its own and closes it in a finally. The work function is handed that
session and must not close over the request's.
"""
from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable, Dict, Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Job
from app.services import failures

# One at a time. See the module docstring.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='radical-job')

# In-memory because the process accepting the cancel is the one running the
# job. Shared state rather than a database flag: the worker checks after every
# round, and a SELECT per round would cost more than the round.
_cancelled: set = set()
_cancel_lock = threading.Lock()

# How often a running job may write its progress back. The sampler reports a
# few hundred times on a long run; the UI polls once a second.
_PROGRESS_INTERVAL_SECONDS = 0.5


class Cancelled(Exception):
    """Raised inside a job when someone asked it to stop. Not an error."""


class JobHandle:
    """
    What a running job uses to say how it is getting on, and to find out
    whether it should stop.

    Passed to the work function rather than reached for globally, so a work
    function stays a plain callable testable without a job (see NullHandle).
    """

    def __init__(self, job_id: str, db: Session):
        self.job_id = job_id
        self._db = db
        self._last_write = 0.0

    @property
    def cancelled(self) -> bool:
        with _cancel_lock:
            return self.job_id in _cancelled

    def check_cancelled(self) -> None:
        """Raise if a cancel has been requested. Call between units of work."""
        if self.cancelled:
            raise Cancelled()

    def update(self, progress: Optional[float] = None,
               force: bool = False, **detail) -> None:
        """
        Record how far along this job is. Reports only and never raises, so a
        caller deciding whether to stop reads `cancelled` itself.

        Throttled to _PROGRESS_INTERVAL_SECONDS, since the sampler reports far
        more often than anyone reads. `force` writes regardless, for the
        transitions that matter rather than the ones that merely tick.
        """
        now = time.monotonic()
        if not force and (now - self._last_write) < _PROGRESS_INTERVAL_SECONDS:
            return
        self._last_write = now
        row = self._db.query(Job).filter(Job.id == self.job_id).first()
        if row is None:
            return
        if progress is not None:
            row.progress = max(0.0, min(1.0, float(progress)))
        if detail:
            # Replaced rather than merged: each reporter sends its whole
            # picture, and a merge would leave stale keys behind.
            row.detail = detail
        self._db.commit()


class NullHandle:
    """
    A handle that reports nowhere and is never cancelled, for running a work
    function synchronously: the ?wait=true paths, and tests exercising the work
    rather than the queue around it.
    """
    job_id = None
    cancelled = False

    def check_cancelled(self) -> None:
        pass

    def update(self, *args, **kwargs) -> None:
        pass


def submit(kind: str, target_id: Optional[str],
           work: Callable[[Session, JobHandle], Optional[Dict]],
           owner_id: Optional[str] = None) -> str:
    """
    Queue `work` and return the id of the job that will run it.

    `work` is called with a fresh Session and a JobHandle. Whatever it returns
    becomes the job's final `detail`. Raising marks the job FAILED with a
    message classified by services/failures.py; raising Cancelled marks it
    cancelled, not failed.

    `owner_id` is who may poll and cancel it. Optional in the signature and
    supplied by every caller, because a job with no owner is one nobody can
    see -- which is the safe way round for a default, but never what a real
    caller wants.

    The row is committed BEFORE the work is queued, so the id returned is always
    readable by the poll that follows. Queuing first would race, since a short
    job can finish before its row exists.
    """
    with SessionLocal() as db:
        row = Job(kind=kind, target_id=target_id, state='queued', owner_id=owner_id)
        db.add(row)
        db.commit()
        job_id = row.id

    _executor.submit(_run, job_id, work)
    return job_id


def _run(job_id: str, work: Callable[[Session, JobHandle], Optional[Dict]]) -> None:
    """The worker thread's whole life. Never raises: a job records its own
    failure, and an exception escaping here would be swallowed by the
    executor."""
    with SessionLocal() as db:
        row = db.query(Job).filter(Job.id == job_id).first()
        if row is None:
            return
        with _cancel_lock:
            already_cancelled = job_id in _cancelled
        if already_cancelled:
            # Cancelled while queued, so it never starts and started_at stays
            # null to say so.
            row.state = 'cancelled'
            row.finished_at = datetime.utcnow()
            db.commit()
            _forget(job_id)
            return

        row.state = 'running'
        row.started_at = datetime.utcnow()
        db.commit()

        handle = JobHandle(job_id, db)
        try:
            summary = work(db, handle)
        except Cancelled:
            db.rollback()
            row = db.query(Job).filter(Job.id == job_id).first()
            row.state = 'cancelled'
            row.finished_at = datetime.utcnow()
            db.commit()
        except Exception as exc:
            db.rollback()
            # The classified message is what the user sees; the log keeps the
            # original, which is the half a bug report needs and the half the
            # message deliberately drops.
            failure = failures.classify(exc)
            print(f"Job {job_id} ({row.kind}) failed [{failure.category}]: "
                  f"{failure.detail or exc}")
            print(traceback.format_exc())
            row = db.query(Job).filter(Job.id == job_id).first()
            row.state = 'failed'
            row.error_message = failure.message
            row.finished_at = datetime.utcnow()
            db.commit()
        else:
            row = db.query(Job).filter(Job.id == job_id).first()
            row.state = 'completed'
            row.progress = 1.0
            row.finished_at = datetime.utcnow()
            if summary is not None:
                row.detail = summary
            db.commit()
        finally:
            _forget(job_id)


def cancel(job_id: str) -> bool:
    """
    Ask a job to stop. True if it was in a state where that means anything.

    Cooperative: a queued job is dropped before it starts, a running one stops
    at its next checkpoint. Finished work is not undone, but a cancelled
    analysis run writes nothing, so the analysis keeps the results it had.
    """
    with SessionLocal() as db:
        row = db.query(Job).filter(Job.id == job_id).first()
        if row is None or row.state not in ('queued', 'running'):
            return False
    with _cancel_lock:
        _cancelled.add(job_id)
    return True


def _forget(job_id: str) -> None:
    with _cancel_lock:
        _cancelled.discard(job_id)


def latest_for(target_id: str, db: Session) -> Optional[Job]:
    """The most recent job about this analysis or project, which is what the
    results page asks for when it arrives with an id and no job id."""
    return (db.query(Job)
            .filter(Job.target_id == target_id)
            .order_by(Job.created_at.desc())
            .first())


def as_dict(row: Job) -> Dict:
    return {
        'job_id': row.id,
        'kind': row.kind,
        'target_id': row.target_id,
        'state': row.state,
        'progress': row.progress,
        'detail': row.detail,
        'error_message': row.error_message,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'started_at': row.started_at.isoformat() if row.started_at else None,
        'finished_at': row.finished_at.isoformat() if row.finished_at else None,
    }


def reconcile_orphans() -> int:
    """
    Fail anything left mid-flight by a restart, and return how many.

    Nothing survives the process running it, so a row still reading 'queued' or
    'running' at startup describes work that will never finish, and would be
    polled forever. An Analysis stuck at 'running' is corrected the same way.

    This stands in for a broker's durability: it does not save the work, it
    stops the app lying about it.
    """
    from app.models import Analysis

    with SessionLocal() as db:
        jobs = db.query(Job).filter(Job.state.in_(('queued', 'running'))).all()
        for row in jobs:
            row.state = 'failed'
            row.error_message = 'Interrupted by a server restart.'
            row.finished_at = datetime.utcnow()

        stranded = db.query(Analysis).filter(Analysis.status == 'running').all()
        for analysis in stranded:
            analysis.status = 'failed'
            analysis.error_message = 'Interrupted by a server restart.'

        if jobs or stranded:
            db.commit()
        return len(jobs)
