//! radical-core's exact solvers held to the api/models corpus: every .oxcal
//! fixture the API solved exactly, run through the same pipeline.

#[path = "../units/model.rs"]
#[allow(dead_code)]
mod model;

use radical_corpus::{as_f64, load_family};
use serde_json::Value;

pub fn num(v: &Value) -> f64 {
    as_f64(v).unwrap_or_else(|| panic!("not a corpus number: {v}"))
}

#[test]
fn smoke() {
    let family = load_family("api/models").unwrap();
    for case in family.cases.iter().filter(|c| c.name.ends_with("/auto")) {
        let outputs = match case.expanded_outputs() {
            Some(o) => o,
            None => continue,
        };
        let solver = &outputs["response"]["data"]["solver"];
        if solver["method"] != "exact" {
            continue;
        }
        let inputs = case.expanded_inputs();
        let request = &inputs["request"];
        let root = model::element(&request["model_tree"]);
        let dates = model::measurements(&request["measurements"]);
        let chain = radical_core::exact_chain::layout_with_reason(Some(&root), &dates, false, true)
            .unwrap();
        let options = radical_core::exact_chain::SolveOptions {
            estimate_durations: true,
            ..Default::default()
        };
        let start = std::time::Instant::now();
        let solved =
            radical_core::exact_chain::solve(&dates, &root, &chain, &options, &Default::default())
                .unwrap();
        eprintln!(
            "{} in {:?}: grid {:?}",
            case.name,
            start.elapsed(),
            solved.grid
        );
        let analysis = &outputs["analysis"]["data"];
        // results by order
        for (k, r) in analysis["results"].as_array().unwrap().iter().enumerate() {
            let id = &dates[k].id;
            let got = solved.measurement(id).unwrap();
            eprintln!(
                "  date {}: mean {} vs {} ({:e})",
                dates[k].label(),
                got.posterior_mean,
                num(&r["posterior_mean"]),
                got.posterior_mean - num(&r["posterior_mean"])
            );
        }
        if let Some(d) = outputs["response"]["data"]["intervals"]["durations"].as_array() {
            let block = solved.intervals.as_ref().unwrap();
            for (g, w) in block.durations.iter().zip(d) {
                eprintln!(
                    "  {}: mean {} vs {}",
                    g.id,
                    g.years.mean,
                    num(&w["years"]["mean"])
                );
            }
        }
    }
}
