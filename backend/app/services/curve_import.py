"""
Reading a calibration curve out of an uploaded file.

Same contract as services/date_import.py: parsing and storing are separate
steps, and this module only reads. A file that cannot be read comes back as a
list of what is wrong with it rather than an exception -- the request
succeeded, and its answer is that row 412 has a negative sigma.

Unlike a date list, almost nothing about a curve file is checkable by eye
afterwards: 9,500 rows will silently produce plausible, wrong calendar ages for
every sample calibrated against them. So this refuses more than it repairs.
Descending calendar order is normalised and comments skipped, those being
format conventions; everything else -- a duplicate calendar age, a non-positive
sigma, a column that looks like F14C -- is an error naming the row, because
each has a plausible innocent reading that would be wrong.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import csv
import io
import re

import numpy as np

# A curve file is bigger than a date list but bounded -- IntCal20 is 220 KB.
# The cap is on the decoded text, so a mis-picked file fails at once rather
# than being parsed row by row into an enormous error report.
MAX_CONTENT_BYTES = 8 * 1024 * 1024

# The most rows worth reporting individually. Past this the file is wrong in a
# structural way and a thousand near-identical errors help nobody.
MAX_REPORTED_ERRORS = 25

# Column-name spellings understood in a headed CSV, in the same style
# date_import.py aliases its own columns.
COLUMN_ALIASES = {
    'cal_bp': ('cal_bp', 'calbp', 'cal bp', 'calendar_age', 'calendar age',
               'cal_age', 'calage', 'cal', 'year_bp', 'bp'),
    'c14_age': ('c14_age', 'c14age', '14c_age', '14c age', 'c14', 'conv_bp',
                'convbp', 'radiocarbon_age', 'radiocarbon age', 'rc_age',
                'conventional_age', '14c'),
    'error': ('error', 'sigma', 'c14_error', 'c14error', '1sigma', '1_sigma',
              'std', 'stdev', 'uncertainty', 'calsigma', 'cal_sigma', 'err'),
}

# Below this, a "radiocarbon age" column is far more likely to be F14C
# (fraction modern, ~0-1.2) than an age in years. Calibrating F14C as an age
# would place every sample within a century of the present.
F14C_CEILING = 10.0


@dataclass
class CurveImportResult:
    """
    What a curve file turned out to contain.

    Three channels, as ImportResult uses them: `errors` means nothing was
    read, `warnings` that it was read but something could not be honoured,
    `notes` that it was read and here is what was assumed.

    `points` is the normalised curve -- ascending calendar age, three columns
    -- and `preview` a downsampled series for drawing it in the import dialog
    before anything is stored.
    """
    points: List[Tuple[float, float, float]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    source_format: str = '14c'
    # How this file was read, for storing alongside it so the parse is
    # reproducible: {'calendar': 'cal_bp'|'bc_ad', 'columns': [...] or None}.
    parse_options: Dict[str, Any] = field(default_factory=dict)
    # A name and citation guessed from the file's own header comments, offered
    # as defaults in the import form.
    suggested_name: Optional[str] = None
    suggested_reference: Optional[str] = None

    @property
    def ok(self) -> bool:
        return not self.errors

    def refuse(self) -> 'CurveImportResult':
        """Drop the rows, keeping only what explains the refusal. An import is
        all-or-nothing, and a refused result still carrying half a curve is an
        invitation to calibrate against half a curve."""
        if self.errors:
            self.points = []
        return self

    def summary(self) -> Dict[str, Any]:
        """The measured description of the curve just read -- the same fields
        curve_registry.CurveSpec reports, so the import dialog can show what
        the curve library will show once it is stored."""
        if not self.points:
            return {}
        cal = np.array([p[0] for p in self.points], dtype=float)
        c14 = np.array([p[1] for p in self.points], dtype=float)
        return {
            'data_points': len(self.points),
            'cal_min': float(cal.min()),
            'cal_max': float(cal.max()),
            'c14_min': float(c14.min()),
            'c14_max': float(c14.max()),
        }

    def preview(self, max_points: int = 400) -> List[List[float]]:
        """
        A downsampled [cal, c14, sigma] series for drawing the curve in the
        import dialog. Min/max decimation is not needed here -- this is a
        sanity check that the file is the shape of a calibration curve, not
        the browser, which serves its own windowed series.
        """
        if not self.points:
            return []
        step = max(1, len(self.points) // max_points)
        return [[p[0], p[1], p[2]] for p in self.points[::step]]

    def as_dict(self) -> Dict[str, Any]:
        return {
            'ok': self.ok,
            'errors': self.errors,
            'warnings': self.warnings,
            'notes': self.notes,
            'source_format': self.source_format,
            'parse_options': self.parse_options,
            'suggested_name': self.suggested_name,
            'suggested_reference': self.suggested_reference,
            'summary': self.summary(),
            'preview': self.preview(),
        }


def decode(content: bytes) -> str:
    """
    The IntCal-family files are latin-1, their headers carrying author names
    with diacritics; spreadsheet exports are usually UTF-8, sometimes with a
    BOM. Tried in that order, falling back rather than failing, since only the
    numbers matter for correctness.
    """
    for encoding in ('utf-8-sig', 'utf-8', 'latin-1'):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode('latin-1', errors='replace')


def _header_lines(text: str) -> List[str]:
    return [line.strip().lstrip('#').strip()
            for line in text.splitlines() if line.strip().startswith('#')]


def _suggestions(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    A name and citation guessed from the file's leading comments.

    The IntCal-family files open with a title line and an author list, where a
    curve's citation actually lives -- the difference between a stored curve
    that cites its source and one labelled 'intcal13.14c'. Only a suggestion:
    the import form shows
    them for editing before anything is stored.
    """
    headers = _header_lines(text)
    if not headers:
        return None, None

    name = None
    for line in headers:
        match = re.search(r'\b((?:IntCal|SHCal|Marine|NHZ|SH)[\s-]?\d{2,4})\b', line, re.I)
        if match:
            name = re.sub(r'[\s-]+', '', match.group(1))
            break

    reference = None
    for line in headers:
        # 'Reimer et al. 2020', 'Hogg et al 2020' -- an author-year citation.
        match = re.search(r'\b([A-Z][A-Za-z\'-]+)\s+et\s+al\.?,?\s+(\d{4})', line)
        if match:
            reference = f'{match.group(1)} et al. {match.group(2)}'
            break

    return name, reference


