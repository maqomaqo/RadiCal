//! A completed analysis as a printable report (`services/pdf_report.py`):
//! the rows the results page and the CSV show, in the model's reading order,
//! each modelled parameter's posterior drawn beside them, and a method note
//! naming the curves and solver behind the numbers.
//!
//! Nothing here reads the store. `routes/results.rs` assembles the same rows
//! for the CSV, the JSON and this, so the three cannot disagree.

use radical_core::Mode;
use radical_core::calibration::SIGMA_2;
use radical_core::date_format::{self, era_label, format_age, format_range};
use radical_core::measurement::Curves;
use radical_store::rows::{Analysis, RadiocarbonSample};
use serde_json::{Map, Value, json};

use crate::State;
use crate::pdf::layout::{Block, CONTENT_WIDTH, Para, RowLook, Style, Table, TableStyle};
use crate::pdf::plot::{self, ACTIVITY_ROW, FIGURE_ASPECT, Strip};
use crate::pdf::{self, Color, hex};
use crate::routes::results::{StructureRow, ranges_of};

fn ink() -> Color {
    hex(plot::INK)
}

fn grey() -> Color {
    hex("#6b625d")
}

fn style(size: f64, leading: f64, bold: bool, color: Color, before: f64, after: f64) -> Style {
    Style {
        size,
        leading,
        bold,
        color,
        space_before: before,
        space_after: after,
        indent: 0.0,
    }
}

struct Styles {
    title: Style,
    subtitle: Style,
    heading: Style,
    body: Style,
    note: Style,
    th: Style,
    td: Style,
    caption: Style,
}

fn styles() -> Styles {
    Styles {
        title: style(17.0, 21.0, true, ink(), 0.0, 2.0),
        subtitle: style(9.0, 12.0, false, grey(), 0.0, 10.0),
        heading: style(12.0, 15.0, true, ink(), 12.0, 5.0),
        body: style(8.5, 11.5, false, ink(), 0.0, 4.0),
        note: style(8.0, 10.5, false, grey(), 0.0, 6.0),
        th: style(7.5, 9.5, true, ink(), 0.0, 0.0),
        td: style(7.5, 9.5, false, ink(), 0.0, 0.0),
        caption: style(9.0, 12.0, true, ink(), 6.0, 1.0),
    }
}

fn para(text: &str, s: Style) -> Block {
    Block::Para(Para::new(text, s, CONTENT_WIDTH))
}

// --- value formatting ----------------------------------------------------

fn number(v: &Value, places: usize) -> String {
    v.as_f64()
        .map(|x| format!("{x:.places$}"))
        .unwrap_or_default()
}

fn age_text(v: &Value, fmt: &str) -> String {
    v.as_f64()
        .map(|x| format_age(Some(x), fmt))
        .unwrap_or_default()
}

/// An HPD region in the reader's convention, the per-range breakdown where
/// there is one (`_hpd_text`).
fn hpd_text(ranges: &Value, lo: &Value, hi: &Value, fmt: &str) -> String {
    let r = ranges_of(ranges);
    if !r.is_empty() {
        return date_format::format_hpd_ranges(&r, fmt);
    }
    match (lo.as_f64(), hi.as_f64()) {
        (Some(lo), Some(hi)) => format_range(Some(hi), Some(lo), fmt),
        _ => String::new(),
    }
}

/// "389 ± 21", or blank for a row with no measurement (`_measurement`).
fn measurement(r: &Value) -> String {
    if r["age_bp"].is_null() {
        return String::new();
    }
    let age = number(&r["age_bp"], 0);
    if r["error_1sigma"].is_null() {
        age
    } else {
        format!("{age} ± {}", number(&r["error_1sigma"], 0))
    }
}

fn years_text(ranges: &Value) -> String {
    let mut r = ranges_of(ranges);
    r.sort_by(|a, b| a.min.total_cmp(&b.min));
    r.iter()
        .map(|r| {
            format!(
                "{:.0}-{:.0} yr ({:.1}%)",
                r.min, r.max, r.probability_percent
            )
        })
        .collect::<Vec<_>>()
        .join("; ")
}

fn thousands(n: i64) -> String {
    let digits = n.abs().to_string();
    let mut out = String::new();
    for (i, c) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(c);
    }
    if n < 0 { format!("-{out}") } else { out }
}

