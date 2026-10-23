//! The calibration curves (`api/curves.py`): the three built in and the ones
//! the user has imported or mixed -- listing and describing them, drawing and
//! comparing them, what a date from a given year would look like, how well
//! each part of a curve can date anything, and adding, describing,
//! archiving and removing stored ones.
//!
//! A stored curve's numbers never change: PATCH writes editorial fields only.
//! A curve in use cannot be deleted; archiving hides it from the pickers and
//! leaves it resolvable, so older analyses still rerun.

use std::collections::HashMap;
use std::sync::{Arc, LazyLock, Mutex};

use axum::Router;
use axum::body::Bytes;
use axum::extract::{Path, Query, State as Extract};
use axum::http::{HeaderMap, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use radical_core::calibration::{SIGMA_1, SIGMA_2, hpd_ranges, point_estimate};
use radical_core::curve_mix::mix;
use radical_core::curve_registry::{
    BUILTINS, Builtin, Measured, MixRecipe, StoredNames, builtin_spec, content_hash, find_mixed,
    measure, resolve_alias,
};
use radical_core::curve_view::{self, DEFAULT_MAX_POINTS, MAX_MAX_POINTS};
use radical_core::measurement::Curves;
use radical_core::pyfmt::g;
use radical_core::resolution::{self, DEFAULT_U0, ResolutionProfile};
use radical_core::{CalibrationCurve, Mode};
use radical_io::curve_import::{self, Calendar, MAX_CONTENT_BYTES};
use radical_store::Timestamp;
use radical_store::rows::{self, CalibrationCurve as CurveRow, ProjectSample, RadiocarbonSample};
use serde_json::{Map, Value, json};

use crate::form::{self, Form};
use crate::routes::analysis::ranges_json;
use crate::validate::{self, Bounds, Fields, Problems};
use crate::{ApiError, ApiResult, State, ok};

type Answer = ApiResult<Response>;

fn status(code: StatusCode, detail: Value) -> ApiError {
    ApiError {
        status: code,
        detail,
    }
}

fn created(data: Value) -> Response {
    (
        StatusCode::CREATED,
        axum::Json(json!({"success": true, "data": data})),
    )
        .into_response()
}

// --------------------------------------------------------------------------
// The registry
// --------------------------------------------------------------------------

/// Every stored curve, oldest first, archived included.
fn stored_rows(state: &State) -> radical_store::Result<Vec<CurveRow>> {
    state
        .store
        .read(|c| rows::select_where(c, "1 = 1", [], "created_at, rowid"))
}

fn stored_row(state: &State, curve_id: &str) -> radical_store::Result<Option<CurveRow>> {
    state.store.read(|c| rows::get(c, curve_id))
}

fn aliases_of(row: &CurveRow) -> Vec<String> {
    row.aliases
        .as_ref()
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default()
}

fn names(rows: &[CurveRow]) -> Vec<StoredNames> {
    rows.iter()
        .map(|r| StoredNames {
            id: r.id.clone(),
            name: r.name.clone(),
            aliases: aliases_of(r),
        })
        .collect()
}

/// `curve_registry.resolve_alias` over every stored curve.
fn alias(state: &State, name: &str) -> radical_store::Result<Option<String>> {
    Ok(resolve_alias(name, &names(&stored_rows(state)?)))
}

fn builtin(curve_id: &str) -> Option<&'static Builtin> {
    BUILTINS.iter().find(|b| b.id == curve_id)
}

/// Whether a curve id names any curve, archived included
/// (`curve_registry.exists`).
fn exists(state: &State, curve_id: &str) -> radical_store::Result<bool> {
    Ok(builtin(curve_id).is_some() || stored_row(state, curve_id)?.is_some())
}

/// Each curve's measured half, measured once: a stored curve's numbers
/// never change, and a deleted one's id is forgotten with it.
static MEASURED: LazyLock<Mutex<HashMap<String, Measured>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

fn measured(state: &State, curve_id: &str) -> Result<Measured, radical_core::Error> {
    if let Some(m) = MEASURED
        .lock()
        .unwrap_or_else(|p| p.into_inner())
        .get(curve_id)
    {
        return Ok(m.clone());
    }
    let m = match builtin(curve_id) {
        Some(_) => builtin_spec(curve_id)?.measured,
        None => measure(state.curves.curve(curve_id)?)?,
    };
    MEASURED
        .lock()
        .unwrap_or_else(|p| p.into_inner())
        .insert(curve_id.to_owned(), m.clone());
    Ok(m)
}

pub fn forget(state: &State, curve_id: &str) {
    MEASURED
        .lock()
        .unwrap_or_else(|p| p.into_inner())
        .remove(curve_id);
    state.curves.forget(curve_id);
}

fn with_measured(mut spec: Map<String, Value>, m: &Measured) -> Map<String, Value> {
    spec.insert("data_points".into(), json!(m.data_points));
    spec.insert("cal_min".into(), json!(m.cal_min));
    spec.insert("cal_max".into(), json!(m.cal_max));
    spec.insert("c14_min".into(), json!(m.c14_min));
    spec.insert("c14_max".into(), json!(m.c14_max));
    spec.insert("resolution".into(), json!(m.resolution));
    spec.insert("content_sha256".into(), json!(m.content_sha256));
    spec
}

fn builtin_json(state: &State, b: &Builtin) -> Result<Value, radical_core::Error> {
    let m = measured(state, b.id)?;
    let spec = json!({
        "id": b.id, "name": b.name, "type": b.kind_of_curve,
        "sample_type": b.sample_type, "description": b.description,
        "reference": b.reference, "year": b.year, "doi": b.doi, "kind": "builtin",
        "aliases": b.aliases,
    });
    let mut spec = with_measured(spec.as_object().unwrap().clone(), &m);
    spec.insert("archived".into(), json!(false));
    spec.insert("source_filename".into(), Value::Null);
    Ok(Value::Object(spec))
}

