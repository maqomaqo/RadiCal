//! The OpenAPI contract: every route the backend's OpenAPI document lists is
//! served here, except the sign-in routes the desktop app has no use for and
//! the few named below as not ported yet.
//!
//! A route counts as served when a request to it gets anything but the
//! fallback's 404 "Not Found" or a 405: a 404 for a missing analysis, or a
//! 422 for an empty body, is the route answering.

use axum::body::Body;
use axum::http::{Method, Request};
use http_body_util::BodyExt;
use radical_corpus::load_family;
use serde_json::Value;
use tower::ServiceExt;

/// The backend's routes the desktop app deliberately does without: one
/// person uses this database, so there is no account to sign in to (the
/// app answers /auth/options and /auth/me itself; see routes/auth.rs).
fn removed(method: &str, path: &str) -> bool {
    path.starts_with("/api/auth/")
        && !matches!(
            (method, path),
            ("GET", "/api/auth/options" | "/api/auth/me")
        )
}

/// Routes not ported yet, each with what stands in their way.
const NOT_YET: &[(&str, &str, &str)] = &[
    ("GET", "/api/results/{analysis_id}/pdf", "the PDF report"),
    ("GET", "/api/reservoir/snapshots", RESERVOIR),
    ("POST", "/api/reservoir/snapshots", RESERVOIR),
    ("GET", "/api/reservoir/snapshots/{snapshot_id}", RESERVOIR),
    (
        "DELETE",
        "/api/reservoir/snapshots/{snapshot_id}",
        RESERVOIR,
    ),
    ("GET", "/api/reservoir/freshness", RESERVOIR),
    ("POST", "/api/reservoir/lookup", RESERVOIR),
];

/// The marine reservoir routes wait on the ΔR lookup in radical-core and an
/// HTTP client for the calib.org ingest.
const RESERVOIR: &str = "the ΔR lookup and the calib.org ingest";

#[tokio::test(flavor = "multi_thread")]
async fn every_backend_route_is_served() {
    let family = load_family("api/exchanges").unwrap();
    let doc = family
        .cases
        .iter()
        .find(|c| c.inputs["url"] == "/api/openapi.json")
        .and_then(|c| c.expanded_outputs())
        .expect("the recording has the OpenAPI document");
    let dir = std::env::temp_dir().join(format!("radical-routes-{}", radical_store::new_id()));
    let state = radical_server::State::open(dir.join("radical.db"), 1).unwrap();
    let app = radical_server::app(state);
    let param = regex::Regex::new(r"\{[^}]+\}").unwrap();
    let mut missing = Vec::new();
    let mut served = 0;
    for (path, ops) in doc["response_json"]["paths"].as_object().unwrap() {
        for method in ops.as_object().unwrap().keys() {
            let method = method.to_uppercase();
            if removed(&method, path) {
                continue;
            }
            let uri = param.replace_all(path, "no-such-id").into_owned();
            let request = Request::builder()
                .method(Method::from_bytes(method.as_bytes()).unwrap())
                .uri(uri)
                .header("content-type", "application/json")
                .body(Body::from("{}"))
                .unwrap();
            let response = app.clone().oneshot(request).await.unwrap();
            let status = response.status().as_u16();
            let bytes = response.into_body().collect().await.unwrap().to_bytes();
            let body: Value = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
            let fallback = (status == 404 && body["detail"] == "Not Found") || status == 405;
            let known = NOT_YET.iter().any(|(m, p, _)| *m == method && p == path);
            match (fallback, known) {
                (true, false) => missing.push(format!("{method} {path}")),
                (false, true) => {
                    missing.push(format!("{method} {path} is served: take it off NOT_YET"))
                }
                (false, false) => served += 1,
                (true, true) => {}
            }
        }
    }
    let _ = std::fs::remove_dir_all(dir);
    eprintln!("{served} routes served, {} not yet", NOT_YET.len());
    assert!(missing.is_empty(), "not served: {missing:#?}");
}
