//! One module per FastAPI router, each mounted under `/api` as `main.py`
//! mounts them.

use axum::Router;

use crate::State;

pub mod auth;
pub mod jobs;

pub fn router() -> Router<State> {
    Router::new()
        .route(
            "/api/health",
            axum::routing::get(|| async {
                axum::Json(serde_json::json!({"status": "ok", "version": crate::VERSION}))
            }),
        )
        .merge(auth::router())
        .merge(jobs::router())
}
