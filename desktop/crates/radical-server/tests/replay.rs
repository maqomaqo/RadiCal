//! The backend's own API tests, replayed against the Rust server.
//!
//! `corpus/record_api.py` records every HTTP exchange the backend's suite
//! makes, in order, against one shared database; this sends each of the
//! suite user's requests to the Rust server, in the same order, against one
//! fresh store, and compares the answers: the status exactly, and the JSON up
//! to ids (bound as they first appear, as the units tests bind them) and to
//! rounding. Long numeric arrays are compared as the summary the corpus
//! stores (length, count, min, max, sum).
//!
//! A route the Rust server does not have yet answers its fallback 404; those
//! are counted as not ported, not as failures. Every route in [`PORTED`] must
//! match on every exchange it can reach: one in a test that already hit a
//! route not ported yet is blocked, which is allowed; a difference is not.

use std::collections::{BTreeMap, HashMap};
use std::sync::LazyLock;

use axum::body::Body;
use axum::http::{Method, Request};
use http_body_util::BodyExt;
use radical_corpus::load_family;
use regex::Regex;
use serde_json::{Value, json};
use tower::ServiceExt;

/// The routes held to the backend on every exchange.
const PORTED: &[&str] = &[
    "GET /api/health",
    "POST /api/projects",
    "GET /api/projects",
    "GET /api/projects/{project_id}",
    "PATCH /api/projects/{project_id}",
    "GET /api/projects/{project_id}/samples",
    "POST /api/projects/{project_id}/samples",
    "PATCH /api/projects/{project_id}/samples",
    "DELETE /api/projects/{project_id}/samples",
    "PATCH /api/projects/{project_id}/samples/{sample_id}",
    "DELETE /api/projects/{project_id}/samples/{sample_id}",
    "POST /api/projects/{project_id}/deploy",
    "POST /api/analysis/calibrate",
    "POST /api/analysis/calibrate-group",
    "POST /api/analysis/same-event",
    "GET /api/analysis/{analysis_id}",
    "PATCH /api/analysis/{analysis_id}",
    "DELETE /api/analysis/{analysis_id}",
    "DELETE /api/analysis",
    "POST /api/analysis/bayesian",
    "POST /api/analysis/{analysis_id}/rerun",
    "POST /api/projects/{project_id}/rerun",
    "PUT /api/analysis/{analysis_id}/model-tree",
    "POST /api/analysis/{analysis_id}/duplicate",
    "PUT /api/analysis/{analysis_id}/inputs",
    "GET /api/jobs/{job_id}",
    "GET /api/results/{analysis_id}/table",
    "GET /api/results/{analysis_id}/export/csv",
    "GET /api/results/{analysis_id}/export/json",
    "GET /api/results/{analysis_id}/p-before",
    "GET /api/results/{analysis_id}/export/oxcal",
    "GET /api/results/{analysis_id}/graphs",
    "GET /api/results/{analysis_id}/pdf",
    "POST /api/results/{analysis_id}/export",
    "GET /api/curves",
    "POST /api/curves",
    "GET /api/curves/compare",
    "POST /api/curves/import/preview",
    "POST /api/curves/mixed",
    "GET /api/curves/type/{curve_type}",
    "GET /api/curves/{curve_id}",
    "PATCH /api/curves/{curve_id}",
    "DELETE /api/curves/{curve_id}",
    "GET /api/curves/{curve_id}/points",
    "GET /api/curves/{curve_id}/points.csv",
    "POST /api/curves/{curve_id}/simulate",
    "GET /api/curves/{curve_id}/resolution",
    "GET /api/curves/{curve_id}/resolution.csv",
    "GET /api/curves/{curve_id}/usage",
    "POST /api/curves/{curve_id}/restore",
    "GET /api/curves/{curve_id}/export",
    "POST /api/import/dates",
    "GET /api/import/csv-template",
    "GET /api/import/csv-columns",
    "GET /api/projects/{project_id}/export",
    "GET /api/projects/{project_id}/samples/export",
    "POST /api/import/project/preview",
    "POST /api/import/project",
    "GET /api/model-templates",
    "POST /api/model-templates",
    "PATCH /api/model-templates/{template_id}",
    "DELETE /api/model-templates/{template_id}",
    "GET /api/model-templates/export",
    "POST /api/import/model-templates/preview",
    "POST /api/import/model-templates",
    "GET /api/analysis/{analysis_id}/runs",
    "GET /api/analysis/{analysis_id}/runs/{run_id}",
    "GET /api/projects/{project_id}/run-diff",
    "POST /api/analysis/{analysis_id}/influence",
    "GET /api/analysis/{analysis_id}/influence",
    "POST /api/analysis/{analysis_id}/influence/open",
    "POST /api/dispersal/propose-sequence",
];

