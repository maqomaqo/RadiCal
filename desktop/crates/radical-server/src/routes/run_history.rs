//! Run history (`api/run_history.py`): an analysis's kept runs, one of them
//! in the shape the Results page reads, and what moved between the last two
//! runs of every analysis in a project.

use std::collections::HashMap;

use axum::Router;
use axum::extract::{Path, Query, State as Extract};
use axum::routing::get;
use radical_io::python::repr_opt;
use radical_store::rows::{self, Analysis, AnalysisRun};
use serde_json::{Value, json};

use crate::history::RUNS_KEPT;
use crate::routes::analysis::{require, walk_tree};
use crate::validate::{Bounds, Fields, Problems};
use crate::{ApiError, ApiResult, State, ok};

const DEFAULT_SHIFT_YEARS: f64 = 10.0;

fn summary(run: &AnalysisRun, current: Option<&str>) -> Value {
    let solver = &run.payload["solver"];
    json!({
        "id": run.id,
        "created_at": run.created_at.isoformat(),
        "app_commit": run.app_commit,
        "inputs_hash": run.inputs_hash,
        "current": Some(run.id.as_str()) == current,
        "solver": solver["method"],
        "seed": solver["seed"],
        "sweeps": solver["sweeps"],
        "converged": solver["converged"],
    })
}

fn runs_of(state: &State, analysis_id: &str) -> radical_store::Result<Vec<AnalysisRun>> {
    state.store.read(|c| {
        rows::select_where(
            c,
            "analysis_id = ?",
            [analysis_id],
            "created_at DESC, id DESC",
        )
    })
}

async fn list_runs(Extract(state): Extract<State>, Path(analysis_id): Path<String>) -> ApiResult {
    state.store.read(|c| Ok(require(c, &analysis_id)))??;
    let runs = runs_of(&state, &analysis_id)?;
    let current = runs.first().map(|r| r.id.clone());
    Ok(ok(json!({
        "kept": RUNS_KEPT,
        "runs": runs.iter().map(|r| summary(r, current.as_deref())).collect::<Vec<_>>(),
    })))
}

/// One kept run in GET /analysis/{id}'s shape, its id `analysis@run`.
async fn get_run(
    Extract(state): Extract<State>,
    Path((analysis_id, run_id)): Path<(String, String)>,
) -> ApiResult {
    state.store.read(|c| Ok(require(c, &analysis_id)))??;
    let run = runs_of(&state, &analysis_id)?
        .into_iter()
        .find(|r| r.id == run_id)
        .ok_or_else(|| ApiError::not_found("That run was not found."))?;
    let mut payload = if run.payload.is_object() {
        run.payload.clone()
    } else {
        json!({})
    };
    payload["id"] = json!(format!("{analysis_id}@{run_id}"));
    payload["run"] = summary(&run, None);
    Ok(ok(payload))
}

/// A quantity's pairing key, and its text as Python's `str()` of the tuple
/// would sort it.
type Key = (String, String);

struct Quantity {
    kind: Value,
    name: String,
    median: f64,
    modes: usize,
    units: &'static str,
}

/// Every modelled quantity a run reports, keyed by type and name so two
/// runs pair up (`_quantities`).
fn quantities(payload: &Value) -> HashMap<Key, Quantity> {
    let mut out = HashMap::new();
    let key = |group: &str, kind: &Value, name: &str| {
        let text = format!(
            "('{group}', {}, {})",
            repr_opt(kind.as_str()),
            repr_opt(Some(name))
        );
        (text, format!("{group}\u{0}{kind}\u{0}{name}"))
    };
    if payload["model_tree"].is_object() {
        let mut nodes = Vec::new();
        walk_tree(&payload["model_tree"], &mut nodes);
        for e in nodes {
            let p = &e["posterior"];
            let Some(median) = p["posterior_median"].as_f64() else {
                continue;
            };
            let name = e["name"].as_str().unwrap_or_default().to_owned();
            out.insert(
                key("element", &e["element_type"], &name),
                Quantity {
                    kind: e["element_type"].clone(),
                    name,
                    median,
                    modes: p["hpd_95_ranges"].as_array().map_or(0, Vec::len),
                    units: "cal BP",
                },
            );
        }
    }
    for r in payload["intervals"]["durations"]
        .as_array()
        .into_iter()
        .flatten()
    {
        let y = &r["years"];
        let Some(median) = y["median"].as_f64() else {
            continue;
        };
        let name = r["name"].as_str().unwrap_or_default().to_owned();
        out.insert(
            key("duration", &r["kind"], &name),
            Quantity {
                kind: r["kind"].clone(),
                name,
                median,
                modes: y["hpd_95_ranges"].as_array().map_or(0, Vec::len),
                units: "yr",
            },
        );
    }
    out
}

