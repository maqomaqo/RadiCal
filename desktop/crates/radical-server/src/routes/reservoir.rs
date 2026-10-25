//! The marine reservoir snapshots (`api/reservoir.py`): a local mirror of
//! the reservoir database, and the ΔR lookup it makes possible without
//! sending a site's position anywhere.
//!
//! A snapshot's numbers never change -- correcting one means ingesting a new
//! one -- and a snapshot a date cites cannot be deleted. The ingest is the
//! only code that talks to the internet, and runs only when asked.

use axum::Router;
use axum::body::Bytes;
use axum::extract::{Path, State as Extract};
use axum::http::StatusCode;
use axum::routing::{get, post};
use radical_core::reservoir::{Combination, Lookup, LookupOptions, ReservoirPoint, lookup};
use radical_io::reservoir_ingest::{
    self, IngestReport, ReservoirRow, content_hash, records_to_rows, snapshot_id,
};
use radical_store::Timestamp;
use radical_store::rows::{
    self, MarineReservoirPoint, MarineReservoirSnapshot, ProjectSample, RadiocarbonSample,
};
use serde_json::{Map, Value, json};

use crate::calib::{self, FetchError};
use crate::validate::{self, Bounds, Problems};
use crate::{ApiError, ApiResult, State, ok};

const SOURCE: &str = "calib.org/marine (CHRONO Marine20 Reservoir Database)";
const SOURCE_SLUG: &str = "calib-marine20";
const CITATION: &str = "Reimer and Reimer 2001, Radiocarbon 43:461-3";

fn not_found() -> ApiError {
    ApiError::not_found("That snapshot was not found")
}

fn spec(s: &MarineReservoirSnapshot) -> Value {
    json!({
        "id": s.id,
        "kind": "ingested",
        "source": s.source,
        "record_count": s.record_count,
        "content_sha256": s.content_sha256,
        "citation": s.citation,
        "note": s.note,
        "fetched_at": s.fetched_at.map(|t| t.isoformat()),
        "source_last_changed": s.source_last_changed.map(|t| t.isoformat()),
        "created_by": null,
    })
}

fn snapshots(state: &State) -> radical_store::Result<Vec<MarineReservoirSnapshot>> {
    state
        .store
        .read(|c| rows::select_where(c, "1 = 1", [], "created_at DESC"))
}

fn snapshot(state: &State, id: &str) -> radical_store::Result<Option<MarineReservoirSnapshot>> {
    state.store.read(|c| rows::get(c, id))
}

/// A snapshot's points, verified against the hash it was stored with
/// (`reservoir_registry.resolve`); Err is the refusal to use it.
fn resolve(
    state: &State,
    s: &MarineReservoirSnapshot,
) -> ApiResult<Result<Vec<ReservoirRow>, String>> {
    let points: Vec<MarineReservoirPoint> = state
        .store
        .read(|c| rows::select_where(c, "snapshot_id = ?", [&s.id], "rowid"))?;
    let rows = points
        .into_iter()
        .map(|p| ReservoirRow {
            map_no: p.map_no,
            latitude: p.latitude,
            longitude: p.longitude,
            delta_r: p.delta_r,
            delta_r_error: p.delta_r_error,
            reference: p.reference,
            locality: p.locality,
            subregion: p.subregion,
            notes: p.notes,
            c14_age: p.c14_age,
            c14_error: p.c14_error,
            lab_id: p.lab_id,
            collection_year: p.collection_year,
            reservoir_age: p.reservoir_age,
            genus: p.genus,
            species: p.species,
            feeding: p.feeding,
            feeding_raw: p.feeding_raw,
        })
        .collect();
    Ok(reservoir_ingest::verify(&s.id, rows, &s.content_sha256).map_err(|e| e.0))
}

/// How many dates cite this snapshot in their ΔR provenance
/// (`reservoir_registry.in_use`).
fn in_use(state: &State, id: &str) -> radical_store::Result<usize> {
    let needle = format!("%\"{id}\"%");
    state.store.read(|c| {
        let samples: Vec<RadiocarbonSample> = rows::select_where(
            c,
            "delta_r_source IS NOT NULL AND CAST(delta_r_source AS TEXT) LIKE ?",
            [&needle],
            "rowid",
        )?;
        let collected: Vec<ProjectSample> = rows::select_where(
            c,
            "delta_r_source IS NOT NULL AND CAST(delta_r_source AS TEXT) LIKE ?",
            [&needle],
            "rowid",
        )?;
        Ok(samples.len() + collected.len())
    })
}

