//! radical-core held to the golden corpus: every `units/` family, case by
//! case, against what the Python implementation answered.
//!
//! The units are deterministic, so they are compared to floating-point
//! tolerance: [`REL`], one part in 10^12, unless a module says otherwise and
//! why. Grid positions, counts, choices and text are compared exactly.

mod calibrate;
mod cell_edges;
mod curve_mix;
mod curve_registry;
mod curve_view;
mod date_format;
mod f14c;
mod interpolation;
mod kde;
mod likelihood;
mod linspace;
mod reservoir;
mod resolution;
mod rng;
mod same_event;

use std::collections::BTreeSet;

use radical_corpus::{Case, Family, as_f64, as_f64s, load_family, load_manifest};
use serde_json::Value;

/// The tolerance a deterministic case is held to, relative to the value
/// expected.
pub const REL: f64 = 1e-12;

/// The looser tolerance for a likelihood, and the posterior masses and
/// densities made from it, value by value.
///
/// Why: the likelihood is exp(-z^2/2) with z = (F_m - F_c) / sigma_F, the
/// difference of two fractions modern in units of their combined error. A
/// one-ulp difference in the platform's `exp` moves F by about 1e-16, which
/// moves z by 1e-16 / sigma_F, and the value by z times that: for a 15-year
/// date sigma_F is F * 15 / 8033, so at the stored window's floor (z ~ 7.4)
/// one ulp of `exp` becomes about 4e-13 of the value. On the platform the
/// corpus was generated on the worst seen is 6e-14 (the cubic spline's
/// solve, in a MatchOxcal tail); against glibc's `exp` it is 3.9e-13
/// (`calibrate/shcal20/95+-15`, the posterior's tail). This leaves room for
/// a second ulp, as from a C runtime whose `exp` is less exact. Everything
/// summed from these values -- HPD shares, point estimates -- is held to
/// [`REL`], since the bulk of the distribution carries it.
pub const REL_LIKELIHOOD: f64 = 5e-12;

/// The families compared here; every units/ family must be one.
const COVERED: [&str; 15] = [
    "calibrate",
    "cell_edges",
    "curve_mix",
    "curve_registry",
    "curve_view",
    "date_format",
    "f14c",
    "interpolation",
    "kde",
    "likelihood",
    "linspace",
    "reservoir",
    "resolution",
    "rng",
    "same_event",
];

#[test]
fn every_units_family_has_a_parity_test() {
    let listed: BTreeSet<String> = load_manifest()
        .unwrap()
        .files
        .into_keys()
        .filter_map(|p| {
            p.strip_prefix("data/units/")
                .and_then(|p| p.strip_suffix(".json"))
                .map(str::to_owned)
        })
        .collect();
    let covered: BTreeSet<String> = COVERED.iter().map(|s| (*s).to_owned()).collect();
    assert_eq!(listed, covered);
}

#[test]
fn the_embedded_curves_are_the_ones_the_corpus_was_made_with() {
    for (id, entry) in load_manifest().unwrap().curves {
        let bytes = radical_core::curves::builtin_bytes(&id).unwrap();
        assert_eq!(radical_corpus::sha256_hex(bytes), entry.sha256, "{id}");
    }
}

pub fn family(name: &str) -> Family {
    load_family(&format!("units/{name}")).unwrap()
}

pub fn num(v: &Value) -> f64 {
    as_f64(v).unwrap_or_else(|| panic!("not a corpus number: {v}"))
}

pub fn nums(v: &Value) -> Vec<f64> {
    as_f64s(v).unwrap_or_else(|| panic!("not an array of corpus numbers: {v}"))
}

/// A number, or None for JSON null.
pub fn opt(v: &Value) -> Option<f64> {
    if v.is_null() { None } else { Some(num(v)) }
}

pub fn outputs(case: &Case) -> Value {
    case.expanded_outputs()
        .unwrap_or_else(|| panic!("{}: expected outputs, found {:?}", case.name, case.error))
}

/// The largest relative difference seen, so a run can report how close the
/// port came as well as whether it was close enough.
#[derive(Default)]
pub struct Closeness {
    pub worst: f64,
    pub worst_at: String,
    pub compared: usize,
}

impl Closeness {
    /// `got` within `rel` of `want`, relative to `want`. NaN matches NaN and
    /// infinities match exactly.
    pub fn check(&mut self, label: &str, got: f64, want: f64, rel: f64) {
        self.compared += 1;
        if got == want || (got.is_nan() && want.is_nan()) {
            return;
        }
        let err = ((got - want) / want).abs();
        if err > self.worst || err.is_nan() {
            self.worst = err;
            self.worst_at = label.to_owned();
        }
        assert!(
            err <= rel,
            "{label}: {got:e} differs from {want:e} by {err:e} (allowed {rel:e})"
        );
    }

    pub fn check_all(&mut self, label: &str, got: &[f64], want: &[f64], rel: f64) {
        assert_eq!(got.len(), want.len(), "{label}: lengths differ");
        for (i, (&g, &w)) in got.iter().zip(want).enumerate() {
            self.check(&format!("{label}[{i}]"), g, w, rel);
        }
    }

    pub fn report(&self, family: &str) {
        eprintln!(
            "units/{family}: {} values, worst relative difference {:e}{}",
            self.compared,
            self.worst,
            if self.worst > 0.0 {
                format!(" at {}", self.worst_at)
            } else {
                String::new()
            }
        );
    }
}

/// Exactly the same double, for values that are positions on a grid or
/// copied straight from an input.
pub fn same(label: &str, got: f64, want: f64) {
    assert!(
        got.to_bits() == want.to_bits() || (got.is_nan() && want.is_nan()),
        "{label}: {got} != {want}"
    );
}

/// HPD ranges: the same grid points exactly, the shares to `REL`.
pub fn check_ranges(c: &mut Closeness, label: &str, got: &[radical_core::HpdRange], want: &Value) {
    let want = want.as_array().unwrap();
    assert_eq!(
        got.len(),
        want.len(),
        "{label}: a different number of ranges"
    );
    for (k, (g, w)) in got.iter().zip(want).enumerate() {
        same(&format!("{label}[{k}].min"), g.min, num(&w["min"]));
        same(&format!("{label}[{k}].max"), g.max, num(&w["max"]));
        c.check(
            &format!("{label}[{k}].probability_percent"),
            g.probability_percent,
            num(&w["probability_percent"]),
            REL,
        );
    }
}