fn row_json(state: &State, r: &CurveRow) -> Result<Value, radical_core::Error> {
    let m = measured(state, &r.id)?;
    let year = r
        .created_at
        .map_or(0, |t| t.isoformat()[..4].parse::<i64>().unwrap_or(0));
    let or_default = |v: &str, d: &str| {
        if v.is_empty() {
            d.to_owned()
        } else {
            v.to_owned()
        }
    };
    let spec = json!({
        "id": r.id, "name": r.name,
        "type": or_default(&r.curve_type, "terrestrial"),
        "sample_type": or_default(&r.sample_type, "terrestrial"),
        "description": r.description.clone().unwrap_or_default(),
        "reference": r.reference.clone().unwrap_or_default(),
        "year": year, "doi": r.doi, "kind": r.kind, "aliases": aliases_of(r),
    });
    let mut spec = with_measured(spec.as_object().unwrap().clone(), &m);
    spec.insert("archived".into(), json!(r.archived));
    spec.insert("source_filename".into(), json!(r.source_filename));
    Ok(Value::Object(spec))
}

/// `curve_registry.get_spec`.
pub(crate) fn spec(state: &State, curve_id: &str) -> ApiResult<Result<Value, String>> {
    if let Some(b) = builtin(curve_id) {
        return Ok(builtin_json(state, b).map_err(|e| e.to_string()));
    }
    match stored_row(state, curve_id)? {
        Some(r) => Ok(row_json(state, &r).map_err(|e| e.to_string())),
        None => Ok(Err(format!("Unknown calibration curve '{curve_id}'."))),
    }
}

/// `curve_registry.list_specs`: the builtins, then the stored curves in the
/// order they were added; any that cannot be loaded left out.
fn list_specs(state: &State, include_archived: bool) -> ApiResult<Vec<Value>> {
    let mut out: Vec<Value> = BUILTINS
        .iter()
        .filter_map(|b| builtin_json(state, b).ok())
        .collect();
    for r in stored_rows(state)? {
        if r.archived && !include_archived {
            continue;
        }
        if let Ok(s) = row_json(state, &r) {
            out.push(s);
        }
    }
    Ok(out)
}

fn visible_or_404(state: &State, curve_id: &str) -> ApiResult<()> {
    if exists(state, curve_id)? {
        Ok(())
    } else {
        Err(ApiError::not_found(format!(
            "No calibration curve '{curve_id}'."
        )))
    }
}

/// The curve's numbers, or the 404 the registry's refusal is.
fn resolve<'a>(state: &'a State, curve_id: &str) -> ApiResult<&'a CalibrationCurve> {
    state
        .curves
        .curve(curve_id)
        .map_err(|e| ApiError::not_found(e.to_string()))
}

/// `curve_registry.usage`: what is calibrated against this curve.
fn usage(state: &State, curve_id: &str) -> ApiResult<Value> {
    let (samples, project_samples, curves) = state.store.read(|c| {
        let s: Vec<RadiocarbonSample> = rows::select_where(c, "curve_id = ?", [curve_id], "rowid")?;
        let p: Vec<ProjectSample> = rows::select_where(c, "curve_id = ?", [curve_id], "rowid")?;
        let m: Vec<CurveRow> = rows::select_where(c, "kind = 'mixed'", [], "rowid")?;
        Ok((s, p, m))
    })?;
    let mut analysis_ids: Vec<String> = Vec::new();
    for s in samples {
        if !analysis_ids.contains(&s.analysis_id) {
            analysis_ids.push(s.analysis_id);
        }
    }
    let mut project_ids: Vec<String> = Vec::new();
    for p in project_samples {
        if !project_ids.contains(&p.project_id) {
            project_ids.push(p.project_id);
        }
    }
    let mixed: Vec<String> = curves
        .into_iter()
        .filter(|r| {
            let recipe = r.recipe.clone().unwrap_or_default();
            recipe["a"] == curve_id || recipe["b"] == curve_id
        })
        .map(|r| r.id)
        .collect();
    let in_use = !(analysis_ids.is_empty() && project_ids.is_empty() && mixed.is_empty());
    Ok(json!({
        "analysis_ids": analysis_ids,
        "project_ids": project_ids,
        "mixed_curve_ids": mixed,
        "others_count": 0,
        "in_use": in_use,
    }))
}

/// `_stored_or_404`: a stored curve, for the routes that change one; a
/// builtin's 403 says why it cannot be.
fn stored_or_404(state: &State, curve_id: &str) -> ApiResult<CurveRow> {
    match stored_row(state, curve_id)? {
        Some(r) => Ok(r),
        None if builtin(curve_id).is_some() => Err(ApiError::new(
            StatusCode::FORBIDDEN,
            format!(
                "'{curve_id}' is one of the curves shipped with this app and cannot be edited or removed."
            ),
        )),
        None => Err(ApiError::not_found(format!(
            "Unknown calibration curve '{curve_id}'."
        ))),
    }
}

// --------------------------------------------------------------------------
// Query parameters, read as FastAPI reads them
// --------------------------------------------------------------------------

fn query_value(q: &HashMap<String, String>) -> Value {
    Value::Object(q.iter().map(|(k, v)| (k.clone(), json!(v))).collect())
}

fn query<T>(q: &HashMap<String, String>, read: impl FnOnce(&mut Fields) -> T) -> ApiResult<T> {
    let input = query_value(q);
    let mut problems = Problems::default();
    let mut f = Fields::new(&input, vec![json!("query")], "query", &mut problems);
    let out = read(&mut f);
    problems.finish()?;
    Ok(out)
}

