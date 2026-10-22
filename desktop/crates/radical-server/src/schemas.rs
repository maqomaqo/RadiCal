//! The request models of `schemas/`, read through [`crate::validate`] in
//! each Python model's declaration order, with its validators in the order
//! it defines them.

use radical_store::Store;
use radical_store::rows::{self, CalibrationCurve};
use serde_json::{Map, Value};

use crate::validate::{Bounds, Fields};

/// The curves built in, as `curve_registry.BUILTIN_CURVE_IDS` lists them.
pub const BUILTIN_CURVE_IDS: [&str; 3] = ["intcal20", "shcal20", "marine20"];

/// Whether a curve id names any curve at all (`curve_registry.exists`).
pub fn curve_exists(store: &Store, curve_id: &str) -> bool {
    BUILTIN_CURVE_IDS.contains(&curve_id)
        || store
            .read(|c| rows::get::<CalibrationCurve>(c, curve_id))
            .ok()
            .flatten()
            .is_some()
}

/// `schemas.sample.validate_curve_id`'s refusal.
pub fn unknown_curve(curve_id: &str) -> String {
    format!(
        "Unknown calibration curve '{curve_id}'. This app ships with {}, and calibrates against any curve you have imported. A curve it does not have cannot be substituted for one it does.",
        BUILTIN_CURVE_IDS.join(", ")
    )
}

/// A date as `MeasurementInput` (and its subclasses) reads one.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Measurement {
    pub sample_name: String,
    pub age_bp: f64,
    pub error_1sigma: f64,
    pub material_type: String,
    pub sample_type: Option<String>,
    pub curve_id: String,
    pub delta_r: Option<f64>,
    pub delta_r_error: Option<f64>,
    pub delta_r_source: Option<Map<String, Value>>,
    pub latitude: Option<f64>,
    pub longitude: Option<f64>,
    pub reservoir_age: Option<f64>,
    pub depth_cm: Option<f64>,
    pub ring_offset: Option<f64>,
    pub ring_offset_error: Option<f64>,
    pub inbuilt_years: Option<f64>,
    pub inbuilt_shape: Option<String>,
    pub location: Option<String>,
    pub notes: Option<String>,
    pub tpq_age_bp: Option<f64>,
    pub taq_age_bp: Option<f64>,
}

pub const AGE: Bounds = Bounds::range(0.0, 60000.0);
pub const ERROR: Bounds = Bounds::above(0.0, 10000.0);
pub const DELTA_R: Bounds = Bounds::range(-5000.0, 5000.0);
pub const DELTA_R_ERROR: Bounds = Bounds::range(0.0, 5000.0);
pub const LATITUDE: Bounds = Bounds::range(-90.0, 90.0);
pub const LONGITUDE: Bounds = Bounds::range(-180.0, 180.0);
pub const RESERVOIR_AGE: Bounds = Bounds::range(-500.0, 2000.0);
pub const DEPTH: Bounds = Bounds::range(0.0, 10000.0);
pub const RING_OFFSET: Bounds = Bounds::range(-50000.0, 50000.0);
pub const RING_OFFSET_ERROR: Bounds = Bounds::range(0.0, 10000.0);
pub const INBUILT: Bounds = Bounds::range(0.0, 1000.0);

/// The sample_name validator: stripped, and refused when blank.
fn sample_name(f: &mut Fields, value: Option<String>) -> Option<String> {
    let v = value?;
    let stripped = v.trim().to_owned();
    if stripped.is_empty() {
        f.value_error(Some("sample_name"), "sample_name cannot be blank");
        return None;
    }
    Some(stripped)
}