/// Tests whose exchanges cannot match, and why. Each is excused whole, so
/// keep the reasons narrow.
const KNOWN: &[(&str, &str)] = &[
    (
        "test_run_control.py::TestDuplicateWithNewSeed::test_the_copy_takes_a_fresh_seed_and_says_so",
        "the copy's seed is random by design",
    ),
    (
        "test_jobs.py::TestFailure::test_a_failed_run_fails_the_job_and_the_analysis",
        "the test breaks a date's curve in the database, behind the API",
    ),
    (
        "test_jobs.py::TestFailure::test_a_job_for_a_deleted_analysis_fails_rather_than_crashing",
        "the job races the deletion, and where it is when the analysis goes decides the message",
    ),
    (
        "test_run_history.py::TestRecording::test_a_failure_to_record_never_fails_the_run",
        "the test breaks the recording with a monkeypatch",
    ),
    (
        "test_run_history.py::TestProjectDiff::test_a_changed_date_is_listed_with_what_moved",
        "the test changes a date in the database, behind the API",
    ),
    (
        "test_influence.py::TestTheReport::test_editing_the_analysis_marks_the_report_stale",
        "the test changes a date in the database, behind the API",
    ),
    (
        "test_oxcal_export.py::TestThroughTheApi::test_the_endpoint_writes_the_analysis_and_counts_what_it_left_out",
        "the file names the commit the backend ran from",
    ),
    // A Boundary on a 10-year grid reported at 5 years: each grid cell is
    // two reporting bins of exactly equal mass, so which of a pair the
    // 68.3% region takes is decided by the last bit, and the two solvers'
    // densities agree to 6e-15, not to the bit.
    (
        "test_agreement.py::TestWhatItCanAndCannotSee::test_does_not_flag_an_outlier_a_lone_phase_can_simply_absorb",
        "a tie between two reporting bins",
    ),
    (
        "test_rerun.py::TestEditingKeepsTheOutlierSetting::test_replacing_inputs_keeps_it",
        "a tie between two reporting bins",
    ),
    (
        "test_project_samples.py::TestDeployingAsCalibration::test_an_analysis_already_stored_as_multi_date_calibration_is_repaired",
        "the test retypes the analysis in the database, behind the API",
    ),
];

/// How close a number must come, relative to max(|value|, 1).
const TOL: f64 = 1e-9;

static PLACEHOLDER: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<id:(\d+)>").unwrap());
static STAMP: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,6})?$").unwrap());

/// The backend's route templates, from the OpenAPI document the suite
/// itself fetched: (method, template, matcher), most literal first so
/// `/curves/compare` wins over `/curves/{curve_id}`.
fn routes(cases: &[radical_corpus::Case]) -> Vec<(String, String, Regex)> {
    let doc = cases
        .iter()
        .find(|c| c.inputs["url"] == "/api/openapi.json")
        .and_then(|c| c.expanded_outputs())
        .expect("the recording has the OpenAPI document");
    let param = Regex::new(r"\{[^}]+\}").unwrap();
    let mut out: Vec<(String, String, Regex)> = Vec::new();
    for (path, ops) in doc["response_json"]["paths"].as_object().unwrap() {
        for method in ops.as_object().unwrap().keys() {
            let pattern = format!(
                "^{}$",
                param.replace_all(
                    &regex::escape(path).replace(r"\{", "{").replace(r"\}", "}"),
                    "[^/]+"
                )
            );
            out.push((
                method.to_uppercase(),
                path.clone(),
                Regex::new(&pattern).unwrap(),
            ));
        }
    }
    out.sort_by_key(|(_, path, _)| (path.matches('{').count(), std::cmp::Reverse(path.len())));
    out
}

/// A request's route, as the backend declares it.
fn template(routes: &[(String, String, Regex)], method: &str, url: &str) -> String {
    let path = url.split('?').next().unwrap();
    routes
        .iter()
        .find(|(m, _, re)| m == method && re.is_match(path))
        .map_or_else(
            || format!("{method} {path}"),
            |(m, t, _)| format!("{m} {t}"),
        )
}

