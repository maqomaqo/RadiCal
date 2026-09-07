"""
Reading dates in from a file: the CSV path, and the front door both file
formats come through.

The .oxcal path lives in services/oxcal_script.py -- parsing OxCal's own model
language is a job of its own -- and is dispatched to from parse_upload below.

Nothing here writes to the database. An import PARSES and hands back what it
read; storing the dates goes through the same endpoints hand-entered ones use,
so a file upload is not a second route into storage with its own checks to
drift out of step. For the same reason every parsed date is validated through
schemas.sample.BayesianMeasurementInput, so an uploaded date is held to the
bounds a typed one is and reports them in the same words.

An import is ALL-OR-NOTHING. One bad row fails the file, listing every problem
found rather than the first: importing the good rows and skipping the rest is
how a date goes missing without anyone noticing.
"""
import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services import curve_registry

# A file bigger than this is not a date list. The cap is on the DECODED text,
# so a mis-picked file fails immediately rather than being parsed line by line
# into an enormous error report.
MAX_CONTENT_BYTES = 4 * 1024 * 1024

# Matches schemas.sample.ProjectSampleBulkInput's own ceiling, so a file that
# parses here cannot be rejected by the endpoint it is destined for.
MAX_DATES = 500


@dataclass
class ImportResult:
    """
    What a file turned out to contain. The three channels are not
    interchangeable and the UI shows them differently:

    errors    the file was NOT imported. Every problem found, not just the
              first, so one round trip fixes the file.
    warnings  imported, but something could not be honoured, or was honoured in
              a way that changes the model. Must be read.
    notes     imported, and here is what was assumed or rearranged on the way
              in -- recorded rather than left implicit, because a structure the
              importer chose is not one the user wrote.
    """
    dates: List[Dict[str, Any]] = field(default_factory=list)
    # The model structure, when the file carried one -- .oxcal always does, a
    # CSV when it names phases. None means the dates are independent and belong
    # in a group calibration.
    model_tree: Optional[Dict[str, Any]] = None
    # date id -> model element id. Redundant with each date's element_id, and
    # returned separately because the frontend draft store keeps assignments in
    # this shape.
    element_assignments: Dict[str, str] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    source_format: str = 'csv'
    # A name for the analysis, from the file where it says one (an .oxcal model
    # names its outermost element) and from the filename otherwise.
    analysis_name: Optional[str] = None

    @property
    def ok(self) -> bool:
        return not self.errors

    def refuse(self) -> 'ImportResult':
        """
        Drop everything read so far, keeping only the errors and warnings that
        explain why.

        An import is all-or-nothing (see the module docstring), and a refused
        result that still carries the rows it managed to read is an invitation
        to use them -- which is the partial import this module exists to
        avoid. Callers can hand the result straight back without having to
        remember the rule.
        """
        if self.errors:
            self.dates = []
            self.model_tree = None
            self.element_assignments = {}
        return self

    @property
    def suggested_analysis_type(self) -> str:
        """
        What running these dates should mean. A model structure is the whole
        point of a Bayesian run, so a file without one gets a group
        calibration -- several dates on one timeline, asserting no
        relationship between them -- rather than a Bayesian analysis whose
        model happens to constrain nothing.
        """
        return 'bayesian' if self.model_tree is not None else 'group_calibration'

    def as_dict(self) -> Dict[str, Any]:
        return {
            'ok': self.ok,
            'dates': self.dates,
            'model_tree': self.model_tree,
            'element_assignments': self.element_assignments,
            'errors': self.errors,
            'warnings': self.warnings,
            'notes': self.notes,
            'source_format': self.source_format,
            'analysis_name': self.analysis_name,
            'suggested_analysis_type': self.suggested_analysis_type,
            'date_count': len(self.dates),
        }


# --------------------------------------------------------------------------
# Column naming
# --------------------------------------------------------------------------

