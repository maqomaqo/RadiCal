//! The PDF report, held to the backend's tests of it
//! (`backend/tests/test_pdf_report.py`). The replay matches the recorded
//! downloads on status alone, since the bytes are a different writer's, so
//! what the report says is checked here: its text is read back out of the
//! file, the way a reader would read it.

use axum::body::Body;
use axum::http::Request;
use http_body_util::BodyExt;
use radical_store::rows::{self, Analysis, ModelElement};
use serde_json::{Value, json};
use tower::ServiceExt;

struct Got {
    status: u16,
    content_type: String,
    disposition: String,
    body: Vec<u8>,
}

async fn send(app: &axum::Router, method: &str, uri: &str, body: Option<Value>) -> Got {
    let request = Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json")
        .body(body.map_or(Body::empty(), |b| Body::from(b.to_string())))
        .unwrap();
    let response = app.clone().oneshot(request).await.unwrap();
    let header = |name: &str| {
        response
            .headers()
            .get(name)
            .map(|v| v.to_str().unwrap().to_owned())
            .unwrap_or_default()
    };
    let (content_type, disposition) = (header("content-type"), header("content-disposition"));
    let status = response.status().as_u16();
    let body = response
        .into_body()
        .collect()
        .await
        .unwrap()
        .to_bytes()
        .to_vec();
    Got {
        status,
        content_type,
        disposition,
        body,
    }
}

async fn created(app: &axum::Router, uri: &str, body: Value) -> String {
    let got = send(app, "POST", uri, Some(body)).await;
    let v: Value = serde_json::from_slice(&got.body).unwrap();
    assert_eq!(got.status, 200, "{v}");
    v["data"]["id"].as_str().unwrap().to_owned()
}

async fn pdf(app: &axum::Router, id: &str, query: &str) -> Vec<u8> {
    let got = send(app, "GET", &format!("/api/results/{id}/pdf{query}"), None).await;
    assert_eq!(got.status, 200);
    got.body
}