/// Placeholder number -> the Rust server's id, one to one.
#[derive(Default, Clone)]
struct Ids {
    forward: HashMap<String, String>,
    taken: HashMap<String, String>,
    /// Set when a request named an id no answer here has produced: made up
    /// by the test, or created by a route not ported yet.
    invented: bool,
    /// The placeholders this replay made ids up for.
    made_up: std::collections::HashSet<String>,
}

impl Ids {
    fn bind(&mut self, n: &str, id: &str) -> bool {
        match self.forward.get(n) {
            Some(bound) => bound == id,
            None => {
                if self.taken.contains_key(id) {
                    return false;
                }
                self.forward.insert(n.to_owned(), id.to_owned());
                self.taken.insert(id.to_owned(), n.to_owned());
                true
            }
        }
    }

    /// A request's text with every placeholder replaced; one never seen is
    /// an id the test made up, and gets a fresh one.
    fn fill(&mut self, text: &str) -> String {
        PLACEHOLDER
            .replace_all(text, |c: &regex::Captures| {
                let n = c[1].to_owned();
                if !self.forward.contains_key(&n) {
                    let fresh = radical_store::new_id();
                    self.bind(&n, &fresh);
                    self.made_up.insert(n.clone());
                }
                if self.made_up.contains(&n) {
                    self.invented = true;
                }
                self.forward[&n].clone()
            })
            .into_owned()
    }

    fn fill_json(&mut self, v: &Value) -> Value {
        match v {
            Value::String(s) => Value::String(self.fill(s)),
            Value::Array(a) => Value::Array(a.iter().map(|x| self.fill_json(x)).collect()),
            Value::Object(o) => Value::Object(
                o.iter()
                    .map(|(k, x)| (self.fill(k), self.fill_json(x)))
                    .collect(),
            ),
            other => other.clone(),
        }
    }
}

/// The corpus's `summarise_long_arrays`.
fn summarise(v: &Value) -> Value {
    fn numbers(v: &Value, out: &mut Vec<f64>) -> bool {
        match v {
            Value::Number(n) => {
                out.push(n.as_f64().unwrap());
                true
            }
            Value::Array(a) => a.iter().all(|x| numbers(x, out)),
            _ => false,
        }
    }
    match v {
        Value::Array(a) => {
            let mut flat = Vec::new();
            if !a.is_empty() && numbers(v, &mut flat) && flat.len() > 64 {
                let min = flat.iter().copied().fold(f64::INFINITY, f64::min);
                let max = flat.iter().copied().fold(f64::NEG_INFINITY, f64::max);
                let sum: f64 = flat.iter().sum();
                return json!({"$summary": {"len": a.len(), "count": flat.len(), "min": min, "max": max, "sum": sum}});
            }
            Value::Array(a.iter().map(summarise).collect())
        }
        Value::Object(o) => {
            Value::Object(o.iter().map(|(k, x)| (k.clone(), summarise(x))).collect())
        }
        other => other.clone(),
    }
}

/// Keys whose values differ between two identical runs (the corpus's
/// VOLATILE, which it drops).
const VOLATILE: &[&str] = &[
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
    "finished_at",
    "queued_at",
    "elapsed_seconds",
    "duration_seconds",
    "runtime_seconds",
    "compute_seconds",
    "wall_seconds",
    "last_run_at",
    "seconds",
    // Which build ran, and when a package was made: the machine's, not the
    // answer's.
    "app_commit",
    "exported_at",
    "commits",
    // A fingerprint over inputs that include random ids. Whether two agree
    // is still held, as `stale` and `same_inputs`.
    "inputs_hash",
];

fn has_placeholder(v: &Value) -> bool {
    match v {
        Value::String(s) => PLACEHOLDER.is_match(s),
        Value::Array(a) => a.iter().any(has_placeholder),
        Value::Object(o) => o.values().any(has_placeholder),
        _ => false,
    }
}

