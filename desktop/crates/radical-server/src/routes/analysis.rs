//! Analyses (`api/analysis.py`): reading one with everything the Results
//! page draws, listing them, renaming or refiling one, and deleting them.
//! Running them -- calibrations, Bayesian models, re-runs -- is in
//! `routes/runs.rs`.

use std::collections::HashMap;

use axum::Router;
use axum::body::Bytes;
use axum::extract::{Path, Query, State as Extract};
use axum::routing::get;
use radical_core::calibration::{REPORTING_CONFIDENCE_LEVELS, hpd_ranges};
use radical_core::measurement::Curves;
use radical_core::model_tree::migrate_from_relationships;
use radical_core::statistics::{self, SameEvent};
use radical_core::stratigraphy::{Group, Relationship};
use radical_core::{HpdRange, Measurement, Mode};
use radical_store::rows::{
    self, Analysis, AnalysisResult, ModelElement, Project, RadiocarbonSample, SampleGroup,
    StratigraphicRelationship,
};
use radical_store::{Timestamp, rusqlite};
use serde_json::{Map, Value, json};

use crate::schemas;
use crate::validate::{self, Problems};
use crate::{ApiError, ApiResult, State, ok};

pub fn iso(t: Option<Timestamp>) -> Value {
    t.map_or(Value::Null, |t| Value::from(t.isoformat()))
}

/// The analysis, or the 404 a missing one is.
pub fn require(conn: &rusqlite::Connection, analysis_id: &str) -> ApiResult<Analysis> {
    rows::get(conn, analysis_id)
        .map_err(ApiError::from)?
        .ok_or_else(|| ApiError::not_found("That analysis was not found."))
}

/// An analysis's dates, in its reading order (`Analysis.measurements`).
pub fn measurements(
    conn: &rusqlite::Connection,
    analysis_id: &str,
) -> radical_store::Result<Vec<RadiocarbonSample>> {
    rows::select_where(
        conn,
        "analysis_id = ?",
        [analysis_id],
        "sort_order, created_at, id",
    )
}

/// A project id from a request, unchanged if absent or real: a 404 rather
/// than an analysis silently left unassigned.
pub fn validate_project_id(
    conn: &rusqlite::Connection,
    project_id: Option<&str>,
) -> ApiResult<Option<String>> {
    let Some(id) = project_id.filter(|p| !p.is_empty()) else {
        return Ok(None);
    };
    let found: Option<Project> = rows::get(conn, id)?;
    if found.is_none() {
        return Err(ApiError::not_found(format!("Project '{id}' not found")));
    }
    Ok(Some(id.to_owned()))
}

// --------------------------------------------------------------------------
// The model tree, as JSON
// --------------------------------------------------------------------------

/// The nested tree from flat rows (`model_tree.rows_to_tree`): siblings in
/// position order, an orphan attached to the root rather than lost.
pub fn rows_to_tree(rows: &[ModelElement]) -> Option<Value> {
    let ids: std::collections::HashSet<&str> = rows.iter().map(|r| r.id.as_str()).collect();
    let root = rows
        .iter()
        .find(|r| r.parent_id.as_deref().is_none_or(|p| !ids.contains(p)))?;
    fn node(
        row: &ModelElement,
        rows: &[ModelElement],
        root_id: &str,
        ids: &std::collections::HashSet<&str>,
    ) -> Value {
        let mut children: Vec<&ModelElement> = rows
            .iter()
            .filter(|r| {
                r.id != root_id
                    && match r.parent_id.as_deref() {
                        Some(p) if ids.contains(p) => p == row.id,
                        _ => row.id == root_id,
                    }
            })
            .collect();
        children.sort_by_key(|r| r.position);
        json!({
            "id": row.id,
            "element_type": row.element_type,
            "name": row.name,
            "tpq_age_bp": row.tpq_age_bp,
            "taq_age_bp": row.taq_age_bp,
            "params": row.params.clone().unwrap_or(Value::Null),
            "posterior": row.posterior.clone().unwrap_or(Value::Null),
            "children": children.iter().map(|c| node(c, rows, root_id, ids)).collect::<Vec<_>>(),
        })
    }
    Some(node(root, rows, &root.id, &ids))
}

