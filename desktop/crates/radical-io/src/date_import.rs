//! Reading dates in from a file: the CSV path, and the front door both file
//! formats come through. Port of `services/date_import.py`.
//!
//! Nothing here stores anything. An import PARSES and hands back what it
//! read; storing goes through the same path hand-entered dates do. Every
//! parsed date is validated as a typed one is ([`crate::schema`]), and
//! reported in the same words.
//!
//! An import is ALL-OR-NOTHING. One bad row fails the file, listing every
//! problem found rather than the first: importing the good rows and skipping
//! the rest is how a date goes missing without anyone noticing.

use std::collections::BTreeMap;
use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Map, Value, json};

use crate::ids::Ids;
use crate::library::Library;
use crate::pycsv::{self, CsvError};
use crate::python;
use crate::schema::{self, Values};

/// A file bigger than this is not a date list.
pub const MAX_CONTENT_BYTES: usize = 4 * 1024 * 1024;
/// The bulk endpoint's own ceiling, so a file that parses here cannot be
/// rejected where it is going.
pub const MAX_DATES: usize = 500;

/// Which fields a date's JSON carries: the CSV reader fills every column it
/// knows, the OxCal reader only those a CQL2 date can set.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum Source {
    #[default]
    Csv,
    Oxcal,
}

/// One imported date, with the fields the API's measurement dicts carry.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Date {
    pub source: Source,
    pub id: String,
    pub sample_name: String,
    pub age_bp: f64,
    pub error_1sigma: f64,
    pub material_type: String,
    pub curve_id: String,
    pub sample_type: Option<String>,
    pub delta_r: Option<f64>,
    pub delta_r_error: Option<f64>,
    pub latitude: Option<f64>,
    pub longitude: Option<f64>,
    pub tpq_age_bp: Option<f64>,
    pub taq_age_bp: Option<f64>,
    pub depth_cm: Option<f64>,
    pub ring_offset: Option<f64>,
    pub ring_offset_error: Option<f64>,
    pub inbuilt_years: Option<f64>,
    pub inbuilt_shape: Option<String>,
    pub delta_r_source: Option<Map<String, Value>>,
    pub location: Option<String>,
    pub notes: Option<String>,
    pub outlier_model: String,
    pub outlier_prior: f64,
    pub phase_name: Option<String>,
    pub element_id: Option<String>,
}

/// A model element, as the importers build them: JSON, the shape the API and
/// the frontend's draft store exchange.
pub fn element(ids: &mut dyn Ids, element_type: &str, name: &str, children: Vec<Value>) -> Value {
    json!({
        "id": ids.next("e"),
        "element_type": element_type,
        "name": name,
        "tpq_age_bp": null,
        "taq_age_bp": null,
        "children": children,
    })
}

/// What a file turned out to contain. `errors`: the file was NOT imported,
/// every problem listed. `warnings`: imported, but something could not be
/// honoured or changes the model. `notes`: imported, and what was assumed on
/// the way in.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ImportResult {
    pub dates: Vec<Date>,
    pub model_tree: Option<Value>,
    /// date id -> model element id.
    pub element_assignments: Vec<(String, String)>,
    pub errors: Vec<String>,
    pub warnings: Vec<String>,
    pub notes: Vec<String>,
    pub source_format: &'static str,
    pub analysis_name: Option<String>,
    pub resolution: Option<i64>,
    /// For a refused .oxcal file: the same file read as its dates alone.
    pub dates_only: Option<Box<ImportResult>>,
    pub blocking_commands: Vec<String>,
}

fn num(v: Option<f64>) -> Value {
    v.map_or(Value::Null, Value::from)
}

fn text(v: &Option<String>) -> Value {
    v.as_ref().map_or(Value::Null, |s| Value::from(s.as_str()))
}

impl ImportResult {
    pub fn new(source_format: &'static str) -> Self {
        Self {
            source_format,
            ..Self::default()
        }
    }

    pub fn ok(&self) -> bool {
        self.errors.is_empty()
    }

    /// Drop everything read so far when the import failed, keeping the
    /// errors and warnings that say why: a refused result carrying rows is an
    /// invitation to use them.
    pub fn refuse(mut self) -> Self {
        if !self.errors.is_empty() {
            self.dates.clear();
            self.model_tree = None;
            self.element_assignments.clear();
        }
        self
    }

