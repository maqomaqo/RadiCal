//! A date as `schemas.sample.BayesianMeasurementInput` validates it: the
//! bounds a typed date is held to, checked in Pydantic's order and reported
//! in Pydantic's words, since the importer passes them on to the user.
//!
//! Pydantic checks every field, in the model's field order, and reports the
//! first constraint each one breaks -- an upper bound before a lower one, so
//! NaN reads "less than or equal to". Only when every field passes do the
//! model's own validators run, in the order they are defined, and the first
//! to object is the only one reported.

/// One problem: the field (empty for the model as a whole) and the message.
pub type Problem = (String, String);

/// The date's values that validation reads.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Values<'a> {
    pub sample_name: &'a str,
    pub age_bp: f64,
    pub error_1sigma: f64,
    pub material_type: &'a str,
    pub delta_r: Option<f64>,
    pub delta_r_error: Option<f64>,
    pub latitude: Option<f64>,
    pub longitude: Option<f64>,
    pub depth_cm: Option<f64>,
    pub ring_offset: Option<f64>,
    pub ring_offset_error: Option<f64>,
    pub inbuilt_years: Option<f64>,
    pub location: Option<&'a str>,
    pub notes: Option<&'a str>,
    pub tpq_age_bp: Option<f64>,
    pub taq_age_bp: Option<f64>,
    pub outlier_prior: f64,
}

/// How Pydantic writes a constraint's bound: the bounds here are whole
/// numbers, written without a decimal point.
fn bound(value: f64) -> String {
    format!("{}", value as i64)
}

/// The first constraint `value` breaks, as Pydantic orders them: le, lt, ge,
/// gt. Negated comparisons on purpose: NaN satisfies none of them, and fails
/// the first.
#[allow(clippy::neg_cmp_op_on_partial_ord)]
fn number(value: f64, ge: Option<f64>, gt: Option<f64>, le: f64) -> Option<String> {
    if !(value <= le) {
        return Some(format!(
            "Input should be less than or equal to {}",
            bound(le)
        ));
    }
    if let Some(ge) = ge
        && !(value >= ge)
    {
        return Some(format!(
            "Input should be greater than or equal to {}",
            bound(ge)
        ));
    }
    if let Some(gt) = gt
        && !(value > gt)
    {
        return Some(format!("Input should be greater than {}", bound(gt)));
    }
    None
}

fn string(value: &str, max: usize) -> Option<String> {
    (value.chars().count() > max).then(|| format!("String should have at most {max} characters"))
}

/// Every problem Pydantic would report, in its order.
pub fn validate(v: &Values) -> Vec<Problem> {
    let mut problems: Vec<Problem> = Vec::new();
    let mut field = |name: &str, problem: Option<String>| {
        if let Some(message) = problem {
            problems.push((name.to_owned(), message));
        }
    };
    let optional =
        |value: Option<f64>, ge: f64, le: f64| value.and_then(|x| number(x, Some(ge), None, le));
    field(
        "sample_name",
        string(v.sample_name, 200).or_else(|| {
            v.sample_name
                .trim()
                .is_empty()
                .then(|| "Value error, sample_name cannot be blank".to_owned())
        }),
    );
    field("age_bp", number(v.age_bp, Some(0.0), None, 60000.0));
    field(
        "error_1sigma",
        number(v.error_1sigma, None, Some(0.0), 10000.0),
    );
    field("material_type", string(v.material_type, 100));
    field("delta_r", optional(v.delta_r, -5000.0, 5000.0));
    field("delta_r_error", optional(v.delta_r_error, 0.0, 5000.0));
    field("latitude", optional(v.latitude, -90.0, 90.0));
    field("longitude", optional(v.longitude, -180.0, 180.0));
    field("depth_cm", optional(v.depth_cm, 0.0, 10000.0));
    field("ring_offset", optional(v.ring_offset, -50000.0, 50000.0));
    field(
        "ring_offset_error",
        optional(v.ring_offset_error, 0.0, 10000.0),
    );
    field("inbuilt_years", optional(v.inbuilt_years, 0.0, 1000.0));
    field("location", v.location.and_then(|s| string(s, 500)));
    field("notes", v.notes.and_then(|s| string(s, 2000)));
    field("tpq_age_bp", optional(v.tpq_age_bp, 0.0, 60000.0));
    field("taq_age_bp", optional(v.taq_age_bp, 0.0, 60000.0));
    field(
        "outlier_prior",
        number(v.outlier_prior, Some(0.0), None, 1.0),
    );
    if !problems.is_empty() {
        return problems;
    }
    // The model validators, in their order of definition; the first stops it.
    let model = if v.ring_offset_error.is_some() && v.ring_offset.is_none() {
        Some("ring_offset_error cannot be set on its own -- it is the uncertainty on a gap, so send the gap it belongs to".to_owned())
    } else if matches!((v.tpq_age_bp, v.taq_age_bp), (Some(tpq), Some(taq)) if taq > tpq) {
        Some("taq_age_bp (minimum age -- terminus ante quem) cannot be greater than tpq_age_bp (maximum age -- terminus post quem); the valid range [taq_age_bp, tpq_age_bp] would be empty".to_owned())
    } else if v.delta_r.is_some() && v.delta_r_error.is_none() {
        Some("delta_r_error is required when delta_r is given -- a reservoir offset has its own uncertainty, and omitting it would understate the uncertainty of the calibrated date".to_owned())
    } else if v.latitude.is_none() != v.longitude.is_none() {
        let missing = if v.longitude.is_none() {
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
    model
        .map(|m| vec![(String::new(), format!("Value error, {m}"))])
        .unwrap_or_default()
}