// --------------------------------------------------------------------------
// Reading
// --------------------------------------------------------------------------

async fn list_curves(
    Extract(state): Extract<State>,
    Query(q): Query<HashMap<String, String>>,
) -> ApiResult {
    let (limit, archived) = query(&q, |f| {
        (
            f.int_or("limit", 100, Bounds::NONE),
            f.bool_or("include_archived", false),
        )
    })?;
    let (limit, archived) = (limit.unwrap_or(100), archived.unwrap_or(false));
    let specs = list_specs(&state, archived)?;
    let items: Vec<&Value> = specs
        .iter()
        .take(usize::try_from(limit).unwrap_or(0))
        .collect();
    Ok(ok(json!({
        "items": items,
        "total": specs.len(),
        "page": 1,
        "per_page": limit,
        "total_pages": if specs.is_empty() { 0 } else { 1 },
    })))
}

fn max_points(f: &mut Fields, default: usize) -> Option<i64> {
    f.int_or(
        "max_points",
        default as i64,
        Bounds::range(2.0, MAX_MAX_POINTS as f64),
    )
}

async fn compare_curves(
    Extract(state): Extract<State>,
    Query(q): Query<HashMap<String, String>>,
) -> ApiResult {
    let (a, b, from, to, points) = query(&q, |f| {
        (
            f.string("a", None, None),
            f.string("b", None, None),
            f.opt_float("cal_from", Bounds::NONE),
            f.opt_float("cal_to", Bounds::NONE),
            max_points(f, DEFAULT_MAX_POINTS),
        )
    })?;
    let (a, b) = (a.unwrap_or_default(), b.unwrap_or_default());
    visible_or_404(&state, &a)?;
    visible_or_404(&state, &b)?;
    let (curve_a, curve_b) = (resolve(&state, &a)?, resolve(&state, &b)?);
    let c = curve_view::compare(curve_a, curve_b, from, to, points.unwrap_or(800) as usize)
        .map_err(|e| ApiError::new(StatusCode::UNPROCESSABLE_ENTITY, e.to_string()))?;
    Ok(ok(json!({
        "cal_from": c.cal_from,
        "cal_to": c.cal_to,
        "points": c.points,
        "point_count": c.points.len(),
        "points_in_window": c.points_in_window,
        "decimated": c.decimated,
        "overlap_cal_min": c.overlap_cal_min,
        "overlap_cal_max": c.overlap_cal_max,
        "a": a,
        "b": b,
    })))
}

async fn get_curve(Extract(state): Extract<State>, Path(curve_id): Path<String>) -> ApiResult {
    visible_or_404(&state, &curve_id)?;
    let s = spec(&state, &curve_id)?.map_err(ApiError::not_found)?;
    Ok(ok(s))
}

async fn by_type(Extract(state): Extract<State>, Path(curve_type): Path<String>) -> ApiResult {
    let specs: Vec<Value> = list_specs(&state, false)?
        .into_iter()
        .filter(|s| s["type"] == curve_type.as_str())
        .collect();
    Ok(ok(json!(specs)))
}

fn window_json(w: &curve_view::Window) -> Value {
    json!({
        "cal_from": w.cal_from,
        "cal_to": w.cal_to,
        "points": w.points,
        "point_count": w.points.len(),
        "points_in_window": w.points_in_window,
        "decimated": w.decimated,
        "curve_cal_min": w.curve_cal_min,
        "curve_cal_max": w.curve_cal_max,
    })
}

