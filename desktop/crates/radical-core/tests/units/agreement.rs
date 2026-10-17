use crate::model::{measurement, measurements, opt_num};
use crate::*;
use radical_core::agreement::{
    agreement_index, combination_threshold, date_agreement, model_agreement, outlier_scale_terms,
    overall_agreement,
};
use radical_core::measurement::Calibrator;

/// Agreement indices of dates under an outlier model read a convolution
/// SciPy forms by FFT, where the port sums it directly; see the models test.
const REL_OUTLIER: f64 = 1e-8;

/// An index this close to zero, in percent, is zero. A date far from its own
/// likelihood scores 1e-15 or less, and there the FFT's rounding in an
/// inbuilt-age allowance's convolution is the whole of Python's value.
const ZERO: f64 = 1e-9;

fn pairs(v: &Value) -> Vec<[f64; 2]> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|p| [num(&p[0]), num(&p[1])])
        .collect()
}

#[test]
fn agreement() {
    let mut c = Closeness::default();
    let cal = Calibrator::default();
    for case in family("agreement").cases {
        let name = case.name.clone();
        let i = case.expanded_inputs();
        let want = outputs(&case);
        let flagged = i
            .get("measurement")
            .is_some_and(|m| opt_num(&m["outlier_prior"]).is_some_and(|p| p > 0.0));
        let rel = if flagged { REL_OUTLIER } else { REL };
        let check = |c: &mut Closeness, got: Option<f64>, want: &Value| match got {
            None => assert!(want.is_null(), "{name}: None, Python had {want}"),
            Some(g) if g.abs() < ZERO && num(want).abs() < ZERO => c.compared += 1,
            Some(g) => c.check(&name, g, num(want), rel),
        };
        match name.split('/').next().unwrap() {
            "agreement_index" => {
                let m = measurement(&i["measurement"]);
                let unmodelled = i.get("unmodelled").map(pairs);
                let got = agreement_index(
                    &m,
                    &pairs(&i["modelled"]),
                    unmodelled.as_deref(),
                    i.get("log_scale").and_then(opt_num_ref),
                    &cal,
                )
                .unwrap();
                check(&mut c, got, &want["index"]);
            }
            "date_agreement" => {
                let m = measurement(&i["measurement"]);
                let scales = (!i["scales"].is_null()).then(|| nums(&i["scales"]));
                let got = date_agreement(&m, &nums(&i["draws"]), scales.as_deref(), &cal).unwrap();
                check(&mut c, got, &want["index"]);
            }
            "overall_agreement" => {
                for (indices, w) in i["indices"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .zip(want["overall"].as_array().unwrap())
                {
                    let indices: Vec<Option<f64>> =
                        indices.as_array().unwrap().iter().map(opt_num).collect();
                    for (extra, w) in [0, 1, 2].into_iter().zip(w.as_array().unwrap()) {
                        match overall_agreement(&indices, extra) {
                            None => assert!(w.is_null(), "{name}"),
                            Some(g) => c.check(&name, g, num(w), REL),
                        }
                    }
                }
            }
            "combination_threshold" => {
                let got: Vec<f64> = (0..12).map(combination_threshold).collect();
                c.check_all(&name, &got, &nums(&want["threshold"]), REL);
            }
            "outlier_scale_terms" => {
                let got = outlier_scale_terms(&measurements(&i["measurements"]));
                assert_eq!(got as u64, want["terms"].as_u64().unwrap());
            }
            "model_agreement" => {
                let draws = crate::intervals::draws(&i["draws"]);
                let scales =
                    (!i["scales"].is_null()).then(|| crate::intervals::draws(&i["scales"]));
                let got = model_agreement(
                    &measurements(&i["measurements"]),
                    &draws,
                    scales.as_ref(),
                    &cal,
                )
                .unwrap();
                match got {
                    None => assert!(want["index"].is_null(), "{name}"),
                    Some(g) => c.check(&name, g, num(&want["index"]), REL_OUTLIER),
                }
            }
            other => panic!("unhandled case {other}"),
        }
    }
    c.report("agreement");
}

fn opt_num_ref(v: &Value) -> Option<f64> {
    opt_num(v)
}
