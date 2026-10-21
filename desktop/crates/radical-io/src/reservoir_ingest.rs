//! Turning a fetch of the CHRONO/calib.org marine reservoir database into a
//! snapshot this app can serve: the parsing half of
//! `services/reservoir_ingest.py`. The fetch itself and the store are the
//! app's (Phase 5); nothing here knows a network exists.
//!
//! Two stages carry the care, because both have a failure mode that looks
//! like success. [`extract_records`] checks the response's header row
//! against [`EXPECTED_COLUMNS`] and refuses anything else, since a source
//! that reordered its columns read positionally would store latitude as
//! longitude. [`decode_field`] works per FIELD, since one response carries
//! both correct UTF-8 and raw cp1252, and either codec applied to the whole
//! document silently mangles the other kind.

use std::sync::LazyLock;

use radical_core::pyfmt::{g, repr};
use radical_core::reservoir::{is_recognised_feeding, normalise_feeding};
use regex::bytes::Regex as BytesRegex;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

use crate::date_import::csv_row;
use crate::pycsv;
use crate::python;

/// The columns the ingest's query asks for, in order; the response's
/// header row must be exactly these.
pub const EXPECTED_COLUMNS: [&str; 18] = [
    "mapno",
    "lon",
    "lat",
    "deltar",
    "deltarerr",
    "reference",
    "locality",
    "subregion",
    "notes",
    "collectionyear",
    "reservoirage",
    "c14age",
    "c14err",
    "labid",
    "last_changed",
    "genus",
    "species",
    "feeding",
];

/// The fields a record must carry for the row to mean anything.
pub const REQUIRED_FIELDS: [&str; 3] = ["mapno", "lat", "lon"];

/// The fetch produced something that is not this database.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IngestError(pub String);

/// One normalised measurement, ready to store or to hash.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ReservoirRow {
    pub map_no: i64,
    pub latitude: f64,
    pub longitude: f64,
    pub delta_r: Option<f64>,
    pub delta_r_error: Option<f64>,
    pub reference: Option<String>,
    pub locality: Option<String>,
    pub subregion: Option<String>,
    pub notes: Option<String>,
    pub c14_age: Option<f64>,
    pub c14_error: Option<f64>,
    pub lab_id: Option<String>,
    pub collection_year: Option<i64>,
    pub reservoir_age: Option<f64>,
    pub genus: Option<String>,
    pub species: Option<String>,
    pub feeding: Option<String>,
    pub feeding_raw: Option<String>,
}

impl ReservoirRow {
    /// `dataclasses.asdict(row)`.
    pub fn to_json(&self) -> Value {
        json!({
            "map_no": self.map_no, "latitude": self.latitude, "longitude": self.longitude,
            "delta_r": self.delta_r, "delta_r_error": self.delta_r_error,
            "reference": self.reference, "locality": self.locality, "subregion": self.subregion,
            "notes": self.notes, "c14_age": self.c14_age, "c14_error": self.c14_error,
            "lab_id": self.lab_id, "collection_year": self.collection_year,
            "reservoir_age": self.reservoir_age, "genus": self.genus, "species": self.species,
            "feeding": self.feeding, "feeding_raw": self.feeding_raw,
        })
    }

    /// The row's columns as `rows_to_csv` writes them: `str()` of each
    /// value, nothing for None.
    fn csv_cells(&self) -> [String; 18] {
        let num = |v: Option<f64>| v.map(repr).unwrap_or_default();
        let text = |v: &Option<String>| v.clone().unwrap_or_default();
        [
            self.map_no.to_string(),
            repr(self.latitude),
            repr(self.longitude),
            num(self.delta_r),
            num(self.delta_r_error),
            text(&self.reference),
            text(&self.locality),
            text(&self.subregion),
            text(&self.notes),
            num(self.c14_age),
            num(self.c14_error),
            text(&self.lab_id),
            self.collection_year
                .map(|y| y.to_string())
                .unwrap_or_default(),
            num(self.reservoir_age),
            text(&self.genus),
            text(&self.species),
            text(&self.feeding),
            text(&self.feeding_raw),
        ]
    }
}

