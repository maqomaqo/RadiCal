//! One module per FastAPI router, each mounted under `/api` as `main.py`
//! mounts them.

use axum::Router;
use axum::extract::DefaultBodyLimit;

use crate::State;

pub mod analysis;
pub mod auth;
pub mod bayesian;
pub mod curve_routes;
pub mod docs;
pub mod imports;
pub mod influence;
pub mod inputs;
pub mod jobs;
pub mod model_templates;
pub mod project_export;
pub mod project_import;
pub mod project_samples;
pub mod projects;
pub mod reservoir;
pub mod results;
pub mod run_history;
pub mod runs;

/// Axum caps a request body at 2 MB unless told otherwise, refusing a larger
/// one in plain text before any route reads it. The largest upload is a
/// project package, whose route checks its size itself; this leaves room for
/// that file and the form around it.
const MAX_BODY_BYTES: usize = project_import::MAX_PACKAGE_BYTES + 1024 * 1024;

pub fn router() -> Router<State> {
    Router::new()
        .route(
            "/api/health",
            axum::routing::get(|| async {
                axum::Json(serde_json::json!({"status": "ok", "version": crate::VERSION}))
            }),
        )
        .merge(docs::router())
        .merge(auth::router())
        .merge(jobs::router())
        .merge(analysis::router())
        .merge(runs::router())
        .merge(bayesian::router())
        .merge(inputs::router())
        .merge(results::router())
        .merge(curve_routes::router())
        .merge(imports::router())
        .merge(project_export::router())
        .merge(project_import::router())
        .merge(model_templates::router())
        .merge(run_history::router())
        .merge(influence::router())
        .merge(reservoir::router())
        .merge(projects::router())
        .merge(project_samples::router())
        .layer(DefaultBodyLimit::max(MAX_BODY_BYTES))
}