/// Flat rows from a nested tree (`model_tree.tree_to_rows`), ids kept.
pub fn tree_to_rows(root: &Value, analysis_id: &str) -> Vec<ModelElement> {
    fn walk(
        e: &Value,
        parent: Option<&str>,
        position: i64,
        analysis_id: &str,
        now: Timestamp,
        out: &mut Vec<ModelElement>,
    ) {
        let present = |k: &str| e.get(k).filter(|v| !v.is_null()).cloned();
        out.push(ModelElement {
            id: e["id"].as_str().unwrap_or_default().to_owned(),
            analysis_id: analysis_id.to_owned(),
            parent_id: parent.map(str::to_owned),
            position,
            element_type: e["element_type"].as_str().unwrap_or_default().to_owned(),
            name: e["name"].as_str().map(str::to_owned),
            tpq_age_bp: e["tpq_age_bp"].as_f64(),
            taq_age_bp: e["taq_age_bp"].as_f64(),
            posterior: present("posterior"),
            created_at: Some(now),
            params: present("params"),
        });
        for (i, child) in e["children"].as_array().into_iter().flatten().enumerate() {
            walk(child, e["id"].as_str(), i as i64, analysis_id, now, out);
        }
    }
    let mut out = Vec::new();
    walk(root, None, 0, analysis_id, Timestamp::now(), &mut out);
    out
}

/// Replace an analysis's stored tree.
pub fn persist_tree(
    tx: &rusqlite::Connection,
    analysis_id: &str,
    root: &Value,
) -> radical_store::Result<()> {
    rows::delete_where::<ModelElement>(tx, "analysis_id = ?", [analysis_id])?;
    for row in tree_to_rows(root, analysis_id) {
        rows::insert(tx, &row)?;
    }
    Ok(())
}

/// A core element as the migration's JSON (no params, no posterior).
pub fn migrated_json(e: &radical_core::model_tree::Element) -> Value {
    json!({
        "id": e.id,
        "element_type": e.element_type,
        "name": e.name,
        "tpq_age_bp": e.tpq_age_bp,
        "taq_age_bp": e.taq_age_bp,
        "children": e.children.iter().map(migrated_json).collect::<Vec<_>>(),
    })
}

/// (root, linearized, each date's phase by sample id).
pub type Migration = (Value, bool, HashMap<String, String>);

/// The tree an analysis without one would get from its contexts and
/// relationships, built without writing: (root, linearized, each date's
/// phase). None for an analysis with no dates (`migrated_tree`).
pub fn migrated_tree(
    conn: &rusqlite::Connection,
    analysis_id: &str,
    samples: &[RadiocarbonSample],
) -> radical_store::Result<Option<Migration>> {
    if samples.is_empty() {
        return Ok(None);
    }
    let groups: Vec<SampleGroup> =
        rows::select_where(conn, "analysis_id = ?", [analysis_id], "rowid")?;
    let relationships: Vec<StratigraphicRelationship> =
        rows::select_where(conn, "analysis_id = ?", [analysis_id], "rowid")?;
    let dates: Vec<Measurement> = samples
        .iter()
        .map(|s| Measurement {
            id: s.id.clone(),
            group_id: s.group_id.clone(),
            ..Measurement::default()
        })
        .collect();
    let groups: Vec<Group> = groups
        .iter()
        .map(|g| Group {
            id: g.id.clone(),
            name: Some(g.name.clone()),
            tpq_age_bp: g.tpq_age_bp,
            taq_age_bp: g.taq_age_bp,
        })
        .collect();
    let relationships: Vec<Relationship> = relationships
        .iter()
        .map(|r| Relationship {
            from_sample_id: r.from_sample_id.clone(),
            to_sample_id: r.to_sample_id.clone(),
            from_type: Some(r.from_type.clone()),
            to_type: Some(r.to_type.clone()),
            relationship: r.relationship.clone(),
        })
        .collect();
    let migrated =
        migrate_from_relationships(&dates, &groups, &relationships, radical_store::new_id);
    let mut root = migrated_json(&migrated.root);
    let mut element_ids: HashMap<String, String> = samples
        .iter()
        .filter_map(|s| s.group_id.clone().map(|g| (s.id.clone(), g)))
        .collect();
    let loose: Vec<&RadiocarbonSample> = samples.iter().filter(|s| s.group_id.is_none()).collect();
    if !loose.is_empty() {
        let loose_id = radical_store::new_id();
        root["children"].as_array_mut().unwrap().push(json!({
            "id": loose_id, "element_type": "phase", "name": "Unassigned dates",
            "tpq_age_bp": null, "taq_age_bp": null, "children": [],
        }));
        for s in loose {
            element_ids.insert(s.id.clone(), loose_id.clone());
        }
    }
    Ok(Some((root, migrated.linearized, element_ids)))
}

