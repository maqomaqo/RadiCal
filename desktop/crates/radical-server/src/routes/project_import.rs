//! Reading a project package back in as a new project
//! (`api/project_import.py`): its date collection, every analysis's inputs,
//! the curves they need, and the run each result came from.
//!
//! A preview that stores nothing and lists what is wrong, then the import,
//! which reads the same file again. All or nothing. What arrives is inputs:
//! each analysis lands pending and is run here. Curves are matched by their
//! numbers, not their names.

use std::collections::{HashMap, HashSet};

use axum::Router;
use axum::body::Bytes;
use axum::extract::State as Extract;
use axum::http::{HeaderMap, StatusCode, header};
use axum::routing::post;
use radical_core::CalibrationCurve;
use radical_core::curve_mix::mix;
use radical_core::curve_registry::{
    BUILTINS, StoredNames, builtin_spec, content_hash, resolve_alias,
};
use radical_core::measurement::Curves;
use radical_io::curve_import::{Calendar, parse_curve};
use radical_io::date_import::{ImportResult, parse_upload};
use radical_io::ids::RandomIds;
use radical_store::Timestamp;
use radical_store::rows::{
    self, Analysis, AnalysisRun, CalibrationCurve as CurveRow, Project, ProjectSample,
};
use radical_store::rusqlite::Connection;
use serde_json::{Map, Value, json};

use crate::accounts::Caller;
use crate::form::{self, Form};
use crate::history::inputs_hash;
use crate::routes::analysis::{persist_tree, walk_tree};
use crate::routes::project_export::{PACKAGE_FORMAT, PACKAGE_VERSION};
use crate::routes::results::library;
use crate::routes::runs::sample_row;
use crate::schemas::{self, BayesianRequest, Measurement};
use crate::solve::relabel_tree;
use crate::validate::{Fields, Problems};
use crate::{ApiError, ApiResult, State, ok};

const MAX_PACKAGE_BYTES: usize = 100 * 1024 * 1024;
const MAX_MEMBERS: usize = 20000;
const ANALYSIS_TYPES: [&str; 3] = ["calibration", "group_calibration", "bayesian"];
/// What a date on a curve the import has yet to store is validated under.
const PLACEHOLDER_CURVE: &str = "intcal20";

struct PlanCurve {
    record: Value,
    /// 'shipped', 'reuse' or 'add'.
    action: &'static str,
    local_id: Option<String>,
    curve: Option<CalibrationCurve>,
}

struct PlanAnalysis {
    name: String,
    analysis_type: String,
    request: BayesianRequest,
    /// The stored outlier_prior and outlier_model, kept as sent.
    outliers: Vec<(Value, Value)>,
    curve_ids: Vec<String>,
    model_tree: Option<Value>,
    config: Value,
    run: Option<Value>,
    source_id: Option<String>,
}

#[derive(Default)]
struct Plan {
    errors: Vec<String>,
    warnings: Vec<String>,
    notes: Vec<String>,
    manifest: Value,
    curves: Vec<(String, PlanCurve)>,
    analyses: Vec<PlanAnalysis>,
    samples: Vec<(Measurement, Option<String>, String)>,
    /// Whose import this is: whose curves it may reuse and name, and whose
    /// the project, analyses and curves it makes are. None on the desktop.
    owner: Option<String>,
}

impl Plan {
    fn scope(&self) -> crate::owner::Scope {
        crate::owner::Scope::of(&match &self.owner {
            Some(id) => Caller::User {
                id: id.clone(),
                access_token: None,
            },
            None => Caller::Local,
        })
    }

    fn curve(&self, id: &str) -> Option<&PlanCurve> {
        self.curves.iter().find(|(c, _)| c == id).map(|(_, p)| p)
    }

    fn set_curve(&mut self, id: &str, curve: PlanCurve) {
        match self.curves.iter_mut().find(|(c, _)| c == id) {
            Some((_, slot)) => *slot = curve,
            None => self.curves.push((id.to_owned(), curve)),
        }
    }

    fn project_name(&self) -> String {
        self.manifest["project"]["name"]
            .as_str()
            .filter(|n| !n.is_empty())
            .unwrap_or("Imported project")
            .to_owned()
    }

    fn summary(&self) -> Value {
        json!({
            "ok": self.errors.is_empty(),
            "errors": self.errors,
            "warnings": self.warnings,
            "notes": self.notes,
            "project_name": self.project_name(),
            "project_description": self.manifest["project"]["description"],
            "exported_at": self.manifest["exported_at"],
            "app_commit": self.manifest["app_commit"],
            "format_version": self.manifest.get("format_version").cloned().unwrap_or(json!(0)),
            "samples": self.samples.len(),
            "analyses": self.analyses.iter().map(|a| json!({
                "name": a.name,
                "analysis_type": a.analysis_type,
                "measurements": a.request.measurements.len(),
                "has_run": a.run.is_some(),
            })).collect::<Vec<_>>(),
            "curves": self.curves.iter().map(|(id, c)| json!({
                "id": id,
                "name": c.record["name"].as_str().filter(|n| !n.is_empty()).unwrap_or(id),
                "kind": c.record["kind"],
                "action": c.action,
            })).collect::<Vec<_>>(),
        })
    }
}

