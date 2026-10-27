//! Bayesian runs and re-runs (`api/analysis.py`, `api/projects.py`): a new
//! model's inputs saved and solved, a saved analysis solved again from its
//! own inputs, and every analysis in a project in turn.
//!
//! A run is queued unless asked to wait (`wait=true`), or unless nothing is
//! saved and so there is nothing for a later poll to read. The inputs are
//! written in the request either way, so a malformed model is refused at
//! once and a queued one is readable at its own URL while it runs.

use std::collections::HashMap;
use std::sync::Arc;

use axum::Json;
use axum::Router;
use axum::body::Bytes;
use axum::extract::{Path, Query, State as Extract};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::post;
use radical_core::Measurement;
use radical_core::exact_chain::Posterior;
use radical_core::measurement::Curves;
use radical_core::model_tree::migrate_from_relationships;
use radical_jobs::Handle;
use radical_store::Timestamp;
use radical_store::rows::{
    self, Analysis, AnalysisResult, ModelElement, RadiocarbonSample, SampleGroup,
    StratigraphicRelationship,
};
use serde_json::{Value, json};

use crate::accounts::Caller;
use crate::routes::analysis::{
    measurements, migrated_json, persist_tree, query_bool, require, tree_or_migrate,
    validate_project_id,
};
use crate::routes::curve_routes::authorise_curve_ids;
use crate::routes::runs::{calibration_result_row, calibration_stats, sample_row};
use crate::schemas::{self, BayesianRequest};
use crate::solve::{self, Failed, Solved, job_progress, posterior_json, result_row};
use crate::validate::{self, Problems};
use crate::{ApiError, State, history};

type Answer = Result<Response, ApiError>;

fn answer(status: StatusCode, data: Value) -> Response {
    (status, Json(json!({"success": true, "data": data}))).into_response()
}

fn wait_flag(q: &HashMap<String, String>) -> Result<bool, ApiError> {
    let mut problems = Problems::default();
    let wait = query_bool(q, "wait", &mut problems);
    problems.finish()?;
    Ok(wait)
}

/// Run blocking work off the async runtime.
async fn blocking<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> T {
    tokio::task::spawn_blocking(f)
        .await
        .expect("the solve does not panic")
}

fn set_status(state: &State, analysis_id: &str, status: &str, error: Option<String>) {
    let _ = state.store.write(|tx| {
        if let Some(mut a) = rows::get::<Analysis>(tx, analysis_id)? {
            a.status = Some(status.to_owned());
            a.error_message = error;
            a.updated_at = Some(Timestamp::now());
            rows::update(tx, &a)?;
        }
        Ok(())
    });
}

// --------------------------------------------------------------------------
// POST /analysis/bayesian
// --------------------------------------------------------------------------

/// A request's saved ids: each client id to the stored one.
#[derive(Default, Clone)]
struct Saved {
    analysis_id: Option<String>,
    groups: HashMap<String, String>,
    samples: HashMap<String, String>,
}

/// (root, each context's bracketing Boundaries, the dates as solved)
/// (`_tree_from_request`).
type FromRequest = (Value, Vec<(String, (String, String))>, Vec<Measurement>);

fn tree_from_request(r: &BayesianRequest) -> FromRequest {
    let mut dates: Vec<Measurement> = r.measurements.iter().map(|m| m.core()).collect();
    if let Some(tree) = &r.model_tree {
        return (tree.clone(), Vec::new(), dates);
    }
    let migrated =
        migrate_from_relationships(&dates, &r.groups, &r.relationships, radical_store::new_id);
    let mut root = migrated_json(&migrated.root);
    for d in &mut dates {
        d.element_id = d.group_id.clone();
    }
    if dates.iter().any(|d| d.element_id.is_none()) {
        let loose_id = radical_store::new_id();
        root["children"].as_array_mut().unwrap().push(json!({
            "id": loose_id, "element_type": "phase", "name": "Unassigned dates",
            "tpq_age_bp": null, "taq_age_bp": null, "children": [],
        }));
        for d in dates.iter_mut().filter(|d| d.element_id.is_none()) {
            d.element_id = Some(loose_id.clone());
        }
    }
    (root, migrated.group_boundaries, dates)
}

