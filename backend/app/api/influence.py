"""
Influence analysis: the model re-solved with each date left out, moved to the
neighbouring Phase, or treated as an outlier (services/influence.py plans the
variants and reduces each result).

The variants are solved in one job, in memory, through the same solver path
as any analysis (analysis._run_bayesian), and kept as ONE report on the
analysis rather than as a saved analysis each: a twelve-date model gives up
to forty-eight, and a project list holding them would bury the analyses
people made. Any one can be opened as a real analysis when it is wanted.
"""
import copy
from datetime import datetime
from typing import Dict, List, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.analysis import (_copy_inputs, _get_or_migrate_tree, _measurement_dicts,
                              _run_analysis_job, _run_bayesian)
from app.api.auth import current_user, owned_or_404
from app.api.run_history import _inputs_hash
from app.database import get_db
from app.models import Analysis, RadiocarbonSample, StratigraphicRelationship, User
from app.services import failures, gibbs, influence, jobs

router = APIRouter()


def job_target(analysis_id: str) -> str:
    """An influence job's target: not the analysis id itself, which the Results
    page watches for runs of the analysis, and would otherwise read this job as
    one."""
    return f'influence:{analysis_id}'


class InfluenceRequest(BaseModel):
    kinds: List[Literal['leave_out', 'move', 'outlier']] = Field(min_length=1)


class OpenVariantRequest(BaseModel):
    variant_id: str


def _run_config(config: Dict) -> Dict:
    """The analysis's own settings, with durations on (an influence target can
    be one) and the KDE and dispersal off (none is)."""
    return {**(config or {}), 'estimate_durations': True, 'estimate_kde': False,
            'estimate_dispersal': False}


def _influence_job(analysis_id: str, kinds: List[str]):
    def work(db: Session, handle: jobs.JobHandle) -> Dict:
        analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
        if analysis is None:
            raise ValueError(f"Analysis '{analysis_id}' no longer exists")
        tree, _linearized = _get_or_migrate_tree(analysis, db)
        measurements = _measurement_dicts(list(analysis.measurements))
        config = _run_config(analysis.bayesian_config)
        variants = influence.plan(measurements, tree, kinds)
        total = len(variants) + 1

        # A sampled variant reports its sweeps; the job's progress is variants
        # done, so this only passes cancellation through.
        def keep_going(_info: Dict) -> bool:
            return not handle.cancelled

        def solve(label: str, done: int, dates: List[Dict]):
            handle.update(progress=done / total, force=True, done=done, total=total,
                          current=label)
            if handle.cancelled:
                raise jobs.Cancelled()
            try:
                _results, boundaries, solver, intervals, _summaries = _run_bayesian(
                    dates, copy.deepcopy(tree), config, on_progress=keep_going)
            except gibbs.Cancelled:
                raise jobs.Cancelled()
            return influence.summarise(boundaries, intervals), solver.get('method')

        baseline, method = solve('the model as it is', 0, measurements)
        rows = []
        for done, variant in enumerate(variants, start=1):
            try:
                summary, variant_method = solve(variant['label'], done,
                                                influence.apply(variant, measurements))
                rows.append({**variant, 'summary': summary, 'solver': variant_method,
                             'error': None})
            except jobs.Cancelled:
                raise
            except Exception as exc:
                # One variant that cannot be solved (a Phase left with no
                # dates, say) is a row that says so, not a failed run.
                rows.append({**variant, 'summary': None, 'solver': None,
                             'error': failures.classify(exc).message})
        analysis.influence = {
            'created_at': datetime.utcnow().isoformat(),
            'inputs_hash': _inputs_hash(analysis, db),
            'kinds': list(kinds),
            'solver': method,
            'baseline': baseline,
            'variants': rows,
        }
        db.commit()
        return {'analysis_id': analysis_id, 'variants': len(rows),
                'failed': sum(1 for row in rows if row['error'])}
    return work


def _bayesian_or_400(analysis: Analysis) -> None:
    if analysis.analysis_type != 'bayesian':
        raise HTTPException(status_code=400,
                            detail='Influence is tested on a Bayesian model.')