/// The analysis's tree, migrated and stored on first read if it has none
/// (`_get_or_migrate_tree`): (root, linearized).
pub fn tree_or_migrate(
    state: &State,
    analysis_id: &str,
) -> radical_store::Result<(Option<Value>, bool)> {
    let rows: Vec<ModelElement> = state
        .store
        .read(|c| rows::select_where(c, "analysis_id = ?", [analysis_id], "rowid"))?;
    if let Some(tree) = rows_to_tree(&rows) {
        return Ok((Some(tree), false));
    }
    state.store.write(|tx| {
        let samples = measurements(tx, analysis_id)?;
        let Some((root, linearized, element_ids)) = migrated_tree(tx, analysis_id, &samples)?
        else {
            return Ok((None, false));
        };
        for mut s in samples {
            s.element_id = element_ids.get(&s.id).cloned();
            rows::update(tx, &s)?;
        }
        persist_tree(tx, analysis_id, &root)?;
        // The backend's tree_to_rows numbers the very tree it flattens, and
        // the migrated tree goes back to the client with those numbers on.
        let mut root = root;
        mark_positions(&mut root, 0);
        Ok((Some(root), linearized))
    })
}

fn mark_positions(e: &mut Value, position: i64) {
    e["_position"] = json!(position);
    if let Some(children) = e["children"].as_array_mut() {
        for (i, child) in children.iter_mut().enumerate() {
            mark_positions(child, i as i64);
        }
    }
}

/// The tree as the frontend gets it: every posterior without its raw
/// `posterior_samples`, which only the database keeps (`_tree_for_client`).
pub fn tree_for_client(root: &Value) -> Value {
    let Some(map) = root.as_object() else {
        return root.clone();
    };
    let mut out = Map::new();
    for (k, v) in map {
        match k.as_str() {
            "children" if !v.is_null() => {
                out.insert(
                    k.clone(),
                    Value::Array(
                        v.as_array()
                            .into_iter()
                            .flatten()
                            .map(tree_for_client)
                            .collect(),
                    ),
                );
            }
            "posterior" if v.is_object() => {
                let mut p = v.as_object().unwrap().clone();
                p.remove("posterior_samples");
                out.insert(k.clone(), Value::Object(p));
            }
            _ => {
                out.insert(k.clone(), v.clone());
            }
        }
    }
    Value::Object(out)
}

pub fn walk_tree<'a>(e: &'a Value, out: &mut Vec<&'a Value>) {
    out.push(e);
    for c in e["children"].as_array().into_iter().flatten() {
        walk_tree(c, out);
    }
}

// --------------------------------------------------------------------------
// Results as JSON
// --------------------------------------------------------------------------

pub fn ranges_json(ranges: &[HpdRange]) -> Value {
    Value::Array(
        ranges
            .iter()
            .map(|r| json!({"min": r.min, "max": r.max, "probability_percent": r.probability_percent}))
            .collect(),
    )
}

/// A same-event test as the API reports one.
pub fn same_event_json(t: &SameEvent) -> Value {
    json!({
        "n": t.n,
        "df": t.df,
        "level": t.level,
        "mean_bp": t.mean_bp,
        "error_bp": t.error_bp,
        "t": t.t,
        "critical": t.critical,
        "consistent": t.consistent,
        "pooled_hpd_ranges": t.pooled_hpd_ranges.as_ref().map(|levels| levels.iter()
            .map(|l| json!({"confidence": l.confidence, "ranges": ranges_json(&l.ranges)}))
            .collect::<Vec<_>>()),
        "refused": t.refused,
    })
}

