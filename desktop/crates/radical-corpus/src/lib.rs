//! The golden corpus: what the earlier Python server answered, recorded by
//! `desktop/corpus/generate.py` (in the repository at tag `v2.1.0`), for the
//! Rust code to be held to.
//!
//! This crate only reads it. The parts of the format a reader has to know:
//!
//! - Each family file is `{"schema": 2, "family": ..., "cases": [...]}`, and a
//!   case carries `name`, `inputs`, and either `outputs` or `error` -- a
//!   refusal is an answer too.
//! - JSON has no NaN or infinity, so a non-finite float is the string `"NaN"`,
//!   `"Infinity"` or `"-Infinity"`. [`as_f64`] reads both forms.
//! - A distribution over a whole curve is stored as the [`Window`] that carries
//!   its mass.
//! - Lists of `[x, y]` pairs are stored as `{"$series": ...}`, and a saved
//!   run's POST results as `{"$from_analysis": ...}` pointing at the GET copy.
//!   [`Case::expanded_outputs`] undoes both, giving back the JSON the Python
//!   code produced.
//! - `manifest.json` lists every file with its SHA-256, and the SHA-256 of the
//!   curve files the corpus was generated against, so a test can tell a
//!   corpus that no longer matches the curves the binary embeds.

use std::collections::BTreeMap;
use std::fmt;
use std::fs;
use std::path::{Path, PathBuf};

use serde::Deserialize;
use serde_json::Value;
use sha2::{Digest, Sha256};

/// The corpus format this crate reads.
pub const SCHEMA: u32 = 2;

#[derive(Debug)]
pub enum Error {
    Io(PathBuf, std::io::Error),
    Json(PathBuf, serde_json::Error),
    Schema(PathBuf, u32),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(path, e) => write!(f, "{}: {e}", path.display()),
            Self::Json(path, e) => write!(f, "{}: {e}", path.display()),
            Self::Schema(path, found) => write!(
                f,
                "{}: corpus schema {found}, this reader understands {SCHEMA}",
                path.display()
            ),
        }
    }
}

impl std::error::Error for Error {}

pub type Result<T> = std::result::Result<T, Error>;

/// The repository root, found from this crate's own location.
pub fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

/// `desktop/corpus`, or `RADICAL_CORPUS` when set.
pub fn corpus_dir() -> PathBuf {
    std::env::var_os("RADICAL_CORPUS")
        .map(PathBuf::from)
        .unwrap_or_else(|| Path::new(env!("CARGO_MANIFEST_DIR")).join("../../corpus"))
}

#[derive(Debug, Deserialize)]
pub struct Manifest {
    pub schema: u32,
    pub seed: u64,
    pub generated_from: BTreeMap<String, Value>,
    pub curves: BTreeMap<String, CurveEntry>,
    /// Path relative to the corpus directory -> SHA-256 of the file.
    pub files: BTreeMap<String, String>,
}

#[derive(Debug, Deserialize)]
pub struct CurveEntry {
    /// Relative to the repository root.
    pub file: String,
    pub sha256: String,
}

#[derive(Debug, Deserialize)]
pub struct Family {
    pub schema: u32,
    pub family: String,
    pub cases: Vec<Case>,
}

#[derive(Debug, Deserialize)]
pub struct Case {
    pub name: String,
    pub inputs: Value,
    /// `Some(Value::Null)` when the function answered None -- one date has no
    /// same-event test -- which is an answer, unlike an absent `outputs`.
    #[serde(default, deserialize_with = "present")]
    pub outputs: Option<Value>,
    #[serde(default, deserialize_with = "present")]
    pub error: Option<Value>,
}

impl Case {
    /// `inputs` with every `$series` expanded.
    pub fn expanded_inputs(&self) -> Value {
        expand_series(&self.inputs)
    }

    /// `outputs` as the Python code produced them: every `$series` expanded,
    /// and a `$from_analysis` filled in from the case's own GET analysis.
    pub fn expanded_outputs(&self) -> Option<Value> {
        let outputs = expand_series(self.outputs.as_ref()?);
        let analysis = outputs.pointer("/analysis/data/results").cloned();
        Some(fill_from_analysis(outputs, analysis.as_ref()))
    }
}

/// `numpy.linspace(start, stop, n)`, bit for bit: `i * step + start` with
/// `step = (stop - start) / (n - 1)`, and the last value forced to `stop`.
pub fn linspace(start: f64, stop: f64, n: usize) -> Vec<f64> {
    match n {
        0 => Vec::new(),
        1 => vec![start],
        _ => {
            let step = (stop - start) / (n - 1) as f64;
            let mut out: Vec<f64> = (0..n).map(|i| i as f64 * step + start).collect();
            out[n - 1] = stop;
            out
        }
    }
}

