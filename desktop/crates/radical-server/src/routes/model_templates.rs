//! Model templates (`api/model_templates.py`): a model's structure and
//! settings saved without its dates, to apply to another analysis's dates,
//! and carried between installations as a JSON file holding any number of
//! them.
//!
//! Nothing in a template names a date: a wiggle match's reference date is
//! dropped, and every element gets an id of its own.

use std::collections::HashSet;

use axum::Router;
use axum::body::Bytes;
use axum::extract::{Path, Query, State as Extract};
use axum::http::{HeaderMap, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, patch, post};
use radical_core::Measurement;
use radical_core::model_tree::{DATE_HOLDERS, validate_tree};
use radical_store::Timestamp;
use radical_store::rows::{self, ModelTemplate};
use serde_json::{Map, Value, json};

use crate::accounts::Caller;
use crate::form::{self, Form};
use crate::history::py_json_indented;
use crate::routes::analysis::walk_tree;
use crate::schemas;
use crate::solve::element;
use crate::validate::{self, Fields, Problems};
use crate::{ApiError, ApiResult, State, ok};

const FILE_FORMAT: &str = "radical-model-templates";
const FILE_VERSION: i64 = 1;
const MAX_FILE_BYTES: usize = 5 * 1024 * 1024;
const MAX_TEMPLATES_PER_FILE: usize = 500;
const MAX_ELEMENTS: usize = 2000;
const MAX_DEPTH: usize = 50;
const ELEMENT_KEYS: [&str; 5] = ["element_type", "name", "tpq_age_bp", "taq_age_bp", "params"];
const DATE_PARAMS: [&str; 1] = ["reference_sample_id"];

/// The structure alone with fresh ids: no date, no result (`clean_tree`).
fn clean_tree(root: &Value) -> Result<Value, String> {
    fn visit(e: &Value, depth: usize, count: &mut usize) -> Result<Value, String> {
        let Some(obj) = e.as_object() else {
            return Err("every model element must be an object".to_owned());
        };
        *count += 1;
        if *count > MAX_ELEMENTS {
            return Err(format!("a template holds at most {MAX_ELEMENTS} elements"));
        }
        if depth > MAX_DEPTH {
            return Err(format!("a template nests at most {MAX_DEPTH} levels deep"));
        }
        let mut out = Map::new();
        out.insert("id".to_owned(), json!(radical_store::new_id()));
        for key in ELEMENT_KEYS {
            if let Some(v) = obj.get(key).filter(|v| !v.is_null()) {
                out.insert(key.to_owned(), v.clone());
            }
        }
        match out.get("params") {
            Some(Value::Object(p)) => {
                let kept: Map<String, Value> = p
                    .iter()
                    .filter(|(k, _)| !DATE_PARAMS.contains(&k.as_str()))
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .collect();
                if kept.is_empty() {
                    out.remove("params");
                } else {
                    out.insert("params".to_owned(), Value::Object(kept));
                }
            }
            Some(_) => return Err("element params must be an object".to_owned()),
            None => {}
        }
        let children = match obj.get("children") {
            None | Some(Value::Null) => Vec::new(),
            Some(Value::Array(a)) => a.clone(),
            Some(Value::Bool(false)) => Vec::new(),
            Some(_) => return Err("element children must be a list".to_owned()),
        };
        let children = children
            .iter()
            .map(|c| visit(c, depth + 1, count))
            .collect::<Result<Vec<_>, _>>()?;
        out.insert("children".to_owned(), Value::Array(children));
        Ok(Value::Object(out))
    }
    visit(root, 0, &mut 0)
}

/// The structure rules for a tree with no dates yet: a wiggle match or a
/// deposition sequence checked as if it held two (`check_tree`).
fn check_tree(root: &Value) -> Result<(), String> {
    let mut nodes = Vec::new();
    walk_tree(root, &mut nodes);
    let mut stand_ins = Vec::new();
    for e in nodes {
        if matches!(
            e["element_type"].as_str(),
            Some("wiggle_sequence" | "deposition_sequence")
        ) {
            let id = e["id"].as_str().unwrap_or_default();
            for n in 0..2 {
                stand_ins.push(Measurement {
                    id: format!("{id}-{n}"),
                    element_id: Some(id.to_owned()),
                    ..Measurement::default()
                });
            }
        }
    }
    validate_tree(Some(&element(root)), &stand_ins).map_err(|e| e.to_string())
}