def _rows_from_delimited(text: str) -> Tuple[List[List[str]], Optional[List[str]], str]:
    """
    Split the file into rows, returning (rows, header, delimiter-name).

    Comment lines are dropped. A first non-comment row whose cells are not all
    numeric is taken as a header; the .14c files have none, which is why the
    header is optional rather than required.
    """
    body = [line for line in text.splitlines()
            if line.strip() and not line.strip().startswith('#')]
    if not body:
        return [], None, 'csv'

    sample = '\n'.join(body[:5])
    delimiter = '\t' if sample.count('\t') > sample.count(',') else ','
    if delimiter == ',' and ',' not in sample and re.search(r'\s{1,}', sample):
        # Whitespace-separated, as some curve files are.
        rows = [re.split(r'[\s]+', line.strip()) for line in body]
        return rows, None, 'whitespace'

    rows = [row for row in csv.reader(io.StringIO('\n'.join(body)), delimiter=delimiter)]
    header = None
    if rows and not all(_is_number(cell) for cell in rows[0][:3]):
        header = [cell.strip().lower() for cell in rows[0]]
        rows = rows[1:]
    return rows, header, 'tsv' if delimiter == '\t' else 'csv'


def _is_number(text: str) -> bool:
    try:
        float(str(text).strip())
        return True
    except (TypeError, ValueError):
        return False


