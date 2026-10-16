//! The same-event test: whether a set of radiocarbon measurements could all
//! date one event (Ward and Wilson 1978; OxCal's R_Combine). A port of
//! `same_event` from `backend/app/services/statistics.py`.
//!
//! Pool the ages with inverse-variance weights,
//!
//! ```text
//! x = sum(x_i / s_i^2) / sum(1 / s_i^2),     s_x = (sum(1 / s_i^2))^(-1/2)
//! ```
//!
//! and test their scatter about that pool:
//!
//! ```text
//! T = sum((x_i - x)^2 / s_i^2)  ~  chi^2 with n - 1 degrees of freedom
//! ```
//!
//! The dates are consistent with one event when T is under the chi^2 critical
//! value at 5%, which is how OxCal reports it ("T=5.7(5% 14.1)").
//!
//! The test runs on conventional radiocarbon ages, so it needs every date to
//! share one curve and one reservoir offset. Dates on different curves are not
//! measuring the same thing, and different Delta R values would shift some
//! ages against others. A Delta R shared by every date shifts them all alike,
//! which leaves T unchanged; it is applied once, when the pooled age is
//! calibrated. Adding its error to each date before pooling instead would
//! count one uncertainty n times and understate T.

use crate::numeric::sum;
use crate::special::chi2_ppf;
use crate::{CalibrationCurve, HpdRange, Mode, REPORTING_CONFIDENCE_LEVELS, hpd_ranges, py_pow};

/// Significance level the test is reported at -- OxCal's, and Ward and
/// Wilson's.
pub const SAME_EVENT_LEVEL: f64 = 0.05;

/// One date, as the test reads it.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Date {
    pub age_bp: f64,
    pub error_1sigma: f64,
    pub curve_id: Option<String>,
    pub delta_r: Option<f64>,
    pub delta_r_error: Option<f64>,
    pub inbuilt_years: Option<f64>,
    pub inbuilt_shape: Option<String>,
}

/// A pooled age's calibrated ranges at one reported level.
#[derive(Debug, Clone, PartialEq)]
pub struct LevelRanges {
    pub confidence: f64,
    pub ranges: Vec<HpdRange>,
}

/// The test's answer. When the dates cannot be pooled, `refused` says why and
/// every statistic is None.
#[derive(Debug, Clone, PartialEq)]
pub struct SameEvent {
    pub n: usize,
    pub df: usize,
    pub level: f64,
    pub mean_bp: Option<f64>,
    pub error_bp: Option<f64>,
    pub t: Option<f64>,
    pub critical: Option<f64>,
    pub consistent: Option<bool>,
    pub pooled_hpd_ranges: Option<Vec<LevelRanges>>,
    pub refused: Option<&'static str>,
}

/// Whether every item has the same key as the first, as Python's
/// `len({key(m) for m in ...}) > 1` asks.
fn all_alike<T: PartialEq>(keys: impl IntoIterator<Item = T>) -> bool {
    let mut keys = keys.into_iter();
    match keys.next() {
        None => true,
        Some(first) => keys.all(|k| k == first),
    }
}

fn truthy(x: Option<f64>) -> f64 {
    // Python's `x or 0.0`.
    x.unwrap_or(0.0)
}

/// The Ward and Wilson test over `dates`, or None for fewer than two.
///
/// `curves` finds the curve the pooled age is calibrated against, and `mode`
/// is the run's likelihood mode. The pooled ranges are given only where
/// pooling is justified, and are None if the curve cannot be found or the
/// pooled age does not calibrate: the test stands without them.
pub fn same_event<'c>(
    dates: &[Date],
    curves: impl Fn(&str) -> Option<&'c CalibrationCurve>,
    mode: Mode,
) -> Option<SameEvent> {
    let n = dates.len();
    if n < 2 {
        return None;
    }
    let base = SameEvent {
        n,
        df: n - 1,
        level: SAME_EVENT_LEVEL,
        mean_bp: None,
        error_bp: None,
        t: None,
        critical: None,
        consistent: None,
        pooled_hpd_ranges: None,
        refused: None,
    };
    let refuse = |why| {
        Some(SameEvent {
            refused: Some(why),
            ..base.clone()
        })
    };

    if !all_alike(dates.iter().map(|d| d.curve_id.as_deref())) {
        return refuse("Dates are on more than one curve.");
    }
    if !all_alike(
        dates
            .iter()
            .map(|d| (truthy(d.delta_r), truthy(d.delta_r_error))),
    ) {
        return refuse("Dates have different ΔR values.");
    }
    // Pooled dates take one allowance between them, so they must share it.
    // Pooling separate samples does not change T (the offset shifts the
    // event, not the measurement), but the pooled date has to carry one
    // allowance.
    if !all_alike(dates.iter().map(|d| {
        let years = truthy(d.inbuilt_years);
        let shape = (years != 0.0).then(|| {
            d.inbuilt_shape
                .as_deref()
                .filter(|s| !s.is_empty())
                .unwrap_or("uniform")
        });
        (years, shape)
    })) {
        return refuse("Dates have different inbuilt-age allowances.");
    }
    if dates.iter().any(|d| d.error_1sigma <= 0.0) {
        return refuse("A date has no measurement error.");
    }

    let weights: Vec<f64> = dates
        .iter()
        .map(|d| 1.0 / (d.error_1sigma * d.error_1sigma))
        .collect();
    let weighted: Vec<f64> = dates
        .iter()
        .zip(&weights)
        .map(|(d, w)| d.age_bp * w)
        .collect();
    let total = sum(&weights);
    let mean = sum(&weighted) / total;
    let error = py_pow(total, -0.5);
    let scatter: Vec<f64> = dates
        .iter()
        .zip(&weights)
        .map(|(d, w)| (d.age_bp - mean) * (d.age_bp - mean) * w)
        .collect();
    let t = sum(&scatter);
    let critical = chi2_ppf(1.0 - SAME_EVENT_LEVEL, (n - 1) as f64);
    let consistent = t < critical;

    Some(SameEvent {
        mean_bp: Some(mean),
        error_bp: Some(error),
        t: Some(t),
        critical: Some(critical),
        consistent: Some(consistent),
        // The pooled age calibrated, only where pooling is justified: a pool
        // of dates from more than one event is a date of nothing.
        pooled_hpd_ranges: if consistent {
            pooled_ranges(&dates[0], mean, error, curves, mode)
        } else {
            None
        },
        ..base
    })
}

/// The pooled age's ranges at each level the results page offers -- the
/// shape of a Bayesian date's unmodelled HPD ranges.
fn pooled_ranges<'c>(
    reference: &Date,
    mean: f64,
    error: f64,
    curves: impl Fn(&str) -> Option<&'c CalibrationCurve>,
    mode: Mode,
) -> Option<Vec<LevelRanges>> {
    let curve = curves(reference.curve_id.as_deref()?)?;
    let probs = curve
        .calibrate(
            mean,
            error,
            reference.delta_r,
            reference.delta_r_error,
            mode,
        )
        .ok()?;
    REPORTING_CONFIDENCE_LEVELS
        .iter()
        .map(|&level| {
            Some(LevelRanges {
                confidence: level,
                ranges: hpd_ranges(curve.cal_ages(), &probs, level).ok()?,
            })
        })
        .collect()
}
