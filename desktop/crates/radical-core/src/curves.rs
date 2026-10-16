//! The builtin curves -- IntCal20, Marine20, SHCal20 -- compiled into the
//! binary from `calibration_data/`, and parsed once on first use.
//!
//! Python looks a curve up through `curve_registry`, which also knows the
//! curves a user has imported or mixed. Those live in the database, so here
//! only the builtins are known; whatever holds the stored curves resolves
//! those itself and falls back to [`builtin`].

use std::sync::OnceLock;

use crate::{CalibrationCurve, Error};

/// The builtin ids, sorted, as Python's error message lists them.
pub const BUILTIN_IDS: [&str; 3] = ["intcal20", "marine20", "shcal20"];

struct Builtin {
    id: &'static str,
    name: &'static str,
    bytes: &'static [u8],
}

const BUILTINS: [Builtin; 3] = [
    Builtin {
        id: "intcal20",
        name: "IntCal20",
        bytes: include_bytes!("../../../../calibration_data/intcal20.14c"),
    },
    Builtin {
        id: "marine20",
        name: "Marine20",
        bytes: include_bytes!("../../../../calibration_data/marine20.14c"),
    },
    Builtin {
        id: "shcal20",
        name: "SHCal20",
        bytes: include_bytes!("../../../../calibration_data/shcal20.14c"),
    },
];

static PARSED: [OnceLock<CalibrationCurve>; 3] =
    [OnceLock::new(), OnceLock::new(), OnceLock::new()];

/// The bytes of a builtin curve file as embedded, for checking them against
/// the hashes the corpus was generated with.
pub fn builtin_bytes(curve_id: &str) -> Option<&'static [u8]> {
    BUILTINS.iter().find(|b| b.id == curve_id).map(|b| b.bytes)
}

/// A builtin curve by id, parsed on first use.
pub fn builtin(curve_id: &str) -> Option<&'static CalibrationCurve> {
    let index = BUILTINS.iter().position(|b| b.id == curve_id)?;
    Some(PARSED[index].get_or_init(|| {
        let spec = &BUILTINS[index];
        // UTF-8: the IntCal and Marine20 headers name their authors with
        // accents.
        let text = std::str::from_utf8(spec.bytes).expect("a builtin curve file is not UTF-8");
        CalibrationCurve::from_14c(text, spec.name).expect("a builtin curve file has no data")
    }))
}

/// Shared curve lookup, so everything that calibrates uses the same curve
/// data.
///
/// An unrecognized id is refused rather than falling back to IntCal20: a
/// mistyped "marine2020" would otherwise return a plausible-looking
/// terrestrial calibration with no reservoir correction and nothing to say
/// so.
pub fn get_curve(curve_id: &str) -> Result<&'static CalibrationCurve, Error> {
    builtin(curve_id).ok_or_else(|| Error::UnknownCurve(curve_id.to_owned()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_builtin_loads_ascending_from_zero_to_55_000() {
        for id in BUILTIN_IDS {
            let curve = get_curve(id).unwrap();
            assert_eq!(curve.cal_ages()[0], 0.0, "{id}");
            assert_eq!(*curve.cal_ages().last().unwrap(), 55_000.0, "{id}");
            assert!(curve.cal_ages().windows(2).all(|w| w[0] < w[1]), "{id}");
        }
        assert_eq!(get_curve("intcal20").unwrap().len(), 9501);
        assert_eq!(get_curve("marine20").unwrap().len(), 5501);
    }

    #[test]
    fn an_unknown_id_is_refused_by_name() {
        let error = get_curve("marine2020").unwrap_err();
        assert_eq!(
            error.to_string(),
            "Unknown calibration curve 'marine2020'. Supported curves: intcal20, marine20, shcal20"
        );
    }
}
