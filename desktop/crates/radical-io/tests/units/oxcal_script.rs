use std::collections::HashMap;

use crate::*;
use radical_core::curve_mix::mix;
use radical_core::curve_registry::{MixRecipe, StoredNames};
use radical_core::measurement::Curves;
use radical_core::{CalibrationCurve, Error, get_curve};
use radical_io::ids::RandomIds;
use radical_io::library::{Library, StoredCurve};
use radical_io::oxcal_script::parse_oxcal;

/// The built-in curves and the corpus owner's mixtures, made from their
/// recipes as the Python's registry makes them.
pub struct CorpusCurves(HashMap<String, CalibrationCurve>);

impl Curves for CorpusCurves {
    fn curve(&self, curve_id: &str) -> Result<&CalibrationCurve, Error> {
        match self.0.get(curve_id) {
            Some(c) => Ok(c),
            None => get_curve(curve_id),
        }
    }
}

pub fn corpus_library(rows: &Value) -> (Vec<StoredCurve>, CorpusCurves) {
    let mut stored = Vec::new();
    let mut curves = HashMap::new();
    for row in rows.as_array().unwrap() {
        let id = row["id"].as_str().unwrap().to_owned();
        let r = &row["recipe"];
        let recipe = MixRecipe {
            id: id.clone(),
            a: r["a"].as_str().unwrap().to_owned(),
            b: r["b"].as_str().unwrap().to_owned(),
            percent_a: r["percent_a"].as_f64().unwrap(),
            percent_a_error: r["percent_a_error"].as_f64().unwrap(),
        };
        let name = row["name"].as_str().unwrap().to_owned();
        let curve = mix(
            get_curve(&recipe.a).unwrap(),
            get_curve(&recipe.b).unwrap(),
            recipe.percent_a,
            recipe.percent_a_error,
            &name,
        )
        .unwrap();
        curves.insert(id.clone(), curve);
        stored.push(StoredCurve {
            names: StoredNames {
                id,
                name,
                aliases: row["aliases"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|a| a.as_str().unwrap().to_owned())
                    .collect(),
            },
            archived: row["archived"].as_bool().unwrap(),
            recipe: Some(recipe),
            source_filename: None,
        });
    }
    (stored, CorpusCurves(curves))
}

/// Every script the import tests read, and the fixtures, as the API's JSON.
#[test]
fn oxcal_script() {
    let fam = family("oxcal_script");
    let rows = outputs(fam.cases.iter().find(|c| c.name == "library").unwrap())["curves"].clone();
    let (stored, curves) = corpus_library(&rows);
    let library = Library::new(stored, &curves);
    let mut ids = RandomIds::default();
    let mut failures = Vec::new();
    for case in &fam.cases {
        let i = case.expanded_inputs();
        let name = case.name.as_str();
        let run = || match name.split('/').next().unwrap() {
            "library" => {}
            "parse_oxcal" => {
                let script = match i["fixture"].as_str() {
                    Some(path) => {
                        std::fs::read_to_string(radical_corpus::repo_root().join(path)).unwrap()
                    }
                    None => i["text"].as_str().unwrap().to_owned(),
                };
                let mut ids = RandomIds::default();
                let got = parse_oxcal(&script, &library, i["filename"].as_str(), &mut ids);
                same_json(name, &got.to_json(), &outputs(case));
            }
            "parse_upload" => {
                let mut ids = RandomIds::default();
                crate::date_import::check_upload(case, &library, &mut ids);
            }
            other => panic!("unhandled case kind {other}"),
        };
        if let Err(e) = std::panic::catch_unwind(std::panic::AssertUnwindSafe(run)) {
            let message = e
                .downcast_ref::<String>()
                .cloned()
                .or_else(|| e.downcast_ref::<&str>().map(|s| (*s).to_owned()))
                .unwrap_or_default();
            failures.push(format!("{name}: {message}"));
        }
    }
    let _ = &mut ids;
    assert!(
        failures.is_empty(),
        "{} failing:\n{}",
        failures.len(),
        failures.join("\n")
    );
}
