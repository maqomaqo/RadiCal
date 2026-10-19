//! The exact solvers end to end, held to the api/models corpus: every .oxcal
//! fixture the API solved without sampling -- a chain of Boundaries, a chain
//! whose stages hold areas side by side, or a wiggle-matched series -- run
//! through the same pipeline `api/analysis.py`'s `_solve_bayesian` runs, and
//! compared field by field with what the API stored and returned.
//!
//! The sampled cases are Phase 3's.

#[path = "../units/model.rs"]
#[allow(dead_code)]
mod model;
mod sampled;

use std::collections::HashMap;

use radical_core::agreement;
use radical_core::calibration::HpdRange;
use radical_core::exact_chain::{self, Evidence, Posterior, SolveOptions};
use radical_core::exact_parallel;
use radical_core::intervals::Block;
use radical_core::measurement::{Calibrator, Measurement};
use radical_core::model_tree::{
    Element, elements_of_type, plan_model, standalone_wiggle_match, validate_ordinates,
};
use radical_core::wiggle_match;
use radical_corpus::{Case, as_f64, as_f64s, load_family};
use serde_json::Value;

pub fn num(v: &Value) -> f64 {
    as_f64(v).unwrap_or_else(|| panic!("not a corpus number: {v}"))
}

fn nums(v: &Value) -> Vec<f64> {
    as_f64s(v).unwrap_or_else(|| panic!("not an array of corpus numbers: {v}"))
}

/// The tolerance a modelled value is held to, relative to the value: a
/// posterior's mean, median and spread, an HPD range's share, an agreement
/// index, an outlier probability, a duration's statistics.
///
/// Why not the units' 1e-12: everything here passes through the message
/// passing, whose matrix products BLAS sums in an order of its own (see
/// `radical_core::matrix`). On the platform the corpus was made on the worst
/// seen is 7e-13 (an agreement index on a marine fixture); this leaves two
/// orders of magnitude for a C runtime whose `exp` and `log` round
/// differently, which moves a likelihood by up to 4e-13 (the units' note on
/// REL_LIKELIHOOD).
const REL: f64 = 1e-10;

/// A date under an outlier model is scored against a table of its likelihood
/// smeared by the offset kernel. Python forms that smear with SciPy's FFT
/// convolution, whose rounding is about 1e-16 of the table's peak in every
/// cell; the port sums the convolution directly. Where a posterior point sits
/// in the smear's tail, a value 10^-8 of the peak, the FFT's rounding is 10^-8
/// of the value, and the index carries it: 1.8e-9 on the charcoal fixture,
/// 5e-11 on the outlier one.
const REL_OUTLIER_AGREEMENT: f64 = 1e-8;

/// A tabulated density is compared value by value relative to its own peak:
/// out in a tail, a value far below the peak is a sum of terms far larger than
/// itself, so relative to its own size the rounding of those sums is
/// unbounded. The same reasoning as the units' kernel sums.
const REL_SERIES: f64 = 1e-10;

#[derive(Default)]
struct Closeness {
    worst: f64,
    worst_at: String,
    compared: usize,
}

