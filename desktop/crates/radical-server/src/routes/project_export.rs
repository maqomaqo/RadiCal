//! A whole project as one zip (`api/project_export.py`): every analysis's
//! Export JSON, what each was run from, the run its results came from, the
//! curves they need, the date collection and a summary table across them.
//! The same zip is the package another installation imports.
//!
//! Read-only, like every export: an analysis with no stored tree exports
//! without one rather than being migrated here.

use axum::extract::{Path, Query, State as Extract};
use axum::http::{StatusCode, header};
use axum::response::{IntoResponse, Response};
use radical_core::date_format::{self, era_label, format_age};
use radical_store::rows::{
    self, Analysis, AnalysisRun, CalibrationCurve as CurveRow, Project, ProjectSample,
};
use radical_store::rusqlite::Connection;
use serde_json::{Map, Value, json};

use crate::accounts::Caller;
use crate::history::{
    self, analysis_inputs, inputs_hash, py_json, py_json_indented, strip_results,
};
use crate::routes::analysis::{measurements, migrated_tree, rows_to_tree, walk_tree};
use crate::routes::results::{
    hpd_cell, json_payload, p_before_all, rounded_coordinate, threshold, years_ranges,
};
use crate::{ApiError, ApiResult, State};

pub const PACKAGE_FORMAT: &str = "radical-project";
pub const PACKAGE_VERSION: i64 = 1;

/// The collection as the CSV importer reads it: (column, field).
const SAMPLE_COLUMNS: [(&str, &str); 21] = [
    ("sample_name", "sample_name"),
    ("age_bp", "age_bp"),
    ("error_1sigma", "error_1sigma"),
    ("material_type", "material_type"),
    ("curve_id", "curve_id"),
    ("phase", "default_context_name"),
    ("delta_r", "delta_r"),
    ("delta_r_error", "delta_r_error"),
    ("tpq_age_bp", "tpq_age_bp"),
    ("taq_age_bp", "taq_age_bp"),
    ("latitude", "latitude"),
    ("longitude", "longitude"),
    ("notes", "notes"),
    ("sample_type", "sample_type"),
    ("location", "location"),
    ("depth_cm", "depth_cm"),
    ("ring_offset", "ring_offset"),
    ("ring_offset_error", "ring_offset_error"),
    ("inbuilt_years", "inbuilt_years"),
    ("inbuilt_shape", "inbuilt_shape"),
    ("delta_r_source", "delta_r_source"),
];

/// Every field a collected date is stored with.
pub const COLLECTION_FIELDS: [&str; 21] = [
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
    "depth_cm",
    "ring_offset",
    "ring_offset_error",
    "inbuilt_years",
    "inbuilt_shape",
    "location",
    "notes",
    "default_context_name",
    "tpq_age_bp",
    "taq_age_bp",
];

/// A stored curve's fields, as a package carries it.
pub const CURVE_FIELDS: [&str; 14] = [
    "id",
    "kind",
    "name",
    "description",
    "reference",
    "doi",
    "type",
    "sample_type",
    "aliases",
    "source_filename",
    "source_text",
    "parse_options",
    "recipe",
    "content_sha256",
];

const SUMMARY_HEADER: [&str; 20] = [
    "Analysis",
    "Analysis ID",
    "Type",
    "Status",
    "Solver",
    "Converged",
    "Convergence",
    "R-hat",
    "Sweeps",
    "Seed",
    "Cross-check",
    "A_model",
    "A_overall",
    "log10 evidence",
    "Quantity",
    "Name",
    "Median BP",
    "95.4% HPD BP",
    "Median (yr)",
    "95.4% Range (yr)",
];

/// A name safe in a file name everywhere (`_slug`).
pub fn slug(text: &str, limit: usize) -> String {
    let mut out = String::new();
    let mut gap = false;
    for c in text.chars() {
        if c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-') {
            out.push(c);
            gap = false;
        } else if !gap {
            out.push('-');
            gap = true;
        }
    }
    let s = out.trim_matches(|c| c == '-' || c == '.');
    let s = if s.is_empty() { "analysis" } else { s };
    s.chars().take(limit).collect()
}

