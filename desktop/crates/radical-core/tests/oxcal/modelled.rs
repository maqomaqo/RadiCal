//! The exact solver's modelled answers on Hillside, against OxCal's.

use std::sync::OnceLock;

use radical_core::exact_chain::{Solution, SolveOptions};

use crate::export::{Raw, Table};
use crate::fixture::{
    NORTH, SOUTH, exact, exact_boundary, exact_with, hillside, max_abs, mean, model, posterior, rms,
};

/// What each setting of the UniformSpanPrior is held to on `shared_boundary`.
/// OxCal sits between the two: the prior's correction is what brings the
/// separation of the groups down to OxCal's 13 years.
struct Expected {
    rms: f64,
    max: f64,
    separation: (f64, f64),
    lobe_gap: f64,
}

const WITH_SPAN_PRIOR: Expected = Expected {
    rms: 2.0,
    max: 3.0,
    separation: (12.0, 19.0),
    lobe_gap: 0.15,
};
const WITHOUT_SPAN_PRIOR: Expected = Expected {
    rms: 8.0,
    max: 10.0,
    separation: (22.0, 31.0),
    lobe_gap: 0.30,
};

fn shared_boundary(span_prior: bool) -> &'static Solution {
    static WITH: OnceLock<Solution> = OnceLock::new();
    static WITHOUT: OnceLock<Solution> = OnceLock::new();
    let options = SolveOptions {
        uniform_span_prior: span_prior,
        ..exact_with(2000)
    };
    let solve = || exact(model(hillside("shared_boundary")), &options);
    if span_prior {
        WITH.get_or_init(solve)
    } else {
        WITHOUT.get_or_init(solve)
    }
}

fn settings() -> [(bool, Expected); 2] {
    [(true, WITH_SPAN_PRIOR), (false, WITHOUT_SPAN_PRIOR)]
}

fn mean_of(solved: &Solution, run: &str, names: &[&str]) -> f64 {
    let m = model(hillside(run));
    mean(
        &names
            .iter()
            .map(|n| posterior(&solved.measurements, m.id(n)).posterior_mean)
            .collect::<Vec<_>>(),
    )
}

#[test]
fn the_grid_covers_the_posterior() {
    // A heavy Boundary tail running off the end of the grid would bias
    // everything, so the solver reports how much mass sits at the edges.
    for (span_prior, _) in settings() {
        let edge = shared_boundary(span_prior).edge_mass;
        assert!(edge < 0.01, "span prior {span_prior}: {edge}");
    }
}

#[test]
fn modelled_dates_land_on_oxcals() {
    let m = model(hillside("shared_boundary"));
    let raw = Raw::read(hillside("shared_boundary"));
    for (span_prior, expected) in settings() {
        let solved = shared_boundary(span_prior);
        let errors: Vec<f64> = m
            .names()
            .iter()
            .map(|n| {
                posterior(&solved.measurements, m.id(n)).posterior_mean - raw.posterior(n).mean()
            })
            .collect();
        assert!(
            max_abs(&errors) < expected.max,
            "span prior {span_prior}: {errors:?}"
        );
        assert!(
            rms(&errors) < expected.rms,
            "span prior {span_prior}: {errors:?}"
        );
    }
}

/// OxCal separates the two groups' means by 13 years. The span prior brings
/// the solver to it; leaving the prior off overshoots it.
#[test]
fn the_groups_are_as_far_apart_as_oxcal_puts_them() {
    for (span_prior, expected) in settings() {
        let solved = shared_boundary(span_prior);
        // In years AD, South minus North.
        let separation =
            mean_of(solved, "shared_boundary", &NORTH) - mean_of(solved, "shared_boundary", &SOUTH);
        let (low, high) = expected.separation;
        assert!(
            low < separation && separation < high,
            "span prior {span_prior}: {separation}"
        );
    }
}

