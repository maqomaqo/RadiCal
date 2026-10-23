//! Influence (`api/influence.py`): the model re-solved with each date left
//! out, moved to the neighbouring Phase, or treated as an outlier, in one
//! job, and kept as one report on the analysis. Any variant can be opened as
//! a real analysis. Also the one spatial model that needs no run
//! (`api/dispersal.py`): an order for a draft's sites.

use axum::Router;
use axum::body::Bytes;
use axum::extract::{Path, State as Extract};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::post;
use radical_core::dispersal::{derive_sites, propose_sequence};
use radical_core::exact_chain::Posterior;
use radical_core::influence::{
    self, DEFAULT_OUTLIER_MODEL, DurationQuantity, KINDS, Quantity, Reduced, Variant,
};
use radical_core::model_tree::Element;
use radical_jobs::{Handle, JobError};
use radical_store::Timestamp;
use radical_store::rows::{self, Analysis, RadiocarbonSample, StratigraphicRelationship};
use serde_json::{Map, Value, json};

use crate::blocks::proposal_json;
use crate::history::inputs_hash;
use crate::routes::analysis::{measurements, require, tree_or_migrate};
use crate::routes::bayesian::analysis_run_work;
use crate::routes::inputs::copy_inputs;
use crate::solve::{self, Failed, Solved, element, json_measurement, stored_measurement};
use crate::validate::{self, Bounds, Problems};
use crate::{ApiError, ApiResult, State, ok};

/// An influence job's target: not the analysis's own id, which the Results
/// page watches for runs of the analysis.
fn job_target(analysis_id: &str) -> String {
    format!("influence:{analysis_id}")
}

/// The analysis's settings with durations on and the KDE and dispersal off
/// (`_run_config`).
fn run_config(config: &Option<Value>) -> Value {
    let mut c = config
        .as_ref()
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    c.insert("estimate_durations".into(), json!(true));
    c.insert("estimate_kde".into(), json!(false));
    c.insert("estimate_dispersal".into(), json!(false));
    Value::Object(c)
}

fn variant_json(v: &Variant) -> Map<String, Value> {
    let mut out = Map::new();
    out.insert("id".into(), json!(v.id));
    out.insert("kind".into(), json!(v.kind));
    out.insert("date_id".into(), json!(v.date_id));
    out.insert("date_name".into(), json!(v.date_name));
    if v.kind == "move" {
        out.insert("to_phase_id".into(), json!(v.to_phase_id));
        out.insert("to_phase_name".into(), json!(v.to_phase_name));
        out.insert("direction".into(), json!(v.direction));
    }
    out.insert("label".into(), json!(v.label));
    out
}

fn reduced_json(r: &Reduced, with_kind: bool) -> Value {
    let mut out = json!({
        "name": r.name, "median": r.median, "hpd_95": r.hpd_95,
        "distribution": r.distribution,
    });
    if with_kind {
        out["kind"] = json!(r.kind);
    }
    out
}

/// One run reduced to what the influence table reads (`summarise`).
fn summarise(solved: &Solved) -> Value {
    let series: Vec<(&String, &Posterior)> =
        solved.elements.iter().map(|(id, p)| (id, p)).collect();
    let boundaries: Vec<(&str, Quantity)> = series
        .iter()
        .map(|(id, p)| {
            (
                id.as_str(),
                Quantity {
                    name: solved.names.get(*id).map(String::as_str),
                    median: Some(p.posterior_median),
                    hpd_95: Some(p.hpd_95),
                    distribution: &p.probability_distribution,
                },
            )
        })
        .collect();
    let rows = solved
        .intervals
        .as_ref()
        .and_then(|i| i["durations"].as_array().cloned())
        .unwrap_or_default();
    let distributions: Vec<Vec<[f64; 2]>> = rows
        .iter()
        .map(|r| {
            r["years"]["distribution"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|p| {
                    [
                        p[0].as_f64().unwrap_or(f64::NAN),
                        p[1].as_f64().unwrap_or(f64::NAN),
                    ]
                })
                .collect()
        })
        .collect();
    let durations: Vec<DurationQuantity> = rows
        .iter()
        .zip(&distributions)
        .map(|(r, d)| DurationQuantity {
            kind: r["kind"].as_str().unwrap_or_default(),
            id: r["id"].as_str().unwrap_or_default(),
            element_id: r["element_id"].as_str().filter(|e| !e.is_empty()),
            quantity: Quantity {
                name: r["name"].as_str(),
                median: r["years"]["median"].as_f64(),
                hpd_95: r["years"]["hpd_95"]
                    .as_array()
                    .and_then(|h| Some((h.first()?.as_f64()?, h.get(1)?.as_f64()?))),
                distribution: d,
            },
        })
        .collect();
    let (b, d) = influence::summarise(&boundaries, &durations);
    json!({
        "boundaries": b.iter().map(|(k, r)| (k.clone(), reduced_json(r, false))).collect::<Map<_, _>>(),
        "durations": d.iter().map(|(k, r)| (k.clone(), reduced_json(r, true))).collect::<Map<_, _>>(),
    })
}

