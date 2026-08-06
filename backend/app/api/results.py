from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Analysis, RadiocarbonSample, AnalysisResult, ModelElement
from app.services.model_tree import rows_to_tree, flatten_structure
from app.services.calibration import get_curve
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

    Falls back to the plain min/max envelope columns for older rows that
    predate the multi-range HPD breakdown (hpd_68_ranges/hpd_95_ranges
    were added after hpd_68_min/max already existed).
    """
    if ranges:
        return '; '.join(
            f"{r['max']:.0f}-{r['min']:.0f} ({r['probability_percent']:.1f}%)"
            for r in ranges
        )
    if fallback_min is not None and fallback_max is not None:
        return f"{fallback_max:.0f}-{fallback_min:.0f}"
    return ''


def _unmodelled_ranges(sample, analysis_type: str) -> Dict:
    """
    What this date calibrates to on its own, with no stratigraphic
    constraint applied -- the "unmodelled vs modelled" comparison the
    results page shows beside each date, and the standard way to see what
    the model actually did.

    Computed here rather than read from a column, for the same reason
    GET /api/analysis/{id} computes it for the page: it is a cheap analytic
    calibration determined entirely by the sample's current
    age/error/curve, so deriving it cannot fall out of step with the
    measurement the way a stored copy would.

    Reported at the same two confidence levels as the modelled posterior,
    so a reader can compare like with like within one export.

    Only meaningful for a Bayesian analysis. For a plain calibration the
    modelled figures already *are* the unmodelled result, so repeating them
    would just invite the reader to compare a number with itself.
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
            'unmodelled_hpd_68_ranges': curve.get_hpd_ranges(cal_ages, probs, 0.68),
            'unmodelled_hpd_95_ranges': curve.get_hpd_ranges(cal_ages, probs, 0.95),
        }
    except Exception:
        # A sample whose curve_id no longer resolves (say a renamed curve)
        # shouldn't fail the whole export. The modelled figures are
        # unaffected; this one just stays empty.
        return blank


def _gather_export_rows(analysis_id: str, db: Session) -> Dict:
    """
    Shared data assembly for every results export format (CSV/JSON/table).
    Joins each AnalysisResult back to its RadiocarbonSample (by sample_id)
    so the export actually reflects what was measured, not just the
    posterior -- and raises 404 up front so every export endpoint fails
    the same, obvious way for a bad analysis_id instead of silently
    returning an empty/placeholder file.

    The unmodelled calibration is derived here too, so the CSV and the JSON
    report the same figure by construction rather than by coincidence.
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
            **_unmodelled_ranges(sample, analysis.analysis_type),
        })

    return {'analysis': analysis, 'rows': rows, 'samples_by_id': samples_by_id}


def _structure_rows(analysis_id: str, db: Session, export_rows: List[Dict],
                    samples_by_id: Dict) -> List[Dict]:
    """
    The analysis's model structure in reading order, with each date's
    exported result attached to its row.

    Read straight from the stored ModelElement rows rather than migrating
    on the fly: an export is a GET and should not write, and anything with
    results to export has already been run, which persists a tree. An
    analysis old enough to have none yet simply exports without structure
    (see _build_csv), and gains it the next time it's opened or re-run.
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
    'Unmodelled 95% HPD', 'Posterior Mean', 'Posterior Median', 'Posterior Std',
    '68% HPD', '95% HPD', 'TPQ BP', 'TAQ BP',
]


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
    "95% HPD" column. Reads what _gather_export_rows already derived, so
    the number here and the one in the JSON export are the same figure.

    The JSON carries the 68% breakdown too; a column-per-level does not pay
    for itself in a spreadsheet, where the 95% range is what gets quoted.
    """
    if not row:
        return ''
    return _format_hpd_ranges(row.get('unmodelled_hpd_95_ranges'), None, None)


def _posterior_cells(row: Optional[Dict]) -> List:
    """
    The modelled half. Dates and boundaries are both modelled parameters
    with a posterior, so they report through exactly the same columns --
    the whole point of exporting the structure rather than only the dates.
    """
    if not row:
        return ['', '', '', '', '']
    return [
        f"{row['posterior_mean']:.1f}" if row.get('posterior_mean') is not None else '',
        f"{row['posterior_median']:.1f}" if row.get('posterior_median') is not None else '',
        f"{row['posterior_std']:.1f}" if row.get('posterior_std') is not None else '',
        _format_hpd_ranges(row.get('hpd_68_ranges'), row.get('hpd_68_min'), row.get('hpd_68_max')),
        _format_hpd_ranges(row.get('hpd_95_ranges'), row.get('hpd_95_min'), row.get('hpd_95_max')),
    ]


def _build_csv(analysis_id: str, db: Session) -> str:
    """
    One row per modelled entity -- sequences, phases, boundaries and dates
    -- in the model's own reading order, matching the results table
    row for row.

    `Order` is written out because a spreadsheet sort would otherwise
    destroy the one thing the file exists to convey; re-sorting on it
    restores the structure.
    """
    data = _gather_export_rows(analysis_id, db)
    analysis = data['analysis']

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_HEADER)

    structure = _structure_rows(analysis_id, db, data['rows'], data['samples_by_id'])

    if not data['rows'] and not structure:
        writer.writerow([f'No results for analysis "{analysis.name}" ({analysis.status})'])
        return output.getvalue()

    # No stored structure (a calibration, or an analysis predating trees):
    # fall back to a flat list of dates so the export still carries the
    # results rather than coming back empty.
    if not structure:
        for order, row in enumerate(data['rows'], start=1):
            writer.writerow(
                [order, 'Date', 0, '', row['sample_name'] or row['sample_id'] or '']
                + _measurement_cells(row) + [_unmodelled_cell(row)]
                + _posterior_cells(row) + ['', '']
            )
        return output.getvalue()

    for order, row in enumerate(structure, start=1):
        posterior = row.get('posterior')
        unmodelled = ''
        if row['kind'] == 'date':
            measured, modelled = _measurement_cells(row.get('result')), _posterior_cells(row.get('result'))
            unmodelled = _unmodelled_cell(row.get('result'))
        elif posterior:
            # A boundary's posterior is summary-shaped, not a result row.
            # It has no unmodelled counterpart either -- a boundary exists
            # only as a modelled parameter.
            measured = _measurement_cells(None)
            modelled = _posterior_cells({
                'posterior_mean': posterior.get('posterior_mean'),
                'posterior_median': posterior.get('posterior_median'),
                'posterior_std': posterior.get('posterior_std'),
                'hpd_68_ranges': posterior.get('hpd_68_ranges'),
                'hpd_95_ranges': posterior.get('hpd_95_ranges'),
                'hpd_68_min': (posterior.get('hpd_68') or [None, None])[0],
                'hpd_68_max': (posterior.get('hpd_68') or [None, None])[1],
                'hpd_95_min': (posterior.get('hpd_95') or [None, None])[0],
                'hpd_95_max': (posterior.get('hpd_95') or [None, None])[1],
            })
        else:
            measured, modelled = _measurement_cells(None), _posterior_cells(None)

        writer.writerow(
            [order, str(row['kind']).title(), row['depth'], row['path'], row['name']]
            + measured + [unmodelled] + modelled
            + [row['tpq_age_bp'] if row['tpq_age_bp'] is not None else '',
               row['taq_age_bp'] if row['taq_age_bp'] is not None else '']
        )

    return output.getvalue()


@router.get("/results/{analysis_id}/table")
async def export_table(analysis_id: str, db: Session = Depends(get_db)):
    """Export results as a CSV table (same content as /export/csv, kept as a separate route for backwards compatibility)."""
    csv_content = _build_csv(analysis_id, db)
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=analysis-{analysis_id}-table.csv"}
    )


@router.get("/results/{analysis_id}/export/csv")
async def export_csv(analysis_id: str, db: Session = Depends(get_db)):
    """Export an analysis's results as CSV -- one row per sample, with real posterior/HPD data."""
    csv_content = _build_csv(analysis_id, db)
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=analysis-{analysis_id}-results.csv"}
    )


