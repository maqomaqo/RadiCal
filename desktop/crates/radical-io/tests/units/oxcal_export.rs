use crate::oxcal_script::corpus_library;
use crate::*;
use radical_core::measurement::Measurement;
use radical_io::date_import::{Date, ImportResult};
use radical_io::ids::RandomIds;
use radical_io::library::Library;
use radical_io::oxcal_export::{Config, write_oxcal};
use radical_io::oxcal_script::{element_from_json, parse_oxcal};

fn opt_num(v: &Value) -> Option<f64> {
    v.as_f64()
}

fn opt_str(v: &Value) -> Option<String> {
    v.as_str().map(str::to_owned)
}

fn measurement(v: &Value) -> Measurement {
    Measurement {
        id: v["id"].as_str().unwrap_or_default().to_owned(),
        sample_name: opt_str(&v["sample_name"]),
        age_bp: v["age_bp"].as_f64().unwrap(),
        error_1sigma: v["error_1sigma"].as_f64().unwrap(),
        curve_id: opt_str(&v["curve_id"]),
        delta_r: opt_num(&v["delta_r"]),
        delta_r_error: opt_num(&v["delta_r_error"]),
        tpq_age_bp: opt_num(&v["tpq_age_bp"]),
        taq_age_bp: opt_num(&v["taq_age_bp"]),
        element_id: opt_str(&v["element_id"]),
        outlier_prior: opt_num(&v["outlier_prior"]),
        outlier_model: opt_str(&v["outlier_model"]),
        inbuilt_years: opt_num(&v["inbuilt_years"]),
        inbuilt_shape: opt_str(&v["inbuilt_shape"]),
        ring_offset: opt_num(&v["ring_offset"]),
        ring_offset_error: opt_num(&v["ring_offset_error"]),
        depth_cm: opt_num(&v["depth_cm"]),
        ..Measurement::default()
    }
}

/// An imported date as the measurement an analysis stores.
pub fn from_import(d: &Date) -> Measurement {
    Measurement {
        id: d.id.clone(),
        sample_name: Some(d.sample_name.clone()),
        age_bp: d.age_bp,
        error_1sigma: d.error_1sigma,
        curve_id: Some(d.curve_id.clone()),
        delta_r: d.delta_r,
        delta_r_error: d.delta_r_error,
        tpq_age_bp: d.tpq_age_bp,
        taq_age_bp: d.taq_age_bp,
        element_id: d.element_id.clone(),
        outlier_prior: Some(d.outlier_prior),
        outlier_model: Some(d.outlier_model.clone()),
        inbuilt_years: d.inbuilt_years,
        inbuilt_shape: d.inbuilt_shape.clone(),
        ring_offset: d.ring_offset,
        ring_offset_error: d.ring_offset_error,
        depth_cm: d.depth_cm,
        ..Measurement::default()
    }
}

/// Re-export what an import read, as the generator does.
fn reexport(name: &str, back: &ImportResult, commit: Option<&str>, library: &Library) -> String {
    let dates: Vec<Measurement> = back.dates.iter().map(from_import).collect();
    let tree = back.model_tree.as_ref().map(element_from_json);
    let kind = if tree.is_some() {
        "bayesian"
    } else {
        "group_calibration"
    };
    let config = Config {
        resolution: back.resolution.map(|r| r as f64),
        uniform_span_prior: None,
    };
    write_oxcal(
        name,
        kind,
        &dates,
        tree.as_ref(),
        &config,
        commit,
        "2026-01-01",
        library,
    )
    .unwrap()
    .0
}

/// Every export the tests make, to the byte; each read back by the Rust
/// importer and written again, to the byte of the Python's own round trip.
#[test]
fn oxcal_export() {
    let script = family("oxcal_script");
    let rows =
        outputs(script.cases.iter().find(|c| c.name == "library").unwrap())["curves"].clone();
    let (stored, curves) = corpus_library(&rows);
    let library = Library::new(stored, &curves);
    for case in family("oxcal_export").cases {
        let i = case.expanded_inputs();
        let name = case.name.as_str();
        let want = outputs(&case);
        let dates: Vec<Measurement> = i["measurements"]
            .as_array()
            .unwrap()
            .iter()
            .map(measurement)
            .collect();
        let tree = (!i["tree"].is_null()).then(|| element_from_json(&i["tree"]));
        let config = Config {
            resolution: i["config"]["resolution"].as_f64(),
            uniform_span_prior: i["config"]["uniform_span_prior"].as_bool(),
        };
        let commit = i["app_commit"].as_str();
        let (text, omitted) = write_oxcal(
            i["name"].as_str().unwrap(),
            i["analysis_type"].as_str().unwrap(),
            &dates,
            tree.as_ref(),
            &config,
            commit,
            "2026-01-01",
            &library,
        )
        .unwrap();
        assert_eq!(text, want["text"].as_str().unwrap(), "{name}");
        assert_eq!(serde_json::json!(omitted), want["omitted"], "{name}");

        let back = parse_oxcal(
            &text,
            &library,
            Some("export.oxcal"),
            &mut RandomIds::default(),
        );
        assert_eq!(
            back.ok(),
            want["reimported_ok"].as_bool().unwrap(),
            "{name}: {:?}",
            back.errors
        );
        if back.ok() {
            let again = reexport(i["name"].as_str().unwrap(), &back, commit, &library);
            assert_eq!(
                again,
                want["reexported"].as_str().unwrap(),
                "{name}: round trip"
            );
        }
    }
}
