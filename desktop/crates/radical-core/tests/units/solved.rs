//! Comparing a solver's whole answer with Python's: shared by the chain,
//! parallel and wiggle-match families.

use crate::model::opt_num;
use crate::*;
use radical_core::calibration::HpdRange;
use radical_core::exact_chain::{Evidence, Posterior, Solution};

/// The tolerance a solve is held to, relative to each value. Everything a
/// solve reports passes through matrix products, which BLAS sums in an order
/// of its own (`radical_core::matrix`); the worst seen is about 1e-13. The
/// models test explains the headroom.
pub const REL_SOLVE: f64 = 1e-10;

/// A series compared value by value relative to its own peak (see the models
/// test).
pub fn check_peaked(c: &mut Closeness, label: &str, got: &[f64], want: &[f64], rel: f64) {
    assert_eq!(got.len(), want.len(), "{label}: lengths differ");
    let peak = want.iter().fold(0.0_f64, |m, v| m.max(v.abs()));
    for (k, (&g, &w)) in got.iter().zip(want).enumerate() {
        c.compared += 1;
        if g == w {
            continue;
        }
        let err = (g - w).abs() / peak;
        if err > c.worst {
            c.worst = err;
            c.worst_at = format!("{label}[{k}]");
        }
        assert!(
            err <= rel,
            "{label}[{k}]: {g:e} differs from {w:e} by {err:e} of the series' peak"
        );
    }
}

pub fn ranges(c: &mut Closeness, label: &str, got: &[HpdRange], want: &Value, rel: f64) {
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
            rel,
        );
    }
}

pub fn posterior(c: &mut Closeness, label: &str, got: &Posterior, want: &Value, rel: f64) {
    c.check(
        &format!("{label}.mean"),
        got.posterior_mean,
        num(&want["posterior_mean"]),
        rel,
    );
    c.check(
        &format!("{label}.median"),
        got.posterior_median,
        num(&want["posterior_median"]),
        rel,
    );
    c.check(
        &format!("{label}.std"),
        got.posterior_std,
        num(&want["posterior_std"]),
        rel,
    );
    for (field, g) in [("hpd_68", got.hpd_68), ("hpd_95", got.hpd_95)] {
        let w = nums(&want[field]);
        same(&format!("{label}.{field}[0]"), g.0, w[0]);
        same(&format!("{label}.{field}[1]"), g.1, w[1]);
    }
    ranges(
        c,
        &format!("{label}.hpd_68_ranges"),
        &got.hpd_68_ranges,
        &want["hpd_68_ranges"],
        rel,
    );
    ranges(
        c,
        &format!("{label}.hpd_95_ranges"),
        &got.hpd_95_ranges,
        &want["hpd_95_ranges"],
        rel,
    );
    let series = want["probability_distribution"].as_array().unwrap();
    let xs: Vec<f64> = series.iter().map(|p| num(&p[0])).collect();
    let ys: Vec<f64> = series.iter().map(|p| num(&p[1])).collect();
    let got_x: Vec<f64> = got.probability_distribution.iter().map(|p| p[0]).collect();
    let got_y: Vec<f64> = got.probability_distribution.iter().map(|p| p[1]).collect();
    assert_eq!(got_x, xs, "{label}: a different plotted window");
    check_peaked(
        c,
        &format!("{label}.probability_distribution"),
        &got_y,
        &ys,
        rel,
    );
    // Inverse-CDF draws from the marginal on the seeded generator: the same
    // uniforms, read through a cumulative sum equal to rounding.
    c.check_all(
        &format!("{label}.posterior_samples"),
        &got.posterior_samples,
        &nums(&want["posterior_samples"]),
        rel,
    );
    match want.get("outlier_probability") {
        None => assert!(
            got.outlier_probability.is_none(),
            "{label}: an outlier probability"
        ),
        Some(w) => c.check(
            &format!("{label}.outlier_probability"),
            got.outlier_probability.expect("an outlier probability"),
            num(w),
            rel,
        ),
    }
}

/// Joint draws are grid points, so they agree exactly unless a uniform falls
/// within rounding of a cell edge.
fn draws(label: &str, got: &radical_core::intervals::Draws, want: &Value) {
    let want = want.as_object().unwrap();
    assert_eq!(
        got.len(),
        want.len(),
        "{label}: joint draws of different parameters"
    );
    for (id, w) in want {
        assert_eq!(got[id], nums(w), "{label}: {id}'s joint draws");
    }
}