fn number(v: &Value, places: usize) -> String {
    v.as_f64()
        .map(|x| format!("{x:.places$}"))
        .unwrap_or_default()
}

/// Every latitude and longitude in a JSON value, at the project's
/// precision (`_round_positions`).
pub fn round_positions(value: &Value, precision: &str) -> Value {
    if precision == "full" {
        return value.clone();
    }
    match value {
        Value::Object(o) => Value::Object(
            o.iter()
                .map(|(k, v)| {
                    let v = match (k.as_str(), v.as_f64()) {
                        ("latitude" | "longitude", Some(x)) if v.is_number() => {
                            json!(rounded_coordinate(Some(x), precision))
                        }
                        _ => round_positions(v, precision),
                    };
                    (k.clone(), v)
                })
                .collect(),
        ),
        Value::Array(a) => Value::Array(a.iter().map(|v| round_positions(v, precision)).collect()),
        other => other.clone(),
    }
}

/// What rebuilding an analysis elsewhere needs (`package_inputs`): its
/// inputs, each date's ΔR provenance, and the structure of an analysis
/// stored before model trees, built without being written.
pub fn package_inputs(conn: &Connection, analysis: &Analysis) -> radical_store::Result<Value> {
    let mut inputs = analysis_inputs(conn, analysis)?;
    let samples = measurements(conn, &analysis.id)?;
    if let Some(rows) = inputs["measurements"].as_array_mut() {
        for (row, s) in rows.iter_mut().zip(&samples) {
            row["delta_r_source"] = s.delta_r_source.clone().unwrap_or(Value::Null);
        }
    }
    if inputs["model_tree"].is_null()
        && analysis.analysis_type == "bayesian"
        && let Some((root, _, element_ids)) = migrated_tree(conn, &analysis.id, &samples)?
    {
        inputs["model_tree"] = strip_results(&root);
        if let Some(rows) = inputs["measurements"].as_array_mut() {
            for row in rows {
                let id = row["id"].as_str().unwrap_or_default().to_owned();
                row["element_id"] = json!(element_ids.get(&id));
            }
        }
    }
    Ok(inputs)
}

/// The run the analysis's current results came from, while it still
/// describes the analysis (`author_run`).
pub fn author_run(conn: &Connection, analysis: &Analysis) -> radical_store::Result<Option<Value>> {
    if analysis.status.as_deref() != Some("completed") {
        return Ok(None);
    }
    let runs: Vec<AnalysisRun> = rows::select_where(
        conn,
        "analysis_id = ?",
        [&analysis.id],
        "created_at DESC, id DESC",
    )?;
    let Some(run) = runs.into_iter().next() else {
        return Ok(None);
    };
    if run.inputs_hash.as_deref() != Some(inputs_hash(conn, analysis)?.as_str()) {
        return Ok(None);
    }
    Ok(Some(json!({
        "created_at": run.created_at.isoformat(),
        "app_commit": run.app_commit,
        "payload": run.payload,
    })))
}

/// A stored curve as a package carries it.
pub fn curve_record(r: &CurveRow) -> Value {
    json!({
        "id": r.id, "kind": r.kind, "name": r.name, "description": r.description,
        "reference": r.reference, "doi": r.doi, "type": r.curve_type,
        "sample_type": r.sample_type, "aliases": r.aliases,
        "source_filename": r.source_filename, "source_text": r.source_text,
        "parse_options": r.parse_options, "recipe": r.recipe,
        "content_sha256": r.content_sha256,
    })
}

