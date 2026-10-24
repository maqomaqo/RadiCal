//! Reading and cancelling background work (`api/jobs.py`).

use axum::Router;
use axum::extract::{Path, Query, State as Extract};
use axum::routing::get;
use radical_store::rows::{self, Job};
use serde_json::{Value, json};

use crate::{ApiError, ApiResult, State, ok, problem};

fn find(state: &State, job_id: &str) -> ApiResult<Job> {
    state
        .store
        .read(|c| rows::get(c, job_id))?
        .ok_or_else(|| ApiError::not_found("That job was not found."))
}

/// One job's state, progress and detail.
async fn get_job(Extract(state): Extract<State>, Path(job_id): Path<String>) -> ApiResult {
    Ok(ok(radical_jobs::as_json(&find(&state, &job_id)?)))
}

/// Recent jobs, newest first, optionally about one analysis or project.
async fn list_jobs(
    Extract(state): Extract<State>,
    Query(query): Query<std::collections::HashMap<String, String>>,
) -> ApiResult {
    let limit = match query.get("limit") {
        None => 20,
        Some(raw) => {
            let Ok(n) = raw.trim().parse::<i64>() else {
                return Err(ApiError::invalid(vec![problem(
                    "int_parsing",
                    &[json!("query"), json!("limit")],
                    "Input should be a valid integer, unable to parse string as an integer",
                    Value::from(raw.as_str()),
                    None,
                )]));
            };
            if n < 1 {
                return Err(ApiError::invalid(vec![problem(
                    "greater_than_equal",
                    &[json!("query"), json!("limit")],
                    "Input should be greater than or equal to 1",
                    Value::from(raw.as_str()),
                    Some(json!({"ge": 1})),
                )]));
            }
            if n > 100 {
                return Err(ApiError::invalid(vec![problem(
                    "less_than_equal",
                    &[json!("query"), json!("limit")],
                    "Input should be less than or equal to 100",
                    Value::from(raw.as_str()),
                    Some(json!({"le": 100})),
                )]));
            }
            n
        }
    };
    let target = query.get("target_id").filter(|t| !t.is_empty()).cloned();
    let rows: Vec<Job> = state.store.read(|c| match &target {
        Some(t) => rows::select_where(
            c,
            "target_id = ?1 ORDER BY created_at DESC LIMIT ?2",
            (t, limit),
            "",
        ),
        None => rows::select_where(c, "1 ORDER BY created_at DESC LIMIT ?1", [limit], ""),
    })?;
    Ok(ok(
        json!({"jobs": rows.iter().map(radical_jobs::as_json).collect::<Vec<_>>()}),
    ))
}

/// Ask a job to stop: accepted, not done -- poll for 'cancelled'.
async fn cancel_job(Extract(state): Extract<State>, Path(job_id): Path<String>) -> ApiResult {
    let row = find(&state, &job_id)?;
    if row.state != "queued" && row.state != "running" {
        return Err(ApiError::conflict(format!(
            "This job has already {}; there is nothing to cancel.",
            row.state
        )));
    }
    state.jobs.cancel(&job_id)?;
    Ok(ok(
        json!({"job_id": job_id, "cancellation_requested": true}),
    ))
}

pub fn router() -> Router<State> {
    Router::new()
        .route("/api/jobs", get(list_jobs))
        .route("/api/jobs/{job_id}", get(get_job).delete(cancel_job))
}