/// `MeasurementInput`'s fields and field validators, without its model
/// validators (see [`measurement_checks`]).
pub fn measurement_fields(f: &mut Fields, store: &Store) -> Measurement {
    let name = f.str_or("sample_name", "Sample", Some(200));
    let sample_name = sample_name(f, name);
    let age_bp = f.float("age_bp", AGE);
    let error_1sigma = f.float("error_1sigma", ERROR);
    let material_type = f.str_or("material_type", "unknown", Some(100));
    let sample_type = f.opt_literal("sample_type", &["terrestrial", "marine"]);
    let mut curve_id = f.str_or("curve_id", "intcal20", None);
    if let Some(id) = &curve_id
        && !curve_exists(store, id)
    {
        let message = unknown_curve(id);
        f.value_error(Some("curve_id"), &message);
        curve_id = None;
    }
    Measurement {
        sample_name: sample_name.unwrap_or_default(),
        age_bp: age_bp.unwrap_or_default(),
        error_1sigma: error_1sigma.unwrap_or_default(),
        material_type: material_type.unwrap_or_default(),
        sample_type,
        curve_id: curve_id.unwrap_or_default(),
        delta_r: f.opt_float("delta_r", DELTA_R),
        delta_r_error: f.opt_float("delta_r_error", DELTA_R_ERROR),
        delta_r_source: f.opt_dict("delta_r_source"),
        latitude: f.opt_float("latitude", LATITUDE),
        longitude: f.opt_float("longitude", LONGITUDE),
        reservoir_age: f.opt_float("reservoir_age", RESERVOIR_AGE),
        depth_cm: f.opt_float("depth_cm", DEPTH),
        ring_offset: f.opt_float("ring_offset", RING_OFFSET),
        ring_offset_error: f.opt_float("ring_offset_error", RING_OFFSET_ERROR),
        inbuilt_years: f.opt_float("inbuilt_years", INBUILT),
        inbuilt_shape: f.opt_literal("inbuilt_shape", &["uniform", "exponential"]),
        location: f.opt_str("location", Some(500)),
        notes: f.opt_str("notes", Some(2000)),
        tpq_age_bp: f.opt_float("tpq_age_bp", AGE),
        taq_age_bp: f.opt_float("taq_age_bp", AGE),
    }
}

/// The empty range a TPQ above a TAQ would leave.
pub const BOUNDS_CONTRADICTORY: &str = "taq_age_bp (minimum age -- terminus ante quem) cannot be greater than tpq_age_bp (maximum age -- terminus post quem); the valid range [taq_age_bp, tpq_age_bp] would be empty";

/// `MeasurementInput`'s model validators, in definition order, the first
/// that fails reported; only when every field passed.
pub fn measurement_checks(f: &mut Fields, m: &Measurement) {
    if !f.clean() {
        return;
    }
    let refusal = if m.ring_offset_error.is_some() && m.ring_offset.is_none() {
        Some("ring_offset_error cannot be set on its own -- it is the uncertainty on a gap, so send the gap it belongs to".to_owned())
    } else if matches!((m.tpq_age_bp, m.taq_age_bp), (Some(tpq), Some(taq)) if taq > tpq) {
        Some(BOUNDS_CONTRADICTORY.to_owned())
    } else if m.delta_r.is_some() && m.delta_r_error.is_none() {
        Some("delta_r_error is required when delta_r is given -- a reservoir offset has its own uncertainty, and omitting it would understate the uncertainty of the calibrated date".to_owned())
    } else if m.latitude.is_some() != m.longitude.is_some() {
        let missing = if m.longitude.is_none() {
            "longitude"
        } else {
            "latitude"
        };
        Some(format!(
            "{missing} is required when the other half of the coordinate is given -- a position needs both, and a half-located date would be silently dropped from anything that uses one"
        ))
    } else {
        None
    };
    if let Some(message) = refusal {
        f.value_error(None, &message);
    }
}

/// `ProjectSampleInput`: a measurement, plus the context it deploys into.
pub fn project_sample_input(
    f: &mut Fields,
    store: &Store,
) -> Option<(Measurement, Option<String>)> {
    let m = measurement_fields(f, store);
    let context = f
        .opt_str("default_context_name", Some(200))
        .and_then(|v| Some(v.trim().to_owned()).filter(|s| !s.is_empty()));
    measurement_checks(f, &m);
    f.clean().then_some((m, context))
}

/// A list of ids that must not repeat (`_no_duplicate_ids`).
pub fn unique_ids(
    f: &mut Fields,
    name: &str,
    min: Option<usize>,
    max: Option<usize>,
) -> Option<Vec<String>> {
    let ids = f.str_list(name, min, max)?;
    let mut seen = std::collections::HashSet::new();
    if !ids.iter().all(|id| seen.insert(id.as_str())) {
        f.value_error(Some(name), &format!("{name} contains a duplicate id"));
        return None;
    }
    Some(ids)
}
