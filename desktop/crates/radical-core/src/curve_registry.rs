//! What is known about a calibration curve: the measurable half of
//! `services/curve_registry.py`.
//!
//! Nothing is ASSERTED here about a curve that can be measured from it
//! instead. [`CurveSpec`] carries editorial metadata -- name, citation, which
//! sample type the curve suits -- written by hand because it cannot be
//! derived, and everything countable is computed from the curve that actually
//! loaded. A stale point count is not something that can be written down.
//!
//! The Python module also finds stored curves in the database, caches them,
//! and counts what uses them. That half belongs to the store (Phase 5); here
//! are the pure functions it is built on, with stored curves handed in by the
//! caller: [`resolve_alias`] takes their names, [`find_mixed`] their recipes.

use std::collections::BTreeMap;

use sha2::{Digest, Sha256};

use crate::calibration::CalibrationCurve;
use crate::numeric::diff;
use crate::{Error, curves, pyfmt};

/// A curve shipped with the app: the editorial half of its description.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Builtin {
    pub id: &'static str,
    pub name: &'static str,
    /// Display categorisation: terrestrial | marine | southern_hemisphere.
    pub kind_of_curve: &'static str,
    /// Which kind of sample this curve suits. Distinct from the type: IntCal20
    /// and SHCal20 are both terrestrial but not interchangeable.
    pub sample_type: &'static str,
    pub description: &'static str,
    pub reference: &'static str,
    pub year: u32,
    pub doi: &'static str,
    /// Other spellings this curve answers to when read out of a file, matched
    /// after [`normalise`].
    pub aliases: &'static [&'static str],
}

/// The builtins, in the order the API lists them; the first for a given
/// sample type is what the frontend switches to when that type is selected.
pub const BUILTINS: [Builtin; 3] = [
    Builtin {
        id: "intcal20",
        name: "IntCal20",
        kind_of_curve: "terrestrial",
        sample_type: "terrestrial",
        description: "Northern Hemisphere terrestrial radiocarbon age calibration",
        reference: "Reimer et al. 2020",
        year: 2020,
        doi: "10.1017/RDC.2020.41",
        aliases: &["intcal", "intcal20"],
    },
    Builtin {
        id: "shcal20",
        name: "SHCal20",
        kind_of_curve: "southern_hemisphere",
        sample_type: "terrestrial",
        description: "Southern Hemisphere terrestrial radiocarbon age calibration",
        reference: "Hogg et al. 2020",
        year: 2020,
        doi: "10.1017/RDC.2020.59",
        aliases: &["shcal", "shcal20"],
    },
    Builtin {
        id: "marine20",
        name: "Marine20",
        kind_of_curve: "marine",
        sample_type: "marine",
        description: "Marine radiocarbon age calibration",
        reference: "Heaton et al. 2020",
        year: 2020,
        doi: "10.1017/RDC.2020.68",
        aliases: &["marine", "marine20"],
    },
];

