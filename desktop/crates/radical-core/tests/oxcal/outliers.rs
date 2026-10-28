//! The outlier models against OxCal: the General model on Dune Section, and
//! both models with three declared-residual dates on the charcoal Hillside
//! run, on each solver.
//!
//! OxCal's Outlier_Model carries ONE offset scale for every date using it,
//! constrained by the data. The sampler draws that shared scale; the exact
//! solver weighs it from the data with evidence passes and mixes each flagged
//! date's offset over that posterior. Mixing over the scale's PRIOR instead,
//! as it once did, left Dune Section's probabilities 0.0166 from OxCal's and
//! the charcoal run 7.1 years rms from OxCal's dates, against 0.0016 and 3.45.

use std::sync::OnceLock;

use radical_core::exact_chain::scale_nodes;
use radical_core::gibbs::{SampleOptions, Sampled};

use crate::export::{Raw, Table, interp, years};
use crate::fixture::{
    CHARCOAL, CHARCOAL_SOUTH, DECLARED_RESIDUAL, DUNE, DUNE_OUTLIER, DUNE_PRIOR, Model,
    exact_boundary, max_abs, mean, model, posterior, rms, sample, solved, untempered,
};

fn outlier_probabilities(
    m: &Model,
    results: &[(String, radical_core::exact_chain::Posterior)],
) -> Vec<(String, f64)> {
    m.names()
        .into_iter()
        .map(|n| {
            let p = posterior(results, m.id(&n)).outlier_probability.unwrap();
            (n, p)
        })
        .collect()
}

/// The worst difference from OxCal's outlier probabilities, 1 - P/100.
fn worst_against_oxcal(probabilities: &[(String, f64)]) -> f64 {
    let table = Table::read(DUNE);
    max_abs(
        &probabilities
            .iter()
            .map(|(n, p)| p - table.get(n).outlier_probability())
            .collect::<Vec<_>>(),
    )
}

/// LB10002 is the outlier, and every sound date lands BELOW its own 5% prior:
/// the evidence argues them down rather than leaving them where they started.
fn finds_the_outlier_oxcal_found(probabilities: &[(String, f64)]) {
    for (name, p) in probabilities {
        if name == DUNE_OUTLIER {
            assert!(*p > 0.95, "{name}: {p}");
        } else {
            assert!(*p < DUNE_PRIOR, "{name}: {p}");
        }
    }
}

#[test]
fn exact_dune_finds_the_outlier_oxcal_found() {
    let m = model(DUNE);
    let probabilities = outlier_probabilities(m, &solved(DUNE).measurements);
    finds_the_outlier_oxcal_found(&probabilities);
    // 0.0016 from OxCal's at most; 0.0158 when each date's offset was mixed
    // over the scale's prior.
    let worst = worst_against_oxcal(&probabilities);
    assert!(worst < 0.003, "{worst}");
}

/// Dune Section: one phase, sixteen dates, a 5% General prior. The span was
/// 4.2 years against OxCal's 19.3 before the interval quadrature was
/// corrected; it measures 16.8 now.
#[test]
fn exact_dune_phase_is_as_wide_as_oxcals() {
    let solution = solved(DUNE);
    let span = exact_boundary(solution, "Start").posterior_mean
        - exact_boundary(solution, "End").posterior_mean;
    let raw = Raw::read(DUNE);
    let oxcal = raw.posterior("Start").mean() - raw.posterior("End").mean();
    assert!((oxcal - 19.3).abs() <= 1.0, "{oxcal}");
    assert!(
        (span - oxcal).abs() <= 0.25 * oxcal,
        "{span} against {oxcal}"
    );
}