/// Every snapshot this installation has.
async fn list_snapshots(Extract(state): Extract<State>) -> ApiResult {
    Ok(ok(json!({
        "items": snapshots(&state)?.iter().map(spec).collect::<Vec<_>>(),
        "has_bundled": false,
    })))
}

/// One snapshot, and whether it still verifies.
async fn get_snapshot(Extract(state): Extract<State>, Path(id): Path<String>) -> ApiResult {
    let s = snapshot(&state, &id)?.ok_or_else(not_found)?;
    let mut payload = spec(&s);
    match resolve(&state, &s)? {
        Ok(rows) => {
            payload["verified"] = json!(true);
            payload["loaded_count"] = json!(rows.len());
        }
        Err(problem) => {
            payload["verified"] = json!(false);
            payload["problem"] = json!(problem);
        }
    }
    payload["in_use_by"] = json!(in_use(&state, &id)?);
    Ok(ok(payload))
}

fn fetch_failed(e: FetchError) -> ApiError {
    match e {
        FetchError::Unavailable(m) => ApiError::new(StatusCode::SERVICE_UNAVAILABLE, m),
        FetchError::Unreadable(m) => ApiError::new(StatusCode::BAD_GATEWAY, m),
    }
}

/// What calib.org holds now, beside what this installation holds: an
/// invitation to re-ingest, never an act of one.
async fn check_freshness(Extract(state): Extract<State>) -> ApiResult {
    let current = snapshots(&state)?
        .into_iter()
        .filter(|s| s.source_last_changed.is_some())
        .max_by_key(|s| s.source_last_changed.map(|t| t.micros));
    let remote = tokio::task::spawn_blocking(calib::fetch_freshness)
        .await
        .map_err(ApiError::internal)?
        .map_err(fetch_failed)?;
    let mine_count = current.as_ref().map(|s| s.record_count);
    let mine_changed = current
        .as_ref()
        .and_then(|s| s.source_last_changed.map(|t| t.isoformat()));
    let remote_changed = remote.source_last_changed.map(|t| t.isoformat());
    let behind = current.is_none()
        || matches!((&remote_changed, &mine_changed), (Some(r), Some(m)) if r > m)
        || mine_count.is_some_and(|n| n != remote.record_count);
    Ok(ok(json!({
        "source": remote.to_json(),
        "mine": current.map(|s| json!({
            "snapshot_id": s.id,
            "record_count": mine_count,
            "source_last_changed": mine_changed,
        })),
        "behind": behind,
    })))
}

fn stamp(t: &reservoir_ingest::Timestamp) -> Timestamp {
    Timestamp::from_parts(
        i64::from(t.year),
        t.month,
        t.day,
        t.hour,
        t.minute,
        t.second,
        0,
    )
}

/// Write one snapshot and its points, or nothing (`reservoir_ingest.store`).
fn store(
    state: &State,
    rows_in: &[ReservoirRow],
    report: &mut IngestReport,
) -> ApiResult<MarineReservoirSnapshot> {
    let changed = report.source_last_changed.unwrap_or_else(|| {
        let iso = Timestamp::now().isoformat();
        let n = |r: std::ops::Range<usize>| iso[r].parse::<u32>().unwrap_or(0);
        reservoir_ingest::Timestamp {
            year: n(0..4),
            month: n(5..7),
            day: n(8..10),
            hour: 0,
            minute: 0,
            second: 0,
        }
    });
    let id = snapshot_id(SOURCE_SLUG, &changed);
    if snapshot(state, &id)?.is_some() {
        return Err(ApiError::conflict(format!(
            "Snapshot '{id}' is already stored. A snapshot's numbers never change, so re-fetching an unchanged database has nothing to add."
        )));
    }
    report.content_sha256 = content_hash(rows_in);
    let now = Some(Timestamp::now());
    let row = MarineReservoirSnapshot {
        created_by: None,
        id: id.clone(),
        source: SOURCE.to_owned(),
        fetched_at: now,
        source_last_changed: report.source_last_changed.as_ref().map(stamp),
        record_count: rows_in.len() as i64,
        content_sha256: report.content_sha256.clone(),
        citation: Some(CITATION.to_owned()),
        note: None,
        created_at: now,
    };
    state.store.write(|tx| {
        rows::insert(tx, &row)?;
        for r in rows_in {
            rows::insert(
                tx,
                &MarineReservoirPoint {
                    snapshot_id: id.clone(),
                    map_no: r.map_no,
                    latitude: r.latitude,
                    longitude: r.longitude,
                    delta_r: r.delta_r,
                    delta_r_error: r.delta_r_error,
                    reference: r.reference.clone(),
                    locality: r.locality.clone(),
                    subregion: r.subregion.clone(),
                    notes: r.notes.clone(),
                    c14_age: r.c14_age,
                    c14_error: r.c14_error,
                    lab_id: r.lab_id.clone(),
                    collection_year: r.collection_year,
                    reservoir_age: r.reservoir_age,
                    genus: r.genus.clone(),
                    species: r.species.clone(),
                    feeding: r.feeding.clone(),
                    feeding_raw: r.feeding_raw.clone(),
                },
            )?;
        }
        Ok(())
    })?;
    Ok(row)
}