/// A curve name reduced to its comparable core: lowercased, punctuation and
/// spacing dropped, and a file extension removed, so 'IntCal20.14c',
/// 'intcal-20' and 'IntCal 20' all match the same curve.
pub fn normalise(name: &str) -> String {
    let text = name.trim().trim_start_matches('=');
    let lower = text.to_lowercase();
    let text = if [".14c", ".csv", ".txt"]
        .iter()
        .any(|ext| lower.ends_with(ext))
    {
        text.rsplit_once('.').map_or(text, |(stem, _)| stem)
    } else {
        text
    };
    text.to_lowercase()
        .chars()
        .filter(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
        .collect()
}

/// `numpy.isclose(a, b)` with its default tolerances.
fn isclose(a: f64, b: f64) -> bool {
    (a - b).abs() <= 1e-8 + 1e-5 * b.abs()
}

/// The calendar grid as the runs of constant step it is made of, e.g.
/// "1 yr (0-13,900), 5 yr (13,900-25,000)". The change points are where the
/// curve's own resolution changes. A curve with too many runs to list -- an
/// imported one need not be regular at all -- is summarised by its range of
/// steps instead.
pub fn step_summary(cal_ages: &[f64]) -> String {
    if cal_ages.len() < 2 {
        return "n/a".to_owned();
    }
    let steps = diff(cal_ages);
    let mut runs: Vec<(f64, f64, f64)> = Vec::new(); // (step, from, to)
    let mut start = 0;
    for i in 1..=steps.len() {
        if i < steps.len() && isclose(steps[i], steps[start]) {
            continue;
        }
        runs.push((steps[start], cal_ages[start], cal_ages[i]));
        start = i;
    }
    if runs.len() > 5 {
        let lo = steps.iter().copied().fold(f64::INFINITY, f64::min);
        let hi = steps.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        return format!("{}-{} yr (irregular)", pyfmt::g(lo, 6), pyfmt::g(hi, 6));
    }
    runs.iter()
        .map(|&(step, lo, hi)| {
            format!(
                "{} yr ({}-{})",
                pyfmt::g(step, 6),
                pyfmt::thousands(lo),
                pyfmt::thousands(hi)
            )
        })
        .collect::<Vec<_>>()
        .join(", ")
}

/// Hash the curve's numbers, not its file. Two files differing only in their
/// header comments describe the same curve and hash the same; a file whose
/// numbers were corrected does not. Lets a rerun notice that the curve under
/// a stored analysis is not the one it was run against.
pub fn content_hash(curve: &CalibrationCurve) -> String {
    let mut digest = Sha256::new();
    for ((cal, c14), err) in curve
        .cal_ages()
        .iter()
        .zip(curve.c14_ages())
        .zip(curve.c14_errors())
    {
        let line = format!(
            "{},{},{}\n",
            pyfmt::g(*cal, 6),
            pyfmt::g(*c14, 6),
            pyfmt::g(*err, 6)
        );
        digest.update(line.as_bytes());
    }
    digest
        .finalize()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// The countable half of a curve's description, measured from the curve.
#[derive(Debug, Clone, PartialEq)]
pub struct Measured {
    pub data_points: usize,
    pub cal_min: f64,
    pub cal_max: f64,
    pub c14_min: f64,
    pub c14_max: f64,
    /// The calendar grid's runs of constant step; see [`step_summary`].
    pub resolution: String,
    /// See [`content_hash`].
    pub content_sha256: String,
}

fn min_max(values: &[f64]) -> (f64, f64) {
    values
        .iter()
        .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), &v| {
            (lo.min(v), hi.max(v))
        })
}

pub fn measure(curve: &CalibrationCurve) -> Result<Measured, Error> {
    if curve.is_empty() {
        return Err(Error::EmptyCurve);
    }
    let (cal_min, cal_max) = min_max(curve.cal_ages());
    let (c14_min, c14_max) = min_max(curve.c14_ages());
    Ok(Measured {
        data_points: curve.len(),
        cal_min,
        cal_max,
        c14_min,
        c14_max,
        resolution: step_summary(curve.cal_ages()),
        content_sha256: content_hash(curve),
    })
}

/// One builtin curve's full description.
#[derive(Debug, Clone, PartialEq)]
pub struct CurveSpec {
    pub builtin: &'static Builtin,
    pub measured: Measured,
}

pub fn builtin_spec(curve_id: &str) -> Result<CurveSpec, Error> {
    let builtin = BUILTINS
        .iter()
        .find(|b| b.id == curve_id)
        .ok_or_else(|| Error::UnknownCurve(curve_id.to_owned()))?;
    Ok(CurveSpec {
        builtin,
        measured: measure(curves::get_curve(curve_id)?)?,
    })
}

/// A stored curve's names, as the caller reads them from the store.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoredNames {
    pub id: String,
    pub name: String,
    pub aliases: Vec<String>,
}

/// A curve name as written in a file -- an OxCal `Curve("=SHCal20")`, a CSV
/// cell reading 'IntCal', a filename 'intcal20.14c' -- mapped to the id this
/// app loads it under, or None if it names no curve we have.
///
/// None rather than a default: a file calibrated against a curve we do not
/// have cannot be honoured by quietly using a different one. Builtins answer
/// first; then `stored`, the stored curves the user may see in the order they
/// were added, a later one taking a name an earlier one also answers to.
pub fn resolve_alias(name: &str, stored: &[StoredNames]) -> Option<String> {
    let key = normalise(name);
    if key.is_empty() {
        return None;
    }
    for builtin in &BUILTINS {
        let names = [builtin.id, builtin.name]
            .into_iter()
            .chain(builtin.aliases.iter().copied());
        if names.map(normalise).any(|n| n == key) {
            return Some(builtin.id.to_owned());
        }
    }
    let mut index: BTreeMap<String, &str> = BTreeMap::new();
    for row in stored {
        index.insert(normalise(&row.id), &row.id);
        index.insert(normalise(&row.name), &row.id);
        for alias in &row.aliases {
            index.insert(normalise(alias), &row.id);
        }
    }
    index.get(&key).map(|id| (*id).to_owned())
}

