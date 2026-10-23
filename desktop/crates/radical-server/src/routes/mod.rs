//! One module per FastAPI router, each mounted under `/api` as `main.py`
//! mounts them.

use axum::Router;

use crate::State;

pub mod analysis;
pub mod auth;
pub mod bayesian;
pub mod curve_routes;
pub mod imports;
pub mod influence;
pub mod inputs;
pub mod jobs;
pub mod model_templates;
pub mod project_export;
pub mod project_import;
pub mod project_samples;
pub mod projects;
pub mod results;
pub mod run_history;
pub mod runs;

pub fn router() -> Router<State> {
    Router::new()
        .route(
            "/",
            axum::routing::get(|| async {
                axum::Json(serde_json::json!({
                    "message": "RadiCal API", "docs": "/api/docs", "health": "/api/health",
                }))
            }),
        )
        .route(
            "/api/health",
            axum::routing::get(|| async {
                axum::Json(serde_json::json!({"status": "ok", "version": crate::VERSION}))
            }),
        )
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
        .merge(projects::router())
        .merge(project_samples::router())
}
