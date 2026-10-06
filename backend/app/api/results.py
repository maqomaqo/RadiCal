from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Analysis, RadiocarbonSample, AnalysisResult, ModelElement, SampleGroup, User
from app.api.auth import current_user, owned_or_404
from app.services.model_tree import rows_to_tree, flatten_structure
from app.services.calibration import SIGMA_1, SIGMA_2, get_curve
from app.services import date_format as date_format_service
from app.services import pdf_report
from app.services import intervals as intervals_service
from app.services import statistics
from typing import Dict, List, Optional
from decimal import Decimal, ROUND_HALF_EVEN
import io
import csv
import json

router = APIRouter()


# How precisely a coordinate may leave the app, as the project's setting spells
# it, mapped to decimal places. The names are the bare numbers rather than
# '0.01 degrees' so this table can read them; the degree sign and the
# approximate distance are the UI's business.
#
# This dict is the definition of the setting. schemas/project.py validates
# against these keys, so an unrecognised value cannot be stored, and
# _rounded_coordinate need not decide what to do with one.
COORDINATE_PRECISIONS: Dict[str, Optional[int]] = {
    'full': None,   # what was entered
    '0.01': 2,      # about 1.1 km
    '0.1': 1,       # about 11 km
    '1': 0,         # about 110 km
}


def _rounded_coordinate(value: Optional[float], precision: str) -> Optional[float]:
    """
    A coordinate as it is allowed to leave this project.

    Site coordinates are restricted data under many heritage bodies',
    landowners' and Indigenous data-governance frameworks. Full precision is
    what is STORED -- degrading data at rest helps nobody and cannot be undone
    -- so this is where a project's choice is applied, at the boundary, to
    every export and every rendered figure alike.

    Round-half-to-even on the decimal the user actually typed, via Decimal
    rather than round(): two exports of the same project must agree with each
    other digit for digit, and binary floating point does not promise that for
    a value like 54.125 the way an exact decimal does.
    """
    if value is None:
        return None
    places = COORDINATE_PRECISIONS.get(precision)
    if places is None:
        return value
    quantum = Decimal(1).scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_EVEN))


def _export_precision(analysis) -> str:
    """
    The precision governing this analysis's downloads, which belongs to the
    PROJECT: an analysis is one question asked of a site's dates, and the
    sensitivity of the site does not vary between them. An analysis with no
    project falls back to full, which is the column's default anyway.
    """
    project = getattr(analysis, 'project', None)
    return (getattr(project, 'coordinate_precision', None) or 'full')


def _format_hpd_ranges(ranges: Optional[List[Dict]], fallback_min, fallback_max) -> str:
    """
    Render an HPD breakdown as a single spreadsheet-friendly string, e.g.
    "3234-3081 (68.0%)" or, for a multimodal posterior,
    "3329-3294 (9.4%); 3253-3103 (77.6%); 3098-3074 (8.0%)".

    Falls back to the plain min/max envelope columns for rows that predate
    the multi-range breakdown.
    """
    if ranges:
        return '; '.join(
            f"{r['max']:.0f}-{r['min']:.0f} ({r['probability_percent']:.1f}%)"
            for r in ranges
        )
    if fallback_min is not None and fallback_max is not None:
        return f"{fallback_max:.0f}-{fallback_min:.0f}"
    return ''


# The durations-and-differences block, as spreadsheet rows: a second table in
# the same file, since it belongs to the same run and is short. Its own header
# because it shares no column with the results table -- every figure here is a
# length in years, so none may be converted to BC/AD.
INTERVAL_HEADER = [
    'Row Type', 'Name', 'Compared With', 'Mean (yr)', 'Median (yr)', 'Std (yr)',
    '68.3% Range (yr)', '95.4% Range (yr)', 'Overlap Probability', 'Overlap',
    'Dated Events',
]

INTERVAL_LABELS = {
    'sequence_duration': 'Sequence Duration',
    'phase_duration': 'Phase Duration',
    'hiatus_duration': 'Hiatus Duration',
    'sample_duration': 'Sample Duration',
    'phase_difference': 'Phase Difference',
    'date_difference': 'Date Difference',
}


def _years(value: Optional[float]) -> str:
    return '' if value is None else f"{value:.0f}"


def _years_ranges(ranges: Optional[List[Dict]]) -> str:
    """
    A duration's HPD as one cell, e.g. "0-125 (95.4%)". Written
    smallest-first, unlike the oldest-first calendar ranges of
    _format_hpd_ranges: a duration is a length, and lengths read upwards.
    """
    if not ranges:
        return ''
    return '; '.join(
        f"{r['min']:.0f}-{r['max']:.0f} ({r['probability_percent']:.1f}%)"
        for r in sorted(ranges, key=lambda r: r['min'])
    )


