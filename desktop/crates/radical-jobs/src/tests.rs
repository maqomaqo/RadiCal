//! The queue's behaviour, after the backend's test_jobs.py and
//! test_failures.py.

use std::sync::mpsc;

use super::*;

fn temp_store() -> (Arc<Store>, std::path::PathBuf) {
    let dir = std::env::temp_dir().join(format!("radical-jobs-{}", radical_store::new_id()));
    (Arc::new(Store::open(dir.join("radical.db")).unwrap()), dir)
}

fn job(store: &Store, id: &str) -> Job {
    store.read(|c| rows::get(c, id)).unwrap().unwrap()
}

/// Poll until the job has finished, as the UI does.
fn await_job(store: &Store, id: &str) -> Job {
    let deadline = Instant::now() + Duration::from_secs(20);
    loop {
        let row = job(store, id);
        if !matches!(row.state.as_str(), "queued" | "running") {
            return row;
        }
        assert!(Instant::now() < deadline, "job {id} never finished");
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn a_job_completes_with_its_summary() {
    let (store, dir) = temp_store();
    let jobs = Jobs::start(Arc::clone(&store), 2);
    let id = jobs
        .submit(
            "bayesian",
            Some("a1"),
            None,
            Box::new(|_| Ok(Some(json!({"dates": 3})))),
        )
        .unwrap();
    let row = await_job(&store, &id);
    assert_eq!(row.state, "completed");
    assert_eq!(row.progress, Some(1.0));
    assert_eq!(row.detail, Some(json!({"dates": 3})));
    assert!(row.started_at.unwrap() <= row.finished_at.unwrap());
    let shown = as_json(&row);
    assert_eq!(shown["job_id"], json!(id));
    assert_eq!(shown["target_id"], json!("a1"));
    jobs.shutdown();
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn a_failure_is_classified_and_a_panic_is_the_apps_fault() {
    let (store, dir) = temp_store();
    let jobs = Jobs::start(Arc::clone(&store), 1);
    let refused = jobs
        .submit(
            "bayesian",
            None,
            None,
            Box::new(|_| {
                Err(radical_core::Error::Model("Phase \"A\" has no dates.".into()).into())
            }),
        )
        .unwrap();
    let curve = jobs
        .submit(
            "calibrate",
            None,
            None,
            Box::new(|_| Err(radical_core::Error::UnknownCurve("x".into()).into())),
        )
        .unwrap();
    let panicked = jobs
        .submit(
            "bayesian",
            None,
            None,
            Box::new(|_| panic!("index out of bounds")),
        )
        .unwrap();
    let row = await_job(&store, &refused);
    assert_eq!(row.state, "failed");
    assert_eq!(
        row.error_message.unwrap(),
        "The model could not be solved: Phase \"A\" has no dates. This is about the model as specified, so changing the structure, the limiters or the dates is what will move it."
    );
    assert!(await_job(&store, &curve).error_message.unwrap().starts_with("The run could not use the calibration curve it was given: Unknown calibration curve 'x'."));
    let row = await_job(&store, &panicked);
    assert_eq!(row.state, "failed");
    let message = row.error_message.unwrap();
    assert!(
        message.starts_with("The run stopped because of an unexpected error inside the app"),
        "{message}"
    );
    assert!(
        !message.contains("index out of bounds"),
        "the panic text is for the log, not the screen"
    );
    jobs.shutdown();
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn a_cancel_stops_a_running_job_and_drops_a_queued_one() {
    let (store, dir) = temp_store();
    let jobs = Jobs::start(Arc::clone(&store), 1);
    let (started_tx, started_rx) = mpsc::channel();
    let running = jobs
        .submit(
            "bayesian",
            None,
            None,
            Box::new(move |handle| {
                started_tx.send(()).unwrap();
                loop {
                    handle.check_cancelled()?;
                    std::thread::sleep(Duration::from_millis(2));
                }
            }),
        )
        .unwrap();
    let queued = jobs
        .submit("bayesian", None, None, Box::new(|_| Ok(None)))
        .unwrap();
    started_rx.recv().unwrap();
    assert!(jobs.cancel(&queued).unwrap());
    assert!(jobs.cancel(&running).unwrap());
    let row = await_job(&store, &running);
    assert_eq!(row.state, "cancelled");
    assert!(row.error_message.is_none());
    let row = await_job(&store, &queued);
    assert_eq!(row.state, "cancelled");
    assert!(
        row.started_at.is_none(),
        "a job cancelled while queued never started"
    );
    // Nothing to stop once it has finished.
    assert!(!jobs.cancel(&running).unwrap());
    assert!(!jobs.cancel("no-such-job").unwrap());
    jobs.shutdown();
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn jobs_about_one_target_run_in_order_and_others_alongside() {
    let (store, dir) = temp_store();
    let jobs = Jobs::start(Arc::clone(&store), 3);
    let (release_tx, release_rx) = mpsc::channel::<()>();
    let release_rx = Arc::new(Mutex::new(release_rx));
    let gate = Arc::clone(&release_rx);
    let first = jobs
        .submit(
            "bayesian",
            Some("a"),
            None,
            Box::new(move |_| {
                gate.lock().unwrap().recv().unwrap();
                Ok(None)
            }),
        )
        .unwrap();
    let second = jobs
        .submit("bayesian", Some("a"), None, Box::new(|_| Ok(None)))
        .unwrap();
    let elsewhere = jobs
        .submit("bayesian", Some("b"), None, Box::new(|_| Ok(None)))
        .unwrap();
    // Another target's job finishes while the first is still held.
    assert_eq!(await_job(&store, &elsewhere).state, "completed");
    assert_eq!(job(&store, &second).state, "queued");
    release_tx.send(()).unwrap();
    let (a, b) = (await_job(&store, &first), await_job(&store, &second));
    assert!(
        b.started_at.unwrap() >= a.finished_at.unwrap(),
        "both ran at once"
    );
    jobs.shutdown();
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn progress_is_throttled_unless_forced() {
    let (store, dir) = temp_store();
    let jobs = Jobs::start(Arc::clone(&store), 1);
    let (seen_tx, seen_rx) = mpsc::channel();
    let probe = Arc::clone(&store);
    let id = jobs
        .submit(
            "bayesian",
            None,
            None,
            Box::new(move |handle| {
                let id = handle.job_id().unwrap().to_owned();
                let read = || -> Job { probe.read(|c| rows::get(c, &id)).unwrap().unwrap() };
                handle.update(Some(0.25), Some(json!({"sweep": 1})), false);
                handle.update(Some(0.5), Some(json!({"sweep": 2})), false);
                seen_tx
                    .send((read().progress, read().detail.clone()))
                    .unwrap();
                handle.update(Some(7.0), Some(json!({"sweep": 3})), true);
                seen_tx
                    .send((read().progress, read().detail.clone()))
                    .unwrap();
                Ok(None)
            }),
        )
        .unwrap();
    assert_eq!(
        seen_rx.recv().unwrap(),
        (Some(0.25), Some(json!({"sweep": 1})))
    );
    assert_eq!(
        seen_rx.recv().unwrap(),
        (Some(1.0), Some(json!({"sweep": 3})))
    );
    await_job(&store, &id);
    // A synchronous run reports nowhere.
    let null = Handle::null();
    null.update(Some(0.5), None, true);
    assert!(!null.cancelled() && null.check_cancelled().is_ok());
    jobs.shutdown();
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn a_restart_fails_whatever_was_mid_flight() {
    let (store, dir) = temp_store();
    let now = Some(Timestamp::now());
    store
        .write(|tx| {
            for (id, state) in [("q", "queued"), ("r", "running"), ("d", "completed")] {
                rows::insert(
                    tx,
                    &Job {
                        id: id.into(),
                        kind: "k".into(),
                        state: state.into(),
                        created_at: now,
                        ..Job::default()
                    },
                )?;
            }
            rows::insert(
                tx,
                &Analysis {
                    id: "an".into(),
                    name: "A".into(),
                    analysis_type: "bayesian".into(),
                    status: Some("running".into()),
                    ..Analysis::default()
                },
            )?;
            Ok(())
        })
        .unwrap();
    assert_eq!(reconcile_orphans(&store).unwrap(), 2);
    for id in ["q", "r"] {
        let row = job(&store, id);
        assert_eq!(
            (row.state.as_str(), row.error_message.as_deref()),
            ("failed", Some(INTERRUPTED))
        );
    }
    assert_eq!(job(&store, "d").state, "completed");
    let analysis: Analysis = store.read(|c| rows::get(c, "an")).unwrap().unwrap();
    assert_eq!(analysis.status.as_deref(), Some("failed"));
    assert_eq!(latest_for(&store, "nothing").unwrap(), None);
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn every_failure_is_a_complete_sentence_in_a_known_category() {
    use radical_core::Error as E;
    for error in [
        JobError::Cancelled,
        JobError::Core(E::EmptyCurve),
        JobError::Core(E::Spline("x must be increasing")),
        JobError::Core(E::Model(String::new())),
        JobError::Refused {
            curve: false,
            message: "No dates.".into(),
        },
        JobError::Internal("boom".into()),
    ] {
        let f = classify(&error);
        assert!(["curve", "model", "cancelled", "internal"].contains(&f.category));
        assert!(f.message.ends_with('.'), "{f:?}");
    }
    assert_eq!(
        classify(&JobError::Core(E::Spline("x"))).category,
        "internal"
    );
    assert_eq!(
        classify(&JobError::Core(E::Model(String::new()))).category,
        "internal"
    );
}