/// A `YYYY-MM-DD HH:MM:SS` stamp, ordered as time is.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct Timestamp {
    pub year: u32,
    pub month: u32,
    pub day: u32,
    pub hour: u32,
    pub minute: u32,
    pub second: u32,
}

impl Timestamp {
    /// Python's `datetime.isoformat()`.
    pub fn isoformat(&self) -> String {
        format!(
            "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
            self.year, self.month, self.day, self.hour, self.minute, self.second
        )
    }

    pub fn date(&self) -> String {
        format!("{:04}-{:02}-{:02}", self.year, self.month, self.day)
    }
}

/// What the fetch found, in the terms a person needs to judge it.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct IngestReport {
    pub row_count: usize,
    pub source_last_changed: Option<Timestamp>,
    pub content_sha256: String,
    /// Rows carrying a Delta-R but no uncertainty on it.
    pub without_error: usize,
    /// Fields that failed a strict UTF-8 decode and were read as cp1252.
    pub cp1252_fallbacks: usize,
    /// Fields that failed both, kept as escaped text.
    pub undecodable_fields: usize,
    /// Rows whose longitude was outside +-180 and was wrapped.
    pub wrapped_longitudes: usize,
    /// Feeding spellings never seen before, with how many rows carried each.
    pub unrecognised_feeding: Vec<(String, usize)>,
    /// Every problem found. Non-empty means nothing may be written.
    pub errors: Vec<String>,
}

impl IngestReport {
    pub fn ok(&self) -> bool {
        self.errors.is_empty()
    }

    pub fn to_json(&self) -> Value {
        json!({
            "ok": self.ok(),
            "row_count": self.row_count,
            "source_last_changed": self.source_last_changed.map(|t| t.isoformat()),
            "content_sha256": self.content_sha256,
            "without_error": self.without_error,
            "cp1252_fallbacks": self.cp1252_fallbacks,
            "undecodable_fields": self.undecodable_fields,
            "wrapped_longitudes": self.wrapped_longitudes,
            "unrecognised_feeding": self.unrecognised_feeding.iter()
                .map(|(k, n)| (k.clone(), Value::from(*n))).collect::<Map<_, _>>(),
            "errors": self.errors,
        })
    }
}

// --------------------------------------------------------------------------
// Decoding
// --------------------------------------------------------------------------

/// One field's value as the source gave it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Field {
    Null,
    /// Already text: taken as it is.
    Text(String),
    /// Undecoded bytes from the response.
    Bytes(Vec<u8>),
}

/// One field's text, and how it had to be read: 'utf-8', 'cp1252',
/// 'escaped' (valid in neither, kept as Python's bytes repr rather than
/// replaced), or 'text' for a value that arrived already decoded.
pub fn decode_field(raw: &Field) -> (Option<String>, &'static str) {
    match raw {
        Field::Null => (None, "text"),
        Field::Text(s) => (Some(s.clone()), "text"),
        Field::Bytes(data) => {
            if let Ok(s) = std::str::from_utf8(data) {
                return (Some(s.to_owned()), "utf-8");
            }
            if let Some(s) = python::decode_cp1252(data) {
                return (Some(s), "cp1252");
            }
            (Some(python::repr_bytes_inner(data)), "escaped")
        }
    }
}

// Matched on BYTES: the document is not valid in any single encoding, so it
// cannot be handed to a parser that wants text. ASCII classes throughout, as
// Python's bytes patterns have.
static TABLE_START: LazyLock<BytesRegex> =
    LazyLock::new(|| BytesRegex::new(r"(?i-u)<table\s+border\s*>").unwrap());