    /// A model structure is the whole point of a Bayesian run, so a file
    /// without one gets a group calibration.
    pub fn suggested_analysis_type(&self) -> &'static str {
        if self.model_tree.is_some() {
            "bayesian"
        } else {
            "group_calibration"
        }
    }

    pub fn date_json(d: &Date) -> Value {
        if d.source == Source::Oxcal {
            return Self::oxcal_date_json(d);
        }
        let mut out = json!({
            "sample_name": d.sample_name, "age_bp": d.age_bp, "error_1sigma": d.error_1sigma,
            "material_type": d.material_type, "curve_id": d.curve_id,
            "sample_type": text(&d.sample_type),
            "delta_r": num(d.delta_r), "delta_r_error": num(d.delta_r_error),
            "latitude": num(d.latitude), "longitude": num(d.longitude),
            "tpq_age_bp": num(d.tpq_age_bp), "taq_age_bp": num(d.taq_age_bp),
            "depth_cm": num(d.depth_cm), "ring_offset": num(d.ring_offset),
            "ring_offset_error": num(d.ring_offset_error), "inbuilt_years": num(d.inbuilt_years),
            "inbuilt_shape": text(&d.inbuilt_shape),
            "delta_r_source": d.delta_r_source.clone().map_or(Value::Null, Value::Object),
            "location": text(&d.location), "notes": text(&d.notes),
            "outlier_model": d.outlier_model, "outlier_prior": d.outlier_prior,
            "phase_name": text(&d.phase_name), "id": d.id,
        });
        if let Some(e) = &d.element_id {
            out["element_id"] = Value::from(e.as_str());
        }
        out
    }

    /// The keys `oxcal_script._translate_date` writes, the ring offsets once a
    /// wiggle match has set them, and the element once attached.
    fn oxcal_date_json(d: &Date) -> Value {
        let mut out = json!({
            "id": d.id, "sample_name": d.sample_name, "age_bp": d.age_bp,
            "error_1sigma": d.error_1sigma, "material_type": d.material_type,
            "curve_id": d.curve_id, "sample_type": text(&d.sample_type),
            "delta_r": num(d.delta_r), "delta_r_error": num(d.delta_r_error),
            "tpq_age_bp": num(d.tpq_age_bp), "taq_age_bp": num(d.taq_age_bp),
            "depth_cm": num(d.depth_cm), "location": text(&d.location), "notes": text(&d.notes),
            "outlier_prior": d.outlier_prior, "outlier_model": d.outlier_model,
            "phase_name": text(&d.phase_name),
        });
        if d.ring_offset.is_some() {
            out["ring_offset"] = num(d.ring_offset);
            out["ring_offset_error"] = num(d.ring_offset_error);
        }
        if let Some(e) = &d.element_id {
            out["element_id"] = Value::from(e.as_str());
        }
        out
    }

    /// The API's response shape (`ImportResult.as_dict`).
    pub fn to_json(&self) -> Value {
        json!({
            "ok": self.ok(),
            "dates": self.dates.iter().map(Self::date_json).collect::<Vec<_>>(),
            "model_tree": self.model_tree.clone().unwrap_or(Value::Null),
            "element_assignments": self.element_assignments.iter()
                .map(|(d, e)| (d.clone(), Value::from(e.as_str()))).collect::<Map<_, _>>(),
            "errors": self.errors, "warnings": self.warnings, "notes": self.notes,
            "source_format": self.source_format,
            "analysis_name": text(&self.analysis_name),
            "resolution": self.resolution.map_or(Value::Null, Value::from),
            "suggested_analysis_type": self.suggested_analysis_type(),
            "date_count": self.dates.len(),
            "dates_only": self.dates_only.as_ref().map_or(Value::Null, |d| d.to_json()),
            "blocking_commands": self.blocking_commands,
        })
    }
}

// --------------------------------------------------------------------------
// Column naming
// --------------------------------------------------------------------------