/// A context's modelled start and end.
struct Context {
    id: String,
    name: Option<String>,
    start: Posterior,
    end: Posterior,
}

fn context_json(c: &Context, samples: bool) -> Value {
    json!({
        "name": c.name,
        "start": posterior_json(&c.start, samples),
        "end": posterior_json(&c.end, samples),
    })
}

/// Solve the request's model and, when it is saved, write what the run
/// produced (`_solve_and_store`).
fn solve_and_store(
    state: &State,
    r: &BayesianRequest,
    saved: &Saved,
    handle: &Handle,
) -> Result<(Solved, Vec<Context>), Failed> {
    if let Some(id) = &saved.analysis_id
        && state
            .store
            .read(|c| rows::get::<Analysis>(c, id))?
            .is_none()
    {
        return Err(Failed::refused(format!("Analysis '{id}' no longer exists")));
    }
    let (mut root, group_boundaries, dates) = tree_from_request(r);
    let mut progress = job_progress(handle);
    let solved = solve::run_bayesian(
        state,
        &dates,
        &root,
        &r.bayesian_config,
        Some(&mut progress),
    )?;
    let contexts: Vec<Context> = group_boundaries
        .iter()
        .filter_map(|(group_id, (start, end))| {
            Some(Context {
                id: group_id.clone(),
                name: r
                    .groups
                    .iter()
                    .find(|g| &g.id == group_id)
                    .and_then(|g| g.name.clone()),
                start: solved.element(start)?.clone(),
                end: solved.element(end)?.clone(),
            })
        })
        .collect();
    let Some(analysis_id) = &saved.analysis_id else {
        return Ok((solved, contexts));
    };

    solve::relabel_tree(&mut root, &saved.groups, &saved.samples);
    set_posteriors(&mut root, &solved);
    let element_by_date: HashMap<&str, Option<&String>> = dates
        .iter()
        .map(|d| (d.id.as_str(), d.element_id.as_ref()))
        .collect();
    let mut ids = saved.groups.clone();
    ids.extend(saved.samples.iter().map(|(k, v)| (k.clone(), v.clone())));
    let intervals = solve::relabel_intervals(solved.intervals.clone(), &ids);
    let summaries = solve::relabel_summaries(solved.summaries.clone(), &saved.groups);
    state.store.write(|tx| {
        for (date_id, p) in &solved.results {
            let sample_id = saved.samples.get(date_id).unwrap_or(date_id);
            rows::insert(tx, &result_row(analysis_id, sample_id, p))?;
        }
        for c in &contexts {
            let Some(stored) = saved.groups.get(&c.id) else {
                continue;
            };
            if let Some(mut g) = rows::get::<SampleGroup>(tx, stored)? {
                g.modeled_start = Some(posterior_json(&c.start, true));
                g.modeled_end = Some(posterior_json(&c.end, true));
                rows::update(tx, &g)?;
            }
        }
        persist_tree(tx, analysis_id, &root)?;
        for m in &r.measurements {
            let Some(sample_id) = saved.samples.get(&m.id) else {
                continue;
            };
            let element_id = if r.model_tree.is_some() {
                m.element_id.clone()
            } else {
                element_by_date
                    .get(m.id.as_str())
                    .copied()
                    .flatten()
                    .map(|assigned| saved.groups.get(assigned).unwrap_or(assigned).clone())
            };
            if let Some(mut s) = rows::get::<RadiocarbonSample>(tx, sample_id)? {
                s.element_id = element_id;
                rows::update(tx, &s)?;
            }
        }
        if let Some(mut a) = rows::get::<Analysis>(tx, analysis_id)? {
            a.solver = Some(solved.solver.clone());
            a.intervals = intervals.clone();
            a.summaries = summaries.clone();
            a.status = Some("completed".to_owned());
            a.updated_at = Some(Timestamp::now());
            rows::update(tx, &a)?;
        }
        Ok(())
    })?;
    history::record_run(state, analysis_id);
    Ok((solved, contexts))
}

