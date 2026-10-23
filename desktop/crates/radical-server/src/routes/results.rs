//! An analysis's results as downloads (`api/results.py`): the CSV in the
//! model's reading order, the JSON beside it, the OxCal model file, the
//! "P before" column, and the plot data. All are built from one assembly, so
//! no two can report a date differently.
//!
//! The PDF report is not here yet.

use std::collections::HashMap;

use axum::Router;
use axum::body::Bytes;
use axum::extract::{Path, Query, State as Extract};
use axum::http::{StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use radical_core::calibration::{SIGMA_1, SIGMA_2, hpd_ranges};
use radical_core::date_format::{self, era_label, format_age, format_range};
use radical_core::measurement::Curves;
use radical_core::model_tree::flatten_structure;
use radical_core::numeric::{argsort_quicksort, interp_one, sum};
use radical_core::pyfmt::{g, repr};
use radical_core::{HpdRange, Measurement, Mode};
use radical_io::library::{Library, StoredCurve};
use radical_io::oxcal_export::{self, Config};
use radical_store::rows::{
    self, Analysis, AnalysisResult, CalibrationCurve as CurveRow, ModelElement, Project,
    RadiocarbonSample,
};
use radical_store::rusqlite::Connection;
use serde_json::{Map, Value, json};

use crate::routes::analysis::{
    iso, measurements, migrated_tree, ranges_json, rows_to_tree, same_event_for_samples, walk_tree,
};
use crate::solve::element;
use crate::{ApiError, ApiResult, State, history, ok};

// --------------------------------------------------------------------------
// Coordinates
// --------------------------------------------------------------------------

/// Decimal places for each coordinate precision a project may set.
fn places(precision: &str) -> Option<u32> {
    match precision {
        "0.01" => Some(2),
        "0.1" => Some(1),
        "1" => Some(0),
        _ => None,
    }
}

/// A coordinate as it may leave its project (`_rounded_coordinate`): the
/// decimal written, rounded half to even, as `Decimal.quantize` does.
pub fn rounded_coordinate(value: Option<f64>, precision: &str) -> Option<f64> {
    let v = value?;
    let Some(places) = places(precision) else {
        return Some(v);
    };
    // Rust's Display is the shortest round-tripping decimal, as Python's
    // str() is, written out without an exponent.
    let text = format!("{v}");
    let (negative, digits) = match text.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, text.as_str()),
    };
    let (whole, frac) = digits.split_once('.').unwrap_or((digits, ""));
    let places = places as usize;
    let mut kept: Vec<u8> = whole
        .bytes()
        .chain(frac.bytes().chain(std::iter::repeat(b'0')).take(places))
        .map(|b| b - b'0')
        .collect();
    let rest: Vec<u8> = frac.bytes().skip(places).map(|b| b - b'0').collect();
    let round_up = match rest.first() {
        None => false,
        Some(&d) if d > 5 => true,
        Some(&d) if d < 5 => false,
        _ if rest[1..].iter().any(|&d| d > 0) => true,
        _ => kept.last().is_some_and(|d| d % 2 == 1),
    };
    if round_up {
        let mut i = kept.len();
        loop {
            if i == 0 {
                kept.insert(0, 1);
                break;
            }
            i -= 1;
            if kept[i] == 9 {
                kept[i] = 0;
            } else {
                kept[i] += 1;
                break;
            }
        }
    }
    let split = kept.len() - places;
    let mut out = String::new();
    if negative {
        out.push('-');
    }
    out.extend(kept[..split].iter().map(|d| char::from(b'0' + d)));
    if places > 0 {
        out.push('.');
        out.extend(kept[split..].iter().map(|d| char::from(b'0' + d)));
    }
    out.parse().ok()
}

// --------------------------------------------------------------------------
// The assembly every download is built from
// --------------------------------------------------------------------------

pub fn ranges_of(v: &Value) -> Vec<HpdRange> {
    v.as_array()
        .into_iter()
        .flatten()
        .map(|r| HpdRange {
            min: r["min"].as_f64().unwrap_or(f64::NAN),
            max: r["max"].as_f64().unwrap_or(f64::NAN),
            probability_percent: r["probability_percent"].as_f64().unwrap_or(f64::NAN),
        })
        .collect()
}

/// What a date calibrates to alone, for a Bayesian analysis
/// (`_unmodelled_ranges`); blank for anything else or a curve that no
/// longer resolves.
fn unmodelled_ranges(
    state: &State,
    s: Option<&RadiocarbonSample>,
    analysis_type: &str,
) -> (Value, Value) {
    let blank = (Value::Null, Value::Null);
    let Some(s) = s.filter(|_| analysis_type == "bayesian") else {
        return blank;
    };
    let Ok(curve) = state
        .curves
        .curve(s.curve_id.as_deref().unwrap_or_default())
    else {
        return blank;
    };
    let Ok(probs) = curve.calibrate(
        s.age_bp,
        s.error_1sigma,
        s.delta_r,
        s.delta_r_error,
        Mode::Standard,
    ) else {
        return blank;
    };
    let cal = curve.cal_ages();
    match (
        hpd_ranges(cal, &probs, SIGMA_1),
        hpd_ranges(cal, &probs, SIGMA_2),
    ) {
        (Ok(a), Ok(b)) => (ranges_json(&a), ranges_json(&b)),
        _ => blank,
    }
}

struct Gathered {
    analysis: Analysis,
    rows: Vec<Value>,
    samples: Vec<RadiocarbonSample>,
    precision: String,
}

