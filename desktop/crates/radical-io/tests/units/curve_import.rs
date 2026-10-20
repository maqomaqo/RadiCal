use crate::*;
use radical_core::pyfmt::repr;
use radical_io::curve_import::{
    Calendar, Columns, CurveImportResult, parse_curve, parse_upload, to_14c_text,
};

fn calendar(v: &Value) -> Calendar {
    match v.as_str().unwrap() {
        "cal_bp" => Calendar::CalBp,
        "bc_ad" => Calendar::BcAd,
        other => panic!("calendar {other}"),
    }
}

fn columns(v: &Value) -> Option<Columns> {
    let c = v.as_object()?;
    let at = |k: &str| c[k].as_u64().unwrap() as usize;
    Some([at("cal_bp"), at("c14_age"), at("error")])
}

fn file(path: &str) -> Vec<u8> {
    std::fs::read(radical_corpus::repo_root().join(path)).unwrap()
}

/// The Python's answer plus the points, as the generator records them.
fn answer(result: &CurveImportResult) -> Value {
    let mut out = result.to_json();
    let text: Vec<String> = result
        .points
        .iter()
        .map(|(a, b, c)| format!("{},{},{}", repr(*a), repr(*b), repr(*c)))
        .collect();
    out["points_sha256"] = Value::from(radical_corpus::sha256_hex(text.join("\n").as_bytes()));
    if result.points.len() <= 40 {
        out["points"] = serde_json::json!(
            result
                .points
                .iter()
                .map(|p| [p.0, p.1, p.2])
                .collect::<Vec<_>>()
        );
    }
    out
}

#[test]
fn curve_import() {
    for case in family("curve_import").cases {
        let i = case.expanded_inputs();
        let name = case.name.as_str();
        let kind = i["kind"]
            .as_str()
            .unwrap_or_else(|| name.split('/').next().unwrap());
        let got = match kind {
            "parse_curve" => {
                let text = match i["file"].as_str() {
                    // Latin-1 or UTF-8, as the generator decoded it.
                    Some(path) if i["encoding"] == "latin-1" => {
                        file(path).iter().map(|&b| char::from(b)).collect()
                    }
                    Some(path) => String::from_utf8(file(path)).unwrap(),
                    None => i["text"].as_str().unwrap().to_owned(),
                };
                answer(
                    &parse_curve(&text, calendar(&i["calendar"]), columns(&i["columns"])).unwrap(),
                )
            }
            "parse_upload" => {
                let content = match i["file"].as_str() {
                    Some(path) => file(path),
                    None => crate::date_import::hex(i["hex"].as_str().unwrap()),
                };
                answer(
                    &parse_upload(
                        i["filename"].as_str().unwrap(),
                        &content,
                        calendar(&i["calendar"]),
                    )
                    .unwrap(),
                )
            }
            "to_14c_text" => {
                let points: Vec<(f64, f64, f64)> = i["points"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|p| {
                        (
                            p[0].as_f64().unwrap(),
                            p[1].as_f64().unwrap(),
                            p[2].as_f64().unwrap(),
                        )
                    })
                    .collect();
                serde_json::json!({"text": to_14c_text(&points, i["header"].as_str().unwrap())})
            }
            other => panic!("unhandled case kind {other}"),
        };
        same_json(name, &got, &outputs(&case));
    }
}