/// Each element's posterior onto the tree: every element the run reported
/// one for, and every Boundary (cleared where the run reported none).
fn set_posteriors(e: &mut Value, solved: &Solved) {
    let found = e["id"].as_str().and_then(|id| solved.element(id));
    if let Some(p) = found {
        e["posterior"] = posterior_json(p, true);
    } else if e["element_type"] == "boundary" {
        e["posterior"] = Value::Null;
    }
    if let Some(children) = e["children"].as_array_mut() {
        for c in children {
            set_posteriors(c, solved);
        }
    }
}

/// Save a Bayesian model's inputs and solve it: queued (202 and a job to
/// poll) unless asked to wait or not saved, when the answer is the run.
async fn bayesian(
    Extract(state): Extract<State>,
    caller: Caller,
    Query(q): Query<HashMap<String, String>>,
    body: Bytes,
) -> Answer {
    let wait = wait_flag(&q)?;
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "BayesianRequest", &mut problems);
    let parsed = schemas::bayesian_request(&mut f, &state.store);
    problems.finish()?;
    let r = parsed.expect("validated");
    let project_id = if r.save {
        state
            .store
            .read(|c| Ok(validate_project_id(c, &caller, r.project_id.as_deref())))??
    } else {
        None
    };
    authorise_curve_ids(
        &state,
        &caller,
        r.measurements.iter().map(|d| d.m.curve_id.as_str()),
    )?;

    let mut saved = Saved::default();
    if r.save {
        let now = Some(Timestamp::now());
        let analysis = Analysis {
            id: radical_store::new_id(),
            name: r.name.clone(),
            analysis_type: "bayesian".to_owned(),
            status: Some("running".to_owned()),
            created_at: now,
            updated_at: now,
            project_id: project_id.clone(),
            bayesian_config: Some(r.bayesian_config.clone()),
            owner_id: caller.owner().map(str::to_owned),
            ..Analysis::default()
        };
        state.store.write(|tx| {
            rows::insert(tx, &analysis)?;
            for g in &r.groups {
                let row = SampleGroup {
                    id: radical_store::new_id(),
                    analysis_id: analysis.id.clone(),
                    name: g.name.clone().unwrap_or_default(),
                    tpq_age_bp: g.tpq_age_bp,
                    taq_age_bp: g.taq_age_bp,
                    created_at: Some(Timestamp::now()),
                    ..SampleGroup::default()
                };
                rows::insert(tx, &row)?;
                saved.groups.insert(g.id.clone(), row.id);
            }
            for (position, m) in r.measurements.iter().enumerate() {
                let group = m
                    .group_id
                    .as_ref()
                    .and_then(|g| saved.groups.get(g).cloned());
                let row = sample_row(&analysis.id, m, group, None, position as i64);
                rows::insert(tx, &row)?;
                saved.samples.insert(m.id.clone(), row.id);
            }
            for rel in &r.relationships {
                let resolve = |id: &str, kind: &Option<String>| {
                    if kind.as_deref() == Some("group") {
                        saved.groups[id].clone()
                    } else {
                        saved.samples[id].clone()
                    }
                };
                rows::insert(
                    tx,
                    &StratigraphicRelationship {
                        id: radical_store::new_id(),
                        analysis_id: analysis.id.clone(),
                        from_sample_id: resolve(&rel.from_sample_id, &rel.from_type),
                        to_sample_id: resolve(&rel.to_sample_id, &rel.to_type),
                        from_type: rel.from_type.clone().unwrap_or_default(),
                        to_type: rel.to_type.clone().unwrap_or_default(),
                        relationship: rel.relationship.clone(),
                        created_at: Some(Timestamp::now()),
                    },
                )?;
            }
            Ok(())
        })?;
        saved.analysis_id = Some(analysis.id);
    }

    if r.save && !wait {
        let analysis_id = saved.analysis_id.clone().unwrap();
        set_status(&state, &analysis_id, "pending", None);
        let (worker, request, ids) = (state.clone(), r.clone(), saved.clone());
        let target = analysis_id.clone();
        let job_id = state.jobs.submit(
            "analysis_run",
            Some(&analysis_id),
            caller.owner(),
            Box::new(move |handle| {
                set_status(&worker, &target, "running", None);
                match solve_and_store(&worker, &request, &ids, handle) {
                    Ok((solved, _)) => Ok(Some(json!({
                        "analysis_id": target,
                        "solver": solved.solver["method"],
                    }))),
                    Err(Failed::Cancelled) => {
                        // Nothing was written: back to never having been run.
                        set_status(&worker, &target, "pending", None);
                        Err(radical_jobs::JobError::Cancelled)
                    }
                    Err(failure) => {
                        let error = failure.job_error();
                        let message = radical_jobs::classify(&error).message;
                        set_status(&worker, &target, "failed", Some(message));
                        Err(error)
                    }
                }
            }),
        )?;
        return Ok(answer(
            StatusCode::ACCEPTED,
            json!({
                "id": analysis_id,
                "name": r.name,
                "analysis_type": "bayesian",
                "status": "queued",
                "saved": true,
                "project_id": project_id,
                "job_id": job_id,
                "measurements_count": r.measurements.len(),
            }),
        ));
    }

    let request = Arc::new(r);
    let (worker, run, ids) = (state.clone(), Arc::clone(&request), saved.clone());
    let outcome = blocking(move || solve_and_store(&worker, &run, &ids, &Handle::null())).await;
    // A failed run leaves its inputs saved and the analysis as it was left,
    // as the backend's rollback after its commits does.
    let (solved, contexts) = outcome.map_err(|e| ApiError::bad_request(e.message()))?;
    let results: serde_json::Map<String, Value> = solved
        .results
        .iter()
        .map(|(id, p)| (id.clone(), solved.result_json(p)))
        .collect();
    let contexts: serde_json::Map<String, Value> = contexts
        .iter()
        .map(|c| (c.id.clone(), context_json(c, false)))
        .collect();
    Ok(answer(
        StatusCode::OK,
        json!({
            "id": saved.analysis_id,
            "name": request.name,
            "analysis_type": "bayesian",
            "status": "completed",
            "saved": request.save,
            "project_id": if request.save { project_id } else { None },
            "results": results,
            "contexts": contexts,
            "solver": solved.solver,
            "intervals": solved.intervals,
            "summaries": solved.summaries,
            "measurements_count": request.measurements.len(),
        }),
    ))
}