/// Every curve these ids need, a mixture's parents first; a builtin named
/// with its hash only (`curve_records`).
fn curve_records(state: &State, ids: &[String]) -> ApiResult<Vec<Value>> {
    let mut sorted: Vec<&String> = ids.iter().filter(|c| !c.is_empty()).collect();
    sorted.sort();
    sorted.dedup();
    let mut records: Vec<(String, Value)> = Vec::new();
    fn visit(
        state: &State,
        id: &str,
        depth: usize,
        records: &mut Vec<(String, Value)>,
    ) -> ApiResult<()> {
        if id.is_empty() || records.iter().any(|(r, _)| r == id) || depth > 8 {
            return Ok(());
        }
        if let Some(b) = radical_core::curve_registry::BUILTINS
            .iter()
            .find(|b| b.id == id)
        {
            let Ok(spec) = radical_core::curve_registry::builtin_spec(b.id) else {
                return Ok(());
            };
            records.push((
                id.to_owned(),
                json!({"id": id, "kind": "builtin", "name": b.name,
                       "content_sha256": spec.measured.content_sha256}),
            ));
            return Ok(());
        }
        let Some(row): Option<CurveRow> = state.store.read(|c| rows::get(c, id))? else {
            return Ok(());
        };
        if radical_core::measurement::Curves::curve(&*state.curves, id).is_err() {
            return Ok(());
        }
        let recipe = row.recipe.clone().unwrap_or_default();
        for parent in [&recipe["a"], &recipe["b"]] {
            if let Some(p) = parent.as_str() {
                visit(state, p, depth + 1, records)?;
            }
        }
        records.push((id.to_owned(), curve_record(&row)));
        Ok(())
    }
    for id in sorted {
        visit(state, id, 0, &mut records)?;
    }
    Ok(records.into_iter().map(|(_, r)| r).collect())
}

fn sample_value(s: &ProjectSample, field: &str) -> Value {
    match field {
        "sample_name" => json!(s.sample_name),
        "age_bp" => json!(s.age_bp),
        "error_1sigma" => json!(s.error_1sigma),
        "material_type" => json!(s.material_type),
        "sample_type" => json!(s.sample_type),
        "curve_id" => json!(s.curve_id),
        "delta_r" => json!(s.delta_r),
        "delta_r_error" => json!(s.delta_r_error),
        "delta_r_source" => s.delta_r_source.clone().unwrap_or(Value::Null),
        "latitude" => json!(s.latitude),
        "longitude" => json!(s.longitude),
        "depth_cm" => json!(s.depth_cm),
        "ring_offset" => json!(s.ring_offset),
        "ring_offset_error" => json!(s.ring_offset_error),
        "inbuilt_years" => json!(s.inbuilt_years),
        "inbuilt_shape" => json!(s.inbuilt_shape),
        "location" => json!(s.location),
        "notes" => json!(s.notes),
        "default_context_name" => json!(s.default_context_name),
        "tpq_age_bp" => json!(s.tpq_age_bp),
        "taq_age_bp" => json!(s.taq_age_bp),
        _ => Value::Null,
    }
}

/// The collection with every stored field (`collection_records`).
pub fn collection_records(samples: &[ProjectSample], precision: &str) -> Value {
    let records: Vec<Value> = samples
        .iter()
        .map(|s| {
            Value::Object(
                COLLECTION_FIELDS
                    .iter()
                    .map(|f| ((*f).to_owned(), sample_value(s, f)))
                    .collect::<Map<_, _>>(),
            )
        })
        .collect();
    round_positions(&Value::Array(records), precision)
}

/// A collection value as its CSV cell: 614 rather than 614.0, structures as
/// compact sorted JSON (`_cell`).
fn sample_cell(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::Object(_) | Value::Array(_) => py_json(v).replace(", ", ",").replace(": ", ":"),
        Value::Number(n) => match n.as_f64() {
            Some(x) if n.is_f64() && x.fract() == 0.0 && x.is_finite() => format!("{}", x as i64),
            Some(x) if n.is_f64() => radical_core::pyfmt::repr(x),
            _ => n.to_string(),
        },
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True" } else { "False" }.to_owned(),
    }
}

/// Python's csv writer, excel dialect.
fn csv_line(cells: &[String]) -> String {
    let quoted: Vec<String> = cells
        .iter()
        .map(|c| {
            if (cells.len() == 1 && c.is_empty()) || c.contains([',', '"', '\r', '\n']) {
                format!("\"{}\"", c.replace('"', "\"\""))
            } else {
                c.clone()
            }
        })
        .collect();
    format!("{}\r\n", quoted.join(","))
}

