use crate::*;
use radical_core::influence::{
    DISTRIBUTION_POINTS, DurationQuantity, Quantity, Variant, apply, compact_distribution,
    neighbouring_phases, plan, summarise,
};

fn series(v: &Value) -> Vec<[f64; 2]> {
    v.as_array().map_or_else(Vec::new, |rows| {
        rows.iter()
            .map(|p| {
                let p = nums(p);
                [p[0], p[1]]
            })
            .collect()
    })
}

fn check_series(label: &str, got: &Option<Vec<[f64; 2]>>, want: &Value) {
    match got {
        None => assert!(want.is_null(), "{label}: None, Python had {want}"),
        Some(g) => {
            let w = series(want);
            assert_eq!(g.len(), w.len(), "{label}");
            for (k, (a, b)) in g.iter().zip(&w).enumerate() {
                same(&format!("{label}[{k}].x"), a[0], b[0]);
                same(&format!("{label}[{k}].y"), a[1], b[1]);
            }
        }
    }
}

fn variant_json(v: &Variant) -> Value {
    let mut out = serde_json::json!({
        "id": v.id, "kind": v.kind, "date_id": v.date_id, "date_name": v.date_name,
    });
    if let Some(to) = &v.to_phase_id {
        out["to_phase_id"] = Value::from(to.as_str());
        out["to_phase_name"] = Value::from(v.to_phase_name.as_deref().unwrap());
        out["direction"] = Value::from(v.direction.unwrap());
    }
    out["label"] = Value::from(v.label.as_str());
    out
}

/// Plans, applied variants and labels as text; series to the bit.
#[test]
fn influence() {
    for case in family("influence").cases {
        let i = case.expanded_inputs();
        let want = outputs(&case);
        let name = case.name.as_str();
        match name.split('/').next().unwrap() {
            "plan" => {
                let tree = crate::model::element(&i["tree"]);
                let dates = crate::model::measurements(&i["measurements"]);
                let kinds: Vec<&str> = i["kinds"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|k| k.as_str().unwrap())
                    .collect();
                let variants = plan(&dates, &tree, &kinds);
                let got: Vec<Value> = variants.iter().map(variant_json).collect();
                assert_eq!(Value::Array(got), want["variants"], "{name}");
                for (v, w) in variants.iter().zip(want["applied"].as_array().unwrap()) {
                    let applied: Vec<Value> = apply(v, &dates)
                        .unwrap()
                        .iter()
                        .map(|m| {
                            serde_json::json!({
                                "id": m.id, "element_id": m.element_id,
                                "outlier_prior": m.outlier_prior, "outlier_model": m.outlier_model,
                            })
                        })
                        .collect();
                    assert_eq!(&Value::Array(applied), w, "{name}/{}", v.id);
                }
            }
            "neighbouring_phases" => {
                let tree = crate::model::element(&i["tree"]);
                let (older, younger) = neighbouring_phases(&tree, i["phase_id"].as_str().unwrap());
                assert_eq!(
                    older.map(|e| e.id.as_str()),
                    want["older"].as_str(),
                    "{name}"
                );
                assert_eq!(
                    younger.map(|e| e.id.as_str()),
                    want["younger"].as_str(),
                    "{name}"
                );
            }
            "compact_distribution" => {
                let got = compact_distribution(&series(&i["distribution"]), DISTRIBUTION_POINTS);
                check_series(name, &got, &want["compact"]);
            }
            "summarise" => {
                let hpd = |v: &Value| v.as_array().map(|a| (num(&a[0]), num(&a[1])));
                let boundary_series: Vec<(String, Vec<[f64; 2]>)> = i["boundaries"]
                    .as_object()
                    .unwrap()
                    .iter()
                    .map(|(id, b)| {
                        (
                            id.clone(),
                            series(&b["posterior"]["probability_distribution"]),
                        )
                    })
                    .collect();
                let boundaries: Vec<(&str, Quantity)> = i["boundaries"]
                    .as_object()
                    .unwrap()
                    .iter()
                    .zip(&boundary_series)
                    .map(|((id, b), (_, s))| {
                        (
                            id.as_str(),
                            Quantity {
                                name: b["name"].as_str(),
                                median: b["posterior"]["posterior_median"].as_f64(),
                                hpd_95: hpd(&b["posterior"]["hpd_95"]),
                                distribution: s,
                            },
                        )
                    })
                    .collect();
                let rows = i["intervals"]["durations"].as_array().unwrap();
                let duration_series: Vec<Vec<[f64; 2]>> = rows
                    .iter()
                    .map(|r| series(&r["years"]["distribution"]))
                    .collect();
                let durations: Vec<DurationQuantity> = rows
                    .iter()
                    .zip(&duration_series)
                    .map(|(r, s)| DurationQuantity {
                        kind: r["kind"].as_str().unwrap(),
                        id: r["id"].as_str().unwrap(),
                        element_id: r["element_id"].as_str(),
                        quantity: Quantity {
                            name: r["name"].as_str(),
                            median: r["years"]["median"].as_f64(),
                            hpd_95: hpd(&r["years"]["hpd_95"]),
                            distribution: s,
                        },
                    })
                    .collect();
                let (b, d) = summarise(&boundaries, &durations);
                for (section, got) in [("boundaries", b), ("durations", d)] {
                    let w = want[section].as_object().unwrap();
                    assert_eq!(got.len(), w.len(), "{name}/{section}");
                    for (key, r) in &got {
                        let wr = &w[key];
                        assert_eq!(r.name.as_deref(), wr["name"].as_str(), "{name}/{key}");
                        assert_eq!(
                            r.kind.as_deref(),
                            wr.get("kind").and_then(Value::as_str),
                            "{name}/{key}"
                        );
                        same(
                            &format!("{name}/{key}.median"),
                            r.median,
                            num(&wr["median"]),
                        );
                        assert_eq!(
                            r.hpd_95.map(|h| h.to_vec()),
                            wr["hpd_95"].as_array().map(|_| nums(&wr["hpd_95"])),
                            "{name}/{key}"
                        );
                        check_series(
                            &format!("{name}/{key}.distribution"),
                            &r.distribution,
                            &wr["distribution"],
                        );
                    }
                }
            }
            other => panic!("{name}: no comparison for {other}"),
        }
    }
}
