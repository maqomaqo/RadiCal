//! A project's own date collection, and deploying dates from it into
//! analyses (`api/project_samples.py`). Deploying COPIES the values, so an
//! analysis stays a snapshot of what it was run on. The collection's CSV
//! export arrives with the project export it shares its writer with.

use axum::Router;
use axum::body::Bytes;
use axum::extract::{Path, State as Extract};
use axum::routing::{patch, post};
use radical_store::rows::{
    self, Analysis, CalibrationCurve, ProjectSample, RadiocarbonSample, SampleGroup,
};
use radical_store::{Timestamp, rusqlite};
use serde_json::{Map, Value, json};

use crate::accounts::Caller;
use crate::routes::curve_routes::authorise_curve_ids;
use crate::routes::projects::require;
use crate::schemas::{self, curve_exists, unknown_curve};
use crate::validate::{self, Problems};
use crate::{ApiError, ApiResult, State, ok};

fn iso(t: Option<Timestamp>) -> Value {
    t.map_or(Value::Null, |t| Value::from(t.isoformat()))
}

fn json_of(m: &Option<Map<String, Value>>) -> Option<Value> {
    m.clone().map(Value::Object)
}

/// A collected date as the API returns it.
fn serialize(s: &ProjectSample) -> Value {
    json!({
        "id": s.id,
        "project_id": s.project_id,
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
        "depth_cm": s.depth_cm,
        "ring_offset": s.ring_offset,
        "ring_offset_error": s.ring_offset_error,
        "inbuilt_years": s.inbuilt_years,
        "inbuilt_shape": s.inbuilt_shape,
        "location": s.location,
        "notes": s.notes,
        "default_context_name": s.default_context_name,
        "tpq_age_bp": s.tpq_age_bp,
        "taq_age_bp": s.taq_age_bp,
        "created_at": iso(s.created_at),
    })
}

/// The kind of sample a curve suits (`curve_registry.get_spec(..).sample_type`).
fn curve_sample_type(state: &State, curve_id: &str) -> Option<String> {
    match curve_id {
        "marine20" => Some("marine".to_owned()),
        "intcal20" | "shcal20" => Some("terrestrial".to_owned()),
        _ => state
            .store
            .read(|c| rows::get::<CalibrationCurve>(c, curve_id))
            .ok()
            .flatten()
            .map(|r| r.sample_type),
    }
}

/// Every date in the collection, oldest entry first.
async fn list(
    Extract(state): Extract<State>,
    caller: Caller,
    Path(project_id): Path<String>,
) -> ApiResult {
    require(&state, &caller, &project_id)?;
    let samples: Vec<ProjectSample> = state.store.read(|c| {
        rows::select_where(c, "project_id = ?", [&project_id], "created_at ASC, rowid")
    })?;
    Ok(ok(
        json!({"items": samples.iter().map(serialize).collect::<Vec<_>>()}),
    ))
}