def _interval_rows(intervals: Optional[Dict]) -> List[List]:
    """Every duration and difference as a row, durations first."""
    if not intervals:
        return []
    rows = []
    for record in (intervals.get('durations') or []):
        years = record.get('years') or {}
        rows.append([
            INTERVAL_LABELS.get(record.get('kind'), record.get('kind')),
            record.get('name') or '', '',
            _years(years.get('mean')), _years(years.get('median')),
            _years(years.get('std')),
            _years_ranges(years.get('hpd_68_ranges')),
            _years_ranges(years.get('hpd_95_ranges')),
            '', '', record.get('dates') if record.get('dates') else '',
        ])
    for record in (intervals.get('differences') or []):
        years = record.get('years') or {}
        overlap = record.get('overlap')
        label = INTERVAL_LABELS.get(record.get('kind'), record.get('kind'))
        names = [record.get('from_name') or '', record.get('to_name') or '']
        if record.get('shared_boundary'):
            # Zero years and zero overlap, both by construction, so the text
            # is printed in place of the figures. Bare zeroes would read as a
            # measured separation.
            rows.append([label] + names + ['', '', '', '', '',
                                           '', intervals_service.SHARED_BOUNDARY_TEXT, ''])
            continue
        rows.append([
            label, *names,
            _years(years.get('mean')), _years(years.get('median')),
            _years(years.get('std')),
            _years_ranges(years.get('hpd_68_ranges')),
            _years_ranges(years.get('hpd_95_ranges')),
            '' if overlap is None else f"{overlap:.3f}",
            record.get('severity') or '', '',
        ])
    return rows


# The age-depth band, as two more tables. Kept apart from INTERVAL_HEADER
# because these rows are not lengths in years: the level rows are CALENDAR
# AGES and the accumulation rows are years per unit depth, and the file's rule
# is that only calendar-age columns gain a BC/AD counterpart. Written only
# when the run has an age-depth model in it, so a plain calibration does not
# grow an empty accumulation-rate section.
AGE_DEPTH_HEADER = [
    'Row Type', 'Sequence', 'Level', 'Depth', 'Dated',
    'Median BP', '68.3% Band BP', '95.4% Band BP',
]
ACCUMULATION_HEADER = [
    'Row Type', 'Sequence', 'From', 'To', 'Depth Gap',
    'Elapsed Median (yr)', 'Elapsed 95.4% (yr)',
    'Rate (yr per unit depth)', 'Rate 68.3%',
]
# The one calendar-age column of the level table that gains a formatted
# counterpart, on the same rule as _FORMATTED_COLUMNS above.
_AGE_DEPTH_FORMATTED = ['Median BP', '68.3% Band BP', '95.4% Band BP']


def _band_cell(band: Optional[List[float]]) -> str:
    """A band as one cell, oldest first -- the order every calendar range in
    this file is written in."""
    if not band:
        return ''
    return f"{max(band):.0f}-{min(band):.0f}"


def _age_depth_rows(intervals: Optional[Dict], date_format: str) -> List[Dict]:
    """
    The age-depth section as (header, rows) blocks, or [] when the run has no
    deposition sequence in it.
    """
    records = (intervals or {}).get('age_depth') or []
    if not records:
        return []

    fmt = date_format_service
    era = fmt.ERA_LABELS.get(date_format)
    level_header = list(AGE_DEPTH_HEADER)
    if era:
        level_header += [f'{name} ({era})' for name in _AGE_DEPTH_FORMATTED]

    level_rows: List[List] = []
    rate_rows: List[List] = []
    for record in records:
        units = record.get('depth_units') or 'cm'
        for level in record.get('levels') or []:
            row = [
                'Age-Depth Level', record.get('name') or '',
                level.get('name') or '',
                f"{level['depth']:g} {units}" if level.get('depth') is not None else '',
                'yes' if level.get('dated') else 'no',
                f"{level['median']:.0f}" if level.get('median') is not None else '',
                _band_cell(level.get('band_68')),
                _band_cell(level.get('band_95')),
            ]
            if era:
                band_68 = level.get('band_68') or [None, None]
                band_95 = level.get('band_95') or [None, None]
                row += [
                    fmt.format_age(level.get('median'), date_format),
                    fmt.format_range(band_68[0], band_68[1], date_format),
                    fmt.format_range(band_95[0], band_95[1], date_format),
                ]
            level_rows.append(row)

        for step in record.get('accumulation') or []:
            years = step.get('years') or {}
            band = step.get('rate_band_68') or []
            rate_rows.append([
                'Accumulation Rate', record.get('name') or '',
                f"{step['from_depth']:g} {units}",
                f"{step['to_depth']:g} {units}",
                f"{step['depth_gap']:g} {units}",
                _years(years.get('median')),
                _years_ranges(years.get('hpd_95_ranges')),
                f"{step['rate_median']:.2f}" if step.get('rate_median') is not None else '',
                f"{min(band):.2f}-{max(band):.2f}" if band else '',
            ])

    blocks = []
    if level_rows:
        blocks.append({'header': level_header, 'rows': level_rows})
    if rate_rows:
        blocks.append({'header': list(ACCUMULATION_HEADER), 'rows': rate_rows})
    return blocks


def _unmodelled_ranges(sample, analysis_type: str) -> Dict:
    """
    What this date calibrates to on its own, with no stratigraphic constraint
    -- the "unmodelled vs modelled" comparison shown beside each date.

    Computed rather than stored: it is a cheap analytic calibration
    determined by the sample's current age/error/curve, so it cannot fall out
    of step with the measurement. Reported at the same two confidence levels
    as the modelled posterior.

    Only meaningful for a Bayesian analysis; for a plain calibration the
    modelled figures already ARE the unmodelled result.
    """
    blank = {'unmodelled_hpd_68_ranges': None, 'unmodelled_hpd_95_ranges': None}
    if analysis_type != 'bayesian' or sample is None:
        return blank
    try:
        curve = get_curve(sample.curve_id)
        cal_ages, probs = curve.calibrate(
            sample.age_bp, sample.error_1sigma, sample.delta_r, sample.delta_r_error,
        )
        return {
            'unmodelled_hpd_68_ranges': curve.get_hpd_ranges(cal_ages, probs, SIGMA_1),
            'unmodelled_hpd_95_ranges': curve.get_hpd_ranges(cal_ages, probs, SIGMA_2),
        }
    except Exception:
        # An unresolvable curve should not fail the whole export; the
        # modelled figures are unaffected and this column stays empty.
        return blank