/// `_gather_export_rows`: each result joined to the date it is of.
fn gather(state: &State, analysis_id: &str) -> ApiResult<Gathered> {
    let (analysis, results, samples, project) = state.store.read(|c| {
        let analysis: Option<Analysis> = rows::get(c, analysis_id)?;
        let results: Vec<AnalysisResult> =
            rows::select_where(c, "analysis_id = ?", [analysis_id], "rowid")?;
        let samples: Vec<RadiocarbonSample> =
            rows::select_where(c, "analysis_id = ?", [analysis_id], "rowid")?;
        let project: Option<Project> = match analysis.as_ref().and_then(|a| a.project_id.clone()) {
            Some(p) => rows::get(c, &p)?,
            None => None,
        };
        Ok((analysis, results, samples, project))
    })?;
    let analysis = analysis.ok_or_else(|| ApiError::not_found("Analysis not found"))?;
    let precision = project
        .map(|p| p.coordinate_precision)
        .filter(|p| !p.is_empty())
        .unwrap_or_else(|| "full".to_owned());
    let rows = results
        .iter()
        .map(|r| {
            let s = samples.iter().find(|s| Some(&s.id) == r.sample_id.as_ref());
            let (u68, u95) = unmodelled_ranges(state, s, &analysis.analysis_type);
            json!({
                "sample_id": r.sample_id,
                "sample_name": s.map(|s| &s.sample_name),
                "age_bp": s.map(|s| s.age_bp),
                "error_1sigma": s.map(|s| s.error_1sigma),
                "material_type": s.map(|s| &s.material_type),
                "sample_type": s.map(|s| &s.sample_type),
                "curve_id": s.and_then(|s| s.curve_id.clone()),
                "latitude": rounded_coordinate(s.and_then(|s| s.latitude), &precision),
                "longitude": rounded_coordinate(s.and_then(|s| s.longitude), &precision),
                "calibrated_age_bp": r.calibrated_age_bp,
                "posterior_mean": r.posterior_mean,
                "posterior_median": r.posterior_median,
                "posterior_std": r.posterior_std,
                "hpd_68_min": r.hpd_68_min,
                "hpd_68_max": r.hpd_68_max,
                "hpd_95_min": r.hpd_95_min,
                "hpd_95_max": r.hpd_95_max,
                "hpd_68_ranges": r.hpd_68_ranges,
                "hpd_95_ranges": r.hpd_95_ranges,
                "agreement_index": r.agreement_index,
                "outlier_probability": r.outlier_probability,
                "unmodelled_hpd_68_ranges": u68,
                "unmodelled_hpd_95_ranges": u95,
            })
        })
        .collect();
    Ok(Gathered {
        analysis,
        rows,
        samples,
        precision,
    })
}

/// One row of the model in reading order, with its date's export row or its
/// element's posterior.
struct StructureRow {
    kind: String,
    element_id: Option<String>,
    name: String,
    depth: usize,
    path: String,
    tpq_age_bp: Option<f64>,
    taq_age_bp: Option<f64>,
    sample_id: Option<String>,
    posterior: Option<Value>,
    result: Option<Value>,
}

fn tree_of(conn: &Connection, analysis_id: &str) -> radical_store::Result<Option<Value>> {
    let elements: Vec<ModelElement> =
        rows::select_where(conn, "analysis_id = ?", [analysis_id], "rowid")?;
    Ok(rows_to_tree(&elements))
}

/// `_structure_rows`: the stored tree flattened, never migrated -- an
/// export does not write.
fn structure(state: &State, g: &Gathered) -> ApiResult<Vec<StructureRow>> {
    let Some(tree) = state.store.read(|c| tree_of(c, &g.analysis.id))? else {
        return Ok(Vec::new());
    };
    let dates: Vec<Measurement> = g
        .samples
        .iter()
        .map(|s| Measurement {
            id: s.id.clone(),
            sample_name: Some(s.sample_name.clone()),
            element_id: s.element_id.clone(),
            group_id: s.group_id.clone(),
            tpq_age_bp: s.tpq_age_bp,
            taq_age_bp: s.taq_age_bp,
            ..Measurement::default()
        })
        .collect();
    let mut nodes = Vec::new();
    walk_tree(&tree, &mut nodes);
    let posterior_of = |id: &str| {
        nodes
            .iter()
            .find(|e| e["id"] == id)
            .map(|e| e["posterior"].clone())
            .filter(|p| !p.is_null())
    };
    Ok(flatten_structure(Some(&element(&tree)), &dates)
        .into_iter()
        .map(|r| {
            let sample_id = r.sample.map(|i| dates[i].id.clone());
            let is_date = r.kind == "date";
            StructureRow {
                posterior: if is_date || r.kind == "unassigned" {
                    None
                } else {
                    r.element_id.as_deref().and_then(posterior_of)
                },
                result: sample_id
                    .as_ref()
                    .and_then(|id| g.rows.iter().find(|row| row["sample_id"] == *id).cloned()),
                kind: r.kind,
                element_id: r.element_id,
                name: r.name,
                depth: r.depth,
                path: r.path,
                tpq_age_bp: r.tpq_age_bp,
                taq_age_bp: r.taq_age_bp,
                sample_id,
            }
        })
        .collect())
}

// --------------------------------------------------------------------------
// P before a calendar age
// --------------------------------------------------------------------------

const THRESHOLD_RANGE_BP: (f64, f64) = (-100.0, 60000.0);

/// `statistics.p_before`: the share of a stored series older than
/// `threshold`, by the trapezoid rule, cut exactly at the threshold.
fn p_before(distribution: &Value, threshold: f64) -> Option<f64> {
    let points = distribution.as_array()?;
    if points.len() < 2 {
        return None;
    }
    let xs: Vec<f64> = points
        .iter()
        .map(|p| p[0].as_f64().unwrap_or(f64::NAN))
        .collect();
    let ys: Vec<f64> = points
        .iter()
        .map(|p| p[1].as_f64().unwrap_or(f64::NAN))
        .collect();
    let order = argsort_quicksort(&xs);
    let ages: Vec<f64> = order.iter().map(|&i| xs[i]).collect();
    let density: Vec<f64> = order.iter().map(|&i| ys[i].max(0.0)).collect();
    let total = trapezoid(&density, &ages);
    if total.is_nan() || total <= 0.0 {
        return None;
    }
    if threshold <= ages[0] {
        return Some(1.0);
    }
    if threshold >= ages[ages.len() - 1] {
        return Some(0.0);
    }
    let at = interp_one(threshold, &ages, &density);
    let (mut ys, mut xs) = (vec![at], vec![threshold]);
    for (a, d) in ages.iter().zip(&density) {
        if *a > threshold {
            xs.push(*a);
            ys.push(*d);
        }
    }
    Some((trapezoid(&ys, &xs) / total).clamp(0.0, 1.0))
}

