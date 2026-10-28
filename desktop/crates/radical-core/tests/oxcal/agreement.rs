//! Agreement indices against OxCal's: per date, Aoverall and Amodel.
//!
//! Two separate things are checked and it matters not to conflate them:
//!
//! - feeding OxCal's OWN posterior in must reproduce OxCal's OWN index. That
//!   tests the formula, and is tight.
//! - feeding RadiCal's posterior in gives a number that differs by however
//!   much RadiCal's posterior differs from OxCal's. That is not a test of the
//!   formula and is asserted loosely.

use std::collections::HashMap;
use std::sync::OnceLock;

use radical_core::agreement::{
    POOR_AGREEMENT_BELOW, agreement_index, attach, model_agreement, outlier_scale_terms,
    overall_agreement,
};
use radical_core::exact_chain::scale_nodes;
use radical_core::gibbs::Sampled;

use crate::export::{Raw, Table};
use crate::fixture::{
    DS, DS_ONE_YEAR, DUNE, DUNE_ONE_YEAR, DUNE_OUTLIER, HILLSIDE, HILLSIDE_ONE_YEAR, Model, cal,
    hillside, max_abs, mean, model, posterior, rms, sample, untempered,
};

fn index(m: &Model, name: &str, modelled: &[[f64; 2]], unmodelled: Option<&[[f64; 2]]>) -> f64 {
    agreement_index(m.date(name), modelled, unmodelled, None, &cal())
        .unwrap()
        .unwrap()
}

/// On the Hillside `ordered` model rerun at Resolution=1: OxCal's posterior
/// in, OxCal's index out, at the resolution where OxCal's own has converged.
fn hillside_one_year(own_likelihood: bool) -> (Vec<f64>, Vec<f64>) {
    let m = model(HILLSIDE_ONE_YEAR);
    let (raw, table) = (Raw::read(HILLSIDE_ONE_YEAR), Table::read(HILLSIDE_ONE_YEAR));
    let mut indices = Vec::new();
    let mut errors = Vec::new();
    for name in m.names() {
        let likelihood = raw.likelihood(&name).pairs();
        let got = index(
            m,
            &name,
            &raw.posterior(&name).pairs(),
            (!own_likelihood).then_some(likelihood.as_slice()),
        );
        indices.push(got);
        errors.push(got - table.get(&name).agreement.unwrap());
    }
    (indices, errors)
}

/// The formula itself: OxCal's likelihood AND posterior in, so nothing but
/// the definition is under test. Measured rms 0.23.
#[test]
fn oxcals_posterior_gives_oxcals_index() {
    let (_, errors) = hillside_one_year(false);
    assert!(max_abs(&errors) < 1.0, "{errors:?}");
    assert!(rms(&errors) < 0.5, "{errors:?}");
}

/// The realistic path, this calibration in place of OxCal's: rms 0.30.
#[test]
fn our_own_likelihood_gives_the_same_index() {
    let (_, errors) = hillside_one_year(true);
    assert!(max_abs(&errors) < 1.0, "{errors:?}");
    assert!(rms(&errors) < 0.5, "{errors:?}");
}

#[test]
fn oxcals_posterior_gives_oxcals_overall_index() {
    let (indices, _) = hillside_one_year(true);
    let indices: Vec<Option<f64>> = indices.into_iter().map(Some).collect();
    let got = overall_agreement(&indices, 0).unwrap();
    let want = Table::read(HILLSIDE_ONE_YEAR).aoverall;
    assert!((got - want).abs() <= 0.5, "{got} against {want}");
}

/// OxCal at its default five years is a coarser approximation of the same
/// integrals, and on these dates reads about 0.7 below the converged value
/// per date -- a consistent offset, not scatter. Pinned so the difference an
/// analyst sees against a default OxCal run stays explained.
#[test]
fn oxcals_default_resolution_reads_lower() {
    for (run, stem) in HILLSIDE {
        let m = model(stem);
        let (raw, table) = (Raw::read(stem), Table::read(stem));
        let errors: Vec<f64> = m
            .names()
            .iter()
            .map(|n| index(m, n, &raw.posterior(n).pairs(), None) - table.get(n).agreement.unwrap())
            .collect();
        let offset = mean(&errors);
        assert!(0.3 < offset && offset < 1.0, "{run}: {offset}");
        let spread = max_abs(&errors.iter().map(|e| e - offset).collect::<Vec<_>>());
        assert!(spread < 0.6, "{run}: {errors:?}");
    }
}