// --------------------------------------------------------------------------
// Re-running a saved analysis
// --------------------------------------------------------------------------

/// Recompute a saved analysis's results from its own stored inputs, writing
/// only the results, and only on success (`_rerun_analysis`).
pub fn rerun(state: &State, analysis_id: &str, handle: &Handle) -> Result<(), Failed> {
    let analysis = state
        .store
        .read(|c| rows::get::<Analysis>(c, analysis_id))?
        .ok_or_else(|| Failed::refused(format!("Analysis '{analysis_id}' no longer exists")))?;
    let samples = state.store.read(|c| measurements(c, analysis_id))?;
    if samples.is_empty() {
        return Err(Failed::refused("Analysis has no measurements to rerun"));
    }
    let config = analysis
        .bayesian_config
        .clone()
        .filter(|c| !c.is_null())
        .unwrap_or_else(|| json!({}));
    match analysis.analysis_type.as_str() {
        "calibration" | "group_calibration" => {
            let mut rows_out = Vec::new();
            for s in &samples {
                let curve = state
                    .curves
                    .curve(s.curve_id.as_deref().unwrap_or_default())?;
                let stats =
                    calibration_stats(curve, s.age_bp, s.error_1sigma, s.delta_r, s.delta_r_error)?;
                rows_out.push(calibration_result_row(analysis_id, &s.id, &stats));
            }
            let flag = |k: &str| config.get(k).and_then(Value::as_bool).unwrap_or(false);
            let intervals = if flag("estimate_durations") && samples.len() > 1 {
                Some(crate::blocks::date_differences(state, &samples)?)
            } else {
                None
            };
            let summaries = if flag("estimate_kde") {
                crate::blocks::date_kde(state, &samples)?
            } else {
                None
            };
            state.store.write(|tx| {
                let existing: Vec<AnalysisResult> =
                    rows::select_where(tx, "analysis_id = ?", [analysis_id], "rowid")?;
                for mut row in rows_out {
                    match existing.iter().find(|e| e.sample_id == row.sample_id) {
                        Some(e) => {
                            row.id = e.id.clone();
                            row.created_at = e.created_at;
                            rows::update(tx, &row)?;
                        }
                        None => rows::insert(tx, &row)?,
                    }
                }
                finish(tx, analysis_id, |a| {
                    a.intervals = intervals.clone();
                    a.summaries = summaries.clone();
                })
            })?;
        }
        "bayesian" => {
            let (root, _) = tree_or_migrate(state, analysis_id)?;
            let Some(root) = root else {
                return Err(Failed::refused("Analysis has no model structure to run"));
            };
            // Read again: a first migration has just filed each date.
            let samples = state.store.read(|c| measurements(c, analysis_id))?;
            let dates: Vec<Measurement> = samples.iter().map(solve::stored_measurement).collect();
            let mut progress = job_progress(handle);
            let solved = solve::run_bayesian(state, &dates, &root, &config, Some(&mut progress))?;

            let children = root["children"].as_array().cloned().unwrap_or_default();
            let mut contexts: HashMap<String, (Value, Value)> = HashMap::new();
            for (i, child) in children.iter().enumerate() {
                if child["element_type"] != "phase" {
                    continue;
                }
                let bound = |e: Option<&Value>| {
                    e.filter(|e| e["element_type"] == "boundary")
                        .and_then(|e| solved.element(e["id"].as_str()?))
                };
                let start = bound(i.checked_sub(1).and_then(|j| children.get(j)));
                let end = bound(children.get(i + 1));
                if let (Some(s), Some(e), Some(id)) = (start, end, child["id"].as_str()) {
                    contexts.insert(
                        id.to_owned(),
                        (posterior_json(s, true), posterior_json(e, true)),
                    );
                }
            }
            state.store.write(|tx| {
                let elements: Vec<ModelElement> =
                    rows::select_where(tx, "analysis_id = ?", [analysis_id], "rowid")?;
                for mut e in elements {
                    let found = solved.element(&e.id);
                    if found.is_some()
                        || matches!(
                            e.element_type.as_str(),
                            "boundary" | "wiggle_sequence" | "interpolated_depth"
                        )
                    {
                        e.posterior = found.map(|p| posterior_json(p, true));
                        rows::update(tx, &e)?;
                    }
                }
                rows::delete_where::<AnalysisResult>(tx, "analysis_id = ?", [analysis_id])?;
                for (sample_id, p) in &solved.results {
                    rows::insert(tx, &result_row(analysis_id, sample_id, p))?;
                }
                let groups: Vec<SampleGroup> =
                    rows::select_where(tx, "analysis_id = ?", [analysis_id], "rowid")?;
                for mut g in groups {
                    let ctx = contexts.get(&g.id);
                    g.modeled_start = ctx.map(|c| c.0.clone());
                    g.modeled_end = ctx.map(|c| c.1.clone());
                    rows::update(tx, &g)?;
                }
                finish(tx, analysis_id, |a| {
                    a.solver = Some(solved.solver.clone());
                    a.intervals = solved.intervals.clone();
                    a.summaries = solved.summaries.clone();
                })
            })?;
        }
        other => {
            return Err(Failed::refused(format!("Unknown analysis_type '{other}'")));
        }
    }
    history::record_run(state, analysis_id);
    Ok(())
}