async fn points(
    Extract(state): Extract<State>,
    Path(curve_id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> ApiResult {
    let (from, to, n) = query(&q, |f| {
        (
            f.opt_float("cal_from", Bounds::NONE),
            f.opt_float("cal_to", Bounds::NONE),
            max_points(f, DEFAULT_MAX_POINTS),
        )
    })?;
    visible_or_404(&state, &curve_id)?;
    let curve = resolve(&state, &curve_id)?;
    let w = curve_view::window(curve, from, to, n.unwrap_or(800) as usize)
        .map_err(ApiError::internal)?;
    let mut data = window_json(&w);
    data["curve_id"] = json!(curve_id);
    Ok(ok(data))
}

async fn points_csv(
    Extract(state): Extract<State>,
    Path(curve_id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Answer {
    let (from, to, n) = query(&q, |f| {
        (
            f.opt_float("cal_from", Bounds::NONE),
            f.opt_float("cal_to", Bounds::NONE),
            max_points(f, MAX_MAX_POINTS),
        )
    })?;
    visible_or_404(&state, &curve_id)?;
    let curve = resolve(&state, &curve_id)?;
    let s = spec(&state, &curve_id)?.map_err(ApiError::not_found)?;
    let w = curve_view::window(curve, from, to, n.unwrap_or(5000) as usize)
        .map_err(ApiError::internal)?;
    let name = format!("{curve_id}-{:.0}-{:.0}.csv", w.cal_from, w.cal_to);
    Ok(attachment(
        "text/csv; charset=utf-8",
        &name,
        curve_view::to_csv(&w, s["name"].as_str().unwrap_or_default()),
    ))
}

fn attachment(content_type: &str, filename: &str, body: String) -> Response {
    (
        [
            (header::CONTENT_TYPE, content_type.to_owned()),
            (
                header::CONTENT_DISPOSITION,
                format!("attachment; filename=\"{filename}\""),
            ),
        ],
        body,
    )
        .into_response()
}

/// A date asked for by calendar year: the curve's own age there,
/// calibrated back with the stated error.
async fn simulate(
    Extract(state): Extract<State>,
    Path(curve_id): Path<String>,
    body: Bytes,
) -> ApiResult {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "SimulateRequest", &mut problems);
    let cal_age = f.float("cal_age", Bounds::range(0.0, 60000.0));
    let error = f.float_or("error", 25.0, Bounds::above(0.0, 10000.0));
    let delta_r = f.opt_float("delta_r", Bounds::range(-5000.0, 5000.0));
    let delta_r_error = f.opt_float("delta_r_error", Bounds::range(0.0, 5000.0));
    problems.finish()?;
    let (cal_age, error) = (cal_age.unwrap_or_default(), error.unwrap_or(25.0));
    visible_or_404(&state, &curve_id)?;
    let curve = resolve(&state, &curve_id)?;
    let cal = curve.cal_ages();
    let lo = cal.iter().copied().fold(f64::INFINITY, f64::min);
    let hi = cal.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    if !(lo <= cal_age && cal_age <= hi) {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            format!(
                "{} cal BP is outside this curve, which covers {}-{} cal BP.",
                g(cal_age, 6),
                g(lo, 6),
                g(hi, 6)
            ),
        ));
    }
    let (c14, sigma) = curve.interpolate(&[cal_age]).map_err(ApiError::internal)?;
    let refused = |e: radical_core::Error| ApiError::bad_request(e.to_string());
    let probs = curve
        .calibrate(c14[0], error, delta_r, delta_r_error, Mode::Standard)
        .map_err(refused)?;
    let point = point_estimate(cal, &probs).map_err(refused)?;
    Ok(ok(json!({
        "curve_id": curve_id,
        "cal_age": cal_age,
        "c14_age": c14[0],
        "curve_error": sigma[0],
        "error": error,
        "hpd_68_ranges": ranges_json(&hpd_ranges(cal, &probs, SIGMA_1).map_err(refused)?),
        "hpd_95_ranges": ranges_json(&hpd_ranges(cal, &probs, SIGMA_2).map_err(refused)?),
        "point_estimates": {
            "mean": point.mean, "median": point.median, "mode": point.mode, "std": point.std,
        },
        "probability_distribution": curve.display_distribution(cal, &probs, 200, 1e-4),
    })))
}

// --------------------------------------------------------------------------
// Resolution
// --------------------------------------------------------------------------

/// The lattice whole-curve profiles are computed on.
const BASE_STRIDE: usize = 5;
/// Beyond this span a window is sliced from the whole-curve profile.
const WIDE_SPAN_YEARS: f64 = 20000.0;

type ProfileKey = (String, u64);
static BASE: LazyLock<Mutex<HashMap<ProfileKey, Arc<ResolutionProfile>>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

fn has_full_profile(sha: &str, u0: f64) -> bool {
    BASE.lock()
        .unwrap_or_else(|p| p.into_inner())
        .contains_key(&(sha.to_owned(), u0.to_bits()))
}

/// `resolution.full_profile`: the whole curve, computed once per (curve,
/// u0), thinned to the nearest multiple of BASE_STRIDE.
fn full_profile(
    curve: &CalibrationCurve,
    sha: &str,
    u0: f64,
    stride: usize,
) -> ApiResult<ResolutionProfile> {
    let key = (sha.to_owned(), u0.to_bits());
    let mut cache = BASE.lock().unwrap_or_else(|p| p.into_inner());
    let base = match cache.get(&key) {
        Some(b) => Arc::clone(b),
        None => {
            let b = Arc::new(
                resolution::profile(curve, None, None, u0, BASE_STRIDE)
                    .map_err(ApiError::internal)?,
            );
            if cache.len() >= 6 {
                let first = cache.keys().next().cloned();
                if let Some(k) = first {
                    cache.remove(&k);
                }
            }
            cache.insert(key, Arc::clone(&b));
            b
        }
    };
    drop(cache);
    let every = ((stride as f64 / BASE_STRIDE as f64).round_ties_even() as usize).max(1);
    let pick = |v: &[f64]| v.iter().step_by(every).copied().collect::<Vec<f64>>();
    Ok(ResolutionProfile {
        cal_ages: pick(&base.cal_ages),
        mean: pick(&base.mean),
        p05: pick(&base.p05),
        p95: pick(&base.p95),
        uncertainty: pick(&base.uncertainty),
        u0: base.u0,
    })
}

fn slice_profile(p: ResolutionProfile, lo: f64, hi: f64) -> ResolutionProfile {
    let keep: Vec<bool> = p.cal_ages.iter().map(|&c| c >= lo && c <= hi).collect();
    let pick = |v: &[f64]| {
        v.iter()
            .zip(&keep)
            .filter(|(_, k)| **k)
            .map(|(x, _)| *x)
            .collect::<Vec<f64>>()
    };
    ResolutionProfile {
        cal_ages: pick(&p.cal_ages),
        mean: pick(&p.mean),
        p05: pick(&p.p05),
        p95: pick(&p.p95),
        uncertainty: pick(&p.uncertainty),
        u0: p.u0,
    }
}

fn profile_json(p: &ResolutionProfile) -> Value {
    json!({
        "u0": p.u0,
        "points": (0..p.cal_ages.len()).map(|i| json!([
            p.cal_ages[i], p.mean[i], p.p05[i], p.p95[i], p.uncertainty[i],
        ])).collect::<Vec<_>>(),
    })
}

fn u0_and_stride(f: &mut Fields) -> (Option<f64>, Option<i64>) {
    (
        f.float_or("u0", DEFAULT_U0, Bounds::above(0.0, 500.0)),
        f.int_or("stride", 5, Bounds::range(1.0, 200.0)),
    )
}

async fn curve_resolution(
    Extract(state): Extract<State>,
    Path(curve_id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> ApiResult {
    let (from, to, (u0, stride)) = query(&q, |f| {
        (
            f.opt_float("cal_from", Bounds::NONE),
            f.opt_float("cal_to", Bounds::NONE),
            u0_and_stride(f),
        )
    })?;
    let (u0, stride) = (u0.unwrap_or(DEFAULT_U0), stride.unwrap_or(5) as usize);
    visible_or_404(&state, &curve_id)?;
    let curve = resolve(&state, &curve_id)?;
    let s = spec(&state, &curve_id)?.map_err(ApiError::not_found)?;
    let (cal_min, cal_max) = (
        s["cal_min"].as_f64().unwrap_or_default(),
        s["cal_max"].as_f64().unwrap_or_default(),
    );
    let mut lo = from.map_or(cal_min, |f| f.max(cal_min));
    let mut hi = to.map_or(cal_max, |t| t.min(cal_max));
    if hi < lo {
        std::mem::swap(&mut lo, &mut hi);
    }
    let sha = s["content_sha256"].as_str().unwrap_or_default().to_owned();
    let warm = has_full_profile(&sha, u0) && stride >= BASE_STRIDE;
    let computed = if warm || hi - lo > WIDE_SPAN_YEARS {
        slice_profile(full_profile(curve, &sha, u0, stride)?, lo, hi)
    } else {
        resolution::profile(curve, Some(lo), Some(hi), u0, stride).map_err(ApiError::internal)?
    };
    let mut data = profile_json(&computed);
    data["curve_id"] = json!(curve_id);
    data["cal_from"] = json!(lo);
    data["cal_to"] = json!(hi);
    Ok(ok(data))
}

async fn curve_resolution_csv(
    Extract(state): Extract<State>,
    Path(curve_id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Answer {
    let (u0, stride) = query(&q, u0_and_stride)?;
    let (u0, stride) = (u0.unwrap_or(DEFAULT_U0), stride.unwrap_or(5) as usize);
    visible_or_404(&state, &curve_id)?;
    let curve = resolve(&state, &curve_id)?;
    let s = spec(&state, &curve_id)?.map_err(ApiError::not_found)?;
    let p = full_profile(
        curve,
        s["content_sha256"].as_str().unwrap_or_default(),
        u0,
        stride,
    )?;
    let mut text = String::from(
        "Sample Age (cal BP),Mean Resolution,5th Percentile,95th Percentile,Uncertainty Used",
    );
    for i in 0..p.cal_ages.len() {
        text.push_str(&format!(
            "\n{:.0},{:.3},{:.3},{:.3},{:.3}",
            p.cal_ages[i], p.mean[i], p.p05[i], p.p95[i], p.uncertainty[i]
        ));
    }
    text.push('\n');
    Ok(attachment(
        "text/csv; charset=utf-8",
        &format!("{curve_id}-resolution-u{}.csv", g(u0, 6)),
        text,
    ))
}

// --------------------------------------------------------------------------
// Importing
// --------------------------------------------------------------------------

/// `schemas.curve.slugify`: 'IntCal13' -> 'intcal13'.
fn slugify(text: &str) -> String {
    let mut out = String::new();
    let mut gap = false;
    for c in text.to_lowercase().chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            out.push(c);
            gap = false;
        } else if !gap {
            out.push('-');
            gap = true;
        }
    }
    out.trim_matches('-').chars().take(64).collect()
}

/// `SLUG_PATTERN`: 2-64 of lowercase letters, digits, '_' and '-', not
/// starting with either of the last two.
fn usable_slug(slug: &str) -> bool {
    let n = slug.chars().count();
    let mut chars = slug.chars();
    (2..=64).contains(&n)
        && chars
            .next()
            .is_some_and(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
        && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-')
}

fn unusable(slug: &str) -> ApiError {
    ApiError::new(
        StatusCode::UNPROCESSABLE_ENTITY,
        format!(
            "'{slug}' is not a usable curve id. Use 2-64 characters: lowercase letters, digits, hyphens or underscores."
        ),
    )
}

/// The trailing newline Python's `str.strip()` and `.lower()` would leave.
fn strip_lower(s: &str) -> String {
    s.trim().to_lowercase()
}

/// `_free_slug`: the slug, or the first free `slug-2`, `slug-3`...
fn free_slug(state: &State, slug: &str) -> ApiResult<String> {
    let taken: Vec<String> = stored_rows(state)?
        .into_iter()
        .map(|r| r.id)
        .chain(BUILTINS.iter().map(|b| b.id.to_owned()))
        .collect();
    if !taken.iter().any(|t| t == slug) {
        return Ok(slug.to_owned());
    }
    (2..1000)
        .map(|n| format!("{slug}-{n}"))
        .find(|c| !taken.contains(c))
        .ok_or_else(|| {
            ApiError::conflict(format!(
                "Could not find a free id based on '{slug}'. Choose another id."
            ))
        })
}

fn check_size(content: &[u8]) -> ApiResult<()> {
    if content.is_empty() {
        return Err(ApiError::bad_request("The uploaded file is empty."));
    }
    if content.len() > MAX_CONTENT_BYTES {
        return Err(ApiError::new(
            StatusCode::PAYLOAD_TOO_LARGE,
            format!(
                "The file is {} KB; the limit is {} KB.",
                content.len() / 1024,
                MAX_CONTENT_BYTES / 1024
            ),
        ));
    }
    Ok(())
}

fn calendar(text: &str) -> Calendar {
    if text == "bc_ad" {
        Calendar::BcAd
    } else {
        Calendar::CalBp
    }
}

fn form_of(headers: &HeaderMap, body: &Bytes) -> Form {
    Form::parse(
        headers
            .get(header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok()),
        body,
    )
}

/// Read an uploaded curve file and say what it holds, storing nothing.
async fn preview(Extract(_state): Extract<State>, headers: HeaderMap, body: Bytes) -> ApiResult {
    let form = form_of(&headers, &body);
    let Some(file) = form.file("file") else {
        return Err(form::missing(&["file"]));
    };
    let cal = form.text("calendar").unwrap_or_else(|| "cal_bp".to_owned());
    if cal != "cal_bp" && cal != "bc_ad" {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "calendar must be 'cal_bp' or 'bc_ad'.",
        ));
    }
    check_size(&file.data)?;
    let filename = file.filename.clone().unwrap_or_default();
    let result = curve_import::parse_upload(&filename, &file.data, calendar(&cal));
    let mut payload = result.to_json();
    let base = result
        .suggested_name
        .clone()
        .filter(|n| !n.is_empty())
        .or_else(|| Some(filename.clone()).filter(|n| !n.is_empty()))
        .unwrap_or_else(|| "curve".to_owned());
    payload["filename"] = json!(filename);
    payload["suggested_id"] = json!(slugify(&base));
    Ok(ok(payload))
}

fn str_list_clean(f: &mut Fields, name: &str) -> Option<Vec<String>> {
    if f.raw(name).is_none() {
        return Some(Vec::new());
    }
    f.str_list(name, None, Some(20)).map(|v| {
        v.iter()
            .map(|a| a.trim().to_owned())
            .filter(|a| !a.is_empty())
            .collect()
    })
}

const CURVE_TYPES: [&str; 4] = ["terrestrial", "marine", "southern_hemisphere", "mixed"];

/// Store a previewed curve, parsed again from the same file.
async fn create_curve(Extract(state): Extract<State>, headers: HeaderMap, body: Bytes) -> Answer {
    let form = form_of(&headers, &body);
    let (file, metadata) = (form.file("file"), form.text("metadata"));
    let missing: Vec<&str> = [("file", file.is_none()), ("metadata", metadata.is_none())]
        .into_iter()
        .filter(|(_, m)| *m)
        .map(|(n, _)| n)
        .collect();
    if !missing.is_empty() {
        return Err(form::missing(&missing));
    }
    let (file, metadata) = (file.unwrap(), metadata.unwrap());
    let meta = validate::json_body(&Bytes::from(metadata))?;
    let mut problems = Problems::default();
    let mut f = Fields::new(&meta, vec![], "CurveMetadata", &mut problems);
    let name = f.string("name", Some(1), Some(120));
    let description = f.opt_str("description", Some(2000));
    let reference = f.opt_str("reference", Some(500));
    let doi = f.opt_str("doi", Some(200));
    let curve_type = f.literal_or("type", &CURVE_TYPES, "terrestrial");
    let sample_type = f.literal_or("sample_type", &["terrestrial", "marine"], "terrestrial");
    let aliases = str_list_clean(&mut f, "aliases");
    problems.finish()?;
    let (name, aliases) = (name.unwrap_or_default(), aliases.unwrap_or_default());

    check_size(&file.data)?;
    let text = curve_import::decode(&file.data);
    let cal = form.text("calendar").unwrap_or_else(|| "cal_bp".to_owned());
    let result = curve_import::parse_curve(&text, calendar(&cal), None);
    if !result.ok() {
        return Err(status(
            StatusCode::UNPROCESSABLE_ENTITY,
            json!({"message": "The curve file could not be read.", "errors": result.errors}),
        ));
    }
    let slug = strip_lower(
        &form
            .text("curve_id")
            .filter(|c| !c.is_empty())
            .unwrap_or_else(|| slugify(&name)),
    );
    if !usable_slug(&slug) {
        return Err(unusable(&slug));
    }
    if alias(&state, &slug)?.is_some() {
        return Err(ApiError::conflict(format!(
            "You already have a calibration curve '{slug}'. Curve data is immutable, so a correction is imported as a new curve under a new id rather than replacing this one."
        )));
    }
    let slug = free_slug(&state, &slug)?;
    for a in &aliases {
        if let Some(clash) = alias(&state, a)? {
            return Err(ApiError::conflict(format!(
                "The alias '{a}' already refers to the curve '{clash}'. An alias that names two curves would make an imported file's curve column ambiguous."
            )));
        }
    }
    let (cal_ages, rest): (Vec<f64>, Vec<(f64, f64)>) =
        result.points.iter().map(|p| (p.0, (p.1, p.2))).unzip();
    let (c14, err): (Vec<f64>, Vec<f64>) = rest.into_iter().unzip();
    let curve = CalibrationCurve::new(&name, cal_ages, c14, err);
    let now = Some(Timestamp::now());
    let row = CurveRow {
        id: slug.clone(),
        kind: "imported".to_owned(),
        name,
        description,
        reference,
        doi,
        curve_type: curve_type.unwrap_or_default(),
        sample_type: sample_type.unwrap_or_default(),
        source_text: Some(text),
        source_filename: file.filename.clone(),
        parse_options: Some(result.parse_options()),
        recipe: None,
        content_sha256: content_hash(&curve),
        aliases: Some(json!(aliases)),
        archived: false,
        created_at: now,
        updated_at: now,
    };
    state.store.write(|tx| rows::insert(tx, &row))?;
    forget(&state, &slug);
    let data = spec(&state, &slug)?.map_err(ApiError::internal)?;
    Ok((
        StatusCode::CREATED,
        axum::Json(json!({
            "success": true, "data": data,
            "notes": result.notes, "warnings": result.warnings,
        })),
    )
        .into_response())
}

/// `curve_mix.describe`.
fn describe(a: &str, b: &str, percent_a: f64, error: f64) -> String {
    let error = if error != 0.0 {
        format!(" ± {}%", g(error, 6))
    } else {
        String::new()
    };
    format!(
        "{}%{error} {a}, {}% {b}",
        g(percent_a, 6),
        g(100.0 - percent_a, 6)
    )
}

/// A curve defined as a proportional mixture of two others, stored as its
/// recipe.
async fn create_mixed(Extract(state): Extract<State>, body: Bytes) -> Answer {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "MixedCurveRequest", &mut problems);
    let curve_id = f.opt_str("curve_id", None);
    let name = f.opt_str("name", Some(120));
    let a = f.string("a", None, None);
    let b = f.string("b", None, None);
    let percent_a = f.float("percent_a", Bounds::range(0.0, 100.0));
    let percent_a_error = f.float_or("percent_a_error", 0.0, Bounds::range(0.0, 100.0));
    let description = f.opt_str("description", Some(2000));
    let reference = f.opt_str("reference", Some(500));
    let aliases = str_list_clean(&mut f, "aliases");
    problems.finish()?;
    let (a, b) = (a.unwrap_or_default(), b.unwrap_or_default());
    let (percent_a, percent_a_error) = (
        percent_a.unwrap_or_default(),
        percent_a_error.unwrap_or(0.0),
    );
    for id in [&a, &b] {
        if !exists(&state, id)? {
            return Err(ApiError::new(
                StatusCode::UNPROCESSABLE_ENTITY,
                format!("There is no calibration curve '{id}' to mix."),
            ));
        }
    }
    if a == b {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "A curve mixed with itself is just that curve.",
        ));
    }
    let spec_a = spec(&state, &a)?.map_err(ApiError::not_found)?;
    let spec_b = spec(&state, &b)?.map_err(ApiError::not_found)?;
    let (name_a, name_b) = (
        spec_a["name"].as_str().unwrap_or_default(),
        spec_b["name"].as_str().unwrap_or_default(),
    );
    let name = name
        .filter(|n| !n.is_empty())
        .unwrap_or_else(|| format!("{name_a}/{name_b} {}%", g(percent_a, 6)));
    let slug = strip_lower(
        &curve_id
            .filter(|c| !c.is_empty())
            .unwrap_or_else(|| slugify(&name)),
    );
    if !usable_slug(&slug) {
        return Err(unusable(&slug));
    }
    if alias(&state, &slug)?.is_some() {
        return Err(ApiError::conflict(format!(
            "You already have a calibration curve '{slug}'."
        )));
    }
    let slug = free_slug(&state, &slug)?;
    let recipes: Vec<MixRecipe> = stored_rows(&state)?
        .into_iter()
        .filter(|r| r.kind == "mixed")
        .map(|r| {
            let rec = r.recipe.clone().unwrap_or_default();
            MixRecipe {
                id: r.id,
                a: rec["a"].as_str().unwrap_or_default().to_owned(),
                b: rec["b"].as_str().unwrap_or_default().to_owned(),
                percent_a: rec["percent_a"].as_f64().unwrap_or(0.0),
                percent_a_error: rec["percent_a_error"].as_f64().unwrap_or(0.0),
            }
        })
        .collect();
    if let Some(existing) = find_mixed(&recipes, &a, &b, percent_a, percent_a_error) {
        return Err(ApiError::conflict(format!(
            "'{existing}' is already this mixture. Two curves with the same recipe would be the same numbers under two names, and would make matching an .oxcal file's Mix_Curves to one of them arbitrary."
        )));
    }
    let mixed = mix(
        resolve(&state, &a)?,
        resolve(&state, &b)?,
        percent_a,
        percent_a_error,
        &name,
    )
    .map_err(|e| ApiError::new(StatusCode::UNPROCESSABLE_ENTITY, e.to_string()))?;
    let marine = spec_a["sample_type"] == "marine" || spec_b["sample_type"] == "marine";
    let now = Some(Timestamp::now());
    let row = CurveRow {
        id: slug.clone(),
        kind: "mixed".to_owned(),
        description: Some(
            description
                .filter(|d| !d.is_empty())
                .unwrap_or_else(|| describe(name_a, name_b, percent_a, percent_a_error)),
        ),
        name,
        reference,
        doi: None,
        curve_type: "mixed".to_owned(),
        sample_type: if marine { "marine" } else { "terrestrial" }.to_owned(),
        source_text: None,
        source_filename: None,
        parse_options: None,
        recipe: Some(json!({
            "a": a, "b": b, "percent_a": percent_a, "percent_a_error": percent_a_error,
        })),
        content_sha256: content_hash(&mixed),
        aliases: Some(json!(aliases.unwrap_or_default())),
        archived: false,
        created_at: now,
        updated_at: now,
    };
    state.store.write(|tx| rows::insert(tx, &row))?;
    forget(&state, &slug);
    Ok(created(spec(&state, &slug)?.map_err(ApiError::internal)?))
}

