//! Projects (`api/projects.py`): creating, listing, reading, editing and
//! deleting them. Re-running a project and exporting one arrive with the
//! analysis routes they are built on.

use std::collections::HashMap;

use axum::Router;
use axum::body::Bytes;
use axum::extract::{Path, State as Extract};
use axum::routing::{get, post};
use radical_store::rows::{self, Analysis, Project};
use radical_store::{Timestamp, rusqlite};
use serde_json::{Value, json};

use crate::validate::{self, Problems};
use crate::{ApiError, ApiResult, State, ok};

/// How precisely a project's coordinates may leave the app.
pub const COORDINATE_PRECISIONS: [&str; 4] = ["full", "0.01", "0.1", "1"];

fn iso(t: Option<Timestamp>) -> Value {
    t.map_or(Value::Null, |t| Value::from(t.isoformat()))
}

/// The two coordinate settings, on every view of one project.
fn with_settings(mut payload: Value, p: &Project) -> Value {
    payload["coordinate_precision"] = Value::from(if p.coordinate_precision.is_empty() {
        "full"
    } else {
        p.coordinate_precision.as_str()
    });
    payload["map_tiles_enabled"] = Value::from(p.map_tiles_enabled);
    payload
}

/// The project, or the 404 a missing one is.
pub fn require(state: &State, project_id: &str) -> ApiResult<Project> {
    state
        .store
        .read(|c| rows::get(c, project_id))?
        .ok_or_else(|| ApiError::not_found("That project was not found."))
}

/// The later of two optional instants.
fn latest(a: Option<Timestamp>, b: Option<Timestamp>) -> Option<Timestamp> {
    match (a, b) {
        (Some(a), Some(b)) => Some(a.max(b)),
        (a, b) => a.or(b),
    }
}

/// Create a new project to group analyses under.
async fn create(Extract(state): Extract<State>, body: Bytes) -> ApiResult {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "ProjectCreate", &mut problems);
    let name = f.string("name", None, None);
    let description = f.opt_str("description", None);
    problems.finish()?;
    let name = name.unwrap_or_default();
    if name.trim().is_empty() {
        return Err(ApiError::bad_request("Project name is required"));
    }
    let now = Some(Timestamp::now());
    let project = Project {
        id: radical_store::new_id(),
        name: name.trim().to_owned(),
        description,
        created_at: now,
        updated_at: now,
        coordinate_precision: "full".to_owned(),
        map_tiles_enabled: false,
    };
    state.store.write(|tx| rows::insert(tx, &project))?;
    Ok(ok(with_settings(
        json!({
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "created_at": iso(project.created_at),
            "updated_at": iso(project.updated_at),
            "last_activity": iso(project.updated_at),
            "analyses_count": 0,
            "status_counts": {"completed": 0, "running": 0, "failed": 0, "pending": 0},
            "samples_count": 0,
        }),
        &project,
    )))
}

/// Every project, with the numbers the project grid ranks and draws with,
/// and how many analyses are filed under none.
async fn list(Extract(state): Extract<State>) -> ApiResult {
    let data = state.store.read(|c| {
        let projects: Vec<Project> = rows::select_where(c, "1", [], "created_at DESC")?;
        let mut by_status: HashMap<Option<String>, HashMap<String, i64>> = HashMap::new();
        let mut stmt = c.prepare("SELECT project_id, status, count(id) FROM analyses GROUP BY project_id, status")?;
        for row in stmt.query_map([], |r| Ok((r.get::<_, Option<String>>(0)?, r.get::<_, Option<String>>(1)?, r.get::<_, i64>(2)?)))? {
            let (project, status, n) = row?;
            *by_status.entry(project).or_default().entry(status.unwrap_or_else(|| "pending".to_owned())).or_default() += n;
        }
        let mut last: HashMap<Option<String>, Option<Timestamp>> = HashMap::new();
        let mut stmt = c.prepare("SELECT project_id, max(updated_at) FROM analyses GROUP BY project_id")?;
        for row in stmt.query_map([], |r| Ok((r.get::<_, Option<String>>(0)?, r.get::<_, Option<Timestamp>>(1)?)))? {
            let (project, when) = row?;
            last.insert(project, when);
        }
        let samples: HashMap<String, i64> = c
            .prepare("SELECT project_id, count(id) FROM project_samples GROUP BY project_id")?
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
            .collect::<rusqlite::Result<_>>()?;
        let empty = HashMap::new();
        let items: Vec<Value> = projects
            .iter()
            .map(|p| {
                let counts = by_status.get(&Some(p.id.clone())).unwrap_or(&empty);
                let n = |k: &str| counts.get(k).copied().unwrap_or(0);
                let activity = latest(last.get(&Some(p.id.clone())).copied().flatten(), p.updated_at);
                json!({
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "created_at": iso(p.created_at),
                    "updated_at": iso(p.updated_at),
                    "last_activity": iso(activity),
                    "analyses_count": counts.values().sum::<i64>(),
                    "status_counts": {"completed": n("completed"), "running": n("running"), "failed": n("failed"), "pending": n("pending")},
                    "samples_count": samples.get(&p.id).copied().unwrap_or(0),
                })
            })
            .collect();
        let unassigned: i64 = by_status.get(&None).map_or(0, |c| c.values().sum());
        Ok(json!({"items": items, "unassigned_count": unassigned}))
    })?;
    Ok(ok(data))
}