/// The influence job's work (`_influence_job`).
fn influence_work(
    state: &State,
    analysis_id: &str,
    kinds: Vec<&'static str>,
) -> radical_jobs::Work {
    let (state, analysis_id) = (state.clone(), analysis_id.to_owned());
    Box::new(move |handle: &Handle| {
        let analysis: Analysis = state
            .store
            .read(|c| rows::get(c, &analysis_id))?
            .ok_or_else(|| {
                Failed::refused(format!("Analysis '{analysis_id}' no longer exists")).job_error()
            })?;
        let (tree, _) = tree_or_migrate(&state, &analysis_id)?;
        let tree = tree.unwrap_or(Value::Null);
        let samples = state.store.read(|c| measurements(c, &analysis_id))?;
        let dates: Vec<_> = samples.iter().map(stored_measurement).collect();
        let config = run_config(&analysis.bayesian_config);
        let variants = influence::plan(&dates, &element(&tree), &kinds);
        let total = variants.len() + 1;
        let solve = |label: &str, done: usize, dates: &[radical_core::Measurement]| {
            handle.update(
                Some(done as f64 / total as f64),
                Some(json!({"done": done, "total": total, "current": label})),
                true,
            );
            if handle.cancelled() {
                return Err(Failed::Cancelled);
            }
            let mut keep_going = |_: &radical_core::gibbs::chain::Progress| !handle.cancelled();
            let solved = solve::run_bayesian(&state, dates, &tree, &config, Some(&mut keep_going))?;
            Ok((summarise(&solved), solved.solver["method"].clone()))
        };
        let (baseline, method) =
            solve("the model as it is", 0, &dates).map_err(Failed::job_error)?;
        let mut rows_out = Vec::new();
        for (done, v) in variants.iter().enumerate() {
            let outcome = influence::apply(v, &dates)
                .map_err(Failed::from)
                .and_then(|varied| solve(&v.label, done + 1, &varied));
            let mut row = variant_json(v);
            match outcome {
                Ok((summary, m)) => {
                    row.insert("summary".into(), summary);
                    row.insert("solver".into(), m);
                    row.insert("error".into(), Value::Null);
                }
                Err(Failed::Cancelled) => return Err(JobError::Cancelled),
                Err(failure) => {
                    row.insert("summary".into(), Value::Null);
                    row.insert("solver".into(), Value::Null);
                    row.insert("error".into(), json!(failure.classified()));
                }
            }
            rows_out.push(Value::Object(row));
        }
        let failed = rows_out.iter().filter(|r| !r["error"].is_null()).count();
        let count = rows_out.len();
        state.store.write(|tx| {
            if let Some(mut a) = rows::get::<Analysis>(tx, &analysis_id)? {
                a.influence = Some(json!({
                    "created_at": Timestamp::now().isoformat(),
                    "inputs_hash": inputs_hash(tx, &a)?,
                    "kinds": kinds,
                    "solver": method,
                    "baseline": baseline,
                    "variants": rows_out,
                }));
                rows::update(tx, &a)?;
            }
            Ok(())
        })?;
        Ok(Some(
            json!({"analysis_id": analysis_id, "variants": count, "failed": failed}),
        ))
    })
}

fn accepted(data: Value) -> Response {
    (
        StatusCode::ACCEPTED,
        axum::Json(json!({"success": true, "data": data})),
    )
        .into_response()
}

