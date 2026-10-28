//! The request models of `schemas/`, read through [`crate::validate`] in
//! each Python model's declaration order, with its validators in the order
//! it defines them.

use std::collections::HashSet;

use radical_core::model_tree::{validate_ordinates, validate_tree};
use radical_core::stratigraphy::{Group, Relationship, expand_relationships, find_cycle};
use radical_store::Store;
use radical_store::rows::{self, CalibrationCurve};
use serde_json::{Map, Value, json};

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

/// `BayesianMeasurementInput`: a measurement with the client's own id, its
/// place in the model, and its outlier setting.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct BayesianMeasurement {
    pub m: Measurement,
    pub id: String,
    pub group_id: Option<String>,
    pub element_id: Option<String>,
    pub outlier_prior: f64,
    pub outlier_model: String,
}

pub fn bayesian_measurement(f: &mut Fields, store: &Store) -> Option<BayesianMeasurement> {
    let m = measurement_fields(f, store);
    let id = f.string("id", Some(1), None);
    let group_id = f.opt_str("group_id", None);
    let element_id = f.opt_str("element_id", None);
    let outlier_prior = f.float_or("outlier_prior", 0.0, Bounds::range(0.0, 1.0));
    let outlier_model = f.literal_or("outlier_model", &["general", "charcoal"], "general");
    measurement_checks(f, &m);
    f.clean().then(|| BayesianMeasurement {
        m,
        id: id.unwrap_or_default(),
        group_id,
        element_id,
        outlier_prior: outlier_prior.unwrap_or_default(),
        outlier_model: outlier_model.unwrap_or_default(),
    })
}

/// `CalibrateRequest`: one measurement, the project to file it under, and
/// whether to save it at all.
pub fn calibrate_request(
    f: &mut Fields,
    store: &Store,
) -> Option<(Measurement, Option<String>, bool)> {
    let m = measurement_fields(f, store);
    let project_id = f.opt_str("project_id", None);
    let save = f.bool_or("save", true);
    measurement_checks(f, &m);
    f.clean().then(|| (m, project_id, save.unwrap_or(true)))
}

impl BayesianMeasurement {
    /// The date as the solvers take it, under the request's own id.
    pub fn core(&self) -> radical_core::Measurement {
        let m = &self.m;
        radical_core::Measurement {
            id: self.id.clone(),
            sample_name: Some(m.sample_name.clone()),
            age_bp: m.age_bp,
            error_1sigma: m.error_1sigma,
            curve_id: Some(m.curve_id.clone()),
            delta_r: m.delta_r,
            delta_r_error: m.delta_r_error,
            tpq_age_bp: m.tpq_age_bp,
            taq_age_bp: m.taq_age_bp,
            element_id: self.element_id.clone(),
            group_id: self.group_id.clone(),
            outlier_prior: Some(self.outlier_prior),
            outlier_model: Some(self.outlier_model.clone()),
            inbuilt_years: m.inbuilt_years,
            inbuilt_shape: m.inbuilt_shape.clone(),
            ring_offset: m.ring_offset,
            ring_offset_error: m.ring_offset_error,
            depth_cm: m.depth_cm,
            latitude: m.latitude,
            longitude: m.longitude,
        }
    }
}

/// `SampleGroupInput`: a context, by the client's own id.
pub fn sample_group(f: &mut Fields) -> Option<Group> {
    let id = f.string("id", Some(1), None);
    let name = f.string("name", Some(1), Some(200)).and_then(|v| {
        let t = v.trim().to_owned();
        if t.is_empty() {
            f.value_error(Some("name"), "group name cannot be blank");
            None
        } else {
            Some(t)
        }
    });
    let tpq_age_bp = f.opt_float("tpq_age_bp", AGE);
    let taq_age_bp = f.opt_float("taq_age_bp", AGE);
    if f.clean()
        && let (Some(tpq), Some(taq)) = (tpq_age_bp, taq_age_bp)
        && taq > tpq
    {
        f.value_error(
            None,
            &format!(
                "group '{}': taq_age_bp (minimum age) cannot be greater than tpq_age_bp (maximum age)",
                name.as_deref().unwrap_or_default()
            ),
        );
    }
    f.clean().then(|| Group {
        id: id.unwrap_or_default(),
        name,
        tpq_age_bp,
        taq_age_bp,
    })
}

