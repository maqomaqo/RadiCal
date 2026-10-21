//! Reading a calibration curve out of an uploaded file. Port of
//! `services/curve_import.py`.
//!
//! Parsing and storing are separate steps, and this module only reads. A
//! file that cannot be read comes back as a list of what is wrong with it.
//! Almost nothing about a curve file is checkable by eye afterwards, so this
//! refuses more than it repairs: descending calendar order is normalised and
//! comments skipped, being format conventions; everything else -- a
//! duplicate calendar age, a non-positive sigma, a column that looks like
//! F14C -- is an error naming the row.

use std::sync::LazyLock;

use radical_core::pyfmt::g;
use regex::Regex;
use serde_json::{Map, Value, json};

use crate::pycsv;
use crate::python;

/// A curve file is bigger than a date list but bounded -- IntCal20 is 220 KB.
pub const MAX_CONTENT_BYTES: usize = 8 * 1024 * 1024;
/// The most rows worth reporting individually.
pub const MAX_REPORTED_ERRORS: usize = 25;
/// Below this, a "radiocarbon age" column is far more likely to be F14C.
pub const F14C_CEILING: f64 = 10.0;

/// Column-name spellings understood in a headed CSV.
pub const COLUMN_ALIASES: [(&str, &[&str]); 3] = [
    (
        "cal_bp",
        &[
            "cal_bp",
            "calbp",
            "cal bp",
            "calendar_age",
            "calendar age",
            "cal_age",
            "calage",
            "cal",
            "year_bp",
            "bp",
        ],
    ),
    (
        "c14_age",
        &[
            "c14_age",
            "c14age",
            "14c_age",
            "14c age",
            "c14",
            "conv_bp",
            "convbp",
            "radiocarbon_age",
            "radiocarbon age",
            "rc_age",
            "conventional_age",
            "14c",
        ],
    ),
    (
        "error",
        &[
            "error",
            "sigma",
            "c14_error",
            "c14error",
            "1sigma",
            "1_sigma",
            "std",
            "stdev",
            "uncertainty",
            "calsigma",
            "cal_sigma",
            "err",
        ],
    ),
];

/// How to read the calendar column.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Calendar {
    /// The .14c convention.
    #[default]
    CalBp,
    /// Negative for BC, no year zero.
    BcAd,
}

impl Calendar {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::CalBp => "cal_bp",
            Self::BcAd => "bc_ad",
        }
    }
}

/// Which column holds what: (cal_bp, c14_age, error).
pub type Columns = [usize; 3];

/// What a curve file turned out to contain.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct CurveImportResult {
    /// The normalised curve: ascending calendar age, (cal BP, 14C age, sigma).
    pub points: Vec<(f64, f64, f64)>,
    pub errors: Vec<String>,
    pub warnings: Vec<String>,
    pub notes: Vec<String>,
    pub source_format: &'static str,
    pub calendar: Calendar,
    /// The columns read, for storing alongside the file so the parse is
    /// reproducible.
    pub columns: Option<Columns>,
    pub suggested_name: Option<String>,
    pub suggested_reference: Option<String>,
}

impl CurveImportResult {
    pub fn ok(&self) -> bool {
        self.errors.is_empty()
    }

    fn refuse(mut self) -> Self {
        if !self.errors.is_empty() {
            self.points.clear();
        }
        self
    }

    pub fn summary(&self) -> Value {
        if self.points.is_empty() {
            return json!({});
        }
        let fold = |f: fn(&(f64, f64, f64)) -> f64, pick: fn(f64, f64) -> f64, start: f64| {
            self.points.iter().map(f).fold(start, pick)
        };
        json!({
            "data_points": self.points.len(),
            "cal_min": fold(|p| p.0, f64::min, f64::INFINITY),
            "cal_max": fold(|p| p.0, f64::max, f64::NEG_INFINITY),
            "c14_min": fold(|p| p.1, f64::min, f64::INFINITY),
            "c14_max": fold(|p| p.1, f64::max, f64::NEG_INFINITY),
        })
    }

    /// A downsampled [cal, c14, sigma] series for drawing the curve before
    /// anything is stored.
    pub fn preview(&self, max_points: usize) -> Vec<[f64; 3]> {
        let step = (self.points.len() / max_points).max(1);
        self.points
            .iter()
            .step_by(step)
            .map(|p| [p.0, p.1, p.2])
            .collect()
    }

