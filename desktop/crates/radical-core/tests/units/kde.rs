use crate::*;
use radical_core::Mode;
use radical_core::kde::{
    Spread, Summary, draw_g, draws_from_distribution, g_grid, kde_density, log_likelihood,
    output_grid, silverman_bandwidth, spread, summarise, summarise_draws,
};
use radical_core::numeric::arange;
use radical_core::rng::Generator;

/// The tolerance a kernel sum is held to, relative to the largest value in
/// its series rather than to each value.
///
/// Why: every kernel sum is a convolution, and NumPy hands each output's
/// products to BLAS's `ddot`, which adds them in an order of the library's
/// choosing (SIMD lanes, then the lanes together), where the port adds them
/// left to right. The two agree to a few ulps of the largest terms, not of
/// each output -- a value out in a kernel's tail is a sum of terms far larger
/// than itself, so relative to its own size the difference is unbounded.
const REL_KERNEL: f64 = 1e-12;

fn check_series(c: &mut Closeness, label: &str, got: &[f64], want: &[f64]) {
    assert_eq!(got.len(), want.len(), "{label}: lengths differ");
    let scale = want.iter().fold(0.0_f64, |m, v| m.max(v.abs()));
    for (k, (&g, &w)) in got.iter().zip(want).enumerate() {
        c.compared += 1;
        let err = (g - w).abs() / scale;
        if err > c.worst {
            c.worst = err;
            c.worst_at = format!("{label}[{k}]");
        }
        assert!(
            err <= REL_KERNEL,
            "{label}[{k}]: {g:e} differs from {w:e} by {err:e} of the series' largest"
        );
    }
}

fn rows(v: &Value) -> Vec<Vec<f64>> {
    v.as_array().unwrap().iter().map(nums).collect()
}

fn check_spread(c: &mut Closeness, label: &str, got: &Spread, want: &Value) {
    c.check(
        &format!("{label}.median"),
        got.median,
        num(&want["median"]),
        REL,
    );
    c.check(&format!("{label}.mean"), got.mean, num(&want["mean"]), REL);
    let range = nums(&want["range_68"]);
    c.check(
        &format!("{label}.range_68[0]"),
        got.range_68[0],
        range[0],
        REL,
    );
    c.check(
        &format!("{label}.range_68[1]"),
        got.range_68[1],
        range[1],
        REL,
    );
}

pub fn check_summary(c: &mut Closeness, label: &str, got: Option<Summary>, want: &Value) {
    if want.is_null() {
        assert!(got.is_none(), "{label}: expected no summary");
        return;
    }
    let got = got.unwrap_or_else(|| panic!("{label}: expected a summary"));
    assert_eq!(got.dates as u64, want["dates"].as_u64().unwrap(), "{label}");
    assert_eq!(got.draws as u64, want["draws"].as_u64().unwrap(), "{label}");
    assert_eq!(
        got.reflected,
        want["reflected"].as_bool().unwrap(),
        "{label}"
    );
    assert_eq!(want["method"], "kde_plot", "{label}");
    let rows = rows(&want["distribution"]);
    assert_eq!(
        got.distribution.len(),
        rows.len(),
        "{label}: a different trimmed grid"
    );
    for (k, (g, w)) in got.distribution.iter().zip(&rows).enumerate() {
        same(&format!("{label}/distribution[{k}].age"), g[0], w[0]);
    }
    let column =
        |series: &[[f64; 3]], col: usize| series.iter().map(|r| r[col]).collect::<Vec<_>>();
    let want_rows: Vec<[f64; 3]> = rows.iter().map(|r| [r[0], r[1], r[2]]).collect();
    check_series(
        c,
        &format!("{label}/mean"),
        &column(&got.distribution, 1),
        &column(&want_rows, 1),
    );
    check_series(
        c,
        &format!("{label}/sd"),
        &column(&got.distribution, 2),
        &column(&want_rows, 2),
    );
    check_spread(
        c,
        &format!("{label}/bandwidth"),
        &got.bandwidth,
        &want["bandwidth"],
    );
    check_spread(
        c,
        &format!("{label}/silverman"),
        &got.silverman,
        &want["silverman"],
    );
    check_spread(c, &format!("{label}/factor"), &got.factor, &want["factor"]);
}

fn calibrated(i: &Value) -> Vec<(Vec<f64>, Vec<f64>)> {
    let curve = radical_core::get_curve(i["curve"].as_str().unwrap()).unwrap();
    i["dates"]
        .as_array()
        .unwrap()
        .iter()
        .map(|d| {
            let d = nums(d);
            let masses = curve
                .calibrate(d[0], d[1], None, None, Mode::Standard)
                .unwrap();
            (curve.cal_ages().to_vec(), masses)
        })
        .collect()
}

