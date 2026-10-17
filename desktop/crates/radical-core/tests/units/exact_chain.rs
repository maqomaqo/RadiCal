use std::collections::{BTreeMap, HashMap};

use crate::model::{measurement, measurements, strings, tree};
use crate::solved::{check_peaked, posterior, solution};
use crate::*;
use radical_core::exact_chain::messages::{draw_from_rows, draw_in, sample_indices};
use radical_core::exact_chain::ordered::log_date_factor;
use radical_core::exact_chain::prior::{
    boundary_pair_term, log_interior_placements, log_limits_factor_of_span, span_prior_correction,
};
use radical_core::exact_chain::summary::{edge_mass, straddling, summarize};
use radical_core::exact_chain::tables::{
    build_grid, date_tables, dates_extent, flat_date_tables, point_budget, reporting_aligned_step,
};
use radical_core::exact_chain::{
    Chain, SolveOptions, convolve_on_grid, event_likelihood, inbuilt_kernel, inbuilt_reach,
    layout_with_reason, outlier_kernel, outlier_kernel_at_scale, scale_nodes, solve,
};
use radical_core::matrix::Matrix;
use radical_core::measurement::Calibrator;
use radical_core::model_tree::inherited_bounds;
use radical_core::rng::Generator;
use radical_core::special::gammaln;

fn rows(v: &Value) -> Vec<Vec<f64>> {
    v.as_array().unwrap().iter().map(nums).collect()
}

fn check_matrix(c: &mut Closeness, label: &str, got: &Matrix, want: &Value, rel: f64) {
    let want = rows(want);
    assert_eq!(got.rows(), want.len(), "{label}: rows");
    for (r, w) in want.iter().enumerate() {
        c.check_all(&format!("{label}[{r}]"), got.row(r), w, rel);
    }
}

fn blocks(v: &Value) -> Vec<Vec<String>> {
    v.as_array().unwrap().iter().map(strings).collect()
}

pub fn check_chain(label: &str, got: &Chain, want: &Value) {
    assert_eq!(got.boundary_ids, strings(&want["boundary_ids"]), "{label}");
    assert_eq!(got.groups, blocks(&want["groups"]), "{label}: groups");
    let names: Vec<(String, String)> = want["boundary_names"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| (p[0].as_str().unwrap().into(), p[1].as_str().unwrap().into()))
        .collect();
    let mut got_names = got.boundary_names.clone();
    got_names.sort();
    let mut names = names;
    names.sort();
    assert_eq!(got_names, names, "{label}: names");
    assert_eq!(got.older_tail, strings(&want["older_tail"]), "{label}");
    assert_eq!(got.younger_tail, strings(&want["younger_tail"]), "{label}");
    let want_blocks: Vec<Vec<Vec<String>>> = want["blocks"]
        .as_array()
        .unwrap()
        .iter()
        .map(blocks)
        .collect();
    assert_eq!(got.blocks, want_blocks, "{label}: blocks");
    assert_eq!(
        got.older_tail_blocks,
        blocks(&want["older_tail_blocks"]),
        "{label}"
    );
    assert_eq!(
        got.younger_tail_blocks,
        blocks(&want["younger_tail_blocks"]),
        "{label}"
    );
}

pub fn options(v: &Value) -> SolveOptions {
    SolveOptions {
        num_samples: v["num_samples"].as_u64().unwrap() as usize,
        interval_draws: v["interval_draws"].as_u64().unwrap() as usize,
        estimate_durations: v["estimate_durations"].as_bool().unwrap_or(false),
        uniform_span_prior: v["uniform_span_prior"].as_bool().unwrap_or(true),
        resolution: v["resolution"].as_f64().unwrap_or(5.0),
        ..SolveOptions::default()
    }
}

fn by_date(v: &Value) -> HashMap<String, Vec<f64>> {
    v.as_object()
        .unwrap()
        .iter()
        .map(|(k, v)| (k.clone(), nums(v)))
        .collect()
}

