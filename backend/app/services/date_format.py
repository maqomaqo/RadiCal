"""
Presenting calendar ages in the reporting format the user chose: cal BP,
BC/AD, or BCE/CE.

Everything is computed and stored internally as cal BP -- what the curves
and the Bayesian model both work in -- so this only affects presentation.
It exists server-side purely so a CSV export can carry the same reporting
format the results page is showing; the JSON export deliberately keeps raw
cal BP, being data rather than a report.

Deliberately a mirror of frontend/src/utils/dateFormat.ts. Two
implementations is not ideal, but the alternative was duplicating the whole
CSV row assembly in the browser instead, and this is the smaller and far
more stable half: the 1950 datum and the no-year-zero rule are fixed by
convention and will not change. tests/test_date_format.py pins both sides
against the same expected values.

One intentional difference from the frontend: ranges are joined with a
plain hyphen, not the en-dash used on screen. The CSV's existing range
columns already use a hyphen, and staying ASCII avoids the mangling an
en-dash invites when a spreadsheet opens the file as anything but UTF-8.
Only the separator differs -- every age itself formats identically.
"""
from typing import Dict, List, Optional

CAL_BP = 'calBP'
BC_AD = 'bcad'
BCE_CE = 'bce_ce'

VALID_DATE_FORMATS = (CAL_BP, BC_AD, BCE_CE)
DEFAULT_DATE_FORMAT = CAL_BP

# Radiocarbon "Present" is fixed at 1950 by international convention,
# regardless of when the measurement or analysis actually took place.
RADIOCARBON_PRESENT = 1950

# Short forms for CSV column headers, so a formatted column says which
# convention it is in without needing the filename or a legend.
ERA_LABELS = {BC_AD: 'BC/AD', BCE_CE: 'BCE/CE'}


def normalise(date_format: Optional[str]) -> str:
    """An unrecognised or absent format falls back to cal BP rather than
    failing the export -- the reporting format is a presentation choice, and
    refusing to produce the file over it would be a poor trade."""
    return date_format if date_format in VALID_DATE_FORMATS else DEFAULT_DATE_FORMAT


def _to_year(cal_bp: float):
    """
    cal BP to a proleptic Gregorian year, using the conventional historical
    numbering with NO year zero -- 1 BC is immediately followed by AD 1.
    BCE/CE use the same numbers and differ only in era label.
    """
    signed_year = RADIOCARBON_PRESENT - cal_bp
    if signed_year > 0:
        return round(signed_year), True
    return round(1 - signed_year), False


def format_age(cal_bp: Optional[float], date_format: str) -> str:
    """One calendar age in the chosen format."""
    if cal_bp is None:
        return ''
    date_format = normalise(date_format)
    if date_format == CAL_BP:
        return f"{round(cal_bp)} cal BP"

    year, is_current_era = _to_year(cal_bp)
    if date_format == BC_AD:
        return f"AD {year}" if is_current_era else f"{year} BC"
    return f"{year} CE" if is_current_era else f"{year} BCE"


def format_range(cal_bp_a: Optional[float], cal_bp_b: Optional[float], date_format: str) -> str:
    """
    A range, always oldest first. A larger cal BP is further in the past, so
    "oldest first" is descending cal BP and ascending calendar year.
    """
    if cal_bp_a is None or cal_bp_b is None:
        return ''
    older, younger = max(cal_bp_a, cal_bp_b), min(cal_bp_a, cal_bp_b)
    date_format = normalise(date_format)
    if date_format == CAL_BP:
        return f"{round(older)}-{round(younger)} cal BP"
    return f"{format_age(older, date_format)} - {format_age(younger, date_format)}"


def format_hpd_ranges(ranges: Optional[List[Dict]], date_format: str) -> str:
    """
    A (possibly multimodal) HPD region as one or more ranges, each with its
    own share of the probability -- the same convention the results page
    shows. Collapsing to a single min-max span would claim probability in
    the gap between separate peaks.
    """
    if not ranges:
        return ''
    return '; '.join(
        f"{format_range(r['max'], r['min'], date_format)} ({r['probability_percent']:.1f}%)"
        for r in ranges
    )
