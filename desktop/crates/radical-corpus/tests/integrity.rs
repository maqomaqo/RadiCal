//! The corpus is what every parity test stands on, so it is checked before
//! anything is compared against it: that it is the corpus the manifest
//! describes, that it was generated against the curves in this repository, and
//! that every case has the shape a parity test will assume.

use std::collections::BTreeSet;
use std::fs;
use std::path::Path;

use radical_corpus::{Window, corpus_dir, load_family, load_manifest, repo_root, sha256_hex};
use serde_json::Value;

fn family_name(listed: &str) -> &str {
    listed
        .strip_prefix("data/")
        .and_then(|s| s.strip_suffix(".json"))
        .unwrap_or_else(|| panic!("{listed}: not a data/*.json path"))
}

#[test]
fn every_listed_file_is_the_one_that_was_generated() {
    let manifest = load_manifest().unwrap();
    assert!(!manifest.files.is_empty());
    for (path, want) in &manifest.files {
        let bytes = fs::read(corpus_dir().join(path)).unwrap();
        assert_eq!(
            &sha256_hex(&bytes),
            want,
            "{path} has changed since generate.py wrote it"
        );
    }
}

#[test]
fn every_file_in_the_corpus_is_listed() {
    fn walk(dir: &Path, base: &Path, out: &mut BTreeSet<String>) {
        for entry in fs::read_dir(dir).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                walk(&path, base, out);
            } else if path.extension().is_some_and(|e| e == "json") {
                let rel = path.strip_prefix(base).unwrap();
                out.insert(rel.to_string_lossy().replace('\\', "/"));
            }
        }
    }
    let mut found = BTreeSet::new();
    walk(&corpus_dir().join("data"), &corpus_dir(), &mut found);
    let listed: BTreeSet<String> = load_manifest().unwrap().files.into_keys().collect();
    assert_eq!(
        found, listed,
        "a corpus file is not in the manifest, or the reverse"
    );
}

#[test]
fn it_was_generated_against_the_curves_in_this_repository() {
    let manifest = load_manifest().unwrap();
    assert_eq!(
        manifest
            .curves
            .keys()
            .map(String::as_str)
            .collect::<Vec<_>>(),
        ["intcal20", "marine20", "shcal20"]
    );
    for (id, entry) in &manifest.curves {
        let bytes = fs::read(repo_root().join(&entry.file)).unwrap();
        assert_eq!(
            sha256_hex(&bytes),
            entry.sha256,
            "{id}: {} differs from the curve the corpus was generated against",
            entry.file
        );
    }
}

#[test]
fn every_case_is_named_once_and_answered_once() {
    for listed in load_manifest().unwrap().files.keys() {
        let name = family_name(listed);
        let family = load_family(name).unwrap();
        assert_eq!(family.family, name);
        assert!(!family.cases.is_empty(), "{name}: no cases");
        let mut seen = BTreeSet::new();
        for case in &family.cases {
            assert!(
                seen.insert(case.name.as_str()),
                "{name}: {} appears twice",
                case.name
            );
            assert!(
                case.outputs.is_some() != case.error.is_some(),
                "{name}/{}: needs exactly one of outputs and error",
                case.name
            );
        }
    }
}

/// Every object shaped like a window, anywhere in a value.
fn windows<'a>(value: &'a Value, out: &mut Vec<&'a Value>) {
    match value {
        Value::Object(map) => {
            if map.contains_key("index_from") && map.contains_key("mass_outside") {
                out.push(value);
            }
            map.values().for_each(|v| windows(v, out));
        }
        Value::Array(items) => items.iter().for_each(|v| windows(v, out)),
        _ => {}
    }
}

#[test]
fn every_stored_window_fits_its_distribution() {
    let family = load_family("units/calibrate").unwrap();
    let mut checked = 0;
    for case in &family.cases {
        let Some(outputs) = &case.outputs else {
            continue;
        };
        let mut found = Vec::new();
        windows(outputs, &mut found);
        for raw in found {
            let w: Window = serde_json::from_value(raw.clone()).unwrap();
            let label = &case.name;
            assert_eq!(w.cal_ages.len(), w.values.len(), "{label}");
            assert!(
                w.range().end <= w.length,
                "{label}: window runs past the curve"
            );
            assert!(
                w.cal_ages.windows(2).all(|p| p[0] < p[1]),
                "{label}: ages not ascending"
            );
            assert!(
                w.values.iter().all(|v| v.is_finite() && *v >= 0.0),
                "{label}"
            );
            let inside: f64 = w.values.iter().sum();
            assert!(
                (inside + w.mass_outside - 1.0).abs() < 1e-9,
                "{label}: a posterior should hold unit mass, holds {}",
                inside + w.mass_outside
            );
            checked += 1;
        }
    }
    assert!(checked > 100, "only {checked} windows found");
}

#[test]
fn linspace_is_numpys_bit_for_bit() {
    let family = load_family("units/linspace").unwrap();
    for case in &family.cases {
        let i = &case.inputs;
        let got = radical_corpus::linspace(
            i["start"].as_f64().unwrap(),
            i["stop"].as_f64().unwrap(),
            i["n"].as_u64().unwrap() as usize,
        );
        let want = radical_corpus::as_f64s(&case.outputs.as_ref().unwrap()["values"]).unwrap();
        assert_eq!(got.len(), want.len(), "{}", case.name);
        for (k, (g, w)) in got.iter().zip(&want).enumerate() {
            assert_eq!(g.to_bits(), w.to_bits(), "{}[{k}]: {g} != {w}", case.name);
        }
    }
}

/// Anything still encoded after expansion.
fn encoded(value: &Value) -> bool {
    match value {
        Value::Object(map) => {
            map.contains_key("$series")
                || map.contains_key("$from_analysis")
                || map.values().any(encoded)
        }
        Value::Array(items) => items.iter().any(encoded),
        _ => false,
    }
}

#[test]
fn every_case_expands_to_plain_json() {
    let mut series_seen = false;
    for listed in load_manifest().unwrap().files.keys() {
        let family = load_family(family_name(listed)).unwrap();
        for case in &family.cases {
            series_seen |= case.outputs.as_ref().is_some_and(encoded);
            assert!(!encoded(&case.expanded_inputs()), "{}", case.name);
            if let Some(outputs) = case.expanded_outputs() {
                assert!(!encoded(&outputs), "{}", case.name);
            }
        }
    }
    assert!(
        series_seen,
        "no encoded case found; is this the schema 2 corpus?"
    );
}

#[test]
fn a_saved_run_answers_the_post_and_the_get_alike() {
    let family = load_family("api/models").unwrap();
    let mut checked = 0;
    for case in &family.cases {
        let Some(outputs) = case.expanded_outputs() else {
            continue;
        };
        let Some(analysis) = outputs.pointer("/analysis/data/results") else {
            continue;
        };
        let posted = outputs
            .pointer("/response/data/results")
            .unwrap()
            .as_object()
            .unwrap();
        assert_eq!(
            posted.len(),
            analysis.as_array().unwrap().len(),
            "{}",
            case.name
        );
        for entry in posted.values() {
            let distribution = entry["probability_distribution"].as_array().unwrap();
            assert!(
                distribution
                    .iter()
                    .all(|p| p.as_array().is_some_and(|p| p.len() == 2)),
                "{}",
                case.name
            );
        }
        checked += 1;
    }
    assert!(checked >= 10, "only {checked} saved runs");
}