/// Where `got` differs from `want`, or None; binds ids as it goes.
fn differs(path: &str, got: &Value, want: &Value, ids: &mut Ids) -> Option<String> {
    match (want, got) {
        (Value::String(w), Value::String(g)) if PLACEHOLDER.is_match(w) => {
            let mut pattern = String::from("^");
            let mut last = 0;
            let mut order = Vec::new();
            for c in PLACEHOLDER.captures_iter(w) {
                let m = c.get(0).unwrap();
                pattern.push_str(&regex::escape(&w[last..m.start()]));
                pattern.push_str("(.+?)");
                order.push(c[1].to_owned());
                last = m.end();
            }
            pattern.push_str(&regex::escape(&w[last..]));
            pattern.push('$');
            let Some(caps) = Regex::new(&pattern).unwrap().captures(g) else {
                return Some(format!("{path}: {g:?} is not {w:?}"));
            };
            for (k, n) in order.iter().enumerate() {
                if !ids.bind(n, &caps[k + 1]) {
                    return Some(format!("{path}: id {} is not <id:{n}>", &caps[k + 1]));
                }
            }
            None
        }
        // Two instants: no two runs write the same clock.
        (Value::String(w), Value::String(g)) if STAMP.is_match(w) && STAMP.is_match(g) => None,
        (Value::Number(w), Value::Number(g)) => {
            let (w, g) = (w.as_f64().unwrap(), g.as_f64().unwrap());
            ((g - w).abs() > TOL * w.abs().max(1.0)).then(|| format!("{path}: {g} vs {w}"))
        }
        (Value::Array(w), Value::Array(g)) => {
            if w.len() != g.len() {
                return Some(format!("{path}: {} items vs {}", g.len(), w.len()));
            }
            let mut trial = ids.clone();
            let in_order = w
                .iter()
                .zip(g)
                .enumerate()
                .find_map(|(i, (w, g))| differs(&format!("{path}[{i}]"), g, w, &mut trial));
            if in_order.is_none() {
                *ids = trial;
                return None;
            }
            // A list ordered by its ids (sorted, or as SQLite's index on them
            // returns it) comes out in another order when the ids are other
            // random ones: the same items in any order is the same answer.
            // So does a package's collection: dates added in one request
            // share a created_at, and their random ids break the tie.
            if !w.iter().any(has_placeholder) && path != "samples.json" {
                return in_order;
            }
            let mut used = vec![false; g.len()];
            for wv in w {
                let found = g.iter().enumerate().find_map(|(j, gv)| {
                    if used[j] {
                        return None;
                    }
                    let mut attempt = ids.clone();
                    differs(path, gv, wv, &mut attempt)
                        .is_none()
                        .then_some((j, attempt))
                });
                match found {
                    Some((j, attempt)) => {
                        used[j] = true;
                        *ids = attempt;
                    }
                    None => return in_order,
                }
            }
            None
        }
        (Value::Object(w), Value::Object(g)) => {
            let mut gk: Vec<&String> = g
                .keys()
                .filter(|k| !VOLATILE.contains(&k.as_str()))
                .collect();
            let mut wk: Vec<&String> = w
                .keys()
                .filter(|k| !VOLATILE.contains(&k.as_str()))
                .collect();
            gk.sort();
            wk.sort();
            let whole_id = |k: &str| {
                PLACEHOLDER
                    .captures(k)
                    .filter(|c| c[0].len() == k.len())
                    .map(|c| c[1].to_owned())
            };
            if !wk.iter().any(|k| whole_id(k).is_some()) {
                if gk != wk {
                    return Some(format!("{path}: keys {gk:?} vs {wk:?}"));
                }
                return w
                    .iter()
                    .filter(|(k, _)| !VOLATILE.contains(&k.as_str()))
                    .find_map(|(k, wv)| differs(&format!("{path}.{k}"), &g[k], wv, ids));
            }
            // Keyed by id: a bound id names its own key, and an unbound one
            // takes the first free key whose value matches.
            if gk.len() != wk.len() {
                return Some(format!("{path}: {} keys vs {}", gk.len(), wk.len()));
            }
            let mut used: std::collections::HashSet<String> = std::collections::HashSet::new();
            for (k, wv) in w {
                let key = match whole_id(k) {
                    None => k.clone(),
                    Some(n) => match ids.forward.get(&n) {
                        Some(bound) => bound.clone(),
                        None => {
                            // Bound only when one key alone matches: values
                            // such as probabilities often tie.
                            // Held against every key, taken or not: a key an
                            // ambiguous one took may be this one's own.
                            let matching: Vec<&&String> = gk
                                .iter()
                                .filter(|candidate| {
                                    let mut trial = ids.clone();
                                    trial.bind(&n, candidate)
                                        && differs("", &g[candidate.as_str()], wv, &mut trial)
                                            .is_none()
                                })
                                .collect();
                            let Some(free) = matching.iter().find(|c| !used.contains(c.as_str()))
                            else {
                                return Some(format!("{path}: nothing matches {k}"));
                            };
                            if matching.len() == 1 {
                                ids.bind(&n, free);
                            }
                            (**free).clone()
                        }
                    },
                };
                let Some(gv) = g.get(&key) else {
                    return Some(format!("{path}: no {k}"));
                };
                used.insert(key.clone());
                if let Some(d) = differs(&format!("{path}.{k}"), gv, wv, ids) {
                    return Some(d);
                }
            }
            None
        }
        _ => (got != want).then(|| format!("{path}: {got} vs {want}")),
    }
}

