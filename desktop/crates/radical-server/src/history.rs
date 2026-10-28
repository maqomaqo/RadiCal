//! Run history (`api/run_history.py`): the last few completed runs of each
//! analysis, each kept as what reading the analysis returned when it
//! finished, with a fingerprint of the inputs it ran from.
//!
//! Recording never fails a run: the run's results are already committed,
//! and a history that could not be written is logged and the analysis
//! stands.

use radical_store::rows::{self, Analysis, AnalysisRun, ModelElement};
use radical_store::{Timestamp, rusqlite};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::State;
use crate::routes::analysis::{analysis_payload, measurements, rows_to_tree};

/// Runs kept per analysis.
pub const RUNS_KEPT: usize = 3;

/// The code a run ran on: APP_COMMIT where the build set it.
pub fn app_commit() -> Option<&'static str> {
    option_env!("APP_COMMIT")
        .map(str::trim)
        .filter(|c| !c.is_empty())
}

/// The fields of a date that are what it was run from.
const INPUT_FIELDS: [&str; 25] = [
    "id",
    "sample_name",
    "age_bp",
    "error_1sigma",
    "material_type",
    "sample_type",
    "curve_id",
    "delta_r",
    "delta_r_error",
    "delta_r_source",
    "latitude",
    "longitude",
    "location",
    "notes",
    "depth_cm",
    "ring_offset",
    "ring_offset_error",
    "inbuilt_years",
    "inbuilt_shape",
    "outlier_prior",
    "outlier_model",
    "group_id",
    "element_id",
    "tpq_age_bp",
    "taq_age_bp",
];

/// A model tree with every posterior taken off: inputs only.
pub fn strip_results(e: &Value) -> Value {
    let mut out = serde_json::Map::new();
    for (k, v) in e.as_object().into_iter().flatten() {
        if k != "posterior" && k != "children" {
            out.insert(k.clone(), v.clone());
        }
    }
    out.insert(
        "children".to_owned(),
        Value::Array(
            e["children"]
                .as_array()
                .into_iter()
                .flatten()
                .map(strip_results)
                .collect(),
        ),
    );
    Value::Object(out)
}

/// What an analysis was run from: its settings, its dates, its model
/// (`project_export.analysis_inputs`).
pub fn analysis_inputs(
    conn: &rusqlite::Connection,
    analysis: &Analysis,
) -> radical_store::Result<Value> {
    let elements: Vec<ModelElement> =
        rows::select_where(conn, "analysis_id = ?", [&analysis.id], "rowid")?;
    let dates: Vec<Value> = measurements(conn, &analysis.id)?
        .iter()
        .map(|s| {
            let all = crate::routes::analysis::measurement_json(s);
            let mut row = serde_json::Map::new();
            for f in INPUT_FIELDS {
                row.insert(f.to_owned(), all.get(f).cloned().unwrap_or(Value::Null));
            }
            Value::Object(row)
        })
        .collect();
    Ok(json!({
        "id": analysis.id,
        "name": analysis.name,
        "analysis_type": analysis.analysis_type,
        "bayesian_config": analysis.bayesian_config.clone().filter(|v| !v.is_null()).unwrap_or_else(|| json!({})),
        "measurements": dates,
        "model_tree": rows_to_tree(&elements).map_or(Value::Null, |t| strip_results(&t)),
    }))
}

/// Python's `json.dumps(value, sort_keys=True)`: keys sorted, `", "` and
/// `": "` between items, non-ASCII escaped, floats as `repr` writes them.
pub fn py_json(value: &Value) -> String {
    let mut out = String::new();
    write_py(value, &mut out);
    out
}