impl Closeness {
    fn check(&mut self, label: &str, got: f64, want: f64, rel: f64) {
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

    fn opt(&mut self, label: &str, got: Option<f64>, want: &Value, rel: f64) {
        match got {
            None => assert!(want.is_null(), "{label}: None, Python had {want}"),
            Some(g) => self.check(label, g, num(want), rel),
        }
    }

    fn series(&mut self, label: &str, got: &[[f64; 2]], want: &Value) {
        let want = want
            .as_array()
            .unwrap_or_else(|| panic!("{label}: not a series"));
        assert_eq!(got.len(), want.len(), "{label}: a different trimmed grid");
        let peak = want.iter().map(|p| num(&p[1]).abs()).fold(0.0, f64::max);
        for (k, (g, w)) in got.iter().zip(want).enumerate() {
            let x = num(&w[0]);
            assert!(
                g[0] == x || ((g[0] - x) / x).abs() <= 1e-15,
                "{label}[{k}]: x {} against {x}",
                g[0]
            );
            self.compared += 1;
            let err = (g[1] - num(&w[1])).abs() / peak;
            if err > self.worst {
                self.worst = err;
                self.worst_at = format!("{label}[{k}]");
            }
            assert!(
                err <= REL_SERIES,
                "{label}[{k}]: {:e} against {:e}, {err:e} of the peak",
                g[1],
                num(&w[1])
            );
        }
    }
}

/// HPD ranges: the same reporting bins exactly, the shares to [`REL`].
fn ranges(c: &mut Closeness, label: &str, got: &[HpdRange], want: &Value) {
    let want = want.as_array().unwrap();
    assert_eq!(
        got.len(),
        want.len(),
        "{label}: a different number of ranges"
    );
    for (k, (g, w)) in got.iter().zip(want).enumerate() {
        assert_eq!(g.min, num(&w["min"]), "{label}[{k}].min");
        assert_eq!(g.max, num(&w["max"]), "{label}[{k}].max");
        c.check(
            &format!("{label}[{k}].probability_percent"),
            g.probability_percent,
            num(&w["probability_percent"]),
            REL,
        );
    }
}

fn posterior(c: &mut Closeness, label: &str, got: &Posterior, want: &Value) {
    c.check(
        &format!("{label}.mean"),
        got.posterior_mean,
        num(&want["posterior_mean"]),
        REL,
    );
    c.check(
        &format!("{label}.median"),
        got.posterior_median,
        num(&want["posterior_median"]),
        REL,
    );
    c.check(
        &format!("{label}.std"),
        got.posterior_std,
        num(&want["posterior_std"]),
        REL,
    );
    for (field, g) in [("hpd_68", got.hpd_68), ("hpd_95", got.hpd_95)] {
        let w = nums(&want[field]);
        assert_eq!([g.0, g.1], [w[0], w[1]], "{label}.{field}");
    }
    ranges(
        c,
        &format!("{label}.hpd_68_ranges"),
        &got.hpd_68_ranges,
        &want["hpd_68_ranges"],
    );
    ranges(
        c,
        &format!("{label}.hpd_95_ranges"),
        &got.hpd_95_ranges,
        &want["hpd_95_ranges"],
    );
    c.series(
        &format!("{label}.probability_distribution"),
        &got.probability_distribution,
        &want["probability_distribution"],
    );
}

fn years(
    c: &mut Closeness,
    label: &str,
    got: &radical_core::intervals::YearsSummary,
    want: &Value,
) {
    c.check(&format!("{label}.mean"), got.mean, num(&want["mean"]), REL);
    c.check(
        &format!("{label}.median"),
        got.median,
        num(&want["median"]),
        REL,
    );
    c.check(&format!("{label}.std"), got.std, num(&want["std"]), REL);
    assert_eq!(got.step, num(&want["step"]), "{label}.step");
    for (field, g) in [("hpd_68", got.hpd_68), ("hpd_95", got.hpd_95)] {
        match g {
            None => assert!(want[field].is_null(), "{label}.{field}"),
            Some(g) => assert_eq!(g.to_vec(), nums(&want[field]), "{label}.{field}"),
        }
    }
    ranges(
        c,
        &format!("{label}.hpd_68_ranges"),
        &got.hpd_68_ranges,
        &want["hpd_68_ranges"],
    );
    ranges(
        c,
        &format!("{label}.hpd_95_ranges"),
        &got.hpd_95_ranges,
        &want["hpd_95_ranges"],
    );
    c.series(
        &format!("{label}.distribution"),
        &got.distribution,
        &want["distribution"],
    );
}

fn intervals(c: &mut Closeness, label: &str, got: &Block, want: &Value) {
    let durations = want["durations"].as_array().unwrap();
    assert_eq!(got.durations.len(), durations.len(), "{label}: durations");
    for (g, w) in got.durations.iter().zip(durations) {
        assert_eq!(g.kind.as_str(), w["kind"], "{label}");
        assert_eq!(g.id, w["id"], "{label}");
        assert_eq!(g.name, w["name"], "{label}");
        assert_eq!(g.element_id.as_deref(), w["element_id"].as_str(), "{label}");
        assert_eq!(g.dates.map(|d| d as u64), w["dates"].as_u64(), "{label}");
        years(c, &format!("{label}/{}", g.id), &g.years, &w["years"]);
    }
    let differences = want["differences"].as_array().unwrap();
    assert_eq!(
        got.differences.len(),
        differences.len(),
        "{label}: differences"
    );
    for (g, w) in got.differences.iter().zip(differences) {
        let l = format!("{label}/{}", g.id);
        assert_eq!(g.id, w["id"], "{l}");
        assert_eq!(g.from_name, w["from_name"], "{l}");
        assert_eq!(g.to_name, w["to_name"], "{l}");
        c.check(&format!("{l}.overlap"), g.overlap, num(&w["overlap"]), REL);
        assert_eq!(g.severity, w["severity"], "{l}");
        assert_eq!(
            g.from_dates.map(|d| d as u64),
            w.get("from_dates").and_then(Value::as_u64),
            "{l}"
        );
        assert_eq!(
            g.to_dates.map(|d| d as u64),
            w.get("to_dates").and_then(Value::as_u64)
        );
        years(c, &l, &g.years, &w["years"]);
    }
    let age_depth = want["age_depth"].as_array().unwrap();
    assert_eq!(got.age_depth.len(), age_depth.len(), "{label}: age-depth");
    for (g, w) in got.age_depth.iter().zip(age_depth) {
        let l = format!("{label}/age_depth/{}", g.element_id);
        assert_eq!(g.element_id, w["element_id"], "{l}");
        assert_eq!(g.name, w["name"], "{l}");
        assert_eq!(g.depth_units, w["depth_units"], "{l}");
        let levels = w["levels"].as_array().unwrap();
        assert_eq!(g.levels.len(), levels.len(), "{l}: levels");
        for (gl, wl) in g.levels.iter().zip(levels) {
            let ll = format!("{l}/{}", gl.id);
            assert_eq!(gl.id, wl["id"], "{ll}");
            assert_eq!(gl.name, wl["name"], "{ll}");
            assert_eq!(
                (gl.depth, gl.dated),
                (num(&wl["depth"]), wl["dated"].as_bool().unwrap()),
                "{ll}"
            );
            c.check(
                &format!("{ll}.median"),
                gl.band.median,
                num(&wl["median"]),
                REL,
            );
            c.check(&format!("{ll}.mean"), gl.band.mean, num(&wl["mean"]), REL);
            assert_eq!(
                gl.band.band_68.to_vec(),
                nums(&wl["band_68"]),
                "{ll}.band_68"
            );
            assert_eq!(
                gl.band.band_95.to_vec(),
                nums(&wl["band_95"]),
                "{ll}.band_95"
            );
        }
        let accumulation = w["accumulation"].as_array().unwrap();
        assert_eq!(
            g.accumulation.len(),
            accumulation.len(),
            "{l}: accumulation"
        );
        for (ga, wa) in g.accumulation.iter().zip(accumulation) {
            let la = format!("{l}/{}-{}", ga.from_name, ga.to_name);
            assert_eq!(
                (ga.from_name.as_str(), ga.to_name.as_str()),
                (
                    wa["from_name"].as_str().unwrap(),
                    wa["to_name"].as_str().unwrap()
                ),
                "{la}"
            );
            assert_eq!(
                [ga.from_depth, ga.to_depth, ga.depth_gap],
                [
                    num(&wa["from_depth"]),
                    num(&wa["to_depth"]),
                    num(&wa["depth_gap"])
                ],
                "{la}"
            );
            years(c, &la, &ga.years, &wa["years"]);
            c.check(
                &format!("{la}.rate_median"),
                ga.rate_median,
                num(&wa["rate_median"]),
                REL,
            );
            let band = nums(&wa["rate_band_68"]);
            c.check(
                &format!("{la}.rate_68.lo"),
                ga.rate_band_68[0],
                band[0],
                REL,
            );
            c.check(
                &format!("{la}.rate_68.hi"),
                ga.rate_band_68[1],
                band[1],
                REL,
            );
        }
    }
    match (&got.phase_activity, want["phase_activity"].is_null()) {
        (None, true) => {}
        (Some(a), false) => {
            let w = &want["phase_activity"];
            assert_eq!(a.start, num(&w["start"]), "{label}: activity start");
            assert_eq!(a.step, num(&w["step"]), "{label}: activity step");
            let rows = w["rows"].as_array().unwrap();
            assert_eq!(a.rows.len(), rows.len(), "{label}: activity rows");
            for (g, w) in a.rows.iter().zip(rows) {
                assert_eq!(g.id, w["id"], "{label}");
                assert_eq!(g.relative, w.get("relative").is_some(), "{label}");
                let values = nums(&w["values"]);
                assert_eq!(g.values.len(), values.len(), "{label}");
                for (a, b) in g.values.iter().zip(&values) {
                    // Three decimals, so a value on a rounding edge may land
                    // either side of it.
                    assert!(
                        (a - b).abs() <= 1.0000001e-3,
                        "{label}/{}: {a} against {b}",
                        g.id
                    );
                }
            }
        }
        (got, _) => panic!(
            "{label}: phase activity {got:?} against {}",
            want["phase_activity"]
        ),
    }
    assert_eq!(got.draws as u64, want["draws"].as_u64().unwrap(), "{label}");
    assert_eq!(
        got.independent,
        want["independent"].as_bool().unwrap(),
        "{label}"
    );
    assert_eq!(got.note.as_deref(), want["note"].as_str(), "{label}");
}

/// What the exact path of `_solve_bayesian` produced for one request.
#[allow(clippy::large_enum_variant)]
enum Solved {
    Exact {
        measurements: Vec<(String, Posterior)>,
        boundaries: Vec<(String, Posterior)>,
        shape: &'static str,
        model_agreement: Option<f64>,
        overall_agreement: Option<f64>,
        outlier_scale: Option<Vec<(String, exact_chain::OutlierScale)>>,
        evidence: Option<Evidence>,
        intervals: Option<Block>,
    },
    Wiggle(Box<wiggle_match::WiggleSolution>, String),
}

/// `api/analysis.py`'s `_solve_bayesian`, the paths that do not sample.
fn solve_like_the_api(request: &Value) -> Result<Option<Solved>, String> {
    let config = &request["bayesian_config"];
    let requested = config["solver"].as_str().unwrap_or("auto");
    let resolution = config["resolution"].as_f64().unwrap_or(5.0);
    let estimate_durations = config["estimate_durations"].as_bool().unwrap_or(false);
    let root: Element = model::element(&request["model_tree"]);
    let dates: Vec<Measurement> = model::measurements(&request["measurements"]);
    let cal = Calibrator::default();
    validate_ordinates(Some(&root), &dates).map_err(|e| e.to_string())?;

    if !elements_of_type(Some(&root), "wiggle_sequence").is_empty() {
        let element = standalone_wiggle_match(Some(&root), &dates)
            .ok_or("a wiggle match inside a larger structure")?;
        if requested == "gibbs" {
            return Err("the sampler was asked for a wiggle match".into());
        }
        let plan = plan_model(&root, &dates);
        let solved = wiggle_match::solve(
            &dates,
            &plan.wiggle_groups[0],
            Some(&root),
            4000,
            42,
            resolution,
            &cal,
        )
        .map_err(|e| e.to_string())?;
        return Ok(Some(Solved::Wiggle(Box::new(solved), element.id.clone())));
    }

    let options = SolveOptions {
        num_samples: config["mcmc_samples"].as_u64().unwrap_or(4000) as usize,
        uniform_span_prior: config["uniform_span_prior"].as_bool().unwrap_or(true),
        estimate_durations,
        resolution,
        ..SolveOptions::default()
    };
    let (mut solved, shape) =
        match exact_chain::layout_with_reason(Some(&root), &dates, false, true) {
            Ok(chain) => (
                exact_chain::solve(&dates, &root, &chain, &options, &cal),
                "chain",
            ),
            Err(_) => match exact_parallel::layout_with_reason(Some(&root), &dates) {
                Ok(Some(stages)) => (
                    exact_parallel::solve(&dates, &root, &stages, &options, &cal),
                    "parallel",
                ),
                _ => return Ok(None),
            },
        };
    let solved = solved.as_mut().map_err(|e| e.to_string())?;
    let overall = agreement::attach(
        &mut solved.measurements,
        &dates,
        Some(&solved.joint_draws),
        solved.joint_scales.as_ref(),
        &cal,
    )
    .map_err(|e| e.to_string())?;
    let model_agreement = agreement::model_agreement(
        &dates,
        &solved.joint_draws,
        solved.joint_scales.as_ref(),
        &cal,
    )
    .map_err(|e| e.to_string())?;
    Ok(Some(Solved::Exact {
        measurements: solved.measurements.clone(),
        boundaries: solved
            .boundaries
            .iter()
            .map(|(b, r)| (b.clone(), r.posterior.clone()))
            .collect(),
        shape,
        model_agreement,
        overall_agreement: overall,
        outlier_scale: solved.outlier_scale.clone(),
        evidence: solved.evidence.clone(),
        intervals: solved.intervals.clone(),
    }))
}

/// Every element's stored posterior, by element id.
fn element_posteriors(tree: &Value, out: &mut HashMap<String, Value>) {
    if !tree["posterior"].is_null() {
        out.insert(
            tree["id"].as_str().unwrap().to_owned(),
            tree["posterior"].clone(),
        );
    }
    for child in tree["children"].as_array().into_iter().flatten() {
        element_posteriors(child, out);
    }
}

fn check_case(case: &Case) -> Option<Closeness> {
    let outputs = case.expanded_outputs()?;
    let response = &outputs["response"]["data"];
    let method = response["solver"]["method"].as_str()?;
    if method != "exact" && method != "wiggle_match" {
        return None;
    }
    let inputs = case.expanded_inputs();
    let request = &inputs["request"];
    let name = &case.name;
    let solved = solve_like_the_api(request)
        .unwrap_or_else(|e| panic!("{name}: refused: {e}"))
        .unwrap_or_else(|| panic!("{name}: the exact solvers declined it"));

    let analysis = &outputs["analysis"]["data"];
    let mut stored: HashMap<String, Value> = HashMap::new();
    element_posteriors(&analysis["model_tree"], &mut stored);
    // Each date's stored result, by the id the request gave it.
    let results = &response["results"];
    let requested: Vec<(&str, &str)> = request["measurements"]
        .as_array()
        .unwrap()
        .iter()
        .map(|m| {
            (
                m["id"].as_str().unwrap(),
                m["sample_name"].as_str().unwrap(),
            )
        })
        .collect();
    let solver = &response["solver"];
    let mut c = Closeness::default();
    match solved {
        Solved::Exact {
            measurements,
            boundaries,
            shape,
            model_agreement,
            overall_agreement,
            outlier_scale,
            evidence,
            intervals: block,
        } => {
            assert_eq!(solver["shape"], shape, "{name}");
            for (id, sample) in &requested {
                let want = &results[*id];
                let got = &measurements
                    .iter()
                    .find(|(d, _)| d == id)
                    .unwrap_or_else(|| panic!("{name}: no result for {id}"))
                    .1;
                let label = format!("{name}/{sample}");
                posterior(&mut c, &label, got, want);
                let flagged = want["outlier_probability"].is_number();
                c.opt(
                    &format!("{label}.agreement"),
                    got.agreement_index,
                    &want["agreement_index"],
                    if flagged { REL_OUTLIER_AGREEMENT } else { REL },
                );
                c.opt(
                    &format!("{label}.outlier_probability"),
                    got.outlier_probability,
                    &want["outlier_probability"],
                    REL,
                );
            }
            for (bid, got) in &boundaries {
                let want = stored
                    .get(bid)
                    .unwrap_or_else(|| panic!("{name}: no stored posterior for {bid}"));
                posterior(&mut c, &format!("{name}/boundary {bid}"), got, want);
            }
            // Both combine the flagged dates' indices, where there are any.
            let rel = if outlier_scale.is_some() {
                REL_OUTLIER_AGREEMENT
            } else {
                REL
            };
            c.opt(
                &format!("{name}/model_agreement"),
                model_agreement,
                &solver["model_agreement"],
                rel,
            );
            c.opt(
                &format!("{name}/overall_agreement"),
                overall_agreement,
                &solver["overall_agreement"],
                rel,
            );
            match (&outlier_scale, solver.get("outlier_scale")) {
                (None, None) => {}
                (Some(scales), Some(want)) => {
                    for (model, s) in scales {
                        let w = &want[model];
                        c.check(
                            &format!("{name}/scale/{model}.mean"),
                            s.log10_mean,
                            num(&w["log10_mean"]),
                            REL,
                        );
                        let hpd = nums(&w["log10_hpd_68"]);
                        c.check(
                            &format!("{name}/scale/{model}.lo"),
                            s.log10_hpd_68.0,
                            hpd[0],
                            REL,
                        );
                        c.check(
                            &format!("{name}/scale/{model}.hi"),
                            s.log10_hpd_68.1,
                            hpd[1],
                            REL,
                        );
                        c.check(
                            &format!("{name}/scale/{model}.years"),
                            s.years_mean,
                            num(&w["years_mean"]),
                            REL,
                        );
                    }
                }
                (got, want) => panic!("{name}: outlier scale {got:?} against {want:?}"),
            }
            match (evidence, &solver["evidence"]) {
                (Some(Evidence::Unavailable(reason)), w) => {
                    assert_eq!(w["available"], false, "{name}");
                    assert_eq!(reason, w["reason"].as_str().unwrap(), "{name}");
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
                    assert_eq!(w["available"], true, "{name}");
                    c.check(&format!("{name}/evidence.log"), log, num(&w["log"]), REL);
                    c.check(
                        &format!("{name}/evidence.log10"),
                        log10,
                        num(&w["log10"]),
                        REL,
                    );
                    assert_eq!(
                        (lower, upper, step),
                        (num(&w["lower"]), num(&w["upper"]), num(&w["step"]))
                    );
                    assert_eq!(approximate, w["approximate"].as_bool().unwrap());
                    assert_eq!(readings, w["readings"].as_str().unwrap());
                }
                (None, w) => assert!(w.is_null(), "{name}: evidence missing"),
            }
            match (&block, &response["intervals"]) {
                (None, w) => assert!(w.is_null(), "{name}: intervals missing"),
                (Some(b), w) => intervals(&mut c, &format!("{name}/intervals"), b, w),
            }
        }
        Solved::Wiggle(solved, element_id) => {
            for (id, sample) in &requested {
                let want = &results[*id];
                let got = &solved
                    .measurements
                    .iter()
                    .find(|(d, _)| d == id)
                    .unwrap_or_else(|| panic!("{name}: no result for {id}"))
                    .1;
                let label = format!("{name}/{sample}");
                posterior(&mut c, &label, got, want);
                c.opt(
                    &format!("{label}.agreement"),
                    got.agreement_index,
                    &want["agreement_index"],
                    REL,
                );
            }
            posterior(
                &mut c,
                &format!("{name}/reference"),
                &solved.reference,
                &stored[&element_id],
            );
            c.opt(
                &format!("{name}/combination_agreement"),
                solved.combination_agreement,
                &solver["combination_agreement"],
                REL,
            );
            assert_eq!(
                solved.combination_threshold,
                num(&solver["combination_threshold"])
            );
            assert_eq!(solved.series_span, num(&solver["series_span"]));
            assert_eq!(solved.gap_units, solver["gap_units"].as_str().unwrap());
            assert_eq!(
                solved.reference_sample_id.as_deref(),
                solver["reference_sample_id"].as_str()
            );
            let grid = &solver["grid"];
            assert_eq!(
                solved.grid,
                (num(&grid["lower"]), num(&grid["upper"]), num(&grid["step"]))
            );
            let members = solver["members"].as_array().unwrap();
            assert_eq!(solved.members.len(), members.len());
            for (g, w) in solved.members.iter().zip(members) {
                assert_eq!(g.id, w["id"].as_str().unwrap());
                assert_eq!(g.name, w["name"].as_str().unwrap());
                assert_eq!(g.ring_offset, num(&w["ring_offset"]));
                assert_eq!(g.ring_offset_error, w["ring_offset_error"].as_f64());
                c.opt(
                    &format!("{name}/member {}", g.name),
                    g.agreement_index,
                    &w["agreement_index"],
                    REL,
                );
            }
            assert!(
                response["intervals"].is_null(),
                "{name}: a wiggle match has no durations"
            );
        }
    }
    Some(c)
}

/// Every case the API solved exactly is solved here and matches; and every
/// case it sampled is one the exact solvers decline too.
#[test]
fn every_exact_case_matches_python() {
    let family = load_family("api/models").unwrap();
    let mut solved = 0;
    for case in &family.cases {
        if let Some(c) = check_case(case) {
            eprintln!(
                "{}: {} values, worst relative difference {:e}{}",
                case.name,
                c.compared,
                c.worst,
                if c.worst > 0.0 {
                    format!(" at {}", c.worst_at)
                } else {
                    String::new()
                }
            );
            solved += 1;
        }
    }
    assert!(solved >= 13, "only {solved} exact cases were found");
}

/// The cases the API sent to the sampler: the exact solvers decline each of
/// them too, so the choice of solver is the API's.
#[test]
fn sampled_cases_are_declined() {
    let family = load_family("api/models").unwrap();
    for case in family.cases.iter().filter(|c| c.name.ends_with("/auto")) {
        let Some(outputs) = case.expanded_outputs() else {
            continue;
        };
        if outputs["response"]["data"]["solver"]["method"] != "gibbs" {
            continue;
        }
        let inputs = case.expanded_inputs();
        let request = &inputs["request"];
        let root = model::element(&request["model_tree"]);
        let dates = model::measurements(&request["measurements"]);
        let chain = exact_chain::layout_with_reason(Some(&root), &dates, false, true);
        let stages = exact_parallel::layout_with_reason(Some(&root), &dates);
        assert!(chain.is_err(), "{}: the chain solver took it", case.name);
        assert!(
            !matches!(stages, Ok(Some(_))),
            "{}: the parallel solver took it",
            case.name
        );
        assert_eq!(
            chain.unwrap_err(),
            outputs["response"]["data"]["solver"]["reason"]
                .as_str()
                .unwrap(),
            "{}",
            case.name
        );
    }
}