/// Add dates to the collection: a list, so a bulk paste is one request.
async fn create(
    Extract(state): Extract<State>,
    caller: Caller,
    Path(project_id): Path<String>,
    body: Bytes,
) -> ApiResult {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "ProjectSampleBulkInput", &mut problems);
    let items = f.models(
        "samples",
        "ProjectSampleInput",
        Some(1),
        Some(500),
        |item| schemas::project_sample_input(item, &state.store),
    );
    problems.finish()?;
    require(&state, &caller, &project_id)?;
    let items = items.unwrap_or_default();
    authorise_curve_ids(
        &state,
        &caller,
        items.iter().map(|(m, _)| m.curve_id.as_str()),
    )?;
    let now = Some(Timestamp::now());
    let created: Vec<ProjectSample> = items
        .into_iter()
        .map(|(m, context)| ProjectSample {
            id: radical_store::new_id(),
            project_id: project_id.clone(),
            sample_type: m.sample_type.clone().unwrap_or_else(|| {
                if m.curve_id == "marine20" {
                    "marine"
                } else {
                    "terrestrial"
                }
                .to_owned()
            }),
            delta_r_source: if m.delta_r.is_some() {
                json_of(&m.delta_r_source)
            } else {
                None
            },
            sample_name: m.sample_name,
            age_bp: m.age_bp,
            error_1sigma: m.error_1sigma,
            material_type: m.material_type,
            curve_id: Some(m.curve_id),
            delta_r: m.delta_r,
            delta_r_error: m.delta_r_error,
            latitude: m.latitude,
            longitude: m.longitude,
            depth_cm: m.depth_cm,
            ring_offset: m.ring_offset,
            ring_offset_error: m.ring_offset_error,
            inbuilt_years: m.inbuilt_years,
            inbuilt_shape: m.inbuilt_shape,
            location: m.location,
            notes: m.notes,
            default_context_name: context,
            tpq_age_bp: m.tpq_age_bp,
            taq_age_bp: m.taq_age_bp,
            created_at: now,
            updated_at: now,
        })
        .collect();
    state.store.write(|tx| {
        for row in &created {
            rows::insert(tx, row)?;
        }
        Ok(())
    })?;
    Ok(ok(
        json!({"items": created.iter().map(serialize).collect::<Vec<_>>()}),
    ))
}

fn find(state: &State, project_id: &str, sample_id: &str) -> ApiResult<ProjectSample> {
    state
        .store
        .read(|c| {
            rows::select_where::<ProjectSample>(
                c,
                "id = ? AND project_id = ?",
                [sample_id, project_id],
                "",
            )
        })?
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found("Sample not found in this project"))
}