# Canonical field -> the header spellings that mean it. Order-independent and
# case-insensitive, so a lab report need not be rearranged and the template
# stays a convenience rather than a requirement.
#
# The app's OWN results export is among the accepted spellings (see
# api/results.CSV_HEADER), so exporting a table, correcting it in a spreadsheet
# and importing it back is a path that works.
COLUMN_ALIASES: Dict[str, tuple] = {
    'sample_name': (
        'sample_name', 'name', 'sample', 'sample_id', 'lab_code', 'labcode',
        'lab_no', 'lab_number', 'lab_id', 'code', 'laboratory_code',
        # OxCal's convention for the lab-code column: a table written to be
        # fed to OxCal names the column after the command it becomes.
        'r_date',
    ),
    'age_bp': (
        'age_bp', 'age', 'bp', 'c14_age', 'c14_age_bp', 'radiocarbon_age',
        'uncal_bp', 'uncalibrated_age', 'cra', 'age_14c',
    ),
    'error_1sigma': (
        'error_1sigma', 'error', 'error_bp', '1_sigma_error', '1sigma_error',
        '1sigma', 'sigma', 'sd', 'std', 'stdev', 'uncertainty', 'error_1',
    ),
    'material_type': ('material_type', 'material', 'sample_material'),
    'curve_id': ('curve_id', 'curve', 'calibration_curve', 'cal_curve'),
    'sample_type': ('sample_type', 'type', 'reservoir_type'),
    'delta_r': ('delta_r', 'deltar', 'dr', 'delta_r_value', 'marine_offset'),
    'delta_r_error': (
        'delta_r_error', 'deltar_error', 'dr_error', 'delta_r_sd',
        'delta_r_1sigma', 'delta_r_uncertainty',
    ),
    'phase': (
        'phase', 'context', 'group', 'context_name', 'phase_name',
        'default_context', 'default_context_name', 'feature', 'structure_path',
    ),
    'tpq_age_bp': ('tpq_age_bp', 'tpq', 'tpq_bp', 'tpq_cal_bp', 'max_age_bp'),
    'taq_age_bp': ('taq_age_bp', 'taq', 'taq_bp', 'taq_cal_bp', 'min_age_bp'),
    'outlier_model': ('outlier_model',),
    'outlier_prior': (
        'outlier_prior', 'outlier', 'outlier_probability', 'outlier_p',
    ),
    'depth_cm': ('depth_cm', 'depth'),
    'location': ('location', 'site', 'site_name'),
    'notes': ('notes', 'note', 'comment', 'comments', 'remarks'),
    # Not imported, only recognised, so re-importing the app's results export
    # can tell a date row from a structural one. See parse_csv.
    'row_type': ('row_type',),
}

REQUIRED_COLUMNS = ('sample_name', 'age_bp', 'error_1sigma')

# Columns of the app's results export that are OUTPUT, so re-importing a
# corrected results table neither reads a posterior back in as data nor warns
# about a dozen columns the user did not add.
_RESULT_ONLY_PREFIXES = ('posterior_', 'unmodelled_', 'agreement', 'outlier_percent')

# '95.4% HPD' normalises to '95_4percent_hpd' and an older export's '95% HPD'
# to '95percent_hpd'. Both must be recognised, or re-importing an older results
# table would read its posterior columns back in as data.
_HPD_COLUMN = re.compile(r'^\d+(?:_\d+)?percent_hpd')

# Every calendar column is exported a second time in the chosen era, e.g.
# 'TPQ BP (BC/AD)' -> 'tpq_bp_bc_ad'. Reading those would double-count a column
# already present, and be wrong by 1950 into the bargain.
_ERA_SUFFIXES = ('_bc_ad', '_bce_ce')


def _is_result_only(key: str, has_row_type: bool) -> bool:
    if key == 'order':
        return True
    if key.startswith(_RESULT_ONLY_PREFIXES) or _HPD_COLUMN.match(key):
        return True
    if key.endswith(_ERA_SUFFIXES):
        return True
    # 'Depth' in the results export is a model element's nesting depth; in a
    # lab report the same header means depth_cm. A Row Type column is only ever
    # written by our own export, so it is what tells the two apart.
    return key == 'depth' and has_row_type