/// The collection as a CSV the date importer reads straight back in
/// (`samples_csv`).
pub fn samples_csv(samples: &[ProjectSample], precision: &str) -> String {
    let mut out = csv_line(
        &SAMPLE_COLUMNS
            .iter()
            .map(|(c, _)| (*c).to_owned())
            .collect::<Vec<_>>(),
    );
    for record in collection_records(samples, precision)
        .as_array()
        .into_iter()
        .flatten()
    {
        out.push_str(&csv_line(
            &SAMPLE_COLUMNS
                .iter()
                .map(|(_, f)| sample_cell(&record[*f]))
                .collect::<Vec<_>>(),
        ));
    }
    out
}

fn solver_cells(analysis: &Analysis) -> Vec<String> {
    let solver = analysis.solver.clone().unwrap_or(Value::Null);
    let method = if analysis.analysis_type != "bayesian" {
        "calibration".to_owned()
    } else {
        solver["method"].as_str().unwrap_or_default().to_owned()
    };
    let converged = &solver["converged"];
    let sweeps = &solver["sweeps"];
    vec![
        method,
        match converged.as_bool() {
            None => String::new(),
            Some(true) => "yes".to_owned(),
            Some(false) => "no".to_owned(),
        },
        number(&solver["convergence"], 3),
        number(&solver["rhat"], 3),
        if sweeps.as_f64().is_some_and(|x| x != 0.0) {
            sweeps.to_string()
        } else {
            String::new()
        },
        if solver["seed"].is_null() {
            String::new()
        } else {
            solver["seed"].to_string()
        },
        solver["cross_check"]["status"]
            .as_str()
            .unwrap_or_default()
            .to_owned(),
        number(&solver["model_agreement"], 1),
        number(&solver["overall_agreement"], 1),
        number(&solver["evidence"]["log10"], 3),
    ]
}

/// Python's `str.capitalize()`.
fn capitalize(s: &str) -> String {
    let mut chars = s.chars();
    match chars.next() {
        None => String::new(),
        Some(first) => first
            .to_uppercase()
            .chain(chars.flat_map(char::to_lowercase))
            .collect(),
    }
}

fn duration_kind(kind: &Value) -> String {
    match kind.as_str() {
        Some("sequence_duration") => "Sequence duration".to_owned(),
        Some("phase_duration") => "Phase duration".to_owned(),
        Some("hiatus_duration") => "Hiatus duration".to_owned(),
        Some("sample_duration") => "Dated events duration".to_owned(),
        Some(other) => other.to_owned(),
        None => String::new(),
    }
}

/// The analysis's Boundaries and other modelled elements in reading order,
/// then its durations; one bare row when it has neither (`summary_rows`).
fn summary_rows(
    conn: &Connection,
    analysis: &Analysis,
    date_format: &str,
    p_values: Option<&Value>,
) -> radical_store::Result<Vec<Vec<String>>> {
    let era = era_label(date_format).is_some();
    let mut head = vec![
        analysis.name.clone(),
        analysis.id.clone(),
        analysis.analysis_type.clone(),
        analysis.status.clone().unwrap_or_else(|| "None".to_owned()),
    ];
    head.extend(solver_cells(analysis));
    let extra = |posterior: Option<&Value>, element_id: Option<&str>| {
        let mut cells = Vec::new();
        if era {
            match posterior {
                Some(p) => {
                    cells.push(format_age(p["posterior_median"].as_f64(), date_format));
                    cells.push(date_format::format_hpd_ranges(
                        &crate::routes::results::ranges_of(&p["hpd_95_ranges"]),
                        date_format,
                    ));
                }
                None => cells.extend([String::new(), String::new()]),
            }
        }
        if let Some(v) = p_values {
            let p = element_id.and_then(|id| v["elements"][id].as_f64());
            cells.push(p.map(|p| format!("{p:.6}")).unwrap_or_default());
        }
        cells
    };
    let elements: Vec<radical_store::rows::ModelElement> =
        rows::select_where(conn, "analysis_id = ?", [&analysis.id], "rowid")?;
    let mut rows_out = Vec::new();
    if let Some(tree) = rows_to_tree(&elements) {
        let mut ordered = Vec::new();
        walk_tree(&tree, &mut ordered);
        for e in ordered {
            let posterior = &e["posterior"];
            if !posterior["probability_distribution"]
                .as_array()
                .is_some_and(|a| !a.is_empty())
            {
                continue;
            }
            let kind = capitalize(
                &e["element_type"]
                    .as_str()
                    .filter(|t| !t.is_empty())
                    .unwrap_or("element")
                    .replace('_', " "),
            );
            let mut row = head.clone();
            row.extend([
                kind,
                e["name"].as_str().unwrap_or_default().to_owned(),
                number(&posterior["posterior_median"], 0),
                hpd_cell(&posterior["hpd_95_ranges"], &Value::Null, &Value::Null),
                String::new(),
                String::new(),
            ]);
            row.extend(extra(Some(posterior), e["id"].as_str()));
            rows_out.push(row);
        }
    }
    let intervals = analysis.intervals.clone().unwrap_or(Value::Null);
    for r in intervals["durations"].as_array().into_iter().flatten() {
        let y = &r["years"];
        let mut row = head.clone();
        row.extend([
            duration_kind(&r["kind"]),
            r["name"].as_str().unwrap_or_default().to_owned(),
            String::new(),
            String::new(),
            number(&y["median"], 0),
            years_ranges(&y["hpd_95_ranges"]),
        ]);
        row.extend(extra(None, None));
        rows_out.push(row);
    }
    if rows_out.is_empty() {
        let mut row = head;
        row.extend(vec![String::new(); 6]);
        row.extend(extra(None, None));
        rows_out.push(row);
    }
    Ok(rows_out)
}