def _gather_export_rows(analysis_id: str, db: Session) -> Dict:
    """
    Shared data assembly for every export format. Joins each AnalysisResult
    back to its RadiocarbonSample so the export reflects what was measured,
    not just the posterior, and raises 404 up front so a bad analysis_id
    fails the same way everywhere instead of returning an empty file.

    The unmodelled calibration is derived here too, so the CSV and the JSON
    report the same figure by construction.
    """
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    results = db.query(AnalysisResult).filter(AnalysisResult.analysis_id == analysis_id).all()
    samples_by_id = {
        s.id: s for s in db.query(RadiocarbonSample).filter(RadiocarbonSample.analysis_id == analysis_id).all()
    }

    # Applied HERE, in the one assembly all three downloads are built from,
    # rather than in each of them: a coordinate below the project's declared
    # precision then has no path out of the app at all, and the CSV, the JSON
    # and the PDF cannot disagree about a position any more than they can
    # about a posterior.
    precision = _export_precision(analysis)

    rows = []
    for r in results:
        sample = samples_by_id.get(r.sample_id)
        rows.append({
            'sample_id': r.sample_id,
            'sample_name': sample.sample_name if sample else None,
            'age_bp': sample.age_bp if sample else None,
            'error_1sigma': sample.error_1sigma if sample else None,
            'material_type': sample.material_type if sample else None,
            'sample_type': sample.sample_type if sample else None,
            'curve_id': sample.curve_id if sample else None,
            'latitude': _rounded_coordinate(
                sample.latitude if sample else None, precision),
            'longitude': _rounded_coordinate(
                sample.longitude if sample else None, precision),
            'calibrated_age_bp': r.calibrated_age_bp,
            'posterior_mean': r.posterior_mean,
            'posterior_median': r.posterior_median,
            'posterior_std': r.posterior_std,
            'hpd_68_min': r.hpd_68_min,
            'hpd_68_max': r.hpd_68_max,
            'hpd_95_min': r.hpd_95_min,
            'hpd_95_max': r.hpd_95_max,
            'hpd_68_ranges': r.hpd_68_ranges,
            'hpd_95_ranges': r.hpd_95_ranges,
            'agreement_index': r.agreement_index,
            'outlier_probability': r.outlier_probability,
            **_unmodelled_ranges(sample, analysis.analysis_type),
        })

    return {'analysis': analysis, 'rows': rows, 'samples_by_id': samples_by_id,
            'coordinate_precision': precision}


def _structure_rows(analysis_id: str, db: Session, export_rows: List[Dict],
                    samples_by_id: Dict) -> List[Dict]:
    """
    The analysis's model structure in reading order, with each date's
    exported result attached to its row.

    Read from the stored ModelElement rows rather than migrating on the fly:
    an export is a GET and should not write. An analysis with no stored tree
    exports without structure (see _build_csv) and gains one when next opened
    or re-run.
    """
    element_rows = db.query(ModelElement).filter(
        ModelElement.analysis_id == analysis_id
    ).all()
    tree = rows_to_tree(element_rows)
    if tree is None:
        return []

    measurements = [
        {
            'id': s.id,
            'sample_name': s.sample_name,
            'element_id': s.element_id,
            'group_id': s.group_id,
            'tpq_age_bp': s.tpq_age_bp,
            'taq_age_bp': s.taq_age_bp,
        }
        for s in samples_by_id.values()
    ]
    results_by_sample = {r['sample_id']: r for r in export_rows}

    structure = flatten_structure(tree, measurements)
    for row in structure:
        row['result'] = results_by_sample.get(row['sample']['id']) if row['sample'] else None
    return structure


CSV_HEADER = [
    'Order', 'Row Type', 'Depth', 'Structure Path', 'Name',
    'Age BP', '1-Sigma Error', 'Material', 'Sample Type', 'Curve',
    'Unmodelled 95.4% HPD', 'Posterior Mean', 'Posterior Median', 'Posterior Std',
    '68.3% HPD', '95.4% HPD', 'Agreement %', 'Outlier %', 'TPQ BP', 'TAQ BP',
    # Appended, beside the age limiters, rather than filed with the measured
    # columns they belong with: everything before this point has held its
    # position since the export existed, and a spreadsheet or script reading
    # this file by column number should not be moved by a new input. Decimal
    # degrees at the project's declared precision, spelled the way the
    # importer reads them back -- this file is an accepted import format, so a
    # table corrected in a spreadsheet has to return intact.
    'Latitude', 'Longitude',
]

# The calendar-age columns, in the order their formatted counterparts are
# appended. Everything else is left alone: Age BP is a radiocarbon age, and
# 1-Sigma Error and Posterior Std are durations, so none may become BC/AD.
_FORMATTED_COLUMNS = [
    'Unmodelled 95.4% HPD', 'Posterior Mean', 'Posterior Median',
    '68.3% HPD', '95.4% HPD', 'TPQ BP', 'TAQ BP',
]