/// A stored mixed curve's recipe: `percent_a` percent of curve `a`, the rest
/// of `b`.
#[derive(Debug, Clone, PartialEq)]
pub struct MixRecipe {
    pub id: String,
    pub a: String,
    pub b: String,
    pub percent_a: f64,
    pub percent_a_error: f64,
}

/// How close a stored mixture must be to a requested one to be the same.
/// Percentages in .oxcal files are written to whole numbers, so anything
/// within a tenth is the same recipe written differently.
pub const MIX_MATCH_TOLERANCE: f64 = 0.1;

/// The first of `recipes` (the user's mixed curves, oldest first) built from
/// this recipe, or None. Both orderings are checked with the percentage
/// flipped: 30% IntCal20 with Marine20 IS 70% Marine20 with IntCal20.
pub fn find_mixed<'a>(
    recipes: &'a [MixRecipe],
    a_id: &str,
    b_id: &str,
    percent_a: f64,
    percent_a_error: f64,
) -> Option<&'a str> {
    recipes
        .iter()
        .find(|r| {
            if (r.percent_a_error - percent_a_error).abs() > MIX_MATCH_TOLERANCE {
                return false;
            }
            let forward = r.a == a_id
                && r.b == b_id
                && (r.percent_a - percent_a).abs() <= MIX_MATCH_TOLERANCE;
            let reversed = r.a == b_id
                && r.b == a_id
                && (r.percent_a - (100.0 - percent_a)).abs() <= MIX_MATCH_TOLERANCE;
            forward || reversed
        })
        .map(|r| r.id.as_str())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn recipe(id: &str, a: &str, b: &str, percent_a: f64, error: f64) -> MixRecipe {
        MixRecipe {
            id: id.into(),
            a: a.into(),
            b: b.into(),
            percent_a,
            percent_a_error: error,
        }
    }

    #[test]
    fn a_mixture_is_found_either_way_round() {
        let stored = [recipe("m1", "intcal20", "marine20", 30.0, 10.0)];
        assert_eq!(
            find_mixed(&stored, "intcal20", "marine20", 30.0, 10.0),
            Some("m1")
        );
        assert_eq!(
            find_mixed(&stored, "marine20", "intcal20", 70.0, 10.0),
            Some("m1")
        );
        assert_eq!(
            find_mixed(&stored, "intcal20", "marine20", 30.05, 10.08),
            Some("m1")
        );
    }

    #[test]
    fn a_different_recipe_is_not_the_same_mixture() {
        let stored = [recipe("m1", "intcal20", "marine20", 30.0, 10.0)];
        assert_eq!(
            find_mixed(&stored, "intcal20", "marine20", 31.0, 10.0),
            None
        );
        assert_eq!(find_mixed(&stored, "intcal20", "marine20", 30.0, 0.0), None);
        assert_eq!(find_mixed(&stored, "shcal20", "marine20", 30.0, 10.0), None);
        assert_eq!(
            find_mixed(&stored, "marine20", "intcal20", 30.0, 10.0),
            None
        );
    }

    #[test]
    fn the_oldest_match_wins() {
        let stored = [
            recipe("old", "intcal20", "marine20", 30.0, 10.0),
            recipe("new", "marine20", "intcal20", 70.0, 10.0),
        ];
        assert_eq!(
            find_mixed(&stored, "intcal20", "marine20", 30.0, 10.0),
            Some("old")
        );
    }

    #[test]
    fn stored_curves_answer_to_their_names_after_the_builtins() {
        let stored = [
            StoredNames {
                id: "c-1".into(),
                name: "Lake Varves".into(),
                aliases: vec!["LV".into()],
            },
            StoredNames {
                id: "c-2".into(),
                name: "IntCal20".into(),
                aliases: vec!["lv".into()],
            },
        ];
        assert_eq!(
            resolve_alias("lake varves.14c", &stored).as_deref(),
            Some("c-1")
        );
        // A later curve takes a name an earlier one also answers to...
        assert_eq!(resolve_alias("LV", &stored).as_deref(), Some("c-2"));
        // ...but never a builtin's.
        assert_eq!(
            resolve_alias("IntCal20", &stored).as_deref(),
            Some("intcal20")
        );
        assert_eq!(resolve_alias("c-1", &stored).as_deref(), Some("c-1"));
        assert_eq!(resolve_alias("nothing", &stored), None);
    }
}