/// Every `{"$series": {"x", "y"}}` in `value` back to its list of pairs.
pub fn expand_series(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            if let (1, Some(series)) = (map.len(), map.get("$series")) {
                return series_pairs(series);
            }
            Value::Object(
                map.iter()
                    .map(|(k, v)| (k.clone(), expand_series(v)))
                    .collect(),
            )
        }
        Value::Array(items) => Value::Array(items.iter().map(expand_series).collect()),
        other => other.clone(),
    }
}

fn series_pairs(series: &Value) -> Value {
    let ys = series["y"].as_array().expect("$series without y");
    let xs: Vec<Value> = match &series["x"] {
        Value::Array(xs) => xs.clone(),
        grid => {
            let spec = grid["linspace"]
                .as_array()
                .expect("$series x is neither a list nor a linspace");
            let (start, stop) = (spec[0].as_f64().unwrap(), spec[1].as_f64().unwrap());
            let n = spec[2].as_u64().unwrap() as usize;
            linspace(start, stop, n)
                .into_iter()
                .map(Value::from)
                .collect()
        }
    };
    assert_eq!(xs.len(), ys.len(), "$series x and y differ in length");
    Value::Array(
        xs.into_iter()
            .zip(ys.iter().cloned())
            .map(|(x, y)| Value::Array(vec![x, y]))
            .collect(),
    )
}

/// Every `{"$from_analysis": ...}` in `value` filled in from `analysis`, the
/// GET analysis `results` of the same case.
fn fill_from_analysis(value: Value, analysis: Option<&Value>) -> Value {
    match value {
        Value::Object(map) => {
            if let (1, Some(spec)) = (map.len(), map.get("$from_analysis")) {
                let entries = analysis
                    .and_then(Value::as_array)
                    .expect("$from_analysis without an analysis");
                let fields: Vec<&str> = spec["fields"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|f| f.as_str().unwrap())
                    .collect();
                let mut out = serde_json::Map::new();
                for pair in spec["ids"].as_array().unwrap() {
                    let (key, sample_id) = (pair[0].as_str().unwrap(), &pair[1]);
                    let entry = entries
                        .iter()
                        .find(|e| &e["sample_id"] == sample_id)
                        .expect("$from_analysis id not in the analysis");
                    let picked = fields
                        .iter()
                        .map(|f| ((*f).to_owned(), entry[*f].clone()))
                        .collect();
                    out.insert(key.to_owned(), Value::Object(picked));
                }
                return Value::Object(out);
            }
            Value::Object(
                map.into_iter()
                    .map(|(k, v)| (k, fill_from_analysis(v, analysis)))
                    .collect(),
            )
        }
        Value::Array(items) => Value::Array(
            items
                .into_iter()
                .map(|v| fill_from_analysis(v, analysis))
                .collect(),
        ),
        other => other,
    }
}

fn present<'de, D: serde::Deserializer<'de>>(d: D) -> std::result::Result<Option<Value>, D::Error> {
    Value::deserialize(d).map(Some)
}

/// The stretch of a curve-length distribution that carries its mass: entries
/// `index_from .. index_from + values.len()` of a `length`-long vector.
/// Everything outside held `mass_outside` in total, each entry below `floor`
/// times the peak.
#[derive(Debug, Deserialize)]
pub struct Window {
    pub length: usize,
    pub index_from: usize,
    #[serde(deserialize_with = "floats")]
    pub cal_ages: Vec<f64>,
    #[serde(deserialize_with = "floats")]
    pub values: Vec<f64>,
    pub mass_outside: f64,
    pub floor: f64,
}

impl Window {
    pub fn range(&self) -> std::ops::Range<usize> {
        self.index_from..self.index_from + self.values.len()
    }
}

fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T> {
    let text = fs::read_to_string(path).map_err(|e| Error::Io(path.to_owned(), e))?;
    serde_json::from_str(&text).map_err(|e| Error::Json(path.to_owned(), e))
}

pub fn load_manifest() -> Result<Manifest> {
    let path = corpus_dir().join("manifest.json");
    let manifest: Manifest = read_json(&path)?;
    if manifest.schema != SCHEMA {
        return Err(Error::Schema(path, manifest.schema));
    }
    Ok(manifest)
}