/// The text of a PDF: every Flate stream inflated, and the strings of its
/// `(...) Tj` operators joined by spaces (`pdf_text`).
fn pdf_text(data: &[u8]) -> String {
    let mut chunks = Vec::new();
    let mut at = 0;
    while let Some(start) = find(&data[at..], b"stream\n").map(|i| at + i + 7) {
        let Some(end) = find(&data[start..], b"endstream").map(|i| start + i) else {
            break;
        };
        let raw = data[start..end]
            .strip_suffix(b"\n")
            .unwrap_or(&data[start..end]);
        if let Ok(inflated) = miniz_oxide::inflate::decompress_to_vec_zlib(raw) {
            chunks.push(inflated.iter().map(|&b| b as char).collect::<String>());
        }
        at = end + b"endstream".len();
    }
    let content = chunks.join("\n");
    let tj = regex::Regex::new(r"\(((?:[^\\)]|\\.)*)\) Tj").unwrap();
    tj.captures_iter(&content)
        .map(|c| {
            c[1].replace("\\(", "(")
                .replace("\\)", ")")
                .replace("\\\\", "\\")
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

fn page_count(data: &[u8]) -> usize {
    let count = |n: &[u8]| data.windows(n.len()).filter(|w| *w == n).count();
    count(b"/Type /Page") - count(b"/Type /Pages")
}

fn calibration(name: &str) -> Value {
    json!({
        "sample_name": name, "age_bp": 3000, "error_1sigma": 30,
        "material_type": "charcoal", "sample_type": "terrestrial",
        "curve_id": "intcal20", "save": true,
    })
}

fn modelled() -> Value {
    json!({
        "name": "Pdf structure",
        "measurements": [
            {"id": "pa", "sample_name": "Lower charcoal", "age_bp": 3300,
             "error_1sigma": 30, "element_id": "pp1"},
            {"id": "pb", "sample_name": "Upper charcoal", "age_bp": 3000,
             "error_1sigma": 30, "element_id": "pp2"},
        ],
        "model_tree": {
            "id": "proot", "element_type": "sequence", "name": "Site", "children": [
                {"id": "pb0", "element_type": "boundary", "name": "Start", "children": []},
                {"id": "pp1", "element_type": "phase", "name": "Lower", "children": []},
                {"id": "pb1", "element_type": "boundary", "name": "Mid", "children": []},
                {"id": "pp2", "element_type": "phase", "name": "Upper", "children": []},
                {"id": "pb2", "element_type": "boundary", "name": "End", "children": []},
            ],
        },
        "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
        "save": true,
    })
}

/// A run whose contexts carry modelled spans but whose tree has gone: an
/// analysis from before trees, as it sits on disk.
fn legacy() -> Value {
    json!({
        "name": "Pdf contexts",
        "measurements": [
            {"id": "ca", "sample_name": "A", "age_bp": 3300, "error_1sigma": 30, "group_id": "cg1"},
            {"id": "cb", "sample_name": "B", "age_bp": 3250, "error_1sigma": 30, "group_id": "cg1"},
            {"id": "cc", "sample_name": "C", "age_bp": 3000, "error_1sigma": 30, "group_id": "cg2"},
            {"id": "cd", "sample_name": "D", "age_bp": 2950, "error_1sigma": 30, "group_id": "cg2"},
        ],
        "groups": [{"id": "cg1", "name": "Occupation"}, {"id": "cg2", "name": "Abandonment"}],
        "relationships": [{"from_sample_id": "cg1", "to_sample_id": "cg2",
                           "relationship": "above", "from_type": "group", "to_type": "group"}],
        "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
        "save": true,
    })
}

#[tokio::test(flavor = "multi_thread")]
async fn the_report_reads_as_the_backend_writes_it() {
    let dir = std::env::temp_dir().join(format!("radical-pdf-{}", radical_store::new_id()));
    let state = radical_server::State::open(dir.join("radical.db"), 1).unwrap();
    let app = radical_server::app(state.clone());

    // The route.
    let plain = created(&app, "/api/analysis/calibrate", calibration("Pit fill")).await;
    let got = send(&app, "GET", &format!("/api/results/{plain}/pdf"), None).await;
    assert_eq!(got.status, 200);
    assert_eq!(got.content_type, "application/pdf");
    assert!(got.body.starts_with(b"%PDF-"));
    assert!(
        got.disposition
            .contains(&format!("analysis-{plain}-report.pdf"))
    );
    assert_eq!(
        send(&app, "GET", "/api/results/ghost/pdf", None)
            .await
            .status,
        404
    );
    let posted = send(
        &app,
        "POST",
        &format!("/api/results/{plain}/export"),
        Some(json!({"format": "pdf"})),
    )
    .await;
    assert_eq!(posted.status, 200);
    assert_eq!(posted.content_type, "application/pdf");
    assert_eq!(pdf_text(&posted.body), pdf_text(&got.body));
    let ghost = send(
        &app,
        "POST",
        "/api/results/ghost/export",
        Some(json!({"format": "pdf"})),
    )
    .await;
    assert_eq!(ghost.status, 404);

    // What a calibration's report says.
    let text = pdf_text(&got.body);
    for phrase in [
        "Pit fill",
        "status: completed",
        "Calibration",
        "IntCal20",
        "Reimer",
        "68.3% HPD",
        "95.4% HPD",
        "cal BP",
    ] {
        assert!(text.contains(phrase), "{phrase} in {text}");
    }
    assert!(!text.contains("Unmodelled 95.4% HPD"));
    assert!(!text.contains("Agree %"));
    let bcad = pdf_text(&pdf(&app, &plain, "?date_format=bcad").await);
    assert!(bcad.contains("BC/AD") && bcad.contains("BC"));
    let fallback = pdf_text(&pdf(&app, &plain, "?date_format=klingon").await);
    assert!(fallback.contains("cal BP"));

    // A pending analysis says it has no results, on one page.
    let pending = Analysis {
        id: radical_store::new_id(),
        name: "Not run yet".to_owned(),
        analysis_type: "calibration".to_owned(),
        status: Some("pending".to_owned()),
        ..Analysis::default()
    };
    state.store.write(|tx| rows::insert(tx, &pending)).unwrap();
    let data = pdf(&app, &pending.id, "").await;
    assert!(pdf_text(&data).to_lowercase().contains("no results"));
    assert_eq!(page_count(&data), 1);

    // A modelled run: its structure, solver, agreement and figures.
    let model = created(&app, "/api/analysis/bayesian?wait=true", modelled()).await;
    let data = pdf(&app, &model, "").await;
    let text = pdf_text(&data);
    for name in [
        "Site",
        "Lower",
        "Upper",
        "Start",
        "Mid",
        "End",
        "Lower charcoal",
        "Upper charcoal",
        "Solved by",
        "Unmodelled 95.4% HPD",
        "Posterior Distributions",
    ] {
        assert!(text.contains(name), "{name} in {text}");
    }
    let at = text
        .find("Agreement over the whole model")
        .expect("agreement quoted");
    let sentence: String = text[at..].chars().take(120).collect();
    for index in [r"A\s*model\s*\d+%", r"A\s*overall\s*\d+%"] {
        assert!(
            regex::Regex::new(index).unwrap().is_match(&sentence),
            "{sentence}"
        );
    }
    for name in ["Lower charcoal", "Upper charcoal", "Start", "Mid", "End"] {
        assert!(text.matches(name).count() >= 2, "{name}");
    }
    assert!(page_count(&data) > 1);
    assert_eq!(text.matches(model.as_str()).count(), page_count(&data));
    assert!(!text.contains("Context Spans"));

    // A model from before trees reports its contexts' spans instead.
    let old = created(&app, "/api/analysis/bayesian?wait=true", legacy()).await;
    state
        .store
        .write(|tx| rows::delete_where::<ModelElement>(tx, "analysis_id = ?", [&old]))
        .unwrap();
    let text = pdf_text(&pdf(&app, &old, "").await);
    for name in [
        "Context Spans",
        "Occupation",
        "Abandonment",
        "A",
        "B",
        "C",
        "D",
    ] {
        assert!(text.contains(name), "{name} in {text}");
    }
    let _ = std::fs::remove_dir_all(dir);
}