/// The test over a group calibration's stored dates, and none for any
/// other kind (`statistics.same_event_for_samples`).
pub fn same_event_for_samples(
    state: &State,
    analysis_type: &str,
    samples: &[RadiocarbonSample],
) -> Value {
    if analysis_type != "group_calibration" {
        return json!([]);
    }
    let dates: Vec<statistics::Date> = samples
        .iter()
        .map(|s| statistics::Date {
            age_bp: s.age_bp,
            error_1sigma: s.error_1sigma,
            curve_id: s.curve_id.clone(),
            delta_r: s.delta_r,
            delta_r_error: s.delta_r_error,
            inbuilt_years: s.inbuilt_years,
            inbuilt_shape: s.inbuilt_shape.clone(),
        })
        .collect();
    match statistics::same_event(&dates, |id| state.curves.curve(id).ok(), Mode::Standard) {
        None => json!([]),
        Some(test) => {
            let mut out = json!({"element_id": null, "name": null});
            for (k, v) in same_event_json(&test).as_object().unwrap() {
                out[k] = v.clone();
            }
            json!([out])
        }
    }
}

/// What calibrating a date alone gives: HPD ranges at every reported level
/// and a plottable series, or None when its curve no longer resolves.
fn unmodelled(state: &State, s: &RadiocarbonSample) -> Option<(Value, Value)> {
    let curve = state
        .curves
        .curve(s.curve_id.as_deref().unwrap_or("intcal20"))
        .ok()?;
    let probs = curve
        .calibrate(
            s.age_bp,
            s.error_1sigma,
            s.delta_r,
            s.delta_r_error,
            Mode::Standard,
        )
        .ok()?;
    let cal = curve.cal_ages();
    let mut levels = Vec::new();
    for level in REPORTING_CONFIDENCE_LEVELS {
        levels.push(json!({"confidence": level, "ranges": ranges_json(&hpd_ranges(cal, &probs, level).ok()?)}));
    }
    let series = curve.display_distribution(cal, &probs, 200, 1e-4);
    Some((Value::Array(levels), json!(series)))
}

/// One stored date as the analysis read reports it.
pub fn measurement_json(s: &RadiocarbonSample) -> Value {
    json!({
        "id": s.id,
        "sample_name": s.sample_name,
        "age_bp": s.age_bp,
        "error_1sigma": s.error_1sigma,
        "material_type": s.material_type,
        "sample_type": s.sample_type,
        "curve_id": s.curve_id,
        "delta_r": s.delta_r,
        "delta_r_error": s.delta_r_error,
        "delta_r_source": s.delta_r_source.clone().unwrap_or(Value::Null),
        "latitude": s.latitude,
        "longitude": s.longitude,
        "outlier_prior": s.outlier_prior,
        "outlier_model": s.outlier_model,
        "reservoir_age": s.reservoir_age,
        "depth_cm": s.depth_cm,
        "ring_offset": s.ring_offset,
        "ring_offset_error": s.ring_offset_error,
        "inbuilt_years": s.inbuilt_years,
        "inbuilt_shape": s.inbuilt_shape,
        "location": s.location,
        "notes": s.notes,
        "group_id": s.group_id,
        "element_id": s.element_id,
        "tpq_age_bp": s.tpq_age_bp,
        "taq_age_bp": s.taq_age_bp,
    })
}