def _csv_header(date_format: str) -> List[str]:
    """
    The header, with a formatted counterpart for each calendar-age column
    when the chosen format is not cal BP. Nothing is added for cal BP, where
    the extra columns would only restate the numbers with a suffix.
    """
    era = date_format_service.ERA_LABELS.get(date_format)
    if not era:
        return list(CSV_HEADER)
    return list(CSV_HEADER) + [f"{name} ({era})" for name in _FORMATTED_COLUMNS]


def _measurement_cells(row: Optional[Dict]) -> List:
    """The measured half of a date row; blank for a structural row."""
    if not row:
        return ['', '', '', '', '']
    return [
        row['age_bp'] if row['age_bp'] is not None else '',
        row['error_1sigma'] if row['error_1sigma'] is not None else '',
        row['material_type'] or '',
        row['sample_type'] or '',
        row['curve_id'] or '',
    ]


def _coordinate_cells(row: Optional[Dict]) -> List:
    """
    Where the date came from; blank for a structural row, since a phase has no
    position of its own.

    Already rounded to the project's precision by _gather_export_rows, so
    there is nothing to decide here -- which is the point of rounding in the
    assembly rather than in each writer.
    """
    if not row:
        return ['', '']
    return [
        row['latitude'] if row.get('latitude') is not None else '',
        row['longitude'] if row.get('longitude') is not None else '',
    ]


def _unmodelled_cell(row: Optional[Dict]) -> str:
    """
    The unmodelled calibration for the CSV, at 95% to match the file's own
    "95% HPD" column, read from what _gather_export_rows derived. The JSON
    carries the 68% breakdown too; a column per level does not pay for itself
    in a spreadsheet.
    """
    if not row:
        return ''
    return _format_hpd_ranges(row.get('unmodelled_hpd_95_ranges'), None, None)


def _posterior_cells(row: Optional[Dict]) -> List:
    """
    The modelled half. Dates and boundaries are both modelled parameters with
    a posterior, so they report through the same columns.
    """
    if not row:
        return ['', '', '', '', '', '', '']
    agreement = row.get('agreement_index')
    outlier = row.get('outlier_probability')
    return [
        f"{row['posterior_mean']:.1f}" if row.get('posterior_mean') is not None else '',
        f"{row['posterior_median']:.1f}" if row.get('posterior_median') is not None else '',
        f"{row['posterior_std']:.1f}" if row.get('posterior_std') is not None else '',
        _format_hpd_ranges(row.get('hpd_68_ranges'), row.get('hpd_68_min'), row.get('hpd_68_max')),
        _format_hpd_ranges(row.get('hpd_95_ranges'), row.get('hpd_95_min'), row.get('hpd_95_max')),
        # Blank rather than 0: a boundary has no unmodelled counterpart to
        # agree with, and a plain calibration has no model.
        f"{agreement:.1f}" if agreement is not None else '',
        # Blank when no outlier model was applied, which is not the same as
        # a zero probability.
        f"{100 * outlier:.1f}" if outlier is not None else '',
    ]


def _formatted_cells(
    result: Optional[Dict],
    tpq_age_bp: Optional[float],
    taq_age_bp: Optional[float],
    date_format: str,
) -> List:
    """
    The same calendar ages in the user's reporting format, alongside the cal
    BP columns rather than replacing them: "1247 BC" is the readable form, but
    the file has to stay sortable and computable.

    Order must match _FORMATTED_COLUMNS.
    """
    fmt = date_format_service
    if not result:
        result = {}
    return [
        fmt.format_hpd_ranges(result.get('unmodelled_hpd_95_ranges'), date_format),
        fmt.format_age(result.get('posterior_mean'), date_format),
        fmt.format_age(result.get('posterior_median'), date_format),
        fmt.format_hpd_ranges(result.get('hpd_68_ranges'), date_format),
        fmt.format_hpd_ranges(result.get('hpd_95_ranges'), date_format),
        fmt.format_age(tpq_age_bp, date_format),
        fmt.format_age(taq_age_bp, date_format),
    ]


def _boundary_as_result(posterior: Dict) -> Dict:
    """A boundary's posterior reshaped like an export row, so it reports
    through the same cells a date does."""
    return {
        'posterior_mean': posterior.get('posterior_mean'),
        'posterior_median': posterior.get('posterior_median'),
        'posterior_std': posterior.get('posterior_std'),
        'hpd_68_ranges': posterior.get('hpd_68_ranges'),
        'hpd_95_ranges': posterior.get('hpd_95_ranges'),
        'hpd_68_min': (posterior.get('hpd_68') or [None, None])[0],
        'hpd_68_max': (posterior.get('hpd_68') or [None, None])[1],
        'hpd_95_min': (posterior.get('hpd_95') or [None, None])[0],
        'hpd_95_max': (posterior.get('hpd_95') or [None, None])[1],
        # A boundary is a modelled parameter with no measurement behind it,
        # so there is nothing for it to agree or disagree with, and nothing
        # for it to be an outlier from.
        'agreement_index': None,
        'outlier_probability': None,
    }