/// Edit one date. Analyses deployed from it keep their own copies.
async fn update(
    Extract(state): Extract<State>,
    caller: Caller,
    Path((project_id, sample_id)): Path<(String, String)>,
    body: Bytes,
) -> ApiResult {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "ProjectSampleUpdate", &mut problems);
    let name = f.opt_str("sample_name", Some(200));
    let name = match name {
        Some(n) if n.trim().is_empty() => {
            f.value_error(Some("sample_name"), "sample_name cannot be blank");
            None
        }
        other => other.map(|n| n.trim().to_owned()),
    };
    let age = f.opt_float("age_bp", schemas::AGE);
    let error = f.opt_float("error_1sigma", schemas::ERROR);
    let material = f.opt_str("material_type", Some(100));
    let sample_type = f.opt_literal("sample_type", &["terrestrial", "marine"]);
    let curve = f.opt_str("curve_id", None);
    if let Some(id) = &curve
        && !curve_exists(&state.store, id)
    {
        let message = unknown_curve(id);
        f.value_error(Some("curve_id"), &message);
    }
    let delta_r = f.opt_float("delta_r", schemas::DELTA_R);
    let delta_r_error = f.opt_float("delta_r_error", schemas::DELTA_R_ERROR);
    let latitude = f.opt_float("latitude", schemas::LATITUDE);
    let longitude = f.opt_float("longitude", schemas::LONGITUDE);
    let depth = f.opt_float("depth_cm", schemas::DEPTH);
    let ring_offset = f.opt_float("ring_offset", schemas::RING_OFFSET);
    let ring_offset_error = f.opt_float("ring_offset_error", schemas::RING_OFFSET_ERROR);
    let inbuilt_years = f.opt_float("inbuilt_years", schemas::INBUILT);
    let inbuilt_shape = f.opt_literal("inbuilt_shape", &["uniform", "exponential"]);
    let location = f.opt_str("location", Some(500));
    let notes = f.opt_str("notes", Some(2000));
    let context = f.opt_str("default_context_name", Some(200));
    let tpq = f.opt_float("tpq_age_bp", schemas::AGE);
    let taq = f.opt_float("taq_age_bp", schemas::AGE);
    let sent: Vec<&str> = [
        "sample_name",
        "age_bp",
        "error_1sigma",
        "material_type",
        "sample_type",
        "curve_id",
        "delta_r",
        "delta_r_error",
        "latitude",
        "longitude",
        "depth_cm",
        "ring_offset",
        "ring_offset_error",
        "inbuilt_years",
        "inbuilt_shape",
        "location",
        "notes",
        "default_context_name",
        "tpq_age_bp",
        "taq_age_bp",
    ]
    .into_iter()
    .filter(|k| f.sent(k))
    .collect();
    problems.finish()?;
    require(&state, &caller, &project_id)?;
    let mut s = find(&state, &project_id, &sample_id)?;
    let before = s.clone();
    for field in &sent {
        match *field {
            "sample_name" => s.sample_name = name.clone().unwrap_or_default(),
            "age_bp" => s.age_bp = age.unwrap_or_default(),
            "error_1sigma" => s.error_1sigma = error.unwrap_or_default(),
            "material_type" => s.material_type = material.clone().unwrap_or_default(),
            "sample_type" => s.sample_type = sample_type.clone().unwrap_or_default(),
            "curve_id" => s.curve_id = curve.clone(),
            "delta_r" => s.delta_r = delta_r,
            "delta_r_error" => s.delta_r_error = delta_r_error,
            "latitude" => s.latitude = latitude,
            "longitude" => s.longitude = longitude,
            "depth_cm" => s.depth_cm = depth,
            "ring_offset" => s.ring_offset = ring_offset,
            "ring_offset_error" => s.ring_offset_error = ring_offset_error,
            "inbuilt_years" => s.inbuilt_years = inbuilt_years,
            "inbuilt_shape" => s.inbuilt_shape = inbuilt_shape.clone(),
            "location" => s.location = location.clone(),
            "notes" => s.notes = notes.clone(),
            "default_context_name" => {
                s.default_context_name = context
                    .as_ref()
                    .and_then(|c| Some(c.trim().to_owned()).filter(|c| !c.is_empty()));
            }
            "tpq_age_bp" => s.tpq_age_bp = tpq,
            "taq_age_bp" => s.taq_age_bp = taq,
            _ => unreachable!(),
        }
    }
    if sent.contains(&"delta_r") || sent.contains(&"delta_r_error") {
        s.delta_r_source = None;
    }
    if let (Some(tpq), Some(taq)) = (s.tpq_age_bp, s.taq_age_bp)
        && taq > tpq
    {
        return Err(ApiError::new(
            axum::http::StatusCode::UNPROCESSABLE_ENTITY,
            schemas::BOUNDS_CONTRADICTORY,
        ));
    }
    if s.latitude.is_none() != s.longitude.is_none() {
        let (present, missing) = if s.longitude.is_none() {
            ("latitude", "longitude")
        } else {
            ("longitude", "latitude")
        };
        return Err(ApiError::new(
            axum::http::StatusCode::UNPROCESSABLE_ENTITY,
            format!(
                "this edit would leave the date with a {present} and no {missing} -- a position needs both halves, so send the other one too, or clear them together"
            ),
        ));
    }
    if s != before {
        s.updated_at = Some(Timestamp::now());
        state.store.write(|tx| rows::update(tx, &s))?;
    }
    Ok(ok(serialize(&s)))
}

/// Remove one date. Analyses deployed from it keep their copies.
async fn delete_one(
    Extract(state): Extract<State>,
    caller: Caller,
    Path((project_id, sample_id)): Path<(String, String)>,
) -> ApiResult {
    require(&state, &caller, &project_id)?;
    find(&state, &project_id, &sample_id)?;
    state
        .store
        .write(|tx| rows::delete_where::<ProjectSample>(tx, "id = ?", [&sample_id]))?;
    Ok(ok(json!({"deleted_sample_id": sample_id})))
}

