use crate::*;
use radical_core::calibration::CalibrationCurve;
use radical_core::curve_mix::mix;
use radical_core::curve_registry::{
    builtin_spec, content_hash, normalise, resolve_alias, step_summary,
};

fn strings(v: &Value) -> Vec<String> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_owned())
        .collect()
}

/// Text and hashes, compared exactly.
#[test]
fn curve_registry() {
    for case in family("curve_registry").cases {
        let i = &case.expanded_inputs();
        let want = outputs(&case);
        let name = case.name.as_str();
        match name.split('/').next().unwrap() {
            "normalise" => {
                let got: Vec<String> = strings(&i["names"]).iter().map(|n| normalise(n)).collect();
                assert_eq!(got, strings(&want["normalised"]), "{name}");
            }
            "resolve_alias" => {
                let got: Vec<Value> = strings(&i["names"])
                    .iter()
                    .map(|n| resolve_alias(n, &[]).map_or(Value::Null, Value::String))
                    .collect();
                assert_eq!(got, want["ids"].as_array().unwrap().clone(), "{name}");
            }
            "step_summary" => {
                assert_eq!(
                    step_summary(&nums(&i["cal_ages"])),
                    want["summary"].as_str().unwrap(),
                    "{name}"
                );
            }
            "spec" => {
                let spec = builtin_spec(i["curve"].as_str().unwrap()).unwrap();
                let (b, m) = (spec.builtin, &spec.measured);
                let got = serde_json::json!({
                    "id": b.id, "name": b.name, "type": b.kind_of_curve, "sample_type": b.sample_type,
                    "description": b.description, "reference": b.reference, "year": b.year,
                    "doi": b.doi, "kind": "builtin", "aliases": b.aliases,
                    "data_points": m.data_points, "cal_min": m.cal_min, "cal_max": m.cal_max,
                    "c14_min": m.c14_min, "c14_max": m.c14_max, "resolution": m.resolution,
                    "content_sha256": m.content_sha256, "archived": false, "source_filename": null,
                });
                assert_eq!(got, want, "{name}");
            }
            "content_hash" => {
                let curve = if name == "content_hash/mixed" {
                    let curve = |k: &str| radical_core::get_curve(i[k].as_str().unwrap()).unwrap();
                    mix(
                        curve("curve_a"),
                        curve("curve_b"),
                        num(&i["percent_a"]),
                        num(&i["percent_a_error"]),
                        "Mixed",
                    )
                    .unwrap()
                } else {
                    CalibrationCurve::new(
                        "odd",
                        nums(&i["cal_ages"]),
                        nums(&i["c14_ages"]),
                        nums(&i["c14_errors"]),
                    )
                };
                assert_eq!(
                    content_hash(&curve),
                    want["sha256"].as_str().unwrap(),
                    "{name}"
                );
            }
            other => panic!("{name}: no comparison for {other}"),
        }
    }
}
