//! Curves by id, built in or stored (`curve_registry.resolve`): a stored
//! curve rebuilt from the file it was imported from, or a mixture from its
//! recipe, and refused unless it hashes to what it was stored with.
//!
//! A stored curve's numbers never change, so each is built once and kept for
//! the life of the process -- leaked, which is what lets the solvers borrow
//! it like a built-in one for as long as they need.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use radical_core::curve_mix::mix;
use radical_core::curve_registry::content_hash;
use radical_core::measurement::Curves;
use radical_core::{CalibrationCurve, Error};
use radical_io::curve_import::{Calendar, parse_curve};
use radical_store::Store;
use radical_store::rows::{self, CalibrationCurve as CurveRow};

/// How deep mixtures of mixtures may go before one is taken to refer to
/// itself.
const MAX_MIX_DEPTH: usize = 4;

/// The curves an analysis can name.
pub struct StoreCurves {
    store: Arc<Store>,
    built: Mutex<HashMap<String, &'static CalibrationCurve>>,
}

impl StoreCurves {
    pub fn new(store: Arc<Store>) -> Self {
        Self {
            store,
            built: Mutex::new(HashMap::new()),
        }
    }

    fn row(&self, curve_id: &str) -> Option<CurveRow> {
        self.store.read(|c| rows::get(c, curve_id)).ok().flatten()
    }

    fn build(&self, curve_id: &str, depth: usize) -> Result<&'static CalibrationCurve, Error> {
        if let Ok(builtin) = radical_core::get_curve(curve_id) {
            return Ok(builtin);
        }
        if let Some(curve) = self
            .built
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .get(curve_id)
        {
            return Ok(curve);
        }
        let row = self
            .row(curve_id)
            .ok_or_else(|| Error::UnknownCurve(curve_id.to_owned()))?;
        let curve = if row.kind == "mixed" {
            self.mixed(&row, depth)?
        } else {
            imported(&row)?
        };
        let curve: &'static CalibrationCurve = Box::leak(Box::new(curve));
        self.built
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .insert(curve_id.to_owned(), curve);
        Ok(curve)
    }

    fn mixed(&self, row: &CurveRow, depth: usize) -> Result<CalibrationCurve, Error> {
        if depth > MAX_MIX_DEPTH {
            return Err(Error::Model(format!(
                "Calibration curve '{}' mixes curves more than {MAX_MIX_DEPTH} deep; it probably refers back to itself.",
                row.id
            )));
        }
        let recipe = row.recipe.clone().unwrap_or_default();
        let (Some(a), Some(b)) = (recipe["a"].as_str(), recipe["b"].as_str()) else {
            return Err(Error::Model(format!(
                "Calibration curve '{}' has no usable recipe.",
                row.id
            )));
        };
        if a == row.id || b == row.id {
            return Err(Error::Model(format!(
                "Calibration curve '{}' refers to itself.",
                row.id
            )));
        }
        let percent = recipe["percent_a"].as_f64().unwrap_or(50.0);
        let error = recipe["percent_a_error"].as_f64().unwrap_or(0.0);
        let curve = (|| {
            mix(
                self.build(a, depth + 1)?,
                self.build(b, depth + 1)?,
                percent,
                error,
                &row.name,
            )
        })()
        .map_err(|e| {
            Error::Model(format!(
                "Calibration curve '{}' cannot be built: {e}",
                row.id
            ))
        })?;
        if !row.content_sha256.is_empty() && content_hash(&curve) != row.content_sha256 {
            return Err(Error::Model(format!(
                "Calibration curve '{}' no longer matches the data it was stored with -- one of the curves it mixes ({a}, {b}) has changed underneath it. Build the mixture again as a new curve rather than trusting this one.",
                row.id
            )));
        }
        Ok(curve)
    }
}

/// An imported curve from its stored file and the options it was read with.
fn imported(row: &CurveRow) -> Result<CalibrationCurve, Error> {
    let Some(text) = row.source_text.as_deref().filter(|t| !t.is_empty()) else {
        return Err(Error::Model(format!(
            "Calibration curve '{}' has no stored data.",
            row.id
        )));
    };
    let options = row.parse_options.clone().unwrap_or_default();
    let calendar = if options["calendar"] == "bc_ad" {
        Calendar::BcAd
    } else {
        Calendar::CalBp
    };
    let columns = options["columns"].as_object().map(|c| {
        let at = |k: &str| c.get(k).and_then(serde_json::Value::as_u64).unwrap_or(0) as usize;
        [at("cal_bp"), at("c14_age"), at("error")]
    });
    let result = parse_curve(text, calendar, columns);
    if let Some(first) = result.errors.first() {
        return Err(Error::Model(format!(
            "Calibration curve '{}' can no longer be read from its stored file: {first}",
            row.id
        )));
    }
    let (cal, rest): (Vec<f64>, Vec<(f64, f64)>) =
        result.points.iter().map(|p| (p.0, (p.1, p.2))).unzip();
    let (c14, err): (Vec<f64>, Vec<f64>) = rest.into_iter().unzip();
    let curve = CalibrationCurve::new(&row.name, cal, c14, err);
    if content_hash(&curve) != row.content_sha256 {
        return Err(Error::Model(format!(
            "Calibration curve '{}' does not match the data it was stored with -- it will not be used. Re-import the curve as a new one rather than trusting this copy.",
            row.id
        )));
    }
    Ok(curve)
}

impl Curves for StoreCurves {
    fn curve(&self, curve_id: &str) -> Result<&CalibrationCurve, Error> {
        self.build(curve_id, 0)
    }
}