def _write_age_depth(writer, analysis, date_format: str) -> None:
    """
    The age-depth band and the accumulation rates, appended below the
    durations table in the same file.

    Written only when the run produced them, so a plain calibration does not
    grow an empty accumulation-rate section -- README's rule that a column
    appears only when there is something to put in it, applied to a whole
    table.
    """
    for block in _age_depth_rows(analysis.intervals, date_format):
        writer.writerow([])
        writer.writerow(block['header'])
        for row in block['rows']:
            writer.writerow(row)


# The kernel density estimates, as two more blocks: one row per estimate
# saying how it was made, then every curve, so a spreadsheet can redraw them.
# `Phase` names the phase a model's estimate is of, and is blank for a group
# calibration's one estimate of all its dates. The curve's age is a calendar
# age and gains a formatted counterpart on the file's usual rule; its density
# and band do not.
KDE_SUMMARY_HEADER = [
    'Row Type', 'Phase', 'Dates', 'Draws', 'Kernel Width Median (yr)',
    'Kernel Width 68.3% (yr)', 'Silverman Factor Median', 'Reflected at Boundaries',
]
KDE_CURVE_HEADER = ['Row Type', 'Phase', 'Age BP', 'Density', 'Density 1 Sigma']


def _write_kde(writer, analysis, date_format: str) -> None:
    """The kernel density estimates, when the run was asked for them."""
    records = (analysis.summaries or {}).get('kde') or []
    if not records:
        return
    writer.writerow([])
    writer.writerow(KDE_SUMMARY_HEADER)
    for kde in records:
        width = kde['bandwidth']
        writer.writerow([
            'KDE Summary', kde.get('name') or '', kde['dates'], kde['draws'],
            f"{width['median']:.0f}",
            f"{width['range_68'][0]:.0f}-{width['range_68'][1]:.0f}",
            f"{kde['factor']['median']:.3f}",
            'yes' if kde.get('reflected') else 'no',
        ])
    era = date_format_service.ERA_LABELS.get(date_format)
    writer.writerow([])
    writer.writerow(KDE_CURVE_HEADER + ([f'Age ({era})'] if era else []))
    for kde in records:
        for age, density, sd in kde['distribution']:
            row = ['KDE', kde.get('name') or '', f'{age:.0f}', f'{density:.6g}', f'{sd:.6g}']
            if era:
                row.append(date_format_service.format_age(age, date_format))
            writer.writerow(row)


# A group calibration's same-event test, as one more block, with the pooled
# age's calibration where the dates pass. The pooled range is a calendar age
# and gains a formatted counterpart on the file's usual rule.
SAME_EVENT_HEADER = [
    'Row Type', 'Dates', 'Pooled Age BP', 'Pooled Error', 'T', 'df',
    '5% Critical', 'One Event', 'Pooled 95.4% HPD', 'Note',
]


def _same_event_tests(analysis, samples) -> List[Dict]:
    """The analysis's same-event tests, from the call the Results page makes,
    so every export reports what the page does."""
    return statistics.same_event_for_samples(analysis.analysis_type, samples)


# Calendar ages a threshold may be set at, in cal BP: the span of the shipped
# curves, with room for a date after 1950.
THRESHOLD_RANGE_BP = (-100.0, 60000.0)


def _p_before(analysis_id: str, db: Session, threshold_bp: float) -> Dict:
    """
    P(before threshold_bp) for every date and modelled parameter of an
    analysis (services/statistics.p_before): `samples` by sample id, read from
    each result's stored distribution, and `elements` by element id for
    everything in the stored tree that carries a posterior of its own -- a
    Boundary, a wiggle match's reference, an undated level. The one assembly
    behind the results page's column and every export, so they cannot differ.
    """
    samples = {
        sample_id: statistics.p_before(distribution, threshold_bp)
        for sample_id, distribution in db.query(
            AnalysisResult.sample_id, AnalysisResult.probability_distribution
        ).filter(AnalysisResult.analysis_id == analysis_id).all()
    }
    elements: Dict[str, Optional[float]] = {}
    tree = rows_to_tree(
        db.query(ModelElement).filter(ModelElement.analysis_id == analysis_id).all())
    stack = [tree] if tree else []
    while stack:
        element = stack.pop()
        posterior = element.get('posterior') or {}
        if posterior.get('probability_distribution'):
            elements[element['id']] = statistics.p_before(
                posterior['probability_distribution'], threshold_bp)
        stack.extend(element.get('children') or [])
    return {'cal_bp': threshold_bp, 'samples': samples, 'elements': elements}


def _threshold(p_before_bp: Optional[float]) -> Optional[float]:
    """A threshold from a query string, refused when off every curve."""
    if p_before_bp is None:
        return None
    lo, hi = THRESHOLD_RANGE_BP
    if not lo <= p_before_bp <= hi:
        raise HTTPException(
            status_code=422,
            detail=f'A threshold must lie between {lo:.0f} and {hi:.0f} cal BP.')
    return float(p_before_bp)


def _p_before_header(threshold_bp: float, date_format: str) -> str:
    """The column's name, in the file's reporting format."""
    return f"P Before {date_format_service.format_age(threshold_bp, date_format)}"