fn oxcals_indices(table: &Table) -> Vec<Option<f64>> {
    table
        .date_indices()
        .into_iter()
        .map(|(_, a)| Some(a))
        .collect()
}

/// The combining rule alone: fed OxCal's own published indices, it must
/// return OxCal's own Aoverall, at either resolution, with the outlier
/// models' scale terms counted in n.
#[test]
fn the_combination_reproduces_oxcals_aoverall() {
    for stem in [HILLSIDE_ONE_YEAR, DUNE_ONE_YEAR, DS, DS_ONE_YEAR] {
        let table = Table::read(stem);
        let got = overall_agreement(
            &oxcals_indices(&table),
            outlier_scale_terms(&model(stem).dates),
        )
        .unwrap();
        assert!(
            (got - table.aoverall).abs() <= 0.15,
            "{stem}: {got} against {}",
            table.aoverall
        );
    }
    for (run, stem) in HILLSIDE {
        let table = Table::read(stem);
        let got = overall_agreement(&oxcals_indices(&table), 0).unwrap();
        assert!(
            (got - table.aoverall).abs() <= 0.15,
            "{run}: {got} against {}",
            table.aoverall
        );
    }
}

/// OxCal counts each outlier model's scale parameter -- A = 100 in its table
/// -- in the n of Aoverall and Amodel. Without it, OxCal's own indices give
/// 44.5 on DS against its reported 45.9.
#[test]
fn leaving_the_scale_term_out_misses_oxcals_aoverall() {
    let table = Table::read(DS);
    let got = overall_agreement(&oxcals_indices(&table), 0).unwrap();
    assert!(
        got < table.aoverall - 1.0,
        "{got} against {}",
        table.aoverall
    );
}

/// Why Amodel needs the joint posterior rather than the A column: on the
/// `ordered` run the date indices multiply to more than one, so every
/// exponent of that product lands above 100 while OxCal reports 96.0.
/// Amodel carries the correlation the model puts between the dates.
#[test]
fn amodel_is_not_derivable_from_the_date_indices() {
    let table = Table::read(hillside("ordered"));
    let product: f64 = table
        .date_indices()
        .iter()
        .map(|(_, a)| a / 100.0)
        .product();
    assert!(product > 1.0, "{product}");
}

// Dune Section, where an outlier model is running. These feed OxCal's own
// posterior and likelihood in with no outlier model in the comparison, which
// reads systematically low against OxCal's index; a solved run scores the
// outlier model's own term, and lands on OxCal's (below).

fn dune_indices() -> Vec<(String, f64)> {
    let m = model(DUNE);
    let raw = Raw::read(DUNE);
    m.names()
        .into_iter()
        .map(|n| {
            let likelihood = raw.likelihood(&n).pairs();
            let i = index(m, &n, &raw.posterior(&n).pairs(), Some(&likelihood));
            (n, i)
        })
        .collect()
}

#[test]
fn the_index_flags_the_outlier_and_nothing_else() {
    let scores = dune_indices();
    for (name, score) in &scores {
        if name == DUNE_OUTLIER {
            assert!(*score < POOR_AGREEMENT_BELOW, "{name}: {score}");
        } else {
            assert!(*score > POOR_AGREEMENT_BELOW, "{name}: {score}");
        }
    }
    let lowest = scores.iter().min_by(|a, b| a.1.total_cmp(&b.1)).unwrap();
    assert_eq!(lowest.0, DUNE_OUTLIER);
}