/// The ordering must not be satisfied by pushing North into the early
/// calibration lobe and South into the late one, as a sampler once did --
/// 0.00 and 0.71 of the mass around AD 1370-1420 against OxCal's 0.12 and
/// 0.17. Both groups keep a comparable, modest share.
#[test]
fn no_spurious_lobe_split() {
    let m = model(hillside("shared_boundary"));
    let (lo, hi) = (1950.0 - 1420.0, 1950.0 - 1370.0);
    for (span_prior, expected) in settings() {
        let solved = shared_boundary(span_prior);
        let share = |names: &[&str]| {
            mean(
                &names
                    .iter()
                    .map(|n| {
                        let d = &posterior(&solved.measurements, m.id(n)).probability_distribution;
                        let total: f64 = d.iter().map(|p| p[1]).sum();
                        let inside: f64 = d
                            .iter()
                            .filter(|p| p[0] >= lo && p[0] <= hi)
                            .map(|p| p[1])
                            .sum();
                        inside / total
                    })
                    .collect::<Vec<_>>(),
            )
        };
        let (north, south) = (share(&NORTH), share(&SOUTH));
        assert!(
            0.02 < north && north < 0.35,
            "span prior {span_prior}: {north}"
        );
        assert!(
            0.02 < south && south < 0.35,
            "span prior {span_prior}: {south}"
        );
        assert!(
            (north - south).abs() < expected.lobe_gap,
            "span prior {span_prior}: {north} against {south}"
        );
    }
}

/// The shared transition is the one Boundary the dates pin down well.
#[test]
fn the_shared_boundary_lands_on_oxcals() {
    let want = Table::read(hillside("shared_boundary"))
        .get("North to South")
        .modelled
        .0[1][0];
    for (span_prior, _) in settings() {
        let got = &exact_boundary(shared_boundary(span_prior), "North to South").hpd_95_ranges;
        let older = got.iter().map(|r| r.max).fold(f64::NEG_INFINITY, f64::max);
        let younger = got.iter().map(|r| r.min).fold(f64::INFINITY, f64::min);
        assert!(
            (1950.0 - older - want.0).abs() <= 10.0 && (1950.0 - younger - want.1).abs() <= 10.0,
            "span prior {span_prior}: AD {}-{} against {want:?}",
            1950.0 - older,
            1950.0 - younger
        );
    }
}

// Two ordered groups against OxCal, closed to about a year:
//
//     run               OxCal   exact
//     unordered          -0.1     0.0
//     ordered            11.7    12.0
//     shared_boundary    13.2    13.5
//
// `ordered` has no Boundary between its groups, and the solver takes them as
// written, as blocks, rather than supplying one: that would make it the SAME
// model as shared_boundary, where OxCal's two runs differ by 1.5 years.
//
// Four causes were found by measurement on the way: the interval quadrature
// giving an interval of span k*step one cell too much likelihood (most of it);
// the same in the sampler; the interior-boundary factor dividing by a
// continuum power where the discrete model needs a binomial count (~1.1
// years); and the span prior's LIMITS factor, absent entirely, identified by
// asking OxCal to run the same models with UniformSpanPrior off.

/// North's mean minus South's, in cal BP.
fn separation(run: &str, uniform_span_prior: bool) -> f64 {
    let options = SolveOptions {
        uniform_span_prior,
        ..exact_with(100)
    };
    let solved = exact(model(hillside(run)), &options);
    mean_of(&solved, run, &NORTH) - mean_of(&solved, run, &SOUTH)
}

fn oxcals_separation(run: &str) -> f64 {
    let raw = Raw::read(hillside(run));
    let of = |names: &[&str]| {
        mean(
            &names
                .iter()
                .map(|n| raw.posterior(n).mean())
                .collect::<Vec<_>>(),
        )
    };
    of(&NORTH) - of(&SOUTH)
}

/// The control: same dates, curve and priors, only the ordering removed.
#[test]
fn with_nothing_ordering_the_groups_we_agree_exactly() {
    let (got, want) = (
        separation("unordered", true),
        oxcals_separation("unordered"),
    );
    assert!((got - want).abs() <= 0.5, "{got} against {want}");
}

/// Measured at +0.36 on `ordered` and +0.26 on `shared_boundary`.
#[test]
fn ordered_groups_agree_to_about_a_year() {
    for run in ["ordered", "shared_boundary"] {
        let excess = separation(run, true) - oxcals_separation(run);
        assert!(excess.abs() < 1.5, "{run}: {excess}");
    }
}

/// Switching the UniformSpanPrior off is much worse (26.7 years against
/// OxCal's 13.2), and OxCal agrees when ITS option is off -- 26.30 -- which
/// is what identified the missing factor. So this is not an artefact of
/// applying the correction.
#[test]
fn both_span_prior_refinements_earn_their_place() {
    let with = separation("shared_boundary", true);
    let without = separation("shared_boundary", false);
    let oxcal = oxcals_separation("shared_boundary");
    assert!(
        (with - oxcal).abs() < (without - oxcal).abs() / 4.0,
        "{with} and {without} against {oxcal}"
    );
    assert!(
        (without - 26.5).abs() <= 1.5,
        "OxCal with its own option off reports 26.30 here: {without}"
    );
}