/// Fetch the reservoir database and store what comes back, all or nothing;
/// the report is the answer either way.
async fn create_snapshot(Extract(state): Extract<State>) -> ApiResult<axum::Json<Value>> {
    let (records, declared) = tokio::task::spawn_blocking(calib::fetch_records)
        .await
        .map_err(ApiError::internal)?
        .map_err(fetch_failed)?;
    let (rows_in, mut report) = records_to_rows(&records, declared);
    if !report.ok() {
        return Ok(axum::Json(
            json!({"success": false, "data": report.to_json()}),
        ));
    }
    let s = store(&state, &rows_in, &mut report)?;
    Ok(ok(
        json!({"snapshot": spec(&s), "report": report.to_json()}),
    ))
}

/// Remove a snapshot, if nothing cites it.
async fn delete_snapshot(Extract(state): Extract<State>, Path(id): Path<String>) -> ApiResult {
    snapshot(&state, &id)?.ok_or_else(not_found)?;
    let citing = in_use(&state, &id)?;
    if citing > 0 {
        let (s, verb) = if citing != 1 { ("s", "") } else { ("", "s") };
        return Err(ApiError::conflict(format!(
            "{citing} date{s} record{verb} a ΔR derived from '{id}'. Deleting it would leave those dates citing a snapshot that no longer exists, and the offsets they carry could no longer be reproduced."
        )));
    }
    state.store.write(|tx| {
        rows::delete_where::<MarineReservoirPoint>(tx, "snapshot_id = ?", [&id])?;
        rows::delete_where::<MarineReservoirSnapshot>(tx, "id = ?", [&id])
    })?;
    Ok(ok(json!({"deleted_snapshot_id": id})))
}

fn combination_json(c: &Combination) -> Value {
    json!({
        "delta_r": c.delta_r, "delta_r_error": c.delta_r_error, "n": c.n,
        "map_nos": c.map_nos, "internal": c.internal, "scatter": c.scatter,
        "excluded_no_error": c.excluded_no_error,
    })
}

fn feeding(l: &Lookup) -> (Value, Value) {
    (
        Value::Object(
            l.feeding_counts
                .iter()
                .map(|(k, n)| (k.clone(), json!(n)))
                .collect::<Map<_, _>>(),
        ),
        Value::Object(
            l.feeding_raw
                .iter()
                .map(|(k, v)| (k.to_string(), json!(v)))
                .collect::<Map<_, _>>(),
        ),
    )
}

/// `Lookup.as_dict`.
fn lookup_json(l: &Lookup) -> Value {
    let (counts, raw) = feeding(l);
    json!({
        "delta_r": l.delta_r,
        "delta_r_error": l.delta_r_error,
        "n": l.n,
        "snapshot_id": l.snapshot_id,
        "centre": {"lat": l.centre.0, "lon": l.centre.1},
        "nearest_km": l.nearest_km,
        "farthest_km": l.farthest_km,
        "excluded_no_error": l.excluded_no_error,
        "excluded_by_user": l.excluded_by_user,
        "feeding": counts,
        "feeding_raw": raw,
        "included_all": combination_json(&l.included_all),
        "suspension_equivalent": l.suspension_equivalent.as_ref().map(combination_json),
        "feeding_shift": l.feeding_shift(),
        "feeding_shift_matters": l.feeding_shift_matters(),
        "feeding_shift_warn_years": radical_core::reservoir::FEEDING_SHIFT_WARN_YEARS,
        "points": l.points.iter().map(|p| json!({
            "map_no": p.map_no, "latitude": p.latitude, "longitude": p.longitude,
            "delta_r": p.delta_r, "delta_r_error": p.delta_r_error,
            "feeding": p.feeding, "feeding_raw": p.feeding_raw,
            "locality": p.locality, "reference": p.reference,
            "distance_km": p.distance_km,
        })).collect::<Vec<_>>(),
    })
}