/// One family by name, as the manifest lists it without `data/` and `.json`:
/// `load_family("units/calibrate")`.
pub fn load_family(name: &str) -> Result<Family> {
    let path = corpus_dir().join("data").join(format!("{name}.json"));
    let family: Family = read_json(&path)?;
    if family.schema != SCHEMA {
        return Err(Error::Schema(path, family.schema));
    }
    Ok(family)
}

/// A corpus number: a JSON number, or one of the three names a non-finite
/// float is written as.
pub fn as_f64(value: &Value) -> Option<f64> {
    match value {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => match s.as_str() {
            "NaN" => Some(f64::NAN),
            "Infinity" => Some(f64::INFINITY),
            "-Infinity" => Some(f64::NEG_INFINITY),
            _ => None,
        },
        _ => None,
    }
}

/// A JSON array of corpus numbers, or None if anything in it is not one.
pub fn as_f64s(value: &Value) -> Option<Vec<f64>> {
    value.as_array()?.iter().map(as_f64).collect()
}

fn floats<'de, D: serde::Deserializer<'de>>(d: D) -> std::result::Result<Vec<f64>, D::Error> {
    let raw = Value::deserialize(d)?;
    as_f64s(&raw).ok_or_else(|| serde::de::Error::custom("expected an array of corpus numbers"))
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn reads_both_spellings_of_a_float() {
        assert_eq!(as_f64(&json!(1.5)), Some(1.5));
        assert_eq!(as_f64(&json!(-3)), Some(-3.0));
        assert!(as_f64(&json!("NaN")).unwrap().is_nan());
        assert_eq!(as_f64(&json!("Infinity")), Some(f64::INFINITY));
        assert_eq!(as_f64(&json!("-Infinity")), Some(f64::NEG_INFINITY));
        assert_eq!(as_f64(&json!("1.5")), None);
        assert_eq!(as_f64(&json!(null)), None);
    }

    #[test]
    fn an_array_with_a_stranger_in_it_is_not_numbers() {
        assert_eq!(
            as_f64s(&json!([1, 2.5, "Infinity"])),
            Some(vec![1.0, 2.5, f64::INFINITY])
        );
        assert_eq!(as_f64s(&json!([1, "x"])), None);
        assert_eq!(as_f64s(&json!({"a": 1})), None);
    }

    #[test]
    fn python_floats_round_trip_exactly() {
        // repr(0.1 + 0.2) in Python; float_roundtrip must read it back to the
        // same bits, or every tolerance below 1 ulp is meaningless.
        let v: Value = serde_json::from_str("0.30000000000000004").unwrap();
        assert_eq!(as_f64(&v).unwrap().to_bits(), (0.1_f64 + 0.2).to_bits());
    }

    #[test]
    fn a_null_answer_is_still_an_answer() {
        let case: Case =
            serde_json::from_str(r#"{"name": "n", "inputs": {}, "outputs": null}"#).unwrap();
        assert_eq!(case.outputs, Some(Value::Null));
        assert_eq!(case.error, None);
    }

    #[test]
    fn a_series_expands_to_the_pairs_it_stands_for() {
        let listed = json!({"$series": {"x": [1.0, 2.5], "y": [0.25, 0.75]}});
        assert_eq!(expand_series(&listed), json!([[1.0, 0.25], [2.5, 0.75]]));
        let grid = json!({"a": [{"$series": {"x": {"linspace": [0.0, 1.0, 3]}, "y": [1, 2, 3]}}]});
        assert_eq!(
            expand_series(&grid),
            json!({"a": [[[0.0, 1], [0.5, 2], [1.0, 3]]]})
        );
    }

    #[test]
    fn from_analysis_picks_the_named_fields_of_the_matching_entry() {
        let case: Case = serde_json::from_value(json!({
            "name": "n", "inputs": {},
            "outputs": {
                "response": {"data": {"results": {"$from_analysis": {
                    "ids": [["a", "<id:9>"]], "fields": ["mean"]}}}},
                "analysis": {"data": {"results": [
                    {"sample_id": "<id:8>", "mean": 1.0, "extra": 0},
                    {"sample_id": "<id:9>", "mean": 2.0, "extra": 0}]}}
            }
        }))
        .unwrap();
        let outputs = case.expanded_outputs().unwrap();
        assert_eq!(
            outputs["response"]["data"]["results"],
            json!({"a": {"mean": 2.0}})
        );
    }

    #[test]
    fn hashes_like_sha256sum() {
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
}