fn text(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// `statistics.format_probability`.
fn probability(p: Option<f64>) -> String {
    match p {
        None => String::new(),
        Some(p) if p < 0.0005 => if p == 0.0 { "0" } else { "<0.001" }.to_owned(),
        Some(p) if p > 0.9995 => if p == 1.0 { "1" } else { ">0.999" }.to_owned(),
        Some(p) => format!("{p:.3}"),
    }
}

// --- tables --------------------------------------------------------------

fn scaled(weights: &[f64]) -> Vec<f64> {
    let total: f64 = weights.iter().sum();
    let total = if total == 0.0 { 1.0 } else { total };
    weights.iter().map(|w| CONTENT_WIDTH * w / total).collect()
}

fn table_style(pad: f64) -> TableStyle {
    TableStyle {
        pad_left: pad,
        pad_right: pad,
        pad_top: 3.0,
        pad_bottom: 3.0,
        header_fill: hex("#f2efec"),
        header_rule: ink(),
        grid: hex("#ded7d2"),
    }
}

/// A plain table: a header and rows of text, sharing a look
/// (`_intervals_table` and its kin).
fn simple_table(header: &[&str], weights: &[f64], rows: Vec<Vec<String>>, s: &Styles) -> Block {
    let widths = scaled(weights);
    let cell = |t: &str, st: Style, w: f64| Para::new(t, st, w - 12.0);
    Block::Table(Table {
        header: header
            .iter()
            .zip(&widths)
            .map(|(h, w)| cell(h, s.th, *w))
            .collect(),
        rows: rows
            .iter()
            .map(|r| {
                (
                    r.iter()
                        .zip(&widths)
                        .map(|(t, w)| cell(t, s.td, *w))
                        .collect(),
                    RowLook::default(),
                )
            })
            .collect(),
        widths,
        style: table_style(6.0),
    })
}

// --- narrative -----------------------------------------------------------

/// Each curve the run calibrated against, named and cited
/// (`_curve_sentences`).
fn curve_sentences(state: &State, ids: &[String]) -> Vec<String> {
    ids.iter()
        .map(|id| match crate::routes::curve_routes::spec(state, id) {
            Ok(Ok(spec)) => {
                let reference = spec["reference"]
                    .as_str()
                    .filter(|r| !r.is_empty())
                    .map(|r| format!(" {r}"))
                    .unwrap_or_default();
                let doi = spec["doi"]
                    .as_str()
                    .filter(|d| !d.is_empty())
                    .map(|d| format!(" doi:{d}"))
                    .unwrap_or_default();
                format!(
                    "<b>{}</b> ({}).{reference}{doi}",
                    text(&spec["name"]),
                    text(&spec["id"])
                )
            }
            _ => format!("<b>{id}</b> (no longer available in this installation)."),
        })
        .collect()
}

/// How the posteriors were computed (`_solver_sentence`).
pub fn solver_sentence(solver: &Value) -> Option<String> {
    if !solver.is_object() {
        return None;
    }
    let method = solver["method"].as_str().unwrap_or_default();
    let named = match method {
        "exact" => "exact integration".to_owned(),
        "gibbs" => "OxCal's MH/Gibbs sampler".to_owned(),
        "wiggle_match" => "the closed-form wiggle match".to_owned(),
        "" => "unknown method".to_owned(),
        other => other.to_owned(),
    };
    let mut s = format!("Solved by {named}.");
    if solver["match_oxcal"] == true {
        s.push_str(" Run to match OxCal 4.4.");
    }
    if let Some(reason) = solver["reason"].as_str().filter(|r| !r.is_empty())
        && method == "gibbs"
    {
        s.push_str(&format!(
            " The model could not be integrated exactly: {reason}"
        ));
        if !s.ends_with('.') {
            s.push('.');
        }
    }
    if solver["converged"] == false {
        s.push_str(" The sampler did not meet its convergence criterion.");
    }
    let quoted: Vec<String> = [
        ("A<sub>model</sub>", &solver["model_agreement"]),
        ("A<sub>overall</sub>", &solver["overall_agreement"]),
    ]
    .iter()
    .filter_map(|(label, v)| v.as_f64().map(|v| format!("{label} {v:.0}%")))
    .collect();
    if !quoted.is_empty() {
        s.push_str(&format!(
            " Agreement over the whole model: {}.",
            quoted.join(", ")
        ));
    }
    if method == "wiggle_match"
        && let Some(combination) = solver["combination_agreement"].as_f64()
    {
        let against = solver["combination_threshold"]
            .as_f64()
            .map(|t| format!(" (pass mark A<sub>n</sub> {t:.0}%)"))
            .unwrap_or_default();
        s.push_str(&format!(
            " The series spans {} {} and its dates agree with their own calibrations at a combination index of {combination:.0}%{against}.",
            radical_core::pyfmt::g(solver["series_span"].as_f64().unwrap_or(0.0), 6),
            solver["gap_units"].as_str().filter(|u| !u.is_empty()).unwrap_or("rings"),
        ));
    }
    for d in solver["deposition"].as_array().into_iter().flatten() {
        let Some(k) = d["k_mean"].as_f64() else {
            continue;
        };
        s.push_str(&format!(
            " {} was modelled at a rate of about {} events per {}{}.",
            d["name"]
                .as_str()
                .filter(|n| !n.is_empty())
                .unwrap_or("The deposition sequence"),
            radical_core::pyfmt::g(k, 2),
            d["depth_units"]
                .as_str()
                .filter(|u| !u.is_empty())
                .unwrap_or("cm"),
            if d["variable"] == true {
                ", inferred from the dates"
            } else {
                ", as set"
            },
        ));
        if d["pinned"] == true {
            s.push_str(" That rate ran up against the end of the range it was allowed, so the prior is shaping the answer as much as the dates are.");
        }
    }
    Some(s)
}

// --- rows ----------------------------------------------------------------

/// One row to report: a structure row, numbered, with its result.
struct Row {
    kind: String,
    depth: usize,
    order: usize,
    name: String,
    path: String,
    element_id: Option<String>,
    sample_id: Option<String>,
    tpq: Option<f64>,
    taq: Option<f64>,
    result: Option<Value>,
}

/// A Boundary's posterior shaped like a result row (`_boundary_result`).
fn boundary_result(p: &Value) -> Value {
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
        "probability_distribution": p["probability_distribution"],
        "agreement_index": null,
        "outlier_probability": null,
    })
}

