//! Running analyses (`api/analysis.py`): a single calibration, a group of
//! independent calibrations on one timeline, and the same-event test the
//! model editor asks before a run. Bayesian runs and re-runs follow.

use axum::Router;
use axum::body::Bytes;
use axum::extract::State as Extract;
use axum::routing::post;
use radical_core::calibration::{SIGMA_1, SIGMA_2, hpd_ranges, point_estimate};
use radical_core::measurement::Curves;
use radical_core::numeric::argmax;
use radical_core::statistics;
use radical_core::{CalibrationCurve, Error, HpdRange, Mode};
use radical_store::Timestamp;
use radical_store::rows::{self, Analysis, AnalysisResult, RadiocarbonSample};
use serde_json::{Value, json};

use crate::routes::analysis::{ranges_json, same_event_json, validate_project_id};
use crate::schemas::{self, BayesianMeasurement, Measurement};
use crate::validate::{self, Problems};
use crate::{ApiError, ApiResult, State, history, ok};

/// Every statistic a plain calibration reports (`_compute_calibration_stats`).
pub struct Stats {
    pub calibrated_age_bp: f64,
    pub mean: f64,
    pub median: f64,
    pub std: f64,
    pub hpd_68: [f64; 2],
    pub hpd_95: [f64; 2],
    pub hpd_68_ranges: Vec<HpdRange>,
    pub hpd_95_ranges: Vec<HpdRange>,
    pub distribution: Vec<[f64; 2]>,
}

fn envelope(ranges: &[HpdRange]) -> Result<[f64; 2], Error> {
    if ranges.is_empty() {
        // Python's min() of nothing.
        return Err(Error::Model("min() iterable argument is empty".to_owned()));
    }
    let lo = ranges.iter().map(|r| r.min).fold(f64::INFINITY, f64::min);
    let hi = ranges
        .iter()
        .map(|r| r.max)
        .fold(f64::NEG_INFINITY, f64::max);
    Ok([lo, hi])
}

/// Calibrate one measurement into everything its result reports.
pub fn calibration_stats(
    curve: &CalibrationCurve,
    age_bp: f64,
    error: f64,
    delta_r: Option<f64>,
    delta_r_error: Option<f64>,
) -> Result<Stats, Error> {
    let probs = curve.calibrate(age_bp, error, delta_r, delta_r_error, Mode::Standard)?;
    let cal = curve.cal_ages();
    let hpd_68_ranges = hpd_ranges(cal, &probs, SIGMA_1)?;
    let hpd_95_ranges = hpd_ranges(cal, &probs, SIGMA_2)?;
    let point = point_estimate(cal, &probs)?;
    Ok(Stats {
        calibrated_age_bp: cal[argmax(&probs).unwrap_or(0)],
        mean: point.mean,
        median: point.median,
        std: point.std,
        hpd_68: envelope(&hpd_68_ranges)?,
        hpd_95: envelope(&hpd_95_ranges)?,
        hpd_68_ranges,
        hpd_95_ranges,
        distribution: curve.display_distribution(cal, &probs, 200, 1e-4),
    })
}

/// A plain calibration's stored result (`_calibration_result_row`).
pub fn calibration_result_row(analysis_id: &str, sample_id: &str, stats: &Stats) -> AnalysisResult {
    AnalysisResult {
        id: radical_store::new_id(),
        analysis_id: analysis_id.to_owned(),
        sample_id: Some(sample_id.to_owned()),
        calibrated_age_bp: Some(stats.calibrated_age_bp),
        posterior_mean: Some(stats.mean),
        posterior_median: Some(stats.median),
        posterior_std: Some(stats.std),
        hpd_68_min: Some(stats.hpd_68[0]),
        hpd_68_max: Some(stats.hpd_68[1]),
        hpd_95_min: Some(stats.hpd_95[0]),
        hpd_95_max: Some(stats.hpd_95[1]),
        hpd_68_ranges: Some(ranges_json(&stats.hpd_68_ranges)),
        hpd_95_ranges: Some(ranges_json(&stats.hpd_95_ranges)),
        probability_distribution: Some(json!(stats.distribution)),
        created_at: Some(Timestamp::now()),
        ..AnalysisResult::default()
    }
}

/// The sample type a date's curve implies when it gives none.
pub fn sample_type_for(m: &Measurement) -> String {
    m.sample_type.clone().unwrap_or_else(|| {
        if m.curve_id == "marine20" {
            "marine"
        } else {
            "terrestrial"
        }
        .to_owned()
    })
}

