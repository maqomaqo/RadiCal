//! The sampler end to end: every fixture the API solved by sampling, run
//! through `gibbs::solve` with the settings the API ran it with (four chains,
//! three temperatures, the seed it recorded) and compared with what it
//! returned.
//!
//! The port draws NumPy's numbers in NumPy's order, so a run here IS the
//! Python run, draw for draw, wherever the arithmetic agrees to the bit: the
//! same sweeps, the same histograms, and every summary to the exact solvers'
//! tolerances. Where it does not -- see [`diverges`] -- an acceptance or an
//! inverse-CDF draw eventually comes out the other way, and from there the
//! two are independent runs of one sampler, held to each other statistically.

use super::*;
use radical_core::gibbs::{SampleOptions, Sampled, solve};
use radical_core::measurement::Measurement;

/// Whether a run is expected to part company with Python's.
///
/// The charcoal outlier model's kernel is one-sided, so a flagged date's
/// smeared likelihood is many orders of magnitude below its peak on one side
/// of it. Python forms that smear with SciPy's FFT convolution, whose
/// rounding is about 1e-16 of the PEAK in every cell; the port sums it
/// directly (exact_chain::convolve_on_grid). Out in the tail the two differ
/// relatively by far more than an ulp, the shared scale's conditional is a
/// product of those values over the flagged dates, and sooner or later an
/// inverse-CDF draw of the scale lands in the other cell. The general model's
/// kernel is symmetric, its tails share the peak's scale, and its runs
/// reproduce exactly.
fn diverges(dates: &[Measurement]) -> bool {
    dates
        .iter()
        .any(|m| m.outlier_prior() > 0.0 && m.outlier_model() == "charcoal")
}

/// How far an independent run may sit from Python's: a posterior's mean and
/// median within this fraction of its own standard deviation, its spread
/// within this fraction of itself. Monte Carlo error at the lengths these
/// runs reach is a few hundredths of a standard deviation.
const STAT_LOCATION: f64 = 0.15;
const STAT_SPREAD: f64 = 0.10;
/// An outlier probability or a convergence measure, absolutely.
const STAT_PROBABILITY: f64 = 0.05;
/// An agreement index or Amodel, relatively: each is read off the run's
/// correlated joint states.
const STAT_AGREEMENT: f64 = 0.10;

fn sample_like_the_api(request: &Value, dates: &[Measurement], root: &Element) -> Sampled {
    let config = &request["bayesian_config"];
    let options = SampleOptions {
        num_samples: config["mcmc_samples"].as_u64().unwrap_or(4000) as usize,
        seed: config["seed"].as_u64().unwrap_or(42),
        chains: config["mcmc_chains"].as_u64().unwrap_or(4) as usize,
        temperatures: config["mcmc_temperatures"].as_u64().unwrap_or(3) as usize,
        min_sweeps: config["mcmc_min_sweeps"].as_u64().unwrap_or(0) as usize,
        uniform_span_prior: config["uniform_span_prior"].as_bool().unwrap_or(true),
        estimate_durations: config["estimate_durations"].as_bool().unwrap_or(false),
        resolution: config["resolution"].as_f64().unwrap_or(5.0),
        match_oxcal: config["match_oxcal"].as_bool().unwrap_or(false),
        ..SampleOptions::default()
    };
    let cal = Calibrator::default();
    let mut sampled = solve(dates, root, &options, &cal, None).unwrap().unwrap();
    agreement::attach(
        &mut sampled.measurements,
        dates,
        sampled.joint_draws.as_ref(),
        sampled.joint_scales.as_ref(),
        &cal,
    )
    .unwrap();
    sampled
}

fn close(label: &str, got: f64, want: f64, allowed: f64) {
    assert!(
        (got - want).abs() <= allowed,
        "{label}: {got} against Python's {want} (allowed {allowed})"
    );
}