fn multipart(files: &Value, data: &Value, ids: &mut Ids) -> (String, Vec<u8>) {
    use base64::Engine;
    let boundary = "radical-replay-boundary";
    let mut body = Vec::new();
    for (field, value) in data.as_object().into_iter().flatten() {
        let text = ids.fill(
            &value
                .as_str()
                .map_or_else(|| value.to_string(), str::to_owned),
        );
        body.extend(
            format!(
                "--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"\r\n\r\n{text}\r\n"
            )
            .as_bytes(),
        );
    }
    for (field, file) in files.as_object().unwrap() {
        // A zip is kept as its members' text, and built again here.
        let content = match file["zip"].as_object() {
            Some(members) => {
                let mut zip = radical_server::zip::Writer::default();
                for (name, text) in members {
                    zip.add(name, ids.fill(text.as_str().unwrap_or_default()).as_bytes());
                }
                zip.finish()
            }
            None => base64::engine::general_purpose::STANDARD
                .decode(file["b64"].as_str().unwrap())
                .unwrap(),
        };
        let name = ids.fill(file["filename"].as_str().unwrap_or("upload"));
        body.extend(format!(
            "--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{name}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
        ).as_bytes());
        body.extend(content);
        body.extend(b"\r\n");
    }
    body.extend(format!("--{boundary}--\r\n").as_bytes());
    (format!("multipart/form-data; boundary={boundary}"), body)
}

async fn send(
    app: &axum::Router,
    method: &str,
    url: &str,
    inputs: &Value,
    ids: &mut Ids,
) -> (u16, Option<Value>, Option<String>) {
    let mut uri = ids.fill(url);
    if let Some(params) = inputs["params"].as_object() {
        let query: Vec<String> = params
            .iter()
            .map(|(k, v)| {
                format!(
                    "{k}={}",
                    ids.fill(&v.as_str().map_or_else(|| v.to_string(), str::to_owned))
                )
            })
            .collect();
        uri.push(if uri.contains('?') { '&' } else { '?' });
        uri.push_str(&query.join("&"));
    }
    let builder = Request::builder()
        .method(Method::from_bytes(method.as_bytes()).unwrap())
        .uri(uri);
    let request = if !inputs["request_json"].is_null() {
        builder
            .header("content-type", "application/json")
            .body(Body::from(
                ids.fill_json(&inputs["request_json"]).to_string(),
            ))
    } else if inputs["files"].is_object() {
        let (ctype, body) = multipart(&inputs["files"], &inputs["request_data"], ids);
        builder.header("content-type", ctype).body(Body::from(body))
    } else if let Some(text) = inputs["request_content"].as_str() {
        builder.body(Body::from(ids.fill(text)))
    } else {
        builder.body(Body::empty())
    }
    .unwrap();
    let response = app.clone().oneshot(request).await.unwrap();
    let status = response.status().as_u16();
    let ctype = response
        .headers()
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_owned();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    if ctype.contains("zip") {
        use base64::Engine;
        let encoded = base64::engine::general_purpose::STANDARD.encode(&bytes);
        (status, None, Some(encoded))
    } else if ctype.contains("json") {
        (status, serde_json::from_slice(&bytes).ok(), None)
    } else {
        (
            status,
            None,
            Some(String::from_utf8_lossy(&bytes).into_owned()),
        )
    }
}

#[derive(Default, Debug)]
struct Tally {
    matched: usize,
    differed: usize,
    not_ported: usize,
    /// In a test that had already reached a route not ported yet, so whose
    /// state this server cannot have.
    blocked: usize,
    /// In a test listed in [`KNOWN`].
    excused: usize,
    first: Option<String>,
}

/// The eight characters of an id a package's file names carry, which the
/// recording could not placeholder.
static ID_PREFIX: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"-[0-9a-f]{8}\.json").unwrap());

/// A JSON value with the VOLATILE keys taken out.
fn without_volatile(v: &Value) -> Value {
    match v {
        Value::Object(o) => Value::Object(
            o.iter()
                .filter(|(k, _)| !VOLATILE.contains(&k.as_str()))
                .map(|(k, v)| (k.clone(), without_volatile(v)))
                .collect(),
        ),
        Value::Array(a) => Value::Array(a.iter().map(without_volatile).collect()),
        other => other.clone(),
    }
}