/// What changed between two runs of one analysis (`diff_runs`).
fn diff_runs(newer: &Value, older: &Value, shift_years: f64) -> Value {
    let (new_q, old_q) = (quantities(newer), quantities(older));
    let mut shared: Vec<&Key> = new_q.keys().filter(|k| old_q.contains_key(*k)).collect();
    shared.sort();
    let moved: Vec<Value> = shared
        .into_iter()
        .filter_map(|k| {
            let (a, b) = (&old_q[k], &new_q[k]);
            let shift = b.median - a.median;
            (shift.abs() >= shift_years || a.modes != b.modes).then(|| {
                json!({
                    "kind": b.kind, "name": b.name, "units": b.units,
                    "before": a.median, "after": b.median, "shift": shift,
                    "modes_before": a.modes, "modes_after": b.modes,
                })
            })
        })
        .collect();
    let only = |from: &HashMap<Key, Quantity>, other: &HashMap<Key, Quantity>| {
        let mut names: Vec<String> = from
            .iter()
            .filter(|(k, _)| !other.contains_key(*k))
            .map(|(_, q)| q.name.clone())
            .collect();
        names.sort();
        names
    };
    let (before, after) = (&older["solver"]["converged"], &newer["solver"]["converged"]);
    json!({
        "moved": moved,
        "only_before": only(&old_q, &new_q),
        "only_after": only(&new_q, &old_q),
        "converged_before": before,
        "converged_after": after,
        "convergence_changed": before != after,
    })
}

/// For every analysis in a project with two kept runs, what moved between
/// the latest and the one before it.
async fn project_run_diff(
    Extract(state): Extract<State>,
    Path(project_id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> ApiResult {
    let input = Value::Object(q.iter().map(|(k, v)| (k.clone(), json!(v))).collect());
    let mut problems = Problems::default();
    let mut f = Fields::new(&input, vec![json!("query")], "query", &mut problems);
    let shift = f.float_or("shift_years", DEFAULT_SHIFT_YEARS, Bounds::NONE);
    problems.finish()?;
    let shift = shift.unwrap_or(DEFAULT_SHIFT_YEARS);
    crate::routes::projects::require(&state, &project_id)?;
    let analyses: Vec<Analysis> = state
        .store
        .read(|c| rows::select_where(c, "project_id = ?", [&project_id], "created_at, id"))?;
    let (mut changed, mut compared) = (Vec::new(), 0);
    for a in analyses {
        let runs = runs_of(&state, &a.id)?;
        if runs.len() < 2 {
            continue;
        }
        compared += 1;
        let diff = diff_runs(&runs[0].payload, &runs[1].payload, shift);
        let interesting = diff["moved"].as_array().is_some_and(|m| !m.is_empty())
            || diff["convergence_changed"] == true
            || diff["only_before"]
                .as_array()
                .is_some_and(|m| !m.is_empty())
            || diff["only_after"].as_array().is_some_and(|m| !m.is_empty());
        if interesting {
            let mut entry = json!({
                "analysis_id": a.id, "name": a.name,
                "run_after": runs[0].id, "run_before": runs[1].id,
                "same_inputs": runs[0].inputs_hash == runs[1].inputs_hash,
                "commits": [runs[1].app_commit, runs[0].app_commit],
            });
            for (k, v) in diff.as_object().unwrap() {
                entry[k] = v.clone();
            }
            changed.push(entry);
        }
    }
    Ok(ok(json!({
        "shift_years": shift,
        "compared": compared,
        "changed": changed,
    })))
}

pub fn router() -> Router<State> {
    Router::new()
        .route("/api/analysis/{analysis_id}/runs", get(list_runs))
        .route("/api/analysis/{analysis_id}/runs/{run_id}", get(get_run))
        .route("/api/projects/{project_id}/run-diff", get(project_run_diff))
}