/// `pydantic` problems as sentences (`_messages`).
fn messages(problems: &Problems) -> Vec<String> {
    problems
        .0
        .iter()
        .map(|p| {
            let at: Vec<String> = p["loc"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|l| l.as_str().map_or_else(|| l.to_string(), str::to_owned))
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

/// The model settings checked as an analysis's are, less the dispersal
/// origin; None stays None (`clean_config`).
fn clean_config(config: &Value) -> Result<Value, Vec<String>> {
    if config.is_null() {
        return Ok(Value::Null);
    }
    let mut problems = Problems::default();
    let mut f = Fields::new(config, vec![], "BayesianConfigInput", &mut problems);
    let validated = schemas::bayesian_config(&mut f);
    match validated {
        Some(mut v) if problems.0.is_empty() => {
            v.as_object_mut().unwrap().remove("dispersal_origin");
            Ok(v)
        }
        _ => Err(messages(&problems)),
    }
}

type Prepared = (String, Option<String>, Value, Value);

/// A template as sent or read from a file, checked and cleaned
/// (`_prepare`); `loc` is where its fields are reported.
fn prepare(data: &Value, loc: Vec<Value>) -> Result<Prepared, Vec<String>> {
    let mut problems = Problems::default();
    let mut f = Fields::new(data, loc, "TemplateInput", &mut problems);
    let name = f.string("name", Some(1), Some(200)).and_then(|n| {
        let t = n.trim().to_owned();
        if t.is_empty() {
            f.value_error(Some("name"), "a template needs a name");
            None
        } else {
            Some(t)
        }
    });
    let description = f
        .opt_str("description", Some(2000))
        .map(|d| d.trim().to_owned())
        .filter(|d| !d.is_empty());
    let tree = if f.raw("model_tree").is_none() {
        // A required dict: missing is reported as Pydantic reports it.
        f.string("model_tree", None, None);
        None
    } else {
        f.opt_dict("model_tree").map(Value::Object)
    };
    if f.raw("model_tree").is_some_and(Value::is_null) {
        let mut ps = Problems::default();
        ps.0.push(validate::problem(
            "dict_type",
            &[json!("model_tree")],
            "Input should be a valid dictionary",
            Value::Null,
            None,
        ));
        return Err(messages(&ps));
    }
    let config = f.opt_dict("bayesian_config").map(Value::Object);
    if !problems.0.is_empty() {
        return Err(messages(&problems));
    }
    let tree = clean_tree(&tree.unwrap_or_default()).map_err(|e| vec![e])?;
    check_tree(&tree).map_err(|e| vec![e])?;
    let config = clean_config(&config.unwrap_or(Value::Null))?;
    Ok((name.unwrap_or_default(), description, tree, config))
}

fn summary(tree: &Value) -> (usize, usize, usize) {
    let mut nodes = Vec::new();
    walk_tree(tree, &mut nodes);
    let phases = nodes
        .iter()
        .filter(|e| {
            e["element_type"]
                .as_str()
                .is_some_and(|t| DATE_HOLDERS.contains(&t))
        })
        .count();
    let boundaries = nodes
        .iter()
        .filter(|e| e["element_type"] == "boundary")
        .count();
    (phases, boundaries, nodes.len())
}

fn serialize(t: &ModelTemplate) -> Value {
    let (phases, boundaries, elements) = summary(&t.model_tree);
    json!({
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "model_tree": t.model_tree,
        "bayesian_config": t.bayesian_config,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
        "phases": phases,
        "boundaries": boundaries,
        "elements": elements,
    })
}

/// The caller's templates.
fn all(state: &State, caller: &Caller) -> radical_store::Result<Vec<ModelTemplate>> {
    let mine = crate::owner::Scope::of(caller).sql("owner_id");
    state
        .store
        .read(|c| rows::select_where(c, &mine, [], "name, created_at"))
}

/// The template, if it is the caller's.
fn owned(state: &State, caller: &Caller, id: &str) -> ApiResult<ModelTemplate> {
    let row = state.store.read(|c| rows::get::<ModelTemplate>(c, id))?;
    crate::owner::check(caller, row, |t| t.owner_id.as_deref(), "That template")
}

async fn list_templates(Extract(state): Extract<State>, caller: Caller) -> ApiResult {
    Ok(ok(
        json!({"items": all(&state, &caller)?.iter().map(serialize).collect::<Vec<_>>()}),
    ))
}

/// Save a model's structure and settings as a template.
async fn create_template(Extract(state): Extract<State>, caller: Caller, body: Bytes) -> ApiResult {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "TemplateInput", &mut problems);
    // The request is checked as FastAPI checks a body first; the rules the
    // model's own validators state follow in `prepare`.
    let _ = f.string("name", Some(1), Some(200));
    let _ = f.opt_str("description", Some(2000));
    if f.raw("model_tree").is_none() {
        let _ = f.string("model_tree", None, None);
    } else {
        let _ = f.opt_dict("model_tree");
    }
    let _ = f.opt_dict("bayesian_config");
    problems.finish()?;
    let (name, description, tree, config) = prepare(&input, vec![])
        .map_err(|m| ApiError::new(StatusCode::UNPROCESSABLE_ENTITY, m.join("; ")))?;
    let now = Timestamp::now();
    let t = ModelTemplate {
        owner_id: caller.owner().map(str::to_owned),
        id: radical_store::new_id(),
        name,
        description,
        model_tree: tree,
        bayesian_config: Some(config).filter(|c| !c.is_null()),
        created_at: now,
        updated_at: now,
    };
    state.store.write(|tx| rows::insert(tx, &t))?;
    Ok(ok(serialize(&t)))
}

/// Rename a template or change its description; never its structure.
async fn update_template(
    Extract(state): Extract<State>,
    caller: Caller,
    Path(id): Path<String>,
    body: Bytes,
) -> ApiResult {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "TemplateUpdate", &mut problems);
    let name = f.opt_str("name", Some(200));
    if name.as_deref() == Some("") {
        let _ = f.string("name", Some(1), Some(200));
    }
    let description = f.opt_str("description", Some(2000));
    let description_sent = f.sent("description");
    problems.finish()?;
    let mut t = owned(&state, &caller, &id)?;
    if let Some(n) = name {
        if n.trim().is_empty() {
            return Err(ApiError::new(
                StatusCode::UNPROCESSABLE_ENTITY,
                "A template needs a name.",
            ));
        }
        t.name = n.trim().to_owned();
    }
    if description_sent {
        t.description = description
            .map(|d| d.trim().to_owned())
            .filter(|d| !d.is_empty());
    }
    t.updated_at = Timestamp::now();
    state.store.write(|tx| rows::update(tx, &t))?;
    Ok(ok(serialize(&t)))
}

async fn delete_template(
    Extract(state): Extract<State>,
    caller: Caller,
    Path(id): Path<String>,
) -> ApiResult {
    owned(&state, &caller, &id)?;
    state
        .store
        .write(|tx| rows::delete_where::<ModelTemplate>(tx, "id = ?", [&id]))?;
    Ok(ok(json!({"id": id})))
}

/// Templates as one JSON file; `template` (repeatable) chooses which.
async fn export_templates(
    Extract(state): Extract<State>,
    caller: Caller,
    Query(q): Query<Vec<(String, String)>>,
) -> ApiResult<Response> {
    let chosen: HashSet<&str> = q
        .iter()
        .filter(|(k, _)| k == "template")
        .map(|(_, v)| v.as_str())
        .collect();
    let templates: Vec<ModelTemplate> = all(&state, &caller)?
        .into_iter()
        .filter(|t| chosen.is_empty() || chosen.contains(t.id.as_str()))
        .collect();
    if !chosen.is_empty() && templates.len() != chosen.len() {
        return Err(ApiError::not_found(
            "Some of the chosen templates were not found.",
        ));
    }
    let now = Timestamp::now().isoformat();
    let content = py_json_indented(
        &json!({
            "format": FILE_FORMAT,
            "format_version": FILE_VERSION,
            "exported_at": format!("{}+00:00", &now[..19]),
            "templates": templates.iter().map(|t| json!({
                "name": t.name, "description": t.description,
                "model_tree": t.model_tree, "bayesian_config": t.bayesian_config,
            })).collect::<Vec<_>>(),
        }),
        1,
    );
    Ok((
        [
            (header::CONTENT_TYPE, "application/json"),
            (
                header::CONTENT_DISPOSITION,
                "attachment; filename=model-templates.json",
            ),
        ],
        content,
    )
        .into_response())
}

/// A tree without its ids, for telling two templates apart (`_shape`).
fn shape(tree: &Value) -> Value {
    let mut out: Map<String, Value> = tree
        .as_object()
        .into_iter()
        .flatten()
        .filter(|(k, _)| k.as_str() != "id" && k.as_str() != "children")
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    out.insert(
        "children".to_owned(),
        Value::Array(
            tree["children"]
                .as_array()
                .into_iter()
                .flatten()
                .map(shape)
                .collect(),
        ),
    );
    Value::Object(out)
}

/// What a templates file holds and what importing it would do, storing
/// nothing (`plan_template_import`).
fn plan_import(state: &State, caller: &Caller, content: &[u8]) -> ApiResult<Value> {
    let refuse = |m: &str| Ok(json!({"ok": false, "errors": [m], "templates": []}));
    let data: Value = match std::str::from_utf8(content)
        .ok()
        .and_then(|t| serde_json::from_str(t).ok())
    {
        Some(d) => d,
        None => {
            return refuse(
                "This is not a model templates file. Use a .json file saved with Export in the templates list.",
            );
        }
    };
    if data["format"] != FILE_FORMAT {
        return refuse("This is not a RadiCal model templates file.");
    }
    if !data["format_version"]
        .as_i64()
        .is_some_and(|v| v <= FILE_VERSION && data["format_version"].is_i64())
    {
        return refuse(
            "This file was made by a newer version of RadiCal. Update RadiCal to import it.",
        );
    }
    let entries = data["templates"].as_array().cloned().unwrap_or_default();
    if entries.is_empty() {
        return refuse("The file holds no templates.");
    }
    if entries.len() > MAX_TEMPLATES_PER_FILE {
        return refuse(&format!(
            "A file holds at most {MAX_TEMPLATES_PER_FILE} templates."
        ));
    }
    let existing = all(state, caller)?;
    let mut taken: HashSet<String> = existing.iter().map(|t| t.name.to_lowercase()).collect();
    let mut same: Vec<(String, Value, Value)> = existing
        .iter()
        .map(|t| {
            (
                t.name.to_lowercase(),
                shape(&t.model_tree),
                t.bayesian_config.clone().unwrap_or(Value::Null),
            )
        })
        .collect();
    let (mut errors, mut plan) = (Vec::new(), Vec::new());
    for (index, entry) in entries.iter().enumerate() {
        let label = entry["name"]
            .as_str()
            .filter(|n| !n.is_empty())
            .map_or_else(|| format!("template {}", index + 1), str::to_owned);
        let prepared = if entry.is_object() {
            prepare(entry, vec![])
        } else {
            Err(vec!["not a template".to_owned()])
        };
        let (name, description, tree, config) = match prepared {
            Ok(p) => p,
            Err(ms) => {
                errors.extend(ms.into_iter().map(|m| format!("'{label}': {m}")));
                continue;
            }
        };
        let (phases, boundaries, elements) = summary(&tree);
        let key = (name.to_lowercase(), shape(&tree), config.clone());
        if same.contains(&key) {
            plan.push(json!({
                "name": name, "action": "same", "saved_as": name,
                "phases": phases, "boundaries": boundaries, "elements": elements,
            }));
            continue;
        }
        let mut saved_as = name.clone();
        let mut n = 2;
        while taken.contains(&saved_as.to_lowercase()) {
            saved_as = format!("{name} ({n})");
            n += 1;
        }
        taken.insert(saved_as.to_lowercase());
        same.push(key);
        plan.push(json!({
            "name": name,
            "action": if saved_as == name { "add" } else { "rename" },
            "saved_as": saved_as, "description": description,
            "model_tree": tree, "bayesian_config": config,
            "phases": phases, "boundaries": boundaries, "elements": elements,
        }));
    }
    Ok(json!({"ok": errors.is_empty(), "errors": errors, "templates": plan}))
}

fn file_of(headers: &HeaderMap, body: &Bytes) -> ApiResult<Vec<u8>> {
    let form = Form::parse(
        headers
            .get(header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok()),
        body,
    );
    let Some(file) = form.file("file") else {
        return Err(form::missing(&["file"]));
    };
    if file.data.is_empty() {
        return Err(ApiError::bad_request("The uploaded file is empty."));
    }
    if file.data.len() > MAX_FILE_BYTES {
        return Err(ApiError::new(
            StatusCode::PAYLOAD_TOO_LARGE,
            format!(
                "The file is {} KB; the limit is {} KB.",
                file.data.len() / 1024,
                MAX_FILE_BYTES / 1024
            ),
        ));
    }
    Ok(file.data.clone())
}

async fn preview_import(
    Extract(state): Extract<State>,
    caller: Caller,
    headers: HeaderMap,
    body: Bytes,
) -> ApiResult {
    let content = file_of(&headers, &body)?;
    let mut plan = plan_import(&state, &caller, &content)?;
    if let Some(items) = plan["templates"].as_array_mut() {
        for t in items {
            let o = t.as_object_mut().unwrap();
            for k in ["model_tree", "bayesian_config", "description"] {
                o.remove(k);
            }
        }
    }
    Ok(ok(plan))
}

/// Add a templates file's templates, all or none.
async fn import_templates(
    Extract(state): Extract<State>,
    caller: Caller,
    headers: HeaderMap,
    body: Bytes,
) -> ApiResult {
    let content = file_of(&headers, &body)?;
    let plan = plan_import(&state, &caller, &content)?;
    if plan["ok"] != true {
        return Err(ApiError {
            status: StatusCode::UNPROCESSABLE_ENTITY,
            detail: json!({"message": "The templates could not be imported.", "errors": plan["errors"]}),
        });
    }
    let items = plan["templates"].as_array().cloned().unwrap_or_default();
    let mut added = 0;
    state.store.write(|tx| {
        for item in &items {
            if item["action"] == "same" {
                continue;
            }
            let now = Timestamp::now();
            rows::insert(
                tx,
                &ModelTemplate {
                    owner_id: caller.owner().map(str::to_owned),
                    id: radical_store::new_id(),
                    name: item["saved_as"].as_str().unwrap_or_default().to_owned(),
                    description: item["description"].as_str().map(str::to_owned),
                    model_tree: item["model_tree"].clone(),
                    bayesian_config: Some(item["bayesian_config"].clone()).filter(|c| !c.is_null()),
                    created_at: now,
                    updated_at: now,
                },
            )?;
            added += 1;
        }
        Ok(())
    })?;
    Ok(ok(json!({"added": added, "skipped": items.len() - added})))
}

pub fn router() -> Router<State> {
    Router::new()
        .route(
            "/api/model-templates",
            get(list_templates).post(create_template),
        )
        .route("/api/model-templates/export", get(export_templates))
        .route(
            "/api/model-templates/{template_id}",
            patch(update_template).delete(delete_template),
        )
        .route("/api/import/model-templates/preview", post(preview_import))
        .route("/api/import/model-templates", post(import_templates))
}