def _p_before_cell(values: Optional[Dict], row: Dict) -> str:
    """A row's threshold probability as a spreadsheet number, or blank for a
    heading or anything with no posterior."""
    if values is None:
        return ''
    if row.get('kind') == 'date':
        sample = row.get('sample') or {}
        p = values['samples'].get(sample.get('id') if isinstance(sample, dict) else None)
    else:
        p = values['elements'].get(row.get('element_id'))
    # Six places, not the screen's three: a spreadsheet computes with it, and
    # 0.99996 written as 1.0000 would be a certainty the posterior never gave.
    return '' if p is None else f'{p:.6f}'


def _write_same_event(writer, tests: List[Dict], date_format: str) -> None:
    """The same-event test, when the analysis has one."""
    if not tests:
        return
    era = date_format_service.ERA_LABELS.get(date_format)
    writer.writerow([])
    writer.writerow(SAME_EVENT_HEADER + ([f'Pooled 95.4% HPD ({era})'] if era else []))
    for test in tests:
        tested = test['t'] is not None
        pooled = statistics.pooled_ranges_at(test, SIGMA_2)
        row = [
            'Same-Event Test', test['n'],
            f"{test['mean_bp']:.0f}" if tested else '',
            f"{test['error_bp']:.0f}" if tested else '',
            f"{test['t']:.2f}" if tested else '',
            test['df'],
            f"{test['critical']:.2f}" if tested else '',
            ('yes' if test['consistent'] else 'no') if tested else '',
            _format_hpd_ranges(pooled, None, None),
            test.get('refused') or '',
        ]
        if era:
            row.append(date_format_service.format_hpd_ranges(pooled, date_format) if pooled else '')
        writer.writerow(row)


def _write_intervals(writer, analysis) -> None:
    """
    The durations-and-differences table, appended below the results table with
    a blank line and its own header.

    Two tables in one file rather than a fourth download for a dozen rows
    belonging to the same run. A spreadsheet reads it as a second block; the
    blank row and repeated header say where it starts.
    """
    rows = _interval_rows(analysis.intervals)
    if not rows:
        return
    writer.writerow([])
    writer.writerow(INTERVAL_HEADER)
    for row in rows:
        writer.writerow(row)


def _build_csv(analysis_id: str, db: Session, date_format: Optional[str] = None,
               p_before_bp: Optional[float] = None) -> str:
    """
    One row per modelled entity -- sequences, phases, boundaries and dates --
    in the model's reading order, matching the results table row for row.

    `Order` is written out because a spreadsheet sort would destroy the
    structure the file exists to convey; re-sorting on it restores it.

    `date_format` is the reporting format the results page is showing. When it
    is not cal BP, each calendar-age column gains a formatted counterpart (see
    _csv_header); the cal BP columns are always present.

    `p_before_bp` is the threshold the results page's "P before" column is
    set to, when it is showing. It adds that column last, so no other column
    moves.
    """
    date_format = date_format_service.normalise(date_format)
    formatted = date_format in date_format_service.ERA_LABELS
    threshold = _threshold(p_before_bp)

    data = _gather_export_rows(analysis_id, db)
    analysis = data['analysis']
    p_values = _p_before(analysis_id, db, threshold) if threshold is not None else None

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(_csv_header(date_format)
                    + ([_p_before_header(threshold, date_format)] if p_values else []))

    structure = _structure_rows(analysis_id, db, data['rows'], data['samples_by_id'])
    same_event = _same_event_tests(analysis, data['samples_by_id'].values())

    if not data['rows'] and not structure:
        writer.writerow([f'No results for analysis "{analysis.name}" ({analysis.status})'])
        return output.getvalue()

    def extra(result, tpq, taq):
        return _formatted_cells(result, tpq, taq, date_format) if formatted else []

    def threshold_cell(row):
        return [_p_before_cell(p_values, row)] if p_values else []

    # No stored structure, so fall back to a flat list of dates rather than
    # coming back empty.
    if not structure:
        for order, row in enumerate(data['rows'], start=1):
            writer.writerow(
                [order, 'Date', 0, '', row['sample_name'] or row['sample_id'] or '']
                + _measurement_cells(row) + [_unmodelled_cell(row)]
                + _posterior_cells(row) + ['', '']
                + _coordinate_cells(row)
                + extra(row, None, None)
                + threshold_cell({'kind': 'date', 'sample': {'id': row['sample_id']}})
            )
        _write_intervals(writer, analysis)
        _write_age_depth(writer, analysis, date_format)
        _write_kde(writer, analysis, date_format)
        _write_same_event(writer, same_event, date_format)
        return output.getvalue()

    for order, row in enumerate(structure, start=1):
        posterior = row.get('posterior')
        unmodelled = ''
        result = None
        if row['kind'] == 'date':
            result = row.get('result')
            measured, modelled = _measurement_cells(result), _posterior_cells(result)
            unmodelled = _unmodelled_cell(result)
        elif posterior:
            # A boundary's posterior is summary-shaped, not a result row, and
            # has no unmodelled counterpart.
            result = _boundary_as_result(posterior)
            measured, modelled = _measurement_cells(None), _posterior_cells(result)
        else:
            measured, modelled = _measurement_cells(None), _posterior_cells(None)

        writer.writerow(
            [order, str(row['kind']).title(), row['depth'], row['path'], row['name']]
            + measured + [unmodelled] + modelled
            + [row['tpq_age_bp'] if row['tpq_age_bp'] is not None else '',
               row['taq_age_bp'] if row['taq_age_bp'] is not None else '']
            # A boundary's `result` is posterior-shaped and carries no
            # position, so this is blank for everything but a date.
            + _coordinate_cells(row.get('result') if row['kind'] == 'date' else None)
            + extra(result, row['tpq_age_bp'], row['taq_age_bp'])
            + threshold_cell(row)
        )

    _write_intervals(writer, analysis)
    _write_age_depth(writer, analysis, date_format)
    _write_kde(writer, analysis, date_format)
    _write_same_event(writer, same_event, date_format)
    return output.getvalue()