#[test]
fn kde() {
    let mut c = Closeness::default();
    for case in family("kde").cases {
        let i = &case.expanded_inputs();
        let want = outputs(&case);
        let name = case.name.as_str();
        match name.split('/').next().unwrap() {
            "silverman" => {
                c.check(
                    name,
                    silverman_bandwidth(&nums(&i["t"])),
                    num(&want["h_s"]),
                    REL,
                );
            }
            "log_likelihood" => {
                let (t, h_s) = (nums(&i["t"]), num(&i["h_s"]));
                let got: Vec<f64> = nums(&i["g"])
                    .iter()
                    .map(|&g| log_likelihood(&t, h_s, g))
                    .collect();
                c.check_all(name, &got, &nums(&want["log_likelihood"]), REL);
            }
            "g_grid" => {
                let got = g_grid(&nums(&i["t"]), num(&i["h_s"]));
                for (label, g, w) in [
                    ("nodes", &got.nodes, "nodes"),
                    ("bounds", &got.bounds, "bounds"),
                ] {
                    let w = nums(&want[w]);
                    assert_eq!(g.len(), w.len(), "{name}/{label}");
                    for (k, (&g, &w)) in g.iter().zip(&w).enumerate() {
                        same(&format!("{name}/{label}[{k}]"), g, w);
                    }
                }
                check_series(
                    &mut c,
                    &format!("{name}/weight"),
                    &got.weight,
                    &nums(&want["weight"]),
                );
            }
            "draw_g" => {
                let (t, h_s) = (nums(&i["t"]), num(&i["h_s"]));
                let mut rng = Generator::new(i["seed"].as_u64().unwrap());
                let got: Vec<f64> = (0..i["count"].as_u64().unwrap())
                    .map(|_| draw_g(&t, h_s, &mut rng))
                    .collect();
                c.check_all(name, &got, &nums(&want["g"]), REL);
            }
            "kde_density" => {
                let spec = nums(&i["ages"]);
                let ages = arange(spec[0], spec[1], spec[2]);
                let bounds = (!i["bounds"].is_null()).then(|| {
                    let b = nums(&i["bounds"]);
                    (b[0], b[1])
                });
                let got = kde_density(&nums(&i["t"]), num(&i["h"]), &ages, bounds);
                check_series(&mut c, name, &got, &nums(&want["density"]));
            }
            "output_grid" => {
                let got = output_grid(&rows(&i["draws"]), &nums(&i["bandwidths"]));
                let want = nums(&want["ages"]);
                assert_eq!(got.len(), want.len(), "{name}");
                for (k, (&g, &w)) in got.iter().zip(&want).enumerate() {
                    same(&format!("{name}[{k}]"), g, w);
                }
            }
            "summary" => check_spread(&mut c, name, &spread(&nums(&i["values"])), &want),
            "draws_from_distribution" => {
                let curve = radical_core::get_curve(i["curve"].as_str().unwrap()).unwrap();
                let masses = curve
                    .calibrate(
                        num(&i["age_bp"]),
                        num(&i["error_1sigma"]),
                        None,
                        None,
                        Mode::Standard,
                    )
                    .unwrap();
                let mut rng = Generator::new(i["seed"].as_u64().unwrap());
                let got = draws_from_distribution(
                    curve.cal_ages(),
                    &masses,
                    &mut rng,
                    i["count"].as_u64().unwrap() as usize,
                )
                .unwrap();
                c.check_all(name, &got, &nums(&want["draws"]), REL);
            }
            "summarise" => {
                let dates = calibrated(i);
                let views: Vec<(&[f64], &[f64])> = dates
                    .iter()
                    .map(|(a, m)| (a.as_slice(), m.as_slice()))
                    .collect();
                let got = summarise(
                    &views,
                    i["draws"].as_u64().unwrap() as usize,
                    i["seed"].as_u64().unwrap(),
                )
                .unwrap();
                check_summary(&mut c, name, got, &want);
            }
            "summarise_draws" => {
                let sampled = rows(&i["sampled"]);
                let bounds = (!i["bounds"].is_null()).then(|| rows(&i["bounds"]));
                let got = summarise_draws(
                    &sampled,
                    bounds.as_ref().map(|b| (b[0].as_slice(), b[1].as_slice())),
                    i["draws"].as_u64().unwrap() as usize,
                    i["seed"].as_u64().unwrap(),
                );
                check_summary(&mut c, name, got, &want);
            }
            other => panic!("{name}: no comparison for {other}"),
        }
    }
    c.report("kde");
}