def normalise_header(raw: str) -> str:
    """
    A header cell reduced to a comparable key: lowercase, the symbols a
    spreadsheet actually contains spelled out, everything else collapsed to
    single underscores.

    The symbol substitutions are what let the app's own on-screen column
    names work as headers -- '± 1σ' is the heading in the dates table, and a
    user copying that table into a spreadsheet gets it verbatim.
    """
    text = (raw or '').strip().lower()
    for symbol, word in (
        ('σ', 'sigma'),      # σ
        ('±', 'error'),      # ±
        ('Δ', 'delta'),      # Δ
        ('δ', 'delta'),      # δ
        ('%', 'percent'),
    ):
        text = text.replace(symbol, word)
    text = re.sub(r'[^a-z0-9]+', '_', text)
    return text.strip('_')


def _resolve_columns(fieldnames: List[str]) -> tuple:
    """
    Map the file's headers onto canonical field names.

    Returns (mapping, unknown, duplicated): mapping is header -> canonical
    field. A header matching nothing is reported rather than ignored -- a
    misspelled 'eror' column would otherwise look like a file that simply
    had no errors in it, and every date would import with the wrong
    uncertainty.
    """
    by_alias = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            by_alias[alias] = canonical

    keys = [normalise_header(f) for f in fieldnames if f is not None]
    has_row_type = 'row_type' in keys

    mapping: Dict[str, str] = {}
    unknown: List[str] = []
    duplicated: List[str] = []
    claimed: Dict[str, str] = {}

    for raw in fieldnames:
        if raw is None:
            continue
        key = normalise_header(raw)
        if not key:
            continue
        if _is_result_only(key, has_row_type):
            continue
        canonical = by_alias.get(key)
        if canonical is None:
            unknown.append(raw.strip())
            continue
        if canonical in claimed:
            duplicated.append(f"'{claimed[canonical]}' and '{raw.strip()}' both mean {canonical}")
            continue
        mapping[raw] = canonical
        claimed[canonical] = raw.strip()

    return mapping, unknown, duplicated


# --------------------------------------------------------------------------
# Cell parsing
# --------------------------------------------------------------------------

def _clean(value: Any) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _number(value: str, field_name: str, line: int, errors: List[str]) -> Optional[float]:
    """
    A numeric cell, tolerating the things spreadsheets put in them: thousands
    separators, a stray '±' or '+/-' prefix, and '~' for an approximate value.
    Returns None for an empty cell; appends an error and returns None for a
    cell that is not a number, so parsing continues and the user sees every
    bad cell at once.
    """
    text = _clean(value)
    if not text:
        return None
    cleaned = text.replace(',', '').replace('±', '').replace('+/-', '')
    cleaned = cleaned.replace('~', '').replace('−', '-').strip()
    try:
        return float(cleaned)
    except ValueError:
        errors.append(f"Row {line}: {field_name} is not a number ('{text}')")
        return None


def _curve(value: str, line: int, errors: List[str], owner_id: str) -> Optional[str]:
    """
    A curve cell as one of the ids the calibration engine loads. Anything
    else is an error naming what was found: falling back to IntCal20 for an
    unrecognised curve is the exact failure schemas/sample.py's
    VALID_CURVE_IDS exists to prevent -- a real result, computed against the
    wrong curve.

    Spellings are matched by the registry, which holds each curve's aliases
    alongside the curve itself, so a curve added there is understood here
    without this function being touched.
    """
    text = _clean(value)
    if not text:
        return None
    resolved = curve_registry.resolve_alias(text, owner_id)
    if resolved is not None:
        return resolved
    # Scoped to the importer: a file naming a curve another account imported
    # must fail exactly as one naming a curve nobody has, and the list offered
    # back must be the reader's own.
    available = ', '.join(spec.name for spec in curve_registry.list_specs(owner_id))
    errors.append(
        f"Row {line}: unknown calibration curve '{text}'. This app calibrates "
        f"against {available}; a curve it does not have cannot be substituted "
        "for one it does."
    )
    return None