/// Canonical field -> the header spellings that mean it, case- and
/// order-independent. The app's own results export is among them, so a table
/// exported, corrected in a spreadsheet and imported back works.
pub const COLUMN_ALIASES: &[(&str, &[&str])] = &[
    (
        "sample_name",
        &[
            "sample_name",
            "name",
            "sample",
            "sample_id",
            "lab_code",
            "labcode",
            "lab_no",
            "lab_number",
            "lab_id",
            "code",
            "laboratory_code",
            "r_date",
        ],
    ),
    (
        "age_bp",
        &[
            "age_bp",
            "age",
            "bp",
            "c14_age",
            "c14_age_bp",
            "radiocarbon_age",
            "uncal_bp",
            "uncalibrated_age",
            "cra",
            "age_14c",
        ],
    ),
    (
        "error_1sigma",
        &[
            "error_1sigma",
            "error",
            "error_bp",
            "1_sigma_error",
            "1sigma_error",
            "1sigma",
            "sigma",
            "sd",
            "std",
            "stdev",
            "uncertainty",
            "error_1",
        ],
    ),
    (
        "material_type",
        &["material_type", "material", "sample_material"],
    ),
    (
        "curve_id",
        &["curve_id", "curve", "calibration_curve", "cal_curve"],
    ),
    ("sample_type", &["sample_type", "type", "reservoir_type"]),
    (
        "delta_r",
        &["delta_r", "deltar", "dr", "delta_r_value", "marine_offset"],
    ),
    (
        "delta_r_error",
        &[
            "delta_r_error",
            "deltar_error",
            "dr_error",
            "delta_r_sd",
            "delta_r_1sigma",
            "delta_r_uncertainty",
        ],
    ),
    (
        "phase",
        &[
            "phase",
            "context",
            "group",
            "context_name",
            "phase_name",
            "default_context",
            "default_context_name",
            "feature",
            "structure_path",
        ],
    ),
    (
        "tpq_age_bp",
        &["tpq_age_bp", "tpq", "tpq_bp", "tpq_cal_bp", "max_age_bp"],
    ),
    (
        "taq_age_bp",
        &["taq_age_bp", "taq", "taq_bp", "taq_cal_bp", "min_age_bp"],
    ),
    ("outlier_model", &["outlier_model"]),
    (
        "outlier_prior",
        &[
            "outlier_prior",
            "outlier",
            "outlier_probability",
            "outlier_p",
        ],
    ),
    ("depth_cm", &["depth_cm", "depth"]),
    (
        "ring_offset",
        &[
            "ring_offset",
            "ring_offsets",
            "ring",
            "rings",
            "gap",
            "gap_years",
            "offset",
            "ring_gap",
            "varve_offset",
        ],
    ),
    (
        "ring_offset_error",
        &[
            "ring_offset_error",
            "ring_error",
            "gap_error",
            "offset_error",
            "ring_offset_sd",
            "gap_sd",
        ],
    ),
    (
        "inbuilt_years",
        &[
            "inbuilt_years",
            "inbuilt_max",
            "inbuilt",
            "inbuilt_age",
            "max_inbuilt",
            "inbuilt_max_years",
            "inbuilt_mean",
        ],
    ),
    (
        "inbuilt_shape",
        &["inbuilt_shape", "inbuilt_kernel", "inbuilt_model"],
    ),
    ("delta_r_source", &["delta_r_source"]),
    (
        "latitude",
        &[
            "latitude",
            "lat",
            "y",
            "dec_lat",
            "decimal_latitude",
            "lat_dd",
        ],
    ),
    (
        "longitude",
        &[
            "longitude",
            "lon",
            "long",
            "lng",
            "x",
            "dec_long",
            "decimal_longitude",
            "lon_dd",
        ],
    ),
    ("location", &["location", "site", "site_name"]),
    (
        "notes",
        &["notes", "note", "comment", "comments", "remarks"],
    ),
    ("row_type", &["row_type"]),
];

pub const REQUIRED_COLUMNS: [&str; 3] = ["sample_name", "age_bp", "error_1sigma"];

const RESULT_ONLY_PREFIXES: [&str; 4] =
    ["posterior_", "unmodelled_", "agreement", "outlier_percent"];
const ERA_SUFFIXES: [&str; 2] = ["_bc_ad", "_bce_ce"];

static HPD_COLUMN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\d+(?:_\d+)?percent_hpd").unwrap());
static NOT_WORD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^a-z0-9]+").unwrap());

/// Output columns of the app's own results export, so re-importing one
/// neither reads a posterior back in as data nor warns about them.
fn is_result_only(key: &str, has_row_type: bool) -> bool {
    key == "order"
        || RESULT_ONLY_PREFIXES.iter().any(|p| key.starts_with(p))
        || HPD_COLUMN.is_match(key)
        || ERA_SUFFIXES.iter().any(|s| key.ends_with(s))
        // 'Depth' in the results export is nesting depth; in a lab report it
        // is depth_cm. Only our own export writes a Row Type column.
        || (key == "depth" && has_row_type)
}

/// A header cell reduced to a comparable key: lowercase, the symbols a
/// spreadsheet contains spelled out, everything else collapsed to single
/// underscores.
pub fn normalise_header(raw: &str) -> String {
    let mut text = python::strip(raw).to_lowercase();
    for (symbol, word) in [
        ("σ", "sigma"),
        ("±", "error"),
        ("Δ", "delta"),
        ("δ", "delta"),
        ("%", "percent"),
    ] {
        text = text.replace(symbol, word);
    }
    NOT_WORD
        .replace_all(&text, "_")
        .trim_matches('_')
        .to_owned()
}

/// (header -> canonical, unrecognised headers, duplicated meanings).
fn resolve_columns(
    fieldnames: &[String],
) -> (BTreeMap<String, &'static str>, Vec<String>, Vec<String>) {
    let keys: Vec<String> = fieldnames.iter().map(|f| normalise_header(f)).collect();
    let has_row_type = keys.iter().any(|k| k == "row_type");
    let mut mapping = BTreeMap::new();
    let mut unknown = Vec::new();
    let mut duplicated = Vec::new();
    let mut claimed: Vec<(&str, String)> = Vec::new();
    for raw in fieldnames {
        let key = normalise_header(raw);
        if key.is_empty() || is_result_only(&key, has_row_type) {
            continue;
        }
        let Some(&(canonical, _)) = COLUMN_ALIASES
            .iter()
            .find(|(_, aliases)| aliases.contains(&key.as_str()))
        else {
            unknown.push(python::strip(raw).to_owned());
            continue;
        };
        if let Some((_, first)) = claimed.iter().find(|(c, _)| *c == canonical) {
            duplicated.push(format!(
                "'{first}' and '{}' both mean {canonical}",
                python::strip(raw)
            ));
            continue;
        }
        mapping.insert(raw.clone(), canonical);
        claimed.push((canonical, python::strip(raw).to_owned()));
    }
    (mapping, unknown, duplicated)
}

