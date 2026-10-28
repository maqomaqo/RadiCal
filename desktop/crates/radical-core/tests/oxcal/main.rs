//! RadiCal against OxCal itself: the fixture models in
//! `desktop/corpus/fixtures/oxcal*/`, each run through RadiCal's solvers and
//! compared with what OxCal 4.4 reported for the same run.
//!
//! The corpus holds RadiCal to its own earlier answers to many decimal
//! places. This holds it to the field-standard tool, at the looser
//! tolerances two different programs can be expected to share, so a change
//! that moves an answer on purpose is checked against OxCal before the corpus
//! is changed to match it.
//!
//! - `likelihoods`: calibration, date by date, against OxCal's own
//!   likelihoods and reported ranges; and the fixture reading itself.
//! - `modelled`: the exact solver's modelled dates, groups and Boundaries.
//! - `outliers`: the outlier models, on both solvers.
//! - `agreement`: the agreement indices, Amodel and Aoverall.
//! - `match_oxcal`: the "Match OxCal 4.4" switch.

#[path = "../units/model.rs"]
#[allow(dead_code)]
mod model;

mod agreement;
mod export;
mod fixture;
mod likelihoods;
mod match_oxcal;
mod modelled;
mod outliers;

use serde_json::Value;

pub fn num(v: &Value) -> f64 {
    radical_corpus::as_f64(v).unwrap_or_else(|| panic!("not a corpus number: {v}"))
}