/// One measurement as a stored date (`_sample_row`).
pub fn sample_row(
    analysis_id: &str,
    b: &BayesianMeasurement,
    group_id: Option<String>,
    element_id: Option<String>,
    sort_order: i64,
) -> RadiocarbonSample {
    let m = &b.m;
    RadiocarbonSample {
        id: radical_store::new_id(),
        analysis_id: analysis_id.to_owned(),
        sort_order,
        sample_name: m.sample_name.clone(),
        age_bp: m.age_bp,
        error_1sigma: m.error_1sigma,
        material_type: m.material_type.clone(),
        sample_type: sample_type_for(m),
        curve_id: Some(m.curve_id.clone()),
        delta_r: m.delta_r,
        delta_r_error: m.delta_r_error,
        delta_r_source: m.delta_r_source.clone().map(Value::Object),
        latitude: m.latitude,
        longitude: m.longitude,
        outlier_prior: Some(b.outlier_prior),
        outlier_model: Some(b.outlier_model.clone()),
        reservoir_age: m.reservoir_age,
        depth_cm: m.depth_cm,
        ring_offset: m.ring_offset,
        ring_offset_error: m.ring_offset_error,
        inbuilt_years: m.inbuilt_years,
        inbuilt_shape: m.inbuilt_shape.clone(),
        location: m.location.clone(),
        notes: m.notes.clone(),
        group_id,
        element_id,
        tpq_age_bp: m.tpq_age_bp,
        taq_age_bp: m.taq_age_bp,
        created_at: Some(Timestamp::now()),
    }
}

/// A calculation's refusal as the route's 400.
fn refused(e: impl std::fmt::Display) -> ApiError {
    ApiError::bad_request(e.to_string())
}

/// Calibrate one measurement, and save it as an analysis unless asked not
/// to.
async fn calibrate(Extract(state): Extract<State>, body: Bytes) -> ApiResult {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "CalibrateRequest", &mut problems);
    let parsed = schemas::calibrate_request(&mut f, &state.store);
    problems.finish()?;
    let (m, project_id, save) = parsed.expect("validated");
    let project_id = if save {
        state
            .store
            .read(|c| Ok(validate_project_id(c, project_id.as_deref())))??
    } else {
        None
    };
    let curve = state.curves.curve(&m.curve_id).map_err(refused)?;
    let stats = calibration_stats(curve, m.age_bp, m.error_1sigma, m.delta_r, m.delta_r_error)
        .map_err(refused)?;
    let mut data = json!({
        "id": null,
        "name": "Single Calibration",
        "analysis_type": "calibration",
        "status": "completed",
        "saved": false,
        "project_id": null,
        "calibrated_age_bp": stats.calibrated_age_bp,
        "posterior_mean": stats.mean,
        "posterior_median": stats.median,
        "posterior_std": stats.std,
        "hpd_68": {"min": stats.hpd_68[0], "max": stats.hpd_68[1]},
        "hpd_95": {"min": stats.hpd_95[0], "max": stats.hpd_95[1]},
        "hpd_68_ranges": ranges_json(&stats.hpd_68_ranges),
        "hpd_95_ranges": ranges_json(&stats.hpd_95_ranges),
        "probability_distribution": stats.distribution,
        "measurements_count": 1,
        "results": [],
    });
    if !save {
        return Ok(ok(data));
    }
    let now = Some(Timestamp::now());
    let analysis = Analysis {
        id: radical_store::new_id(),
        name: "Single Calibration".to_owned(),
        analysis_type: "calibration".to_owned(),
        status: Some("completed".to_owned()),
        created_at: now,
        updated_at: now,
        project_id: project_id.clone(),
        bayesian_config: Some(json!({})),
        ..Analysis::default()
    };
    // Only what a single calibration stores of its date.
    let sample = RadiocarbonSample {
        id: radical_store::new_id(),
        analysis_id: analysis.id.clone(),
        sample_name: m.sample_name.clone(),
        age_bp: m.age_bp,
        error_1sigma: m.error_1sigma,
        material_type: m.material_type.clone(),
        sample_type: sample_type_for(&m),
        curve_id: Some(m.curve_id.clone()),
        delta_r: m.delta_r,
        delta_r_error: m.delta_r_error,
        delta_r_source: m.delta_r_source.clone().map(Value::Object),
        latitude: m.latitude,
        longitude: m.longitude,
        reservoir_age: m.reservoir_age,
        depth_cm: m.depth_cm,
        location: m.location.clone(),
        notes: m.notes.clone(),
        created_at: now,
        ..RadiocarbonSample::default()
    };
    let result = calibration_result_row(&analysis.id, &sample.id, &stats);
    state.store.write(|tx| {
        rows::insert(tx, &analysis)?;
        rows::insert(tx, &sample)?;
        rows::insert(tx, &result)?;
        Ok(())
    })?;
    history::record_run(&state, &analysis.id);
    data["id"] = json!(analysis.id);
    data["saved"] = json!(true);
    data["project_id"] = json!(project_id);
    Ok(ok(data))
}