// --------------------------------------------------------------------------
// Reading the zip
// --------------------------------------------------------------------------

fn open(content: &[u8], plan: &mut Plan) -> Option<HashMap<String, Vec<u8>>> {
    if content.is_empty() {
        plan.errors.push("The file is empty.".to_owned());
        return None;
    }
    let members = match crate::zip::read(content) {
        Ok(m) => m,
        Err(e) if e == "File is not a zip file" => {
            plan.errors.push(
                "This is not a project package. Use a .zip saved from a project page (Export) in RadiCal.".to_owned(),
            );
            return None;
        }
        Err(_) => {
            plan.errors
                .push("The package is too large to import.".to_owned());
            return None;
        }
    };
    if members.len() > MAX_MEMBERS {
        plan.errors
            .push("The package is too large to import.".to_owned());
        return None;
    }
    let names: HashSet<&str> = members.iter().map(|(n, _)| n.as_str()).collect();
    if !names.contains("manifest.json") || !names.iter().any(|n| n.starts_with("inputs/")) {
        plan.errors.push(
            "This zip is not a RadiCal project package: it has no manifest.json and inputs/ folder. Use a .zip saved from a project page (Export).".to_owned(),
        );
        return None;
    }
    let wanted = ["manifest.json", "samples.json", "samples.csv"];
    Some(
        members
            .into_iter()
            .filter(|(n, _)| {
                wanted.contains(&n.as_str())
                    || (n.ends_with(".json")
                        && matches!(n.split('/').next(), Some("inputs" | "runs" | "curves")))
            })
            .collect(),
    )
}

fn read_json(files: &HashMap<String, Vec<u8>>, name: &str, plan: &mut Plan) -> Option<Value> {
    let text = match std::str::from_utf8(&files[name]) {
        Ok(t) => t,
        Err(e) => {
            plan.errors.push(format!("{name} could not be read: {e}."));
            return None;
        }
    };
    match serde_json::from_str(text) {
        Ok(v) => Some(v),
        Err(e) => {
            plan.errors.push(format!("{name} could not be read: {e}."));
            None
        }
    }
}

/// Pydantic's errors as short sentences, located where that helps
/// (`_describe`).
fn describe(problems: &Problems) -> Vec<String> {
    problems
        .0
        .iter()
        .map(|p| {
            let at: Vec<String> = p["loc"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|l| match l {
                    Value::String(s) => s.clone(),
                    other => other.to_string(),
                })
                .collect();
            let msg = p["msg"].as_str().unwrap_or("invalid");
            let msg = msg.strip_prefix("Value error, ").unwrap_or(msg);
            if at.is_empty() {
                msg.to_owned()
            } else {
                format!("{}: {msg}", at.join("."))
            }
        })
        .collect()
}

// --------------------------------------------------------------------------
// Curves
// --------------------------------------------------------------------------

fn calendar(options: &Value) -> Calendar {
    if options["calendar"] == "bc_ad" {
        Calendar::BcAd
    } else {
        Calendar::CalBp
    }
}

/// The numbers a carried curve stands for (`_build_curve`).
fn build_curve(state: &State, record: &Value, plan: &Plan) -> Result<CalibrationCurve, String> {
    if record["kind"] == "mixed" {
        let recipe = &record["recipe"];
        let mut parents: Vec<&CalibrationCurve> = Vec::new();
        for key in ["a", "b"] {
            let id = recipe[key].as_str().unwrap_or_default();
            let Some(parent) = plan.curve(id) else {
                return Err(format!(
                    "it mixes '{}', which the package does not carry",
                    recipe[key].as_str().unwrap_or("None")
                ));
            };
            match (&parent.curve, &parent.local_id) {
                (Some(c), _) => parents.push(c),
                (None, Some(local)) => {
                    parents.push(state.curves.curve(local).map_err(|e| e.to_string())?);
                }
                (None, None) => return Err(format!("it mixes '{id}', which is not available")),
            }
        }
        return mix(
            parents[0],
            parents[1],
            recipe["percent_a"].as_f64().unwrap_or(50.0),
            recipe["percent_a_error"].as_f64().unwrap_or(0.0),
            record["name"]
                .as_str()
                .filter(|n| !n.is_empty())
                .unwrap_or("Mixed"),
        )
        .map_err(|e| e.to_string());
    }
    let options = &record["parse_options"];
    let columns = options["columns"].as_object().map(|c| {
        let at = |k: &str| c.get(k).and_then(Value::as_u64).unwrap_or(0) as usize;
        [at("cal_bp"), at("c14_age"), at("error")]
    });
    let result = parse_curve(
        record["source_text"].as_str().unwrap_or_default(),
        calendar(options),
        columns,
    );
    if let Some(first) = result.errors.first() {
        return Err(first.clone());
    }
    let (cal, rest): (Vec<f64>, Vec<(f64, f64)>) =
        result.points.iter().map(|p| (p.0, (p.1, p.2))).unzip();
    let (c14, err): (Vec<f64>, Vec<f64>) = rest.into_iter().unzip();
    Ok(CalibrationCurve::new("", cal, c14, err))
}