/// A zip held to the recorded one member by member, in order: JSON as
/// JSON, text line by line.
fn zip_differs(encoded: &str, want: &Value, ids: &mut Ids) -> Option<String> {
    use base64::Engine;
    let Ok(bytes) = base64::engine::general_purpose::STANDARD.decode(encoded) else {
        return Some("not a zip".to_owned());
    };
    let got = match radical_server::zip::read(&bytes) {
        Ok(m) => m,
        Err(e) => return Some(e),
    };
    let want = want.as_object().unwrap();
    if got.len() != want.len() {
        let names: Vec<&String> = got.iter().map(|(n, _)| n).collect();
        return Some(format!(
            "{} members vs {}: {names:?}",
            got.len(),
            want.len()
        ));
    }
    let norm = |t: &str| ID_PREFIX.replace_all(t, "-HEX.json").into_owned();
    // The recording keeps members by name, so they are held to it by name.
    let mut got = got;
    got.sort_by_key(|(n, _)| norm(n));
    let mut want: Vec<(&String, &Value)> = want.iter().collect();
    want.sort_by_key(|(n, _)| norm(n));
    for ((name, data), (want_name, want_member)) in got.iter().zip(want) {
        if norm(name) != norm(want_name) {
            return Some(format!("member {name} vs {want_name}"));
        }
        let text = norm(&String::from_utf8_lossy(data));
        let Some(want_text) = want_member.as_str().map(norm) else {
            continue;
        };
        if name.ends_with(".json") {
            let (Ok(g), Ok(w)) = (
                serde_json::from_str::<Value>(&text),
                serde_json::from_str::<Value>(&want_text),
            ) else {
                return Some(format!("{name}: not JSON"));
            };
            if let Some(d) = differs(name, &g, &without_volatile(&w), ids) {
                return Some(d);
            }
        } else if let Some(d) = lines_differ(name, &text, &want_text, ids) {
            return Some(d);
        }
    }
    None
}

/// Text held to the recording line by line, ids bound as they go; rows in
/// another order are matched one for one, since a table ordered by random
/// ids comes out in no order the replay can reproduce.
fn lines_differ(label: &str, got: &str, want: &str, ids: &mut Ids) -> Option<String> {
    let (g, w): (Vec<&str>, Vec<&str>) = (got.lines().collect(), want.lines().collect());
    if g.len() != w.len() {
        return Some(format!("{label}: {} lines vs {}", g.len(), w.len()));
    }
    let mut trial = ids.clone();
    let in_order = g.iter().zip(&w).enumerate().find_map(|(k, (gl, wl))| {
        differs(
            &format!("{label}:{}", k + 1),
            &Value::from(*gl),
            &Value::from(*wl),
            &mut trial,
        )
    });
    let Some(first) = in_order else {
        *ids = trial;
        return None;
    };
    let mut used = vec![false; g.len()];
    let mut trial = ids.clone();
    for wl in &w {
        let found = (0..g.len()).find(|&k| {
            !used[k] && {
                let mut t = trial.clone();
                differs("", &Value::from(g[k]), &Value::from(*wl), &mut t).is_none()
            }
        });
        let Some(k) = found else {
            return Some(first);
        };
        differs("", &Value::from(g[k]), &Value::from(*wl), &mut trial);
        used[k] = true;
    }
    *ids = trial;
    None
}

/// What a test writes to the database itself before its first request,
/// done here too, so the tests after it see the same rows. Answers the id of
/// the row made, for the first id the test's requests name that no answer
/// has produced.
fn behind_the_api(state: &radical_server::State, test: &str) -> Option<String> {
    if test.ends_with(
        "test_pdf_report.py::TestReportContent::test_a_pending_analysis_still_produces_a_report",
    ) {
        let now = Some(radical_store::Timestamp::now());
        let analysis = radical_store::rows::Analysis {
            id: radical_store::new_id(),
            name: "Not run yet".to_owned(),
            analysis_type: "calibration".to_owned(),
            status: Some("pending".to_owned()),
            created_at: now,
            updated_at: now,
            ..radical_store::rows::Analysis::default()
        };
        state
            .store
            .write(|tx| radical_store::rows::insert(tx, &analysis))
            .unwrap();
        return Some(analysis.id);
    }
    None
}