/// The project as a zip (`build_zip`).
pub fn build_zip(
    state: &State,
    project: Option<&Project>,
    analyses: &[Analysis],
    samples: &[ProjectSample],
    date_format: Option<&str>,
    p_before_bp: Option<f64>,
    selection: bool,
) -> ApiResult<Vec<u8>> {
    let precision = project
        .map(|p| p.coordinate_precision.clone())
        .filter(|p| !p.is_empty())
        .unwrap_or_else(|| "full".to_owned());
    let date_format = date_format::normalise(date_format);
    let t = threshold(p_before_bp)?;
    let mut header: Vec<String> = SUMMARY_HEADER.iter().map(|s| (*s).to_owned()).collect();
    if let Some(era) = era_label(date_format) {
        header.push(format!("Median ({era})"));
        header.push(format!("95.4% HPD ({era})"));
    }
    if let Some(t) = t {
        header.push(format!("P Before {}", format_age(Some(t), date_format)));
    }
    let mut summary = csv_line(&header);
    let mut zip = crate::zip::Writer::default();
    let mut listed = Vec::new();
    let mut curve_ids: Vec<String> = samples.iter().filter_map(|s| s.curve_id.clone()).collect();
    for (index, analysis) in analyses.iter().enumerate() {
        let id8: String = analysis.id.chars().take(8).collect();
        let stem = format!("{:03}-{}-{id8}.json", index + 1, slug(&analysis.name, 60));
        // Chosen by owner already (export_project): no second check.
        let exported =
            json!({"success": true, "data": json_payload(state, &Caller::Local, &analysis.id, t)?});
        zip.add(
            &format!("analyses/{stem}"),
            py_json_indented(&exported, 1).as_bytes(),
        );
        let (inputs, run, dates) = state.store.read(|c| {
            Ok((
                package_inputs(c, analysis)?,
                author_run(c, analysis)?,
                measurements(c, &analysis.id)?,
            ))
        })?;
        zip.add(
            &format!("inputs/{stem}"),
            py_json_indented(&round_positions(&inputs, &precision), 1).as_bytes(),
        );
        if let Some(run) = &run {
            zip.add(
                &format!("runs/{stem}"),
                py_json_indented(&round_positions(run, &precision), 1).as_bytes(),
            );
        }
        curve_ids.extend(dates.iter().filter_map(|s| s.curve_id.clone()));
        let p_values = match t {
            Some(t) => Some(p_before_all(state, &analysis.id, t)?),
            None => None,
        };
        for row in state
            .store
            .read(|c| summary_rows(c, analysis, date_format, p_values.as_ref()))?
        {
            summary.push_str(&csv_line(&row));
        }
        listed.push(json!({
            "id": analysis.id, "name": analysis.name,
            "analysis_type": analysis.analysis_type, "status": analysis.status,
            "file": stem, "measurements": dates.len(), "has_run": run.is_some(),
        }));
    }
    let curves = curve_records(state, &curve_ids)?;
    for record in &curves {
        if record["kind"] != "builtin" {
            zip.add(
                &format!("curves/{}.json", record["id"].as_str().unwrap_or_default()),
                py_json_indented(record, 1).as_bytes(),
            );
        }
    }
    zip.add("summary.csv", summary.as_bytes());
    zip.add("samples.csv", samples_csv(samples, &precision).as_bytes());
    zip.add(
        "samples.json",
        py_json_indented(&collection_records(samples, &precision), 1).as_bytes(),
    );
    let now = radical_store::Timestamp::now().isoformat();
    let manifest = json!({
        "format": PACKAGE_FORMAT,
        "format_version": PACKAGE_VERSION,
        "app_commit": history::app_commit(),
        "project": match project {
            Some(p) => json!({"id": p.id, "name": p.name, "description": p.description}),
            None => json!({"id": null, "name": "Unassigned", "description": null}),
        },
        "selection": selection,
        "coordinate_precision": precision,
        "curves": curves.iter().map(|r| json!({
            "id": r["id"], "kind": r["kind"], "name": r["name"],
            "content_sha256": r["content_sha256"],
        })).collect::<Vec<_>>(),
        "exported_at": format!("{}+00:00", &now[..19]),
        "date_format": date_format,
        "p_before_bp": t,
        "reporting_level": radical_core::SIGMA_2,
        "analyses": listed,
        "samples": samples.len(),
        "notes": "analyses/ holds each analysis exactly as its Results page Export > JSON gives it; inputs/ what it was run from; runs/ the run its results came from; curves/ every curve used that RadiCal does not ship. Projects > Import project reads this file back in. Calendar ages are cal BP unless a column says otherwise; durations are in years.",
    });
    zip.add("manifest.json", py_json_indented(&manifest, 1).as_bytes());
    Ok(zip.finish())
}

