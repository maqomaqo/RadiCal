//! A radiocarbon date as the model solvers read it, and where they find the
//! curve it is calibrated against.
//!
//! Python passes each date around as a dict and reads its keys with `.get`,
//! so a missing key and a `None` mean the same thing; here every optional key
//! is an `Option`. Where the Python code treats an empty string or a zero as
//! "not set" (`m.get('curve_id') or 'intcal20'`), the accessors below do the
//! same, so a port of a call site uses the accessor rather than the field.

use crate::{CalibrationCurve, Error, Mode};

/// One measurement and its place in a model. Port of the measurement dicts
/// `backend/app/api/analysis.py` builds from a request and hands the services.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Measurement {
    pub id: String,
    pub sample_name: Option<String>,
    pub age_bp: f64,
    pub error_1sigma: f64,
    pub curve_id: Option<String>,
    pub delta_r: Option<f64>,
    pub delta_r_error: Option<f64>,
    pub tpq_age_bp: Option<f64>,
    pub taq_age_bp: Option<f64>,
    /// The model element the date is attached to.
    pub element_id: Option<String>,
    /// The context of the older contexts-and-relationships representation.
    pub group_id: Option<String>,
    pub outlier_prior: Option<f64>,
    pub outlier_model: Option<String>,
    pub inbuilt_years: Option<f64>,
    pub inbuilt_shape: Option<String>,
    pub ring_offset: Option<f64>,
    pub ring_offset_error: Option<f64>,
    pub depth_cm: Option<f64>,
}

/// `value or default` for an optional string: Python's empty string is false.
pub(crate) fn or_str<'a>(value: &'a Option<String>, default: &'a str) -> &'a str {
    match value.as_deref() {
        Some(s) if !s.is_empty() => s,
        _ => default,
    }
}

/// `value or 0.0` for an optional number.
pub(crate) fn or_zero(value: Option<f64>) -> f64 {
    match value {
        Some(v) if v != 0.0 => v,
        _ => 0.0,
    }
}

impl Measurement {
    /// `m.get('curve_id') or 'intcal20'`.
    pub fn curve(&self) -> &str {
        or_str(&self.curve_id, "intcal20")
    }

    /// `m.get('sample_name', m.get('id'))`: the name whenever one was given,
    /// even an empty one.
    pub fn name_or_id(&self) -> &str {
        self.sample_name.as_deref().unwrap_or(&self.id)
    }

    /// `m.get('sample_name') or m['id']`.
    pub fn label(&self) -> &str {
        or_str(&self.sample_name, &self.id)
    }

    /// `m.get('outlier_prior') or 0.0`.
    pub fn outlier_prior(&self) -> f64 {
        or_zero(self.outlier_prior)
    }

    /// `m.get('outlier_model') or 'general'`.
    pub fn outlier_model(&self) -> &str {
        or_str(
            &self.outlier_model,
            crate::exact_chain::DEFAULT_OUTLIER_MODEL,
        )
    }

    /// `m.get('inbuilt_years') or 0.0`.
    pub fn inbuilt_years(&self) -> f64 {
        or_zero(self.inbuilt_years)
    }
}

/// Where the solvers find a curve by id. Python's `get_curve` also knows the
/// curves a user imported or mixed, which live in the database; whatever
/// holds those implements this and falls back to [`Builtins`].
pub trait Curves: Sync {
    fn curve(&self, curve_id: &str) -> Result<&CalibrationCurve, Error>;
}

/// The three curves compiled in ([`crate::curves`]).
#[derive(Debug, Clone, Copy, Default)]
pub struct Builtins;

impl Curves for Builtins {
    fn curve(&self, curve_id: &str) -> Result<&CalibrationCurve, Error> {
        crate::curves::get_curve(curve_id)
    }
}

/// The curves a run calibrates against and the likelihood [`Mode`] it forms
/// them in: what Python reads from `get_curve` and the `matching_oxcal`
/// context variable, passed down explicitly.
#[derive(Clone, Copy)]
pub struct Calibrator<'a> {
    pub curves: &'a dyn Curves,
    pub mode: Mode,
}

impl Default for Calibrator<'static> {
    fn default() -> Self {
        Self {
            curves: &Builtins,
            mode: Mode::Standard,
        }
    }
}

impl<'a> Calibrator<'a> {
    pub fn new(curves: &'a dyn Curves, mode: Mode) -> Self {
        Self { curves, mode }
    }

    /// The date's own curve.
    pub fn curve_of(&self, m: &Measurement) -> Result<&'a CalibrationCurve, Error> {
        self.curves.curve(m.curve())
    }

    /// `curve.likelihood(points, age, error, delta_r, delta_r_error)`: the
    /// measurement's likelihood at each calendar age, its reservoir offset
    /// applied.
    pub fn likelihood(&self, points: &[f64], m: &Measurement) -> Result<Vec<f64>, Error> {
        self.curve_of(m)?.likelihood(
            points,
            m.age_bp,
            m.error_1sigma,
            m.delta_r,
            m.delta_r_error,
            self.mode,
        )
    }

    /// `curve.calibrate(...)`: (the curve's calendar ages, the posterior
    /// masses on them).
    pub fn calibrate(&self, m: &Measurement) -> Result<(&'a [f64], Vec<f64>), Error> {
        let curve = self.curve_of(m)?;
        let masses = curve.calibrate(
            m.age_bp,
            m.error_1sigma,
            m.delta_r,
            m.delta_r_error,
            self.mode,
        )?;
        Ok((curve.cal_ages(), masses))
    }
}