static ROW: LazyLock<BytesRegex> =
    LazyLock::new(|| BytesRegex::new(r"(?is-u)<tr>\s*(<td>.*?</td>)\s*</tr>").unwrap());
static CELL: LazyLock<BytesRegex> =
    LazyLock::new(|| BytesRegex::new(r"(?is-u)<td>(.*?)</td>").unwrap());
static DECLARED_COUNT: LazyLock<BytesRegex> =
    LazyLock::new(|| BytesRegex::new(r"(?i-u)(\d+)\s+records").unwrap());
static AGGREGATE_ROW: LazyLock<BytesRegex> = LazyLock::new(|| {
    BytesRegex::new(r"(?is-u)<tr>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*</tr>").unwrap()
});

/// Python's `bytes.strip()`.
fn strip_bytes(data: &[u8]) -> &[u8] {
    let space = |b: &u8| matches!(b, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c);
    let start = data.iter().position(|b| !space(b)).unwrap_or(data.len());
    let end = data
        .iter()
        .rposition(|b| !space(b))
        .map_or(start, |e| e + 1);
    &data[start..end]
}

/// One record: the query's column names to undecoded fields.
pub type Record = Vec<(String, Field)>;

/// The result table out of one query response, as undecoded bytes per
/// field, and the record count the page declared.
pub fn extract_records(raw: &[u8]) -> Result<(Vec<Record>, Option<u64>), IngestError> {
    let Some(start) = TABLE_START.find(raw) else {
        return Err(IngestError(
            "The response has no result table in it. The query interface may have changed, or the query may have been rejected.".to_owned(),
        ));
    };
    let declared = DECLARED_COUNT
        .captures(&raw[..start.start()])
        .and_then(|c| std::str::from_utf8(&c[1]).ok()?.parse::<u64>().ok());
    let body = &raw[start.end()..];
    let rows: Vec<Vec<&[u8]>> = ROW
        .captures_iter(body)
        .map(|m| {
            CELL.captures_iter(m.get(1).unwrap().as_bytes())
                .map(|c| c.get(1).unwrap().as_bytes())
                .collect()
        })
        .collect();
    if rows.is_empty() {
        return Err(IngestError(
            "The response has a result table with no rows in it.".to_owned(),
        ));
    }
    let header: Vec<String> = rows[0]
        .iter()
        .map(|c| {
            strip_bytes(c)
                .iter()
                .map(|b| {
                    if b.is_ascii() {
                        char::from(b.to_ascii_lowercase())
                    } else {
                        '\u{fffd}'
                    }
                })
                .collect()
        })
        .collect();
    if header != EXPECTED_COLUMNS {
        return Err(IngestError(format!(
            "The result table does not have the columns this ingest asks for. Found {}; wanted {}. The source has changed shape and the parser must be updated rather than the rows read positionally.",
            python::repr_list(&header),
            python::repr_list(&EXPECTED_COLUMNS)
        )));
    }
    let mut records = Vec::new();
    for (index, cells) in rows.iter().enumerate().skip(1) {
        if cells.len() != EXPECTED_COLUMNS.len() {
            return Err(IngestError(format!(
                "Row {index} of the result table has {} cells, not {}. Refusing to read it positionally.",
                cells.len(),
                EXPECTED_COLUMNS.len()
            )));
        }
        records.push(
            EXPECTED_COLUMNS
                .iter()
                .zip(cells)
                .map(|(k, v)| ((*k).to_owned(), Field::Bytes(v.to_vec())))
                .collect(),
        );
    }
    Ok((records, declared))
}

/// What the source says it holds now.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Freshness {
    pub record_count: i64,
    pub source_last_changed: Option<Timestamp>,
}

impl Freshness {
    pub fn to_json(&self) -> Value {
        json!({
            "record_count": self.record_count,
            "source_last_changed": self.source_last_changed.map(|t| t.isoformat()),
        })
    }
}