// --------------------------------------------------------------------------
// Cell parsing
// --------------------------------------------------------------------------

fn clean(value: Option<&str>) -> String {
    python::strip(value.unwrap_or("")).to_owned()
}

/// A numeric cell, tolerating thousands separators, a stray '±' or '+/-',
/// '~' and a Unicode minus. None for an empty cell; an error and None for one
/// that is not a number, so every bad cell is seen at once.
fn number(value: Option<&str>, field: &str, line: usize, errors: &mut Vec<String>) -> Option<f64> {
    let text = clean(value);
    if text.is_empty() {
        return None;
    }
    let cleaned = text
        .replace([',', '±'], "")
        .replace("+/-", "")
        .replace('~', "")
        .replace('−', "-");
    match python::float(&cleaned) {
        Some(v) => Some(v),
        None => {
            errors.push(format!("Row {line}: {field} is not a number ('{text}')"));
            None
        }
    }
}

fn curve(
    value: Option<&str>,
    line: usize,
    errors: &mut Vec<String>,
    library: &Library,
) -> Option<String> {
    let text = clean(value);
    if text.is_empty() {
        return None;
    }
    if let Some(id) = library.resolve_alias(&text) {
        return Some(id);
    }
    let available = library.curve_names(false);
    errors.push(format!(
        "Row {line}: unknown calibration curve '{text}'. This app calibrates against {}; a curve it does not have cannot be substituted for one it does.",
        available.join(", ")
    ));
    None
}

static DMS_MARKS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"(?i)[°º'′"″]|\b(?:deg|degs|degrees|min|mins|sec|secs)\b"#).unwrap()
});
static COMPASS: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^[NSEW]\s*[-+.\d]|[-.\d]\s*[NSEW]$").unwrap());
static TWO_NUMBERS: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[-+]?[\d.]+(?:\s+[-+]?[\d.]+)+$").unwrap());

/// A coordinate in WGS84 decimal degrees and nothing else. Degrees and
/// minutes are REFUSED, not parsed: reading 54°30' as 54.30 is a 55 km error
/// nothing downstream would catch. A decimal comma likewise, which the number
/// parser would read as a thousands separator.
fn coordinate(
    value: Option<&str>,
    field: &str,
    line: usize,
    errors: &mut Vec<String>,
) -> Option<f64> {
    let text = clean(value);
    if text.is_empty() {
        return None;
    }
    let wanted = format!(
        "Give {field} in decimal degrees, with a point and a leading minus for south and west -- 54.5 and -5.6, not 54°30'N and 5°36'W."
    );
    if DMS_MARKS.is_match(&text) || COMPASS.is_match(&text) || TWO_NUMBERS.is_match(&text) {
        errors.push(format!(
            "Row {line}: {field} '{text}' is in degrees and minutes. {wanted}"
        ));
        return None;
    }
    if text.contains(',') {
        errors.push(format!(
            "Row {line}: {field} '{text}' uses a comma as its decimal mark. {wanted}"
        ));
        return None;
    }
    number(Some(&text), field, line, errors)
}

/// A phase name; a structure PATH ('Site > North > Feature J', as the
/// results export writes it) reduces to its innermost element.
fn phase_name(value: Option<&str>) -> Option<String> {
    let text = clean(value);
    if text.is_empty() {
        return None;
    }
    let last = if text.contains('>') {
        python::strip(text.rsplit('>').next().unwrap()).to_owned()
    } else {
        text
    };
    (!last.is_empty()).then_some(last)
}

fn choice(
    value: Option<&str>,
    allowed: [&str; 2],
    what: &str,
    line: usize,
    errors: &mut Vec<String>,
) -> Option<String> {
    let text = clean(value).to_lowercase();
    if text.is_empty() {
        return None;
    }
    if allowed.contains(&text.as_str()) {
        return Some(text);
    }
    errors.push(format!(
        "Row {line}: unknown {what} '{}' (expected '{}' or '{}')",
        clean(value),
        allowed[0],
        allowed[1]
    ));
    None
}

/// An outlier prior as a probability, from a fraction or a percentage.
fn outlier_prior(value: Option<&str>, line: usize, errors: &mut Vec<String>) -> Option<f64> {
    let text = clean(value);
    if text.is_empty() {
        return None;
    }
    let percent = text.ends_with('%');
    let mut n = number(
        Some(text.trim_end_matches('%')),
        "outlier prior",
        line,
        errors,
    )?;
    if percent {
        n /= 100.0;
    }
    if n > 1.0 {
        errors.push(format!(
            "Row {line}: outlier prior {text} is above 1. Give it as a probability (0.05) or a percentage with a % sign (5%)."
        ));
        return None;
    }
    Some(n)
}

