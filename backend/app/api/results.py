from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Analysis, RadiocarbonSample, AnalysisResult, ModelElement, SampleGroup
from app.services.model_tree import rows_to_tree, flatten_structure
from app.services.calibration import SIGMA_1, SIGMA_2, get_curve
from app.services import date_format as date_format_service
from app.services import pdf_report
from app.services import intervals as intervals_service
from typing import Dict, List, Optional
import io
import csv
import json

router = APIRouter()


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

    return {'analysis': analysis, 'rows': rows, 'samples_by_id': samples_by_id}


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


def _build_csv(analysis_id: str, db: Session, date_format: Optional[str] = None) -> str:
    """
    One row per modelled entity -- sequences, phases, boundaries and dates --
    in the model's reading order, matching the results table row for row.

    `Order` is written out because a spreadsheet sort would destroy the
    structure the file exists to convey; re-sorting on it restores it.

    `date_format` is the reporting format the results page is showing. When it
    is not cal BP, each calendar-age column gains a formatted counterpart (see
    _csv_header); the cal BP columns are always present.
    """
    date_format = date_format_service.normalise(date_format)
    formatted = date_format in date_format_service.ERA_LABELS

    data = _gather_export_rows(analysis_id, db)
    analysis = data['analysis']

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(_csv_header(date_format))

    structure = _structure_rows(analysis_id, db, data['rows'], data['samples_by_id'])

    if not data['rows'] and not structure:
        writer.writerow([f'No results for analysis "{analysis.name}" ({analysis.status})'])
        return output.getvalue()

    def extra(result, tpq, taq):
        return _formatted_cells(result, tpq, taq, date_format) if formatted else []

    # No stored structure, so fall back to a flat list of dates rather than
    # coming back empty.
    if not structure:
        for order, row in enumerate(data['rows'], start=1):
            writer.writerow(
                [order, 'Date', 0, '', row['sample_name'] or row['sample_id'] or '']
                + _measurement_cells(row) + [_unmodelled_cell(row)]
                + _posterior_cells(row) + ['', '']
                + extra(row, None, None)
            )
        _write_intervals(writer, analysis)
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
            + extra(result, row['tpq_age_bp'], row['taq_age_bp'])
        )

    _write_intervals(writer, analysis)
    return output.getvalue()


@router.get("/results/{analysis_id}/table")
def export_table(analysis_id: str, date_format: Optional[str] = None, db: Session = Depends(get_db)):
    """Export results as a CSV table. Same content as /export/csv, kept as a
    separate route for backwards compatibility."""
    csv_content = _build_csv(analysis_id, db, date_format)
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=analysis-{analysis_id}-table.csv"}
    )


@router.get("/results/{analysis_id}/export/csv")
def export_csv(analysis_id: str, date_format: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Export an analysis's results as CSV, in the model's own reading order.

    `date_format` (calBP / bcad / bce_ce) is the reporting format the results
    page is showing; when it is not cal BP each calendar-age column gains a
    formatted counterpart. An unrecognised value falls back to cal BP rather
    than failing the download.
    """
    csv_content = _build_csv(analysis_id, db, date_format)
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
def export_json(analysis_id: str, db: Session = Depends(get_db)):
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
            "structure": _structure_payload(structure),
            # Null unless the run was asked for them (Analysis.intervals).
            # Carried whole rather than flattened as the CSV flattens it,
            # since JSON has room for the full HPD breakdown.
            "intervals": analysis.intervals,
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


@router.get("/results/{analysis_id}/pdf")
def export_pdf(analysis_id: str, date_format: Optional[str] = None,
                     db: Session = Depends(get_db)):
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
        project_name=analysis.project.name if analysis.project else None,
        date_format=date_format,
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
def get_graphs(analysis_id: str, db: Session = Depends(get_db)):
    """
    Each sample's probability distribution, for API consumers that want only
    the plot data. The Results page renders from GET /api/analysis/{id}
    instead.
    """
    # An unknown analysis is a 404, not an empty graph list.
    if not db.query(Analysis.id).filter(Analysis.id == analysis_id).first():
        raise HTTPException(status_code=404, detail="Analysis not found")
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
def export_results(analysis_id: str, options: Dict, db: Session = Depends(get_db)):
    """Export results in the specified format: 'csv', 'json' or 'pdf'."""
    format_type = options.get('format', 'csv')

    if format_type == 'csv':
        return export_csv(analysis_id, options.get('date_format'), db)
    elif format_type == 'json':
        return export_json(analysis_id, db)
    elif format_type == 'pdf':
        return export_pdf(analysis_id, options.get('date_format'), db)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {format_type}")