/// The row count and last-changed stamp out of a probe response, whose one
/// aggregate row comes back transposed: one `<tr>` per column.
pub fn extract_freshness(raw: &[u8]) -> Result<Freshness, IngestError> {
    let Some(start) = TABLE_START.find(raw) else {
        return Err(IngestError(
            "The freshness probe returned no result table. The query interface may have changed, or the query may have been rejected.".to_owned(),
        ));
    };
    let mut values: Vec<(String, String)> = Vec::new();
    for caps in AGGREGATE_ROW.captures_iter(&raw[start.end()..]) {
        let (label, _) = decode_field(&Field::Bytes(caps[1].to_vec()));
        let (value, _) = decode_field(&Field::Bytes(caps[2].to_vec()));
        let Some(label) = label else { continue };
        let key = python::strip(&label).to_lowercase();
        let value = python::strip(value.as_deref().unwrap_or("")).to_owned();
        match values.iter_mut().find(|(k, _)| *k == key) {
            Some(slot) => slot.1 = value,
            None => values.push((key, value)),
        }
    }
    let get = |key: &str| {
        values
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v.as_str())
    };
    let Some(count_text) = get("count(*)") else {
        let keys: Vec<&str> = values.iter().map(|(k, _)| k.as_str()).collect();
        return Err(IngestError(format!(
            "The freshness probe did not report a row count. Got {}.",
            python::repr_list(&keys)
        )));
    };
    let count = python::int(count_text).map_err(|_| {
        IngestError(format!(
            "The freshness probe reported a row count of {}, which is not a number.",
            python::repr_str(count_text)
        ))
    })?;
    Ok(Freshness {
        record_count: count,
        source_last_changed: parse_timestamp(get("max(last_changed)")),
    })
}

/// A numeric field, or None: the source writes an absent value as empty,
/// 'NULL' or a dash, and none of them is a zero.
fn number(value: Option<&str>) -> Option<f64> {
    let text = python::strip(value?);
    if text.is_empty() || text.to_uppercase() == "NULL" || text == "-" || text == "--" {
        return None;
    }
    python::float(text)
}

/// A longitude outside +-180 brought back onto the earth. Longitude is
/// modular, so there is exactly one answer; latitude has no such identity.
pub fn wrap_longitude(value: f64) -> f64 {
    if (-180.0..=180.0).contains(&value) {
        return value;
    }
    (value + 180.0).rem_euclid(360.0) - 180.0
}

static STAMP: LazyLock<regex::Regex> = LazyLock::new(|| {
    // `datetime.strptime`'s own patterns for %Y-%m-%d and %H:%M:%S, its
    // space matching any run of whitespace. Matched from the start, the
    // first alternative that fits taken, as Python's engine takes it.
    regex::Regex::new(
        r"(?i)^([0-9]{4})-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12][0-9]|0[1-9]|[1-9]| [1-9])(?:[\s\x1c-\x1f]+(2[0-3]|[0-1][0-9]|[0-9]):([0-5][0-9]|[0-9]):(6[0-1]|[0-5][0-9]|[0-9]))?",
    )
    .unwrap()
});

fn days_in_month(year: u32, month: u32) -> u32 {
    match month {
        2 if (year.is_multiple_of(4) && !year.is_multiple_of(100)) || year.is_multiple_of(400) => {
            29
        }
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    }
}

/// A 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD' stamp, or None for anything
/// else: a row with an odd one is still a good measurement.
fn parse_timestamp(value: Option<&str>) -> Option<Timestamp> {
    let text = text(value)?;
    let caps = STAMP.captures(&text)?;
    if caps.get(0).unwrap().end() != text.len() {
        return None;
    }
    let field = |i: usize| {
        caps.get(i)
            .map_or(0, |m| m.as_str().trim().parse::<u32>().unwrap())
    };
    let stamp = Timestamp {
        year: field(1),
        month: field(2),
        day: field(3),
        hour: field(4),
        minute: field(5),
        second: field(6),
    };
    // datetime's own range checks.
    let valid = stamp.year >= 1
        && stamp.day <= days_in_month(stamp.year, stamp.month)
        && stamp.second <= 59;
    valid.then_some(stamp)
}

