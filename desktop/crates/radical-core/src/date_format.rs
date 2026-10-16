//! Presenting calendar ages in the reporting format the user chose: cal BP,
//! BC/AD, or BCE/CE. A port of `backend/app/services/date_format.py`.
//!
//! Everything is computed and stored as cal BP, so this is presentation only.
//! It exists server-side so a CSV export can carry the reporting format the
//! results page is showing; the JSON export keeps raw cal BP, being data
//! rather than a report.
//!
//! A mirror of `frontend/src/utils/dateFormat.ts`. Two implementations is not
//! ideal, but the alternative was duplicating the whole CSV row assembly in
//! the browser, and this is the smaller and more stable half -- the 1950 datum
//! and the no-year-zero rule are fixed by convention.
//!
//! One intentional difference: ranges join with a plain hyphen, not the
//! en-dash used on screen, matching the CSV's other range columns and avoiding
//! the mangling an en-dash invites outside UTF-8. Every age itself formats
//! identically.

use crate::HpdRange;

pub const CAL_BP: &str = "calBP";
pub const BC_AD: &str = "bcad";
pub const BCE_CE: &str = "bce_ce";

pub const VALID_DATE_FORMATS: [&str; 3] = [CAL_BP, BC_AD, BCE_CE];
pub const DEFAULT_DATE_FORMAT: &str = CAL_BP;

/// Radiocarbon "Present" is fixed at 1950 by convention, whenever the
/// measurement was actually taken.
pub const RADIOCARBON_PRESENT: f64 = 1950.0;

/// Short forms for CSV column headers, so a formatted column names its own
/// convention without a legend.
pub fn era_label(date_format: &str) -> Option<&'static str> {
    match date_format {
        BC_AD => Some("BC/AD"),
        BCE_CE => Some("BCE/CE"),
        _ => None,
    }
}

/// An unrecognised or absent format falls back to cal BP rather than failing
/// the export over a presentation choice.
pub fn normalise(date_format: Option<&str>) -> &'static str {
    VALID_DATE_FORMATS
        .into_iter()
        .find(|&f| Some(f) == date_format)
        .unwrap_or(DEFAULT_DATE_FORMAT)
}

/// Python's `round()` of a float to an int: halves to even.
fn round_half_even(x: f64) -> i64 {
    x.round_ties_even() as i64
}

/// cal BP to a proleptic Gregorian year in the conventional historical
/// numbering, with NO year zero: 1 BC is followed by AD 1. BCE/CE use the
/// same numbers and differ only in era label. Returns the year and whether it
/// is in the current era.
fn to_year(cal_bp: f64) -> (i64, bool) {
    let signed_year = RADIOCARBON_PRESENT - cal_bp;
    if signed_year > 0.0 {
        (round_half_even(signed_year), true)
    } else {
        (round_half_even(1.0 - signed_year), false)
    }
}

/// One calendar age in the chosen format; empty for no age.
pub fn format_age(cal_bp: Option<f64>, date_format: &str) -> String {
    let Some(cal_bp) = cal_bp else {
        return String::new();
    };
    let date_format = normalise(Some(date_format));
    if date_format == CAL_BP {
        return format!("{} cal BP", round_half_even(cal_bp));
    }
    let (year, current_era) = to_year(cal_bp);
    match (date_format == BC_AD, current_era) {
        (true, true) => format!("AD {year}"),
        (true, false) => format!("{year} BC"),
        (false, true) => format!("{year} CE"),
        (false, false) => format!("{year} BCE"),
    }
}

/// A range, always oldest first. A larger cal BP is further in the past, so
/// "oldest first" is descending cal BP and ascending calendar year.
pub fn format_range(cal_bp_a: Option<f64>, cal_bp_b: Option<f64>, date_format: &str) -> String {
    let (Some(a), Some(b)) = (cal_bp_a, cal_bp_b) else {
        return String::new();
    };
    // Python's max(a, b) and min(a, b): the first unless the second beats it.
    let older = if b > a { b } else { a };
    let younger = if b < a { b } else { a };
    let date_format = normalise(Some(date_format));
    if date_format == CAL_BP {
        return format!(
            "{}-{} cal BP",
            round_half_even(older),
            round_half_even(younger)
        );
    }
    format!(
        "{} - {}",
        format_age(Some(older), date_format),
        format_age(Some(younger), date_format)
    )
}

/// A possibly multimodal HPD region as ranges, each with its own share of the
/// probability, as the results page shows it. A single min-max span would
/// claim probability in the gap between separate peaks.
pub fn format_hpd_ranges(ranges: &[HpdRange], date_format: &str) -> String {
    ranges
        .iter()
        .map(|r| {
            format!(
                "{} ({:.1}%)",
                format_range(Some(r.max), Some(r.min), date_format),
                r.probability_percent
            )
        })
        .collect::<Vec<_>>()
        .join("; ")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rounds_halves_to_even_as_python_does() {
        assert_eq!(format_age(Some(0.5), CAL_BP), "0 cal BP");
        assert_eq!(format_age(Some(1.5), CAL_BP), "2 cal BP");
        assert_eq!(format_age(Some(-0.4), CAL_BP), "0 cal BP");
        // Python formats a percentage's exact binary value, halves to even.
        assert_eq!(format!("{:.1}", 0.25), "0.2");
        assert_eq!(format!("{:.1}", 0.35), "0.3");
        assert_eq!(format!("{:.2}", 0.125), "0.12");
    }

    #[test]
    fn there_is_no_year_zero() {
        assert_eq!(format_age(Some(1949.0), BC_AD), "AD 1");
        assert_eq!(format_age(Some(1950.0), BC_AD), "1 BC");
        assert_eq!(format_age(Some(1950.0), BCE_CE), "1 BCE");
        assert_eq!(format_age(None, BC_AD), "");
    }

    #[test]
    fn an_unknown_format_is_cal_bp() {
        assert_eq!(normalise(Some("nonsense")), CAL_BP);
        assert_eq!(normalise(None), CAL_BP);
        assert_eq!(normalise(Some(BCE_CE)), BCE_CE);
        assert_eq!(era_label(BC_AD), Some("BC/AD"));
    }
}
