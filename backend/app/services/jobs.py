"""
Work that runs outside the request that asked for it.

Why this exists. A Bayesian run takes 12.7 s for eight dates and 43.4 s for
sixty, and about five minutes for a model that runs to the sweep cap; "Re-run
All" is that once per analysis in the project. Held open as an HTTP request
that is past any reverse proxy's default timeout, and it gives the person who
pressed the button a spinner with no progress, no estimate and no way out.
docs/ASYNC_JOBS_PLAN.md has the measurements.

Why a thread pool rather than Celery or RQ. A broker buys two things: work
that survives a restart, and several runs at once. This app has no
authentication and is documented as single-user local, so the first is worth
a startup reconciliation rather than a Redis container (see reconcile below),
and the second is not wanted at all -- see max_workers. The seam is
`submit(kind, target_id, work)`, and a distributed backend would replace this
module without touching a caller.

Why ONE worker. gibbs's sweep loop is pure Python, so it holds the GIL: two
concurrent runs would each take roughly twice as long, and neither would
finish sooner than running them in order. One worker also gives a real queue
-- "N ahead of you" is a fact rather than an estimate -- and the number is
the only thing that would change if the solver ever ran outside the
interpreter.

Sessions. A job runs long after `get_db` closed the request's session, so
each one opens its own and closes it in a finally. The work function is
handed that session; it must not close over the request's.
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

# One at a time. See the module docstring.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='radical-job')

# Cancellation is in-memory because the process that accepts the cancel is
# the process running the job -- there is only one. It has to be shared
# state rather than a database flag read in a loop: the worker checks it
# after every round, and a SELECT per round would cost more than the round.
_cancelled: set = set()
_cancel_lock = threading.Lock()

# How often a running job is allowed to write its progress back. The sampler
# reports after every round, which on a long run is a few hundred times; the
# UI polls once a second and cannot see finer than that anyway.
_PROGRESS_INTERVAL_SECONDS = 0.5


class Cancelled(Exception):
    """Raised inside a job when someone asked it to stop. Not an error."""


class JobHandle:
    """
    What a running job uses to say how it is getting on, and to find out
    whether it should stop.

    Passed to the work function rather than reached for globally, so a work
    function is a plain callable and can be tested without a job at all --
    pass it a handle that reports nowhere.
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
        Record how far along this job is. Reports only -- it never raises, so
        a caller deciding whether to stop reads `cancelled` and decides for
        itself. The sampler's contract is to return False from its callback;
        a loop with no such contract calls check_cancelled instead.

        Throttled to _PROGRESS_INTERVAL_SECONDS, because the sampler reports
        far more often than anyone reads. `force` writes regardless, for the
        transitions that matter (a project rerun finishing an analysis) as
        opposed to the ones that merely tick.
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
            # picture, and a merge would leave a stale key behind when a
            # phase stops reporting one.
            row.detail = detail
        self._db.commit()


class NullHandle:
    """
    A handle that reports nowhere and is never cancelled.

    For running a work function synchronously -- the ?wait=true paths, and a
    test exercising the work itself rather than the queue around it. It is
    what makes a work function a plain callable rather than something that
    only runs inside a job.
    """
    job_id = None
    cancelled = False

    def check_cancelled(self) -> None:
        pass

    def update(self, *args, **kwargs) -> None:
        pass


def submit(kind: str, target_id: Optional[str],
           work: Callable[[Session, JobHandle], Optional[Dict]]) -> str:
    """
    Queue `work` and return the id of the job that will run it.

    `work` is called with a fresh Session and a JobHandle. Whatever it
    returns is stored as the job's final `detail`, so a job can report a
    summary the progress updates never carried. Raising marks the job
    failed with the exception's message; raising Cancelled marks it
    cancelled, which is not a failure.

    The row is committed BEFORE the work is queued, so the id this returns
    is always readable by the poll that follows it. Queuing first would
    race: a very short job can finish before its row exists.
    """
    with SessionLocal() as db:
        row = Job(kind=kind, target_id=target_id, state='queued')
        db.add(row)
        db.commit()
        job_id = row.id

    _executor.submit(_run, job_id, work)
    return job_id


def _run(job_id: str, work: Callable[[Session, JobHandle], Optional[Dict]]) -> None:
    """The worker thread's whole life. Never raises: a job records its own
    failure, and an exception escaping here would be swallowed by the
    executor and lost."""
    with SessionLocal() as db:
        row = db.query(Job).filter(Job.id == job_id).first()
        if row is None:
            return
        with _cancel_lock:
            already_cancelled = job_id in _cancelled
        if already_cancelled:
            # Cancelled while queued: it never starts, and started_at stays
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
            print(f"Job {job_id} ({row.kind}) failed: {exc}")
            print(traceback.format_exc())
            row = db.query(Job).filter(Job.id == job_id).first()
            row.state = 'failed'
            row.error_message = str(exc)
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

    Cooperative: a queued job is dropped before it starts, and a running one
    stops at its next checkpoint (for a sampled model, the end of the current
    round). Work already finished is not undone -- but a cancelled analysis
    run writes nothing, so the analysis keeps whatever results it had.
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
    """The most recent job about this analysis or project, if any. What the
    results page asks for when it arrives holding an id and no job id."""
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

    Nothing survives the process that was running it, so a row still reading
    'queued' or 'running' at startup describes work that will never finish.
    Left alone it would be polled forever by a page that has no way to know
    the difference. An Analysis stuck at 'running' is corrected the same way
    -- it is what the results page shows, and it is equally untrue.

    This is what stands in for a broker's durability. It does not save the
    work; it stops the app lying about it.
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