/// A text field, or None for empty or 'NULL'.
fn text(value: Option<&str>) -> Option<String> {
    let text = python::strip(value?);
    (!text.is_empty() && text.to_uppercase() != "NULL").then(|| text.to_owned())
}

// --------------------------------------------------------------------------
// Normalisation
// --------------------------------------------------------------------------

/// Raw records into normalised rows, with the report describing them. Every
/// problem is collected; a caller writes nothing when the report has errors.
pub fn records_to_rows(
    records: &[Record],
    declared: Option<u64>,
) -> (Vec<ReservoirRow>, IngestReport) {
    let mut report = IngestReport::default();
    let mut rows = Vec::new();
    let mut seen: Vec<(i64, usize)> = Vec::new();
    for (index, record) in records.iter().enumerate().map(|(i, r)| (i + 1, r)) {
        let mut decoded: Vec<(&str, Option<String>)> = Vec::new();
        for (key, value) in record {
            let (text, how) = decode_field(value);
            match how {
                "cp1252" => report.cp1252_fallbacks += 1,
                "escaped" => report.undecodable_fields += 1,
                _ => {}
            }
            // AFTER the decode: unescaping first would mean having already
            // chosen a codec.
            decoded.push((key, text.map(|t| python::html_unescape(&t))));
        }
        let get = |key: &str| {
            decoded
                .iter()
                .rev()
                .find(|(k, _)| *k == key)
                .and_then(|(_, v)| v.as_deref())
        };

        let missing: Vec<&str> = REQUIRED_FIELDS
            .iter()
            .copied()
            .filter(|f| text(get(f)).is_none())
            .collect();
        if !missing.is_empty() {
            report.errors.push(format!(
                "Row {index}: {} missing -- a reservoir point needs a map number and a position",
                missing.join(", ")
            ));
            continue;
        }
        let (Some(map_no), Some(latitude), Some(mut longitude)) =
            (number(get("mapno")), number(get("lat")), number(get("lon")))
        else {
            report.errors.push(format!(
                "Row {index}: map number and position must be numbers, got mapno={}, lat={}, lon={}",
                python::repr_opt(get("mapno")),
                python::repr_opt(get("lat")),
                python::repr_opt(get("lon"))
            ));
            continue;
        };
        if !(-90.0..=90.0).contains(&latitude) {
            report.errors.push(format!(
                "Row {index} (map no {}): latitude {} is not a latitude",
                map_no.trunc() as i64,
                repr(latitude)
            ));
            continue;
        }
        if !(-180.0..=180.0).contains(&longitude) {
            longitude = wrap_longitude(longitude);
            report.wrapped_longitudes += 1;
        }
        let key = map_no.trunc() as i64;
        if let Some((_, first)) = seen.iter().find(|(k, _)| *k == key) {
            report.errors.push(format!(
                "Row {index}: map number {key} already appeared at row {first} -- it identifies a point and cannot repeat"
            ));
            continue;
        }
        seen.push((key, index));

        let feeding_raw = text(get("feeding"));
        if !is_recognised_feeding(feeding_raw.as_deref()) {
            let spelling = python::strip(feeding_raw.as_deref().unwrap_or("")).to_owned();
            match report
                .unrecognised_feeding
                .iter_mut()
                .find(|(s, _)| *s == spelling)
            {
                Some(slot) => slot.1 += 1,
                None => report.unrecognised_feeding.push((spelling, 1)),
            }
        }
        let delta_r = number(get("deltar"));
        let delta_r_error = number(get("deltarerr"));
        if delta_r.is_some() && delta_r_error.is_none() {
            report.without_error += 1;
        }
        if let Some(changed) = parse_timestamp(get("last_changed"))
            && report
                .source_last_changed
                .is_none_or(|current| changed > current)
        {
            report.source_last_changed = Some(changed);
        }
        rows.push(ReservoirRow {
            map_no: key,
            latitude,
            longitude,
            delta_r,
            delta_r_error,
            reference: text(get("reference")),
            locality: text(get("locality")),
            subregion: text(get("subregion")),
            notes: text(get("notes")),
            c14_age: number(get("c14age")),
            c14_error: number(get("c14err")),
            lab_id: text(get("labid")),
            // 'inf' and 'nan' parse as numbers and are not years. Descriptive
            // metadata, like last_changed: the measurement stands without it.
            collection_year: number(get("collectionyear"))
                .filter(|y| y.is_finite())
                .map(|y| y.trunc() as i64),
            reservoir_age: number(get("reservoirage")),
            genus: text(get("genus")),
            species: text(get("species")),
            feeding: Some(normalise_feeding(feeding_raw.as_deref()).to_owned()),
            feeding_raw,
        });
    }
    report.row_count = rows.len();
    if rows.is_empty() && report.errors.is_empty() {
        report
            .errors
            .push("The fetch returned no rows at all.".to_owned());
    }
    if let Some(declared) = declared
        && report.ok()
        && rows.len() as u64 != declared
    {
        report.errors.push(format!(
            "The source said {declared} records and {} were read. Refusing to store a partial mirror.",
            rows.len()
        ));
    }
    (rows, report)
}