    /// `{'calendar': ..., 'columns': {...}}`, as stored with the curve.
    pub fn parse_options(&self) -> Value {
        let mut out = Map::new();
        out.insert("calendar".to_owned(), Value::from(self.calendar.as_str()));
        if let Some([cal, c14, error]) = self.columns {
            out.insert(
                "columns".to_owned(),
                json!({"cal_bp": cal, "c14_age": c14, "error": error}),
            );
        }
        Value::Object(out)
    }

    /// The API's response shape (`CurveImportResult.as_dict`).
    pub fn to_json(&self) -> Value {
        json!({
            "ok": self.ok(),
            "errors": self.errors,
            "warnings": self.warnings,
            "notes": self.notes,
            "source_format": self.source_format,
            "parse_options": self.parse_options(),
            "suggested_name": self.suggested_name,
            "suggested_reference": self.suggested_reference,
            "summary": self.summary(),
            "preview": self.preview(400),
        })
    }
}

/// UTF-8 (a byte-order mark dropped), else Latin-1, which cannot fail.
pub fn decode(content: &[u8]) -> String {
    python::decode_utf8_sig(content)
        .unwrap_or_else(|| content.iter().map(|&b| char::from(b)).collect())
}

fn header_lines(text: &str) -> Vec<&str> {
    python::splitlines(text)
        .into_iter()
        .map(python::strip)
        .filter(|l| l.starts_with('#'))
        .map(|l| python::strip(l.trim_start_matches('#')))
        .collect()
}

static NAME: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b((?:IntCal|SHCal|Marine|NHZ|SH)[\s\x1c-\x1f-]?\d{2,4})\b").unwrap()
});
static NAME_SEPARATORS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[\s\x1c-\x1f-]+").unwrap());
static CITATION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b([A-Z][A-Za-z'-]+)[\s\x1c-\x1f]+et[\s\x1c-\x1f]+al\.?,?[\s\x1c-\x1f]+(\d{4})")
        .unwrap()
});

/// A name and citation guessed from the file's leading comments.
fn suggestions(text: &str) -> (Option<String>, Option<String>) {
    let headers = header_lines(text);
    let name = headers
        .iter()
        .find_map(|l| NAME.captures(l))
        .map(|c| NAME_SEPARATORS.replace_all(&c[1], "").into_owned());
    let reference = headers
        .iter()
        .find_map(|l| CITATION.captures(l))
        .map(|c| format!("{} et al. {}", &c[1], &c[2]));
    (name, reference)
}

fn is_number(text: &str) -> bool {
    python::float(text).is_some()
}

static WHITESPACE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[\s\x1c-\x1f]+").unwrap());
static NOT_WORD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^a-z0-9]+").unwrap());