/// Queue an influence run over the variants `kinds` asks for.
async fn run_influence(
    Extract(state): Extract<State>,
    Path(analysis_id): Path<String>,
    body: Bytes,
) -> ApiResult<Response> {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "InfluenceRequest", &mut problems);
    let raw = f.raw("kinds").cloned();
    let kinds = f.str_list("kinds", Some(1), None);
    if let (Some(list), Some(Value::Array(items))) = (&kinds, &raw) {
        for (i, k) in list.iter().enumerate() {
            if !KINDS.contains(&k.as_str()) {
                problems.0.push(validate::problem(
                    "literal_error",
                    &[json!("body"), json!("kinds"), json!(i)],
                    "Input should be 'leave_out', 'move' or 'outlier'",
                    items[i].clone(),
                    Some(json!({"expected": "'leave_out', 'move' or 'outlier'"})),
                ));
            }
        }
    }
    problems.finish()?;
    let wanted = kinds.unwrap_or_default();
    let analysis = state.store.read(|c| Ok(require(c, &analysis_id)))??;
    if analysis.analysis_type != "bayesian" {
        return Err(ApiError::bad_request(
            "Influence is tested on a Bayesian model.",
        ));
    }
    if analysis.status.as_deref() != Some("completed") {
        return Err(ApiError::bad_request(
            "Run the analysis first; its influence is tested against its result.",
        ));
    }
    let (tree, _) = tree_or_migrate(&state, &analysis_id)?;
    let Some(tree) = tree else {
        return Err(ApiError::bad_request("This analysis has no model to vary."));
    };
    let kinds: Vec<&'static str> = KINDS
        .iter()
        .copied()
        .filter(|k| wanted.iter().any(|w| w == k))
        .collect();
    let samples = state.store.read(|c| measurements(c, &analysis_id))?;
    let dates: Vec<_> = samples.iter().map(stored_measurement).collect();
    let planned = influence::plan(&dates, &element(&tree), &kinds);
    if planned.is_empty() {
        return Err(ApiError::bad_request(
            "None of these changes applies to any date in this model.",
        ));
    }
    let job_id = state.jobs.submit(
        "influence",
        Some(&job_target(&analysis_id)),
        influence_work(&state, &analysis_id, kinds),
    )?;
    Ok(accepted(json!({
        "id": analysis_id, "job_id": job_id, "variants": planned.len(),
    })))
}

/// The last influence report, or null, and whether the analysis has changed
/// since it was made.
async fn get_influence(
    Extract(state): Extract<State>,
    Path(analysis_id): Path<String>,
) -> ApiResult<axum::Json<Value>> {
    let analysis = state.store.read(|c| Ok(require(c, &analysis_id)))??;
    let report = analysis.influence.clone().unwrap_or(Value::Null);
    let stale = match report.as_object() {
        Some(r) if !r.is_empty() => {
            let now = state.store.read(|c| inputs_hash(c, &analysis))?;
            r.get("inputs_hash").and_then(Value::as_str) != Some(now.as_str())
        }
        _ => false,
    };
    let latest = radical_jobs::latest_for(&state.store, &job_target(&analysis_id))?;
    let running = latest.filter(|j| j.state == "queued" || j.state == "running");
    Ok(axum::Json(json!({
        "success": true,
        "data": report,
        "stale": stale,
        "job_id": running.map(|j| j.id),
    })))
}