@router.post("/analysis/{analysis_id}/influence")
def run_influence(analysis_id: str, request: InfluenceRequest,
                  db: Session = Depends(get_db), user: User = Depends(current_user)):
    """
    Queue an influence run over the variants `kinds` asks for. Answers 202
    with a job_id; the report is at GET .../influence once it completes.
    """
    analysis = owned_or_404(
        db.query(Analysis).filter(Analysis.id == analysis_id).first(), user, 'That analysis')
    _bayesian_or_400(analysis)
    if analysis.status != 'completed':
        raise HTTPException(status_code=400,
                            detail='Run the analysis first; its influence is tested against its result.')
    tree, _linearized = _get_or_migrate_tree(analysis, db)
    if tree is None:
        raise HTTPException(status_code=400, detail='This analysis has no model to vary.')
    kinds = sorted(set(request.kinds), key=influence.KINDS.index)
    planned = influence.plan(_measurement_dicts(list(analysis.measurements)), tree, kinds)
    if not planned:
        raise HTTPException(status_code=400,
                            detail='None of these changes applies to any date in this model.')
    job_id = jobs.submit('influence', job_target(analysis.id), _influence_job(analysis.id, kinds),
                         owner_id=user.id)
    return JSONResponse(status_code=202, content={
        'success': True,
        'data': {'id': analysis.id, 'job_id': job_id, 'variants': len(planned)},
    })


@router.get("/analysis/{analysis_id}/influence")
def get_influence(analysis_id: str, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    """
    The last influence report, or null. `stale` is true when the analysis's
    dates, model or settings have changed since it was made.
    """
    analysis = owned_or_404(
        db.query(Analysis).filter(Analysis.id == analysis_id).first(), user, 'That analysis')
    report = analysis.influence
    stale = bool(report) and report.get('inputs_hash') != _inputs_hash(analysis, db)
    latest = jobs.latest_for(job_target(analysis.id), db)
    running = latest is not None and latest.state in ('queued', 'running')
    return {'success': True, 'data': report, 'stale': stale,
            'job_id': latest.id if running else None}


@router.post("/analysis/{analysis_id}/influence/open")
def open_variant(analysis_id: str, request: OpenVariantRequest,
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    """
    Make one variant of the last report a saved analysis in the same project,
    and queue its run: answers 202 with the new analysis's id and a job_id.
    """
    source = owned_or_404(
        db.query(Analysis).filter(Analysis.id == analysis_id).first(), user, 'That analysis')
    report = source.influence or {}
    variant = next((v for v in report.get('variants') or []
                    if v['id'] == request.variant_id), None)
    if variant is None:
        raise HTTPException(status_code=404, detail='That variant was not found.')

    opened, samples_map, element_map = _copy_inputs(
        source, f"{source.name} ({variant['label']})", source.bayesian_config, user, db)
    copied = samples_map.get(variant['date_id'])
    target = element_map.get(variant.get('to_phase_id')) if variant['kind'] == 'move' else None
    if copied is None or (variant['kind'] == 'move' and target is None):
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail='The analysis has changed since this report, so the variant no longer '
                   'applies. Test its influence again.')
    row = db.query(RadiocarbonSample).filter(RadiocarbonSample.id == copied).first()
    if variant['kind'] == 'leave_out':
        db.query(StratigraphicRelationship).filter(
            StratigraphicRelationship.analysis_id == opened.id,
            (StratigraphicRelationship.from_sample_id == copied)
            | (StratigraphicRelationship.to_sample_id == copied)).delete(
            synchronize_session=False)
        db.delete(row)
    elif variant['kind'] == 'move':
        row.element_id = target
    else:
        row.outlier_prior = 1.0
        row.outlier_model = row.outlier_model or influence.DEFAULT_OUTLIER_MODEL
    db.commit()
    job_id = jobs.submit('analysis_run', opened.id, _run_analysis_job(opened.id),
                         owner_id=user.id)
    return JSONResponse(status_code=202, content={
        'success': True,
        'data': {'id': opened.id, 'name': opened.name, 'job_id': job_id},
    })