/// NumPy's `trapezoid`.
fn trapezoid(y: &[f64], x: &[f64]) -> f64 {
    let terms: Vec<f64> = (1..y.len())
        .map(|i| (x[i] - x[i - 1]) * (y[i] + y[i - 1]) / 2.0)
        .collect();
    sum(&terms)
}

/// A threshold from a query, refused when off every curve (`_threshold`).
pub fn threshold(raw: Option<f64>) -> ApiResult<Option<f64>> {
    let Some(t) = raw else { return Ok(None) };
    let (lo, hi) = THRESHOLD_RANGE_BP;
    if !(lo <= t && t <= hi) {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            format!("A threshold must lie between {lo:.0} and {hi:.0} cal BP."),
        ));
    }
    Ok(Some(t))
}

/// `_p_before`: every date's and every modelled element's.
pub fn p_before_all(state: &State, analysis_id: &str, t: f64) -> ApiResult<Value> {
    let (results, tree) = state.store.read(|c| {
        let results: Vec<AnalysisResult> =
            rows::select_where(c, "analysis_id = ?", [analysis_id], "rowid")?;
        Ok((results, tree_of(c, analysis_id)?))
    })?;
    let mut samples = Map::new();
    for r in &results {
        let p = r
            .probability_distribution
            .as_ref()
            .and_then(|d| p_before(d, t));
        samples.insert(r.sample_id.clone().unwrap_or_default(), json!(p));
    }
    let mut elements = Map::new();
    if let Some(tree) = &tree {
        let mut nodes = Vec::new();
        walk_tree(tree, &mut nodes);
        for e in nodes {
            let d = &e["posterior"]["probability_distribution"];
            if d.as_array().is_some_and(|a| !a.is_empty()) {
                elements.insert(
                    e["id"].as_str().unwrap_or_default().to_owned(),
                    json!(p_before(d, t)),
                );
            }
        }
    }
    Ok(json!({"cal_bp": t, "samples": samples, "elements": elements}))
}

// --------------------------------------------------------------------------
// The CSV
// --------------------------------------------------------------------------

/// Python's csv writer, excel dialect: quoted only where needed, CRLF.
#[derive(Default)]
struct Csv(String);

impl Csv {
    fn row(&mut self, cells: &[String]) {
        let one_empty = cells.len() == 1 && cells[0].is_empty();
        for (i, cell) in cells.iter().enumerate() {
            if i > 0 {
                self.0.push(',');
            }
            if one_empty || cell.contains([',', '"', '\r', '\n']) {
                self.0.push('"');
                self.0.push_str(&cell.replace('"', "\"\""));
                self.0.push('"');
            } else {
                self.0.push_str(cell);
            }
        }
        self.0.push_str("\r\n");
    }
}

/// A JSON value as Python's `str()` would write it into a cell.
fn cell(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True" } else { "False" }.to_owned(),
        Value::Number(n) => match n.as_i64() {
            Some(i) if !n.is_f64() => i.to_string(),
            _ => repr(n.as_f64().unwrap_or(f64::NAN)),
        },
        other => other.to_string(),
    }
}

fn opt_repr(v: Option<f64>) -> String {
    v.map(repr).unwrap_or_default()
}

/// `_format_hpd_ranges`: oldest first, with each range's share.
pub fn hpd_cell(ranges: &Value, lo: &Value, hi: &Value) -> String {
    let ranges = ranges_of(ranges);
    if !ranges.is_empty() {
        return ranges
            .iter()
            .map(|r| format!("{:.0}-{:.0} ({:.1}%)", r.max, r.min, r.probability_percent))
            .collect::<Vec<_>>()
            .join("; ");
    }
    match (lo.as_f64(), hi.as_f64()) {
        (Some(lo), Some(hi)) => format!("{hi:.0}-{lo:.0}"),
        _ => String::new(),
    }
}

const CSV_HEADER: [&str; 22] = [
    "Order",
    "Row Type",
    "Depth",
    "Structure Path",
    "Name",
    "Age BP",
    "1-Sigma Error",
    "Material",
    "Sample Type",
    "Curve",
    "Unmodelled 95.4% HPD",
    "Posterior Mean",
    "Posterior Median",
    "Posterior Std",
    "68.3% HPD",
    "95.4% HPD",
    "Agreement %",
    "Outlier %",
    "TPQ BP",
    "TAQ BP",
    "Latitude",
    "Longitude",
];

const FORMATTED_COLUMNS: [&str; 7] = [
    "Unmodelled 95.4% HPD",
    "Posterior Mean",
    "Posterior Median",
    "68.3% HPD",
    "95.4% HPD",
    "TPQ BP",
    "TAQ BP",
];

fn measurement_cells(row: Option<&Value>) -> Vec<String> {
    let Some(r) = row else {
        return vec![String::new(); 5];
    };
    [
        "age_bp",
        "error_1sigma",
        "material_type",
        "sample_type",
        "curve_id",
    ]
    .iter()
    .map(|k| cell(&r[*k]))
    .collect()
}

fn coordinate_cells(row: Option<&Value>) -> Vec<String> {
    let Some(r) = row else {
        return vec![String::new(); 2];
    };
    vec![cell(&r["latitude"]), cell(&r["longitude"])]
}

fn unmodelled_cell(row: Option<&Value>) -> String {
    row.map_or_else(String::new, |r| {
        hpd_cell(&r["unmodelled_hpd_95_ranges"], &Value::Null, &Value::Null)
    })
}

fn one_place(v: &Value) -> String {
    v.as_f64().map(|x| format!("{x:.1}")).unwrap_or_default()
}

fn posterior_cells(row: Option<&Value>) -> Vec<String> {
    let Some(r) = row else {
        return vec![String::new(); 7];
    };
    vec![
        one_place(&r["posterior_mean"]),
        one_place(&r["posterior_median"]),
        one_place(&r["posterior_std"]),
        hpd_cell(&r["hpd_68_ranges"], &r["hpd_68_min"], &r["hpd_68_max"]),
        hpd_cell(&r["hpd_95_ranges"], &r["hpd_95_min"], &r["hpd_95_max"]),
        one_place(&r["agreement_index"]),
        r["outlier_probability"]
            .as_f64()
            .map(|o| format!("{:.1}", 100.0 * o))
            .unwrap_or_default(),
    ]
}