fn is_builtin(id: &str) -> bool {
    BUILTINS.iter().any(|b| b.id == id)
}

/// The importing account's stored curves.
fn stored_curves(state: &State, plan: &Plan) -> radical_store::Result<Vec<CurveRow>> {
    let mine = plan.scope().sql("created_by");
    state
        .store
        .read(|c| rows::select_where(c, &mine, [], "created_at, rowid"))
}

fn plan_curves(
    state: &State,
    files: &HashMap<String, Vec<u8>>,
    plan: &mut Plan,
) -> radical_store::Result<()> {
    let manifest_curves = plan.manifest["curves"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    for entry in manifest_curves {
        if entry["kind"] != "builtin" {
            continue;
        }
        let id = entry["id"].as_str().unwrap_or_default().to_owned();
        if !is_builtin(&id) {
            plan.errors.push(format!(
                "The package names a shipped curve '{}' that this installation does not have.",
                entry["id"].as_str().unwrap_or("None")
            ));
            continue;
        }
        let name = entry["name"]
            .as_str()
            .filter(|n| !n.is_empty())
            .unwrap_or(&id)
            .to_owned();
        let sha = entry["content_sha256"]
            .as_str()
            .unwrap_or_default()
            .to_owned();
        plan.set_curve(
            &id,
            PlanCurve {
                record: entry,
                action: "shipped",
                local_id: Some(id.clone()),
                curve: None,
            },
        );
        let local = builtin_spec(&id)
            .map(|s| s.measured.content_sha256)
            .unwrap_or_default();
        if !sha.is_empty() && sha != local {
            plan.warnings.push(format!(
                "{name} here is not the same file the package was made with, so results may differ from the author's."
            ));
        }
    }

    let mut names: Vec<&String> = files.keys().filter(|n| n.starts_with("curves/")).collect();
    names.sort();
    let mut records = Vec::new();
    for name in names {
        if let Some(r) = read_json(files, name, plan)
            && r.is_object()
            && r["id"].as_str().is_some_and(|i| !i.is_empty())
        {
            records.push(r);
        }
    }
    let by_hash: HashMap<String, String> = stored_curves(state, plan)?
        .into_iter()
        .map(|r| (r.content_sha256, r.id))
        .collect();

    let mut pending = records;
    while !pending.is_empty() {
        let mut progressed = false;
        let mut i = 0;
        while i < pending.len() {
            let record = pending[i].clone();
            let mixed = record["kind"] == "mixed";
            let parent = |k: &str| record["recipe"][k].as_str().unwrap_or_default().to_owned();
            if mixed
                && !["a", "b"]
                    .iter()
                    .all(|k| plan.curve(&parent(k)).is_some() || is_builtin(&parent(k)))
            {
                i += 1;
                continue;
            }
            if mixed {
                for k in ["a", "b"] {
                    let p = parent(k);
                    if plan.curve(&p).is_none() {
                        plan.set_curve(
                            &p,
                            PlanCurve {
                                record: json!({"id": p, "kind": "builtin", "name": p}),
                                action: "shipped",
                                local_id: Some(p.clone()),
                                curve: None,
                            },
                        );
                    }
                }
            }
            pending.remove(i);
            progressed = true;
            let id = record["id"].as_str().unwrap_or_default().to_owned();
            let label = record["name"]
                .as_str()
                .filter(|n| !n.is_empty())
                .unwrap_or(&id)
                .to_owned();
            let built = match build_curve(state, &record, plan) {
                Ok(c) => c,
                Err(e) => {
                    plan.errors
                        .push(format!("The curve '{label}' could not be read: {e}."));
                    continue;
                }
            };
            let digest = content_hash(&built);
            if record["content_sha256"].as_str() != Some(digest.as_str()) {
                plan.errors.push(format!(
                    "The curve '{label}' does not match its own checksum, so the package has been altered or damaged. It will not be imported."
                ));
                continue;
            }
            let (action, local_id) = match by_hash.get(&digest) {
                Some(local) => ("reuse", Some(local.clone())),
                None => ("add", None),
            };
            plan.set_curve(
                &id,
                PlanCurve {
                    record,
                    action,
                    local_id,
                    curve: Some(built),
                },
            );
        }
        if !progressed {
            for record in &pending {
                let label = record["name"]
                    .as_str()
                    .filter(|n| !n.is_empty())
                    .or(record["id"].as_str())
                    .unwrap_or_default();
                plan.errors.push(format!(
                    "The curve '{label}' mixes a curve the package does not carry."
                ));
            }
            break;
        }
    }
    Ok(())
}

/// The id a date's curve is validated under (`_curve_for`); None, with an
/// error, for a curve neither carried nor here.
fn curve_for(state: &State, curve_id: &str, plan: &mut Plan, at: &str) -> Option<String> {
    if plan.curve(curve_id).is_none() {
        if is_builtin(curve_id) {
            plan.set_curve(
                curve_id,
                PlanCurve {
                    record: json!({"id": curve_id, "kind": "builtin", "name": curve_id}),
                    action: "shipped",
                    local_id: Some(curve_id.to_owned()),
                    curve: None,
                },
            );
        } else if state
            .store
            .read(|c| rows::get::<CurveRow>(c, curve_id))
            .ok()
            .flatten()
            .is_some_and(|r| plan.owner.is_none() || r.created_by == plan.owner)
        {
            plan.set_curve(
                curve_id,
                PlanCurve {
                    record: json!({"id": curve_id, "kind": "stored", "name": curve_id}),
                    action: "reuse",
                    local_id: Some(curve_id.to_owned()),
                    curve: None,
                },
            );
        } else {
            plan.errors.push(format!(
                "{at} uses the curve '{curve_id}', which the package does not carry. Import that curve on the Curves page first."
            ));
            return None;
        }
    }
    let known = plan.curve(curve_id).expect("set above");
    if known.action == "add" {
        Some(PLACEHOLDER_CURVE.to_owned())
    } else {
        known.local_id.clone()
    }
}

// --------------------------------------------------------------------------
// Dates and analyses
// --------------------------------------------------------------------------

/// The fields `ProjectSampleInput` has.
const SAMPLE_INPUT_FIELDS: [&str; 22] = [
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
    "reservoir_age",
    "depth_cm",
    "ring_offset",
    "ring_offset_error",
    "inbuilt_years",
    "inbuilt_shape",
    "location",
    "notes",
    "tpq_age_bp",
    "taq_age_bp",
    "default_context_name",
];

fn plan_samples(state: &State, files: &HashMap<String, Vec<u8>>, plan: &mut Plan) -> ApiResult<()> {
    let records: Vec<Value> = if files.contains_key("samples.json") {
        read_json(files, "samples.json", plan)
            .and_then(|v| v.as_array().cloned())
            .unwrap_or_default()
    } else if let Some(csv) = files.get("samples.csv") {
        let library = library(state, &plan.scope())?;
        let parsed = parse_upload("samples.csv", csv, &library, &mut RandomIds::default());
        if !parsed.errors.is_empty() {
            plan.errors.extend(
                parsed
                    .errors
                    .iter()
                    .filter(|e| *e != "No dates found in the file.")
                    .map(|e| format!("samples.csv: {e}")),
            );
            return Ok(());
        }
        parsed
            .dates
            .iter()
            .map(|d| {
                let mut v = ImportResult::date_json(d);
                v["default_context_name"] = v["phase_name"].clone();
                v
            })
            .collect()
    } else {
        return Ok(());
    };
    for (index, record) in records.iter().enumerate() {
        let index = index + 1;
        if !record.is_object() {
            plan.errors
                .push(format!("Collection date {index} is not a date."));
            continue;
        }
        let label = record["sample_name"]
            .as_str()
            .filter(|n| !n.is_empty())
            .map_or_else(|| index.to_string(), str::to_owned);
        let at = format!("Collection date '{label}'");
        let curve_id = record["curve_id"]
            .as_str()
            .filter(|c| !c.is_empty())
            .unwrap_or("intcal20")
            .to_owned();
        let Some(validated) = curve_for(state, &curve_id, plan, &at) else {
            continue;
        };
        let mut fields: Map<String, Value> = record
            .as_object()
            .unwrap()
            .iter()
            .filter(|(k, v)| SAMPLE_INPUT_FIELDS.contains(&k.as_str()) && !v.is_null())
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        fields.insert("curve_id".to_owned(), json!(validated));
        let input = Value::Object(fields);
        let mut problems = Problems::default();
        let mut f = Fields::new(&input, vec![], "ProjectSampleInput", &mut problems);
        let parsed = schemas::project_sample_input(&mut f, &state.store);
        match parsed {
            Some((m, context)) if problems.0.is_empty() => {
                plan.samples.push((m, context, curve_id));
            }
            _ => plan.errors.extend(
                describe(&problems)
                    .into_iter()
                    .map(|m| format!("{at}: {m}")),
            ),
        }
    }
    Ok(())
}

/// The analysis's settings, each known one validated and each unknown one
/// kept (`_merged_config`).
fn merged_config(stored: &Value) -> Result<Value, Problems> {
    let mut problems = Problems::default();
    let mut f = Fields::new(stored, vec![], "BayesianConfigInput", &mut problems);
    let validated = schemas::bayesian_config(&mut f);
    match validated {
        Some(v) if problems.0.is_empty() => Ok(Value::Object(
            stored
                .as_object()
                .into_iter()
                .flatten()
                .map(|(k, value)| {
                    (
                        k.clone(),
                        v.get(k).cloned().unwrap_or_else(|| value.clone()),
                    )
                })
                .collect(),
        )),
        _ => Err(problems),
    }
}

fn plan_analysis(state: &State, stem: &str, inputs: &Value, run: Option<Value>, plan: &mut Plan) {
    let label = inputs["name"]
        .as_str()
        .filter(|n| !n.is_empty())
        .unwrap_or(stem)
        .to_owned();
    let at = format!("Analysis '{label}'");
    let analysis_type = inputs["analysis_type"]
        .as_str()
        .unwrap_or_default()
        .to_owned();
    if !ANALYSIS_TYPES.contains(&analysis_type.as_str()) {
        let shown = match &inputs["analysis_type"] {
            Value::Null => "None".to_owned(),
            Value::String(s) => radical_io::python::repr_str(s),
            other => other.to_string(),
        };
        plan.errors
            .push(format!("{at} has an unknown type, {shown}."));
        return;
    }
    let dates = inputs["measurements"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    if dates.is_empty() {
        plan.errors.push(format!("{at} has no dates."));
        return;
    }
    let (mut curve_ids, mut outliers, mut rows_in) = (Vec::new(), Vec::new(), Vec::new());
    for (index, m) in dates.iter().enumerate() {
        let Some(obj) = m.as_object() else {
            plan.errors
                .push(format!("{at}: date {} is not a date.", index + 1));
            return;
        };
        let curve_id = m["curve_id"]
            .as_str()
            .filter(|c| !c.is_empty())
            .unwrap_or("intcal20")
            .to_owned();
        let date_name = match &m["sample_name"] {
            Value::Null => "None".to_owned(),
            Value::String(s) => s.clone(),
            other => other.to_string(),
        };
        let Some(validated) =
            curve_for(state, &curve_id, plan, &format!("{at}, date '{date_name}'"))
        else {
            return;
        };
        curve_ids.push(curve_id);
        outliers.push((m["outlier_prior"].clone(), m["outlier_model"].clone()));
        let mut row: Map<String, Value> = obj
            .iter()
            .filter(|(k, v)| !v.is_null() && k.as_str() != "group_id")
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        let id = match &m["id"] {
            Value::String(s) if !s.is_empty() => s.clone(),
            Value::Null => format!("date-{index}"),
            Value::String(_) => format!("date-{index}"),
            other => other.to_string(),
        };
        row.insert("id".to_owned(), json!(id));
        row.insert("curve_id".to_owned(), json!(validated));
        rows_in.push(Value::Object(row));
    }
    let tree = inputs.get("model_tree").cloned().filter(|t| !t.is_null());
    if tree.is_none() {
        for row in &mut rows_in {
            row.as_object_mut().unwrap().remove("element_id");
        }
    }
    let stored_config = inputs
        .get("bayesian_config")
        .cloned()
        .filter(|c| !c.is_null() && c.as_object().is_none_or(|o| !o.is_empty()))
        .unwrap_or_else(|| json!({}));
    let config = match merged_config(&stored_config) {
        Ok(c) => c,
        Err(problems) => {
            plan.errors.extend(
                describe(&problems)
                    .into_iter()
                    .map(|m| format!("{at}: {m}")),
            );
            return;
        }
    };
    let body = json!({
        "name": label,
        "measurements": rows_in,
        "model_tree": tree,
        "bayesian_config": config,
        "analysis_type": analysis_type,
    });
    let mut problems = Problems::default();
    let mut f = Fields::new(&body, vec![], "BayesianRequest", &mut problems);
    let request = schemas::bayesian_request(&mut f, &state.store);
    let Some(request) = request.filter(|_| problems.0.is_empty()) else {
        plan.errors.extend(
            describe(&problems)
                .into_iter()
                .map(|m| format!("{at}: {m}")),
        );
        return;
    };
    plan.analyses.push(PlanAnalysis {
        name: label,
        analysis_type,
        request,
        outliers,
        curve_ids,
        model_tree: tree,
        config,
        run: run.filter(|r| r.is_object() && r["payload"].is_object()),
        source_id: inputs["id"].as_str().map(str::to_owned),
    });
}

/// Read a package and decide everything importing it would do, storing
/// nothing (`plan_import`).
fn plan_import(state: &State, caller: &Caller, content: &[u8]) -> ApiResult<Plan> {
    let mut plan = Plan {
        owner: caller.owner().map(str::to_owned),
        ..Plan::default()
    };
    let Some(files) = open(content, &mut plan) else {
        return Ok(plan);
    };
    let manifest = read_json(&files, "manifest.json", &mut plan);
    let Some(manifest) = manifest.filter(Value::is_object) else {
        plan.errors
            .push("manifest.json is not a package manifest.".to_owned());
        return Ok(plan);
    };
    plan.manifest = manifest;
    if plan
        .manifest
        .get("format")
        .is_some_and(|f| f != PACKAGE_FORMAT)
    {
        plan.errors
            .push("This zip is not a RadiCal project package.".to_owned());
        return Ok(plan);
    }
    let version = plan
        .manifest
        .get("format_version")
        .cloned()
        .unwrap_or(json!(0));
    let Some(version) = version.as_i64().filter(|v| *v <= PACKAGE_VERSION) else {
        plan.errors.push(
            "This package was made by a newer version of RadiCal. Update RadiCal to import it."
                .to_owned(),
        );
        return Ok(plan);
    };
    if version == 0 {
        plan.notes.push(
            "This package was made before packages carried their curves and runs. Its analyses import without the author's results to compare against.".to_owned(),
        );
    }
    plan_curves(state, &files, &mut plan)?;
    plan_samples(state, &files, &mut plan)?;
    let mut inputs: Vec<&String> = files.keys().filter(|n| n.starts_with("inputs/")).collect();
    inputs.sort();
    for name in inputs {
        let Some(record) = read_json(&files, name, &mut plan).filter(Value::is_object) else {
            continue;
        };
        let stem = name.split_once('/').map_or("", |(_, s)| s);
        let run_name = format!("runs/{stem}");
        let run = if files.contains_key(&run_name) {
            read_json(&files, &run_name, &mut plan)
        } else {
            None
        };
        plan_analysis(state, stem, &record, run, &mut plan);
    }
    if plan.analyses.is_empty() && plan.samples.is_empty() && plan.errors.is_empty() {
        plan.errors
            .push("The package holds no analyses and no dates.".to_owned());
    }
    let precision = plan.manifest["coordinate_precision"]
        .as_str()
        .filter(|p| !p.is_empty())
        .unwrap_or("full");
    if precision != "full"
        && plan
            .analyses
            .iter()
            .any(|a| a.config["estimate_dispersal"] == true)
    {
        plan.warnings.push(
            "Positions in this package were rounded before it was shared, so spatial results may differ from the author's.".to_owned(),
        );
    }
    let added: Vec<String> = plan
        .curves
        .iter()
        .filter(|(_, c)| c.action == "add")
        .map(|(id, c)| {
            c.record["name"]
                .as_str()
                .filter(|n| !n.is_empty())
                .unwrap_or(id)
                .to_owned()
        })
        .collect();
    if !added.is_empty() {
        plan.notes.push(format!(
            "{} curve{} will be added to your curves: {}.",
            added.len(),
            if added.len() != 1 { "s" } else { "" },
            added.join(", ")
        ));
    }
    let without_run = plan.analyses.iter().filter(|a| a.run.is_none()).count();
    if version > 0 && without_run > 0 {
        plan.notes.push(format!(
            "{without_run} of {} analyses have no result from the author to compare against (not run, or edited since).",
            plan.analyses.len()
        ));
    }
    Ok(plan)
}

// --------------------------------------------------------------------------
// Storing it
// --------------------------------------------------------------------------

/// Every id in a stored run rewritten to the import's: whole strings, keys,
/// and colon-joined pairs (`_swap_ids`).
fn swap_ids(value: &Value, mapping: &HashMap<String, String>) -> Value {
    let swap = |s: &str| -> String {
        if let Some(m) = mapping.get(s) {
            return m.clone();
        }
        if s.contains(':') && s.split(':').any(|p| mapping.contains_key(p)) {
            return s
                .split(':')
                .map(|p| mapping.get(p).map_or(p, String::as_str))
                .collect::<Vec<_>>()
                .join(":");
        }
        s.to_owned()
    };
    match value {
        Value::String(s) => Value::String(swap(s)),
        Value::Object(o) => Value::Object(
            o.iter()
                .map(|(k, v)| (swap(k), swap_ids(v, mapping)))
                .collect(),
        ),
        Value::Array(a) => Value::Array(a.iter().map(|v| swap_ids(v, mapping)).collect()),
        other => other.clone(),
    }
}

/// When a run was made, from its isoformat, its zone dropped; now when
/// unreadable (`_when`).
fn when(text: Option<&str>) -> Timestamp {
    let Some(text) = text else {
        return Timestamp::now();
    };
    let bare = text
        .split_once('+')
        .map_or(text, |(t, _)| t)
        .trim_end_matches('Z');
    Timestamp::parse(bare).unwrap_or_else(Timestamp::now)
}

fn store_curves(
    tx: &Connection,
    plan: &mut Plan,
    names: &[StoredNames],
) -> radical_store::Result<()> {
    let mut taken: HashSet<String> = rows::select_where::<CurveRow>(tx, "1 = 1", [], "rowid")?
        .into_iter()
        .map(|r| r.id)
        .chain(BUILTINS.iter().map(|b| b.id.to_owned()))
        .collect();
    let local_ids: HashMap<String, Option<String>> = plan
        .curves
        .iter()
        .map(|(id, c)| (id.clone(), c.local_id.clone()))
        .collect();
    for (_, curve) in plan.curves.iter_mut() {
        if curve.action != "add" {
            continue;
        }
        let record = curve.record.clone();
        let base = record["id"].as_str().unwrap_or_default().to_owned();
        let local_id = (1..1000)
            .map(|n| {
                if n == 1 {
                    base.clone()
                } else {
                    format!("{base}-{n}")
                }
            })
            .find(|c| !taken.contains(c) && resolve_alias(c, names).is_none())
            .unwrap_or_else(|| format!("{base}-{}", radical_store::new_id()));
        taken.insert(local_id.clone());
        let aliases: Vec<Value> = record["aliases"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|a| {
                a.as_str()
                    .is_some_and(|a| resolve_alias(a, names).is_none())
            })
            .cloned()
            .collect();
        let mut name = record["name"]
            .as_str()
            .filter(|n| !n.is_empty())
            .unwrap_or(&local_id)
            .to_owned();
        if resolve_alias(&name, names).is_some() {
            name = format!("{name} (imported)");
        }
        let recipe = record.get("recipe").filter(|r| truthy(r)).map(|r| {
            let mut r = r.clone();
            for k in ["a", "b"] {
                let parent = r[k].as_str().unwrap_or_default().to_owned();
                r[k] = json!(local_ids.get(&parent).cloned().flatten());
            }
            r
        });
        let text = |k: &str| record[k].as_str().map(str::to_owned);
        let now = Some(Timestamp::now());
        rows::insert(
            tx,
            &CurveRow {
                created_by: plan.owner.clone(),
                id: local_id.clone(),
                kind: text("kind")
                    .filter(|k| !k.is_empty())
                    .unwrap_or_else(|| "imported".to_owned()),
                name,
                description: text("description"),
                reference: text("reference"),
                doi: text("doi"),
                curve_type: text("type")
                    .filter(|t| !t.is_empty())
                    .unwrap_or_else(|| "terrestrial".to_owned()),
                sample_type: text("sample_type")
                    .filter(|t| !t.is_empty())
                    .unwrap_or_else(|| "terrestrial".to_owned()),
                source_text: text("source_text"),
                source_filename: text("source_filename"),
                parse_options: record
                    .get("parse_options")
                    .cloned()
                    .filter(|v| !v.is_null()),
                recipe,
                content_sha256: text("content_sha256").unwrap_or_default(),
                aliases: Some(Value::Array(aliases)),
                archived: false,
                created_at: now,
                updated_at: now,
            },
        )?;
        curve.local_id = Some(local_id);
    }
    Ok(())
}

fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

fn store_analysis(
    tx: &Connection,
    item: &PlanAnalysis,
    project: &Project,
    plan: &Plan,
) -> radical_store::Result<()> {
    let r = &item.request;
    let analysis_type = if item.analysis_type == "calibration" && r.measurements.len() > 1 {
        "group_calibration".to_owned()
    } else {
        item.analysis_type.clone()
    };
    let now = Some(Timestamp::now());
    let analysis = Analysis {
        id: radical_store::new_id(),
        name: item.name.clone(),
        analysis_type,
        status: Some("pending".to_owned()),
        created_at: now,
        updated_at: now,
        project_id: Some(project.id.clone()),
        bayesian_config: Some(item.config.clone()),
        owner_id: plan.owner.clone(),
        ..Analysis::default()
    };
    rows::insert(tx, &analysis)?;
    let mut element_map: HashMap<String, String> = HashMap::new();
    let mut tree = item.model_tree.clone();
    if let Some(t) = &tree {
        let mut nodes = Vec::new();
        walk_tree(t, &mut nodes);
        for e in nodes {
            if let Some(id) = e["id"].as_str() {
                element_map.insert(id.to_owned(), radical_store::new_id());
            }
        }
    }
    let mut samples_map: HashMap<String, String> = HashMap::new();
    for (position, ((m, curve_id), (prior, model))) in r
        .measurements
        .iter()
        .zip(&item.curve_ids)
        .zip(&item.outliers)
        .enumerate()
    {
        let element = m
            .element_id
            .as_ref()
            .and_then(|e| element_map.get(e))
            .cloned();
        let mut row = sample_row(&analysis.id, m, None, element, position as i64);
        row.curve_id = plan.curve(curve_id).and_then(|c| c.local_id.clone());
        row.outlier_prior = prior.as_f64();
        row.outlier_model = model.as_str().map(str::to_owned);
        rows::insert(tx, &row)?;
        samples_map.insert(m.id.clone(), row.id);
    }
    if let Some(t) = &mut tree {
        relabel_tree(t, &element_map, &samples_map);
        persist_tree(tx, &analysis.id, t)?;
    }
    if let Some(run) = &item.run {
        let mut mapping: HashMap<String, String> = element_map.clone();
        mapping.extend(samples_map);
        if let Some(source) = &item.source_id {
            mapping.insert(source.clone(), analysis.id.clone());
        }
        if let Some(source) = plan.manifest["project"]["id"].as_str() {
            mapping.insert(source.to_owned(), project.id.clone());
        }
        rows::insert(
            tx,
            &AnalysisRun {
                id: radical_store::new_id(),
                analysis_id: analysis.id.clone(),
                created_at: when(run["created_at"].as_str()),
                app_commit: run["app_commit"].as_str().map(str::to_owned),
                inputs_hash: Some(inputs_hash(tx, &analysis)?),
                payload: swap_ids(&run["payload"], &mapping),
            },
        )?;
    }
    Ok(())
}

fn commit_import(state: &State, plan: &mut Plan, name: &str) -> radical_store::Result<Project> {
    let precision = plan.manifest["coordinate_precision"]
        .as_str()
        .filter(|p| matches!(*p, "full" | "0.01" | "0.1" | "1"))
        .unwrap_or("full")
        .to_owned();
    let now = Some(Timestamp::now());
    let project = Project {
        owner_id: plan.owner.clone(),
        id: radical_store::new_id(),
        name: name.to_owned(),
        description: plan.manifest["project"]["description"]
            .as_str()
            .map(str::to_owned),
        created_at: now,
        updated_at: now,
        coordinate_precision: precision,
        map_tiles_enabled: false,
    };
    let names: Vec<StoredNames> = stored_curves(state, plan)?
        .iter()
        .map(|r| StoredNames {
            id: r.id.clone(),
            name: r.name.clone(),
            aliases: r
                .aliases
                .as_ref()
                .and_then(Value::as_array)
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(str::to_owned))
                        .collect()
                })
                .unwrap_or_default(),
        })
        .collect();
    state.store.write(|tx| {
        rows::insert(tx, &project)?;
        store_curves(tx, plan, &names)?;
        for (m, context, curve_id) in &plan.samples {
            let local = plan.curve(curve_id).and_then(|c| c.local_id.clone());
            let sample_type = m.sample_type.clone().unwrap_or_else(|| {
                if local.as_deref() == Some("marine20") {
                    "marine"
                } else {
                    "terrestrial"
                }
                .to_owned()
            });
            rows::insert(
                tx,
                &ProjectSample {
                    id: radical_store::new_id(),
                    project_id: project.id.clone(),
                    sample_name: m.sample_name.clone(),
                    age_bp: m.age_bp,
                    error_1sigma: m.error_1sigma,
                    material_type: m.material_type.clone(),
                    sample_type,
                    curve_id: local,
                    reservoir_age: m.reservoir_age,
                    depth_cm: m.depth_cm,
                    location: m.location.clone(),
                    notes: m.notes.clone(),
                    default_context_name: context.clone(),
                    tpq_age_bp: m.tpq_age_bp,
                    taq_age_bp: m.taq_age_bp,
                    created_at: Some(Timestamp::now()),
                    updated_at: Some(Timestamp::now()),
                    delta_r: m.delta_r,
                    delta_r_error: m.delta_r_error,
                    ring_offset: m.ring_offset,
                    ring_offset_error: m.ring_offset_error,
                    latitude: m.latitude,
                    longitude: m.longitude,
                    delta_r_source: m.delta_r_source.clone().map(Value::Object),
                    inbuilt_years: m.inbuilt_years,
                    inbuilt_shape: m.inbuilt_shape.clone(),
                },
            )?;
        }
        for item in &plan.analyses {
            store_analysis(tx, item, &project, plan)?;
        }
        Ok(())
    })?;
    for (_, c) in &plan.curves {
        if let Some(id) = &c.local_id {
            crate::routes::curve_routes::forget(state, id);
        }
    }
    Ok(project)
}