/// These ids' dates in this project, in the store's order, or the 404 that
/// names the ones that are not there.
fn selection(state: &State, project_id: &str, ids: &[String]) -> ApiResult<Vec<ProjectSample>> {
    let marks = vec!["?"; ids.len()].join(", ");
    let mut params: Vec<&dyn rusqlite::ToSql> = vec![&project_id];
    params.extend(ids.iter().map(|i| i as &dyn rusqlite::ToSql));
    let found: Vec<ProjectSample> = state.store.read(|c| {
        rows::select_where(
            c,
            &format!("project_id = ? AND id IN ({marks})"),
            params.as_slice(),
            "id",
        )
    })?;
    let missing: Vec<&str> = ids
        .iter()
        .filter(|id| !found.iter().any(|s| &s.id == *id))
        .map(String::as_str)
        .collect();
    if !missing.is_empty() {
        return Err(ApiError::not_found(format!(
            "These dates are not in this project's collection: {}",
            missing.join(", ")
        )));
    }
    Ok(found)
}

/// One value across a selection: a curve (with the sample type it
/// implies), a Delta-R (marine dates only), or a shared position.
async fn bulk_update(
    Extract(state): Extract<State>,
    caller: Caller,
    Path(project_id): Path<String>,
    body: Bytes,
) -> ApiResult {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "ProjectSampleBulkUpdate", &mut problems);
    let ids = schemas::unique_ids(&mut f, "sample_ids", Some(1), Some(500));
    let curve = f.opt_str("curve_id", None);
    if f.sent("curve_id") && f.raw("curve_id").is_some_and(Value::is_null) {
        f.value_error(
            Some("curve_id"),
            "curve_id cannot be cleared -- every date needs a curve",
        );
    } else if let Some(id) = &curve
        && !curve_exists(&state.store, id)
    {
        let message = unknown_curve(id);
        f.value_error(Some("curve_id"), &message);
    }
    let delta_r = f.opt_float("delta_r", schemas::DELTA_R);
    let delta_r_error = f.opt_float("delta_r_error", schemas::DELTA_R_ERROR);
    let latitude = f.opt_float("latitude", schemas::LATITUDE);
    let longitude = f.opt_float("longitude", schemas::LONGITUDE);
    let source = f.opt_dict("delta_r_source");
    let sent = |k: &str| f.sent(k);
    let (s_curve, s_dr, s_dre, s_lat, s_lon, s_src) = (
        sent("curve_id"),
        sent("delta_r"),
        sent("delta_r_error"),
        sent("latitude"),
        sent("longitude"),
        sent("delta_r_source"),
    );
    if f.clean() {
        let refusal = if !(s_curve || s_dr || s_dre || s_lat || s_lon) {
            Some("nothing to apply -- send curve_id, delta_r, a coordinate, or any combination of them".to_owned())
        } else if s_dr && delta_r.is_some() && delta_r_error.is_none() {
            Some("delta_r_error is required when delta_r is given -- a reservoir offset has its own uncertainty, and omitting it would understate the uncertainty of every date it is applied to".to_owned())
        } else if s_dre && !s_dr {
            Some(
                "delta_r_error cannot be set on its own -- send the offset it belongs to"
                    .to_owned(),
            )
        } else if s_src && source.is_some() && (!s_dr || delta_r.is_none()) {
            Some("delta_r_source cannot be sent without the delta_r it describes -- a derivation with no derived value beside it would claim to explain whatever was already there".to_owned())
        } else if s_lat != s_lon {
            let missing = if !s_lon { "longitude" } else { "latitude" };
            Some(format!(
                "{missing} is required when the other half of the coordinate is given -- a bulk update writes a position onto every date in the selection, and half of one is not a position"
            ))
        } else if s_lat && latitude.is_none() != longitude.is_none() {
            Some("a coordinate is cleared as a pair -- send both latitude and longitude as null, or neither".to_owned())
        } else {
            None
        };
        if let Some(message) = refusal {
            f.value_error(None, &message);
        }
    }
    problems.finish()?;
    require(&state, &caller, &project_id)?;
    if s_curve {
        authorise_curve_ids(&state, &caller, curve.as_deref())?;
    }
    let mut samples = selection(&state, &project_id, &ids.unwrap_or_default())?;
    let mut changed = std::collections::HashSet::new();
    let mut skipped: Vec<String> = Vec::new();
    let mut type_changed = 0;
    if s_curve {
        let curve = curve.unwrap_or_default();
        let kind = if curve_sample_type(&state, &curve).as_deref() == Some("marine") {
            "marine"
        } else {
            "terrestrial"
        };
        for s in &mut samples {
            s.curve_id = Some(curve.clone());
            let current = if s.sample_type.is_empty() {
                "terrestrial"
            } else {
                s.sample_type.as_str()
            };
            if current != kind {
                s.sample_type = kind.to_owned();
                type_changed += 1;
            }
            changed.insert(s.id.clone());
        }
    }
    if s_lat {
        for s in &mut samples {
            s.latitude = latitude;
            s.longitude = longitude;
            changed.insert(s.id.clone());
        }
    }
    if s_dr {
        for s in &mut samples {
            let marine = s.sample_type == "marine"
                || curve_sample_type(&state, s.curve_id.as_deref().unwrap_or("intcal20"))
                    .as_deref()
                    == Some("marine");
            if marine {
                s.delta_r = delta_r;
                s.delta_r_error = if delta_r.is_some() {
                    delta_r_error
                } else {
                    None
                };
                s.delta_r_source = json_of(&source);
                changed.insert(s.id.clone());
            } else {
                skipped.push(s.id.clone());
            }
        }
    }
    let now = Some(Timestamp::now());
    state.store.write(|tx| {
        for s in samples.iter_mut().filter(|s| changed.contains(&s.id)) {
            s.updated_at = now;
            rows::update(tx, s)?;
        }
        Ok(())
    })?;
    let updated = samples.iter().filter(|s| changed.contains(&s.id)).count();
    Ok(ok(json!({
        "updated_count": updated,
        "skipped_count": skipped.len(),
        "skipped_sample_ids": skipped,
        "sample_type_changed_count": type_changed,
        "items": samples.iter().map(serialize).collect::<Vec<_>>(),
    })))
}