def _phase_name(value: str) -> Optional[str]:
    """
    A context/phase name. A value holding a '>' is read as a structure PATH
    and reduced to its last segment, which is how the app's own results
    export writes a date's position ('Nenthorn > Quarry > Fire_Feature_J') --
    the innermost element is the phase the date actually sits in.
    """
    text = _clean(value)
    if not text:
        return None
    if '>' in text:
        text = text.split('>')[-1].strip()
    return text or None


def _outlier_model(value: str, line: int, errors: List[str]) -> Optional[str]:
    text = _clean(value).lower()
    if not text:
        return None
    if text in ('general', 'charcoal'):
        return text
    errors.append(
        f"Row {line}: unknown outlier model '{_clean(value)}' "
        "(expected 'general' or 'charcoal')"
    )
    return None


def _outlier_prior(value: str, line: int, errors: List[str]) -> Optional[float]:
    """
    An outlier prior as a probability. Accepts a percentage ('5%') as well as
    a fraction, because both spellings turn up in published models and 5 is
    unambiguous as a percentage -- a prior of 5.0 is not a thing.
    """
    text = _clean(value)
    if not text:
        return None
    percent = text.endswith('%')
    number = _number(text.rstrip('%'), 'outlier prior', line, errors)
    if number is None:
        return None
    if percent:
        number = number / 100.0
    if number > 1.0:
        errors.append(
            f"Row {line}: outlier prior {text} is above 1. Give it as a "
            "probability (0.05) or a percentage with a % sign (5%)."
        )
        return None
    return number


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------

def _sniff_delimiter(text: str) -> str:
    """
    Comma, tab or semicolon, whichever the header line actually uses.

    csv.Sniffer is not used here: it inspects the whole sample and is easily
    misled by a delimiter appearing inside quoted notes, where the header line
    alone is unambiguous. Semicolons matter because that is what a European
    locale's Excel writes.
    """
    header = text.split('\n', 1)[0]
    counts = {d: header.count(d) for d in (',', '\t', ';')}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else ','


def parse_csv(text: str, owner_id: str, filename: Optional[str] = None) -> ImportResult:
    """
    Read a delimited table of dates.

    Header-driven and order-independent -- see COLUMN_ALIASES for the
    spellings understood. Only sample_name, age_bp and error_1sigma are
    required; everything else takes the same default a hand-entered date
    would.

    A `phase` column is what turns a list of dates into a model: dates
    sharing a phase name are grouped, and a starting structure is built
    around them (see _tree_from_phases, which reports what it assumed).
    Without one, the file is a list of independent dates.
    """
    result = ImportResult(source_format='csv')
    result.analysis_name = _name_from_filename(filename)

    text = text.lstrip('﻿')
    if not text.strip():
        result.errors.append('The file is empty.')
        return result

    delimiter = _sniff_delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        result.errors.append('The file has no header row.')
        return result

    mapping, unknown, duplicated = _resolve_columns(reader.fieldnames)
    canonical = set(mapping.values())

    missing = [c for c in REQUIRED_COLUMNS if c not in canonical]
    if missing:
        result.errors.append(
            'The file is missing required column(s): '
            + ', '.join(missing)
            + '. Found: '
            + (', '.join(f"'{f.strip()}'" for f in reader.fieldnames if f and f.strip()) or 'nothing')
            + '. Download the template for the column names this reads.'
        )
        return result.refuse()

    for problem in duplicated:
        result.errors.append(f'Two columns mean the same thing: {problem}.')
    if unknown:
        result.warnings.append(
            'These columns were not recognised and were ignored: '
            + ', '.join(f"'{u}'" for u in unknown)
            + '. Check for a misspelling -- a column this importer does not '
            'recognise is one whose values are not being used.'
        )

    if delimiter != ',':
        result.notes.append(
            f"Read as {'tab' if delimiter == chr(9) else 'semicolon'}-separated."
        )

    has_row_type = 'row_type' in canonical
    skipped_structural = 0
    rows: List[Dict[str, Any]] = []

    for offset, raw_row in enumerate(reader):
        # +2: the header is line 1 and enumerate starts at 0.
        line = offset + 2
        row = {
            mapping[header]: value
            for header, value in raw_row.items()
            if header in mapping
        }

        if not any(_clean(v) for v in row.values()):
            continue

        # The app's results export has a row per model ELEMENT as well as per
        # date, and a sequence has no age to calibrate. Skipped rather than
        # reported as fourteen rows with a missing age.
        if has_row_type and _clean(row.get('row_type')).lower() not in ('date', ''):
            skipped_structural += 1
            continue

        rows.append({'line': line, 'values': row})

    if len(rows) > MAX_DATES:
        result.errors.append(
            f'The file holds {len(rows)} dates; the limit is {MAX_DATES} per import.'
        )
        return result.refuse()

    if skipped_structural:
        result.notes.append(
            f'Skipped {skipped_structural} structural row(s) '
            '(sequences, phases and boundaries) -- only date rows carry a '
            'measurement to import.'
        )

    for row in rows:
        parsed = _row_to_date(row['values'], row['line'], result.errors, owner_id)
        if parsed is not None:
            result.dates.append(parsed)

    if not result.dates and not result.errors:
        result.errors.append('No dates found in the file.')
        return result.refuse()

    _finish(result)
    return result.refuse()


