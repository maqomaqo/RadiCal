//! Work that runs outside the request that asked for it. Port of
//! `services/jobs.py` and `services/failures.py`.
//!
//! The seam is the backend's: [`Jobs::submit`]`(kind, target_id, work)`
//! commits a `jobs` row, queues the work, and returns the id a poll reads.
//! The work gets a [`Handle`] to report progress (throttled to
//! [`PROGRESS_INTERVAL`]) and to check, between units of work, whether it
//! should stop. Cancelling is cooperative and in memory, since the process
//! accepting the cancel is the one running the job.
//!
//! The backend ran one job at a time because its sampler held the GIL; here
//! the workers are the machine's cores less one, so a long run does not hold
//! up a short one. Jobs about the SAME analysis or project still run one
//! after another, in the order submitted: two re-runs of one analysis
//! overlapping would interleave their writes, which one worker ruled out. Nothing survives the process, so [`reconcile_orphans`]
//! marks anything left mid-flight as failed at startup rather than letting
//! it be polled forever.

use std::collections::{HashMap, HashSet, VecDeque};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::mpsc::{Receiver, Sender, channel};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use radical_store::rows::{self, Analysis, Job};
use radical_store::{Store, Timestamp};
use serde_json::{Value, json};

/// How often a running job may write its progress back.
pub const PROGRESS_INTERVAL: Duration = Duration::from_millis(500);

/// What [`reconcile_orphans`] says of work a restart interrupted.
pub const INTERRUPTED: &str = "Interrupted by a server restart.";

// --------------------------------------------------------------------------
// Failures
// --------------------------------------------------------------------------

/// Why a job's work did not finish.
#[derive(Debug)]
pub enum JobError {
    /// Someone asked it to stop. Not an error.
    Cancelled,
    /// A refusal from the calibration maths, with its written sentence.
    Core(radical_core::Error),
    /// A refusal the work wrote itself: a model or curve problem the analyst
    /// can act on.
    Refused { curve: bool, message: String },
    /// Anything else: a fault in the app, not in the model.
    Internal(String),
}

impl From<radical_core::Error> for JobError {
    fn from(e: radical_core::Error) -> Self {
        Self::Core(e)
    }
}

impl From<radical_store::Error> for JobError {
    fn from(e: radical_store::Error) -> Self {
        Self::Internal(format!("database: {e}"))
    }
}

/// A classified failure, ready to store and to show
/// (`failures.Failure`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Failure {
    /// 'curve', 'model', 'cancelled' or 'internal'.
    pub category: &'static str,
    /// What to show: always a complete sentence.
    pub message: String,
    /// The original text when it was NOT shown, for the log.
    pub detail: Option<String>,
}

/// Whether a core error is about the curve the run was given, whose remedy
/// is choosing another curve rather than changing the model.
fn is_curve_error(e: &radical_core::Error) -> bool {
    use radical_core::Error as E;
    matches!(
        e,
        E::EmptyCurve
            | E::NoCurveData
            | E::UnknownCurve(_)
            | E::MixProportion
            | E::MixProportionError
            | E::MixEmptyCurve
            | E::MixNoOverlap
    )
}

/// Core errors that are a library's or the interpreter's in the Python, not
/// a sentence the app wrote: shown as internal.
fn is_internal(e: &radical_core::Error) -> bool {
    matches!(
        e,
        radical_core::Error::Spline(_) | radical_core::Error::ZeroStride
    )
}