// --------------------------------------------------------------------------
// Routes
// --------------------------------------------------------------------------

fn package(headers: &HeaderMap, body: &Bytes) -> ApiResult<(Form, Vec<u8>, String)> {
    let form = Form::parse(
        headers
            .get(header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok()),
        body,
    );
    let Some(file) = form.file("file") else {
        return Err(form::missing(&["file"]));
    };
    if file.data.len() > MAX_PACKAGE_BYTES {
        return Err(ApiError::new(
            StatusCode::PAYLOAD_TOO_LARGE,
            format!(
                "The file is {} MB; the limit is {} MB.",
                file.data.len() / (1024 * 1024),
                MAX_PACKAGE_BYTES / (1024 * 1024)
            ),
        ));
    }
    let (data, filename) = (file.data.clone(), file.filename.clone().unwrap_or_default());
    Ok((form, data, filename))
}

/// What a project package holds and what importing it would do; 200 for a
/// package with problems, its answer being `data.errors`.
async fn preview(
    Extract(state): Extract<State>,
    caller: Caller,
    headers: HeaderMap,
    body: Bytes,
) -> ApiResult {
    let (_, content, filename) = package(&headers, &body)?;
    let plan = tokio::task::spawn_blocking(move || plan_import(&state, &caller, &content))
        .await
        .map_err(ApiError::internal)??;
    let mut data = plan.summary();
    data["filename"] = json!(filename);
    Ok(ok(data))
}