/// Several measurements calibrated independently and saved as one
/// analysis, on one timeline, with no relationship asserted.
async fn calibrate_group(Extract(state): Extract<State>, body: Bytes) -> ApiResult {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "GroupCalibrationRequest", &mut problems);
    let name = f.str_or("name", "Group Calibration", Some(200)).map(|n| {
        let t = n.trim().to_owned();
        if t.is_empty() {
            "Group Calibration".to_owned()
        } else {
            t
        }
    });
    let dates = f.models(
        "measurements",
        "BayesianMeasurementInput",
        Some(1),
        None,
        |item| schemas::bayesian_measurement(item, &state.store),
    );
    if let Some(dates) = &dates {
        let mut seen = std::collections::HashSet::new();
        if !dates.iter().all(|d| seen.insert(d.id.as_str())) {
            f.value_error(Some("measurements"), "measurements contain a duplicate id");
        }
    }
    let project_id = f.opt_str("project_id", None);
    let _save = f.bool_or("save", true);
    let durations = f.bool_or("estimate_durations", false).unwrap_or(false);
    let kde = f.bool_or("estimate_kde", false).unwrap_or(false);
    problems.finish()?;
    let dates = dates.unwrap_or_default();
    let project_id = state
        .store
        .read(|c| Ok(validate_project_id(c, project_id.as_deref())))??;
    let now = Some(Timestamp::now());
    let mut analysis = Analysis {
        id: radical_store::new_id(),
        name: name.unwrap_or_default(),
        analysis_type: "group_calibration".to_owned(),
        status: Some("completed".to_owned()),
        created_at: now,
        updated_at: now,
        project_id: project_id.clone(),
        bayesian_config: Some(json!({"estimate_durations": durations, "estimate_kde": kde})),
        ..Analysis::default()
    };
    let mut samples = Vec::new();
    let mut results = Vec::new();
    for (position, d) in dates.iter().enumerate() {
        let sample = sample_row(&analysis.id, d, None, None, position as i64);
        let curve = state.curves.curve(&d.m.curve_id).map_err(refused)?;
        let stats = calibration_stats(
            curve,
            sample.age_bp,
            sample.error_1sigma,
            sample.delta_r,
            sample.delta_r_error,
        )
        .map_err(refused)?;
        results.push(calibration_result_row(&analysis.id, &sample.id, &stats));
        samples.push(sample);
    }
    if durations && samples.len() > 1 {
        analysis.intervals =
            Some(crate::blocks::date_differences(&state, &samples).map_err(refused)?);
    }
    if kde {
        analysis.summaries = crate::blocks::date_kde(&state, &samples).map_err(refused)?;
    }
    state.store.write(|tx| {
        rows::insert(tx, &analysis)?;
        for s in &samples {
            rows::insert(tx, s)?;
        }
        for r in &results {
            rows::insert(tx, r)?;
        }
        Ok(())
    })?;
    history::record_run(&state, &analysis.id);
    Ok(ok(json!({
        "id": analysis.id,
        "name": analysis.name,
        "analysis_type": "group_calibration",
        "status": "completed",
        "project_id": project_id,
        "measurements_count": dates.len(),
    })))
}

/// Whether these measurements could all date one event; nothing is saved.
async fn same_event(Extract(state): Extract<State>, body: Bytes) -> ApiResult {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "SameEventRequest", &mut problems);
    let dates = f.models("measurements", "MeasurementInput", Some(2), None, |item| {
        let m = schemas::measurement_fields(item, &state.store);
        schemas::measurement_checks(item, &m);
        item.clean().then_some(m)
    });
    problems.finish()?;
    let dates: Vec<statistics::Date> = dates
        .unwrap_or_default()
        .iter()
        .map(|m| statistics::Date {
            age_bp: m.age_bp,
            error_1sigma: m.error_1sigma,
            curve_id: Some(m.curve_id.clone()),
            delta_r: m.delta_r,
            delta_r_error: m.delta_r_error,
            inbuilt_years: m.inbuilt_years,
            inbuilt_shape: m.inbuilt_shape.clone(),
        })
        .collect();
    let test = statistics::same_event(&dates, |id| state.curves.curve(id).ok(), Mode::Standard);
    Ok(ok(test.as_ref().map_or(Value::Null, same_event_json)))
}

pub fn router() -> Router<State> {
    Router::new()
        .route("/api/analysis/calibrate", post(calibrate))
        .route("/api/analysis/calibrate-group", post(calibrate_group))
        .route("/api/analysis/same-event", post(same_event))
}