def _posterior_summary(posterior: Dict) -> Dict:
    """
    A boundary's posterior without its raw MCMC draws.

    The stored value also holds `posterior_samples` -- the full chain, tens
    of thousands of floats per boundary (see bayesian._summarize_posterior)
    -- which is not what an export is for, and would dwarf everything else
    in the file. The binned probability_distribution is kept, so a consumer
    can still plot the transition. Dates are already summarised this way by
    _gather_export_rows, which never selects the raw samples.
    """
    return {k: v for k, v in posterior.items() if k != 'posterior_samples'}


def _structure_payload(structure: List[Dict]) -> List[Dict]:
    """
    The structure rows as JSON, in the same reading order as the CSV and
    the results table: each date carrying its result, each boundary its
    posterior summary, and headings carrying only their place in the tree.
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
async def export_json(analysis_id: str, db: Session = Depends(get_db)):
    """
    Export an analysis's results as JSON, including sample details merged
    into each result row.

    Two views of the same data. `results` is the flat per-date list: one
    entry per measured date, with what was measured joined onto what was
    modelled. `structure` is that same run read in the model's own order --
    sequences, phases, boundaries and dates, exactly the rows the CSV and
    the results table show -- so a consumer can reproduce the model rather
    than only the dates. Both come from one assembly, so they cannot
    disagree; `structure` is empty for an analysis with no stored structure
    (a plain calibration), where there is nothing to read in model order.

    The nested tree itself is not repeated here -- GET /api/analysis/{id}
    already returns model_tree. What this adds is the ordered, joined view.
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
        }
    }


@router.get("/results/{analysis_id}/pdf")
async def export_pdf(analysis_id: str, db: Session = Depends(get_db)):
    """
    PDF report generation is not implemented yet (needs ReportLab
    rendering of the results table + posterior plots). Explicitly 501
    rather than a fake 200 "success" so callers don't mistake this for a
    working export.
    """
    # Still validate the analysis exists, so the error a caller sees
    # distinguishes "not built yet" from "wrong analysis id".
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
    raise HTTPException(status_code=501, detail="PDF export is not implemented yet")


@router.get("/results/{analysis_id}/graphs")
async def get_graphs(analysis_id: str, db: Session = Depends(get_db)):
    """
    Returns each sample's probability distribution for external plotting.
    Note: the Results page does not call this -- it renders plots from the
    data already included in GET /api/analysis/{id}. This exists for API
    consumers that only want the plot data.
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
async def export_results(analysis_id: str, options: Dict, db: Session = Depends(get_db)):
    """Export results in the specified format ('csv' or 'json'; 'pdf' is not implemented yet)."""
    format_type = options.get('format', 'csv')

    if format_type == 'csv':
        return await export_csv(analysis_id, db)
    elif format_type == 'json':
        return await export_json(analysis_id, db)
    elif format_type == 'pdf':
        return await export_pdf(analysis_id, db)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {format_type}")
