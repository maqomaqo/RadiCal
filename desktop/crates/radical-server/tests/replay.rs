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
            if !w.iter().any(has_placeholder) {
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
            let mut wk: Vec<&String> = w.keys().collect();
            gk.sort();
            wk.sort();
            if gk != wk {
                return Some(format!("{path}: keys {gk:?} vs {wk:?}"));
            }
            w.iter()
                .find_map(|(k, wv)| differs(&format!("{path}.{k}"), &g[k], wv, ids))
        }
        _ => (got != want).then(|| format!("{path}: {got} vs {want}")),
    }
}

fn multipart(files: &Value, ids: &mut Ids) -> (String, Vec<u8>) {
    use base64::Engine;
    let boundary = "radical-replay-boundary";
    let mut body = Vec::new();
    for (field, file) in files.as_object().unwrap() {
        let content = base64::engine::general_purpose::STANDARD
            .decode(file["b64"].as_str().unwrap())
            .unwrap();
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
        let (ctype, body) = multipart(&inputs["files"], ids);
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
    if ctype.contains("json") {
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
    first: Option<String>,
}

#[tokio::test(flavor = "multi_thread")]
async fn the_backend_suite_replayed() {
    let family = load_family("api/exchanges").unwrap();
    let dir = std::env::temp_dir().join(format!("radical-replay-{}", radical_store::new_id()));
    let state = radical_server::State::open(dir.join("radical.db"), 2).unwrap();
    let app = radical_server::app(state);
    let mut ids = Ids::default();
    let mut tally: BTreeMap<String, Tally> = BTreeMap::new();
    let routes = routes(&family.cases);
    let mut blocked_tests: std::collections::HashSet<String> = std::collections::HashSet::new();
    for case in &family.cases {
        let i = case.expanded_inputs();
        let want = case.expanded_outputs().unwrap();
        let (method, url) = (i["method"].as_str().unwrap(), i["url"].as_str().unwrap());
        // The suite's own account, or nobody for the routes open to all.
        let open = matches!(url, "/api/health");
        if !(i["auth"] == "suite" || (open && i["auth"] == "none")) || url.starts_with("/api/auth/")
        {
            continue;
        }
        let route = template(&routes, method, url);
        let mut answer = send(&app, method, url, &i, &mut ids).await;
        // A poll the backend saw finished waits for the Rust job to finish;
        // one it saw still running accepts whatever state this one is in.
        if route == "GET /api/jobs/{job_id}" {
            let state = want["response_json"]["data"]["state"]
                .as_str()
                .unwrap_or("");
            if !matches!(state, "queued" | "running") {
                for _ in 0..600 {
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
        let problem = if status != want["status"].as_u64().unwrap() as u16 {
            Some(format!("status {status} vs {}", want["status"]))
        } else if let Some(w) = want.get("response_json") {
            match &body {
                Some(b) => differs("", &summarise(b), w, &mut ids),
                None => Some("not JSON".to_owned()),
            }
        } else if let Some(w) = want.get("response_text") {
            (text.as_deref() != w.as_str()).then(|| "text differs".to_owned())
        } else {
            None
        };
        match problem {
            None => entry.matched += 1,
            Some(_) if blocked_tests.contains(&test) => entry.blocked += 1,
            Some(p) => {
                entry.differed += 1;
                entry.first.get_or_insert_with(|| {
                    format!("{} ({}): {p}", case.name, i["test"].as_str().unwrap_or(""))
                });
            }
        }
    }
    let (mut m, mut d, mut n, mut b) = (0, 0, 0, 0);
    eprintln!("match  diff block  todo  route");
    for (route, t) in &tally {
        m += t.matched;
        d += t.differed;
        n += t.not_ported;
        b += t.blocked;
        if t.matched + t.differed + t.blocked > 0 {
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
        "matched {m}, differed {d}, blocked {b}, not ported {n}, over {} routes",
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