/// The fifteen sound dates read about 5 low: OxCal compares against a
/// likelihood broadened by the 5% outlier component and this does not.
/// Feeding a mixture in closes most of it, which is what identified the
/// cause.
#[test]
fn without_the_outlier_term_sound_dates_read_low() {
    let table = Table::read(DUNE);
    let errors: Vec<f64> = dune_indices()
        .iter()
        .filter(|(n, _)| n != DUNE_OUTLIER)
        .map(|(n, i)| i - table.get(n).agreement.unwrap())
        .collect();
    let offset = mean(&errors);
    assert!(-8.0 < offset && offset < -1.0, "{offset}");
    assert!(max_abs(&errors) < 8.0, "{errors:?}");
}

/// A date the model has pushed entirely off its likelihood has no overlap
/// left to measure, so the index bottoms out; OxCal reports 5.3 because its
/// comparison carries the outlier component's tail. And OxCal's own indices
/// combine to its Aoverall once the scale term is counted.
#[test]
fn without_the_outlier_term_the_outlier_reads_zero() {
    let table = Table::read(DUNE);
    let (_, score) = dune_indices()
        .into_iter()
        .find(|(n, _)| n == DUNE_OUTLIER)
        .unwrap();
    assert!(score < 1.0, "{score}");
    assert!(table.get(DUNE_OUTLIER).agreement.unwrap() < 10.0);
    let combined = overall_agreement(
        &oxcals_indices(&table),
        outlier_scale_terms(&model(DUNE).dates),
    )
    .unwrap();
    assert!(
        (combined - table.aoverall).abs() <= 0.2,
        "{combined} against {}",
        table.aoverall
    );
}

/// OxCal's exported outlier-scale posterior, binned onto the solvers' scale
/// nodes: (log10 scale, weight).
fn scale_posterior(raw: &Raw) -> Vec<(f64, f64)> {
    let nodes = scale_nodes("general");
    let step = nodes[1] - nodes[0];
    let mut weights = vec![0.0; nodes.len()];
    for &(u, w) in &raw.scale {
        let i = ((u - nodes[0]) / step)
            .round()
            .clamp(0.0, (nodes.len() - 1) as f64) as usize;
        weights[i] += w;
    }
    let total: f64 = weights.iter().sum();
    let kept: Vec<(f64, f64)> = nodes
        .iter()
        .zip(&weights)
        .filter(|(_, w)| **w / total > 1e-4)
        .map(|(&u, &w)| (u, w))
        .collect();
    let kept_total: f64 = kept.iter().map(|k| k.1).sum();
    kept.into_iter().map(|(u, w)| (u, w / kept_total)).collect()
}

/// OxCal's posteriors and its scale posterior in, OxCal's indices out, the
/// flagged dates included. Measured per-date rms 0.47 on Dune Section and
/// 0.42 on DS. The scale is taken as independent of each date here, which
/// the exports force; a run pairs them draw by draw.
#[test]
fn oxcals_outlier_runs_give_oxcals_indices() {
    for stem in [DUNE_ONE_YEAR, DS_ONE_YEAR] {
        let m = model(stem);
        let (raw, table) = (Raw::read(stem), Table::read(stem));
        let scales = scale_posterior(&raw);
        let mut indices = Vec::new();
        let mut errors = Vec::new();
        for name in m.names() {
            let modelled = raw.posterior(&name).pairs();
            let got: f64 = scales
                .iter()
                .map(|&(u, w)| {
                    w * agreement_index(m.date(&name), &modelled, None, Some(u), &cal())
                        .unwrap()
                        .unwrap()
                })
                .sum();
            indices.push(Some(got));
            errors.push(got - table.get(&name).agreement.unwrap());
        }
        assert!(max_abs(&errors) < 1.5, "{stem}: {errors:?}");
        assert!(rms(&errors) < 0.8, "{stem}: {errors:?}");
        let overall = overall_agreement(&indices, outlier_scale_terms(&m.dates)).unwrap();
        assert!((overall - table.aoverall).abs() <= 1.5, "{stem}: {overall}");
    }
}

// Amodel end to end, against the Amodel OxCal reports: the number an analyst
// screens a model on. Solved rather than fed in, since Amodel is a function of
// the JOINT posterior and the exports hold only the marginals, so this is
// looser than the formula tests by however much the posterior differs.