/// Tell the app's deliberate refusals, kept in their own words, from
/// faults, said plainly to be the app's (`failures.classify`).
pub fn classify(error: &JobError) -> Failure {
    let curve = |raw: &str| Failure {
        category: "curve",
        message: format!(
            "The run could not use the calibration curve it was given: {raw} Choosing a different curve, or restoring that one, should let it run."
        ),
        detail: None,
    };
    let model = |raw: &str| Failure {
        category: "model",
        message: format!(
            "The model could not be solved: {raw} This is about the model as specified, so changing the structure, the limiters or the dates is what will move it."
        ),
        detail: None,
    };
    let internal = |detail: String| {
        Failure {
        category: "internal",
        message: "The run stopped because of an unexpected error inside the app, not because of anything about this model. Nothing was saved over, so the analysis still holds whatever results it had. The server log has the details; re-running may simply work.".to_owned(),
        detail: Some(detail),
    }
    };
    match error {
        JobError::Cancelled => Failure {
            category: "cancelled",
            message: "The run was stopped before it finished.".to_owned(),
            detail: None,
        },
        JobError::Core(e) if is_internal(e) => internal(e.to_string()),
        JobError::Core(e) => {
            let raw = e.to_string();
            let raw = raw.trim();
            if raw.is_empty() {
                internal(format!("{e:?}"))
            } else if is_curve_error(e) {
                curve(raw)
            } else {
                model(raw)
            }
        }
        JobError::Refused {
            curve: true,
            message,
        } => curve(message.trim()),
        JobError::Refused {
            curve: false,
            message,
        } => model(message.trim()),
        JobError::Internal(detail) => internal(detail.clone()),
    }
}

// --------------------------------------------------------------------------
// The handle a job reports through
// --------------------------------------------------------------------------

type CancelSet = Arc<Mutex<HashSet<String>>>;

/// A queued job: its id, what it is about, and its work.
type Queued = (String, Option<String>, Work);

/// Targets with a job running, each with the jobs about it waiting their
/// turn.
type Busy = Arc<Mutex<HashMap<String, VecDeque<Queued>>>>;

/// What a running job uses to say how it is getting on, and to find out
/// whether it should stop.
pub struct Handle {
    job_id: Option<String>,
    store: Option<Arc<Store>>,
    cancelled: CancelSet,
    last_write: Mutex<Option<Instant>>,
}

impl Handle {
    /// A handle that reports nowhere and is never cancelled, for running work
    /// synchronously (`NullHandle`).
    pub fn null() -> Self {
        Self {
            job_id: None,
            store: None,
            cancelled: CancelSet::default(),
            last_write: Mutex::new(None),
        }
    }

    pub fn job_id(&self) -> Option<&str> {
        self.job_id.as_deref()
    }

    pub fn cancelled(&self) -> bool {
        self.job_id.as_ref().is_some_and(|id| {
            self.cancelled
                .lock()
                .unwrap_or_else(|p| p.into_inner())
                .contains(id)
        })
    }

    /// Err(Cancelled) if a cancel has been requested. Call between units of
    /// work.
    pub fn check_cancelled(&self) -> Result<(), JobError> {
        if self.cancelled() {
            Err(JobError::Cancelled)
        } else {
            Ok(())
        }
    }

    /// Record how far along the job is, at most every [`PROGRESS_INTERVAL`]
    /// unless `force`. `detail`, when given, replaces the last one whole.
    /// Reports only, and never fails the job.
    pub fn update(&self, progress: Option<f64>, detail: Option<Value>, force: bool) {
        let (Some(id), Some(store)) = (&self.job_id, &self.store) else {
            return;
        };
        {
            let mut last = self.last_write.lock().unwrap_or_else(|p| p.into_inner());
            let now = Instant::now();
            if !force && last.is_some_and(|t| now.duration_since(t) < PROGRESS_INTERVAL) {
                return;
            }
            *last = Some(now);
        }
        let _ = store.write(|tx| {
            let Some(mut row) = rows::get::<Job>(tx, id)? else {
                return Ok(());
            };
            if let Some(p) = progress {
                row.progress = Some(p.clamp(0.0, 1.0));
            }
            if let Some(d) = detail {
                row.detail = Some(d);
            }
            rows::update(tx, &row)?;
            Ok(())
        });
    }
}

// --------------------------------------------------------------------------
// The queue
// --------------------------------------------------------------------------

/// A job's work: given its handle, it returns what becomes the job's final
/// `detail`, or why it did not finish.
pub type Work = Box<dyn FnOnce(&Handle) -> Result<Option<Value>, JobError> + Send + 'static>;

/// The queue and its workers.
pub struct Jobs {
    store: Arc<Store>,
    cancelled: CancelSet,
    sender: Option<Sender<Queued>>,
    workers: Vec<JoinHandle<()>>,
}

/// The machine's cores less one, at least one.
pub fn default_workers() -> usize {
    std::thread::available_parallelism().map_or(1, |n| n.get().saturating_sub(1).max(1))
}

