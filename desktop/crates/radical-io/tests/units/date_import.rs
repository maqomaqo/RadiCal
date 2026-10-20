use crate::*;
use radical_io::date_import::{
    looks_like_cql2, normalise_header, parse_csv, parse_upload, template_csv,
};
use radical_io::ids::RandomIds;
use radical_io::library::Library;

fn text(v: &Value) -> &str {
    v.as_str().unwrap()
}

/// Every case, against the curves built in: the corpus owner had no stored
/// curves when these ran.
#[test]
fn date_import() {
    let mut ids = RandomIds::default();
    let library = Library::builtins();
    for case in family("date_import").cases {
        let i = case.expanded_inputs();
        let name = case.name.as_str();
        match name.split('/').next().unwrap() {
            "parse_csv" => {
                let got = parse_csv(text(&i["text"]), &library, i["filename"].as_str(), &mut ids);
                match (&got, &case.error) {
                    (Ok(r), None) => same_json(name, &r.to_json(), &outputs(&case)),
                    (Err(e), Some(want)) => assert_eq!(e.0, text(&want["message"]), "{name}"),
                    _ => panic!("{name}: {got:?} vs {:?}", case.error),
                }
            }
            "parse_upload" => check_upload(&case, &library, &mut ids),
            "normalise_header" => {
                let got: Vec<String> = i["headers"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|h| normalise_header(text(h)))
                    .collect();
                assert_eq!(serde_json::json!(got), outputs(&case)["keys"], "{name}");
            }
            "looks_like_cql2" => {
                let got: Vec<bool> = i["texts"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|t| looks_like_cql2(text(t)))
                    .collect();
                assert_eq!(serde_json::json!(got), outputs(&case)["cql2"], "{name}");
            }
            "template_csv" => {
                assert_eq!(template_csv(), text(&outputs(&case)["csv"]), "{name}");
            }
            other => panic!("unhandled case kind {other}"),
        }
    }
}

pub fn hex(text: &str) -> Vec<u8> {
    (0..text.len())
        .step_by(2)
        .map(|k| u8::from_str_radix(&text[k..k + 2], 16).unwrap())
        .collect()
}

/// One `parse_upload/...` case: the bytes from `hex`, or `length` copies of
/// `repeat`.
pub fn check_upload(case: &Case, library: &Library, ids: &mut RandomIds) {
    let i = case.expanded_inputs();
    let content = match i["hex"].as_str() {
        Some(h) => hex(h),
        None => text(&i["repeat"])
            .repeat(i["length"].as_u64().unwrap() as usize)
            .into_bytes(),
    };
    let got = parse_upload(text(&i["filename"]), &content, library, ids).unwrap();
    same_json(&case.name, &got.to_json(), &outputs(case));
}