fn delta_r_source(
    value: Option<&str>,
    line: usize,
    errors: &mut Vec<String>,
) -> Option<Map<String, Value>> {
    let text = clean(value);
    if text.is_empty() {
        return None;
    }
    match serde_json::from_str::<Value>(&text) {
        Ok(Value::Object(map)) => Some(map),
        _ => {
            errors.push(format!(
                "Row {line}: delta_r_source is not the record the app exports (a JSON object). Clear the cell to import the date without it."
            ));
            None
        }
    }
}

// --------------------------------------------------------------------------
// CSV
// --------------------------------------------------------------------------

/// `re.sub(r'\r(?!\n)', '\n', text)`: each CR not followed by LF becomes LF.
fn lone_cr_to_lf(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        out.push(if c == '\r' && chars.peek() != Some(&'\n') {
            '\n'
        } else {
            c
        });
    }
    out
}

/// Comma, tab or semicolon, whichever the header line uses most; ties go to
/// that order.
fn sniff_delimiter(text: &str) -> char {
    let header = text.split('\n').next().unwrap_or("");
    let mut best = (',', 0);
    for d in [',', '\t', ';'] {
        let count = header.matches(d).count();
        if count > best.1 {
            best = (d, count);
        }
    }
    best.0
}

pub(crate) fn name_from_filename(filename: Option<&str>) -> Option<String> {
    static EXTENSION: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(?i)\.(csv|tsv|txt|oxcal)$").unwrap());
    let filename = filename?;
    let stem = EXTENSION.replace(python::strip(filename), "").into_owned();
    let stem = python::strip(
        stem.rsplit('/')
            .next()
            .unwrap()
            .rsplit('\\')
            .next()
            .unwrap(),
    )
    .to_owned();
    (!stem.is_empty()).then_some(stem)
}

/// Read a delimited table of dates: header-driven and order-independent (see
/// [`COLUMN_ALIASES`]). A `phase` column turns a list of dates into a model.
/// `Err` only where Python's csv reader itself raises.
pub fn parse_csv(
    text: &str,
    library: &Library,
    filename: Option<&str>,
    ids: &mut dyn Ids,
) -> Result<ImportResult, CsvError> {
    let mut result = ImportResult::new("csv");
    result.analysis_name = name_from_filename(filename);
    let text = text.trim_start_matches('\u{feff}');
    if python::strip(text).is_empty() {
        result.errors.push("The file is empty.".to_owned());
        return Ok(result);
    }
    // Old Mac line endings (a lone CR) are a convention, not a fault: read
    // them as line breaks. CRLF is left alone; the csv reader handles it.
    let normalised = lone_cr_to_lf(text);
    let text = normalised.as_str();
    let delimiter = sniff_delimiter(text);
    let (fieldnames, raw_rows) = pycsv::dict_rows(text, delimiter)?;
    let Some(fieldnames) = fieldnames.filter(|f| !f.is_empty()) else {
        result.errors.push("The file has no header row.".to_owned());
        return Ok(result);
    };
    let (mapping, unknown, duplicated) = resolve_columns(&fieldnames);
    let missing: Vec<&str> = REQUIRED_COLUMNS
        .iter()
        .copied()
        .filter(|c| !mapping.values().any(|m| m == c))
        .collect();
    if !missing.is_empty() {
        let found: Vec<String> = fieldnames
            .iter()
            .map(|f| python::strip(f))
            .filter(|f| !f.is_empty())
            .map(|f| format!("'{f}'"))
            .collect();
        result.errors.push(format!(
            "The file is missing required column(s): {}. Found: {}. Download the template for the column names this reads.",
            missing.join(", "),
            if found.is_empty() { "nothing".to_owned() } else { found.join(", ") }
        ));
        return Ok(result.refuse());
    }
    for problem in duplicated {
        result
            .errors
            .push(format!("Two columns mean the same thing: {problem}."));
    }
    if !unknown.is_empty() {
        result.warnings.push(format!(
            "These columns were not recognised and were ignored: {}. Check for a misspelling -- a column this importer does not recognise is one whose values are not being used.",
            unknown.iter().map(|u| format!("'{u}'")).collect::<Vec<_>>().join(", ")
        ));
    }
    if delimiter != ',' {
        result.notes.push(format!(
            "Read as {}-separated.",
            if delimiter == '\t' {
                "tab"
            } else {
                "semicolon"
            }
        ));
    }

    let has_row_type = mapping.values().any(|c| *c == "row_type");
    let mut skipped_structural = 0;
    let mut rows: Vec<(usize, BTreeMap<&str, Option<String>>)> = Vec::new();
    for (offset, raw) in raw_rows.iter().enumerate() {
        let line = offset + 2;
        let row: BTreeMap<&str, Option<String>> = raw
            .values
            .iter()
            .filter_map(|(header, value)| mapping.get(header).map(|c| (*c, value.clone())))
            .collect();
        if !row.values().any(|v| !clean(v.as_deref()).is_empty()) {
            continue;
        }
        if has_row_type {
            let kind = clean(row.get("row_type").and_then(|v| v.as_deref())).to_lowercase();
            if kind != "date" && !kind.is_empty() {
                skipped_structural += 1;
                continue;
            }
        }
        rows.push((line, row));
    }
    if rows.len() > MAX_DATES {
        result.errors.push(format!(
            "The file holds {} dates; the limit is {MAX_DATES} per import.",
            rows.len()
        ));
        return Ok(result.refuse());
    }
    if skipped_structural > 0 {
        result.notes.push(format!(
            "Skipped {skipped_structural} structural row(s) (sequences, phases and boundaries) -- only date rows carry a measurement to import."
        ));
    }
    for (line, row) in &rows {
        if let Some(date) = row_to_date(row, *line, &mut result.errors, library) {
            result.dates.push(date);
        }
    }
    if result.dates.is_empty() && result.errors.is_empty() {
        result.errors.push("No dates found in the file.".to_owned());
        return Ok(result.refuse());
    }
    finish(&mut result, ids);
    Ok(result.refuse())
}