/// The analysis marked completed, with `change` applied.
fn finish(
    tx: &radical_store::rusqlite::Connection,
    analysis_id: &str,
    change: impl FnOnce(&mut Analysis),
) -> radical_store::Result<()> {
    if let Some(mut a) = rows::get::<Analysis>(tx, analysis_id)? {
        change(&mut a);
        a.status = Some("completed".to_owned());
        a.error_message = None;
        a.updated_at = Some(Timestamp::now());
        rows::update(tx, &a)?;
    }
    Ok(())
}

/// A failed re-run, recorded on the analysis: what it stores and shows.
fn record_failure(state: &State, analysis_id: &str, failure: Failed) -> String {
    let message = failure.classified();
    set_status(state, analysis_id, "failed", Some(message.clone()));
    message
}

/// Recompute a saved analysis in place: queued, or run here with
/// `wait=true`.
async fn rerun_analysis(
    Extract(state): Extract<State>,
    caller: Caller,
    Path(analysis_id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Answer {
    let wait = wait_flag(&q)?;
    state
        .store
        .read(|c| Ok(require(c, &caller, &analysis_id)))??;
    if wait {
        let (worker, id) = (state.clone(), analysis_id.clone());
        let outcome = blocking(move || rerun(&worker, &id, &Handle::null())).await;
        if let Err(failure) = outcome {
            let message = record_failure(&state, &analysis_id, failure);
            return Err(ApiError::bad_request(message));
        }
        let status = state
            .store
            .read(|c| rows::get::<Analysis>(c, &analysis_id))?
            .and_then(|a| a.status);
        return Ok(answer(
            StatusCode::OK,
            json!({"id": analysis_id, "status": status}),
        ));
    }
    set_status(&state, &analysis_id, "pending", None);
    let (worker, target) = (state.clone(), analysis_id.clone());
    let job_id = state.jobs.submit_undoable(
        "analysis_run",
        Some(&analysis_id),
        caller.owner(),
        analysis_run_work(&state, &analysis_id),
        Some(Box::new(move || status_after_cancel(&worker, &target))),
    )?;
    Ok(answer(
        StatusCode::ACCEPTED,
        json!({"id": analysis_id, "status": "queued", "job_id": job_id}),
    ))
}

/// A cancelled re-run, whether it was running or never started: only a
/// finished run writes, so the analysis keeps the results it already had.
fn status_after_cancel(state: &State, analysis_id: &str) {
    let has_results = state
        .store
        .read(|c| {
            rows::select_where::<AnalysisResult>(c, "analysis_id = ?", [analysis_id], "rowid")
        })
        .is_ok_and(|r| !r.is_empty());
    let status = if has_results { "completed" } else { "pending" };
    set_status(state, analysis_id, status, None);
}

/// An 'analysis_run' job's work (`_run_analysis_job`): solve a saved
/// analysis from its own inputs, recording failure or cancellation on it.
pub fn analysis_run_work(state: &State, analysis_id: &str) -> radical_jobs::Work {
    let (worker, target) = (state.clone(), analysis_id.to_owned());
    Box::new(move |handle| {
        if worker
            .store
            .read(|c| rows::get::<Analysis>(c, &target))?
            .is_none()
        {
            return Err(
                Failed::refused(format!("Analysis '{target}' no longer exists")).job_error(),
            );
        }
        set_status(&worker, &target, "running", None);
        match rerun(&worker, &target, handle) {
            Ok(()) => {
                let solver = worker
                    .store
                    .read(|c| rows::get::<Analysis>(c, &target))?
                    .and_then(|a| a.solver)
                    .map_or(Value::Null, |s| s["method"].clone());
                Ok(Some(json!({"analysis_id": target, "solver": solver})))
            }
            Err(Failed::Cancelled) => {
                status_after_cancel(&worker, &target);
                Err(radical_jobs::JobError::Cancelled)
            }
            Err(failure) => {
                let error = failure.job_error();
                let message = radical_jobs::classify(&error).message;
                set_status(&worker, &target, "failed", Some(message));
                Err(error)
            }
        }
    })
}

/// Every analysis in a project, in turn: (rerun count, failures).
fn rerun_all(
    state: &State,
    caller: &Caller,
    project_id: &str,
    handle: &Handle,
) -> Result<(usize, Vec<Value>, usize), radical_jobs::JobError> {
    let mine = crate::owner::Scope::of(caller).sql("owner_id");
    let analyses: Vec<Analysis> = state.store.read(|c| {
        rows::select_where(
            c,
            &format!("project_id = ? AND {mine}"),
            [project_id],
            "rowid",
        )
    })?;
    let total = analyses.len();
    let mut rerun_count = 0;
    let mut failed: Vec<Value> = Vec::new();
    handle.update(
        Some(0.0),
        Some(json!({"completed": 0, "total": total, "failed": []})),
        true,
    );
    for a in &analyses {
        handle.check_cancelled()?;
        match rerun(state, &a.id, &Handle::null()) {
            Ok(()) => rerun_count += 1,
            Err(failure) => {
                let message = record_failure(state, &a.id, failure);
                failed.push(json!({"id": a.id, "name": a.name, "error": message}));
            }
        }
        let done = rerun_count + failed.len();
        handle.update(
            Some(if total > 0 {
                done as f64 / total as f64
            } else {
                1.0
            }),
            Some(json!({"completed": done, "total": total, "failed": failed})),
            true,
        );
    }
    Ok((rerun_count, failed, total))
}

/// Recompute every analysis in a project: queued, or run here with
/// `wait=true`. One failure does not stop the rest.
async fn rerun_project(
    Extract(state): Extract<State>,
    caller: Caller,
    Path(project_id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Answer {
    let wait = wait_flag(&q)?;
    crate::routes::projects::require(&state, &caller, &project_id)?;
    if wait {
        let (worker, id, who) = (state.clone(), project_id.clone(), caller.clone());
        let outcome = blocking(move || rerun_all(&worker, &who, &id, &Handle::null())).await;
        let (rerun_count, failed, _) =
            outcome.map_err(|e| ApiError::internal(radical_jobs::classify(&e).message))?;
        return Ok(answer(
            StatusCode::OK,
            json!({"rerun_count": rerun_count, "failed": failed}),
        ));
    }
    let (worker, target, who) = (state.clone(), project_id.clone(), caller.clone());
    let job_id = state.jobs.submit(
        "project_rerun",
        Some(&project_id),
        caller.owner(),
        Box::new(move |handle| {
            let (rerun_count, failed, total) = rerun_all(&worker, &who, &target, handle)?;
            Ok(Some(json!({
                "rerun_count": rerun_count,
                "total": total,
                "failed": failed,
                "completed": rerun_count + failed.len(),
            })))
        }),
    )?;
    Ok(answer(
        StatusCode::ACCEPTED,
        json!({"project_id": project_id, "status": "queued", "job_id": job_id}),
    ))
}

pub fn router() -> Router<State> {
    Router::new()
        .route("/api/analysis/bayesian", post(bayesian))
        .route("/api/analysis/{analysis_id}/rerun", post(rerun_analysis))
        .route("/api/projects/{project_id}/rerun", post(rerun_project))
}