fn formatted_cells(
    result: Option<&Value>,
    tpq: Option<f64>,
    taq: Option<f64>,
    date_format: &str,
) -> Vec<String> {
    let empty = Value::Null;
    let r = result.unwrap_or(&empty);
    let hpd = |k: &str| date_format::format_hpd_ranges(&ranges_of(&r[k]), date_format);
    vec![
        hpd("unmodelled_hpd_95_ranges"),
        format_age(r["posterior_mean"].as_f64(), date_format),
        format_age(r["posterior_median"].as_f64(), date_format),
        hpd("hpd_68_ranges"),
        hpd("hpd_95_ranges"),
        format_age(tpq, date_format),
        format_age(taq, date_format),
    ]
}

/// A boundary's posterior shaped as an export row (`_boundary_as_result`).
fn boundary_as_result(p: &Value) -> Value {
    json!({
        "posterior_mean": p["posterior_mean"],
        "posterior_median": p["posterior_median"],
        "posterior_std": p["posterior_std"],
        "hpd_68_ranges": p["hpd_68_ranges"],
        "hpd_95_ranges": p["hpd_95_ranges"],
        "hpd_68_min": p["hpd_68"][0],
        "hpd_68_max": p["hpd_68"][1],
        "hpd_95_min": p["hpd_95"][0],
        "hpd_95_max": p["hpd_95"][1],
        "agreement_index": null,
        "outlier_probability": null,
    })
}

const INTERVAL_HEADER: [&str; 11] = [
    "Row Type",
    "Name",
    "Compared With",
    "Mean (yr)",
    "Median (yr)",
    "Std (yr)",
    "68.3% Range (yr)",
    "95.4% Range (yr)",
    "Overlap Probability",
    "Overlap",
    "Dated Events",
];

fn interval_label(kind: &Value) -> String {
    match kind.as_str() {
        Some("sequence_duration") => "Sequence Duration".to_owned(),
        Some("phase_duration") => "Phase Duration".to_owned(),
        Some("hiatus_duration") => "Hiatus Duration".to_owned(),
        Some("sample_duration") => "Sample Duration".to_owned(),
        Some("phase_difference") => "Phase Difference".to_owned(),
        Some("date_difference") => "Date Difference".to_owned(),
        _ => cell(kind),
    }
}

fn whole_years(v: &Value) -> String {
    v.as_f64().map(|x| format!("{x:.0}")).unwrap_or_default()
}

/// `_years_ranges`: smallest first, since a duration is a length.
pub fn years_ranges(v: &Value) -> String {
    let mut ranges = ranges_of(v);
    if ranges.is_empty() {
        return String::new();
    }
    ranges.sort_by(|a, b| a.min.total_cmp(&b.min));
    ranges
        .iter()
        .map(|r| format!("{:.0}-{:.0} ({:.1}%)", r.min, r.max, r.probability_percent))
        .collect::<Vec<_>>()
        .join("; ")
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

fn str_or_blank(v: &Value) -> String {
    if truthy(v) { cell(v) } else { String::new() }
}

fn write_intervals(csv: &mut Csv, intervals: &Value) {
    if !truthy(intervals) {
        return;
    }
    let mut rows: Vec<Vec<String>> = Vec::new();
    for r in intervals["durations"].as_array().into_iter().flatten() {
        let y = &r["years"];
        rows.push(vec![
            interval_label(&r["kind"]),
            str_or_blank(&r["name"]),
            String::new(),
            whole_years(&y["mean"]),
            whole_years(&y["median"]),
            whole_years(&y["std"]),
            years_ranges(&y["hpd_68_ranges"]),
            years_ranges(&y["hpd_95_ranges"]),
            String::new(),
            String::new(),
            str_or_blank(&r["dates"]),
        ]);
    }
    for r in intervals["differences"].as_array().into_iter().flatten() {
        let y = &r["years"];
        let label = interval_label(&r["kind"]);
        let names = [str_or_blank(&r["from_name"]), str_or_blank(&r["to_name"])];
        if truthy(&r["shared_boundary"]) {
            let mut row = vec![label];
            row.extend(names);
            row.extend(vec![String::new(); 5]);
            row.push(String::new());
            row.push(radical_core::intervals::SHARED_BOUNDARY_TEXT.to_owned());
            row.push(String::new());
            rows.push(row);
            continue;
        }
        let mut row = vec![label];
        row.extend(names);
        row.extend([
            whole_years(&y["mean"]),
            whole_years(&y["median"]),
            whole_years(&y["std"]),
            years_ranges(&y["hpd_68_ranges"]),
            years_ranges(&y["hpd_95_ranges"]),
            r["overlap"]
                .as_f64()
                .map(|o| format!("{o:.3}"))
                .unwrap_or_default(),
            str_or_blank(&r["severity"]),
            String::new(),
        ]);
        rows.push(row);
    }
    if rows.is_empty() {
        return;
    }
    csv.row(&[]);
    csv.row(&strings(&INTERVAL_HEADER));
    for row in rows {
        csv.row(&row);
    }
}

fn strings(xs: &[&str]) -> Vec<String> {
    xs.iter().map(|s| (*s).to_owned()).collect()
}

fn band_cell(band: &Value) -> String {
    let b: Vec<f64> = band
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_f64)
        .collect();
    if b.is_empty() {
        return String::new();
    }
    let hi = b.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let lo = b.iter().copied().fold(f64::INFINITY, f64::min);
    format!("{hi:.0}-{lo:.0}")
}

