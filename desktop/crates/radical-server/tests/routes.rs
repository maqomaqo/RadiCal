//! The OpenAPI contract: every route the backend's OpenAPI document lists is
//! served here, in accounts mode (RadiCal on a server), except the few named
//! below as not ported yet. RadiCal Desktop serves the same routes but has
//! no accounts, so its sign-in routes answer as if absent.
//!
//! A route counts as served when a request to it gets anything but the
//! fallback's 404 "Not Found" or a 405: a 401 for no token, a 404 for a
//! missing analysis, or a 422 for an empty body, is the route answering. The
//! check on a token runs only for a route that exists, so a path that is not
//! one still gets the fallback.

use axum::body::Body;
use axum::http::{Method, Request};
use http_body_util::BodyExt;
use radical_corpus::load_family;
use serde_json::Value;
use tower::ServiceExt;

/// Routes not ported yet, each with what stands in their way.
const NOT_YET: &[(&str, &str, &str)] = &[];

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
    let state = radical_server::State::open(dir.join("radical.db"), 1)
        .unwrap()
        .with_accounts(radical_server::accounts::Accounts {
            secret: "radical-routes-secret".to_owned(),
        });
    let app = radical_server::app(state);
    let param = regex::Regex::new(r"\{[^}]+\}").unwrap();
    let mut missing = Vec::new();
    let mut served = 0;
    for (path, ops) in doc["response_json"]["paths"].as_object().unwrap() {
        for method in ops.as_object().unwrap().keys() {
            let method = method.to_uppercase();
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

#[tokio::test(flavor = "multi_thread")]
async fn the_api_documents_itself_without_signing_in() {
    let dir = std::env::temp_dir().join(format!("radical-docs-{}", radical_store::new_id()));
    let state = radical_server::State::open(dir.join("radical.db"), 1)
        .unwrap()
        .with_accounts(radical_server::accounts::Accounts {
            secret: "radical-routes-secret".to_owned(),
        });
    let app = radical_server::app(state);
    for (path, status, starts) in [
        ("/api/openapi.json", 200, "{"),
        ("/api/docs", 200, "<!DOCTYPE html>"),
        ("/api/redoc", 200, "<!DOCTYPE html>"),
        ("/docs", 308, ""),
        ("/openapi.json", 308, ""),
    ] {
        let request = Request::builder().uri(path).body(Body::empty()).unwrap();
        let response = app.clone().oneshot(request).await.unwrap();
        assert_eq!(response.status().as_u16(), status, "{path}");
        let bytes = response.into_body().collect().await.unwrap().to_bytes();
        assert!(
            String::from_utf8_lossy(&bytes).starts_with(starts),
            "{path}"
        );
    }
    let _ = std::fs::remove_dir_all(dir);
}

/// Axum refuses a body over 2 MB in plain text unless told otherwise; a
/// project package is often larger, and its route must still answer in JSON.
#[tokio::test(flavor = "multi_thread")]
async fn an_upload_over_two_megabytes_reaches_its_route() {
    let dir = std::env::temp_dir().join(format!("radical-upload-{}", radical_store::new_id()));
    let state = radical_server::State::open(dir.join("radical.db"), 1).unwrap();
    let app = radical_server::app(state);
    let boundary = "radical-upload-boundary";
    let mut body = format!(
        "--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"project.zip\"\r\n\
         Content-Type: application/zip\r\n\r\n"
    )
    .into_bytes();
    body.extend(std::iter::repeat_n(b'x', 3 * 1024 * 1024));
    body.extend(format!("\r\n--{boundary}--\r\n").into_bytes());
    let request = Request::builder()
        .method(Method::POST)
        .uri("/api/import/project/preview")
        .header(
            "content-type",
            format!("multipart/form-data; boundary={boundary}"),
        )
        .body(Body::from(body))
        .unwrap();
    let response = app.oneshot(request).await.unwrap();
    let status = response.status().as_u16();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    let _ = std::fs::remove_dir_all(dir);
    assert_ne!(status, 413, "{}", String::from_utf8_lossy(&bytes));
    let body: Value = serde_json::from_slice(&bytes).expect("the route answers in JSON");
    assert!(body.get("success").is_some(), "{body}");
}