def _column_indices(header: List[str], errors: List[str]) -> Optional[Dict[str, int]]:
    """
    Match a header row to the three columns needed. Order is irrelevant: a
    file whose columns are ordered differently is not a broken file.
    """
    found: Dict[str, int] = {}
    for field_name, aliases in COLUMN_ALIASES.items():
        for index, cell in enumerate(header):
            normalised = re.sub(r'[^a-z0-9]+', '_', cell.strip().lower()).strip('_')
            if normalised in {re.sub(r'[^a-z0-9]+', '_', a).strip('_') for a in aliases}:
                found[field_name] = index
                break
    missing = [name for name in COLUMN_ALIASES if name not in found]
    if missing:
        errors.append(
            "Could not find a column for: " + ', '.join(missing) +
            ". A curve needs a calendar age, a radiocarbon age and a 1-sigma "
            "error; the header row read as: " + ', '.join(header) + "."
        )
        return None
    return found


def parse_curve(
    text: str,
    calendar: str = 'cal_bp',
    columns: Optional[Dict[str, int]] = None,
) -> CurveImportResult:
    """
    Read a calibration curve from the text of an uploaded file.

    Args:
        text: the decoded file.
        calendar: how to read the calendar column -- 'cal_bp' (the .14c
            convention) or 'bc_ad' (negative for BC), which is how a curve
            authored in a spreadsheet usually comes. Converted on the way in
            so everything downstream is cal BP, as every other age in this app
            is.
        columns: which column holds what, as
            {'cal_bp': i, 'c14_age': j, 'error': k}. Given, it is used instead
            of working it out from the header -- this is what a stored curve
            passes back in, so re-reading it years later cannot depend on what
            the detection code would guess by then. Omitted, the columns are
            detected and reported in `parse_options` for exactly that purpose.
    """
    result = CurveImportResult()
    result.parse_options = {'calendar': calendar}
    result.suggested_name, result.suggested_reference = _suggestions(text)

    rows, header, source_format = _rows_from_delimited(text)
    result.source_format = source_format

    if not rows:
        result.errors.append("The file has no data rows -- only comments or blank lines.")
        return result.refuse()

    if columns is not None:
        indices = {k: int(v) for k, v in columns.items()}
        result.parse_options['columns'] = dict(indices)
        result.notes.append(
            "Read the columns this curve was stored with rather than "
            "detecting them again."
        )
    elif header is not None:
        indices = _column_indices(header, result.errors)
        if indices is None:
            return result.refuse()
        result.parse_options['columns'] = {k: int(v) for k, v in indices.items()}
        result.notes.append(
            f"Read columns by name: calendar age from '{header[indices['cal_bp']]}', "
            f"radiocarbon age from '{header[indices['c14_age']]}', "
            f"error from '{header[indices['error']]}'."
        )
    else:
        indices = {'cal_bp': 0, 'c14_age': 1, 'error': 2}
        result.parse_options['columns'] = dict(indices)
        result.notes.append(
            "No header row, so the first three columns were read as calendar "
            "age, radiocarbon age and 1-sigma error -- the .14c layout."
        )

    parsed: List[Tuple[float, float, float]] = []
    for line_number, row in enumerate(rows, start=2 if header is not None else 1):
        if len(result.errors) >= MAX_REPORTED_ERRORS:
            result.errors.append(
                f"Stopped after {MAX_REPORTED_ERRORS} problems; the file's "
                "layout is probably not what was expected."
            )
            return result.refuse()

        if max(indices.values()) >= len(row):
            result.errors.append(
                f"Row {line_number}: has {len(row)} column(s), needs at least "
                f"{max(indices.values()) + 1}."
            )
            continue

        values = []
        for field_name in ('cal_bp', 'c14_age', 'error'):
            cell = str(row[indices[field_name]]).strip()
            if not _is_number(cell):
                result.errors.append(
                    f"Row {line_number}: {field_name.replace('_', ' ')} is not "
                    f"a number ('{cell}')."
                )
                values = None
                break
            values.append(float(cell))
        if values is None:
            continue

        cal, c14, sigma = values
        if calendar == 'bc_ad':
            # No year zero in the BC/AD convention, and cal BP counts from
            # 1950: AD 1 is 1949 cal BP, 1 BC is 1950 cal BP.
            cal = 1950 - cal if cal > 0 else 1950 - (cal + 1)

        if not all(np.isfinite(v) for v in (cal, c14, sigma)):
            result.errors.append(f"Row {line_number}: contains a non-finite value.")
            continue
        if sigma <= 0:
            result.errors.append(
                f"Row {line_number}: 1-sigma error is {sigma:g}. A curve's "
                "error must be positive -- it goes into the quadrature sum "
                "with the measurement's own error."
            )
            continue
        if cal < 0:
            result.errors.append(
                f"Row {line_number}: calendar age {cal:g} is negative (after "
                f"1950 AD). Post-bomb curves need an F14C likelihood rather "
                "than a conventional-age one, which this app does not yet "
                "have, so it cannot calibrate against one."
            )
            continue

        parsed.append((cal, c14, sigma))

    if result.errors:
        return result.refuse()

    if len(parsed) < 2:
        result.errors.append(
            f"Only {len(parsed)} usable row(s). A calibration curve needs at "
            "least two points to interpolate between."
        )
        return result.refuse()

    c14_values = np.array([p[1] for p in parsed])
    if np.nanmax(np.abs(c14_values)) < F14C_CEILING:
        result.errors.append(
            "The radiocarbon age column tops out at "
            f"{np.nanmax(c14_values):g}, which looks like F14C (fraction "
            "modern) rather than a conventional radiocarbon age in years. "
            "Calibrating those values as ages would date every sample to "
            "within a century of the present."
        )
        return result.refuse()

    ascending = all(parsed[i][0] < parsed[i + 1][0] for i in range(len(parsed) - 1))
    if not ascending:
        descending = all(parsed[i][0] > parsed[i + 1][0] for i in range(len(parsed) - 1))
        parsed.sort(key=lambda p: p[0])
        if descending:
            result.notes.append(
                "The file is written oldest-first, as the IntCal files are; "
                "sorted youngest-first for storage."
            )
        else:
            result.notes.append("Rows were not in calendar order; sorted.")

    duplicates = [parsed[i][0] for i in range(len(parsed) - 1)
                  if parsed[i][0] == parsed[i + 1][0]]
    if duplicates:
        shown = ', '.join(f'{d:g}' for d in duplicates[:5])
        result.errors.append(
            f"{len(duplicates)} duplicated calendar age(s) ({shown}"
            f"{', ...' if len(duplicates) > 5 else ''}). A curve must give one "
            "radiocarbon age per calendar year; two rows for the same year "
            "leave it ambiguous which one calibration should use."
        )
        return result.refuse()

    result.points = parsed
    return result


def parse_upload(filename: str, content: bytes, calendar: str = 'cal_bp') -> CurveImportResult:
    """Decode an uploaded curve file and read it. Format is decided by content
    rather than extension, so a .csv holding .14c rows reads correctly."""
    result = parse_curve(decode(content), calendar=calendar)
    if result.suggested_name is None and filename:
        stem = re.split(r'[\\/]', filename)[-1].rsplit('.', 1)[0]
        result.suggested_name = stem or None
    return result


def to_14c_text(points: List[Tuple[float, float, float]], header: str = '') -> str:
    """
    Render a curve in the .14c format the bundled curves use, for export and
    for storing a mixed curve's synthesised data in a shape any other
    radiocarbon program can read.
    """
    lines = [f'# {line}' for line in header.splitlines() if line] if header else []
    lines.append('# CAL BP, 14C age, Sigma')
    lines.extend(f'{cal:g},{c14:g},{sigma:g}' for cal, c14, sigma in points)
    return '\n'.join(lines) + '\n'
