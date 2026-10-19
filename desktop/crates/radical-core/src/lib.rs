//! The calibration maths of RadiCal and its exact solvers: pure functions
//! over curves, measurements and model trees, with no I/O, no database and no
//! async. A port of `backend/app/services`, held to the Python implementation
//! by the golden corpus in `desktop/corpus` (see `tests/units`, and
//! `tests/models` for whole API runs).
//!
//! The Python docstrings' reasoning is carried across as doc comments. Where
//! the port departs from the Python shape, it says so:
//!
//! - "Match OxCal 4.4" is a [`Mode`] passed down explicitly rather than a
//!   context variable.
//! - Functions return structs where Python returns dicts, and [`Error`] where
//!   Python raises `ValueError`, with the same message ([`Error::Model`] where
//!   the message names a sample or an element).
//! - The exact solvers' matrix products and SciPy's FFT convolutions are
//!   summed in an order of this crate's own ([`matrix`],
//!   [`exact_chain::convolve_on_grid`]), so the solvers agree with Python to
//!   rounding rather than to the bit.
//! - Only the builtin curves are known here ([`curves`]); stored and mixed
//!   curves belong to the store, which hands them in.
//!
//! The arithmetic follows NumPy's order of operations ([`numeric`]), so that
//! a result agrees with Python's to the bit wherever the platform's `exp`,
//! `ln` and `pow` agree with the ones the corpus was generated with.

pub mod agreement;
pub mod calibration;
pub mod cross_check;
pub mod curve_mix;
pub mod curve_registry;
pub mod curve_view;
pub mod curves;
pub mod date_format;
pub mod exact_chain;
pub mod exact_parallel;
pub mod gibbs;
pub mod influence;
pub mod intervals;
pub mod kde;
pub mod matrix;
pub mod measurement;
pub mod model_tree;
pub mod numeric;
pub mod pyfmt;
pub mod reservoir;
pub mod resolution;
pub mod rng;
mod sha1;
pub mod special;
pub mod spline;
pub mod statistics;
pub mod stratigraphy;
pub mod wiggle_match;

use std::fmt;

pub use calibration::{
    CalibrationCurve, HpdRange, Mode, PointEstimate, REPORTING_CONFIDENCE_LEVELS, SIGMA_1, SIGMA_2,
    SIGMA_3, cell_edges, f14c, hpd_ranges, hpd_ranges_from_distribution, point_estimate,
};
pub use curves::get_curve;
pub use measurement::Measurement;

/// A refusal. Each message is the one the Python code raises, word for word,
/// since the API passes them on to the user.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    EmptyCurve,
    NoCurveData,
    UnknownCurve(String),
    DegeneratePosterior,
    EmptyDistribution,
    DegenerateDistribution,
    MisalignedWidths,
    ZeroWeights,
    /// SciPy's refusal to build a spline through these points.
    Spline(&'static str),
    MixProportion,
    MixProportionError,
    MixEmptyCurve,
    MixNoOverlap,
    NonPositiveUncertainty,
    ZeroStride,
    EmptyWindow,
    NothingMapsIntoWindow,
    EmptyCalibration,
    CurvesDoNotOverlap,
    /// A model, or a date in one, refused with Python's `ValueError` message,
    /// which names the element or sample at fault.
    Model(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let message = match self {
            Self::EmptyCurve => "Calibration curve data is empty",
            Self::NoCurveData => "No valid data found in the curve file",
            Self::UnknownCurve(id) => {
                return write!(
                    f,
                    "Unknown calibration curve '{id}'. Supported curves: {}",
                    curves::BUILTIN_IDS.join(", ")
                );
            }
            Self::DegeneratePosterior => {
                "Calibration produced a degenerate (zero or non-finite) posterior; \
                 the measurement is likely far outside the calibration curve's range."
            }
            Self::EmptyDistribution => "Empty ages or probabilities",
            Self::DegenerateDistribution => "Distribution has zero or non-finite total probability",
            Self::MisalignedWidths => "widths must align with ages and weights",
            Self::ZeroWeights => "Weights sum to zero, can't be normalized",
            Self::Spline(message) => message,
            Self::MixProportion => "The proportion must be between 0 and 100 percent",
            Self::MixProportionError => "The uncertainty on the proportion cannot be negative",
            Self::MixEmptyCurve => "Both curves must have data to be mixed",
            Self::MixNoOverlap => {
                "These two curves do not overlap in calendar time, so there is no \
                 span over which a mixture of them could be calibrated."
            }
            Self::NonPositiveUncertainty => "The laboratory uncertainty must be positive",
            Self::ZeroStride => "slice step cannot be zero",
            Self::EmptyWindow => "The requested window contains no calendar years",
            Self::NothingMapsIntoWindow => {
                "No radiocarbon age maps into this window; it may lie outside the \
                 range the curve covers."
            }
            Self::EmptyCalibration => "a calibration came out empty; nothing to draw from",
            Self::Model(message) => message,
            Self::CurvesDoNotOverlap => {
                "These two curves do not overlap in calendar time, so there is \
                 nothing to compare."
            }
        };
        f.write_str(message)
    }
}

impl std::error::Error for Error {}

/// Python's `x ** y` for a Python float: the C library's `pow`, called, not
/// folded. On some platforms `pow(x, 2.0)` is not correctly rounded and so not
/// always `x * x`, and the Python code squares plain floats with `**` in a few
/// places (NumPy arrays squared with `**` are exact squares, and use `x * x`
/// here). `black_box` keeps the compiler from replacing the call with a
/// multiplication; it changes nothing but the last bit.
pub(crate) fn py_pow(x: f64, y: f64) -> f64 {
    x.powf(std::hint::black_box(y))
}