/// One row as a date, or None with its errors appended, in the order the
/// Python evaluates the cells.
fn row_to_date(
    row: &BTreeMap<&str, Option<String>>,
    line: usize,
    errors: &mut Vec<String>,
    library: &Library,
) -> Option<Date> {
    let get = |k: &str| row.get(k).and_then(|v| v.as_deref());
    let before = errors.len();
    let name = clean(get("sample_name"));
    if name.is_empty() {
        errors.push(format!("Row {line}: sample name is blank"));
    }
    let age = number(get("age_bp"), "age BP", line, errors);
    if age.is_none() && clean(get("age_bp")).is_empty() {
        errors.push(format!("Row {line}: age BP is missing"));
    }
    let error = number(get("error_1sigma"), "1-sigma error", line, errors);
    if error.is_none() && clean(get("error_1sigma")).is_empty() {
        errors.push(format!("Row {line}: 1-sigma error is missing"));
    }
    let curve_id = curve(get("curve_id"), line, errors, library);
    let mut sample_type = Some(clean(get("sample_type")).to_lowercase()).filter(|s| !s.is_empty());
    if let Some(t) = &sample_type
        && t != "terrestrial"
        && t != "marine"
    {
        errors.push(format!(
            "Row {line}: sample type '{}' should be 'terrestrial' or 'marine'",
            clean(get("sample_type"))
        ));
        sample_type = None;
    }
    let material = clean(get("material_type"));
    let delta_r = number(get("delta_r"), "delta R", line, errors);
    let delta_r_error = number(get("delta_r_error"), "delta R error", line, errors);
    let latitude = coordinate(get("latitude"), "latitude", line, errors);
    let longitude = coordinate(get("longitude"), "longitude", line, errors);
    let tpq = number(get("tpq_age_bp"), "TPQ", line, errors);
    let taq = number(get("taq_age_bp"), "TAQ", line, errors);
    let depth = number(get("depth_cm"), "depth", line, errors);
    let ring_offset = number(get("ring_offset"), "ring offset", line, errors);
    let ring_offset_error = number(get("ring_offset_error"), "ring offset error", line, errors);
    let inbuilt_years = number(get("inbuilt_years"), "inbuilt age", line, errors);
    let inbuilt_shape = choice(
        get("inbuilt_shape"),
        ["uniform", "exponential"],
        "inbuilt-age shape",
        line,
        errors,
    );
    let source = delta_r_source(get("delta_r_source"), line, errors);
    let location = Some(clean(get("location"))).filter(|s| !s.is_empty());
    let notes = Some(clean(get("notes"))).filter(|s| !s.is_empty());
    let outlier_model = choice(
        get("outlier_model"),
        ["general", "charcoal"],
        "outlier model",
        line,
        errors,
    );
    let prior = outlier_prior(get("outlier_prior"), line, errors);
    if errors.len() != before {
        return None;
    }
    Some(Date {
        source: Source::Csv,
        id: String::new(),
        sample_name: name,
        age_bp: age?,
        error_1sigma: error?,
        material_type: if material.is_empty() {
            "unknown".to_owned()
        } else {
            material
        },
        curve_id: curve_id.unwrap_or_else(|| "intcal20".to_owned()),
        sample_type,
        delta_r,
        delta_r_error,
        latitude,
        longitude,
        tpq_age_bp: tpq,
        taq_age_bp: taq,
        depth_cm: depth,
        ring_offset,
        ring_offset_error,
        inbuilt_years,
        inbuilt_shape,
        delta_r_source: source,
        location,
        notes,
        outlier_model: outlier_model.unwrap_or_else(|| "general".to_owned()),
        // `or 0.0`: a prior of zero reads as none, which it is.
        outlier_prior: prior.filter(|&p| p != 0.0).unwrap_or(0.0),
        phase_name: phase_name(get("phase")),
        element_id: None,
    })
}