/// Everything the Results page draws for one analysis (`get_analysis`):
/// also what a run records as its history.
pub fn analysis_payload(state: &State, analysis_id: &str) -> ApiResult<Value> {
    let analysis = state.store.read(|c| Ok(require(c, analysis_id)))??;
    let (samples, results, relationships, groups, project) = state.store.read(|c| {
        Ok((
            measurements(c, analysis_id)?,
            rows::select_where::<AnalysisResult>(c, "analysis_id = ?", [analysis_id], "rowid")?,
            rows::select_where::<StratigraphicRelationship>(
                c,
                "analysis_id = ?",
                [analysis_id],
                "rowid",
            )?,
            rows::select_where::<SampleGroup>(c, "analysis_id = ?", [analysis_id], "rowid")?,
            match &analysis.project_id {
                Some(p) => rows::get::<Project>(c, p)?,
                None => None,
            },
        ))
    })?;
    let (model_tree, linearized) = if analysis.analysis_type == "bayesian" {
        tree_or_migrate(state, analysis_id)?
    } else {
        (None, false)
    };
    let samples = if model_tree.is_some() && analysis.analysis_type == "bayesian" {
        // A first read may just have migrated the dates into phases.
        state.store.read(|c| measurements(c, analysis_id))?
    } else {
        samples
    };
    let by_id: HashMap<&str, &RadiocarbonSample> =
        samples.iter().map(|s| (s.id.as_str(), s)).collect();
    let mut groups_payload: Vec<Value> = groups
        .iter()
        .map(|g| {
            json!({
                "id": g.id, "name": g.name, "tpq_age_bp": g.tpq_age_bp, "taq_age_bp": g.taq_age_bp,
                "modeled_start": g.modeled_start.clone().unwrap_or(Value::Null),
                "modeled_end": g.modeled_end.clone().unwrap_or(Value::Null),
            })
        })
        .collect();
    if groups_payload.is_empty()
        && let Some(tree) = &model_tree
    {
        let mut all = Vec::new();
        walk_tree(tree, &mut all);
        groups_payload = all
            .iter()
            .filter(|e| e["element_type"] == "phase")
            .map(|e| {
                json!({
                    "id": e["id"],
                    "name": e["name"].as_str().filter(|n| !n.is_empty()).unwrap_or("Phase"),
                    "tpq_age_bp": e["tpq_age_bp"],
                    "taq_age_bp": e["taq_age_bp"],
                    "modeled_start": null,
                    "modeled_end": null,
                })
            })
            .collect();
    }
    let mut unmodelled_by_id: HashMap<String, (Value, Value)> = HashMap::new();
    if analysis.analysis_type == "bayesian" {
        for r in &results {
            if let Some(s) = r.sample_id.as_deref().and_then(|id| by_id.get(id))
                && let Some(found) = unmodelled(state, s)
            {
                unmodelled_by_id.insert(s.id.clone(), found);
            }
        }
    }
    let results_json: Vec<Value> = results
        .iter()
        .map(|r| {
            let sample = r.sample_id.as_deref().and_then(|id| by_id.get(id));
            let un = r.sample_id.as_deref().and_then(|id| unmodelled_by_id.get(id));
            json!({
                "sample_id": r.sample_id,
                "sample_name": sample.map(|s| s.sample_name.clone()),
                "posterior_mean": r.posterior_mean,
                "posterior_median": r.posterior_median,
                "posterior_std": r.posterior_std,
                "hpd_68": r.hpd_68_min.map(|min| json!([min, r.hpd_68_max])),
                "hpd_95": r.hpd_95_min.map(|min| json!([min, r.hpd_95_max])),
                "hpd_68_ranges": r.hpd_68_ranges.clone().unwrap_or(Value::Null),
                "hpd_95_ranges": r.hpd_95_ranges.clone().unwrap_or(Value::Null),
                "probability_distribution": r.probability_distribution.clone().unwrap_or(Value::Null),
                "unmodelled_hpd_ranges": un.map(|u| u.0.clone()),
                "unmodelled_distribution": un.map(|u| u.1.clone()),
                "agreement_index": r.agreement_index,
                "outlier_probability": r.outlier_probability,
            })
        })
        .collect();
    Ok(json!({
        "id": analysis.id,
        "name": analysis.name,
        "analysis_type": analysis.analysis_type,
        "status": analysis.status,
        "error_message": analysis.error_message,
        "created_at": iso(analysis.created_at),
        "measurements_count": samples.len(),
        "project_id": analysis.project_id,
        "project_name": project.map(|p| p.name),
        "bayesian_config": analysis.bayesian_config.clone().filter(|v| !v.is_null()).unwrap_or_else(|| json!({})),
        "solver": analysis.solver.clone().unwrap_or(Value::Null),
        "intervals": analysis.intervals.clone().unwrap_or(Value::Null),
        "summaries": analysis.summaries.clone().unwrap_or(Value::Null),
        "same_event": same_event_for_samples(state, &analysis.analysis_type, &samples),
        "model_tree": model_tree.as_ref().map_or(Value::Null, tree_for_client),
        "model_tree_linearized": linearized,
        "measurements": samples.iter().map(measurement_json).collect::<Vec<_>>(),
        "groups": groups_payload,
        "relationships": relationships.iter().map(|r| json!({
            "id": r.id,
            "from_sample_id": r.from_sample_id,
            "to_sample_id": r.to_sample_id,
            "from_type": r.from_type,
            "to_type": r.to_type,
            "relationship": r.relationship,
        })).collect::<Vec<_>>(),
        "results": results_json,
    }))
}

