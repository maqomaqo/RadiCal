//! A re-run cancelled before it starts: the replay can only cover the
//! running path, since the backend's suite waits for that on purpose, so
//! this holds the queue's only worker to keep the re-run queued.

use std::sync::mpsc;
use std::time::{Duration, Instant};

use axum::Router;
use axum::body::Body;
use axum::http::{Method, Request};
use http_body_util::BodyExt;
use radical_corpus::load_family;
use serde_json::Value;
use tower::ServiceExt;

async fn call(app: &Router, method: Method, uri: &str, body: Option<&Value>) -> (u16, Value) {
    let request = Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json")
        .body(body.map_or_else(Body::empty, |b| Body::from(b.to_string())))
        .unwrap();
    let response = app.clone().oneshot(request).await.unwrap();
    let status = response.status().as_u16();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    (
        status,
        serde_json::from_slice(&bytes).unwrap_or(Value::Null),
    )
}

#[tokio::test(flavor = "multi_thread")]
async fn a_rerun_cancelled_before_it_starts_keeps_the_old_results() {
    // The recorded suite's own model, run to completion in the request.
    let family = load_family("api/exchanges").unwrap();
    let created = family
        .cases
        .iter()
        .map(|c| &c.inputs)
        .find(|i| {
            i["test"].as_str().is_some_and(|t| {
                t.ends_with("test_cancelling_stops_the_run_and_keeps_the_old_results")
            }) && i["url"]
                .as_str()
                .is_some_and(|u| u.starts_with("/api/analysis/bayesian"))
        })
        .expect("the recording has the cancellation test's model");
    let dir = std::env::temp_dir().join(format!("radical-jobs-{}", radical_store::new_id()));
    let state = radical_server::State::open(dir.join("radical.db"), 1).unwrap();
    let jobs = std::sync::Arc::clone(&state.jobs);
    let app = radical_server::app(state);
    let url = created["url"].as_str().unwrap();
    let (status, body) = call(&app, Method::POST, url, Some(&created["request_json"])).await;
    assert_eq!(status, 200, "{body}");
    let id = body["data"]["id"].as_str().unwrap().to_owned();
    let analysis = format!("/api/analysis/{id}");
    let (_, before) = call(&app, Method::GET, &analysis, None).await;
    assert_eq!(before["data"]["status"], "completed");

    // Hold the only worker, so the re-run waits in the queue.
    let (started_tx, started_rx) = mpsc::channel();
    let (release_tx, release_rx) = mpsc::channel::<()>();
    jobs.submit(
        "hold",
        None,
        None,
        Box::new(move |_| {
            started_tx.send(()).unwrap();
            release_rx.recv().unwrap();
            Ok(None)
        }),
    )
    .unwrap();
    started_rx.recv_timeout(Duration::from_secs(20)).unwrap();

    let (status, posted) = call(&app, Method::POST, &format!("{analysis}/rerun"), None).await;
    assert_eq!(status, 202, "{posted}");
    let job = format!("/api/jobs/{}", posted["data"]["job_id"].as_str().unwrap());
    let (_, queued) = call(&app, Method::GET, &analysis, None).await;
    assert_eq!(queued["data"]["status"], "pending");
    let (status, _) = call(&app, Method::DELETE, &job, None).await;
    assert_eq!(status, 200);
    release_tx.send(()).unwrap();

    let deadline = Instant::now() + Duration::from_secs(60);
    let finished = loop {
        let (_, polled) = call(&app, Method::GET, &job, None).await;
        let state = polled["data"]["state"].as_str().unwrap_or("").to_owned();
        if !matches!(state.as_str(), "queued" | "running") {
            break polled;
        }
        assert!(Instant::now() < deadline, "the re-run never finished");
        tokio::time::sleep(Duration::from_millis(20)).await;
    };
    assert_eq!(finished["data"]["state"], "cancelled");
    assert!(finished["data"]["started_at"].is_null());

    let (_, after) = call(&app, Method::GET, &analysis, None).await;
    assert_eq!(after["data"]["status"], "completed");
    assert_eq!(after["data"]["results"], before["data"]["results"]);
    let _ = std::fs::remove_dir_all(dir);
}