def _row_to_date(values: Dict[str, Any], line: int, errors: List[str],
                 owner_id: str) -> Optional[Dict[str, Any]]:
    """One row as a date dict, or None with errors appended."""
    before = len(errors)

    name = _clean(values.get('sample_name'))
    if not name:
        errors.append(f'Row {line}: sample name is blank')

    age = _number(values.get('age_bp'), 'age BP', line, errors)
    if age is None and _clean(values.get('age_bp')) == '':
        errors.append(f'Row {line}: age BP is missing')

    error = _number(values.get('error_1sigma'), '1-sigma error', line, errors)
    if error is None and _clean(values.get('error_1sigma')) == '':
        errors.append(f'Row {line}: 1-sigma error is missing')

    curve = _curve(values.get('curve_id'), line, errors, owner_id)
    sample_type = _clean(values.get('sample_type')).lower() or None
    if sample_type is not None and sample_type not in ('terrestrial', 'marine'):
        errors.append(
            f"Row {line}: sample type '{_clean(values.get('sample_type'))}' "
            "should be 'terrestrial' or 'marine'"
        )
        sample_type = None

    date = {
        'sample_name': name,
        'age_bp': age,
        'error_1sigma': error,
        'material_type': _clean(values.get('material_type')) or 'unknown',
        'curve_id': curve or 'intcal20',
        'sample_type': sample_type,
        'delta_r': _number(values.get('delta_r'), 'delta R', line, errors),
        'delta_r_error': _number(values.get('delta_r_error'), 'delta R error', line, errors),
        'tpq_age_bp': _number(values.get('tpq_age_bp'), 'TPQ', line, errors),
        'taq_age_bp': _number(values.get('taq_age_bp'), 'TAQ', line, errors),
        'depth_cm': _number(values.get('depth_cm'), 'depth', line, errors),
        'location': _clean(values.get('location')) or None,
        'notes': _clean(values.get('notes')) or None,
        'outlier_model': _outlier_model(values.get('outlier_model'), line, errors) or 'general',
        'outlier_prior': _outlier_prior(values.get('outlier_prior'), line, errors) or 0.0,
        'phase_name': _phase_name(values.get('phase')),
    }

    if len(errors) != before:
        return None
    return date


def _name_from_filename(filename: Optional[str]) -> Optional[str]:
    if not filename:
        return None
    stem = re.sub(r'\.(csv|tsv|txt|oxcal)$', '', filename.strip(), flags=re.I)
    stem = stem.rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
    return stem.strip() or None


# --------------------------------------------------------------------------
# Validation and structure
# --------------------------------------------------------------------------

def _finish(result: ImportResult) -> None:
    """
    Assign ids, validate every date against the app's own measurement schema,
    and build a structure if the dates carry phase names.
    """
    import uuid

    for date in result.dates:
        date['id'] = f"d-{uuid.uuid4().hex[:12]}"

    _validate_dates(result)
    if result.errors:
        return

    _tree_from_phases(result)


