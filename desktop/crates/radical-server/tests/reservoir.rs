//! The marine reservoir routes, held to the backend's own tests of them
//! (`backend/tests/test_reservoir_registry.py`), which the replay cannot
//! reach: they run as a second account and store their snapshots behind the
//! API. The same is done here, through the store. The fetch from calib.org
//! is not exercised: it needs the network.

use axum::body::Body;
use axum::http::Request;
use http_body_util::BodyExt;
use radical_io::reservoir_ingest::{ReservoirRow, content_hash};
use radical_store::Timestamp;
use radical_store::rows::{self, MarineReservoirPoint, MarineReservoirSnapshot, RadiocarbonSample};
use serde_json::{Value, json};
use tower::ServiceExt;

async fn call(app: &axum::Router, method: &str, uri: &str, body: Option<Value>) -> (u16, Value) {
    let request = Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json")
        .body(body.map_or(Body::empty(), |b| Body::from(b.to_string())))
        .unwrap();
    let response = app.clone().oneshot(request).await.unwrap();
    let status = response.status().as_u16();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    (
        status,
        serde_json::from_slice(&bytes).unwrap_or(Value::Null),
    )
}

fn row(
    map_no: i64,
    lat: f64,
    lon: f64,
    dr: Option<f64>,
    err: Option<f64>,
    feeding: &str,
) -> ReservoirRow {
    ReservoirRow {
        map_no,
        latitude: lat,
        longitude: lon,
        delta_r: dr,
        delta_r_error: err,
        locality: Some(format!("Point {map_no}")),
        feeding: Some(feeding.to_owned()),
        feeding_raw: Some(feeding.to_owned()),
        ..ReservoirRow::default()
    }
}

/// A snapshot stored as an ingest stores one, with `tamper` changing a point
/// after the hash is taken.
fn plant(state: &radical_server::State, id: &str, tamper: bool) -> Vec<ReservoirRow> {
    let rows_in = vec![
        row(1, -41.10, 174.00, Some(30.0), Some(40.0), "suspension"),
        row(2, -41.20, 174.10, Some(-20.0), Some(35.0), "suspension"),
        row(3, -41.05, 173.90, Some(250.0), Some(60.0), "deposit"),
        row(4, -40.90, 173.80, Some(15.0), Some(25.0), "carnivore"),
    ];
    let now = Some(Timestamp::now());
    let snapshot = MarineReservoirSnapshot {
        created_by: None,
        id: id.to_owned(),
        source: "test".to_owned(),
        fetched_at: now,
        source_last_changed: Some(Timestamp::from_parts(2026, 5, 11, 0, 0, 0, 0)),
        record_count: rows_in.len() as i64,
        content_sha256: content_hash(&rows_in),
        citation: None,
        note: None,
        created_at: now,
    };
    state
        .store
        .write(|tx| {
            rows::insert(tx, &snapshot)?;
            for (i, r) in rows_in.iter().enumerate() {
                let delta_r = if tamper && i == 0 {
                    Some(999.0)
                } else {
                    r.delta_r
                };
                rows::insert(
                    tx,
                    &MarineReservoirPoint {
                        snapshot_id: id.to_owned(),
                        map_no: r.map_no,
                        latitude: r.latitude,
                        longitude: r.longitude,
                        delta_r,
                        delta_r_error: r.delta_r_error,
                        locality: r.locality.clone(),
                        feeding: r.feeding.clone(),
                        feeding_raw: r.feeding_raw.clone(),
                        ..MarineReservoirPoint::default()
                    },
                )?;
            }
            Ok(())
        })
        .unwrap();
    rows_in
}