// --------------------------------------------------------------------------
// Hashing and the bundled file
// --------------------------------------------------------------------------

fn by_map_no(rows: &[ReservoirRow]) -> Vec<&ReservoirRow> {
    let mut sorted: Vec<&ReservoirRow> = rows.iter().collect();
    sorted.sort_by_key(|r| r.map_no);
    sorted
}

/// sha256 over the NUMBERS, sorted by map number and formatted `%.6g`, so a
/// reshuffle or a float read differently by two parsers does not read as a
/// changed measurement.
pub fn content_hash(rows: &[ReservoirRow]) -> String {
    let mut text = String::new();
    for row in by_map_no(rows) {
        let opt = |v: Option<f64>| v.map(|x| g(x, 6)).unwrap_or_default();
        text.push_str(&format!(
            "{}|{}|{}|{}|{}|{}\n",
            row.map_no,
            g(row.latitude, 6),
            g(row.longitude, 6),
            opt(row.delta_r),
            opt(row.delta_r_error),
            row.feeding.as_deref().unwrap_or("")
        ));
    }
    Sha256::digest(text.as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// The snapshot's id: its source and the source's OWN last-changed date.
pub fn snapshot_id(source_slug: &str, last_changed: &Timestamp) -> String {
    format!("{source_slug}-{}", last_changed.date())
}

/// The columns of a bundled snapshot file.
pub const CSV_COLUMNS: [&str; 18] = [
    "map_no",
    "latitude",
    "longitude",
    "delta_r",
    "delta_r_error",
    "reference",
    "locality",
    "subregion",
    "notes",
    "c14_age",
    "c14_error",
    "lab_id",
    "collection_year",
    "reservoir_age",
    "genus",
    "species",
    "feeding",
    "feeding_raw",
];

/// The normalised rows as the file that ships, sorted by map number as the
/// hash sorts them.
pub fn rows_to_csv(rows: &[ReservoirRow]) -> String {
    let mut out = csv_row(&CSV_COLUMNS);
    for row in by_map_no(rows) {
        let cells = row.csv_cells();
        let refs: Vec<&str> = cells.iter().map(String::as_str).collect();
        out.push_str(&csv_row(&refs));
    }
    out
}

/// A bundled snapshot read back, strict about its header: a file whose
/// columns are not these is not this format.
pub fn rows_from_csv(text_in: &str) -> Result<Vec<ReservoirRow>, IngestError> {
    let records = pycsv::records(text_in, ',').map_err(|e| IngestError(e.message))?;
    let mut records = records.into_iter();
    let Some(header) = records.next() else {
        return Err(IngestError(
            "The bundled snapshot file is empty.".to_owned(),
        ));
    };
    if header
        .iter()
        .map(|h| python::strip(h))
        .ne(CSV_COLUMNS.iter().copied())
    {
        return Err(IngestError(format!(
            "The bundled snapshot does not have the expected columns. Found {}; wanted {}.",
            python::repr_list(&header),
            python::repr_list(&CSV_COLUMNS)
        )));
    }
    let mut rows = Vec::new();
    for (offset, values) in records.enumerate() {
        let line = offset + 2;
        if !values.iter().any(|v| !python::strip(v).is_empty()) {
            continue;
        }
        if values.len() != CSV_COLUMNS.len() {
            return Err(IngestError(format!(
                "Row {line} of the bundled snapshot has {} values, not {}.",
                values.len(),
                CSV_COLUMNS.len()
            )));
        }
        let at = |name: &str| values[CSV_COLUMNS.iter().position(|c| *c == name).unwrap()].as_str();
        let read = || -> Result<ReservoirRow, String> {
            let map_no = python::int(at("map_no"))?;
            let latitude = python::float_or_error(at("latitude"))?;
            let longitude = python::float_or_error(at("longitude"))?;
            let year = at("collection_year");
            let collection_year = if python::strip(year).is_empty() {
                None
            } else {
                let y = python::float_or_error(year)?;
                if y.is_nan() {
                    return Err("cannot convert float NaN to integer".to_owned());
                }
                if y.is_infinite() {
                    // The Python raises OverflowError here, which nothing
                    // catches; a refusal names it instead.
                    return Err("cannot convert float infinity to integer".to_owned());
                }
                Some(y.trunc() as i64)
            };
            Ok(ReservoirRow {
                map_no,
                latitude,
                longitude,
                delta_r: number(Some(at("delta_r"))),
                delta_r_error: number(Some(at("delta_r_error"))),
                reference: text(Some(at("reference"))),
                locality: text(Some(at("locality"))),
                subregion: text(Some(at("subregion"))),
                notes: text(Some(at("notes"))),
                c14_age: number(Some(at("c14_age"))),
                c14_error: number(Some(at("c14_error"))),
                lab_id: text(Some(at("lab_id"))),
                collection_year,
                reservoir_age: number(Some(at("reservoir_age"))),
                genus: text(Some(at("genus"))),
                species: text(Some(at("species"))),
                feeding: text(Some(at("feeding"))),
                feeding_raw: text(Some(at("feeding_raw"))),
            })
        };
        rows.push(read().map_err(|exc| {
            IngestError(format!(
                "Row {line} of the bundled snapshot could not be read: {exc}"
            ))
        })?);
    }
    if rows.is_empty() {
        return Err(IngestError(
            "The bundled snapshot has a header and no rows.".to_owned(),
        ));
    }
    Ok(rows)
}

// --------------------------------------------------------------------------
// The registry's check (`reservoir_registry.resolve`)
// --------------------------------------------------------------------------

/// A snapshot's rows, refused unless they hash to what the snapshot was
/// stored with: quietly different numbers are worse than none.
pub fn verify(
    snapshot_id: &str,
    rows: Vec<ReservoirRow>,
    content_sha256: &str,
) -> Result<Vec<ReservoirRow>, IngestError> {
    if content_hash(&rows) != content_sha256 {
        return Err(IngestError(format!(
            "Marine reservoir snapshot '{snapshot_id}' does not match the data it was stored with, so it will not be used. Any ΔR derived from it cannot be reproduced. Re-ingest the database as a new snapshot rather than trusting this copy."
        )));
    }
    Ok(rows)
}