/// A trap, pinned because it manufactured a 30% discrepancy that does not
/// exist. OxCal exports about 250 years around each parameter; the solver
/// trims its curve at 1e-4 of the peak. For a date with a broad outlier
/// component those rules keep very different amounts of tail, so comparing
/// standard deviations on each curve's OWN support compares truncation rules:
/// 0.70x OxCal's width, where on a common window the same solve is 0.97x.
#[test]
fn exact_dune_widths_compare_only_on_a_common_window() {
    let m = model(DUNE);
    let raw = Raw::read(DUNE);
    let solution = solved(DUNE);
    let sd = |window: (f64, f64), cal: &[f64], prob: &[f64]| {
        let axis = years(window.0, window.1 + 1e-9);
        let density: Vec<f64> = axis
            .iter()
            .map(|&x| interp(x, cal, prob, Some(0.0)))
            .collect();
        let total: f64 = density.iter().sum();
        let mean: f64 = axis.iter().zip(&density).map(|(a, d)| a * d / total).sum();
        axis.iter()
            .zip(&density)
            .map(|(a, d)| (a - mean).powi(2) * d / total)
            .sum::<f64>()
            .sqrt()
    };
    let (mut own, mut common) = (Vec::new(), Vec::new());
    for name in m.names() {
        let theirs = raw.posterior(&name);
        let mut pairs = posterior(&solution.measurements, m.id(&name))
            .probability_distribution
            .clone();
        pairs.sort_by(|a, b| a[0].total_cmp(&b[0]));
        let cal: Vec<f64> = pairs.iter().map(|p| p[0]).collect();
        let prob: Vec<f64> = pairs.iter().map(|p| p[1]).collect();
        let (t0, t1) = (theirs.cal[0], theirs.cal[theirs.cal.len() - 1]);
        let (o0, o1) = (cal[0], cal[cal.len() - 1]);
        own.push(sd((o0, o1), &cal, &prob) / sd((t0, t1), &theirs.cal, &theirs.prob));
        let window = (o0.max(t0), o1.min(t1));
        common.push(sd(window, &cal, &prob) / sd(window, &theirs.cal, &theirs.prob));
    }
    assert!((mean(&common) - 1.0).abs() <= 0.1, "{}", mean(&common));
    assert!(
        mean(&own) < 0.85,
        "the naive comparison should still be the misleading one; if it agrees now \
         the trimming rules have changed and this demonstrates nothing: {}",
        mean(&own)
    );
}

/// The sampler on Dune Section, with exactly 20,000 untempered sweeps on the
/// exact solver's grid, so its run length does not depend on how hard the
/// model is to converge.
fn dune_sampled() -> &'static Sampled {
    static SAMPLED: OnceLock<Sampled> = OnceLock::new();
    SAMPLED.get_or_init(|| {
        let grid = &solved(DUNE).grid;
        let points = ((grid.upper - grid.lower) / grid.step + 1e-9).floor() as usize + 1;
        let options = SampleOptions {
            sweeps: 20_000,
            max_sweeps: Some(20_000),
            grid: Some(
                (0..points)
                    .map(|i| grid.lower + i as f64 * grid.step)
                    .collect(),
            ),
            ..untempered(200)
        };
        sample(model(DUNE), &options)
    })
}

#[test]
fn sampled_dune_finds_the_outlier_oxcal_found() {
    let probabilities = outlier_probabilities(model(DUNE), &dune_sampled().measurements);
    finds_the_outlier_oxcal_found(&probabilities);
    // Sharing the scale is the whole of what closes the gap to OxCal: within
    // 0.0016 on all sixteen dates, where marginalising it per date sat 0.0166
    // away.
    let worst = worst_against_oxcal(&probabilities);
    assert!(worst < 0.005, "{worst}");
}

/// OxCal exports the shared scale's own posterior, but here the General
/// model's 5% priors leave the data barely able to constrain it, so all that
/// can be asked is that it stays inside its U(0,4) prior. The charcoal run,
/// below, is where the scale is pinned.
#[test]
fn sampled_dune_scale_stays_in_its_prior() {
    let (_, scale) = dune_sampled()
        .outlier_scale
        .iter()
        .find(|(m, _)| m == "general")
        .unwrap();
    assert!((0.0..=4.0).contains(&scale.log10_mean), "{scale:?}");
    assert!(scale.years_mean > 1.0, "{scale:?}");
}

/// OxCal's modelled mean of every date and Boundary in the charcoal run.
fn charcoal_oxcal_mean(name: &str) -> f64 {
    static RAW: OnceLock<Raw> = OnceLock::new();
    RAW.get_or_init(|| Raw::read(CHARCOAL))
        .posterior(name)
        .mean()
}

fn charcoal_rms(results: &[(String, radical_core::exact_chain::Posterior)]) -> f64 {
    let m = model(CHARCOAL);
    rms(&m
        .names()
        .iter()
        .map(|n| posterior(results, m.id(n)).posterior_mean - charcoal_oxcal_mean(n))
        .collect::<Vec<_>>())
}