async fn read(Extract(state): Extract<State>, Path(analysis_id): Path<String>) -> ApiResult {
    Ok(ok(analysis_payload(&state, &analysis_id)?))
}

// --------------------------------------------------------------------------
// The list
// --------------------------------------------------------------------------

/// A stored HPD region as [min, max] pairs, newest-first by their upper end.
fn hpd_segments(ranges: &Option<Value>) -> Vec<[f64; 2]> {
    let mut out: Vec<[f64; 2]> = ranges
        .as_ref()
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|r| Some([r["min"].as_f64()?, r["max"].as_f64()?]))
        .collect();
    out.sort_by(|a, b| b[1].partial_cmp(&a[1]).unwrap_or(std::cmp::Ordering::Equal));
    out
}

/// A query parameter read as FastAPI reads an int or a bool.
fn query_int(
    q: &HashMap<String, String>,
    name: &str,
    default: i64,
    problems: &mut Problems,
) -> i64 {
    let Some(raw) = q.get(name) else {
        return default;
    };
    match raw.trim().parse::<i64>() {
        Ok(n) => n,
        Err(_) => {
            problems.0.push(validate::problem(
                "int_parsing",
                &[json!("query"), json!(name)],
                "Input should be a valid integer, unable to parse string as an integer",
                Value::from(raw.as_str()),
                None,
            ));
            default
        }
    }
}

pub fn query_bool(q: &HashMap<String, String>, name: &str, problems: &mut Problems) -> bool {
    let Some(raw) = q.get(name) else { return false };
    match raw.trim().to_lowercase().as_str() {
        "1" | "on" | "t" | "true" | "y" | "yes" => true,
        "0" | "off" | "f" | "false" | "n" | "no" => false,
        _ => {
            problems.0.push(validate::problem(
                "bool_parsing",
                &[json!("query"), json!(name)],
                "Input should be a valid boolean, unable to interpret input",
                Value::from(raw.as_str()),
                None,
            ));
            false
        }
    }
}

/// Analyses, most recent first; one project's, or those filed under none.
async fn list(
    Extract(state): Extract<State>,
    Query(q): Query<HashMap<String, String>>,
) -> ApiResult {
    let mut problems = Problems::default();
    let page = query_int(&q, "page", 1, &mut problems);
    let per_page = query_int(&q, "per_page", 10, &mut problems);
    let unassigned = query_bool(&q, "unassigned", &mut problems);
    problems.finish()?;
    let project_id = q.get("project_id").filter(|p| !p.is_empty()).cloned();
    let data = state.store.read(|c| {
        let (condition, params): (&str, Vec<String>) = if unassigned {
            ("project_id IS NULL", vec![])
        } else if let Some(p) = &project_id {
            ("project_id = ?", vec![p.clone()])
        } else {
            ("1", vec![])
        };
        let total: i64 = c.query_row(
            &format!("SELECT count(*) FROM analyses WHERE {condition}"),
            rusqlite::params_from_iter(params.iter()),
            |r| r.get(0),
        )?;
        let order = format!("created_at DESC LIMIT {per_page} OFFSET {}", (page - 1) * per_page);
        let analyses: Vec<Analysis> =
            rows::select_where(c, condition, rusqlite::params_from_iter(params.iter()), &order)?;
        let mut items = Vec::new();
        for a in &analyses {
            let count: i64 =
                c.query_row("SELECT count(id) FROM radiocarbon_samples WHERE analysis_id = ?", [&a.id], |r| r.get(0))?;
            let results: Vec<AnalysisResult> = rows::select_where(c, "analysis_id = ?", [&a.id], "rowid")?;
            let project = match &a.project_id {
                Some(p) => rows::get::<Project>(c, p)?,
                None => None,
            };
            let edges: Vec<f64> = results.iter().flat_map(|r| hpd_segments(&r.hpd_95_ranges)).flatten().collect();
            let span = (!edges.is_empty()).then(|| {
                let lo = edges.iter().copied().fold(f64::INFINITY, f64::min);
                let hi = edges.iter().copied().fold(f64::NEG_INFINITY, f64::max);
                json!([lo, hi])
            });
            let intervals: Vec<Value> = results
                .iter()
                .filter_map(|r| {
                    let h95 = hpd_segments(&r.hpd_95_ranges);
                    (!h95.is_empty()).then(|| {
                        json!({"h68": hpd_segments(&r.hpd_68_ranges), "h95": h95, "median": r.posterior_median})
                    })
                })
                .collect();
            items.push(json!({
                "id": a.id,
                "name": a.name,
                "analysis_type": a.analysis_type,
                "status": a.status,
                "created_at": iso(a.created_at),
                "measurements_count": count,
                "project_id": a.project_id,
                "project_name": project.map(|p| p.name),
                "calendar_span": span,
                "intervals": intervals,
            }));
        }
        Ok(json!({
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1).div_euclid(per_page),
        }))
    })?;
    Ok(ok(data))
}