/// Ids, validation, and a structure if the dates carry phase names.
fn finish(result: &mut ImportResult, ids: &mut dyn Ids) {
    for date in &mut result.dates {
        date.id = ids.next("d");
    }
    validate_dates(result);
    if result.errors.is_empty() {
        tree_from_phases(result, ids);
    }
}

/// Every date through the measurement schema, reported against its own row
/// while the file is still open; and the warnings no single row shows.
pub fn validate_dates(result: &mut ImportResult) {
    let mut counts: Vec<(String, usize)> = Vec::new();
    for (index, d) in result.dates.iter().enumerate() {
        let values = Values {
            sample_name: &d.sample_name,
            age_bp: d.age_bp,
            error_1sigma: d.error_1sigma,
            material_type: &d.material_type,
            delta_r: d.delta_r,
            delta_r_error: d.delta_r_error,
            latitude: d.latitude,
            longitude: d.longitude,
            depth_cm: d.depth_cm,
            ring_offset: d.ring_offset,
            ring_offset_error: d.ring_offset_error,
            inbuilt_years: d.inbuilt_years,
            location: d.location.as_deref(),
            notes: d.notes.as_deref(),
            tpq_age_bp: d.tpq_age_bp,
            taq_age_bp: d.taq_age_bp,
            outlier_prior: d.outlier_prior,
        };
        let label = if d.sample_name.is_empty() {
            format!("row {}", index + 1)
        } else {
            d.sample_name.clone()
        };
        for (field, message) in schema::validate(&values) {
            let field = if field.is_empty() {
                "row".to_owned()
            } else {
                field
            };
            result
                .errors
                .push(format!("'{label}': {field} -- {message}"));
        }
        let key = python::strip(&d.sample_name).to_lowercase();
        match counts.iter_mut().find(|(k, _)| *k == key) {
            Some((_, n)) => *n += 1,
            None => counts.push((key, 1)),
        }
    }
    let null_island: Vec<String> = result
        .dates
        .iter()
        .enumerate()
        .filter(|(_, d)| d.latitude == Some(0.0) && d.longitude == Some(0.0))
        .map(|(i, d)| {
            if d.sample_name.is_empty() {
                format!("row {}", i + 1)
            } else {
                d.sample_name.clone()
            }
        })
        .collect();
    if !null_island.is_empty() {
        result.warnings.push(format!(
            "These dates are at latitude 0, longitude 0: {}. That is a real place in the Gulf of Guinea, but it is usually an empty spreadsheet cell -- check the file before relying on the positions.",
            null_island.join(", ")
        ));
    }
    let mut repeated: Vec<&str> = counts
        .iter()
        .filter(|(k, n)| *n > 1 && !k.is_empty())
        .map(|(k, _)| k.as_str())
        .collect();
    repeated.sort_unstable();
    if !repeated.is_empty() {
        result.warnings.push(format!(
            "These sample names appear more than once: {}. They import as separate dates -- check the file does not repeat a row.",
            repeated.join(", ")
        ));
    }
}

/// A starting structure from a CSV's phase names: one Sequence holding a
/// Start Boundary, the phases inside one unordered Phase, and an End
/// Boundary. Unordered because a CSV says which dates go together and
/// nothing about which group came first.
fn tree_from_phases(result: &mut ImportResult, ids: &mut dyn Ids) {
    if !result.dates.iter().any(|d| d.phase_name.is_some()) {
        if !result.dates.is_empty() {
            result.notes.push(
                "No phase/context column, so the dates come in with no model structure -- they will be calibrated independently. Add a \"phase\" column, or group them in the Model Structure editor, to model them together.".to_owned(),
            );
        }
        return;
    }
    let unnamed = result
        .dates
        .iter()
        .filter(|d| d.phase_name.is_none())
        .count();
    let mut order: Vec<String> = Vec::new();
    for d in &result.dates {
        if let Some(p) = &d.phase_name
            && !order.contains(p)
        {
            order.push(p.clone());
        }
    }
    let phases: Vec<Value> = order
        .iter()
        .map(|name| element(ids, "phase", name, Vec::new()))
        .collect();
    let catch_all = (unnamed > 0).then(|| {
        result.warnings.push(format!(
            "{unnamed} date(s) have no phase/context value. They are grouped under \"Unassigned dates\" so none is lost -- move them in the Model Structure editor."
        ));
        element(ids, "phase", "Unassigned dates", Vec::new())
    });
    for d in &mut result.dates {
        let target = match &d.phase_name {
            Some(p) => &phases[order.iter().position(|o| o == p).unwrap()],
            None => catch_all.as_ref().unwrap(),
        };
        let id = target["id"].as_str().unwrap().to_owned();
        d.element_id = Some(id.clone());
        result.element_assignments.push((d.id.clone(), id));
    }
    let mut children = phases;
    children.extend(catch_all);
    let contexts = element(ids, "phase", "Contexts", children);
    let start = element(ids, "boundary", "Start", Vec::new());
    let end = element(ids, "boundary", "End", Vec::new());
    let name = result
        .analysis_name
        .clone()
        .unwrap_or_else(|| "Imported model".to_owned());
    result.model_tree = Some(element(ids, "sequence", &name, vec![start, contexts, end]));
    result.notes.push(format!(
        "Built a starting structure from the phase column: {} phase(s) inside one Start/End pair. The phases are UNORDERED with respect to each other -- a CSV does not say which came first. Open Model Structure to order them or move the boundaries.",
        order.len()
    ));
}

