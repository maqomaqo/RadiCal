"""
Scenario simulations: an analysis's model and sampling plan, redrawn from a
known truth (services/simulation.py), as replicate analyses in its project.

The replicates are ordinary analyses -- each a copy of the template with its
dates' ages replaced -- so everything the app does with an analysis (results,
compare, influence, export) works on them unchanged. What they were drawn
from is kept on a Simulation: the truth windows, the seed and plan, and the
truth key, every simulated date's event and inbuilt age, for scoring.
"""
import csv
import io
import secrets
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.analysis import (_copy_inputs, _get_or_migrate_tree, _measurement_dicts,
                              _rerun_analysis)
from app.api.auth import current_user, owned_or_404
from app.database import get_db
from app.models import Analysis, AnalysisResult, RadiocarbonSample, Simulation, User
from app.services import failures, jobs, simulation
from app.services.model_tree import DATE_HOLDERS

router = APIRouter()

# Enough replicates for a reading of Monte Carlo spread; each is a full run.
MAX_REPLICATES = 20


class Window(BaseModel):
    element_id: Optional[str] = None
    name: Optional[str] = None
    kind: str = 'phase'
    old_bp: float
    young_bp: float


class SimulationRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    windows: List[Window] = Field(min_length=1)
    replicates: int = Field(default=5, ge=0, le=MAX_REPLICATES)
    seed: Optional[int] = Field(default=None, ge=0, le=2**31 - 1)
    scatter: bool = True
    inbuilt: bool = True


def job_target(simulation_id: str) -> str:
    """Not the source analysis's id, which the Results page watches for its
    own runs (as api/influence.job_target)."""
    return f'simulation:{simulation_id}'


def _template(analysis: Analysis, db: Session):
    """(tree or None, measurements) of an analysis that can be simulated, or 400."""
    if analysis.analysis_type not in ('bayesian', 'group_calibration'):
        raise HTTPException(status_code=400,
                            detail='A simulation starts from a Bayesian model or a group calibration.')
    tree = None
    if analysis.analysis_type == 'bayesian':
        tree, _linearized = _get_or_migrate_tree(analysis, db)
        if tree is None:
            raise HTTPException(status_code=400, detail='This analysis has no model to simulate.')
        holders = {}
        stack = [tree]
        while stack:
            element = stack.pop()
            holders[element['id']] = element.get('element_type')
            stack.extend(element.get('children') or [])
        for sample in analysis.measurements:
            kind = holders.get(sample.element_id or sample.group_id)
            if kind in DATE_HOLDERS and kind != 'phase':
                raise HTTPException(
                    status_code=400,
                    detail='This model holds a wiggle match or a deposition sequence, whose dates '
                           'are placed by their ring offsets or depths; it cannot be simulated '
                           'Phase by Phase.')
    return tree, _measurement_dicts(list(analysis.measurements))


def _date_medians(analysis_id: str, db: Session) -> Dict[str, float]:
    return {r.sample_id: float(r.posterior_median)
            for r in db.query(AnalysisResult).filter(AnalysisResult.analysis_id == analysis_id)
            if r.posterior_median is not None}


@router.get("/analysis/{analysis_id}/simulate/defaults")
def simulation_defaults(analysis_id: str, db: Session = Depends(get_db),
                        user: User = Depends(current_user)):
    """
    The truth windows a simulation of this analysis starts from -- each
    Phase's from the posterior medians of its Boundaries -- and the same with
    every gap closed, with a fresh seed to offer.
    """
    analysis = owned_or_404(
        db.query(Analysis).filter(Analysis.id == analysis_id).first(), user, 'That analysis')
    if analysis.status != 'completed':
        raise HTTPException(status_code=400,
                            detail='Run the analysis first; its result sets the starting truth.')
    tree, measurements = _template(analysis, db)
    windows = simulation.default_windows(tree, measurements, _date_medians(analysis.id, db))
    return {'success': True, 'data': {
        'windows': windows,
        'closed': simulation.close_gaps(tree, windows),
        'seed': secrets.randbelow(2**31 - 1),
        'max_replicates': MAX_REPLICATES,
    }}


def _simulation_job(simulation_id: str, analysis_ids: List[str]):
    def work(db: Session, handle: jobs.JobHandle) -> Dict:
        total = len(analysis_ids)
        failed = []
        handle.update(progress=0.0, force=True, completed=0, total=total, failed=[])
        for done, analysis_id in enumerate(analysis_ids, start=1):
            handle.check_cancelled()
            analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
            if analysis is None:
                continue
            try:
                _rerun_analysis(analysis, db)
            except jobs.Cancelled:
                raise
            except Exception as exc:
                db.rollback()
                failure = failures.classify(exc)
                analysis.status = 'failed'
                analysis.error_message = failure.message
                db.commit()
                failed.append({'id': analysis.id, 'name': analysis.name, 'error': failure.message})
            handle.update(progress=done / total, force=True, completed=done, total=total,
                          failed=failed)
        return {'simulation_id': simulation_id, 'completed': total, 'total': total,
                'failed': failed}
    return work


