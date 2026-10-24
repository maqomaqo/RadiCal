//! The server as the desktop app runs it: every `/api/` request asks for
//! the launch token, and the frontend is served from the same origin.

use axum::body::Body;
use axum::http::Request;
use http_body_util::BodyExt;
use radical_server::launch::{Launch, app, new_token};
use tower::ServiceExt;

async fn get(router: &axum::Router, uri: &str, token: Option<&str>) -> (u16, String, String) {
    let mut request = Request::builder().uri(uri);
    if let Some(t) = token {
        request = request.header("authorization", format!("Bearer {t}"));
    }
    let response = router
        .clone()
        .oneshot(request.body(Body::empty()).unwrap())
        .await
        .unwrap();
    let status = response.status().as_u16();
    let kind = response
        .headers()
        .get("content-type")
        .map(|v| v.to_str().unwrap().to_owned())
        .unwrap_or_default();
    let body = response.into_body().collect().await.unwrap().to_bytes();
    (status, kind, String::from_utf8_lossy(&body).into_owned())
}

#[tokio::test(flavor = "multi_thread")]
async fn the_api_wants_the_token_and_the_frontend_is_served_beside_it() {
    let dir = std::env::temp_dir().join(format!("radical-launch-{}", radical_store::new_id()));
    let front = dir.join("dist");
    std::fs::create_dir_all(front.join("assets")).unwrap();
    std::fs::write(
        front.join("index.html"),
        "<!doctype html><title>RadiCal</title>",
    )
    .unwrap();
    std::fs::write(front.join("assets/app-1234.js"), "console.log(1)").unwrap();
    let state = radical_server::State::open(dir.join("radical.db"), 1).unwrap();
    let token = new_token();
    let router = app(
        state,
        Launch {
            token: Some(token.clone()),
            frontend: Some(front),
        },
    );

    // The API: refused without the token, or with another one.
    let (status, _, body) = get(&router, "/api/health", None).await;
    assert_eq!(
        (status, body.as_str()),
        (401, r#"{"detail":"Not authenticated"}"#)
    );
    assert_eq!(get(&router, "/api/health", Some("guess")).await.0, 401);
    let (status, _, body) = get(&router, "/api/health", Some(&token)).await;
    assert_eq!(status, 200, "{body}");
    assert!(body.contains("\"status\":\"ok\""));
    // An API path that is not a route is the API's 404, not the frontend.
    let (status, kind, _) = get(&router, "/api/nowhere", Some(&token)).await;
    assert_eq!((status, kind.as_str()), (404, "application/json"));

    // The frontend needs no token: the window loads it before it has one.
    let (status, kind, body) = get(&router, "/", None).await;
    assert_eq!((status, kind.as_str()), (200, "text/html; charset=utf-8"));
    assert!(body.contains("<title>RadiCal</title>"));
    let (status, kind, _) = get(&router, "/assets/app-1234.js", None).await;
    assert_eq!(
        (status, kind.as_str()),
        (200, "text/javascript; charset=utf-8")
    );
    // A route the frontend draws itself gets index.html.
    let (status, _, body) = get(&router, "/results/abc", None).await;
    assert_eq!(status, 200);
    assert!(body.contains("<title>RadiCal</title>"));
    let _ = std::fs::remove_dir_all(dir);
}