type Rows = (Vec<Vec<String>>, Option<Vec<String>>, &'static str);

/// The file split into rows: (rows, header, format). Comment lines dropped;
/// a first row whose first three cells are not all numbers is the header.
fn rows_from_delimited(text: &str) -> Result<Rows, pycsv::CsvError> {
    let body: Vec<&str> = python::splitlines(text)
        .into_iter()
        .filter(|l| {
            let s = python::strip(l);
            !s.is_empty() && !s.starts_with('#')
        })
        .collect();
    if body.is_empty() {
        return Ok((Vec::new(), None, "csv"));
    }
    let sample = body[..body.len().min(5)].join("\n");
    let delimiter = if sample.matches('\t').count() > sample.matches(',').count() {
        '\t'
    } else {
        ','
    };
    if delimiter == ',' && !sample.contains(',') && WHITESPACE.is_match(&sample) {
        let rows = body
            .iter()
            .map(|l| {
                WHITESPACE
                    .split(python::strip(l))
                    .map(str::to_owned)
                    .collect()
            })
            .collect();
        return Ok((rows, None, "whitespace"));
    }
    let mut rows = pycsv::records(&body.join("\n"), delimiter)?;
    let mut header = None;
    if !rows.is_empty() && !rows[0].iter().take(3).all(|c| is_number(c)) {
        header = Some(
            rows.remove(0)
                .iter()
                .map(|c| python::strip(c).to_lowercase())
                .collect(),
        );
    }
    Ok((rows, header, if delimiter == '\t' { "tsv" } else { "csv" }))
}

fn normalise(cell: &str) -> String {
    NOT_WORD
        .replace_all(&python::strip(cell).to_lowercase(), "_")
        .trim_matches('_')
        .to_owned()
}

/// A header row matched to the three columns needed, in any order.
fn column_indices(header: &[String], errors: &mut Vec<String>) -> Option<Columns> {
    let mut found: [Option<usize>; 3] = [None; 3];
    for (k, (_, aliases)) in COLUMN_ALIASES.iter().enumerate() {
        let wanted: Vec<String> = aliases.iter().map(|a| normalise(a)).collect();
        found[k] = header
            .iter()
            .position(|cell| wanted.contains(&normalise(cell)));
    }
    let missing: Vec<&str> = COLUMN_ALIASES
        .iter()
        .zip(&found)
        .filter(|(_, f)| f.is_none())
        .map(|((name, _), _)| *name)
        .collect();
    if !missing.is_empty() {
        errors.push(format!(
            "Could not find a column for: {}. A curve needs a calendar age, a radiocarbon age and a 1-sigma error; the header row read as: {}.",
            missing.join(", "),
            header.join(", ")
        ));
        return None;
    }
    Some([found[0].unwrap(), found[1].unwrap(), found[2].unwrap()])
}

/// Read a calibration curve from the text of an uploaded file. `columns`,
/// when given, is used instead of detecting them -- what a stored curve
/// passes back in, so re-reading it cannot depend on today's detection.
pub fn parse_curve(text: &str, calendar: Calendar, columns: Option<Columns>) -> CurveImportResult {
    let mut result = CurveImportResult {
        calendar,
        source_format: "14c",
        ..CurveImportResult::default()
    };
    (result.suggested_name, result.suggested_reference) = suggestions(text);
    let (rows, header, source_format) = match rows_from_delimited(text) {
        Ok(read) => read,
        Err(exc) => {
            result
                .errors
                .push(crate::date_import::unreadable_table(&exc));
            return result.refuse();
        }
    };
    result.source_format = source_format;
    if rows.is_empty() {
        result
            .errors
            .push("The file has no data rows -- only comments or blank lines.".to_owned());
        return result.refuse();
    }
    let indices = if let Some(columns) = columns {
        result.notes.push(
            "Read the columns this curve was stored with rather than detecting them again."
                .to_owned(),
        );
        columns
    } else if let Some(header) = &header {
        let Some(indices) = column_indices(header, &mut result.errors) else {
            return result.refuse();
        };
        result.notes.push(format!(
            "Read columns by name: calendar age from '{}', radiocarbon age from '{}', error from '{}'.",
            header[indices[0]], header[indices[1]], header[indices[2]]
        ));
        indices
    } else {
        result.notes.push(
            "No header row, so the first three columns were read as calendar age, radiocarbon age and 1-sigma error -- the .14c layout.".to_owned(),
        );
        [0, 1, 2]
    };
    result.columns = Some(indices);

    let widest = *indices.iter().max().unwrap();
    let first_line = if header.is_some() { 2 } else { 1 };
    let mut parsed: Vec<(f64, f64, f64)> = Vec::new();
    for (offset, row) in rows.iter().enumerate() {
        let line = first_line + offset;
        if result.errors.len() >= MAX_REPORTED_ERRORS {
            result.errors.push(format!(
                "Stopped after {MAX_REPORTED_ERRORS} problems; the file's layout is probably not what was expected."
            ));
            return result.refuse();
        }
        if widest >= row.len() {
            result.errors.push(format!(
                "Row {line}: has {} column(s), needs at least {}.",
                row.len(),
                widest + 1
            ));
            continue;
        }
        let mut values = [0.0; 3];
        let mut bad = false;
        for (k, field) in ["cal bp", "c14 age", "error"].iter().enumerate() {
            let cell = python::strip(&row[indices[k]]);
            match python::float(cell) {
                Some(v) => values[k] = v,
                None => {
                    result
                        .errors
                        .push(format!("Row {line}: {field} is not a number ('{cell}')."));
                    bad = true;
                    break;
                }
            }
        }
        if bad {
            continue;
        }
        let [mut cal, c14, sigma] = values;
        if calendar == Calendar::BcAd {
            // No year zero: AD 1 is 1949 cal BP, 1 BC is 1950 cal BP.
            cal = if cal > 0.0 {
                1950.0 - cal
            } else {
                1950.0 - (cal + 1.0)
            };
        }
        if !(cal.is_finite() && c14.is_finite() && sigma.is_finite()) {
            result
                .errors
                .push(format!("Row {line}: contains a non-finite value."));
            continue;
        }
        if sigma <= 0.0 {
            result.errors.push(format!(
                "Row {line}: 1-sigma error is {}. A curve's error must be positive -- it goes into the quadrature sum with the measurement's own error.",
                g(sigma, 6)
            ));
            continue;
        }
        if cal < 0.0 {
            result.errors.push(format!(
                "Row {line}: calendar age {} is negative (after 1950 AD). Post-bomb curves are not supported.",
                g(cal, 6)
            ));
            continue;
        }
        parsed.push((cal, c14, sigma));
    }
    if !result.errors.is_empty() {
        return result.refuse();
    }
    if parsed.len() < 2 {
        result.errors.push(format!(
            "Only {} usable row(s). A calibration curve needs at least two points to interpolate between.",
            parsed.len()
        ));
        return result.refuse();
    }
    let largest = parsed
        .iter()
        .map(|p| p.1.abs())
        .fold(f64::NEG_INFINITY, f64::max);
    if largest < F14C_CEILING {
        let top = parsed.iter().map(|p| p.1).fold(f64::NEG_INFINITY, f64::max);
        result.errors.push(format!(
            "The radiocarbon age column tops out at {}, which looks like F14C (fraction modern) rather than a conventional radiocarbon age in years. Calibrating those values as ages would date every sample to within a century of the present.",
            g(top, 6)
        ));
        return result.refuse();
    }
    let ascending = parsed.windows(2).all(|w| w[0].0 < w[1].0);
    if !ascending {
        let descending = parsed.windows(2).all(|w| w[0].0 > w[1].0);
        parsed.sort_by(|a, b| a.0.partial_cmp(&b.0).expect("finite by now"));
        result.notes.push(
            if descending {
                "The file is written oldest-first, as the IntCal files are; sorted youngest-first for storage."
            } else {
                "Rows were not in calendar order; sorted."
            }
            .to_owned(),
        );
    }
    let duplicates: Vec<f64> = parsed
        .windows(2)
        .filter(|w| w[0].0 == w[1].0)
        .map(|w| w[0].0)
        .collect();
    if !duplicates.is_empty() {
        let shown: Vec<String> = duplicates.iter().take(5).map(|d| g(*d, 6)).collect();
        result.errors.push(format!(
            "{} duplicated calendar age(s) ({}{}). A curve must give one radiocarbon age per calendar year; two rows for the same year leave it ambiguous which one calibration should use.",
            duplicates.len(),
            shown.join(", "),
            if duplicates.len() > 5 { ", ..." } else { "" }
        ));
        return result.refuse();
    }
    result.points = parsed;
    result
}

static PATH_SEPARATOR: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[\\/]").unwrap());

/// Decode an uploaded curve file and read it, the format decided by content
/// rather than extension.
pub fn parse_upload(filename: &str, content: &[u8], calendar: Calendar) -> CurveImportResult {
    let mut result = parse_curve(&decode(content), calendar, None);
    if result.suggested_name.is_none() && !filename.is_empty() {
        let last = PATH_SEPARATOR.split(filename).last().unwrap_or("");
        let stem = last.rsplit_once('.').map_or(last, |(stem, _)| stem);
        result.suggested_name = (!stem.is_empty()).then(|| stem.to_owned());
    }
    result
}

/// A curve in the .14c format the bundled curves use.
pub fn to_14c_text(points: &[(f64, f64, f64)], header: &str) -> String {
    let mut lines: Vec<String> = python::splitlines(header)
        .into_iter()
        .filter(|l| !l.is_empty())
        .map(|l| format!("# {l}"))
        .collect();
    lines.push("# CAL BP, 14C age, Sigma".to_owned());
    lines.extend(
        points
            .iter()
            .map(|(cal, c14, sigma)| format!("{},{},{}", g(*cal, 6), g(*c14, 6), g(*sigma, 6))),
    );
    format!("{}\n", lines.join("\n"))
}