/// Import a project package as a new project, refused whole if anything in
/// it is wrong. Every analysis arrives pending.
async fn import_project(
    Extract(state): Extract<State>,
    caller: Caller,
    headers: HeaderMap,
    body: Bytes,
) -> ApiResult {
    let (form, content, _) = package(&headers, &body)?;
    let name = form.text("name");
    let result = tokio::task::spawn_blocking(move || -> ApiResult<serde_json::Value> {
        let mut plan = plan_import(&state, &caller, &content)?;
        if !plan.errors.is_empty() {
            return Err(ApiError {
                status: StatusCode::UNPROCESSABLE_ENTITY,
                detail: json!({"message": "The package could not be imported.", "errors": plan.errors}),
            });
        }
        let name = name
            .map(|n| n.trim().to_owned())
            .filter(|n| !n.is_empty())
            .unwrap_or_else(|| plan.project_name());
        let name: String = name.chars().take(200).collect();
        let project = commit_import(&state, &mut plan, &name).map_err(|e| {
            ApiError::bad_request(format!("The package could not be imported: {e}"))
        })?;
        Ok(json!({
            "project_id": project.id,
            "name": project.name,
            "analyses": plan.analyses.len(),
            "samples": plan.samples.len(),
            "curves_added": plan.curves.iter().filter(|(_, c)| c.action == "add").count(),
            "with_author_run": plan.analyses.iter().filter(|a| a.run.is_some()).count(),
        }))
    })
    .await
    .map_err(ApiError::internal)??;
    Ok(ok(result))
}

pub fn router() -> Router<State> {
    Router::new()
        .route("/api/import/project/preview", post(preview))
        .route("/api/import/project", post(import_project))
}