// --------------------------------------------------------------------------
// Lifecycle
// --------------------------------------------------------------------------

/// Correct a stored curve's description, citation or aliases; never its
/// numbers.
async fn update_curve(
    Extract(state): Extract<State>,
    Path(curve_id): Path<String>,
    body: Bytes,
) -> ApiResult {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "CurveMetadataUpdate", &mut problems);
    let name = f.opt_str("name", Some(120));
    if let Some(n) = &name
        && n.is_empty()
    {
        f.string("name", Some(1), Some(120));
    }
    let description = f.opt_str("description", Some(2000));
    let reference = f.opt_str("reference", Some(500));
    let doi = f.opt_str("doi", Some(200));
    let curve_type = f.opt_literal("type", &CURVE_TYPES);
    let sample_type = f.opt_literal("sample_type", &["terrestrial", "marine"]);
    let aliases = if f.raw("aliases").is_some_and(|v| !v.is_null()) {
        f.str_list("aliases", None, Some(20))
    } else {
        None
    };
    let sent: Vec<String> = input
        .as_object()
        .map(|o| o.keys().cloned().collect())
        .unwrap_or_default();
    problems.finish()?;
    let mut row = stored_or_404(&state, &curve_id)?;
    for a in aliases.iter().flatten() {
        if let Some(clash) = alias(&state, a)?
            && clash != curve_id
        {
            return Err(ApiError::conflict(format!(
                "The alias '{a}' already refers to the curve '{clash}'."
            )));
        }
    }
    let set = |k: &str| sent.iter().any(|s| s == k);
    if set("name")
        && let Some(n) = name
    {
        row.name = n;
    }
    if set("description") {
        row.description = description;
    }
    if set("reference") {
        row.reference = reference;
    }
    if set("doi") {
        row.doi = doi;
    }
    if set("type")
        && let Some(t) = curve_type
    {
        row.curve_type = t;
    }
    if set("sample_type")
        && let Some(t) = sample_type
    {
        row.sample_type = t;
    }
    if set("aliases") {
        row.aliases = aliases.map(|a| json!(a));
    }
    row.updated_at = Some(Timestamp::now());
    state.store.write(|tx| rows::update(tx, &row))?;
    Ok(ok(spec(&state, &curve_id)?.map_err(ApiError::internal)?))
}