/// The `delta_r_source` record that makes a derived ΔR citable
/// (`reservoir.provenance`).
fn provenance(l: &Lookup, max_km: Option<f64>, suspension_only: bool) -> Value {
    let (counts, raw) = feeding(l);
    let counterfactual = l
        .suspension_equivalent
        .as_ref()
        .filter(|s| s.delta_r.is_some())
        .map(|s| json!({"delta_r": s.delta_r, "delta_r_error": s.delta_r_error, "n": s.n}));
    let now = Timestamp::now().isoformat();
    json!({
        "method": "nearest_n",
        "snapshot_id": l.snapshot_id,
        "centre": {"lat": l.centre.0, "lon": l.centre.1},
        "n": l.n,
        "map_nos": l.included_all.map_nos,
        "excluded_no_error": l.excluded_no_error,
        "excluded_by_user": l.excluded_by_user,
        "filters": {
            "feeding_preset": if suspension_only { Some("suspension_only") } else { None },
            "max_km": max_km,
        },
        "feeding": counts,
        "feeding_raw": raw,
        "counterfactual_suspension_only": counterfactual,
        "nearest_km": l.nearest_km,
        "farthest_km": l.farthest_km,
        "computed_at": format!("{now}+00:00"),
    })
}

/// Derive a ΔR for a position from a snapshot, without telling anybody
/// where it is. Writes nothing.
async fn lookup_delta_r(Extract(state): Extract<State>, body: Bytes) -> ApiResult {
    let input = validate::json_body(&body)?;
    let mut problems = Problems::default();
    let mut f = validate::body(&input, "LookupRequest", &mut problems);
    let latitude = f.float("latitude", Bounds::range(-90.0, 90.0));
    let longitude = f.float("longitude", Bounds::range(-180.0, 180.0));
    let snapshot_id = f.string("snapshot_id", None, None);
    let n = f.int_or("n", 10, Bounds::range(1.0, 50.0));
    let max_km = f.opt_float("max_km", Bounds::above(0.0, 20000.0));
    let exclude: Option<Vec<i64>> = match f.raw("exclude_map_nos") {
        None => Some(Vec::new()),
        Some(Value::Array(items)) if items.len() <= 50 => {
            let parsed: Option<Vec<i64>> = items.iter().map(Value::as_i64).collect();
            if parsed.is_none() {
                f.value_error(Some("exclude_map_nos"), "map numbers must be whole numbers");
            }
            parsed
        }
        Some(_) => {
            let _ = f.str_list("exclude_map_nos", None, Some(50));
            None
        }
    };
    let suspension_only = f.bool_or("suspension_only", false);
    problems.finish()?;
    let snapshot_id = snapshot_id.unwrap_or_default();
    let s = snapshot(&state, &snapshot_id)?.ok_or_else(not_found)?;
    let rows_in = resolve(&state, &s)?.map_err(ApiError::conflict)?;
    let points: Vec<ReservoirPoint> = rows_in
        .into_iter()
        .map(|r| ReservoirPoint {
            map_no: r.map_no,
            latitude: r.latitude,
            longitude: r.longitude,
            delta_r: r.delta_r,
            delta_r_error: r.delta_r_error,
            feeding: r.feeding,
            feeding_raw: r.feeding_raw,
            locality: r.locality,
            reference: r.reference,
            distance_km: None,
        })
        .collect();
    let suspension_only = suspension_only.unwrap_or(false);
    let options = LookupOptions {
        n: n.unwrap_or(10) as usize,
        snapshot_id: Some(snapshot_id),
        max_km,
        exclude_map_nos: exclude.unwrap_or_default(),
        suspension_only,
    };
    let result = lookup(
        latitude.unwrap_or_default(),
        longitude.unwrap_or_default(),
        &points,
        &options,
    )
    .map_err(|m| ApiError::new(StatusCode::UNPROCESSABLE_ENTITY, m))?;
    let mut payload = lookup_json(&result);
    payload["delta_r_source"] = provenance(&result, max_km, suspension_only);
    Ok(ok(payload))
}

pub fn router() -> Router<State> {
    Router::new()
        .route(
            "/api/reservoir/snapshots",
            get(list_snapshots).post(create_snapshot),
        )
        .route(
            "/api/reservoir/snapshots/{snapshot_id}",
            get(get_snapshot).delete(delete_snapshot),
        )
        .route("/api/reservoir/freshness", get(check_freshness))
        .route("/api/reservoir/lookup", post(lookup_delta_r))
}