pub fn solution(c: &mut Closeness, label: &str, got: &Solution, want: &Value) {
    let measurements = want["measurements"].as_array().unwrap();
    assert_eq!(got.measurements.len(), measurements.len(), "{label}: dates");
    for ((id, g), w) in got.measurements.iter().zip(measurements) {
        assert_eq!(id, w[0].as_str().unwrap(), "{label}: date order");
        posterior(c, &format!("{label}/{id}"), g, &w[1], REL_SOLVE);
    }
    let boundaries = want["boundaries"].as_array().unwrap();
    assert_eq!(
        got.boundaries.len(),
        boundaries.len(),
        "{label}: boundaries"
    );
    for ((id, g), w) in got.boundaries.iter().zip(boundaries) {
        assert_eq!(id, w[0].as_str().unwrap(), "{label}: boundary order");
        assert_eq!(g.name, w[1]["name"], "{label}");
        posterior(
            c,
            &format!("{label}/{id}"),
            &g.posterior,
            &w[1]["posterior"],
            REL_SOLVE,
        );
    }
    draws(label, &got.joint_draws, &want["joint_draws"]);
    draws(label, &got.joint_boundaries, &want["joint_boundaries"]);
    match (&got.intervals, want["intervals"].is_null()) {
        (None, true) => {}
        (Some(block), false) => crate::intervals::check_block(
            c,
            &format!("{label}/intervals"),
            block,
            &want["intervals"],
            REL_SOLVE,
        ),
        (got, _) => panic!("{label}: intervals {got:?} against {}", want["intervals"]),
    }
    c.check(
        &format!("{label}.log_evidence"),
        got.log_evidence,
        num(&want["log_evidence"]),
        REL_SOLVE,
    );
    assert_eq!(
        got.uniform_span_prior,
        want["uniform_span_prior"].as_bool().unwrap(),
        "{label}"
    );
    let grid = &want["grid"];
    same(
        &format!("{label}.grid.lower"),
        got.grid.lower,
        num(&grid["lower"]),
    );
    same(
        &format!("{label}.grid.upper"),
        got.grid.upper,
        num(&grid["upper"]),
    );
    same(
        &format!("{label}.grid.step"),
        got.grid.step,
        num(&grid["step"]),
    );
    assert_eq!(
        got.grid.first_pass_step,
        opt_num(&grid["first_pass_step"]),
        "{label}"
    );
    c.check(
        &format!("{label}.edge_mass"),
        got.edge_mass,
        num(&want["edge_mass"]),
        REL_SOLVE,
    );
    match (&got.outlier_scale, want["outlier_scale"].as_object()) {
        (None, None) => {}
        (Some(scales), Some(w)) => {
            assert_eq!(scales.len(), w.len(), "{label}: outlier models");
            for (model, s) in scales {
                let w = &w[model];
                c.check(
                    &format!("{label}/{model}.mean"),
                    s.log10_mean,
                    num(&w["log10_mean"]),
                    REL_SOLVE,
                );
                let hpd = nums(&w["log10_hpd_68"]);
                c.check(
                    &format!("{label}/{model}.lo"),
                    s.log10_hpd_68.0,
                    hpd[0],
                    REL_SOLVE,
                );
                c.check(
                    &format!("{label}/{model}.hi"),
                    s.log10_hpd_68.1,
                    hpd[1],
                    REL_SOLVE,
                );
                c.check(
                    &format!("{label}/{model}.years"),
                    s.years_mean,
                    num(&w["years_mean"]),
                    REL_SOLVE,
                );
            }
            let joint = got.joint_scales.as_ref().expect("joint scales");
            for (model, values) in want["joint_scales"].as_object().unwrap() {
                assert_eq!(
                    joint[model],
                    nums(values),
                    "{label}: {model}'s joint scales"
                );
            }
        }
        (got, want) => panic!("{label}: outlier scale {got:?} against {want:?}"),
    }
    match (&got.evidence, &want["evidence"]) {
        (None, w) => assert!(w.is_null(), "{label}: evidence missing"),
        (Some(Evidence::Unavailable(reason)), w) => {
            assert_eq!(w["available"], false, "{label}");
            assert_eq!(reason, w["reason"].as_str().unwrap(), "{label}");
        }
        (
            Some(Evidence::Available {
                log,
                log10,
                lower,
                upper,
                step,
                approximate,
                readings,
            }),
            w,
        ) => {
            assert_eq!(w["available"], true, "{label}: {w}");
            c.check(
                &format!("{label}/evidence.log"),
                *log,
                num(&w["log"]),
                REL_SOLVE,
            );
            c.check(
                &format!("{label}/evidence.log10"),
                *log10,
                num(&w["log10"]),
                REL_SOLVE,
            );
            same(label, *lower, num(&w["lower"]));
            same(label, *upper, num(&w["upper"]));
            same(label, *step, num(&w["step"]));
            assert_eq!(*approximate, w["approximate"].as_bool().unwrap(), "{label}");
            assert_eq!(readings, w["readings"].as_str().unwrap(), "{label}");
        }
    }
}
