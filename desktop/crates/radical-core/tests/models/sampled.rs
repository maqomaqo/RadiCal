//! The sampler end to end: every fixture the API solved by sampling, run
//! through `gibbs::solve` with the settings the API ran it with (four chains,
//! three temperatures, the seed it recorded) and compared with what it
//! returned.
//!
//! The port draws NumPy's numbers in NumPy's order, so a run here IS the
//! Python run, draw for draw: the same sweeps, the same histograms, and every
//! summary to the exact solvers' tolerances. That holds for the charcoal
//! outlier model too now that the backend sums its one-sided smear directly
//! rather than by FFT, whose rounding in the far tail once sent an
//! inverse-CDF draw of the shared scale into the other cell.

use super::*;
use radical_core::cross_check;
use radical_core::gibbs::{SampleOptions, Sampled, solve};
use radical_core::measurement::Measurement;

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

/// One sampled case; None if it is not one.
fn check_sampled(case: &Case) -> Option<Closeness> {
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

    // The run itself first, exactly.
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

    for (id, got) in &sampled.measurements {
        let want = &results[id.as_str()];
        let label = format!("{name}/{id}");
        posterior(&mut c, &label, got, want);
        c.opt(
            &format!("{label}.agreement"),
            got.agreement_index,
            &want["agreement_index"],
            REL,
        );
        c.opt(
            &format!("{label}.outlier_probability"),
            got.outlier_probability,
            &want["outlier_probability"],
            REL,
        );
    }
    c.opt(
        &format!("{name}/model_agreement"),
        amodel,
        &solver["model_agreement"],
        REL,
    );
    c.opt(
        &format!("{name}/overall_agreement"),
        overall,
        &solver["overall_agreement"],
        REL,
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

    let check = cross_check::check(&dates, &root, &sampled, true, &cal);
    let want = &solver["cross_check"];
    assert_eq!(
        check.status,
        want["status"].as_str().unwrap(),
        "{name}: cross-check"
    );
    assert_eq!(
        check.reason.as_deref(),
        want["reason"].as_str(),
        "{name}: cross-check reason"
    );
    assert_eq!(
        check.shape,
        want["shape"].as_str(),
        "{name}: cross-check shape"
    );
    assert_eq!(
        check.checked as u64,
        want["checked"].as_u64().unwrap(),
        "{name}"
    );
    assert_eq!(check.threshold, num(&want["threshold"]), "{name}");
    assert_eq!(
        check.threshold_gap_limit,
        want["threshold_gap_limit"].as_f64(),
        "{name}"
    );
    let rows = |got: &[cross_check::Row], want: Vec<Value>, label: &str, c: &mut Closeness| {
        assert_eq!(got.len(), want.len(), "{label}");
        for (g, w) in got.iter().zip(&want) {
            assert_eq!(g.name, w["name"].as_str().unwrap(), "{label}");
            for (field, value) in [
                ("distance", g.distance),
                ("threshold_gap", g.threshold_gap),
                ("sampled_mean", g.sampled_mean),
                ("direct_mean", g.direct_mean),
            ] {
                c.check(
                    &format!("{label}/{}.{field}", g.name),
                    value,
                    num(&w[field]),
                    REL,
                );
            }
        }
    };
    let worst: Vec<Value> = if want["worst"].is_null() {
        Vec::new()
    } else {
        vec![want["worst"].clone()]
    };
    rows(
        check.worst.as_slice(),
        worst,
        &format!("{name}/cross_check.worst"),
        &mut c,
    );
    rows(
        &check.differing,
        want["differing"].as_array().unwrap().clone(),
        &format!("{name}/cross_check.differing"),
        &mut c,
    );

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
    Some(c)
}

/// Every case the API sampled is sampled here and reproduced, value for
/// value.
#[test]
fn every_sampled_case_matches_python() {
    let family = load_family("api/models").unwrap();
    let mut reproduced = 0;
    for case in &family.cases {
        let Some(c) = check_sampled(case) else {
            continue;
        };
        reproduced += 1;
        eprintln!(
            "{}: reproduced, {} values, worst relative difference {:e} {}",
            case.name, c.compared, c.worst, c.worst_at
        );
    }
    assert!(reproduced >= 13, "only {reproduced} runs reproduced");
}
