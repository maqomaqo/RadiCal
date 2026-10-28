//! Time the sampler on the benchmark models in `desktop/bench/models.json`:
//!
//! ```text
//! cargo run --release -p radical-core --example sampler_bench
//! ```
//!
//! A full solve as the API runs it (four chains, three temperatures, seed 42,
//! stopping when converged), and one untempered chain held to a fixed 10,000
//! sweeps: the cost per sweep.

#[path = "../tests/units/model.rs"]
#[allow(dead_code)]
mod model;

use std::time::Instant;

use radical_core::gibbs::chain::{RunOptions, Start};
use radical_core::gibbs::{Model, SampleOptions, run, solve};
use radical_core::measurement::Calibrator;
use serde_json::Value;

fn num(v: &Value) -> f64 {
    radical_corpus::as_f64(v).unwrap()
}

const FIXED_SWEEPS: usize = 10_000;

fn main() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../bench/models.json");
    let models: serde_json::Map<String, Value> = serde_json::from_str(
        &std::fs::read_to_string(path).expect("desktop/bench/models.json"),
    )
    .unwrap();
    let cal = Calibrator::default();
    println!(
        "{:28} {:>18} {:>8} {:>20}",
        "model", "solve (4 chains)", "sweeps", "10k sweeps, 1 chain"
    );
    for (name, model_json) in &models {
        let root = model::element(&model_json["model_tree"]);
        let dates = model::measurements(&model_json["measurements"]);
        let options = SampleOptions {
            chains: 4,
            seed: 42,
            num_samples: 4000,
            ..SampleOptions::default()
        };
        let started = Instant::now();
        let solved = solve(&dates, &root, &options, &cal, None).unwrap().unwrap();
        let solve_seconds = started.elapsed().as_secs_f64();
        let built = Model::new(&dates, &root, None, true, &cal).unwrap();
        let fixed = RunOptions {
            sweeps: FIXED_SWEEPS,
            max_sweeps: Some(FIXED_SWEEPS),
            seed: 42,
            target_convergence: 2.0,
            temperatures: 1,
            start: Start::default(),
            ..RunOptions::default()
        };
        let started = Instant::now();
        run(built, &fixed, None).unwrap().unwrap();
        let fixed_seconds = started.elapsed().as_secs_f64();
        println!(
            "{name:28} {solve_seconds:17.2}s {:8} {fixed_seconds:19.2}s",
            solved.sweeps
        );
    }
}