/// The rows in reading order, or the flat list of dates when there is no
/// stored structure (`_ordered_rows`).
fn ordered(structure: &[StructureRow], rows: &[Value]) -> Vec<Row> {
    if !structure.is_empty() {
        return structure
            .iter()
            .enumerate()
            .map(|(i, r)| Row {
                kind: r.kind.clone(),
                depth: r.depth,
                order: i + 1,
                name: r.name.clone(),
                path: r.path.clone(),
                element_id: r.element_id.clone(),
                sample_id: r.sample_id.clone(),
                tpq: r.tpq_age_bp,
                taq: r.taq_age_bp,
                result: if r.kind == "date" {
                    r.result.clone()
                } else {
                    r.posterior.as_ref().map(boundary_result)
                },
            })
            .collect();
    }
    rows.iter()
        .enumerate()
        .map(|(i, r)| Row {
            kind: "date".to_owned(),
            depth: 0,
            order: i + 1,
            name: {
                let n = text(&r["sample_name"]);
                if n.is_empty() {
                    text(&r["sample_id"])
                } else {
                    n
                }
            },
            path: String::new(),
            element_id: None,
            sample_id: r["sample_id"].as_str().map(str::to_owned),
            tpq: None,
            taq: None,
            result: Some(r.clone()),
        })
        .collect()
}

/// The strings a row prints, by column key (`_display_row`).
fn display(row: &Row, fmt: &str) -> Map<String, Value> {
    let empty = Value::Null;
    let r = row.result.as_ref().unwrap_or(&empty);
    let position = match (r["latitude"].as_f64(), r["longitude"].as_f64()) {
        (Some(lat), Some(lon)) => format!(
            "{}, {}",
            radical_core::pyfmt::repr(lat),
            radical_core::pyfmt::repr(lon)
        ),
        _ => String::new(),
    };
    let mut out = Map::new();
    let mut put = |k: &str, v: String| {
        out.insert(k.to_owned(), json!(v));
    };
    put("order", row.order.to_string());
    put("name", row.name.clone());
    put("age", measurement(r));
    put("curve", text(&r["curve_id"]));
    put(
        "unmodelled",
        hpd_text(&r["unmodelled_hpd_95_ranges"], &empty, &empty, fmt),
    );
    put("mean", age_text(&r["posterior_mean"], fmt));
    put("median", age_text(&r["posterior_median"], fmt));
    put(
        "hpd68",
        hpd_text(&r["hpd_68_ranges"], &r["hpd_68_min"], &r["hpd_68_max"], fmt),
    );
    put(
        "hpd95",
        hpd_text(&r["hpd_95_ranges"], &r["hpd_95_min"], &r["hpd_95_max"], fmt),
    );
    put("agreement", number(&r["agreement_index"], 1));
    put(
        "outlier",
        r["outlier_probability"]
            .as_f64()
            .map(|o| format!("{:.1}", 100.0 * o))
            .unwrap_or_default(),
    );
    put(
        "tpq",
        row.tpq
            .map(|t| format_age(Some(t), fmt))
            .unwrap_or_default(),
    );
    put(
        "taq",
        row.taq
            .map(|t| format_age(Some(t), fmt))
            .unwrap_or_default(),
    );
    put("position", position);
    out
}

fn has_any(rows: &[&Value], key: &str) -> bool {
    rows.iter().any(|r| !r[key].is_null())
}

/// The columns this run has something to say in (`_columns`).
pub fn columns(
    results: &[&Value],
    is_bayesian: bool,
    has_limiters: bool,
) -> Vec<(String, String, f64)> {
    let mut c: Vec<(&str, &str, f64)> = vec![
        ("order", "#", 0.5),
        ("name", "Structure / Sample", 3.2),
        ("age", "Radiocarbon Age", 1.4),
        ("curve", "Curve", 1.1),
    ];
    if is_bayesian && has_any(results, "unmodelled_hpd_95_ranges") {
        c.push(("unmodelled", "Unmodelled 95.4% HPD", 2.6));
    }
    if has_any(results, "posterior_mean") {
        c.push(("mean", "Mean", 1.2));
    }
    if has_any(results, "posterior_median") {
        c.push(("median", "Median", 1.2));
    }
    c.push(("hpd68", "68.3% HPD", 2.6));
    c.push(("hpd95", "95.4% HPD", 2.6));
    if has_any(results, "latitude") {
        c.push(("position", "Position", 1.8));
    }
    if has_any(results, "agreement_index") {
        c.push(("agreement", "Agree %", 1.2));
    }
    if has_any(results, "outlier_probability") {
        c.push(("outlier", "Outlier %", 1.2));
    }
    if has_limiters {
        c.push(("tpq", "TPQ", 1.2));
        c.push(("taq", "TAQ", 1.2));
    }
    c.into_iter()
        .map(|(k, l, w)| (k.to_owned(), l.to_owned(), w))
        .collect()
}