/// `RelationshipInput`.
pub fn relationship(f: &mut Fields) -> Option<Relationship> {
    let from_sample_id = f.string("from_sample_id", None, None);
    let to_sample_id = f.string("to_sample_id", None, None);
    let from_type = f.literal_or("from_type", &["sample", "group"], "sample");
    let to_type = f.literal_or("to_type", &["sample", "group"], "sample");
    let relationship = f.literal_req("relationship", &["above", "below", "equal"]);
    f.clean().then(|| Relationship {
        from_sample_id: from_sample_id.unwrap_or_default(),
        to_sample_id: to_sample_id.unwrap_or_default(),
        from_type,
        to_type,
        relationship: relationship.unwrap_or_default(),
    })
}

/// `BayesianConfigInput`, as its `model_dump()`: every setting, defaults
/// filled in.
pub fn bayesian_config(f: &mut Fields) -> Option<Value> {
    let int = |lo: f64, hi: f64| Bounds::range(lo, hi);
    let solver = f.literal_or("solver", &["auto", "exact", "gibbs"], "auto");
    let mcmc_samples = f.int_or("mcmc_samples", 4000, int(100.0, 20000.0));
    let mcmc_min_sweeps = f.int_or("mcmc_min_sweeps", 0, int(0.0, 1_000_000.0));
    let apply_inbuilt = f.bool_or("apply_inbuilt", true);
    let seed = f.opt_int("seed", int(0.0, 2_147_483_647.0));
    let mcmc_tune = f.int_or("mcmc_tune", 1000, int(100.0, 20000.0));
    let mcmc_chains = f.int_or("mcmc_chains", 4, int(1.0, 16.0));
    let mcmc_temperatures = f.int_or("mcmc_temperatures", 3, int(1.0, 8.0));
    let uniform_span_prior = f.bool_or("uniform_span_prior", true);
    let match_oxcal = f.bool_or("match_oxcal", false);
    let resolution = f.int_or("resolution", 5, int(1.0, 100.0));
    let estimate_durations = f.bool_or("estimate_durations", false);
    let estimate_kde = f.bool_or("estimate_kde", false);
    let estimate_dispersal = f.bool_or("estimate_dispersal", false);
    let origin = f.model("dispersal_origin", "DispersalOrigin", true, |o| {
        let lat = o.float("lat", LATITUDE);
        let lon = o.float("lon", LONGITUDE);
        Some(json!({"lat": lat?, "lon": lon?}))
    });
    f.clean().then(|| {
        json!({
            "solver": solver,
            "mcmc_samples": mcmc_samples,
            "mcmc_min_sweeps": mcmc_min_sweeps,
            "apply_inbuilt": apply_inbuilt,
            "seed": seed,
            "mcmc_tune": mcmc_tune,
            "mcmc_chains": mcmc_chains,
            "mcmc_temperatures": mcmc_temperatures,
            "uniform_span_prior": uniform_span_prior,
            "match_oxcal": match_oxcal,
            "resolution": resolution,
            "estimate_durations": estimate_durations,
            "estimate_kde": estimate_kde,
            "estimate_dispersal": estimate_dispersal,
            "dispersal_origin": origin.flatten(),
        })
    })
}

/// `BayesianRequest`, the body of POST /analysis/bayesian and of PUT
/// /analysis/{id}/inputs.
#[derive(Debug, Clone)]
pub struct BayesianRequest {
    pub name: String,
    pub measurements: Vec<BayesianMeasurement>,
    pub groups: Vec<Group>,
    pub relationships: Vec<Relationship>,
    pub project_id: Option<String>,
    pub save: bool,
    pub bayesian_config: Value,
    pub model_tree: Option<Value>,
    pub analysis_type: Option<String>,
}