// --------------------------------------------------------------------------
// Editing and deleting
// --------------------------------------------------------------------------

/// Everything tied to these analyses, then the analyses.
pub fn delete_analyses(tx: &rusqlite::Connection, ids: &[String]) -> radical_store::Result<()> {
    for id in ids {
        for table in [
            "analysis_results",
            "stratigraphic_relationships",
            "model_elements",
            "radiocarbon_samples",
            "sample_groups",
            "analysis_runs",
        ] {
            tx.execute(&format!("DELETE FROM {table} WHERE analysis_id = ?"), [id])?;
        }
        tx.execute("DELETE FROM analyses WHERE id = ?", [id])?;
    }
    Ok(())
}

async fn delete_one(Extract(state): Extract<State>, Path(analysis_id): Path<String>) -> ApiResult {
    state.store.read(|c| Ok(require(c, &analysis_id)))??;
    state
        .store
        .write(|tx| delete_analyses(tx, std::slice::from_ref(&analysis_id)))?;
    Ok(ok(json!({"deleted_analysis_id": analysis_id})))
}

/// Several analyses at once, all or nothing on the ids.
async fn delete_many(Extract(state): Extract<State>, body: Bytes) -> ApiResult {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "AnalysisBulkDelete", &mut problems);
    let ids = schemas::unique_ids(&mut f, "analysis_ids", Some(1), Some(500));
    problems.finish()?;
    let ids = ids.unwrap_or_default();
    let missing: Vec<String> = state.store.read(|c| {
        let mut out = Vec::new();
        for id in &ids {
            if rows::get::<Analysis>(c, id)?.is_none() {
                out.push(id.clone());
            }
        }
        Ok(out)
    })?;
    if !missing.is_empty() {
        return Err(ApiError::not_found(format!(
            "These analyses were not found: {}",
            missing.join(", ")
        )));
    }
    state.store.write(|tx| delete_analyses(tx, &ids))?;
    let mut sorted = ids.clone();
    sorted.sort();
    Ok(ok(
        json!({"deleted_count": ids.len(), "deleted_analysis_ids": sorted}),
    ))
}

/// Refile an analysis under a project (or none), or rename it.
async fn update(
    Extract(state): Extract<State>,
    Path(analysis_id): Path<String>,
    body: Bytes,
) -> ApiResult {
    let input = validate::json_body(&body)?;
    let Some(updates) = input.as_object() else {
        return Err(ApiError::invalid(vec![validate::problem(
            "dict_type",
            &[json!("body")],
            "Input should be a valid dictionary",
            input.clone(),
            None,
        )]));
    };
    let mut analysis = state.store.read(|c| Ok(require(c, &analysis_id)))??;
    if updates.contains_key("project_id") {
        let wanted = updates["project_id"].as_str();
        analysis.project_id = state.store.read(|c| Ok(validate_project_id(c, wanted)))??;
    }
    if let Some(name) = updates
        .get("name")
        .and_then(Value::as_str)
        .filter(|n| !n.is_empty())
    {
        analysis.name = name.to_owned();
    }
    analysis.updated_at = Some(Timestamp::now());
    state.store.write(|tx| rows::update(tx, &analysis))?;
    let project_name = match &analysis.project_id {
        Some(p) => state
            .store
            .read(|c| rows::get::<Project>(c, p))?
            .map(|p| p.name),
        None => None,
    };
    Ok(ok(json!({
        "id": analysis.id,
        "name": analysis.name,
        "project_id": analysis.project_id,
        "project_name": project_name,
    })))
}

pub fn router() -> Router<State> {
    Router::new()
        .route("/api/analysis", get(list).delete(delete_many))
        .route(
            "/api/analysis/{analysis_id}",
            get(read).delete(delete_one).patch(update),
        )
}