impl Jobs {
    /// Start `workers` worker threads over `store`.
    pub fn start(store: Arc<Store>, workers: usize) -> Self {
        let (sender, receiver) = channel::<Queued>();
        let receiver = Arc::new(Mutex::new(receiver));
        let cancelled = CancelSet::default();
        let busy = Busy::default();
        let workers = (0..workers.max(1))
            .map(|i| {
                let receiver: Arc<Mutex<Receiver<Queued>>> = Arc::clone(&receiver);
                let store = Arc::clone(&store);
                let cancelled = Arc::clone(&cancelled);
                let busy = Arc::clone(&busy);
                std::thread::Builder::new()
                    .name(format!("radical-job-{i}"))
                    .spawn(move || {
                        loop {
                            let next = receiver.lock().unwrap_or_else(|p| p.into_inner()).recv();
                            let Ok((job_id, target, work)) = next else {
                                return;
                            };
                            let Some(target) = target else {
                                run(&store, &cancelled, &job_id, work);
                                continue;
                            };
                            {
                                let mut busy = busy.lock().unwrap_or_else(|p| p.into_inner());
                                if let Some(waiting) = busy.get_mut(&target) {
                                    // Its target is in use: it runs after the
                                    // jobs ahead of it, on the worker running
                                    // them.
                                    waiting.push_back((job_id, Some(target), work));
                                    continue;
                                }
                                busy.insert(target.clone(), VecDeque::new());
                            }
                            let mut current = (job_id, work);
                            loop {
                                run(&store, &cancelled, &current.0, current.1);
                                let mut busy = busy.lock().unwrap_or_else(|p| p.into_inner());
                                match busy.get_mut(&target).and_then(VecDeque::pop_front) {
                                    Some((id, _, work)) => current = (id, work),
                                    None => {
                                        busy.remove(&target);
                                        break;
                                    }
                                }
                            }
                        }
                    })
                    .expect("a worker thread starts")
            })
            .collect();
        Self {
            store,
            cancelled,
            sender: Some(sender),
            workers,
        }
    }

    /// Queue `work` and return the id of the job that will run it. The row
    /// is committed BEFORE the work is queued, so the id is always readable
    /// by the poll that follows.
    pub fn submit(
        &self,
        kind: &str,
        target_id: Option<&str>,
        owner_id: Option<&str>,
        work: Work,
    ) -> radical_store::Result<String> {
        let row = Job {
            id: radical_store::new_id(),
            kind: kind.to_owned(),
            target_id: target_id.map(str::to_owned),
            state: "queued".to_owned(),
            created_at: Some(Timestamp::now()),
            // Whose it is, on a server: another account's job is a 404.
            owner_id: owner_id.map(str::to_owned),
            ..Job::default()
        };
        self.store.write(|tx| rows::insert(tx, &row))?;
        if let Some(sender) = &self.sender {
            // The workers outlive every sender but this one, so this only
            // fails during shutdown, when the job stays queued and the next
            // start reconciles it.
            let _ = sender.send((row.id.clone(), row.target_id.clone(), work));
        }
        Ok(row.id)
    }

    /// Ask a job to stop: true if it was queued or running. A queued job is
    /// dropped before it starts; a running one stops at its next checkpoint.
    pub fn cancel(&self, job_id: &str) -> radical_store::Result<bool> {
        let row: Option<Job> = self.store.read(|c| rows::get(c, job_id))?;
        if !row.is_some_and(|r| r.state == "queued" || r.state == "running") {
            return Ok(false);
        }
        self.cancelled
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .insert(job_id.to_owned());
        Ok(true)
    }

    /// Stop taking work and wait for what is running to finish.
    pub fn shutdown(mut self) {
        self.sender.take();
        for worker in self.workers.drain(..) {
            let _ = worker.join();
        }
    }
}