pub fn bayesian_request(f: &mut Fields, store: &Store) -> Option<BayesianRequest> {
    let name = f.str_or("name", "Untitled", Some(200)).map(|v| {
        let t = v.trim().to_owned();
        if t.is_empty() {
            "Untitled".to_owned()
        } else {
            t
        }
    });
    let measurements = f.models(
        "measurements",
        "BayesianMeasurementInput",
        Some(1),
        None,
        |item| bayesian_measurement(item, store),
    );
    let groups = f.models_or("groups", "SampleGroupInput", sample_group);
    let relationships = f.models_or("relationships", "RelationshipInput", relationship);
    let project_id = f.opt_str("project_id", None);
    let save = f.bool_or("save", true);
    let config = f.model(
        "bayesian_config",
        "BayesianConfigInput",
        false,
        bayesian_config,
    );
    let model_tree = f.opt_dict("model_tree").map(Value::Object);
    let analysis_type = f.opt_literal(
        "analysis_type",
        &["calibration", "group_calibration", "bayesian"],
    );
    if !f.clean() {
        return None;
    }
    let config = match config {
        Some(Some(c)) => c,
        _ => {
            let mut problems = crate::validate::Problems::default();
            let empty = json!({});
            let mut defaults = Fields::new(&empty, vec![], "BayesianConfigInput", &mut problems);
            bayesian_config(&mut defaults).expect("the defaults are valid")
        }
    };
    let request = BayesianRequest {
        name: name.unwrap_or_default(),
        measurements: measurements.unwrap_or_default(),
        groups: groups.unwrap_or_default(),
        relationships: relationships.unwrap_or_default(),
        project_id,
        save: save.unwrap_or(true),
        bayesian_config: config,
        model_tree,
        analysis_type,
    };
    if let Err(message) = stratigraphy_checks(&request) {
        f.value_error(None, &message);
        return None;
    }
    Some(request)
}

/// `BayesianRequest._validate_stratigraphy`.
fn stratigraphy_checks(r: &BayesianRequest) -> Result<(), String> {
    let mut seen = HashSet::new();
    if !r.measurements.iter().all(|m| seen.insert(m.id.as_str())) {
        return Err("measurements contain a duplicate id".to_owned());
    }
    if let Some(tree) = &r.model_tree {
        let root = crate::solve::element(tree);
        let described: Vec<radical_core::Measurement> = r
            .measurements
            .iter()
            .map(|m| radical_core::Measurement {
                id: m.id.clone(),
                sample_name: Some(m.m.sample_name.clone()),
                element_id: m.element_id.clone(),
                ring_offset: m.m.ring_offset,
                ring_offset_error: m.m.ring_offset_error,
                depth_cm: m.m.depth_cm,
                outlier_prior: Some(m.outlier_prior),
                ..radical_core::Measurement::default()
            })
            .collect();
        validate_tree(Some(&root), &described).map_err(|e| e.to_string())?;
        validate_ordinates(Some(&root), &described).map_err(|e| e.to_string())?;
        return Ok(());
    }
    let mut seen = HashSet::new();
    if !r.groups.iter().all(|g| seen.insert(g.id.as_str())) {
        return Err("groups contain a duplicate id".to_owned());
    }
    let measurement_ids: HashSet<&str> = r.measurements.iter().map(|m| m.id.as_str()).collect();
    let group_ids: HashSet<&str> = r.groups.iter().map(|g| g.id.as_str()).collect();
    if !measurement_ids.is_disjoint(&group_ids) {
        return Err(
            "a measurement id and a group id collide -- ids must be unique across both".to_owned(),
        );
    }
    for m in &r.measurements {
        if let Some(g) = &m.group_id
            && !group_ids.contains(g.as_str())
        {
            return Err(format!(
                "measurement '{}' references unknown group_id '{g}'",
                m.id
            ));
        }
    }
    for rel in &r.relationships {
        for (id, kind, where_) in [
            (&rel.from_sample_id, &rel.from_type, "from_sample_id"),
            (&rel.to_sample_id, &rel.to_type, "to_sample_id"),
        ] {
            let kind = kind.as_deref().unwrap_or("sample");
            let pool = if kind == "group" {
                &group_ids
            } else {
                &measurement_ids
            };
            if !pool.contains(id.as_str()) {
                return Err(format!(
                    "relationship {where_} references unknown {kind} id '{id}'"
                ));
            }
        }
    }
    let dates: Vec<radical_core::Measurement> = r
        .measurements
        .iter()
        .map(BayesianMeasurement::core)
        .collect();
    let (older_younger, _equal) = expand_relationships(&dates, &r.groups, &r.relationships);
    if let Some(cycle) = find_cycle(&older_younger) {
        let labels: Vec<String> = cycle
            .iter()
            .map(|n| {
                r.measurements
                    .iter()
                    .find(|m| &m.id == n)
                    .map_or_else(|| n.clone(), |m| m.m.sample_name.clone())
            })
            .collect();
        return Err(format!(
            "stratigraphic relationships are contradictory (a circular above/below chain involving: {}) -- no set of calendar ages could satisfy every constraint at once",
            labels.join(", ")
        ));
    }
    Ok(())
}