async fn curve_usage(Extract(state): Extract<State>, Path(curve_id): Path<String>) -> ApiResult {
    if !exists(&state, &curve_id)? {
        return Err(ApiError::not_found(format!(
            "Unknown calibration curve '{curve_id}'."
        )));
    }
    Ok(ok(usage(&state, &curve_id)?))
}

/// Remove a stored curve -- refused while anything uses it -- or archive
/// it.
async fn delete_curve(
    Extract(state): Extract<State>,
    Path(curve_id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> ApiResult {
    let archive = query(&q, |f| f.bool_or("archive", false))?.unwrap_or(false);
    let mut row = stored_or_404(&state, &curve_id)?;
    if archive {
        row.archived = true;
        row.updated_at = Some(Timestamp::now());
        state.store.write(|tx| rows::update(tx, &row))?;
        return Ok(ok(json!({"id": curve_id, "archived": true})));
    }
    let in_use = usage(&state, &curve_id)?;
    if in_use["in_use"] == true {
        return Err(status(
            StatusCode::CONFLICT,
            json!({
                "message": format!(
                    "'{curve_id}' is in use and cannot be deleted. Archive it instead: it will disappear from the curve pickers, and the analyses below will still rerun."
                ),
                "usage": in_use,
            }),
        ));
    }
    state
        .store
        .write(|tx| rows::delete_where::<CurveRow>(tx, "id = ?", [&curve_id]))?;
    forget(&state, &curve_id);
    Ok(ok(json!({"id": curve_id, "deleted": true})))
}

async fn restore_curve(Extract(state): Extract<State>, Path(curve_id): Path<String>) -> ApiResult {
    let mut row = stored_or_404(&state, &curve_id)?;
    row.archived = false;
    row.updated_at = Some(Timestamp::now());
    state.store.write(|tx| rows::update(tx, &row))?;
    Ok(ok(spec(&state, &curve_id)?.map_err(ApiError::internal)?))
}

/// The curve as a .14c file: an imported one byte for byte as uploaded,
/// anything else written from its points.
async fn export_curve(Extract(state): Extract<State>, Path(curve_id): Path<String>) -> Answer {
    visible_or_404(&state, &curve_id)?;
    let s = spec(&state, &curve_id)?.map_err(ApiError::not_found)?;
    let row = if s["kind"] == "builtin" {
        None
    } else {
        stored_row(&state, &curve_id)?
    };
    let (text, filename) =
        match row.filter(|r| r.source_text.as_deref().is_some_and(|t| !t.is_empty())) {
            Some(r) => (
                r.source_text.clone().unwrap_or_default(),
                r.source_filename
                    .clone()
                    .unwrap_or_else(|| format!("{curve_id}.14c")),
            ),
            None => {
                let curve = resolve(&state, &curve_id)?;
                let points: Vec<(f64, f64, f64)> = (0..curve.cal_ages().len())
                    .map(|i| {
                        (
                            curve.cal_ages()[i],
                            curve.c14_ages()[i],
                            curve.c14_errors()[i],
                        )
                    })
                    .collect();
                let header = format!(
                    "{}\n{}",
                    s["name"].as_str().unwrap_or_default(),
                    s["reference"].as_str().unwrap_or_default()
                );
                (
                    curve_import::to_14c_text(&points, &header),
                    format!("{curve_id}.14c"),
                )
            }
        };
    Ok(attachment("text/plain; charset=utf-8", &filename, text))
}

pub fn router() -> Router<State> {
    Router::new()
        .route("/api/curves", get(list_curves).post(create_curve))
        .route("/api/curves/compare", get(compare_curves))
        .route("/api/curves/import/preview", post(preview))
        .route("/api/curves/mixed", post(create_mixed))
        .route("/api/curves/type/{curve_type}", get(by_type))
        .route(
            "/api/curves/{curve_id}",
            get(get_curve).patch(update_curve).delete(delete_curve),
        )
        .route("/api/curves/{curve_id}/points", get(points))
        .route("/api/curves/{curve_id}/points.csv", get(points_csv))
        .route("/api/curves/{curve_id}/simulate", post(simulate))
        .route("/api/curves/{curve_id}/resolution", get(curve_resolution))
        .route(
            "/api/curves/{curve_id}/resolution.csv",
            get(curve_resolution_csv),
        )
        .route("/api/curves/{curve_id}/usage", get(curve_usage))
        .route("/api/curves/{curve_id}/restore", post(restore_curve))
        .route("/api/curves/{curve_id}/export", get(export_curve))
}