/// A worker's handling of one job. Never panics out: a job records its own
/// failure.
fn run(store: &Arc<Store>, cancelled: &CancelSet, job_id: &str, work: Work) {
    let forget = || {
        cancelled
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .remove(job_id);
    };
    let finish = |state: &str, error: Option<String>, detail: Option<Value>| {
        let _ = store.write(|tx| {
            let Some(mut row) = rows::get::<Job>(tx, job_id)? else {
                return Ok(());
            };
            row.state = state.to_owned();
            row.finished_at = Some(Timestamp::now());
            if state == "completed" {
                row.progress = Some(1.0);
            }
            if error.is_some() {
                row.error_message = error.clone();
            }
            if detail.is_some() {
                row.detail = detail.clone();
            }
            rows::update(tx, &row)?;
            Ok(())
        });
    };
    let already = cancelled
        .lock()
        .unwrap_or_else(|p| p.into_inner())
        .contains(job_id);
    if already {
        // Cancelled while queued: it never starts, and started_at stays null
        // to say so.
        finish("cancelled", None, None);
        forget();
        return;
    }
    let started = store.write(|tx| {
        let Some(mut row) = rows::get::<Job>(tx, job_id)? else {
            return Ok(None);
        };
        row.state = "running".to_owned();
        row.started_at = Some(Timestamp::now());
        rows::update(tx, &row)?;
        Ok(Some(row.kind))
    });
    let Ok(Some(kind)) = started else {
        forget();
        return;
    };
    let handle = Handle {
        job_id: Some(job_id.to_owned()),
        store: Some(Arc::clone(store)),
        cancelled: Arc::clone(cancelled),
        last_write: Mutex::new(None),
    };
    let outcome = catch_unwind(AssertUnwindSafe(|| work(&handle))).unwrap_or_else(|panic| {
        let text = panic
            .downcast_ref::<String>()
            .cloned()
            .or_else(|| panic.downcast_ref::<&str>().map(|s| (*s).to_owned()))
            .unwrap_or_else(|| "a panic with no message".to_owned());
        Err(JobError::Internal(format!("panic: {text}")))
    });
    match outcome {
        Ok(summary) => finish("completed", None, summary),
        Err(JobError::Cancelled) => finish("cancelled", None, None),
        Err(error) => {
            let failure = classify(&error);
            // The classified message is what the user sees; the log keeps the
            // original.
            eprintln!(
                "Job {job_id} ({kind}) failed [{}]: {}",
                failure.category,
                failure.detail.as_deref().unwrap_or(&failure.message)
            );
            finish("failed", Some(failure.message), None);
        }
    }
    forget();
}

/// The most recent job about this analysis or project.
pub fn latest_for(store: &Store, target_id: &str) -> radical_store::Result<Option<Job>> {
    let rows: Vec<Job> = store
        .read(|c| rows::select_where(c, "target_id = ?", [target_id], "created_at DESC LIMIT 1"))?;
    Ok(rows.into_iter().next())
}

/// A job as the API returns it (`jobs.as_dict`).
pub fn as_json(row: &Job) -> Value {
    let when = |t: &Option<Timestamp>| t.map(|t| t.isoformat());
    json!({
        "job_id": row.id,
        "kind": row.kind,
        "target_id": row.target_id,
        "state": row.state,
        "progress": row.progress,
        "detail": row.detail.clone().unwrap_or(Value::Null),
        "error_message": row.error_message,
        "created_at": when(&row.created_at),
        "started_at": when(&row.started_at),
        "finished_at": when(&row.finished_at),
    })
}

/// Fail anything left mid-flight by a restart, and any analysis stranded at
/// 'running'; how many jobs. It does not save the work, it stops the app
/// lying about it.
pub fn reconcile_orphans(store: &Store) -> radical_store::Result<usize> {
    store.write(|tx| {
        let now = Timestamp::now();
        let jobs: Vec<Job> = rows::select_where(tx, "state IN ('queued', 'running')", [], "")?;
        for mut row in jobs.clone() {
            row.state = "failed".to_owned();
            row.error_message = Some(INTERRUPTED.to_owned());
            row.finished_at = Some(now);
            rows::update(tx, &row)?;
        }
        let stranded: Vec<Analysis> = rows::select_where(tx, "status = 'running'", [], "")?;
        for mut analysis in stranded {
            analysis.status = Some("failed".to_owned());
            analysis.error_message = Some(INTERRUPTED.to_owned());
            rows::update(tx, &analysis)?;
        }
        Ok(jobs.len())
    })
}

#[cfg(test)]
mod tests;
