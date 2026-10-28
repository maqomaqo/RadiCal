//! The fixture models as RadiCal runs them, and the runs.
//!
//! Each model is the request the API was sent after importing the fixture's
//! `.oxcal` file, recorded in the api/models corpus, so the dates, outlier
//! settings and structure are the ones OxCal ran rather than a copy of them.
//! Dates are found by name, through [`export::key`], since that is all the
//! two sides share.

use std::collections::HashMap;
use std::sync::OnceLock;

use radical_core::exact_chain::{self, Posterior, Solution, SolveOptions};
use radical_core::gibbs::{self, SampleOptions, Sampled};
use radical_core::measurement::{Calibrator, Measurement};
use radical_core::model_tree::Element;
use radical_corpus::load_family;

use crate::export::key;
use crate::model;

/// The three Hillside runs: the same eight dates on SHCal20, differing only
/// in how the two branches (North and South) relate to each other.
///
/// - `ordered`: the original. North and South are sibling Sequences inside
///   Sequence("Hillside") with no Boundaries of their own, so the ordering is
///   implied by the structure.
/// - `unordered`: both branches wrapped in one Phase("Sites"), so nothing
///   orders them.
/// - `shared_boundary`: a Boundary("North to South") between them, so the
///   transition is one explicit shared parameter.
///
/// `ordered` and `shared_boundary` come out nearly identical in OxCal
/// (Fire_Feature_J at AD 1311-1350 in both; Amodel 96 and 95.5) while
/// `unordered` differs (Amodel 106.6): OxCal DOES order sibling groups that
/// carry no Boundaries of their own.
pub const HILLSIDE: [(&str, &str); 3] = [
    ("ordered", "oxcal/Hillside_sites_3"),
    ("unordered", "oxcal/Hillside_sites_3_unordered"),
    ("shared_boundary", "oxcal/Hillside_sites_3_shared_boundary"),
];

pub fn hillside(run: &str) -> &'static str {
    HILLSIDE.iter().find(|(r, _)| *r == run).unwrap().1
}

/// Hillside's dates by group: North is Fire_Feature_J, South is
/// Fire_Feature_K then Fire_Feature_A.
pub const NORTH: [&str; 3] = ["LD10012", "LD10009", "LD10011"];
pub const SOUTH: [&str; 5] = ["LD10006", "LD10007", "LD10008", "LA10003", "LA10001"];

/// Dune Section: sixteen dates in one Phase on SHCal20 under
/// `Outlier_Model("General", T(5), U(0,4), "t")` at 5% each. LB10002, at
/// 970 +/- 47 BP against the rest at 537-660, is the outlier, and OxCal
/// treats it as one: agreement 5.3 against 71-151 for the rest, its modelled
/// range dragged from AD 1044-1158 to AD 1394-1410.
pub const DUNE: &str = "oxcal_outlier/Dune_Section";
pub const DUNE_OUTLIER: &str = "LB10002";
pub const DUNE_PRIOR: f64 = 0.05;

/// Hillside again, eleven dates under BOTH outlier models,
///
/// ```text
/// Outlier_Model("General",  T(5),         U(0,4), "t")
/// Outlier_Model("Charcoal", Exp(1,-10,0), U(0,3), "t")
/// ```
///
/// with the oldest date in each of the three phases declared residual
/// outright, Outlier("Charcoal", 1.00): at prior 1.0 the mixture IS the
/// outlier component, which is what makes the run sensitive to the charcoal
/// kernel's shape. There is no Boundary between North and South: two groups
/// in a row inside one pair, which the exact solver integrates as written, as
/// blocks.
pub const CHARCOAL: &str = "oxcal_charcoal/Hillside_Seq";
pub const DECLARED_RESIDUAL: [&str; 3] = ["LD10010", "LD10005", "LA10002"];
pub const CHARCOAL_SOUTH: [&str; 7] = [
    "LD10005", "LD10006", "LD10007", "LD10008", "LA10002", "LA10003", "LA10001",
];

/// OxCal reruns made to pin the agreement indices, which OxCal computes at
/// its calculation resolution: Hillside `ordered` and Dune Section at
/// Resolution=1, and DS -- a layered sequence of ten Boundaries, an undated
/// Phase and a 5% General outlier prior on twelve dates -- at OxCal's
/// default five years and at one. OxCal's own indices move with the
/// resolution (Hillside's Aoverall is 100.4 at five years and 105.4 at one),
/// and the one-year values are the ones the definition is held to.
pub const HILLSIDE_ONE_YEAR: &str = "oxcal_resolution/Hillside_sites_3_res1";
pub const DUNE_ONE_YEAR: &str = "oxcal_resolution/Dune_Section_res1";
pub const DS: &str = "oxcal_resolution/DS_Layers";
pub const DS_ONE_YEAR: &str = "oxcal_resolution/DS_Layers_res1";