/// One sampled case; None if it is not one.
fn check_sampled(case: &Case) -> Option<(Closeness, bool)> {
    let outputs = case.expanded_outputs()?;
    let response = &outputs["response"]["data"];
    if response["solver"]["method"] != "gibbs" {
        return None;
    }
    let inputs = case.expanded_inputs();
    let request = &inputs["request"];
    let name = &case.name;
    let root = model::element(&request["model_tree"]);
    let dates = model::measurements(&request["measurements"]);
    let mut sampled = sample_like_the_api(request, &dates, &root);
    let cal = Calibrator::default();
    let amodel = agreement::model_agreement(
        &dates,
        sampled.joint_draws.as_ref().unwrap(),
        sampled.joint_scales.as_ref(),
        &cal,
    )
    .unwrap();
    let overall = agreement::attach(
        &mut sampled.measurements,
        &dates,
        sampled.joint_draws.as_ref(),
        sampled.joint_scales.as_ref(),
        &cal,
    )
    .unwrap();
    let solver = &response["solver"];
    let results = &response["results"];
    let mut c = Closeness::default();
    let exact = !diverges(&dates);

    if !exact {
        assert_eq!(
            sampled.converged,
            solver["converged"].as_bool().unwrap(),
            "{name}"
        );
        close(
            &format!("{name}/convergence"),
            sampled.convergence,
            num(&solver["convergence"]),
            STAT_PROBABILITY,
        );
        for (id, got) in &sampled.measurements {
            let want = &results[id.as_str()];
            let label = format!("{name}/{id}");
            let sd = num(&want["posterior_std"]);
            close(
                &format!("{label}.mean"),
                got.posterior_mean,
                num(&want["posterior_mean"]),
                STAT_LOCATION * sd,
            );
            close(
                &format!("{label}.median"),
                got.posterior_median,
                num(&want["posterior_median"]),
                STAT_LOCATION * sd,
            );
            close(
                &format!("{label}.std"),
                got.posterior_std,
                sd,
                STAT_SPREAD * sd,
            );
            if let Some(p) = got.outlier_probability {
                close(
                    &format!("{label}.outlier"),
                    p,
                    num(&want["outlier_probability"]),
                    STAT_PROBABILITY,
                );
            }
            let a = num(&want["agreement_index"]);
            close(
                &format!("{label}.agreement"),
                got.agreement_index.unwrap(),
                a,
                STAT_AGREEMENT * a,
            );
        }
        for (field, got) in [("model_agreement", amodel), ("overall_agreement", overall)] {
            let w = num(&solver[field]);
            close(
                &format!("{name}/{field}"),
                got.unwrap(),
                w,
                STAT_AGREEMENT * w,
            );
        }
        return Some((c, false));
    }

    // Reproduced: the run itself first, exactly.
    assert_eq!(
        sampled.sweeps as u64,
        solver["sweeps"].as_u64().unwrap(),
        "{name}: sweeps"
    );
    assert_eq!(
        sampled.chains as u64,
        solver["chains"].as_u64().unwrap(),
        "{name}"
    );
    let per_chain: Vec<u64> = sampled.sweeps_per_chain.iter().map(|&s| s as u64).collect();
    let want_per_chain: Vec<u64> = solver["sweeps_per_chain"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_u64().unwrap())
        .collect();
    assert_eq!(per_chain, want_per_chain, "{name}: sweeps per chain");
    assert_eq!(
        sampled.converged,
        solver["converged"].as_bool().unwrap(),
        "{name}"
    );
    for (field, got) in [
        ("convergence", sampled.convergence),
        ("within_chain_convergence", sampled.within_chain_convergence),
    ] {
        c.check(&format!("{name}/{field}"), got, num(&solver[field]), REL);
    }
    c.opt(
        &format!("{name}/between"),
        sampled.between_chain_convergence,
        &solver["between_chain_convergence"],
        REL,
    );
    c.opt(&format!("{name}/rhat"), sampled.rhat, &solver["rhat"], REL);
    for (k, (g, w)) in sampled
        .per_chain_convergence
        .iter()
        .zip(solver["per_chain_convergence"].as_array().unwrap())
        .enumerate()
    {
        c.check(&format!("{name}/per_chain[{k}]"), *g, num(w), REL);
    }

    let flagged_any = dates.iter().any(|m| m.outlier_prior() > 0.0);
    for (id, got) in &sampled.measurements {
        let want = &results[id.as_str()];
        let label = format!("{name}/{id}");
        posterior(&mut c, &label, got, want);
        let flagged = want["outlier_probability"].is_number();
        let rel = if flagged { REL_OUTLIER_AGREEMENT } else { REL };
        c.opt(
            &format!("{label}.agreement"),
            got.agreement_index,
            &want["agreement_index"],
            rel,
        );
        c.opt(
            &format!("{label}.outlier_probability"),
            got.outlier_probability,
            &want["outlier_probability"],
            REL_OUTLIER_AGREEMENT,
        );
    }
    let rel = if flagged_any {
        REL_OUTLIER_AGREEMENT
    } else {
        REL
    };
    c.opt(
        &format!("{name}/model_agreement"),
        amodel,
        &solver["model_agreement"],
        rel,
    );
    c.opt(
        &format!("{name}/overall_agreement"),
        overall,
        &solver["overall_agreement"],
        rel,
    );

    let want_scales = solver["outlier_scale"].as_object().unwrap();
    assert_eq!(
        sampled.outlier_scale.len(),
        want_scales.len(),
        "{name}: outlier scales"
    );
    for (model_name, s) in &sampled.outlier_scale {
        let w = &want_scales[model_name];
        c.check(
            &format!("{name}/scale/{model_name}.mean"),
            s.log10_mean,
            num(&w["log10_mean"]),
            REL,
        );
        let hpd = nums(&w["log10_hpd_68"]);
        c.check(
            &format!("{name}/scale/{model_name}.lo"),
            s.log10_hpd_68.0,
            hpd[0],
            REL,
        );
        c.check(
            &format!("{name}/scale/{model_name}.hi"),
            s.log10_hpd_68.1,
            hpd[1],
            REL,
        );
        c.check(
            &format!("{name}/scale/{model_name}.years"),
            s.years_mean,
            num(&w["years_mean"]),
            REL,
        );
    }

    let probe = sampled.lobe_probe.as_ref().unwrap();
    let w = &solver["lobe_probe"];
    assert_eq!(
        [probe.probed, probe.left, probe.untestable, probe.skipped].map(|n| n as u64),
        ["probed", "left", "untestable", "skipped"].map(|k| w[k].as_u64().unwrap()),
        "{name}: lobe probe"
    );
    let unreached = w["not_reached"].as_array().unwrap();
    assert_eq!(
        probe.not_reached.len(),
        unreached.len(),
        "{name}: unreached lobes"
    );
    for (g, w) in probe.not_reached.iter().zip(unreached) {
        assert_eq!(g.id, w["id"].as_str().unwrap(), "{name}");
        assert_eq!(
            (g.from_bp, g.to_bp),
            (num(&w["from_bp"]), num(&w["to_bp"])),
            "{name}"
        );
        c.check(
            &format!("{name}/probe/{}", g.id),
            g.occupancy,
            num(&w["occupancy"]),
            REL,
        );
    }

    let deposition = solver["deposition"].as_array().unwrap();
    assert_eq!(
        sampled.deposition.len(),
        deposition.len(),
        "{name}: deposition"
    );
    for (g, w) in sampled.deposition.iter().zip(deposition) {
        assert_eq!(g.element_id, w["element_id"].as_str().unwrap(), "{name}");
        assert_eq!(g.levels as u64, w["levels"].as_u64().unwrap(), "{name}");
        assert_eq!(g.variable, w["variable"].as_bool().unwrap(), "{name}");
        c.opt(&format!("{name}/k_mean"), g.k_mean, &w["k_mean"], REL);
        c.opt(
            &format!("{name}/log10_k_mean"),
            g.log10_k_mean,
            &w["log10_k_mean"],
            REL,
        );
        if let Some((lo, hi)) = g.log10_k_hpd_68 {
            let hpd = nums(&w["log10_k_hpd_68"]);
            c.check(&format!("{name}/k_hpd.lo"), lo, hpd[0], REL);
            c.check(&format!("{name}/k_hpd.hi"), hi, hpd[1], REL);
        }
        assert_eq!(g.pinned, w["pinned"].as_bool(), "{name}");
    }

    match (&sampled.intervals, &response["intervals"]) {
        (None, w) => assert!(w.is_null(), "{name}: intervals missing"),
        (Some(b), w) => intervals(&mut c, &format!("{name}/intervals"), b, w),
    }

    // A saved run's Boundaries and undated levels, from its stored tree.
    if let Some(analysis) = outputs.pointer("/analysis/data") {
        let mut stored: HashMap<String, Value> = HashMap::new();
        element_posteriors(&analysis["model_tree"], &mut stored);
        for (id, _name, got) in &sampled.boundaries {
            let want = stored
                .get(id)
                .unwrap_or_else(|| panic!("{name}: no stored posterior for {id}"));
            posterior(&mut c, &format!("{name}/boundary {id}"), got, want);
        }
    }
    Some((c, true))
}

/// Every case the API sampled is sampled here: the runs Python's arithmetic
/// allows to be reproduced are, value for value, and the rest agree as two
/// runs of one sampler should.
#[test]
fn every_sampled_case_matches_python() {
    let family = load_family("api/models").unwrap();
    let (mut reproduced, mut independent) = (0, 0);
    for case in &family.cases {
        let Some((c, exact)) = check_sampled(case) else {
            continue;
        };
        if exact {
            reproduced += 1;
            eprintln!(
                "{}: reproduced, {} values, worst relative difference {:e} {}",
                case.name, c.compared, c.worst, c.worst_at
            );
        } else {
            independent += 1;
            eprintln!(
                "{}: an independent run, within its statistical bounds",
                case.name
            );
        }
    }
    assert!(reproduced >= 11, "only {reproduced} runs reproduced");
    assert!(independent >= 2, "only {independent} independent runs");
}