/// The results table: one row per modelled entity in reading order,
/// structure indented by depth and set in bold, Boundaries in coral.
fn results_table(
    rows: &[Row],
    shown: &[Map<String, Value>],
    cols: &[(String, String, f64)],
    s: &Styles,
) -> Block {
    let widths = scaled(&cols.iter().map(|c| c.2).collect::<Vec<_>>());
    let pad = 4.0;
    let header = cols
        .iter()
        .zip(&widths)
        .map(|(c, w)| Para::new(&c.1, s.th, w - 2.0 * pad))
        .collect();
    let body = rows
        .iter()
        .zip(shown)
        .map(|(row, cells)| {
            let paras = cols
                .iter()
                .zip(&widths)
                .map(|((key, _, _), w)| {
                    let value = text(cells.get(key).unwrap_or(&Value::Null));
                    if key == "name" {
                        let st = Style {
                            indent: 6.0 * row.depth as f64,
                            bold: row.kind != "date",
                            ..s.td
                        };
                        Para::new(&value, st, w - 2.0 * pad)
                    } else {
                        Para::new(&value, s.td, w - 2.0 * pad)
                    }
                })
                .collect();
            let look = match row.kind.as_str() {
                "boundary" => RowLook {
                    fill: None,
                    text: Some(hex(plot::BOUNDARY_INK)),
                },
                "date" => RowLook::default(),
                _ => RowLook {
                    fill: Some(hex("#f7f5f3")),
                    text: None,
                },
            };
            (paras, look)
        })
        .collect();
    Block::Table(Table {
        widths,
        header,
        rows: body,
        style: table_style(pad),
    })
}

/// What a date calibrates to alone, for the shadow behind its modelled
/// posterior (`_prior_distribution`).
fn prior(state: &State, s: Option<&RadiocarbonSample>) -> Vec<[f64; 2]> {
    let Some(s) = s else { return Vec::new() };
    let Ok(curve) = state
        .curves
        .curve(s.curve_id.as_deref().unwrap_or_default())
    else {
        return Vec::new();
    };
    match curve.calibrate(
        s.age_bp,
        s.error_1sigma,
        s.delta_r,
        s.delta_r_error,
        Mode::Standard,
    ) {
        Ok(probs) => curve.display_distribution(curve.cal_ages(), &probs, 200, 1e-4),
        Err(_) => Vec::new(),
    }
}

fn series(v: &Value) -> Vec<[f64; 2]> {
    v.as_array()
        .into_iter()
        .flatten()
        .filter_map(|p| Some([p[0].as_f64()?, p[1].as_f64()?]))
        .collect()
}

/// HPD bars: each range of a level, or its envelope for an older row.
fn spans(ranges: &Value, lo: &Value, hi: &Value) -> Vec<(f64, f64)> {
    let r = ranges_of(ranges);
    if !r.is_empty() {
        return r.iter().map(|r| (r.min, r.max)).collect();
    }
    match (lo.as_f64(), hi.as_f64()) {
        (Some(a), Some(b)) => vec![(a, b)],
        _ => Vec::new(),
    }
}

/// Everything the report is made from.
pub(crate) struct Inputs<'a> {
    pub analysis: &'a Analysis,
    pub structure: &'a [StructureRow],
    pub rows: &'a [Value],
    pub samples: &'a [RadiocarbonSample],
    pub distributions: &'a [(String, Value)],
    /// (name, start, end) for each context with a modelled span.
    pub context_spans: &'a [(String, Value, Value)],
    pub same_event: &'a Value,
    pub p_before: Option<&'a Value>,
    pub project_name: Option<String>,
    pub date_format: Option<&'a str>,
    pub coordinate_precision: &'a str,
}