pub struct Model {
    pub root: Element,
    pub dates: Vec<Measurement>,
}

impl Model {
    /// The date OxCal calls `name`.
    pub fn date(&self, name: &str) -> &Measurement {
        let k = key(name);
        self.dates
            .iter()
            .find(|m| key(m.sample_name.as_deref().unwrap_or_default()) == k)
            .unwrap_or_else(|| panic!("{name}: no such date"))
    }

    pub fn id(&self, name: &str) -> &str {
        &self.date(name).id
    }

    /// OxCal's name for each date, in the request's order.
    pub fn names(&self) -> Vec<String> {
        self.dates
            .iter()
            .map(|m| key(m.sample_name.as_deref().unwrap()))
            .collect()
    }
}

/// The fixture `stem` names, e.g. `oxcal/Hillside_sites_3`.
pub fn model(stem: &str) -> &'static Model {
    static MODELS: OnceLock<HashMap<String, Model>> = OnceLock::new();
    let models = MODELS.get_or_init(|| {
        let family = load_family("api/models").unwrap();
        family
            .cases
            .iter()
            .filter_map(|case| {
                let fixture = case
                    .name
                    .strip_prefix("bayesian/")?
                    .strip_suffix(".oxcal/auto")?;
                let inputs = case.expanded_inputs();
                let request = &inputs["request"];
                Some((
                    fixture.to_owned(),
                    Model {
                        root: model::element(&request["model_tree"]),
                        dates: model::measurements(&request["measurements"]),
                    },
                ))
            })
            .collect()
    });
    models
        .get(stem)
        .unwrap_or_else(|| panic!("{stem}: not in the api/models corpus"))
}

pub fn cal() -> Calibrator<'static> {
    Calibrator::default()
}

/// The exact solver on a model, as the API lays it out.
pub fn exact(model: &Model, options: &SolveOptions) -> Solution {
    let chain = exact_chain::layout(Some(&model.root), &model.dates, false, true)
        .expect("the exact solver declined the model");
    exact_chain::solve(&model.dates, &model.root, &chain, options, &cal()).unwrap()
}

pub fn exact_with(num_samples: usize) -> SolveOptions {
    SolveOptions {
        num_samples,
        ..SolveOptions::default()
    }
}

/// One default exact run per fixture, shared by the tests that read it.
pub fn solved(stem: &str) -> &'static Solution {
    static SOLVED: OnceLock<std::sync::Mutex<HashMap<String, &'static Solution>>> = OnceLock::new();
    let cache = SOLVED.get_or_init(Default::default);
    if let Some(s) = cache.lock().unwrap().get(stem) {
        return s;
    }
    let solution: &'static Solution = Box::leak(Box::new(exact(model(stem), &exact_with(200))));
    cache
        .lock()
        .unwrap()
        .entry(stem.to_owned())
        .or_insert(solution)
}

pub fn sample(model: &Model, options: &SampleOptions) -> Sampled {
    gibbs::solve(&model.dates, &model.root, options, &cal(), None)
        .unwrap()
        .unwrap()
}

/// The sampler untempered (temperatures=1): tempering is for crossing
/// calibration lobes, and it triples the cost of a run.
pub fn untempered(num_samples: usize) -> SampleOptions {
    SampleOptions {
        num_samples,
        temperatures: 1,
        ..SampleOptions::default()
    }
}

/// The posterior a run gave the date with request id `id`.
pub fn posterior<'a>(results: &'a [(String, Posterior)], id: &str) -> &'a Posterior {
    &results
        .iter()
        .find(|(d, _)| d == id)
        .unwrap_or_else(|| panic!("{id}: no result"))
        .1
}

/// Each Boundary's posterior by its name, from either solver.
pub fn exact_boundary<'a>(solved: &'a Solution, name: &str) -> &'a Posterior {
    &solved
        .boundaries
        .iter()
        .find(|(_, b)| b.name == name)
        .unwrap_or_else(|| panic!("{name}: no such Boundary"))
        .1
        .posterior
}

pub fn rms(errors: &[f64]) -> f64 {
    (errors.iter().map(|e| e * e).sum::<f64>() / errors.len() as f64).sqrt()
}

pub fn max_abs(errors: &[f64]) -> f64 {
    errors.iter().map(|e| e.abs()).fold(0.0, f64::max)
}

pub fn mean(values: &[f64]) -> f64 {
    values.iter().sum::<f64>() / values.len() as f64
}