fn query_list<'a>(q: &'a [(String, String)], name: &str) -> Vec<&'a str> {
    q.iter()
        .filter(|(k, _)| k == name)
        .map(|(_, v)| v.as_str())
        .collect()
}

fn query_float(q: &[(String, String)], name: &str) -> ApiResult<Option<f64>> {
    let Some((_, raw)) = q.iter().find(|(k, _)| k == name) else {
        return Ok(None);
    };
    radical_io::python::float(raw).map(Some).ok_or_else(|| {
        ApiError::invalid(vec![crate::problem(
            "float_parsing",
            &[json!("query"), json!(name)],
            "Input should be a valid number, unable to parse string as a number",
            json!(raw),
            None,
        )])
    })
}

/// GET /projects/{id}/export: the project, or the chosen analyses of it, as
/// one zip; `unassigned` for the analyses filed under no project.
pub async fn export_project(
    Extract(state): Extract<State>,
    caller: Caller,
    Path(project_id): Path<String>,
    Query(q): Query<Vec<(String, String)>>,
) -> ApiResult<Response> {
    let p_before = query_float(&q, "p_before_bp")?;
    let date_format = q
        .iter()
        .find(|(k, _)| k == "date_format")
        .map(|(_, v)| v.clone());
    let chosen: Vec<String> = query_list(&q, "analysis")
        .into_iter()
        .map(str::to_owned)
        .collect();
    let unassigned = project_id == "unassigned";
    let project = if unassigned {
        if chosen.is_empty() {
            return Err(ApiError::new(
                StatusCode::UNPROCESSABLE_ENTITY,
                "Choose the unassigned analyses to export.",
            ));
        }
        None
    } else {
        Some(crate::routes::projects::require(
            &state,
            &caller,
            &project_id,
        )?)
    };
    let mine = crate::owner::Scope::of(&caller).sql("owner_id");
    let analyses: Vec<Analysis> = state.store.read(|c| {
        if unassigned {
            rows::select_where(
                c,
                &format!("project_id IS NULL AND {mine}"),
                [],
                "created_at, id",
            )
        } else {
            rows::select_where(
                c,
                &format!("project_id = ? AND {mine}"),
                [&project_id],
                "created_at, id",
            )
        }
    })?;
    let analyses: Vec<Analysis> = if chosen.is_empty() {
        analyses
    } else {
        analyses
            .into_iter()
            .filter(|a| chosen.contains(&a.id))
            .collect()
    };
    if !chosen.is_empty() {
        let mut missing: Vec<&String> = chosen
            .iter()
            .filter(|c| !analyses.iter().any(|a| &&a.id == c))
            .collect();
        missing.sort();
        missing.dedup();
        if !missing.is_empty() {
            return Err(ApiError::not_found(format!(
                "{} of the chosen analyses are not in this project.",
                missing.len()
            )));
        }
    }
    let mut samples: Vec<ProjectSample> = match &project {
        Some(_) => state
            .store
            .read(|c| rows::select_where(c, "project_id = ?", [&project_id], "created_at, id"))?,
        None => Vec::new(),
    };
    if project.is_some() && !chosen.is_empty() {
        let mut used = Vec::new();
        for a in &analyses {
            for m in state.store.read(|c| measurements(c, &a.id))? {
                used.push(m.sample_name.trim().to_lowercase());
            }
        }
        samples.retain(|s| used.contains(&s.sample_name.trim().to_lowercase()));
    }
    let worker = state.clone();
    let project_for_zip = project.clone();
    let selection = !chosen.is_empty();
    let content = tokio::task::spawn_blocking(move || {
        build_zip(
            &worker,
            project_for_zip.as_ref(),
            &analyses,
            &samples,
            date_format.as_deref(),
            p_before,
            selection,
        )
        .map(|bytes| (bytes, analyses.len()))
    })
    .await
    .map_err(ApiError::internal)??;
    let (content, count) = content;
    let name = slug(
        project.as_ref().map_or("unassigned", |p| p.name.as_str()),
        60,
    );
    let stem = if selection {
        format!(
            "project-{name}-{count}-{}",
            if count == 1 { "analysis" } else { "analyses" }
        )
    } else {
        format!("project-{name}")
    };
    Ok((
        [
            (header::CONTENT_TYPE, "application/zip".to_owned()),
            (
                header::CONTENT_DISPOSITION,
                format!("attachment; filename={stem}.zip"),
            ),
        ],
        content,
    )
        .into_response())
}

