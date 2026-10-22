//! RadiCal's HTTP API: the FastAPI backend's routes, paths, status codes and
//! JSON, served from the desktop app over radical-store and radical-jobs.
//!
//! One router per FastAPI router, in `routes/`. What the backend did per
//! request -- open a session, check ownership -- is here a shared [`State`]
//! and nothing at all: one person uses this database, so every row is
//! theirs. The accounts are gone; `/api/auth/options` says so
//! (`single_user`), and `/api/auth/me` answers for the one local user, which
//! is how the frontend knows to skip its sign-in page.

use std::sync::Arc;

use axum::Json;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use radical_jobs::Jobs;
use radical_store::Store;
use serde_json::{Value, json};

pub mod routes;

/// What every handler can reach.
#[derive(Clone)]
pub struct State {
    pub store: Arc<Store>,
    pub jobs: Arc<Jobs>,
}

impl State {
    /// Open the store at `path`, fail what a restart interrupted, and start
    /// the workers.
    pub fn open(path: impl AsRef<std::path::Path>, workers: usize) -> radical_store::Result<Self> {
        let store = Arc::new(Store::open(path)?);
        let orphans = radical_jobs::reconcile_orphans(&store)?;
        if orphans > 0 {
            eprintln!("Failed {orphans} job(s) left mid-flight by a restart");
        }
        let jobs = Arc::new(Jobs::start(Arc::clone(&store), workers));
        Ok(Self { store, jobs })
    }
}

/// The API's version, as `/api/health` reports it.
pub const VERSION: &str = "0.1.0";

// --------------------------------------------------------------------------
// Responses
// --------------------------------------------------------------------------

/// `{"success": true, "data": ...}`, the shape of nearly every answer.
pub fn ok(data: Value) -> Json<Value> {
    Json(json!({"success": true, "data": data}))
}

/// FastAPI's `HTTPException`: a status and `{"detail": ...}`.
#[derive(Debug, Clone, PartialEq)]
pub struct ApiError {
    pub status: StatusCode,
    pub detail: Value,
}

impl ApiError {
    pub fn new(status: StatusCode, detail: impl Into<String>) -> Self {
        Self {
            status,
            detail: Value::String(detail.into()),
        }
    }

    pub fn not_found(detail: impl Into<String>) -> Self {
        Self::new(StatusCode::NOT_FOUND, detail)
    }

    pub fn conflict(detail: impl Into<String>) -> Self {
        Self::new(StatusCode::CONFLICT, detail)
    }

    pub fn bad_request(detail: impl Into<String>) -> Self {
        Self::new(StatusCode::BAD_REQUEST, detail)
    }

    /// The fault is the app's: logged in full, shown plainly.
    pub fn internal(cause: impl std::fmt::Display) -> Self {
        eprintln!("internal error: {cause}");
        Self::new(StatusCode::INTERNAL_SERVER_ERROR, "Internal Server Error")
    }

    /// A request-validation failure, as FastAPI reports one: 422 with
    /// Pydantic's list of problems.
    pub fn invalid(problems: Vec<Value>) -> Self {
        Self {
            status: StatusCode::UNPROCESSABLE_ENTITY,
            detail: Value::Array(problems),
        }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.status, Json(json!({"detail": self.detail}))).into_response()
    }
}

impl From<radical_store::Error> for ApiError {
    fn from(e: radical_store::Error) -> Self {
        Self::internal(e)
    }
}

pub type ApiResult<T = Json<Value>> = Result<T, ApiError>;

/// One Pydantic validation problem, as FastAPI lists it.
pub fn problem(kind: &str, loc: &[&str], msg: &str, input: Value, ctx: Option<Value>) -> Value {
    let mut out = json!({"type": kind, "loc": loc, "msg": msg, "input": input});
    if let Some(ctx) = ctx {
        out["ctx"] = ctx;
    }
    out
}

/// The whole API.
pub fn app(state: State) -> axum::Router {
    routes::router()
        .fallback(|| async { ApiError::new(StatusCode::NOT_FOUND, "Not Found") })
        .with_state(state)
}