def _owned_analysis(analysis_id: str, db: Session, user: User) -> Analysis:
    """
    The analysis, if it is this user's.

    Every route here takes an id and hands it to an assembly function that
    looks the row up again. Calling this FIRST is what keeps the ownership
    check in one place rather than inside each of those -- and it is a 404 for
    someone else's analysis, not a 403, so an id cannot be used to discover
    that an analysis exists. See api/auth.owned_or_404.
    """
    return owned_or_404(
        db.query(Analysis).filter(Analysis.id == analysis_id).first(),
        user, 'That analysis')


@router.get("/results/{analysis_id}/table")
def export_table(analysis_id: str, date_format: Optional[str] = None,
                 p_before_bp: Optional[float] = None,
                 db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    """Export results as a CSV table. Same content as /export/csv, kept as a
    separate route for backwards compatibility."""
    _owned_analysis(analysis_id, db, user)
    csv_content = _build_csv(analysis_id, db, date_format, p_before_bp)
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=analysis-{analysis_id}-table.csv"}
    )


@router.get("/results/{analysis_id}/export/csv")
def export_csv(analysis_id: str, date_format: Optional[str] = None,
               p_before_bp: Optional[float] = None,
               db: Session = Depends(get_db),
               user: User = Depends(current_user)):
    """
    Export an analysis's results as CSV, in the model's own reading order.

    `date_format` (calBP / bcad / bce_ce) is the reporting format the results
    page is showing; when it is not cal BP each calendar-age column gains a
    formatted counterpart. An unrecognised value falls back to cal BP rather
    than failing the download.

    `p_before_bp` adds the results page's "P before" column, at that
    threshold in cal BP, when the page is showing it.
    """
    _owned_analysis(analysis_id, db, user)
    csv_content = _build_csv(analysis_id, db, date_format, p_before_bp)
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=analysis-{analysis_id}-results.csv"}
    )


def _posterior_summary(posterior: Dict) -> Dict:
    """
    A boundary's posterior without its raw MCMC draws.

    `posterior_samples` is tens of thousands of floats per boundary (see
    exact_chain._summarize) and would dwarf everything else in the file. The
    binned probability_distribution is kept, so a consumer can still plot the
    transition. _gather_export_rows never selects the raw samples for dates.
    """
    return {k: v for k, v in posterior.items() if k != 'posterior_samples'}


def _structure_payload(structure: List[Dict]) -> List[Dict]:
    """
    The structure rows as JSON, in the same reading order as the CSV: each
    date carrying its result, each boundary its posterior summary, and
    headings only their place in the tree.
    """
    payload = []
    for row in structure:
        entry = {
            'kind': row['kind'],
            'name': row['name'],
            'depth': row['depth'],
            'path': row['path'],
            'element_id': row['element_id'],
            'tpq_age_bp': row['tpq_age_bp'],
            'taq_age_bp': row['taq_age_bp'],
        }
        if row['kind'] == 'date':
            entry['sample_id'] = row['sample']['id'] if row['sample'] else None
            entry['result'] = row.get('result')
        elif row.get('posterior'):
            entry['posterior'] = _posterior_summary(row['posterior'])
        payload.append(entry)
    return payload