/// GET /projects/{id}/samples/export: the collection, or the chosen dates
/// of it, as a CSV the importer reads back.
pub async fn export_samples(
    Extract(state): Extract<State>,
    caller: Caller,
    Path(project_id): Path<String>,
    Query(q): Query<Vec<(String, String)>>,
) -> ApiResult<Response> {
    let project = crate::routes::projects::require(&state, &caller, &project_id)?;
    let chosen: Vec<&str> = query_list(&q, "sample");
    let samples: Vec<ProjectSample> = state
        .store
        .read(|c| rows::select_where(c, "project_id = ?", [&project_id], "created_at, id"))?;
    let samples: Vec<ProjectSample> = if chosen.is_empty() {
        samples
    } else {
        let wanted: std::collections::HashSet<&str> = chosen.iter().copied().collect();
        let picked: Vec<ProjectSample> = samples
            .into_iter()
            .filter(|s| wanted.contains(s.id.as_str()))
            .collect();
        if picked.len() != wanted.len() {
            return Err(ApiError::not_found(
                "Some of the chosen dates are not in this collection.",
            ));
        }
        picked
    };
    let precision = if project.coordinate_precision.is_empty() {
        "full".to_owned()
    } else {
        project.coordinate_precision.clone()
    };
    let name = slug(
        if project.name.is_empty() {
            "project"
        } else {
            &project.name
        },
        60,
    );
    Ok((
        [
            (header::CONTENT_TYPE, "text/csv; charset=utf-8".to_owned()),
            (
                header::CONTENT_DISPOSITION,
                format!("attachment; filename=dates-{name}.csv"),
            ),
        ],
        samples_csv(&samples, &precision),
    )
        .into_response())
}

pub fn router() -> axum::Router<State> {
    axum::Router::new()
        .route(
            "/api/projects/{project_id}/export",
            axum::routing::get(export_project),
        )
        .route(
            "/api/projects/{project_id}/samples/export",
            axum::routing::get(export_samples),
        )
}