#[tokio::test(flavor = "multi_thread")]
async fn snapshots_are_listed_verified_looked_up_and_guarded() {
    let dir = std::env::temp_dir().join(format!("radical-reservoir-{}", radical_store::new_id()));
    let state = radical_server::State::open(dir.join("radical.db"), 1).unwrap();
    let app = radical_server::app(state.clone());

    let (status, list) = call(&app, "GET", "/api/reservoir/snapshots", None).await;
    assert_eq!(status, 200);
    assert_eq!(list["data"], json!({"items": [], "has_bundled": false}));

    plant(&state, "test-good", false);
    plant(&state, "test-tampered", true);
    let (_, list) = call(&app, "GET", "/api/reservoir/snapshots", None).await;
    assert_eq!(list["data"]["items"].as_array().unwrap().len(), 2);

    let (status, good) = call(&app, "GET", "/api/reservoir/snapshots/test-good", None).await;
    assert_eq!(status, 200);
    assert_eq!(good["data"]["verified"], true);
    assert_eq!(good["data"]["loaded_count"], 4);
    assert_eq!(good["data"]["in_use_by"], 0);

    let (status, bad) = call(&app, "GET", "/api/reservoir/snapshots/test-tampered", None).await;
    assert_eq!(status, 200);
    assert_eq!(bad["data"]["verified"], false);
    assert!(
        bad["data"]["problem"]
            .as_str()
            .unwrap()
            .contains("does not match")
    );

    let (status, missing) = call(&app, "GET", "/api/reservoir/snapshots/nope", None).await;
    assert_eq!(
        (status, &missing["detail"]),
        (404, &json!("That snapshot was not found"))
    );

    // The lookup: the core's arithmetic (held to Python in its own units),
    // with the provenance that makes it citable.
    let ask = |snapshot: &str, extra: Value| {
        let mut body =
            json!({"latitude": -41.12, "longitude": 174.02, "snapshot_id": snapshot, "n": 4});
        for (k, v) in extra.as_object().unwrap() {
            body[k] = v.clone();
        }
        body
    };
    let (status, found) = call(
        &app,
        "POST",
        "/api/reservoir/lookup",
        Some(ask("test-good", json!({}))),
    )
    .await;
    assert_eq!(status, 200, "{found}");
    let data = &found["data"];
    assert_eq!(data["n"], 4);
    assert_eq!(data["delta_r_source"]["snapshot_id"], "test-good");
    assert_eq!(data["delta_r_source"]["method"], "nearest_n");
    assert_eq!(
        data["delta_r_source"]["map_nos"].as_array().unwrap().len(),
        4
    );
    assert!(data["suspension_equivalent"]["n"] == 2);

    let (status, refused) = call(
        &app,
        "POST",
        "/api/reservoir/lookup",
        Some(ask("test-good", json!({"max_km": 0.5}))),
    )
    .await;
    assert_eq!(status, 422);
    assert!(
        refused["detail"]
            .as_str()
            .unwrap()
            .starts_with("No reservoir measurements")
    );

    let (status, _) = call(
        &app,
        "POST",
        "/api/reservoir/lookup",
        Some(ask("test-tampered", json!({}))),
    )
    .await;
    assert_eq!(status, 409);
    let (status, _) = call(
        &app,
        "POST",
        "/api/reservoir/lookup",
        Some(ask("nope", json!({}))),
    )
    .await;
    assert_eq!(status, 404);
    let (status, invalid) = call(
        &app,
        "POST",
        "/api/reservoir/lookup",
        Some(json!({"latitude": 95})),
    )
    .await;
    assert_eq!(status, 422);
    assert_eq!(invalid["detail"][0]["type"], "less_than_equal");

    // A cited snapshot cannot be deleted; an uncited one can.
    state
        .store
        .write(|tx| {
            let analysis = rows::Analysis {
                id: "citing".to_owned(),
                name: "Cites it".to_owned(),
                analysis_type: "calibration".to_owned(),
                ..rows::Analysis::default()
            };
            rows::insert(tx, &analysis)?;
            rows::insert(
                tx,
                &RadiocarbonSample {
                    id: radical_store::new_id(),
                    analysis_id: "citing".to_owned(),
                    sample_name: "cites it".to_owned(),
                    delta_r_source: Some(json!({"snapshot_id": "test-good"})),
                    ..RadiocarbonSample::default()
                },
            )
        })
        .unwrap();
    let (status, refusal) = call(&app, "DELETE", "/api/reservoir/snapshots/test-good", None).await;
    assert_eq!(status, 409);
    assert!(
        refusal["detail"]
            .as_str()
            .unwrap()
            .starts_with("1 date records")
    );
    let (status, gone) = call(
        &app,
        "DELETE",
        "/api/reservoir/snapshots/test-tampered",
        None,
    )
    .await;
    assert_eq!(
        (status, &gone["data"]["deleted_snapshot_id"]),
        (200, &json!("test-tampered"))
    );
    let (status, _) = call(&app, "GET", "/api/reservoir/snapshots/test-tampered", None).await;
    assert_eq!(status, 404);
    let points = state
        .store
        .read(|c| {
            rows::select_where::<MarineReservoirPoint>(
                c,
                "snapshot_id = ?",
                ["test-tampered"],
                "rowid",
            )
        })
        .unwrap();
    assert!(points.is_empty(), "a deleted snapshot's points go with it");
    let _ = std::fs::remove_dir_all(dir);
}