fn limits(v: &Value) -> Option<((f64, f64), (f64, f64))> {
    (!v.is_null()).then(|| {
        (
            (num(&v[0][0]), num(&v[0][1])),
            (num(&v[1][0]), num(&v[1][1])),
        )
    })
}

#[test]
fn exact_chain() {
    let mut c = Closeness::default();
    let cal = Calibrator::default();
    for case in family("exact_chain").cases {
        let name = case.name.clone();
        let i = case.expanded_inputs();
        let kind = name.split('/').next().unwrap();
        if let Some(error) = &case.error {
            let message = error["message"].as_str().unwrap();
            let got = match kind {
                "inbuilt_kernel" => {
                    inbuilt_kernel(num(&i["step"]), &measurement(&i["measurement"])).map(|_| ())
                }
                _ => panic!("{name}: an unexpected refusal"),
            };
            assert_eq!(got.unwrap_err().to_string(), message, "{name}");
            continue;
        }
        let want = outputs(&case);
        match kind {
            "gammaln" => {
                for (x, w) in nums(&i["x"]).into_iter().zip(nums(&want["gammaln"])) {
                    same(&format!("{name}({x})"), gammaln(x), w);
                }
            }
            "inbuilt_kernel" => {
                let m = measurement(&i["measurement"]);
                same(&name, inbuilt_reach(&m), num(&want["reach"]));
                let got = inbuilt_kernel(num(&i["step"]), &m).unwrap();
                match got {
                    None => assert!(want["kernel"].is_null(), "{name}"),
                    Some(k) => c.check_all(&name, &k, &nums(&want["kernel"]), REL),
                }
            }
            "event_likelihood" => {
                let got =
                    event_likelihood(&nums(&i["grid"]), &measurement(&i["measurement"]), &cal)
                        .unwrap();
                check_peaked(&mut c, &name, &got, &nums(&want["likelihood"]), REL);
            }
            "scale_nodes" => {
                assert_eq!(
                    scale_nodes(i["model"].as_str().unwrap()),
                    nums(&want["nodes"])
                );
            }
            "kernel_at_scale" => {
                let got = outlier_kernel_at_scale(
                    num(&i["step"]),
                    num(&i["half_width"]),
                    i["model"].as_str().unwrap(),
                    num(&i["log_scale"]),
                );
                check_peaked(&mut c, &name, &got, &nums(&want["kernel"]), REL);
            }
            "outlier_kernel" => {
                let got = outlier_kernel(
                    num(&i["step"]),
                    num(&i["half_width"]),
                    i["model"].as_str().unwrap(),
                );
                check_peaked(&mut c, &name, &got, &nums(&want["kernel"]), REL);
            }
            "convolve_on_grid" => {
                // SciPy convolves by FFT, whose rounding is about 1e-16 of the
                // peak in every cell, where the port sums directly.
                let got = convolve_on_grid(&nums(&i["values"]), &nums(&i["kernel"]));
                check_peaked(&mut c, &name, &got, &nums(&want["convolved"]), REL);
            }
            "reporting_aligned_step" => {
                same(
                    &name,
                    reporting_aligned_step(num(&i["step"])),
                    num(&want["step"]),
                );
            }
            "point_budget" => {
                let got: Vec<f64> = (1..25).map(point_budget).collect();
                assert_eq!(got, nums(&want["budget"]));
            }
            "log_interior_placements" => {
                let spans: Vec<f64> = (0..60).map(f64::from).collect();
                for (k, w) in [1, 2, 3, 5]
                    .into_iter()
                    .zip(want["placements"].as_array().unwrap())
                {
                    c.check_all(
                        &format!("{name}/{k}"),
                        &log_interior_placements(&spans, k),
                        &nums(w),
                        REL,
                    );
                }
            }
            "log_limits_factor_of_span" => {
                let spans = nums(&i["spans"]);
                for (l, w) in i["limits"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .zip(want["factor"].as_array().unwrap())
                {
                    let (o, y) = limits(l).unwrap();
                    match log_limits_factor_of_span(&spans, o, y) {
                        None => assert!(w.is_null(), "{name}: {l}"),
                        Some(f) => c.check_all(&format!("{name}/{l}"), &f, &nums(w), REL),
                    }
                }
            }
            "span_prior_correction" => {
                let got = span_prior_correction(
                    &nums(&i["grid"]),
                    i["boundaries"].as_u64().unwrap() as usize,
                    true,
                    limits(&i["limits"]),
                );
                match got {
                    None => assert!(want["correction"].is_null(), "{name}"),
                    Some(m) => check_matrix(&mut c, &name, &m, &want["correction"], REL),
                }
            }
            "boundary_pair_term" => {
                let grid = nums(&i["grid"]);
                for (k, w) in [0, 1, 3].into_iter().zip(want["term"].as_array().unwrap()) {
                    check_matrix(
                        &mut c,
                        &format!("{name}/{k}"),
                        &boundary_pair_term(&grid, k),
                        w,
                        REL,
                    );
                }
            }
            "date_tables" => {
                let root = tree(&i["tree"]).unwrap();
                let dates = measurements(&i["dates"]);
                let weights: Option<BTreeMap<String, Vec<f64>>> = i.get("scale_weights").map(|w| {
                    w.as_object()
                        .unwrap()
                        .iter()
                        .map(|(k, v)| (k.clone(), nums(v)))
                        .collect()
                });
                let got = date_tables(
                    &nums(&i["grid"]),
                    &dates,
                    &inherited_bounds(&root),
                    weights.as_ref(),
                    None,
                    &cal,
                )
                .unwrap();
                for (id, w) in by_date(&want["likelihood"]) {
                    check_peaked(
                        &mut c,
                        &format!("{name}/{id}.likelihood"),
                        &got.likelihood[&id],
                        &w,
                        REL,
                    );
                }
                for (id, w) in by_date(&want["cumulative"]) {
                    check_peaked(
                        &mut c,
                        &format!("{name}/{id}.cumulative"),
                        &got.cumulative[&id],
                        &w,
                        REL,
                    );
                }
                let parts = want["outlier_parts"].as_object().unwrap();
                assert_eq!(got.outlier_parts.len(), parts.len(), "{name}");
                for (id, w) in parts {
                    let (inlier, broad, prior) = &got.outlier_parts[id];
                    check_peaked(
                        &mut c,
                        &format!("{name}/{id}.inlier"),
                        inlier,
                        &nums(&w["inlier"]),
                        REL,
                    );
                    check_peaked(
                        &mut c,
                        &format!("{name}/{id}.broad"),
                        broad,
                        &nums(&w["broad"]),
                        REL,
                    );
                    same(&name, *prior, num(&w["prior"]));
                }
            }
            "flat_date_tables" => {
                let root = tree(&i["tree"]).unwrap();
                let (lik, cum) = flat_date_tables(
                    &nums(&i["grid"]),
                    &measurements(&i["dates"]),
                    &inherited_bounds(&root),
                    None,
                )
                .unwrap();
                for (id, w) in by_date(&want["likelihood"]) {
                    c.check_all(&format!("{name}/{id}"), &lik[&id], &w, REL);
                }
                for (id, w) in by_date(&want["cumulative"]) {
                    c.check_all(&format!("{name}/{id}"), &cum[&id], &w, REL);
                }
            }
            "grid" => {
                let root = tree(&i["tree"]).unwrap();
                let dates = measurements(&i["dates"]);
                let bounds = inherited_bounds(&root);
                let extent = dates_extent(&dates, &bounds, &cal).unwrap();
                assert_eq!(
                    [extent.0, extent.1, extent.2, extent.3].to_vec(),
                    nums(&want["extent"]),
                    "{name}"
                );
                for (groups, w) in want["grid"].as_object().unwrap() {
                    let grid = build_grid(&dates, &bounds, groups.parse().unwrap(), &cal).unwrap();
                    assert_eq!(
                        vec![grid[0], grid[1], grid[grid.len() - 1]],
                        nums(w),
                        "{name}/{groups}"
                    );
                }
            }
            "interval_integral" => {
                let got = log_date_factor(&nums(&i["cumulative"]), &nums(&i["likelihood"]), 2.5);
                check_matrix(&mut c, &name, &got, &want["log_date_factor"], REL);
            }
            "straddling" => {
                let got = straddling(&nums(&i["rows"]), &nums(&i["cols"]));
                c.check_all(&name, &got, &nums(&want["straddling"]), REL);
            }
            "summarize" => {
                let mut rng = Generator::new(i["seed"].as_u64().unwrap());
                let got = summarize(
                    &nums(&i["grid"]),
                    &nums(&i["density"]),
                    &mut rng,
                    i["samples"].as_u64().unwrap() as usize,
                    num(&i["step"]),
                )
                .unwrap();
                posterior(&mut c, &name, &got, &want, REL);
            }
            "edge_mass" => {
                let densities: Vec<Vec<f64>> = rows(&i["densities"]);
                let got = edge_mass(&densities, nums(&i["grid"]).len());
                c.check(&name, got, num(&want["edge_mass"]), REL);
            }
            "sample_indices" => {
                let mut rng = Generator::new(i["seed"].as_u64().unwrap());
                let got = sample_indices(&mut rng, &nums(&i["weights"]), 60).unwrap();
                let want: Vec<usize> = nums(&want["indices"]).iter().map(|&v| v as usize).collect();
                assert_eq!(got, want, "{name}");
            }
            "draw_from_rows" => {
                let table = rows(&i["rows"]);
                let keys: Vec<usize> = nums(&i["keys"]).iter().map(|&v| v as usize).collect();
                let mut rng = Generator::new(i["seed"].as_u64().unwrap());
                let got = draw_from_rows(&mut rng, |k| table[k].clone(), &keys).unwrap();
                let want: Vec<usize> = nums(&want["picked"]).iter().map(|&v| v as usize).collect();
                assert_eq!(got, want, "{name}");
            }
            "draw_in" => {
                let lik = nums(&i["likelihood"]);
                let cum = nums(&i["cumulative"]);
                let low: Vec<usize> = nums(&i["low"]).iter().map(|&v| v as usize).collect();
                let high: Vec<usize> = nums(&i["high"]).iter().map(|&v| v as usize).collect();
                for (half, field) in [(true, "half"), (false, "whole")] {
                    let mut rng = Generator::new(i["seed"].as_u64().unwrap());
                    let got = draw_in(&mut rng, &cum, &lik, &low, &high, num(&i["step"]), half);
                    let want: Vec<usize> = nums(&want[field]).iter().map(|&v| v as usize).collect();
                    assert_eq!(got, want, "{name}/{field}");
                }
            }
            "layout" => {
                let root = tree(&i["tree"]);
                let dates = measurements(&i["dates"]);
                let got = layout_with_reason(root.as_ref(), &dates, false, true);
                match (&got, want["chain"].is_null()) {
                    (Ok(chain), false) => check_chain(&name, chain, &want["chain"]),
                    (Err(reason), true) => {
                        assert_eq!(reason, want["reason"].as_str().unwrap(), "{name}")
                    }
                    _ => panic!("{name}: {got:?} against {}", want["reason"]),
                }
                if want.get("strict").is_some() {
                    let strict = layout_with_reason(root.as_ref(), &dates, false, false);
                    match (&strict, want["strict"].is_null()) {
                        (Ok(chain), false) => check_chain(&name, chain, &want["strict"]),
                        (Err(reason), true) => {
                            assert_eq!(reason, want["strict_reason"].as_str().unwrap(), "{name}")
                        }
                        _ => panic!(
                            "{name}: strict {strict:?} against {}",
                            want["strict_reason"]
                        ),
                    }
                }
            }
            "solve" => {
                let root = tree(&i["tree"]).unwrap();
                let dates = measurements(&i["dates"]);
                let chain = layout_with_reason(Some(&root), &dates, false, true).unwrap();
                let got = solve(&dates, &root, &chain, &options(&i["options"]), &cal).unwrap();
                solution(&mut c, &name, &got, &want);
            }
            other => panic!("unhandled case {other}"),
        }
    }
    c.report("exact_chain");
}