/// Measured at 3.45 years rms with the shared scale weighed from the data;
/// 7.1 mixing each date's offset over the scale's prior, and 16.1 with a
/// single fixed 200-year scale.
#[test]
fn exact_charcoal_dates_land_within_five_years() {
    let rms = charcoal_rms(&solved(CHARCOAL).measurements);
    assert!(rms < 4.5, "{rms}");
}

/// Outlier("Charcoal", 1.00) is a statement, not a suspicion: at prior 1.0
/// the mixture IS the outlier component, so the posterior cannot argue it
/// down.
fn declared_residual_dates_are_certain(
    results: &[(String, radical_core::exact_chain::Posterior)],
    others_below: f64,
) {
    let m = model(CHARCOAL);
    for (name, p) in outlier_probabilities(m, results) {
        if DECLARED_RESIDUAL.contains(&name.as_str()) {
            assert!((p - 1.0).abs() <= 1e-6, "{name}: {p}");
        } else {
            assert!(p < others_below, "{name}: {p}");
        }
    }
}

#[test]
fn exact_charcoal_reports_the_declared_outliers() {
    declared_residual_dates_are_certain(&solved(CHARCOAL).measurements, 0.5);
}

/// What the charcoal model is for: the three declared-residual dates' contexts
/// come out substantially YOUNGER than their own calibrations, in the
/// direction OxCal moved them.
#[test]
fn exact_charcoal_pulls_residual_dates_towards_their_context() {
    let m = model(CHARCOAL);
    let raw = Raw::read(CHARCOAL);
    for name in DECLARED_RESIDUAL {
        let unmodelled = raw.likelihood(name).mean();
        let ours = posterior(&solved(CHARCOAL).measurements, m.id(name)).posterior_mean;
        let theirs = charcoal_oxcal_mean(name);
        assert!(
            ours < unmodelled - 15.0,
            "{name}: {ours} against {unmodelled}"
        );
        assert!(
            theirs < unmodelled - 15.0,
            "{name}: OxCal {theirs} against {unmodelled}"
        );
    }
}

/// With three dates declared residual, the span once came out too WIDE
/// against OxCal's 29.2 -- 54.9 years, then 36.3 on the model as written.
/// Weighing the shared scale from the data put it at 29.4.
#[test]
fn exact_charcoal_span_is_oxcals() {
    let means: Vec<f64> = solved(CHARCOAL)
        .boundaries
        .iter()
        .map(|(_, b)| b.posterior.posterior_mean)
        .collect();
    let span = means.iter().copied().fold(f64::NEG_INFINITY, f64::max)
        - means.iter().copied().fold(f64::INFINITY, f64::min);
    let oxcal = charcoal_oxcal_mean("Start Hillside") - charcoal_oxcal_mean("End Hillside");
    assert!((oxcal - 29.2).abs() <= 1.0, "{oxcal}");
    assert!((span - oxcal).abs() <= 3.0, "{span} against {oxcal}");
}