/// A project and the analyses filed under it.
async fn read(Extract(state): Extract<State>, Path(project_id): Path<String>) -> ApiResult {
    let project = require(&state, &project_id)?;
    let data = state.store.read(|c| {
        let analyses: Vec<Analysis> =
            rows::select_where(c, "project_id = ?", [&project_id], "created_at DESC")?;
        let counts: HashMap<String, i64> = c
            .prepare(
                "SELECT analysis_id, count(id) FROM radiocarbon_samples WHERE analysis_id IN \
                 (SELECT id FROM analyses WHERE project_id = ?) GROUP BY analysis_id",
            )?
            .query_map([&project_id], |r| Ok((r.get(0)?, r.get(1)?)))?
            .collect::<rusqlite::Result<_>>()?;
        let samples: i64 = c.query_row(
            "SELECT count(id) FROM project_samples WHERE project_id = ?",
            [&project_id],
            |r| r.get(0),
        )?;
        let newest = analyses.iter().filter_map(|a| a.updated_at).max();
        let activity = latest(newest, project.updated_at);
        Ok(json!({
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "created_at": iso(project.created_at),
            "updated_at": iso(project.updated_at),
            "last_activity": iso(activity),
            "samples_count": samples,
            "analyses": analyses.iter().map(|a| json!({
                "id": a.id,
                "name": a.name,
                "analysis_type": a.analysis_type,
                "status": a.status,
                "created_at": iso(a.created_at),
                "measurements_count": counts.get(&a.id).copied().unwrap_or(0),
            })).collect::<Vec<_>>(),
        }))
    })?;
    Ok(ok(with_settings(data, &project)))
}

/// Rename a project, edit its description, or change one of its two
/// coordinate settings.
async fn update(
    Extract(state): Extract<State>,
    Path(project_id): Path<String>,
    body: Bytes,
) -> ApiResult {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "ProjectUpdate", &mut problems);
    let name = f.opt_str("name", None);
    let description = f.opt_str("description", None);
    let precision = f.opt_literal("coordinate_precision", &COORDINATE_PRECISIONS);
    let tiles = f.opt_bool("map_tiles_enabled");
    problems.finish()?;
    let mut project = require(&state, &project_id)?;
    let before = project.clone();
    if let Some(name) = name {
        if name.trim().is_empty() {
            return Err(ApiError::bad_request("Project name cannot be empty"));
        }
        project.name = name.trim().to_owned();
    }
    if description.is_some() {
        project.description = description;
    }
    if let Some(p) = precision {
        project.coordinate_precision = p;
    }
    if let Some(t) = tiles {
        project.map_tiles_enabled = t;
    }
    // SQLAlchemy's onupdate: the stamp moves only when something did.
    if project != before {
        project.updated_at = Some(Timestamp::now());
        state.store.write(|tx| rows::update(tx, &project))?;
    }
    Ok(ok(with_settings(
        json!({
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "created_at": iso(project.created_at),
            "updated_at": iso(project.updated_at),
        }),
        &project,
    )))
}

/// Delete a project. Its analyses are unassigned, not deleted; its date
/// collection goes with it.
async fn delete(Extract(state): Extract<State>, Path(project_id): Path<String>) -> ApiResult {
    require(&state, &project_id)?;
    let reassigned = state.store.write(|tx| {
        let moved = tx.execute(
            "UPDATE analyses SET project_id = NULL WHERE project_id = ?",
            [&project_id],
        )?;
        tx.execute(
            "DELETE FROM project_samples WHERE project_id = ?",
            [&project_id],
        )?;
        tx.execute("DELETE FROM projects WHERE id = ?", [&project_id])?;
        Ok(moved)
    })?;
    Ok(ok(
        json!({"deleted_project_id": project_id, "unassigned_analyses": reassigned}),
    ))
}

pub fn router() -> Router<State> {
    Router::new()
        .route("/api/projects", post(create).get(list))
        .route(
            "/api/projects/{project_id}",
            get(read).patch(update).delete(delete),
        )
}