/// Make one variant of the last report a saved analysis in the same
/// project, and queue its run.
async fn open_variant(
    Extract(state): Extract<State>,
    Path(analysis_id): Path<String>,
    body: Bytes,
) -> ApiResult<Response> {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "OpenVariantRequest", &mut problems);
    let variant_id = f.string("variant_id", None, None);
    problems.finish()?;
    let variant_id = variant_id.unwrap_or_default();
    let source = state.store.read(|c| Ok(require(c, &analysis_id)))??;
    let report = source.influence.clone().unwrap_or(Value::Null);
    let Some(variant) = report["variants"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|v| v["id"] == variant_id.as_str())
        .cloned()
    else {
        return Err(ApiError::not_found("That variant was not found."));
    };
    let kind = variant["kind"].as_str().unwrap_or_default().to_owned();
    let date_id = variant["date_id"].as_str().unwrap_or_default().to_owned();
    let to_phase = variant["to_phase_id"].as_str().map(str::to_owned);
    let name = format!(
        "{} ({})",
        source.name,
        variant["label"].as_str().unwrap_or_default()
    );
    let conflict = || {
        ApiError::conflict(
            "The analysis has changed since this report, so the variant no longer applies. Test its influence again.",
        )
    };
    let opened = state.store.write(|tx| {
        // Checked before anything is copied: the copy is all or nothing.
        let samples = measurements(tx, &source.id)?;
        let elements: Vec<rows::ModelElement> =
            rows::select_where(tx, "analysis_id = ?", [&source.id], "rowid")?;
        let groups: Vec<rows::SampleGroup> =
            rows::select_where(tx, "analysis_id = ?", [&source.id], "rowid")?;
        let has_date = samples.iter().any(|s| s.id == date_id);
        let has_phase = to_phase.as_ref().is_some_and(|p| {
            elements.iter().any(|e| &e.id == p) || groups.iter().any(|g| &g.id == p)
        });
        if !has_date || (kind == "move" && !has_phase) {
            return Ok(None);
        }
        let (copy, samples_map, element_map) =
            copy_inputs(tx, &source, &name, source.bayesian_config.clone())?;
        let copied = samples_map[&date_id].clone();
        let mut row: RadiocarbonSample = rows::get(tx, &copied)?.expect("just copied");
        match kind.as_str() {
            "leave_out" => {
                rows::delete_where::<StratigraphicRelationship>(
                    tx,
                    "analysis_id = ? AND (from_sample_id = ? OR to_sample_id = ?)",
                    [&copy.id, &copied, &copied],
                )?;
                rows::delete_where::<RadiocarbonSample>(tx, "id = ?", [&copied])?;
            }
            "move" => {
                row.element_id = to_phase.as_ref().and_then(|p| element_map.get(p)).cloned();
                rows::update(tx, &row)?;
            }
            _ => {
                row.outlier_prior = Some(1.0);
                row.outlier_model = row
                    .outlier_model
                    .clone()
                    .filter(|m| !m.is_empty())
                    .or_else(|| Some(DEFAULT_OUTLIER_MODEL.to_owned()));
                rows::update(tx, &row)?;
            }
        }
        Ok(Some(copy))
    })?;
    let Some(opened) = opened else {
        return Err(conflict());
    };
    let job_id = state.jobs.submit(
        "analysis_run",
        Some(&opened.id),
        analysis_run_work(&state, &opened.id),
    )?;
    Ok(accepted(
        json!({"id": opened.id, "name": opened.name, "job_id": job_id}),
    ))
}

/// An order for a draft model's sites, read off a minimum spanning tree: a
/// proposal, never applied.
async fn propose(Extract(_state): Extract<State>, body: Bytes) -> ApiResult {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "ProposeSequenceRequest", &mut problems);
    let tree = if f.raw("model_tree").is_none() {
        let _ = f.string("model_tree", None, None);
        None
    } else {
        f.opt_dict("model_tree").map(Value::Object)
    };
    let dates: Vec<Value> = match f.raw("measurements") {
        None => Vec::new(),
        Some(Value::Array(items)) => items.clone(),
        Some(other) => {
            let other = other.clone();
            problems.0.push(validate::problem(
                "list_type",
                &[json!("body"), json!("measurements")],
                "Input should be a valid list",
                other,
                None,
            ));
            Vec::new()
        }
    };
    let mut f = validate::body(&input, "ProposeSequenceRequest", &mut problems);
    let origin = f.model("origin", "DispersalOrigin", true, |o| {
        let lat = o.float("lat", Bounds::range(-90.0, 90.0));
        let lon = o.float("lon", Bounds::range(-180.0, 180.0));
        Some((lat?, lon?))
    });
    problems.finish()?;
    let tree = tree.unwrap_or_default();
    let measurements: Vec<_> = dates.iter().map(json_measurement).collect();
    let root: Element = element(&tree);
    let sites = derive_sites(Some(&root), &measurements);
    Ok(ok(proposal_json(&propose_sequence(
        &sites,
        origin.flatten(),
    ))))
}

pub fn router() -> Router<State> {
    Router::new()
        .route(
            "/api/analysis/{analysis_id}/influence",
            post(run_influence).get(get_influence),
        )
        .route(
            "/api/analysis/{analysis_id}/influence/open",
            post(open_variant),
        )
        .route("/api/dispersal/propose-sequence", post(propose))
}