fn hillside_sampled(run: &str) -> &'static Sampled {
    static SAMPLED: OnceLock<std::sync::Mutex<HashMap<String, &'static Sampled>>> = OnceLock::new();
    let cache = SAMPLED.get_or_init(Default::default);
    if let Some(s) = cache.lock().unwrap().get(run) {
        return s;
    }
    let sampled: &'static Sampled =
        Box::leak(Box::new(sample(model(hillside(run)), &untempered(200))));
    cache
        .lock()
        .unwrap()
        .entry(run.to_owned())
        .or_insert(sampled)
}

fn amodel(m: &Model, sampled: &Sampled) -> f64 {
    model_agreement(
        &m.dates,
        sampled.joint_draws.as_ref().unwrap(),
        sampled.joint_scales.as_ref(),
        &cal(),
    )
    .unwrap()
    .unwrap()
}

/// Against OxCal at one year, 100.6: measured 100.9.
#[test]
fn a_run_reproduces_oxcals_amodel() {
    let got = amodel(model(hillside("ordered")), hillside_sampled("ordered"));
    let want = Table::read(HILLSIDE_ONE_YEAR).amodel;
    assert!((got - want).abs() <= 1.5, "{got} against {want}");
}

/// OxCal's default five-year runs read 4.6 lower than its one-year run on
/// `ordered`, and a run here reads about that far above each of the three
/// default runs: measured 4.9, 5.1 and 5.4.
#[test]
fn oxcals_default_resolution_reads_amodel_lower() {
    for (run, stem) in HILLSIDE {
        let gap = amodel(model(stem), hillside_sampled(run)) - Table::read(stem).amodel;
        assert!(3.0 < gap && gap < 7.0, "{run}: {gap}");
    }
}

/// The direction OxCal reports the two indices apart in: on the ordered model
/// the constraints correlate the dates and Amodel falls below Aoverall (96.0
/// against 100.4), while on the unordered one the two all but coincide (106.6
/// against 107.0).
#[test]
fn ordering_pulls_amodel_below_aoverall() {
    let ordered = Table::read(hillside("ordered"));
    let unordered = Table::read(hillside("unordered"));
    assert!(ordered.amodel < ordered.aoverall);
    assert!((unordered.amodel - unordered.aoverall).abs() < 1.0);

    let gap = |run: &str| {
        let m = model(hillside(run));
        let sampled = hillside_sampled(run);
        let indices: Vec<Option<f64>> = m
            .names()
            .iter()
            .map(|n| {
                Some(index(
                    m,
                    n,
                    &posterior(&sampled.measurements, m.id(n)).probability_distribution,
                    None,
                ))
            })
            .collect();
        overall_agreement(&indices, 0).unwrap() - amodel(m, sampled)
    };
    let (ordered_gap, unordered_gap) = (gap("ordered"), gap("unordered"));
    assert!(
        ordered_gap > unordered_gap,
        "{ordered_gap} against {unordered_gap}"
    );
    assert!(ordered_gap > 0.0, "{ordered_gap}");
}

/// End to end on Dune Section: LB10002 is scored off the joint draws under
/// the scale each state held, and the model's indices land on OxCal's
/// one-year run (Amodel 83.9, Aoverall 84.2). Integrating the shift out
/// instead gave LB10002 0.5 and Aoverall 48.9.
#[test]
fn a_run_scores_the_flagged_date_as_oxcal_does() {
    let m = model(DUNE);
    let mut sampled = sample(m, &untempered(200));
    let overall = attach(
        &mut sampled.measurements,
        &m.dates,
        sampled.joint_draws.as_ref(),
        sampled.joint_scales.as_ref(),
        &cal(),
    )
    .unwrap()
    .unwrap();
    let flagged = posterior(&sampled.measurements, m.id(DUNE_OUTLIER))
        .agreement_index
        .unwrap();
    let table = Table::read(DUNE_ONE_YEAR);
    assert!(3.5 < flagged && flagged < 7.5, "{flagged}");
    assert!(
        (overall - table.aoverall).abs() <= 4.0,
        "{overall} against {}",
        table.aoverall
    );
    let got = amodel(m, &sampled);
    assert!(
        (got - table.amodel).abs() <= 4.0,
        "{got} against {}",
        table.amodel
    );
}