fn stored_curve_ids(state: &radical_server::State) -> Vec<String> {
    let rows: Vec<radical_store::rows::CalibrationCurve> = state
        .store
        .read(|c| radical_store::rows::select_where(c, "1 = 1", [], "rowid"))
        .unwrap();
    rows.into_iter().map(|r| r.id).collect()
}

/// The suite's `remove_stored_curves` fixture: every curve the test stored
/// deleted once it has finished, behind the API.
fn remove_stored_curves(state: &radical_server::State, before: &[String]) {
    for id in stored_curve_ids(state) {
        if !before.contains(&id) {
            state
                .store
                .write(|tx| {
                    radical_store::rows::delete_where::<radical_store::rows::CalibrationCurve>(
                        tx,
                        "id = ?",
                        [&id],
                    )
                })
                .unwrap();
            radical_server::routes::curve_routes::forget(state, &id);
        }
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn the_backend_suite_replayed() {
    let family = load_family("api/exchanges").unwrap();
    let dir = std::env::temp_dir().join(format!("radical-replay-{}", radical_store::new_id()));
    let state = radical_server::State::open(dir.join("radical.db"), 2).unwrap();
    let app = radical_server::app(state.clone());
    // The test whose exchanges these are, and the curves stored before it
    // began when it is one that removes what it stored.
    let mut current: Option<(String, Option<Vec<String>>)> = None;
    let mut made: Option<String> = None;
    let mut ids = Ids::default();
    let mut tally: BTreeMap<String, Tally> = BTreeMap::new();
    let routes = routes(&family.cases);
    let mut blocked_tests: std::collections::HashSet<String> = std::collections::HashSet::new();
    for case in &family.cases {
        let i = case.expanded_inputs();
        let want = case.expanded_outputs().unwrap();
        let test_name = i["test"].as_str().unwrap_or("").to_owned();
        if std::env::var("REPLAY_DUMP_AT").is_ok_and(|n| case.name.starts_with(&n)) {
            let rows: Vec<radical_store::rows::Analysis> = state
                .store
                .read(|c| radical_store::rows::select_where(c, "project_id IS NULL", [], "rowid"))
                .unwrap();
            for a in rows {
                let placeholder = ids.taken.get(&a.id).cloned().unwrap_or_default();
                eprintln!("UNASSIGNED <id:{placeholder}> {} {:?}", a.name, a.status);
            }
        }
        if current.as_ref().is_none_or(|(t, _)| *t != test_name) {
            if let Some((_, Some(before))) = current.take() {
                remove_stored_curves(&state, &before);
            }
            let cleans = i["cleanup"]
                .as_array()
                .is_some_and(|c| c.iter().any(|f| f == "remove_stored_curves"));
            current = Some((test_name.clone(), cleans.then(|| stored_curve_ids(&state))));
            made = behind_the_api(&state, &test_name);
        }
        if let Some(id) = &made
            && let Some(c) = PLACEHOLDER
                .captures_iter(i["url"].as_str().unwrap_or(""))
                .find(|c| !ids.forward.contains_key(&c[1]))
        {
            ids.bind(&c[1], id);
            made = None;
        }
        let (method, url) = (i["method"].as_str().unwrap(), i["url"].as_str().unwrap());
        // The suite's own account, or nobody for the routes open to all.
        let open = matches!(url, "/api/health");
        if !(i["auth"] == "suite" || (open && i["auth"] == "none")) || url.starts_with("/api/auth/")
        {
            continue;
        }
        let route = template(&routes, method, url);
        ids.invented = false;
        let mut answer = send(&app, method, url, &i, &mut ids).await;
        let invented = ids.invented;
        // A poll the backend saw finished waits for the Rust job to finish,
        // as long as the suite's own `_await_job` would (300 s: a slow
        // runner takes far longer than a laptop over an influence report);
        // one it saw still running accepts whatever state this one is in.
        if route == "GET /api/jobs/{job_id}" {
            let state = want["response_json"]["data"]["state"]
                .as_str()
                .unwrap_or("");
            if !matches!(state, "queued" | "running") {
                for _ in 0..6000 {
                    let now = answer
                        .1
                        .as_ref()
                        .and_then(|b| b["data"]["state"].as_str())
                        .unwrap_or("");
                    if !matches!(now, "queued" | "running") {
                        break;
                    }
                    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
                    answer = send(&app, method, url, &i, &mut ids).await;
                }
            }
        }
        let entry = tally.entry(route.clone()).or_default();
        let (status, body, text) = answer;
        // The fallback's answer: no such route here yet.
        let test = i["test"].as_str().unwrap_or("").to_owned();
        // A path served for other methods only answers 405.
        let unrouted = (status == 404 && body.as_ref().is_some_and(|b| b["detail"] == "Not Found"))
            || (status == 405 && want["status"] != 405);
        if unrouted {
            entry.not_ported += 1;
            blocked_tests.insert(test);
            continue;
        }
        // A poll that caught the job mid-flight recorded a moment, not an
        // answer: only which job it is can be held to. A job that failed or
        // was cancelled keeps the last progress it reported, which is when
        // it stopped rather than what it did.
        let recorded = want["response_json"]["data"]["state"].as_str();
        let polled = route == "GET /api/jobs/{job_id}";
        let in_flight = polled && matches!(recorded, Some("queued" | "running"));
        let stopped = polled && matches!(recorded, Some("failed" | "cancelled"));
        let moment = |v: &Value| {
            let mut v = v.clone();
            if let Some(d) = v["data"].as_object_mut() {
                if in_flight {
                    for k in ["state", "progress", "detail", "error_message"] {
                        d.insert(k.to_owned(), Value::Null);
                    }
                } else if stopped {
                    for k in ["progress", "detail"] {
                        d.insert(k.to_owned(), Value::Null);
                    }
                }
            }
            v
        };
        let problem = if status != want["status"].as_u64().unwrap() as u16 {
            Some(format!("status {status} vs {}", want["status"]))
        } else if let Some(w) = want.get("response_json") {
            match &body {
                Some(b) => differs("", &moment(&summarise(b)), &moment(w), &mut ids),
                None => Some("not JSON".to_owned()),
            }
        } else if let Some(w) = want.get("response_zip") {
            zip_differs(text.as_deref().unwrap_or(""), w, &mut ids)
        } else if let Some(w) = want.get("response_text") {
            lines_differ(
                "text",
                text.as_deref().unwrap_or(""),
                w.as_str().unwrap_or(""),
                &mut ids,
            )
        } else {
            None
        };
        match problem {
            None => entry.matched += 1,
            // Behind a route not ported yet: in its own test, or through an
            // id only such a route could have made.
            Some(p) if blocked_tests.contains(&test) || invented => {
                if std::env::var("REPLAY_SHOW").is_ok_and(|s| route.contains(&s)) {
                    eprintln!("BLOCKED {route} {} ({}): {p}", case.name, test);
                }
                entry.blocked += 1;
            }
            Some(p) if KNOWN.iter().any(|(t, _)| test.contains(t)) => {
                if std::env::var("REPLAY_SHOW").is_ok_and(|s| route.contains(&s)) {
                    eprintln!("EXCUSED {route} {} ({}): {p}", case.name, test);
                }
                entry.excused += 1;
            }
            Some(p) => {
                // REPLAY_SHOW=<part of a route> lists every difference there.
                if std::env::var("REPLAY_SHOW").is_ok_and(|s| route.contains(&s)) {
                    eprintln!("DIFF {route} {} ({}): {p}", case.name, test);
                }
                entry.differed += 1;
                entry.first.get_or_insert_with(|| {
                    format!("{} ({}): {p}", case.name, i["test"].as_str().unwrap_or(""))
                });
            }
        }
    }
    let (mut m, mut d, mut n, mut b, mut x) = (0, 0, 0, 0, 0);
    eprintln!("match  diff block  todo  route");
    for (route, t) in &tally {
        m += t.matched;
        d += t.differed;
        n += t.not_ported;
        b += t.blocked;
        x += t.excused;
        if t.matched + t.differed + t.blocked + t.not_ported + t.excused > 0 {
            eprintln!(
                "{:5} {:5} {:5} {:5}  {route}{}",
                t.matched,
                t.differed,
                t.blocked,
                t.not_ported,
                t.first
                    .as_ref()
                    .map(|f| format!("\n                    first: {f}"))
                    .unwrap_or_default()
            );
        }
    }
    eprintln!(
        "matched {m}, differed {d}, blocked {b}, excused {x}, not ported {n}, over {} routes",
        tally.len()
    );
    let _ = std::fs::remove_dir_all(dir);
    for route in PORTED {
        let t = tally
            .get(*route)
            .unwrap_or_else(|| panic!("{route} is not in the recording"));
        assert!(t.differed == 0 && t.not_ported == 0, "{route}: {t:?}");
    }
}