// --------------------------------------------------------------------------
// The template
// --------------------------------------------------------------------------

pub const TEMPLATE_COLUMNS: [&str; 15] = [
    "sample_name",
    "age_bp",
    "error_1sigma",
    "material_type",
    "curve_id",
    "phase",
    "delta_r",
    "delta_r_error",
    "outlier_model",
    "outlier_prior",
    "tpq_age_bp",
    "taq_age_bp",
    "latitude",
    "longitude",
    "notes",
];

const TEMPLATE_ROWS: [[&str; 15]; 3] = [
    [
        "Ld-10012",
        "685",
        "25",
        "charcoal",
        "shcal20",
        "Fire Feature J",
        "",
        "",
        "general",
        "0.05",
        "",
        "",
        "",
        "",
        "short-lived twig",
    ],
    [
        "Ld-10010",
        "842",
        "21",
        "charcoal",
        "shcal20",
        "Fire Feature J",
        "",
        "",
        "charcoal",
        "1.0",
        "",
        "",
        "",
        "",
        "old wood -- declared residual",
    ],
    [
        "LB-10002",
        "970",
        "47",
        "marine shell",
        "marine20",
        "Midden 1",
        "-7",
        "45",
        "general",
        "0.05",
        "",
        "",
        "-45.8742",
        "170.5036",
        "delta_r needs its error; decimal degrees only",
    ],
];

/// `csv.writer`'s quoting: a field holding the delimiter, a quote or a line
/// break is quoted, with quotes doubled; rows end in CRLF.
pub fn csv_row(fields: &[&str]) -> String {
    let quoted: Vec<String> = fields
        .iter()
        .map(|f| {
            if f.contains([',', '"', '\n', '\r']) {
                format!("\"{}\"", f.replace('"', "\"\""))
            } else {
                (*f).to_owned()
            }
        })
        .collect();
    format!("{}\r\n", quoted.join(","))
}

/// The blank-with-examples template offered for download.
pub fn template_csv() -> String {
    let mut out = csv_row(&TEMPLATE_COLUMNS);
    for row in &TEMPLATE_ROWS {
        out.push_str(&csv_row(row));
    }
    out
}

// --------------------------------------------------------------------------
// Dispatch
// --------------------------------------------------------------------------

/// Whether this is an OxCal model rather than a table: a command only CQL2
/// has, followed by an opening bracket.
pub fn looks_like_cql2(text: &str) -> bool {
    static COMMENT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"//[^\n]*").unwrap());
    static COMMAND: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"\b(Plot|Sequence|Phase|R_Date|R_Simulate|Boundary|Outlier_Model|Curve)\s*\(")
            .unwrap()
    });
    COMMAND.is_match(&COMMENT.replace_all(text, ""))
}

/// Text from an upload, as UTF-8 (a byte-order mark dropped) or failing that
/// Windows-1252; None when it is neither.
pub fn decode(content: &[u8]) -> Option<String> {
    python::decode_utf8_sig(content).or_else(|| python::decode_cp1252(content))
}

/// An uploaded file, read by the reader its extension names, or by its
/// content where the extension says nothing (`.txt` names both tables and
/// saved OxCal models). `Err` only where Python's csv reader raises.
pub fn parse_upload(
    filename: &str,
    content: &[u8],
    library: &Library,
    ids: &mut dyn Ids,
) -> Result<ImportResult, CsvError> {
    if content.len() > MAX_CONTENT_BYTES {
        let mut result = ImportResult::new("csv");
        result.errors.push(format!(
            "The file is {} KB; the limit is {} KB. This importer reads lists of dates and OxCal model files, not data archives.",
            content.len() / 1024,
            MAX_CONTENT_BYTES / 1024
        ));
        return Ok(result);
    }
    let Some(text) = decode(content) else {
        let mut result = ImportResult::new("csv");
        result.errors.push(
            "The file is not text this importer can read (it decoded as neither UTF-8 nor Windows-1252). If it is a spreadsheet, save it as CSV first.".to_owned(),
        );
        return Ok(result);
    };
    let lower = filename.to_lowercase();
    if lower.ends_with(".oxcal") {
        return Ok(crate::oxcal_script::parse_oxcal(
            &text,
            library,
            Some(filename),
            ids,
        ));
    }
    if lower.ends_with(".csv") || lower.ends_with(".tsv") {
        return parse_csv(&text, library, Some(filename), ids);
    }
    if looks_like_cql2(&text) {
        return Ok(crate::oxcal_script::parse_oxcal(
            &text,
            library,
            Some(filename),
            ids,
        ));
    }
    parse_csv(&text, library, Some(filename), ids)
}