fn write_py(value: &Value, out: &mut String) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => match (n.as_i64(), n.as_u64()) {
            (Some(i), _) => out.push_str(&i.to_string()),
            (_, Some(u)) => out.push_str(&u.to_string()),
            _ => {
                let x = n.as_f64().unwrap();
                out.push_str(&match x {
                    x if x.is_nan() => "NaN".to_owned(),
                    x if x.is_infinite() => {
                        if x > 0.0 { "Infinity" } else { "-Infinity" }.to_owned()
                    }
                    x => radical_core::pyfmt::repr(x),
                });
            }
        },
        Value::String(s) => {
            out.push('"');
            for c in s.chars() {
                match c {
                    '"' => out.push_str("\\\""),
                    '\\' => out.push_str("\\\\"),
                    '\n' => out.push_str("\\n"),
                    '\r' => out.push_str("\\r"),
                    '\t' => out.push_str("\\t"),
                    '\u{08}' => out.push_str("\\b"),
                    '\u{0c}' => out.push_str("\\f"),
                    c if (c as u32) < 0x20 || (c as u32) > 0x7e => {
                        let mut buf = [0u16; 2];
                        for unit in c.encode_utf16(&mut buf) {
                            out.push_str(&format!("\\u{unit:04x}"));
                        }
                    }
                    c => out.push(c),
                }
            }
            out.push('"');
        }
        Value::Array(a) => {
            out.push('[');
            for (i, v) in a.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_py(v, out);
            }
            out.push(']');
        }
        Value::Object(o) => {
            let mut keys: Vec<&String> = o.keys().collect();
            keys.sort();
            out.push('{');
            for (i, k) in keys.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_py(&Value::String((*k).clone()), out);
                out.push_str(": ");
                write_py(&o[*k], out);
            }
            out.push('}');
        }
    }
}

/// Python's `json.dumps(value, indent=indent)`: one item a line, `": "`
/// between key and value, non-ASCII escaped. Keys in serde_json's order.
pub fn py_json_indented(value: &Value, indent: usize) -> String {
    let mut out = String::new();
    write_indented(value, indent, 0, &mut out);
    out
}

fn write_indented(value: &Value, indent: usize, depth: usize, out: &mut String) {
    let pad = |d: usize| " ".repeat(indent * d);
    match value {
        Value::Array(a) if !a.is_empty() => {
            out.push('[');
            for (i, v) in a.iter().enumerate() {
                out.push_str(if i > 0 { ",\n" } else { "\n" });
                out.push_str(&pad(depth + 1));
                write_indented(v, indent, depth + 1, out);
            }
            out.push('\n');
            out.push_str(&pad(depth));
            out.push(']');
        }
        Value::Object(o) if !o.is_empty() => {
            out.push('{');
            for (i, (k, v)) in o.iter().enumerate() {
                out.push_str(if i > 0 { ",\n" } else { "\n" });
                out.push_str(&pad(depth + 1));
                write_py(&Value::String(k.clone()), out);
                out.push_str(": ");
                write_indented(v, indent, depth + 1, out);
            }
            out.push('\n');
            out.push_str(&pad(depth));
            out.push('}');
        }
        other => write_py(other, out),
    }
}

/// A fingerprint of what a run was computed from, and not of the
/// analysis's name, which changes nothing.
pub fn inputs_hash(
    conn: &rusqlite::Connection,
    analysis: &Analysis,
) -> radical_store::Result<String> {
    let mut inputs = analysis_inputs(conn, analysis)?;
    inputs.as_object_mut().unwrap().remove("name");
    let digest = Sha256::digest(py_json(&inputs).as_bytes());
    Ok(digest.iter().take(8).map(|b| format!("{b:02x}")).collect())
}

/// Keep the analysis's just-finished run, and drop any beyond RUNS_KEPT.
pub fn record_run(state: &State, analysis_id: &str) {
    let result = (|| -> Result<(), String> {
        let analysis: Option<Analysis> = state
            .store
            .read(|c| rows::get(c, analysis_id))
            .map_err(|e| e.to_string())?;
        let Some(analysis) = analysis.filter(|a| a.status.as_deref() == Some("completed")) else {
            return Ok(());
        };
        let payload =
            analysis_payload(state, analysis_id).map_err(|e| format!("{:?}", e.detail))?;
        state
            .store
            .write(|tx| {
                let run = AnalysisRun {
                    id: radical_store::new_id(),
                    analysis_id: analysis_id.to_owned(),
                    created_at: Timestamp::now(),
                    app_commit: app_commit().map(str::to_owned),
                    inputs_hash: Some(inputs_hash(tx, &analysis)?),
                    payload,
                };
                rows::insert(tx, &run)?;
                let runs: Vec<AnalysisRun> = rows::select_where(
                    tx,
                    "analysis_id = ?",
                    [analysis_id],
                    "created_at DESC, id DESC",
                )?;
                for stale in runs.iter().skip(RUNS_KEPT) {
                    rows::delete_where::<AnalysisRun>(tx, "id = ?", [&stale.id])?;
                }
                Ok(())
            })
            .map_err(|e| e.to_string())
    })();
    if let Err(e) = result {
        eprintln!("Could not record the run of analysis {analysis_id}: {e}");
    }
}