/// The analysis as a PDF (`render_report`).
pub(crate) fn render(state: &State, i: &Inputs) -> Vec<u8> {
    let fmt = date_format::normalise(i.date_format);
    let s = styles();
    let a = i.analysis;
    let is_bayesian = a.analysis_type == "bayesian";
    let status = a.status.clone().unwrap_or_else(|| "None".to_owned());
    let mut story: Vec<Block> = vec![para(&a.name, s.title)];
    let created = a.created_at.map_or_else(
        || "unknown date".to_owned(),
        |t| t.isoformat()[..10].to_owned(),
    );
    let mut descriptors = vec![
        match a.analysis_type.as_str() {
            "bayesian" => "Bayesian chronological model".to_owned(),
            "group_calibration" => "Group calibration".to_owned(),
            "calibration" => "Calibration".to_owned(),
            other => other.to_owned(),
        },
        format!("status: {status}"),
        format!("created {created}"),
    ];
    if let Some(p) = i.project_name.as_ref().filter(|p| !p.is_empty()) {
        descriptors.insert(1, format!("project: {p}"));
    }
    story.push(para(&descriptors.join(" · "), s.subtitle));

    let rows = ordered(i.structure, i.rows);
    let footer = format!("RadiCal — {} ({})", a.name, a.id);
    let info = pdf::Info {
        title: format!("RadiCal report: {}", a.name),
        author: "RadiCal".to_owned(),
        subject: format!("Radiocarbon analysis results ({})", a.analysis_type),
    };
    if rows.is_empty() {
        story.push(para(
            &format!(
                "This analysis has no results to report (status: {status}). Run it, then export the report again."
            ),
            s.body,
        ));
        return pdf::write(&pdf::layout::flow(&story, &footer), &info);
    }

    let results: Vec<&Value> = rows.iter().filter_map(|r| r.result.as_ref()).collect();
    let mut shown: Vec<Map<String, Value>> = rows.iter().map(|r| display(r, fmt)).collect();
    if let Some(p) = i.p_before {
        for (row, cells) in rows.iter().zip(&mut shown) {
            let value = if row.kind == "date" {
                row.sample_id
                    .as_ref()
                    .and_then(|id| p["samples"][id].as_f64())
            } else {
                row.element_id
                    .as_ref()
                    .and_then(|id| p["elements"][id].as_f64())
            };
            cells.insert("p_before".to_owned(), json!(probability(value)));
        }
    }

    // The method note.
    story.push(para("Method", s.heading));
    let mut curve_ids: Vec<String> = Vec::new();
    for r in i.rows {
        if let Some(c) = r["curve_id"].as_str().filter(|c| !c.is_empty())
            && !curve_ids.iter().any(|x| x == c)
        {
            curve_ids.push(c.to_owned());
        }
    }
    for sentence in curve_sentences(state, &curve_ids) {
        story.push(para(&format!("Calibrated against {sentence}"), s.body));
    }
    if let Some(sentence) = solver_sentence(a.solver.as_ref().unwrap_or(&Value::Null)) {
        story.push(para(&sentence, s.body));
    }
    let era = era_label(fmt).unwrap_or("cal BP");
    story.push(para(
        &format!(
            "Radiocarbon ages are conventional ages BP with their 1-sigma measurement error. Calendar ages are reported in {era}. Highest Posterior Density (HPD) regions are given at 68.3% and 95.4% -- the coverages of one and two standard deviations; where a region falls in more than one part, each part is listed with its own share of the probability rather than being collapsed into a single span."
        ),
        s.note,
    ));
    if has_any(&results, "latitude") {
        let rounded = i.coordinate_precision != "full";
        story.push(para(
            &format!(
                "Positions are WGS84 decimal degrees. {}",
                if rounded {
                    format!(
                        "They have been rounded to the nearest {} degree for this report, at the project's setting, and are not the recorded site locations.",
                        i.coordinate_precision
                    )
                } else {
                    "They are given at the precision they were recorded.".to_owned()
                }
            ),
            s.note,
        ));
    }

    // The results table.
    story.push(para("Results", s.heading));
    let has_limiters = rows.iter().any(|r| r.tpq.is_some() || r.taq.is_some());
    let mut cols = columns(&results, is_bayesian, has_limiters);
    if let Some(p) = i.p_before {
        let threshold = format_age(p["cal_bp"].as_f64(), fmt);
        cols.push(("p_before".to_owned(), format!("P before {threshold}"), 1.3));
    }
    story.push(results_table(&rows, &shown, &cols, &s));

    // Context spans, where no Boundary already reports them.
    let has_boundaries = rows.iter().any(|r| r.kind == "boundary");
    if !i.context_spans.is_empty() && !has_boundaries {
        story.push(para("Context Spans", s.heading));
        story.push(para(
            "Modelled start and end of each context (OxCal-style Boundary parameters). These may extend beyond the oldest and youngest dated sample, since the dated samples are treated as a partial sample of the context's true duration.",
            s.note,
        ));
        let span = |p: &Value| hpd_text(&p["hpd_95_ranges"], &p["hpd_95"][0], &p["hpd_95"][1], fmt);
        let body = i
            .context_spans
            .iter()
            .map(|(name, start, end)| {
                vec![
                    name.clone(),
                    span(start),
                    span(end),
                    age_text(&start["posterior_mean"], fmt),
                    age_text(&end["posterior_mean"], fmt),
                ]
            })
            .collect();
        story.push(simple_table(
            &[
                "Context",
                "Modelled Start (95.4% HPD)",
                "Modelled End (95.4% HPD)",
                "Start Mean",
                "End Mean",
            ],
            &[2.2, 3.4, 3.4, 1.6, 1.6],
            body,
            &s,
        ));
    }

    // Durations and differences, when the run was asked for them.
    let intervals = a.intervals.clone().unwrap_or(Value::Null);
    let durations = intervals["durations"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let differences = intervals["differences"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    if !durations.is_empty() || !differences.is_empty() {
        story.push(para("Durations and Differences", s.heading));
        story.push(para(
            &format!(
                "Lengths of time, in years, rather than calendar dates -- so these are not converted to the reporting era. A phase's duration is the interval its Boundaries enclose and is longer than the span of its dated events, since those events are a sample of the phase rather than the whole of it. Estimated from {} joint draws, so unlike every other figure in this report they carry a Monte Carlo error.",
                thousands(intervals["draws"].as_i64().unwrap_or(0))
            ),
            s.note,
        ));
        if !durations.is_empty() {
            let body = durations
                .iter()
                .map(|d| {
                    let y = &d["years"];
                    vec![
                        match d["kind"].as_str() {
                            Some("sequence_duration") => "Sequence".to_owned(),
                            Some("phase_duration") => "Phase".to_owned(),
                            Some("hiatus_duration") => "Hiatus".to_owned(),
                            Some("sample_duration") => "Dated events".to_owned(),
                            _ => text(&d["kind"]),
                        },
                        text(&d["name"]),
                        number(&y["mean"], 0),
                        years_text(&y["hpd_68_ranges"]),
                        years_text(&y["hpd_95_ranges"]),
                    ]
                })
                .collect();
            story.push(simple_table(
                &["What", "Name", "Mean (yr)", "68.3% (yr)", "95.4% (yr)"],
                &[1.4, 2.4, 1.2, 2.6, 2.6],
                body,
                &s,
            ));
        }
        if let Some(activity) = intervals.get("phase_activity").filter(|v| v.is_object()) {
            let strips: Vec<Strip> = activity["rows"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|r| {
                    let name = text(&r["name"]).trim().to_owned();
                    let dates = r["dates"].as_i64().unwrap_or(0);
                    Strip {
                        label: if dates == 0 {
                            name
                        } else if dates == 1 {
                            format!("{name} (1 date)")
                        } else {
                            format!("{name} ({dates} dates)")
                        },
                        relative: r["relative"] == true,
                        values: r["values"]
                            .as_array()
                            .into_iter()
                            .flatten()
                            .filter_map(Value::as_f64)
                            .collect(),
                    }
                })
                .collect();
            let n = strips.len();
            if let Some(draw) = plot::phase_activity(
                strips,
                activity["start"].as_f64().unwrap_or(0.0),
                activity["step"].as_f64().unwrap_or(1.0),
                fmt,
            ) {
                story.push(Block::Keep(vec![
                    para("Phases through time", s.caption),
                    para(
                        "Shading is the probability each phase was in progress in each year. A phase represented by a single date shows that date's own probability, relative to its peak.",
                        s.note,
                    ),
                    Block::Figure {
                        height: CONTENT_WIDTH * (0.07 + ACTIVITY_ROW * n as f64),
                        draw,
                    },
                ]));
            }
        }
        if !differences.is_empty() {
            let shared = differences.iter().any(|d| d["shared_boundary"] == true);
            let shared_text = radical_core::intervals::SHARED_BOUNDARY_TEXT;
            story.push(para(
                &format!(
                    "Differences are positive for a gap and negative for an overlap. \"Overlap\" is the probability that the two cannot be separated in time -- for two phases, that their modelled intervals intersect; for two dates, that the model cannot say which came first -- graded at the same 68.3% and 95.4% levels the HPD regions use. Phases are compared only where the model leaves their order open, as in parallel branches.{}",
                    if shared {
                        format!(" Two phases either side of one shared Boundary read \"{shared_text}\" instead: one phase's end is the next one's start, so the model gives them no gap and no overlap to measure.")
                    } else {
                        String::new()
                    }
                ),
                s.note,
            ));
            let body = differences
                .iter()
                .map(|d| {
                    if d["shared_boundary"] == true {
                        return vec![
                            text(&d["from_name"]),
                            text(&d["to_name"]),
                            String::new(),
                            shared_text.to_owned(),
                            String::new(),
                        ];
                    }
                    let mut word = text(&d["severity"]);
                    if let Some(o) = d["overlap"].as_f64() {
                        word = format!("{word} ({:.0}%)", o * 100.0);
                    }
                    vec![
                        text(&d["from_name"]),
                        text(&d["to_name"]),
                        number(&d["years"]["mean"], 0),
                        years_text(&d["years"]["hpd_95_ranges"]),
                        word,
                    ]
                })
                .collect();
            story.push(simple_table(
                &["", "Compared with", "Mean (yr)", "95.4% (yr)", "Overlap"],
                &[2.0, 2.0, 1.1, 2.4, 1.5],
                body,
                &s,
            ));
        }
        if let Some(note) = intervals["note"].as_str().filter(|n| !n.is_empty()) {
            story.push(para(note, s.note));
        }
    }

    // Age against depth.
    for record in intervals["age_depth"].as_array().into_iter().flatten() {
        story.push(para(
            &format!("Age and Depth: {}", text(&record["name"])),
            s.heading,
        ));
        story.push(para(
            "Age against depth, and how fast the deposit accumulated between one level and the next. The bands are quantiles rather than HPD regions, so they nest; the rates are read off the same joint draws as the ages above them, since a rate is a function of two ages at once and pairing their separate posteriors would give an answer wrong in shape.",
            s.note,
        ));
        let units = record["depth_units"]
            .as_str()
            .filter(|u| !u.is_empty())
            .unwrap_or("cm");
        let band = |b: &Value| {
            let v: Vec<f64> = b
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_f64)
                .collect();
            if v.len() < 2 {
                return String::new();
            }
            let hi = v.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            let lo = v.iter().copied().fold(f64::INFINITY, f64::min);
            format_range(Some(hi), Some(lo), fmt)
        };
        let levels = record["levels"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|l| {
                vec![
                    format!(
                        "{}{}",
                        text(&l["name"]),
                        if l["dated"] == true { "" } else { " (undated)" }
                    ),
                    number(&l["depth"], 1),
                    age_text(&l["median"], fmt),
                    band(&l["band_68"]),
                    band(&l["band_95"]),
                ]
            })
            .collect();
        let depth_label = format!("Depth ({units})");
        story.push(simple_table(
            &["Level", &depth_label, "Median", "68.3%", "95.4%"],
            &[2.6, 1.2, 1.8, 2.2, 2.2],
            levels,
            &s,
        ));
        if let Some(steps) = record["accumulation"].as_array().filter(|a| !a.is_empty()) {
            let body = steps
                .iter()
                .map(|st| {
                    let b: Vec<f64> = st["rate_band_68"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(Value::as_f64)
                        .collect();
                    vec![
                        format!("{} to {}", text(&st["from_name"]), text(&st["to_name"])),
                        number(&st["depth_gap"], 1),
                        number(&st["years"]["median"], 0),
                        number(&st["rate_median"], 1),
                        if b.is_empty() {
                            String::new()
                        } else {
                            format!(
                                "{:.1}-{:.1}",
                                b.iter().copied().fold(f64::INFINITY, f64::min),
                                b.iter().copied().fold(f64::NEG_INFINITY, f64::max)
                            )
                        },
                    ]
                })
                .collect();
            let rate = format!("Rate (yr/{units})");
            story.push(simple_table(
                &["Between", units, "Years", &rate, "68.3% rate"],
                &[3.2, 1.0, 1.6, 1.6, 1.8],
                body,
                &s,
            ));
        }
    }

    // The kernel density estimates.
    let summaries = a.summaries.clone().unwrap_or(Value::Null);
    let mut kde_blocks: Vec<Block> = Vec::new();
    for k in summaries["kde"].as_array().into_iter().flatten() {
        let points: Vec<[f64; 3]> = k["distribution"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|p| Some([p[0].as_f64()?, p[1].as_f64()?, p[2].as_f64()?]))
            .collect();
        let Some(draw) = plot::kde(&points, fmt) else {
            continue;
        };
        let w = &k["bandwidth"];
        let mut block = Vec::new();
        if let Some(name) = k["name"].as_str().filter(|n| !n.is_empty()) {
            block.push(para(&format!("Phase: {name}"), s.caption));
        }
        block.push(para(
            &format!(
                "{} dates, {} draws. Kernel width {} years (68.3%: {}–{}){}",
                text(&k["dates"]),
                thousands(k["draws"].as_i64().unwrap_or(0)),
                number(&w["median"], 0),
                number(&w["range_68"][0], 0),
                number(&w["range_68"][1], 0),
                if k["reflected"] == true {
                    ", reflected at the phase's Boundaries."
                } else {
                    "."
                }
            ),
            s.note,
        ));
        block.push(Block::Figure {
            height: CONTENT_WIDTH * FIGURE_ASPECT,
            draw,
        });
        kde_blocks.push(Block::Keep(block));
    }
    if !kde_blocks.is_empty() {
        story.push(para("Kernel Density Estimates", s.heading));
        story.push(para(
            "How the dates are spread through time, as OxCal's KDE_Plot (Bronk Ramsey 2017): the mean of many kernel density estimates, each of one joint draw of the dates, with their ±1σ as a band.",
            s.note,
        ));
        story.extend(kde_blocks);
    }

    // Whether a group calibration's dates could be one event.
    let tests = i.same_event.as_array().cloned().unwrap_or_default();
    if !tests.is_empty() {
        story.push(para("Same-Event Test", s.heading));
        story.push(para(
            "Ward and Wilson's (1978) test of whether a set of radiocarbon ages could date one event, as OxCal's R_Combine: T against the chi-squared critical value at 5%. The pooled age is the error-weighted mean, calibrated only where the dates pass.",
            s.note,
        ));
        let body = tests
            .iter()
            .map(|t| {
                let tested = !t["t"].is_null();
                let pooled = t["pooled_hpd_ranges"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .find(|e| {
                        e["confidence"]
                            .as_f64()
                            .is_some_and(|c| (c - SIGMA_2).abs() < 1e-9)
                    })
                    .map_or(Value::Null, |e| e["ranges"].clone());
                let verdict = if !t["refused"].is_null() || t["consistent"].is_null() {
                    text(&t["refused"])
                } else if t["consistent"] == true {
                    "Consistent with one event".to_owned()
                } else {
                    "More scatter than one event allows".to_owned()
                };
                vec![
                    text(&t["n"]),
                    if tested {
                        format!(
                            "{} ± {}",
                            number(&t["mean_bp"], 0),
                            number(&t["error_bp"], 0)
                        )
                    } else {
                        String::new()
                    },
                    if tested {
                        format!(
                            "{} ({}, df {})",
                            number(&t["t"], 1),
                            number(&t["critical"], 2),
                            text(&t["df"])
                        )
                    } else {
                        String::new()
                    },
                    verdict,
                    if pooled.is_null() {
                        String::new()
                    } else {
                        hpd_text(&pooled, &Value::Null, &Value::Null, fmt)
                    },
                ]
            })
            .collect();
        story.push(simple_table(
            &[
                "Dates",
                "Pooled age BP",
                "T (5% critical)",
                "Result",
                "Pooled 95.4%",
            ],
            &[0.8, 1.4, 1.8, 2.6, 3.0],
            body,
            &s,
        ));
    }

    // One figure per modelled parameter.
    let mut figures: Vec<Block> = Vec::new();
    for row in &rows {
        let Some(r) = &row.result else { continue };
        let is_boundary = row.kind == "boundary";
        let sample = row
            .sample_id
            .as_ref()
            .and_then(|id| i.samples.iter().find(|s| &s.id == id));
        let distribution = if is_boundary {
            series(&r["probability_distribution"])
        } else {
            row.sample_id
                .as_ref()
                .and_then(|id| i.distributions.iter().find(|(s, _)| s == id))
                .map(|(_, d)| series(d))
                .unwrap_or_default()
        };
        let shadow = if is_bayesian && !is_boundary {
            prior(state, sample)
        } else {
            Vec::new()
        };
        let Some(draw) = plot::posterior(
            &distribution,
            &spans(&r["hpd_68_ranges"], &r["hpd_68_min"], &r["hpd_68_max"]),
            &spans(&r["hpd_95_ranges"], &r["hpd_95_min"], &r["hpd_95_max"]),
            &shadow,
            is_boundary,
            fmt,
        ) else {
            continue;
        };
        let mut block = vec![para(&row.name, s.caption)];
        if !row.path.is_empty() {
            block.push(para(&row.path, s.note));
        }
        block.push(Block::Figure {
            height: CONTENT_WIDTH * FIGURE_ASPECT,
            draw,
        });
        figures.push(Block::Keep(block));
    }
    if !figures.is_empty() {
        story.push(Block::PageBreak);
        story.push(para("Posterior Distributions", s.heading));
        story.extend(figures);
    }
    pdf::write(&pdf::layout::flow(&story, &footer), &info)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn keys(rows: &[Value], is_bayesian: bool, has_limiters: bool) -> Vec<String> {
        let refs: Vec<&Value> = rows.iter().collect();
        columns(&refs, is_bayesian, has_limiters)
            .into_iter()
            .map(|c| c.0)
            .collect()
    }

    #[test]
    fn a_column_appears_only_when_something_fills_it() {
        let k = keys(
            &[json!({"posterior_mean": 3100.0, "agreement_index": null})],
            false,
            false,
        );
        assert!(k.contains(&"mean".to_owned()));
        assert!(!k.contains(&"agreement".to_owned()));
        assert!(!k.contains(&"tpq".to_owned()));
    }

    #[test]
    fn limiters_appear_when_the_model_used_them() {
        assert!(keys(&[], false, true).ends_with(&["tpq".to_owned(), "taq".to_owned()]));
    }

    #[test]
    fn the_hpd_columns_are_never_dropped() {
        let k = keys(&[], true, false);
        assert!(k.contains(&"hpd68".to_owned()) && k.contains(&"hpd95".to_owned()));
    }

    #[test]
    fn hpd_text_prefers_the_breakdown_then_the_envelope_then_nothing() {
        let ranges = json!([
            {"min": 3103.0, "max": 3253.0, "probability_percent": 77.6},
            {"min": 3074.0, "max": 3098.0, "probability_percent": 8.0},
        ]);
        let t = hpd_text(&ranges, &Value::Null, &Value::Null, "calBP");
        assert!(
            t.contains("77.6%") && t.contains("8.0%") && t.contains(';'),
            "{t}"
        );
        let t = hpd_text(&Value::Null, &json!(3081.0), &json!(3234.0), "calBP");
        assert!(t.contains("3234") && t.contains("3081"), "{t}");
        assert_eq!(
            hpd_text(&Value::Null, &Value::Null, &Value::Null, "calBP"),
            ""
        );
    }

    #[test]
    fn a_wiggle_match_quotes_its_combination_against_the_pass_mark() {
        let solver = json!({"method": "wiggle_match", "series_span": 250.0,
                            "gap_units": "rings", "combination_agreement": 104.2});
        let mut with = solver.clone();
        with["combination_threshold"] = json!(25.0);
        let s = solver_sentence(&with).unwrap();
        assert!(s.contains("combination index of 104%"), "{s}");
        assert!(s.contains("A<sub>n</sub> 25%"), "{s}");
        let s = solver_sentence(&solver).unwrap();
        assert!(s.contains("combination index of 104%."), "{s}");
        assert!(!s.contains("pass mark"), "{s}");
    }

    #[test]
    fn says_when_the_run_matched_oxcal() {
        let s = solver_sentence(&json!({"method": "gibbs", "match_oxcal": true})).unwrap();
        assert!(s.contains("Run to match OxCal 4.4."));
        let s = solver_sentence(&json!({"method": "gibbs"})).unwrap();
        assert!(!s.contains("OxCal 4.4"));
    }

    #[test]
    fn probabilities_are_quoted_as_the_csv_quotes_them() {
        assert_eq!(probability(None), "");
        assert_eq!(probability(Some(0.0)), "0");
        assert_eq!(probability(Some(0.0002)), "<0.001");
        assert_eq!(probability(Some(1.0)), "1");
        assert_eq!(probability(Some(0.9999)), ">0.999");
        assert_eq!(probability(Some(0.25)), "0.250");
        assert_eq!(thousands(12000), "12,000");
        assert_eq!(thousands(999), "999");
    }
}