fn write_age_depth(csv: &mut Csv, intervals: &Value, date_format: &str) {
    let records = intervals["age_depth"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    if records.is_empty() {
        return;
    }
    let era = era_label(date_format);
    let mut level_header = strings(&[
        "Row Type",
        "Sequence",
        "Level",
        "Depth",
        "Dated",
        "Median BP",
        "68.3% Band BP",
        "95.4% Band BP",
    ]);
    if let Some(era) = era {
        for name in ["Median BP", "68.3% Band BP", "95.4% Band BP"] {
            level_header.push(format!("{name} ({era})"));
        }
    }
    let (mut levels, mut rates) = (Vec::new(), Vec::new());
    for r in &records {
        let units = r["depth_units"]
            .as_str()
            .filter(|u| !u.is_empty())
            .unwrap_or("cm");
        for level in r["levels"].as_array().into_iter().flatten() {
            let mut row = vec![
                "Age-Depth Level".to_owned(),
                str_or_blank(&r["name"]),
                str_or_blank(&level["name"]),
                level["depth"]
                    .as_f64()
                    .map(|d| format!("{} {units}", g(d, 6)))
                    .unwrap_or_default(),
                if truthy(&level["dated"]) { "yes" } else { "no" }.to_owned(),
                whole_years(&level["median"]),
                band_cell(&level["band_68"]),
                band_cell(&level["band_95"]),
            ];
            if era.is_some() {
                let pair = |b: &Value| (b[0].as_f64(), b[1].as_f64());
                let (a68, b68) = pair(&level["band_68"]);
                let (a95, b95) = pair(&level["band_95"]);
                row.push(format_age(level["median"].as_f64(), date_format));
                row.push(format_range(a68, b68, date_format));
                row.push(format_range(a95, b95, date_format));
            }
            levels.push(row);
        }
        for step in r["accumulation"].as_array().into_iter().flatten() {
            let y = &step["years"];
            let band: Vec<f64> = step["rate_band_68"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_f64)
                .collect();
            let depth = |k: &str| {
                step[k]
                    .as_f64()
                    .map(|d| format!("{} {units}", g(d, 6)))
                    .unwrap_or_default()
            };
            rates.push(vec![
                "Accumulation Rate".to_owned(),
                str_or_blank(&r["name"]),
                depth("from_depth"),
                depth("to_depth"),
                depth("depth_gap"),
                whole_years(&y["median"]),
                years_ranges(&y["hpd_95_ranges"]),
                step["rate_median"]
                    .as_f64()
                    .map(|x| format!("{x:.2}"))
                    .unwrap_or_default(),
                if band.is_empty() {
                    String::new()
                } else {
                    let lo = band.iter().copied().fold(f64::INFINITY, f64::min);
                    let hi = band.iter().copied().fold(f64::NEG_INFINITY, f64::max);
                    format!("{lo:.2}-{hi:.2}")
                },
            ]);
        }
    }
    for (header, rows) in [
        (level_header, levels),
        (
            strings(&[
                "Row Type",
                "Sequence",
                "From",
                "To",
                "Depth Gap",
                "Elapsed Median (yr)",
                "Elapsed 95.4% (yr)",
                "Rate (yr per unit depth)",
                "Rate 68.3%",
            ]),
            rates,
        ),
    ] {
        if rows.is_empty() {
            continue;
        }
        csv.row(&[]);
        csv.row(&header);
        for row in rows {
            csv.row(&row);
        }
    }
}

fn write_kde(csv: &mut Csv, summaries: &Value, date_format: &str) {
    let records = summaries["kde"].as_array().cloned().unwrap_or_default();
    if records.is_empty() {
        return;
    }
    csv.row(&[]);
    csv.row(&strings(&[
        "Row Type",
        "Phase",
        "Dates",
        "Draws",
        "Kernel Width Median (yr)",
        "Kernel Width 68.3% (yr)",
        "Silverman Factor Median",
        "Reflected at Boundaries",
    ]));
    for k in &records {
        let w = &k["bandwidth"];
        csv.row(&[
            "KDE Summary".to_owned(),
            str_or_blank(&k["name"]),
            cell(&k["dates"]),
            cell(&k["draws"]),
            whole_years(&w["median"]),
            format!(
                "{}-{}",
                whole_years(&w["range_68"][0]),
                whole_years(&w["range_68"][1])
            ),
            k["factor"]["median"]
                .as_f64()
                .map(|x| format!("{x:.3}"))
                .unwrap_or_default(),
            if truthy(&k["reflected"]) { "yes" } else { "no" }.to_owned(),
        ]);
    }
    let era = era_label(date_format);
    csv.row(&[]);
    let mut header = strings(&["Row Type", "Phase", "Age BP", "Density", "Density 1 Sigma"]);
    if let Some(era) = era {
        header.push(format!("Age ({era})"));
    }
    csv.row(&header);
    for k in &records {
        for point in k["distribution"].as_array().into_iter().flatten() {
            let n = |i: usize| point[i].as_f64().unwrap_or(f64::NAN);
            let mut row = vec![
                "KDE".to_owned(),
                str_or_blank(&k["name"]),
                format!("{:.0}", n(0)),
                g(n(1), 6),
                g(n(2), 6),
            ];
            if era.is_some() {
                row.push(format_age(Some(n(0)), date_format));
            }
            csv.row(&row);
        }
    }
}

fn pooled_at(test: &Value, confidence: f64) -> Value {
    test["pooled_hpd_ranges"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|e| {
            e["confidence"]
                .as_f64()
                .is_some_and(|c| (c - confidence).abs() < 1e-9)
        })
        .map_or(Value::Null, |e| e["ranges"].clone())
}

fn write_same_event(csv: &mut Csv, tests: &Value, date_format: &str) {
    let tests = tests.as_array().cloned().unwrap_or_default();
    if tests.is_empty() {
        return;
    }
    let era = era_label(date_format);
    csv.row(&[]);
    let mut header = strings(&[
        "Row Type",
        "Dates",
        "Pooled Age BP",
        "Pooled Error",
        "T",
        "df",
        "5% Critical",
        "One Event",
        "Pooled 95.4% HPD",
        "Note",
    ]);
    if let Some(era) = era {
        header.push(format!("Pooled 95.4% HPD ({era})"));
    }
    csv.row(&header);
    for t in &tests {
        let tested = !t["t"].is_null();
        let fixed = |k: &str, places: usize| {
            if tested {
                t[k].as_f64()
                    .map(|x| format!("{x:.places$}"))
                    .unwrap_or_default()
            } else {
                String::new()
            }
        };
        let pooled = pooled_at(t, radical_core::SIGMA_2);
        let mut row = vec![
            "Same-Event Test".to_owned(),
            cell(&t["n"]),
            fixed("mean_bp", 0),
            fixed("error_bp", 0),
            fixed("t", 2),
            cell(&t["df"]),
            fixed("critical", 2),
            if tested {
                if truthy(&t["consistent"]) {
                    "yes"
                } else {
                    "no"
                }
                .to_owned()
            } else {
                String::new()
            },
            hpd_cell(&pooled, &Value::Null, &Value::Null),
            str_or_blank(&t["refused"]),
        ];
        if era.is_some() {
            row.push(if truthy(&pooled) {
                date_format::format_hpd_ranges(&ranges_of(&pooled), date_format)
            } else {
                String::new()
            });
        }
        csv.row(&row);
    }
}

/// Python's `str.title()`.
fn title(s: &str) -> String {
    let mut out = String::new();
    let mut after_letter = false;
    for c in s.chars() {
        if c.is_alphabetic() {
            if after_letter {
                out.extend(c.to_lowercase());
            } else {
                out.extend(c.to_uppercase());
            }
            after_letter = true;
        } else {
            out.push(c);
            after_letter = false;
        }
    }
    out
}

fn p_before_cell(
    values: Option<&Value>,
    kind: &str,
    sample_id: Option<&str>,
    element_id: Option<&str>,
) -> String {
    let Some(v) = values else {
        return String::new();
    };
    let p = if kind == "date" {
        sample_id.and_then(|id| v["samples"][id].as_f64())
    } else {
        element_id.and_then(|id| v["elements"][id].as_f64())
    };
    p.map(|p| format!("{p:.6}")).unwrap_or_default()
}

/// `_build_csv`.
fn build_csv(
    state: &State,
    analysis_id: &str,
    date_format: Option<&str>,
    p_before_bp: Option<f64>,
) -> ApiResult<String> {
    let date_format = date_format::normalise(date_format);
    let era = era_label(date_format);
    let t = threshold(p_before_bp)?;
    let g = gather(state, analysis_id)?;
    let p_values = match t {
        Some(t) => Some(p_before_all(state, analysis_id, t)?),
        None => None,
    };
    let mut csv = Csv::default();
    let mut header = strings(&CSV_HEADER);
    if let Some(era) = era {
        header.extend(FORMATTED_COLUMNS.iter().map(|n| format!("{n} ({era})")));
    }
    if let Some(t) = t {
        header.push(format!("P Before {}", format_age(Some(t), date_format)));
    }
    csv.row(&header);
    let structure = structure(state, &g)?;
    let same_event = same_event_for_samples(state, &g.analysis.analysis_type, &g.samples);
    if g.rows.is_empty() && structure.is_empty() {
        csv.row(&[format!(
            "No results for analysis \"{}\" ({})",
            g.analysis.name,
            g.analysis
                .status
                .as_deref()
                .map_or("None".to_owned(), str::to_owned)
        )]);
        return Ok(csv.0);
    }
    let extra = |result: Option<&Value>, tpq, taq| {
        if era.is_some() {
            formatted_cells(result, tpq, taq, date_format)
        } else {
            Vec::new()
        }
    };
    let threshold_cell = |kind: &str, sample: Option<&str>, element: Option<&str>| {
        if p_values.is_some() {
            vec![p_before_cell(p_values.as_ref(), kind, sample, element)]
        } else {
            Vec::new()
        }
    };
    let intervals = g.analysis.intervals.clone().unwrap_or(Value::Null);
    let summaries = g.analysis.summaries.clone().unwrap_or(Value::Null);
    let tail = |csv: &mut Csv| {
        write_intervals(csv, &intervals);
        write_age_depth(csv, &intervals, date_format);
        write_kde(csv, &summaries, date_format);
        write_same_event(csv, &same_event, date_format);
    };
    if structure.is_empty() {
        for (order, row) in g.rows.iter().enumerate() {
            let name = str_or_blank(&row["sample_name"]);
            let name = if name.is_empty() {
                str_or_blank(&row["sample_id"])
            } else {
                name
            };
            let mut cells = vec![
                (order + 1).to_string(),
                "Date".to_owned(),
                "0".to_owned(),
                String::new(),
                name,
            ];
            cells.extend(measurement_cells(Some(row)));
            cells.push(unmodelled_cell(Some(row)));
            cells.extend(posterior_cells(Some(row)));
            cells.extend([String::new(), String::new()]);
            cells.extend(coordinate_cells(Some(row)));
            cells.extend(extra(Some(row), None, None));
            cells.extend(threshold_cell("date", row["sample_id"].as_str(), None));
            csv.row(&cells);
        }
        tail(&mut csv);
        return Ok(csv.0);
    }
    for (order, row) in structure.iter().enumerate() {
        let (measured, modelled, unmodelled, result) = if row.kind == "date" {
            let r = row.result.as_ref();
            (
                measurement_cells(r),
                posterior_cells(r),
                unmodelled_cell(r),
                row.result.clone(),
            )
        } else if let Some(p) = &row.posterior {
            let r = boundary_as_result(p);
            (
                measurement_cells(None),
                posterior_cells(Some(&r)),
                String::new(),
                Some(r),
            )
        } else {
            (
                measurement_cells(None),
                posterior_cells(None),
                String::new(),
                None,
            )
        };
        let mut cells = vec![
            (order + 1).to_string(),
            title(&row.kind),
            row.depth.to_string(),
            row.path.clone(),
            row.name.clone(),
        ];
        cells.extend(measured);
        cells.push(unmodelled);
        cells.extend(modelled);
        cells.push(opt_repr(row.tpq_age_bp));
        cells.push(opt_repr(row.taq_age_bp));
        cells.extend(coordinate_cells(if row.kind == "date" {
            row.result.as_ref()
        } else {
            None
        }));
        cells.extend(extra(result.as_ref(), row.tpq_age_bp, row.taq_age_bp));
        cells.extend(threshold_cell(
            &row.kind,
            row.sample_id.as_deref(),
            row.element_id.as_deref(),
        ));
        csv.row(&cells);
    }
    tail(&mut csv);
    Ok(csv.0)
}

// --------------------------------------------------------------------------
// Routes
// --------------------------------------------------------------------------

/// The 404 every route answers for an analysis that is not there.
fn owned(state: &State, analysis_id: &str) -> ApiResult<Analysis> {
    state
        .store
        .read(|c| rows::get(c, analysis_id))?
        .ok_or_else(|| ApiError::not_found("That analysis was not found."))
}

/// A float query parameter, as FastAPI reads one.
fn query_float(q: &HashMap<String, String>, name: &str) -> ApiResult<Option<f64>> {
    let Some(raw) = q.get(name) else {
        return Ok(None);
    };
    match radical_io::python::float(raw) {
        Some(x) => Ok(Some(x)),
        None => Err(ApiError::invalid(vec![crate::problem(
            "float_parsing",
            &[json!("query"), json!(name)],
            "Input should be a valid number, unable to parse string as a number",
            Value::from(raw.as_str()),
            None,
        )])),
    }
}

fn download(content_type: &str, filename: String, body: String) -> Response {
    (
        [
            (header::CONTENT_TYPE, content_type.to_owned()),
            (header::CONTENT_DISPOSITION, filename),
        ],
        body,
    )
        .into_response()
}

fn csv_response(
    state: &State,
    analysis_id: &str,
    date_format: Option<&str>,
    p: Option<f64>,
    suffix: &str,
) -> ApiResult<Response> {
    owned(state, analysis_id)?;
    let text = build_csv(state, analysis_id, date_format, p)?;
    Ok(download(
        "text/csv; charset=utf-8",
        format!("attachment; filename=analysis-{analysis_id}-{suffix}.csv"),
        text,
    ))
}

async fn export_table(
    Extract(state): Extract<State>,
    Path(analysis_id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> ApiResult<Response> {
    let p = query_float(&q, "p_before_bp")?;
    csv_response(
        &state,
        &analysis_id,
        q.get("date_format").map(String::as_str),
        p,
        "table",
    )
}

async fn export_csv(
    Extract(state): Extract<State>,
    Path(analysis_id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> ApiResult<Response> {
    let p = query_float(&q, "p_before_bp")?;
    csv_response(
        &state,
        &analysis_id,
        q.get("date_format").map(String::as_str),
        p,
        "results",
    )
}

fn without_samples(p: &Value) -> Value {
    let mut p = p.clone();
    if let Some(o) = p.as_object_mut() {
        o.remove("posterior_samples");
    }
    p
}

pub fn json_payload(
    state: &State,
    analysis_id: &str,
    p_before_bp: Option<f64>,
) -> ApiResult<Value> {
    owned(state, analysis_id)?;
    let t = threshold(p_before_bp)?;
    let g = gather(state, analysis_id)?;
    let structure = structure(state, &g)?;
    let payload: Vec<Value> = structure
        .iter()
        .map(|r| {
            let mut e = json!({
                "kind": r.kind, "name": r.name, "depth": r.depth, "path": r.path,
                "element_id": r.element_id, "tpq_age_bp": r.tpq_age_bp,
                "taq_age_bp": r.taq_age_bp,
            });
            if r.kind == "date" {
                e["sample_id"] = json!(r.sample_id);
                e["result"] = r.result.clone().unwrap_or(Value::Null);
            } else if let Some(p) = &r.posterior {
                e["posterior"] = without_samples(p);
            }
            e
        })
        .collect();
    let a = &g.analysis;
    Ok(json!({
        "id": a.id,
        "name": a.name,
        "analysis_type": a.analysis_type,
        "status": a.status,
        "created_at": iso(a.created_at),
        "project_id": a.project_id,
        "results": g.rows,
        "coordinate_precision": g.precision,
        "structure": payload,
        "intervals": a.intervals,
        "summaries": a.summaries,
        "solver": a.solver,
        "same_event": same_event_for_samples(state, &a.analysis_type, &g.samples),
        "p_before": match t {
            Some(t) => p_before_all(state, analysis_id, t)?,
            None => Value::Null,
        },
    }))
}

async fn export_json(
    Extract(state): Extract<State>,
    Path(analysis_id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> ApiResult {
    let p = query_float(&q, "p_before_bp")?;
    Ok(ok(json_payload(&state, &analysis_id, p)?))
}

async fn get_p_before(
    Extract(state): Extract<State>,
    Path(analysis_id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> ApiResult {
    let cal_bp = query_float(&q, "cal_bp")?;
    let Some(cal_bp) = cal_bp else {
        return Err(ApiError::invalid(vec![crate::problem(
            "missing",
            &[json!("query"), json!("cal_bp")],
            "Field required",
            Value::Null,
            None,
        )]));
    };
    owned(&state, &analysis_id)?;
    let t = threshold(Some(cal_bp))?.expect("given");
    Ok(ok(p_before_all(&state, &analysis_id, t)?))
}

/// The library of curves a file may name: the built-ins and every stored
/// curve, oldest first.
pub fn library(state: &State) -> radical_store::Result<Library<'_>> {
    let rows: Vec<CurveRow> = state
        .store
        .read(|c| rows::select_where(c, "1 = 1", [], "created_at, rowid"))?;
    let stored = rows
        .into_iter()
        .map(|r| StoredCurve {
            names: radical_core::curve_registry::StoredNames {
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
            },
            archived: r.archived,
            recipe: r
                .recipe
                .as_ref()
                .filter(|_| r.kind == "mixed")
                .and_then(|rec| {
                    Some(radical_core::curve_registry::MixRecipe {
                        id: r.id.clone(),
                        a: rec["a"].as_str()?.to_owned(),
                        b: rec["b"].as_str()?.to_owned(),
                        percent_a: rec["percent_a"].as_f64().unwrap_or(50.0),
                        percent_a_error: rec["percent_a_error"].as_f64().unwrap_or(0.0),
                    })
                }),
            source_filename: r.source_filename.clone(),
        })
        .collect();
    Ok(Library::new(stored, &*state.curves))
}

/// Today's date, YYYY-MM-DD, in UTC.
fn today() -> String {
    radical_store::Timestamp::now().isoformat()[..10].to_owned()
}

async fn export_oxcal(
    Extract(state): Extract<State>,
    Path(analysis_id): Path<String>,
) -> ApiResult<Response> {
    let analysis = owned(&state, &analysis_id)?;
    let (samples, tree, migrated) = state.store.read(|c| {
        let samples = measurements(c, &analysis_id)?;
        let tree = tree_of(c, &analysis_id)?;
        let migrated = if tree.is_none() && analysis.analysis_type == "bayesian" {
            migrated_tree(c, &analysis_id, &samples)?
        } else {
            None
        };
        Ok((samples, tree, migrated))
    })?;
    let (tree, element_ids) = match (tree, migrated) {
        (Some(t), _) => (Some(t), None),
        (None, Some((t, _, ids))) => (Some(t), Some(ids)),
        (None, None) => (None, None),
    };
    let dates: Vec<Measurement> = samples
        .iter()
        .map(|s| Measurement {
            id: s.id.clone(),
            sample_name: Some(s.sample_name.clone()),
            age_bp: s.age_bp,
            error_1sigma: s.error_1sigma,
            curve_id: s.curve_id.clone(),
            delta_r: s.delta_r,
            delta_r_error: s.delta_r_error,
            outlier_prior: s.outlier_prior,
            outlier_model: s.outlier_model.clone(),
            depth_cm: s.depth_cm,
            ring_offset: s.ring_offset,
            ring_offset_error: s.ring_offset_error,
            tpq_age_bp: s.tpq_age_bp,
            taq_age_bp: s.taq_age_bp,
            inbuilt_years: s.inbuilt_years,
            inbuilt_shape: s.inbuilt_shape.clone(),
            element_id: match &element_ids {
                Some(ids) => ids.get(&s.id).cloned(),
                None => s.element_id.clone(),
            },
            ..Measurement::default()
        })
        .collect();
    let config = analysis.bayesian_config.clone().unwrap_or(Value::Null);
    let config = Config {
        resolution: config["resolution"].as_f64(),
        uniform_span_prior: config["uniform_span_prior"].as_bool(),
    };
    let library = library(&state)?;
    let root = tree.as_ref().map(element);
    let (text, omitted) = oxcal_export::write_oxcal(
        &analysis.name,
        &analysis.analysis_type,
        &dates,
        root.as_ref(),
        &config,
        history::app_commit(),
        &today(),
        &library,
    )
    .map_err(ApiError::internal)?;
    let stem = filename_stem(&analysis.name);
    Ok((
        [
            (header::CONTENT_TYPE, "text/plain; charset=utf-8".to_owned()),
            (
                header::CONTENT_DISPOSITION,
                format!("attachment; filename=\"{stem}.oxcal\""),
            ),
            (
                header::HeaderName::from_static("x-radical-not-written"),
                omitted.len().to_string(),
            ),
            (
                header::ACCESS_CONTROL_EXPOSE_HEADERS,
                "X-RadiCal-Not-Written".to_owned(),
            ),
        ],
        text,
    )
        .into_response())
}

/// `re.sub(r'[^A-Za-z0-9._-]+', '-', name).strip('-.')`, or 'analysis'.
fn filename_stem(name: &str) -> String {
    let mut out = String::new();
    let mut in_run = false;
    for c in name.chars() {
        if c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-') {
            out.push(c);
            in_run = false;
        } else if !in_run {
            out.push('-');
            in_run = true;
        }
    }
    let stem = out.trim_matches(|c| c == '-' || c == '.');
    if stem.is_empty() {
        "analysis".to_owned()
    } else {
        stem.to_owned()
    }
}

async fn get_graphs(Extract(state): Extract<State>, Path(analysis_id): Path<String>) -> ApiResult {
    owned(&state, &analysis_id)?;
    let results: Vec<AnalysisResult> = state
        .store
        .read(|c| rows::select_where(c, "analysis_id = ?", [&analysis_id], "rowid"))?;
    Ok(ok(json!({
        "graphs": results.iter().map(|r| json!({
            "sample_id": r.sample_id,
            "probability_distribution": r.probability_distribution,
        })).collect::<Vec<_>>(),
    })))
}

/// The CSV or the JSON, chosen in the body.
async fn export_results(
    Extract(state): Extract<State>,
    Path(analysis_id): Path<String>,
    body: Bytes,
) -> ApiResult<Response> {
    let input = crate::validate::json_body(&body)?;
    let Some(options) = input.as_object() else {
        return Err(ApiError::invalid(vec![crate::problem(
            "dict_type",
            &[json!("body")],
            "Input should be a valid dictionary",
            input.clone(),
            None,
        )]));
    };
    let format = options.get("format").map_or("csv".to_owned(), cell_text);
    let p = options.get("p_before_bp").and_then(Value::as_f64);
    let date_format = options.get("date_format").and_then(Value::as_str);
    match format.as_str() {
        "csv" => csv_response(&state, &analysis_id, date_format, p, "results"),
        "json" => Ok(ok(json_payload(&state, &analysis_id, p)?).into_response()),
        // The PDF report is not ported yet: answered as the route it will be.
        "pdf" => Err(ApiError::not_found("Not Found")),
        other => Err(ApiError::bad_request(format!(
            "Unsupported format: {other}"
        ))),
    }
}

/// A body value as Python's f-string would print it.
fn cell_text(v: &Value) -> String {
    match v {
        Value::Null => "None".to_owned(),
        other => cell(other),
    }
}

pub fn router() -> Router<State> {
    Router::new()
        .route("/api/results/{analysis_id}/table", get(export_table))
        .route("/api/results/{analysis_id}/export/csv", get(export_csv))
        .route("/api/results/{analysis_id}/export/json", get(export_json))
        .route("/api/results/{analysis_id}/p-before", get(get_p_before))
        .route("/api/results/{analysis_id}/export/oxcal", get(export_oxcal))
        .route("/api/results/{analysis_id}/graphs", get(get_graphs))
        .route("/api/results/{analysis_id}/export", post(export_results))
}
