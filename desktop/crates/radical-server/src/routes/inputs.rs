//! Editing a saved analysis's inputs (`api/analysis.py`): its model tree,
//! a copy to vary, and its dates, contexts and settings wholesale. Each
//! clears the results the old inputs produced and leaves the analysis
//! pending until it is run again.

use std::collections::HashMap;

use axum::Router;
use axum::body::Bytes;
use axum::extract::{Path, Query, State as Extract};
use axum::http::StatusCode;
use axum::routing::{post, put};
use radical_core::Measurement;
use radical_core::model_tree::{validate_ordinates, validate_tree};
use radical_store::Timestamp;
use radical_store::rows::{
    self, Analysis, AnalysisResult, ModelElement, RadiocarbonSample, SampleGroup,
    StratigraphicRelationship,
};
use serde_json::{Value, json};

use crate::routes::analysis::{
    measurements, persist_tree, query_bool, require, rows_to_tree, tree_for_client, walk_tree,
};
use crate::routes::runs::sample_row;
use crate::schemas;
use crate::solve::{element, relabel_tree};
use crate::validate::{self, Problems};
use crate::{ApiError, ApiResult, State, ok};

fn unprocessable(detail: impl Into<String>) -> ApiError {
    ApiError::new(StatusCode::UNPROCESSABLE_ENTITY, detail)
}

/// Replace an analysis's model tree, and optionally which phase each date
/// is in. Validated before anything is written; the old results go.
async fn replace_model_tree(
    Extract(state): Extract<State>,
    Path(analysis_id): Path<String>,
    body: Bytes,
) -> ApiResult {
    let input = validate::json_body(&body)?;
    let Some(payload) = input.as_object() else {
        return Err(ApiError::invalid(vec![validate::problem(
            "dict_type",
            &[json!("body")],
            "Input should be a valid dictionary",
            input.clone(),
            None,
        )]));
    };
    let mut analysis = state.store.read(|c| Ok(require(c, &analysis_id)))??;
    let root = payload.get("model_tree").cloned().unwrap_or(Value::Null);
    if !truthy(&root) {
        return Err(unprocessable("model_tree is required"));
    }
    let assignments: Vec<(String, Option<String>)> = payload
        .get("sample_elements")
        .and_then(Value::as_object)
        .map(|m| {
            m.iter()
                .map(|(k, v)| (k.clone(), v.as_str().map(str::to_owned)))
                .collect()
        })
        .unwrap_or_default();
    let samples = state.store.read(|c| measurements(c, &analysis_id))?;
    if let Some((sample_id, _)) = assignments
        .iter()
        .find(|(id, _)| !samples.iter().any(|s| &s.id == id))
    {
        return Err(unprocessable(format!(
            "sample '{sample_id}' does not belong to this analysis"
        )));
    }
    let assigned = |s: &RadiocarbonSample| -> Option<String> {
        match assignments.iter().find(|(id, _)| id == &s.id) {
            Some((_, e)) => e.clone(),
            None => s
                .element_id
                .clone()
                .filter(|e| !e.is_empty())
                .or_else(|| s.group_id.clone()),
        }
    };
    let prospective: Vec<Measurement> = samples
        .iter()
        .map(|s| Measurement {
            id: s.id.clone(),
            sample_name: Some(s.sample_name.clone()),
            element_id: assigned(s),
            ring_offset: s.ring_offset,
            ring_offset_error: s.ring_offset_error,
            depth_cm: s.depth_cm,
            outlier_prior: s.outlier_prior,
            ..Measurement::default()
        })
        .collect();
    let tree = element(&root);
    validate_tree(Some(&tree), &prospective)
        .and_then(|()| validate_ordinates(Some(&tree), &prospective))
        .map_err(|e| unprocessable(e.to_string()))?;
    analysis.status = Some("pending".to_owned());
    analysis.error_message = None;
    analysis.updated_at = Some(Timestamp::now());
    let stored = state.store.write(|tx| {
        for mut s in samples {
            if let Some((_, e)) = assignments.iter().find(|(id, _)| id == &s.id) {
                s.element_id = e.clone();
                rows::update(tx, &s)?;
            }
        }
        persist_tree(tx, &analysis_id, &root)?;
        rows::delete_where::<AnalysisResult>(tx, "analysis_id = ?", [&analysis_id])?;
        rows::update(tx, &analysis)?;
        rows::select_where::<ModelElement>(tx, "analysis_id = ?", [&analysis_id], "rowid")
    })?;
    Ok(ok(json!({
        "id": analysis.id,
        "status": analysis.status,
        "model_tree": rows_to_tree(&stored).map_or(Value::Null, |t| tree_for_client(&t)),
    })))
}

fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// A seed for a replicate: uniform below 2^31 - 1, as `secrets.randbelow`.
fn fresh_seed() -> i64 {
    let hex: String = radical_store::new_id()
        .chars()
        .filter(char::is_ascii_hexdigit)
        .take(15)
        .collect();
    (u64::from_str_radix(&hex, 16).unwrap_or(0) % (2_147_483_647u64)) as i64
}

/// Copy an analysis's inputs into a new, pending one: a variant to edit,
/// a replicate with a fresh seed, or the same model with the inbuilt-age
/// allowances switched.
async fn duplicate(
    Extract(state): Extract<State>,
    Path(analysis_id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> ApiResult {
    let mut problems = Problems::default();
    let new_seed = query_bool(&q, "new_seed", &mut problems);
    let apply_inbuilt = q
        .contains_key("apply_inbuilt")
        .then(|| query_bool(&q, "apply_inbuilt", &mut problems));
    problems.finish()?;
    let source = state.store.read(|c| Ok(require(c, &analysis_id)))??;
    let mut name = format!("{} (copy)", source.name);
    let mut config = source.bayesian_config.clone();
    let as_object = |c: &Option<Value>| {
        c.as_ref()
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default()
    };
    if new_seed {
        if source.analysis_type != "bayesian" {
            return Err(ApiError::bad_request(
                "Only a Bayesian analysis has a seed to change.",
            ));
        }
        let seed = fresh_seed();
        name = format!("{} (seed {seed})", source.name);
        let mut c = as_object(&config);
        c.insert("seed".to_owned(), json!(seed));
        config = Some(Value::Object(c));
    }
    if let Some(on) = apply_inbuilt {
        if source.analysis_type != "bayesian" {
            return Err(ApiError::bad_request(
                "Inbuilt-age allowances apply only in a Bayesian model.",
            ));
        }
        name = format!(
            "{} (inbuilt age {})",
            source.name,
            if on { "on" } else { "off" }
        );
        let mut c = as_object(&config);
        c.insert("apply_inbuilt".to_owned(), json!(on));
        config = Some(Value::Object(c));
    }
    let copy = state
        .store
        .write(|tx| copy_inputs(tx, &source, &name, config.clone()))?;
    Ok(ok(json!({
        "id": copy.id,
        "name": copy.name,
        "analysis_type": copy.analysis_type,
        "status": copy.status,
        "project_id": copy.project_id,
        "source_analysis_id": source.id,
    })))
}

/// `_copy_inputs`: the copy, with its own contexts, dates, relationships
/// and tree, ids remapped and no results.
pub fn copy_inputs(
    tx: &radical_store::rusqlite::Connection,
    source: &Analysis,
    name: &str,
    config: Option<Value>,
) -> radical_store::Result<Analysis> {
    let now = Some(Timestamp::now());
    let copy = Analysis {
        id: radical_store::new_id(),
        name: name.to_owned(),
        analysis_type: source.analysis_type.clone(),
        status: Some("pending".to_owned()),
        created_at: now,
        updated_at: now,
        project_id: source.project_id.clone(),
        bayesian_config: config,
        ..Analysis::default()
    };
    rows::insert(tx, &copy)?;

    let mut groups_map: HashMap<String, String> = HashMap::new();
    let groups: Vec<SampleGroup> =
        rows::select_where(tx, "analysis_id = ?", [&source.id], "rowid")?;
    for g in groups {
        let row = SampleGroup {
            id: radical_store::new_id(),
            analysis_id: copy.id.clone(),
            name: g.name.clone(),
            tpq_age_bp: g.tpq_age_bp,
            taq_age_bp: g.taq_age_bp,
            created_at: now,
            ..SampleGroup::default()
        };
        rows::insert(tx, &row)?;
        groups_map.insert(g.id, row.id);
    }

    let samples = measurements(tx, &source.id)?;
    let mut samples_map: HashMap<String, String> = HashMap::new();
    for (position, s) in samples.iter().enumerate() {
        let placed = s
            .element_id
            .clone()
            .filter(|e| !e.is_empty())
            .or_else(|| s.group_id.clone());
        let row = RadiocarbonSample {
            id: radical_store::new_id(),
            analysis_id: copy.id.clone(),
            sort_order: position as i64,
            sample_type: if s.sample_type.is_empty() {
                if s.curve_id.as_deref() == Some("marine20") {
                    "marine".to_owned()
                } else {
                    "terrestrial".to_owned()
                }
            } else {
                s.sample_type.clone()
            },
            group_id: s.group_id.as_ref().and_then(|g| groups_map.get(g).cloned()),
            element_id: placed.and_then(|e| groups_map.get(&e).cloned()),
            created_at: now,
            ..s.clone()
        };
        rows::insert(tx, &row)?;
        samples_map.insert(s.id.clone(), row.id);
    }

    let relationships: Vec<StratigraphicRelationship> =
        rows::select_where(tx, "analysis_id = ?", [&source.id], "rowid")?;
    for rel in relationships {
        let lookup = |kind: &str| {
            if kind == "group" {
                &groups_map
            } else {
                &samples_map
            }
        };
        let (Some(from), Some(to)) = (
            lookup(&rel.from_type).get(&rel.from_sample_id),
            lookup(&rel.to_type).get(&rel.to_sample_id),
        ) else {
            // Dropped rather than copied dangling.
            continue;
        };
        rows::insert(
            tx,
            &StratigraphicRelationship {
                id: radical_store::new_id(),
                analysis_id: copy.id.clone(),
                from_sample_id: from.clone(),
                to_sample_id: to.clone(),
                created_at: now,
                ..rel.clone()
            },
        )?;
    }

    let elements: Vec<ModelElement> =
        rows::select_where(tx, "analysis_id = ?", [&source.id], "rowid")?;
    if let Some(mut tree) = rows_to_tree(&elements) {
        let mut element_map = groups_map.clone();
        let mut all = Vec::new();
        walk_tree(&tree, &mut all);
        for e in all {
            if let Some(id) = e["id"].as_str() {
                element_map
                    .entry(id.to_owned())
                    .or_insert_with(radical_store::new_id);
            }
        }
        clear_posteriors(&mut tree);
        relabel_tree(&mut tree, &element_map, &samples_map);
        persist_tree(tx, &copy.id, &tree)?;
        for s in &samples {
            let source_element = s
                .element_id
                .clone()
                .filter(|e| !e.is_empty())
                .or_else(|| s.group_id.clone());
            if let (Some(new_id), Some(e)) = (samples_map.get(&s.id), source_element)
                && let Some(mut row) = rows::get::<RadiocarbonSample>(tx, new_id)?
            {
                row.element_id = element_map.get(&e).cloned();
                rows::update(tx, &row)?;
            }
        }
    }
    Ok(copy)
}

fn clear_posteriors(e: &mut Value) {
    e["posterior"] = Value::Null;
    if let Some(children) = e["children"].as_array_mut() {
        for c in children {
            clear_posteriors(c);
        }
    }
}

/// Replace a saved analysis's inputs wholesale, held to the checks a new
/// analysis is; `analysis_type` converts it.
async fn replace_inputs(
    Extract(state): Extract<State>,
    Path(analysis_id): Path<String>,
    body: Bytes,
) -> ApiResult {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "BayesianRequest", &mut problems);
    let parsed = schemas::bayesian_request(&mut f, &state.store);
    problems.finish()?;
    let r = parsed.expect("validated");
    let mut analysis = state.store.read(|c| Ok(require(c, &analysis_id)))??;
    state.store.write(|tx| {
        rows::delete_where::<AnalysisResult>(tx, "analysis_id = ?", [&analysis_id])?;
        rows::delete_where::<StratigraphicRelationship>(tx, "analysis_id = ?", [&analysis_id])?;
        rows::delete_where::<RadiocarbonSample>(tx, "analysis_id = ?", [&analysis_id])?;
        rows::delete_where::<SampleGroup>(tx, "analysis_id = ?", [&analysis_id])?;
        rows::delete_where::<ModelElement>(tx, "analysis_id = ?", [&analysis_id])?;
        let now = Some(Timestamp::now());
        let mut groups_map: HashMap<String, String> = HashMap::new();
        for g in &r.groups {
            let row = SampleGroup {
                id: radical_store::new_id(),
                analysis_id: analysis_id.clone(),
                name: g.name.clone().unwrap_or_default(),
                tpq_age_bp: g.tpq_age_bp,
                taq_age_bp: g.taq_age_bp,
                created_at: now,
                ..SampleGroup::default()
            };
            rows::insert(tx, &row)?;
            groups_map.insert(g.id.clone(), row.id);
        }
        let mut samples_map: HashMap<String, String> = HashMap::new();
        for (position, m) in r.measurements.iter().enumerate() {
            let group = m.group_id.as_ref().and_then(|g| groups_map.get(g).cloned());
            let element_id = if r.model_tree.is_some() {
                m.element_id.clone()
            } else {
                group.clone()
            };
            let row = sample_row(&analysis_id, m, group, element_id, position as i64);
            rows::insert(tx, &row)?;
            samples_map.insert(m.id.clone(), row.id);
        }
        for rel in &r.relationships {
            let resolve = |id: &str, kind: &Option<String>| {
                if kind.as_deref() == Some("group") {
                    groups_map[id].clone()
                } else {
                    samples_map[id].clone()
                }
            };
            rows::insert(
                tx,
                &StratigraphicRelationship {
                    id: radical_store::new_id(),
                    analysis_id: analysis_id.clone(),
                    from_sample_id: resolve(&rel.from_sample_id, &rel.from_type),
                    to_sample_id: resolve(&rel.to_sample_id, &rel.to_type),
                    from_type: rel.from_type.clone().unwrap_or_default(),
                    to_type: rel.to_type.clone().unwrap_or_default(),
                    relationship: rel.relationship.clone(),
                    created_at: now,
                },
            )?;
        }
        if let Some(tree) = &r.model_tree {
            let mut root = tree.clone();
            relabel_tree(&mut root, &groups_map, &HashMap::new());
            persist_tree(tx, &analysis_id, &root)?;
        }
        if !r.name.is_empty() {
            analysis.name = r.name.clone();
        }
        if let Some(kind) = &r.analysis_type {
            analysis.analysis_type = if kind == "calibration" && r.measurements.len() > 1 {
                "group_calibration".to_owned()
            } else {
                kind.clone()
            };
        }
        analysis.bayesian_config = Some(r.bayesian_config.clone());
        analysis.status = Some("pending".to_owned());
        analysis.error_message = None;
        analysis.solver = None;
        analysis.intervals = None;
        analysis.summaries = None;
        analysis.updated_at = now;
        rows::update(tx, &analysis)
    })?;
    Ok(ok(json!({
        "id": analysis.id,
        "name": analysis.name,
        "status": analysis.status,
        "analysis_type": analysis.analysis_type,
        "measurements_count": r.measurements.len(),
    })))
}

pub fn router() -> Router<State> {
    Router::new()
        .route(
            "/api/analysis/{analysis_id}/model-tree",
            put(replace_model_tree),
        )
        .route("/api/analysis/{analysis_id}/duplicate", post(duplicate))
        .route("/api/analysis/{analysis_id}/inputs", put(replace_inputs))
}