def _validate_dates(result: ImportResult) -> None:
    """
    Every parsed date through schemas.sample.BayesianMeasurementInput, so an
    uploaded date meets the bounds a typed one does and is refused in the same
    words. Done here rather than at the endpoint the dates are posted to, so a
    bad value is reported against its own row while the file is still open.
    """
    from pydantic import ValidationError
    from app.schemas.sample import BayesianMeasurementInput

    duplicate_names: Dict[str, int] = {}
    for index, date in enumerate(result.dates):
        payload = {k: v for k, v in date.items() if k != 'phase_name'}
        try:
            BayesianMeasurementInput(**payload)
        except ValidationError as exc:
            for error in exc.errors():
                field_name = '.'.join(str(p) for p in error['loc']) or 'row'
                label = date.get('sample_name') or f'row {index + 1}'
                result.errors.append(f"'{label}': {field_name} -- {error['msg']}")
        name = (date.get('sample_name') or '').strip().lower()
        duplicate_names[name] = duplicate_names.get(name, 0) + 1

    repeated = sorted(n for n, count in duplicate_names.items() if count > 1 and n)
    if repeated:
        # Not an error -- a site really can have two dates from one sample,
        # and OxCal allows repeated labels -- but it is also the shape of a
        # copy-paste accident, and the two are indistinguishable from here.
        result.warnings.append(
            'These sample names appear more than once: '
            + ', '.join(repeated)
            + '. They import as separate dates -- check the file does not '
            'repeat a row.'
        )


def _tree_from_phases(result: ImportResult) -> None:
    """
    Build a starting model structure from a CSV's phase names: one Sequence
    holding a Start Boundary, every phase, and an End Boundary, with the phases
    nested inside one unordered Phase.

    Unordered because a CSV says which dates go together and NOTHING about
    which group came first -- a Sequence of the phases would read an ordering
    out of the row order. So all the dates fall between one pair of transitions
    and the contexts are unordered: OxCal's ordinary single-phase model, and
    the weakest structure that still uses the grouping the file gives. Ordering
    them is then the user's decision in the Model Structure editor.
    """
    import uuid

    named = [d for d in result.dates if d.get('phase_name')]
    if not named:
        if result.dates:
            result.notes.append(
                'No phase/context column, so the dates come in with no model '
                'structure -- they will be calibrated independently. Add a '
                '"phase" column, or group them in the Model Structure editor, '
                'to model them together.'
            )
        return

    unnamed = [d for d in result.dates if not d.get('phase_name')]

    def element(etype: str, name: str, children=None) -> Dict[str, Any]:
        return {
            'id': f"e-{uuid.uuid4().hex[:12]}",
            'element_type': etype,
            'name': name,
            'tpq_age_bp': None,
            'taq_age_bp': None,
            'children': children or [],
        }

    # First-appearance order: stable and matching how the file reads, without
    # being claimed as chronological.
    order: List[str] = []
    for date in result.dates:
        phase = date.get('phase_name')
        if phase and phase not in order:
            order.append(phase)

    phases = {name: element('phase', name) for name in order}

    # A blank phase cell gets a phase of its own rather than being dropped
    # into a named one. Held separately rather than under a reserved key, so it
    # cannot collide with a file that really calls a context "Unassigned dates".
    catch_all = None
    if unnamed:
        catch_all = element('phase', 'Unassigned dates')
        result.warnings.append(
            f'{len(unnamed)} date(s) have no phase/context value. They are '
            'grouped under "Unassigned dates" so none is lost -- move them in '
            'the Model Structure editor.'
        )

    for date in result.dates:
        phase = date.get('phase_name')
        target = phases[phase] if phase else catch_all
        date['element_id'] = target['id']
        result.element_assignments[date['id']] = target['id']

    children = [phases[name] for name in order]
    if catch_all is not None:
        children.append(catch_all)
    contexts = element('phase', 'Contexts', children)
    root = element('sequence', result.analysis_name or 'Imported model', [
        element('boundary', 'Start'),
        contexts,
        element('boundary', 'End'),
    ])
    result.model_tree = root

    result.notes.append(
        f'Built a starting structure from the phase column: {len(order)} '
        'phase(s) inside one Start/End pair. The phases are UNORDERED with '
        'respect to each other -- a CSV does not say which came first. Open '
        'Model Structure to order them or move the boundaries.'
    )