@router.get("/results/{analysis_id}/export/json")
def export_json(analysis_id: str, p_before_bp: Optional[float] = None,
                db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    """
    Export an analysis's results as JSON, with sample details merged into
    each result row.

    Two views of the same data: `results` is the flat per-date list, and
    `structure` is the same run in the model's own order, so a consumer can
    reproduce the model rather than only the dates. Both come from one
    assembly, so they cannot disagree; `structure` is empty for an analysis
    with no stored structure.

    The nested tree is not repeated here -- GET /api/analysis/{id} already
    returns model_tree.
    """
    _owned_analysis(analysis_id, db, user)
    threshold = _threshold(p_before_bp)
    data = _gather_export_rows(analysis_id, db)
    analysis = data['analysis']
    structure = _structure_rows(analysis_id, db, data['rows'], data['samples_by_id'])

    return {
        "success": True,
        "data": {
            "id": analysis.id,
            "name": analysis.name,
            "analysis_type": analysis.analysis_type,
            "status": analysis.status,
            "created_at": analysis.created_at.isoformat(),
            "project_id": analysis.project_id,
            "results": data['rows'],
            # Stated rather than left to be inferred: a consumer reading
            # 54.5, -5.6 cannot otherwise tell a site recorded to that
            # precision from one rounded to it on the way out.
            "coordinate_precision": data['coordinate_precision'],
            "structure": _structure_payload(structure),
            # Null unless the run was asked for them (Analysis.intervals).
            # Carried whole rather than flattened as the CSV flattens it,
            # since JSON has room for the full HPD breakdown.
            "intervals": analysis.intervals,
            # Null unless the run was asked for one (Analysis.summaries).
            "summaries": analysis.summaries,
            # How the run was computed: method, convergence, chains, and the
            # sampler's cross_check against a direct calculation. Null for an
            # analysis run before it was recorded.
            "solver": analysis.solver,
            # The same-event tests the Results page shows, from the same call.
            "same_event": _same_event_tests(
                analysis, data['samples_by_id'].values()),
            # The results page's "P before" column, when it was showing:
            # {cal_bp, samples: {id: p}, elements: {id: p}}. Null otherwise.
            "p_before": (_p_before(analysis_id, db, threshold)
                         if threshold is not None else None),
        }
    }


def _context_spans(analysis_id: str, db: Session) -> List[Dict]:
    """
    Each context's modelled start and end, for analyses whose model carries
    no explicit Boundary elements to report them instead. The raw draws are
    dropped on the way out, as in _posterior_summary.
    """
    groups = db.query(SampleGroup).filter(SampleGroup.analysis_id == analysis_id).all()
    spans = []
    for group in groups:
        if not (group.modeled_start and group.modeled_end):
            continue
        spans.append({
            'name': group.name,
            'start': _posterior_summary(group.modeled_start),
            'end': _posterior_summary(group.modeled_end),
        })
    return spans


@router.get("/results/{analysis_id}/p-before")
def get_p_before(analysis_id: str, cal_bp: float, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    """
    The probability each date and modelled parameter puts before `cal_bp`
    (services/statistics.p_before), for the results page's "P before"
    column. Read from the stored posteriors, so it answers for any analysis
    without a re-run.
    """
    _owned_analysis(analysis_id, db, user)
    return {"success": True, "data": _p_before(analysis_id, db, _threshold(cal_bp))}


@router.get("/results/{analysis_id}/pdf")
def export_pdf(analysis_id: str, date_format: Optional[str] = None,
               p_before_bp: Optional[float] = None,
                     db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    """
    Export an analysis as a printable PDF report: metadata, the curves and
    solver that produced it, the results table in reading order, each
    context's modelled span, and a posterior plot per modelled parameter.

    Built from the same assembly the CSV and JSON exports use, so the three
    downloads cannot report different numbers. `date_format` is carried the
    same way the CSV carries it, falling back to cal BP if unrecognised.

    Rendering is synchronous and costs a matplotlib figure per modelled
    parameter, so a large model takes a few seconds -- the thing to revisit
    if models grow.
    """
    _owned_analysis(analysis_id, db, user)
    threshold = _threshold(p_before_bp)
    data = _gather_export_rows(analysis_id, db)
    analysis = data['analysis']
    structure = _structure_rows(analysis_id, db, data['rows'], data['samples_by_id'])

    # Fetched here rather than through the shared assembly, which leaves the
    # column unselected: only the plots need a few hundred numbers per date.
    distributions = {
        sample_id: distribution
        for sample_id, distribution in db.query(
            AnalysisResult.sample_id, AnalysisResult.probability_distribution
        ).filter(AnalysisResult.analysis_id == analysis_id).all()
    }

    pdf_bytes = pdf_report.render_report(
        analysis=analysis,
        structure=structure,
        rows=data['rows'],
        samples_by_id=data['samples_by_id'],
        distributions=distributions,
        context_spans=_context_spans(analysis_id, db),
        intervals=analysis.intervals,
        summaries=analysis.summaries,
        same_event=_same_event_tests(analysis, data['samples_by_id'].values()),
        p_before=_p_before(analysis_id, db, threshold) if threshold is not None else None,
        project_name=analysis.project.name if analysis.project else None,
        date_format=date_format,
        coordinate_precision=data['coordinate_precision'],
    )
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition":
                f"attachment; filename=analysis-{analysis_id}-report.pdf"
        },
    )


@router.get("/results/{analysis_id}/graphs")
def get_graphs(analysis_id: str, db: Session = Depends(get_db),
               user: User = Depends(current_user)):
    """
    Each sample's probability distribution, for API consumers that want only
    the plot data. The Results page renders from GET /api/analysis/{id}
    instead.
    """
    # An unknown analysis is a 404, not an empty graph list.
    _owned_analysis(analysis_id, db, user)
    results = db.query(AnalysisResult).filter(AnalysisResult.analysis_id == analysis_id).all()
    return {
        "success": True,
        "data": {
            "graphs": [
                {"sample_id": r.sample_id, "probability_distribution": r.probability_distribution}
                for r in results
            ]
        }
    }


@router.post("/results/{analysis_id}/export")
def export_results(analysis_id: str, options: Dict, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    """Export results in the specified format: 'csv', 'json' or 'pdf'."""
    format_type = options.get('format', 'csv')

    # Each delegate re-checks ownership itself, since they are also routes in
    # their own right. Passing `user` through rather than dropping it is what
    # keeps that true when this is called directly rather than over HTTP.
    threshold = options.get('p_before_bp')
    if format_type == 'csv':
        return export_csv(analysis_id, date_format=options.get('date_format'),
                          p_before_bp=threshold, db=db, user=user)
    elif format_type == 'json':
        return export_json(analysis_id, p_before_bp=threshold, db=db, user=user)
    elif format_type == 'pdf':
        return export_pdf(analysis_id, date_format=options.get('date_format'),
                          p_before_bp=threshold, db=db, user=user)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {format_type}")