/// Remove several dates at once, all or nothing on the ids.
async fn bulk_delete(
    Extract(state): Extract<State>,
    caller: Caller,
    Path(project_id): Path<String>,
    body: Bytes,
) -> ApiResult {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "ProjectSampleBulkDelete", &mut problems);
    let ids = schemas::unique_ids(&mut f, "sample_ids", Some(1), Some(500));
    problems.finish()?;
    require(&state, &caller, &project_id)?;
    let samples = selection(&state, &project_id, &ids.unwrap_or_default())?;
    let mut found: Vec<String> = samples.iter().map(|s| s.id.clone()).collect();
    found.sort();
    state.store.write(|tx| {
        for id in &found {
            rows::delete_where::<ProjectSample>(tx, "id = ?", [id])?;
        }
        Ok(())
    })?;
    Ok(ok(
        json!({"deleted_count": found.len(), "deleted_sample_ids": found}),
    ))
}

/// A new analysis in this project from a selection of its dates, copied,
/// grouped into contexts by their default context names.
async fn deploy(
    Extract(state): Extract<State>,
    caller: Caller,
    Path(project_id): Path<String>,
    body: Bytes,
) -> ApiResult {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "DeployRequest", &mut problems);
    let name = f.str_or("name", "Untitled", Some(200)).map(|n| {
        let t = n.trim().to_owned();
        if t.is_empty() {
            "Untitled".to_owned()
        } else {
            t
        }
    });
    let ids = schemas::unique_ids(&mut f, "sample_ids", Some(1), None);
    let kind = f.literal_or("analysis_type", &["bayesian", "calibration"], "bayesian");
    problems.finish()?;
    require(&state, &caller, &project_id)?;
    let ids = ids.unwrap_or_default();
    let found = selection(&state, &project_id, &ids)?;
    let ordered: Vec<&ProjectSample> = ids
        .iter()
        .map(|id| found.iter().find(|s| &s.id == id).unwrap())
        .collect();
    let mut analysis_type = kind.unwrap_or_default();
    if analysis_type == "calibration" && ordered.len() > 1 {
        analysis_type = "group_calibration".to_owned();
    }
    let now = Some(Timestamp::now());
    let analysis = Analysis {
        id: radical_store::new_id(),
        name: name.unwrap_or_default(),
        analysis_type,
        status: Some("pending".to_owned()),
        created_at: now,
        updated_at: now,
        project_id: Some(project_id.clone()),
        bayesian_config: Some(json!({})),
        owner_id: caller.owner().map(str::to_owned),
        ..Analysis::default()
    };
    let mut groups: Vec<(String, String)> = Vec::new();
    for src in &ordered {
        let name = src
            .default_context_name
            .as_deref()
            .unwrap_or("")
            .trim()
            .to_owned();
        if !name.is_empty() && !groups.iter().any(|(n, _)| *n == name) {
            groups.push((name, radical_store::new_id()));
        }
    }
    state.store.write(|tx| {
        rows::insert(tx, &analysis)?;
        for (name, id) in &groups {
            rows::insert(
                tx,
                &SampleGroup {
                    id: id.clone(),
                    analysis_id: analysis.id.clone(),
                    name: name.clone(),
                    created_at: now,
                    ..SampleGroup::default()
                },
            )?;
        }
        for (position, src) in ordered.iter().enumerate() {
            let context = src
                .default_context_name
                .as_deref()
                .unwrap_or("")
                .trim()
                .to_owned();
            rows::insert(
                tx,
                &RadiocarbonSample {
                    id: radical_store::new_id(),
                    analysis_id: analysis.id.clone(),
                    sort_order: position as i64,
                    sample_name: src.sample_name.clone(),
                    age_bp: src.age_bp,
                    error_1sigma: src.error_1sigma,
                    material_type: src.material_type.clone(),
                    sample_type: src.sample_type.clone(),
                    curve_id: src.curve_id.clone(),
                    delta_r: src.delta_r,
                    delta_r_error: src.delta_r_error,
                    delta_r_source: src.delta_r_source.clone(),
                    latitude: src.latitude,
                    longitude: src.longitude,
                    depth_cm: src.depth_cm,
                    ring_offset: src.ring_offset,
                    ring_offset_error: src.ring_offset_error,
                    inbuilt_years: src.inbuilt_years,
                    inbuilt_shape: src.inbuilt_shape.clone(),
                    location: src.location.clone(),
                    notes: src.notes.clone(),
                    group_id: groups
                        .iter()
                        .find(|(n, _)| *n == context)
                        .map(|(_, id)| id.clone()),
                    tpq_age_bp: src.tpq_age_bp,
                    taq_age_bp: src.taq_age_bp,
                    created_at: now,
                    ..RadiocarbonSample::default()
                },
            )?;
        }
        Ok(())
    })?;
    Ok(ok(json!({
        "id": analysis.id,
        "name": analysis.name,
        "analysis_type": analysis.analysis_type,
        "status": analysis.status,
        "project_id": analysis.project_id,
        "measurements_count": ordered.len(),
        "contexts_created": groups.len(),
    })))
}

pub fn router() -> Router<State> {
    Router::new()
        .route(
            "/api/projects/{project_id}/samples",
            post(create)
                .get(list)
                .patch(bulk_update)
                .delete(bulk_delete),
        )
        .route(
            "/api/projects/{project_id}/samples/{sample_id}",
            patch(update).delete(delete_one),
        )
        .route("/api/projects/{project_id}/deploy", post(deploy))
}
