from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Analysis, RadiocarbonSample, AnalysisResult
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


def _gather_export_rows(analysis_id: str, db: Session) -> Dict:
    """
    Shared data assembly for every results export format (CSV/JSON/table).
    Joins each AnalysisResult back to its RadiocarbonSample (by sample_id)
    so the export actually reflects what was measured, not just the
    posterior -- and raises 404 up front so every export endpoint fails
    the same, obvious way for a bad analysis_id instead of silently
    returning an empty/placeholder file.
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
        })

    return {'analysis': analysis, 'rows': rows}


def _build_csv(analysis_id: str, db: Session) -> str:
    data = _gather_export_rows(analysis_id, db)
    analysis = data['analysis']

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Sample Name', 'Age BP', '1-Sigma Error', 'Material', 'Sample Type', 'Curve',
        'Calibrated Age BP (mode)', 'Posterior Mean', 'Posterior Median', 'Posterior Std',
        '68% HPD', '95% HPD',
    ])

    if not data['rows']:
        writer.writerow([f'No results for analysis "{analysis.name}" ({analysis.status})'])
    else:
        for row in data['rows']:
            writer.writerow([
                row['sample_name'] or row['sample_id'] or '',
                row['age_bp'] if row['age_bp'] is not None else '',
                row['error_1sigma'] if row['error_1sigma'] is not None else '',
                row['material_type'] or '',
                row['sample_type'] or '',
                row['curve_id'] or '',
                f"{row['calibrated_age_bp']:.0f}" if row['calibrated_age_bp'] is not None else '',
                f"{row['posterior_mean']:.1f}" if row['posterior_mean'] is not None else '',
                f"{row['posterior_median']:.1f}" if row['posterior_median'] is not None else '',
                f"{row['posterior_std']:.1f}" if row['posterior_std'] is not None else '',
                _format_hpd_ranges(row['hpd_68_ranges'], row['hpd_68_min'], row['hpd_68_max']),
                _format_hpd_ranges(row['hpd_95_ranges'], row['hpd_95_min'], row['hpd_95_max']),
            ])

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


@router.get("/results/{analysis_id}/export/json")
async def export_json(analysis_id: str, db: Session = Depends(get_db)):
    """Export an analysis's results as JSON, including sample details merged into each result row."""
    data = _gather_export_rows(analysis_id, db)
    analysis = data['analysis']

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
    data = _gather_export_rows(analysis_id, db)
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