/// Where the remaining difference sits, which any further fix has to move.
/// Averaged over the seven South dates, these marginals carry a young tail
/// OxCal's do not, and are correspondingly short in the oldest lobe:
///
/// ```text
/// cal BP band     OxCal   exact
/// 540-580         0.028   0.246
/// 620-640         0.406   0.274
/// ```
///
/// That is the charcoal kernel's one-sided smear, correct in direction and
/// scale; what differs is how hard the span prior resists it. Asserted as an
/// excess and a deficit rather than at these values.
#[test]
fn exact_charcoal_excess_is_a_young_tail() {
    let m = model(CHARCOAL);
    let raw = Raw::read(CHARCOAL);
    let axis = years(520.0, 700.0);
    // (cal BP ascending, density) for one date.
    type Curve = (Vec<f64>, Vec<f64>);
    let stacked = |curve: &dyn Fn(&str) -> Curve| {
        let mut total = vec![0.0; axis.len()];
        for name in CHARCOAL_SOUTH {
            let (cal, prob) = curve(name);
            let density: Vec<f64> = axis
                .iter()
                .map(|&x| interp(x, &cal, &prob, Some(0.0)))
                .collect();
            let sum: f64 = density.iter().sum();
            for (t, d) in total.iter_mut().zip(&density) {
                *t += d / sum / CHARCOAL_SOUTH.len() as f64;
            }
        }
        total
    };
    let mine = stacked(&|name| {
        let mut pairs = posterior(&solved(CHARCOAL).measurements, m.id(name))
            .probability_distribution
            .clone();
        pairs.sort_by(|a, b| a[0].total_cmp(&b[0]));
        (
            pairs.iter().map(|p| p[0]).collect(),
            pairs.iter().map(|p| p[1]).collect(),
        )
    });
    let theirs = stacked(&|name| {
        let d = raw.posterior(name);
        (d.cal.clone(), d.prob.clone())
    });
    let band = |v: &[f64], lo: f64, hi: f64| -> f64 {
        axis.iter()
            .zip(v)
            .filter(|(a, _)| **a >= lo && **a < hi)
            .map(|(_, x)| x)
            .sum()
    };
    let (mine_young, theirs_young) = (band(&mine, 540.0, 580.0), band(&theirs, 540.0, 580.0));
    let (mine_oldest, theirs_oldest) = (band(&mine, 620.0, 640.0), band(&theirs, 620.0, 640.0));
    assert!(
        mine_young > 3.0 * theirs_young,
        "{mine_young} against {theirs_young}"
    );
    assert!(
        mine_oldest < theirs_oldest,
        "{mine_oldest} against {theirs_oldest}"
    );
}

/// OxCal's exported U(0,3) posterior for the Charcoal model: mean 1.79, 68%
/// 1.49-2.06, pinned well inside a prior that runs to 1000 years -- the one
/// place an inferred quantity, not an answer it feeds, can be compared.
fn recovers_the_charcoal_scale(scales: &[(String, radical_core::exact_chain::OutlierScale)]) {
    let (_, charcoal) = scales.iter().find(|(m, _)| m == "charcoal").unwrap();
    assert!((charcoal.log10_mean - 1.79).abs() <= 0.15, "{charcoal:?}");
    let (low, high) = charcoal.log10_hpd_68;
    assert!(
        (low - 1.49).abs() <= 0.2 && (high - 2.06).abs() <= 0.2,
        "{charcoal:?}"
    );
}

#[test]
fn exact_charcoal_recovers_the_scale_oxcal_inferred() {
    let solution = solved(CHARCOAL);
    recovers_the_charcoal_scale(solution.outlier_scale.as_ref().unwrap());
    let drawn = &solution.joint_scales.as_ref().unwrap()["charcoal"];
    let draws = solution.joint_draws.values().next().unwrap().len();
    assert_eq!(drawn.len(), draws);
    let nodes = scale_nodes("charcoal");
    assert!(drawn.iter().all(|s| nodes.contains(s)));
}

fn charcoal_sampled() -> &'static Sampled {
    static SAMPLED: OnceLock<Sampled> = OnceLock::new();
    SAMPLED.get_or_init(|| sample(model(CHARCOAL), &untempered(200)))
}

/// The sampler is told only the prior and must find what OxCal found. The
/// data really constrain it: the posterior is far narrower than U(0,3). The
/// General model's scale is a separate shared parameter that OxCal puts at
/// 1.81, but with only 5% priors the data barely move it, so it agrees as
/// loosely as its prior is wide.
#[test]
fn sampled_charcoal_recovers_the_scale_oxcal_inferred() {
    let scales = &charcoal_sampled().outlier_scale;
    recovers_the_charcoal_scale(scales);
    let (_, charcoal) = scales.iter().find(|(m, _)| m == "charcoal").unwrap();
    assert!(
        charcoal.log10_hpd_68.1 - charcoal.log10_hpd_68.0 < 1.0,
        "{charcoal:?}"
    );
    let (_, general) = scales.iter().find(|(m, _)| m == "general").unwrap();
    assert!((general.log10_mean - 1.81).abs() <= 0.4, "{general:?}");
}

/// Both solvers sit about 3.4 years rms from OxCal's dates: the exact solver
/// at 3.43, the sampler between 3.0 and 3.7 by seed.
#[test]
fn sampled_charcoal_dates_land_within_five_years() {
    let sampled = charcoal_sampled();
    let rms = charcoal_rms(&sampled.measurements);
    assert!(rms < 4.5, "{rms}");
    declared_residual_dates_are_certain(&sampled.measurements, 0.2);
}