@router.post("/analysis/{analysis_id}/simulate")
def simulate(analysis_id: str, request: SimulationRequest, db: Session = Depends(get_db),
             user: User = Depends(current_user)):
    """
    Draw replicates r0..rN of this analysis from the truth in `windows`, save
    each as an analysis in its project, and queue them to run as one job.
    Answers 202 with the simulation's id and the job's.
    """
    source = owned_or_404(
        db.query(Analysis).filter(Analysis.id == analysis_id).first(), user, 'That analysis')
    tree, measurements = _template(source, db)
    windows = [w.model_dump() for w in request.windows]
    for window in windows:
        if window['old_bp'] < window['young_bp']:
            raise HTTPException(
                status_code=422,
                detail=f"The window for {window.get('name') or 'a Phase'} ends before it starts.")
    seed = request.seed if request.seed is not None else secrets.randbelow(2**31 - 1)
    name = (request.name or '').strip() or f'{source.name} simulated'

    try:
        drawn = [simulation.simulate(measurements, windows, seed, k,
                                     scatter=request.scatter, inbuilt=request.inbuilt)
                 for k in range(request.replicates + 1)]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    record = Simulation(owner_id=user.id, project_id=source.project_id,
                        source_analysis_id=source.id, name=name, seed=seed,
                        replicates=request.replicates, scatter=request.scatter,
                        inbuilt=request.inbuilt, windows=windows,
                        truth_key=[{**row, 'replicate': k}
                                   for k, rows in enumerate(drawn) for row in rows])
    db.add(record)
    db.flush()

    analysis_ids = []
    for k, rows in enumerate(drawn):
        copy, samples_map, _elements = _copy_inputs(source, f'{name} r{k}',
                                                    source.bayesian_config, user, db)
        copy.simulation_id = record.id
        copy.replicate = k
        for row in rows:
            sample = db.query(RadiocarbonSample).filter(
                RadiocarbonSample.id == samples_map[row['date_id']]).first()
            sample.age_bp = row['age_bp']
            sample.notes = (f"Simulated (r{k}): event {row['event_bp']:.1f} cal BP, "
                            f"inbuilt {row['inbuilt_years']:.1f} yr.")
        analysis_ids.append(copy.id)
    db.commit()

    job_id = jobs.submit('simulation', job_target(record.id),
                         _simulation_job(record.id, analysis_ids), owner_id=user.id)
    return JSONResponse(status_code=202, content={
        'success': True,
        'data': {'id': record.id, 'job_id': job_id, 'seed': seed, 'analysis_ids': analysis_ids},
    })


def _simulation_json(record: Simulation, db: Session) -> Dict:
    replicates = (db.query(Analysis).filter(Analysis.simulation_id == record.id)
                  .order_by(Analysis.replicate).all())
    latest = jobs.latest_for(job_target(record.id), db)
    return {
        'id': record.id,
        'name': record.name,
        'source_analysis_id': record.source_analysis_id,
        'project_id': record.project_id,
        'seed': record.seed,
        'replicates': record.replicates,
        'scatter': record.scatter,
        'inbuilt': record.inbuilt,
        'windows': record.windows,
        'created_at': record.created_at.isoformat() if record.created_at else None,
        'analyses': [{'id': a.id, 'name': a.name, 'replicate': a.replicate, 'status': a.status}
                     for a in replicates],
        'job_id': latest.id if latest is not None and latest.state in ('queued', 'running') else None,
    }


@router.get("/analysis/{analysis_id}/simulations")
def list_simulations(analysis_id: str, db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    """The simulations drawn from this analysis, newest first."""
    owned_or_404(db.query(Analysis).filter(Analysis.id == analysis_id).first(), user,
                 'That analysis')
    records = (db.query(Simulation)
               .filter(Simulation.source_analysis_id == analysis_id,
                       Simulation.owner_id == user.id)
               .order_by(Simulation.created_at.desc()).all())
    return {'success': True, 'data': [_simulation_json(r, db) for r in records]}


def _owned_simulation(simulation_id: str, db: Session, user: User) -> Simulation:
    record = db.query(Simulation).filter(Simulation.id == simulation_id).first()
    if record is None or record.owner_id != user.id:
        raise HTTPException(status_code=404, detail='That simulation was not found.')
    return record


@router.get("/simulations/{simulation_id}")
def get_simulation(simulation_id: str, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    return {'success': True, 'data': _simulation_json(_owned_simulation(simulation_id, db, user), db)}


TRUTH_COLUMNS = ['replicate', 'sample_name', 'event_bp', 'inbuilt_years', 'sample_bp',
                 'curve_bp', 'delta_r', 'z', 'error', 'age_bp', 'window_old_bp',
                 'window_young_bp', 'element_id', 'date_id']


@router.get("/simulations/{simulation_id}/truth.csv")
def truth_key_csv(simulation_id: str, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    """The truth key: one row per simulated date per replicate, cal BP."""
    record = _owned_simulation(simulation_id, db, user)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=TRUTH_COLUMNS, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(record.truth_key)
    filename = ''.join(c if c.isalnum() or c in '-_' else '_' for c in record.name)[:80]
    return Response(content=out.getvalue(), media_type='text/csv', headers={
        'Content-Disposition': f'attachment; filename="{filename}_truth.csv"'})