# --------------------------------------------------------------------------
# The template
# --------------------------------------------------------------------------

TEMPLATE_COLUMNS = [
    'sample_name', 'age_bp', 'error_1sigma', 'material_type', 'curve_id',
    'phase', 'delta_r', 'delta_r_error', 'outlier_model', 'outlier_prior',
    'tpq_age_bp', 'taq_age_bp', 'notes',
]

TEMPLATE_ROWS = [
    ['Wk-51189', '685', '25', 'charcoal', 'shcal20', 'Fire Feature J',
     '', '', 'general', '0.05', '', '', 'short-lived twig'],
    ['Wk-50104', '842', '21', 'charcoal', 'shcal20', 'Fire Feature J',
     '', '', 'charcoal', '1.0', '', '', 'old wood -- declared residual'],
    ['NZ-7756', '970', '47', 'marine shell', 'marine20', 'Midden 1',
     '-7', '45', 'general', '0.05', '', '', 'delta_r needs its error'],
]


def template_csv() -> str:
    """
    The blank-with-examples template offered for download.

    The three example rows each show something the column list alone cannot: a
    plain terrestrial date, a date DECLARED residual at charcoal prior 1.0, and
    a marine date whose delta_r carries its required uncertainty.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(TEMPLATE_COLUMNS)
    for row in TEMPLATE_ROWS:
        writer.writerow(row)
    return output.getvalue()


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

def parse_upload(filename: str, content: bytes, owner_id: str) -> ImportResult:
    """
    Parse an uploaded file, choosing the reader by extension and falling back
    on the content itself.

    Extension is only a hint: `.txt` names both exported tables and saved
    OxCal models, so a file whose first meaningful line looks like CQL2 is read
    as a model whatever it is called.
    """
    if len(content) > MAX_CONTENT_BYTES:
        result = ImportResult()
        result.errors.append(
            f'The file is {len(content) // 1024} KB; the limit is '
            f'{MAX_CONTENT_BYTES // 1024} KB. This importer reads lists of '
            'dates and OxCal model files, not data archives.'
        )
        return result

    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            # What Excel on Windows writes when it is not writing UTF-8.
            text = content.decode('cp1252')
        except UnicodeDecodeError:
            result = ImportResult()
            result.errors.append(
                'The file is not text this importer can read (it decoded as '
                'neither UTF-8 nor Windows-1252). If it is a spreadsheet, '
                'save it as CSV first.'
            )
            return result

    lower = (filename or '').lower()

    # An explicit extension is taken at its word in BOTH directions. Sniffing
    # a .csv would let a notes cell reading "see Sequence(3) in the report"
    # send a perfectly good table to the model parser, which would then refuse
    # it as broken CQL2 -- a confusing failure with no obvious cause.
    if lower.endswith('.oxcal'):
        from app.services.oxcal_script import parse_oxcal
        return parse_oxcal(text, owner_id, filename)
    if lower.endswith(('.csv', '.tsv')):
        return parse_csv(text, owner_id, filename)

    # Anything else -- '.txt' above all, an ordinary name for both an exported
    # table and a saved OxCal model -- is decided by the content.
    if looks_like_cql2(text):
        from app.services.oxcal_script import parse_oxcal
        return parse_oxcal(text, owner_id, filename)
    return parse_csv(text, owner_id, filename)


def looks_like_cql2(text: str) -> bool:
    """
    Whether this is an OxCal model rather than a table. Keyed on the
    commands that only appear in CQL2, followed by an opening bracket --
    'Sequence' could be a column name, but 'Sequence(' could not.
    """
    stripped = re.sub(r'//[^\n]*', '', text)
    return bool(re.search(
        r'\b(Plot|Sequence|Phase|R_Date|Boundary|Outlier_Model|Curve)\s*\(',
        stripped,
    ))
