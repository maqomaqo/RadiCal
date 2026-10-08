"""
Run history (feature spec W6): the last few completed runs of each analysis,
so a re-run -- usually after a change to the code -- can be set beside the run
before it instead of overwriting it.

Each run is kept as what GET /analysis/{id} returned once it finished, so the
Results and Compare pages read an old run with the code they already have.
Only RUNS_KEPT are kept per analysis: the point is the effect of the latest
change, not an archive. With the sampler's fixed default seed, a re-run of
unchanged inputs on unchanged code reproduces its result exactly, so any
difference the diff reports is real.

Recording never fails a run. The run's results are already committed when it
is called; a history that could not be written is logged and the analysis
stands.
"""
import hashlib
import json
import logging
import os
import subprocess
from functools import lru_cache
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.auth import current_user, owned_or_404
from app.database import get_db
from app.models import Analysis, AnalysisRun, Project, User

router = APIRouter()
log = logging.getLogger(__name__)

# How many completed runs each analysis keeps, the current one included.
RUNS_KEPT = 3
# How far a median must move, in years, for the project diff to list it.
DEFAULT_SHIFT_YEARS = 10.0


@lru_cache(maxsize=1)
def app_commit() -> Optional[str]:
    """
    The code a run ran on: APP_COMMIT where a deployment sets it, otherwise the
    checkout's own HEAD, otherwise unknown. A container built from a copy of
    the source has no .git, which is what the variable is for.
    """
    commit = os.getenv('APP_COMMIT')
    if commit:
        return commit.strip()[:40] or None
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        out = subprocess.run(['git', 'rev-parse', '--short=12', 'HEAD'], cwd=here,
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:                            # noqa: BLE001 - no git, no commit
        return None


def _inputs_hash(analysis: Analysis, db: Session) -> str:
    """A fingerprint of what the run was computed from -- settings, dates,
    model -- and not of its name, which changes nothing."""
    from app.api.project_export import analysis_inputs
    inputs = analysis_inputs(analysis, db)
    inputs.pop('name', None)
    text = json.dumps(inputs, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def record_run(analysis_id: str, db: Session) -> None:
    """
    Keep the analysis's just-finished run, and drop any beyond RUNS_KEPT.
    Called once a run's results are committed; see the module docstring for
    why it never raises.
    """
    try:
        from app.api.analysis import get_analysis
        analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
        if analysis is None or analysis.status != 'completed' or not analysis.owner_id:
            return
        owner = db.query(User).filter(User.id == analysis.owner_id).first()
        if owner is None:
            return
        payload = get_analysis(analysis_id, db, owner)['data']
        db.add(AnalysisRun(
            analysis_id=analysis_id,
            app_commit=app_commit(),
            inputs_hash=_inputs_hash(analysis, db),
            payload=json.loads(json.dumps(payload, default=str)),
        ))
        db.flush()
        runs = (db.query(AnalysisRun.id)
                .filter(AnalysisRun.analysis_id == analysis_id)
                .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
                .all())
        stale = [run_id for (run_id,) in runs[RUNS_KEPT:]]
        if stale:
            db.query(AnalysisRun).filter(AnalysisRun.id.in_(stale)).delete(
                synchronize_session=False)
        db.commit()
    except Exception:                            # noqa: BLE001 - see docstring
        db.rollback()
        log.exception('Could not record the run of analysis %s', analysis_id)


def _summary(run: AnalysisRun, current_id: Optional[str]) -> Dict:
    payload = run.payload or {}
    solver = payload.get('solver') or {}
    return {
        'id': run.id,
        'created_at': run.created_at.isoformat() if run.created_at else None,
        'app_commit': run.app_commit,
        'inputs_hash': run.inputs_hash,
        'current': run.id == current_id,
        'solver': solver.get('method'),
        'seed': solver.get('seed'),
        'sweeps': solver.get('sweeps'),
        'converged': solver.get('converged'),
    }


def _runs(analysis_id: str, db: Session) -> List[AnalysisRun]:
    return (db.query(AnalysisRun)
            .filter(AnalysisRun.analysis_id == analysis_id)
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
            .all())


# --------------------------------------------------------------------------
# What moved between two runs
# --------------------------------------------------------------------------

def _walk(element: Optional[Dict]):
    if not element:
        return
    yield element
    for child in element.get('children') or []:
        yield from _walk(child)


def _quantities(payload: Dict) -> Dict[tuple, Dict]:
    """
    Every modelled quantity a run reports, keyed so two runs pair up: a
    Boundary (or anything else with a posterior of its own) by type and name,
    a duration by kind and name. Names rather than ids, as the comparison
    page pairs them, so a run of a duplicated model still pairs.
    """
    out: Dict[tuple, Dict] = {}
    for element in _walk(payload.get('model_tree')):
        posterior = element.get('posterior') or {}
        if posterior.get('posterior_median') is None:
            continue
        out[('element', element.get('element_type'), element.get('name') or '')] = {
            'kind': element.get('element_type'),
            'name': element.get('name') or '',
            'median': posterior['posterior_median'],
            'modes': len(posterior.get('hpd_95_ranges') or []),
            'units': 'cal BP',
        }
    for record in ((payload.get('intervals') or {}).get('durations') or []):
        years = record.get('years') or {}
        if years.get('median') is None:
            continue
        out[('duration', record.get('kind'), record.get('name') or '')] = {
            'kind': record.get('kind'),
            'name': record.get('name') or '',
            'median': years['median'],
            'modes': len(years.get('hpd_95_ranges') or []),
            'units': 'yr',
        }
    return out


def diff_runs(newer: Dict, older: Dict, shift_years: float = DEFAULT_SHIFT_YEARS) -> Dict:
    """
    What changed between two runs of one analysis: every quantity whose median
    moved by `shift_years` or more, or whose 95% region gained or lost a part
    (a mode appearing or going, which can matter more than a shift -- REF-A-
    NOE lost its short mode while its median moved under ten years), and any
    change in whether the run converged.
    """
    new_q, old_q = _quantities(newer), _quantities(older)
    moved = []
    for key in sorted(set(new_q) & set(old_q), key=str):
        a, b = old_q[key], new_q[key]
        shift = b['median'] - a['median']
        if abs(shift) >= shift_years or a['modes'] != b['modes']:
            moved.append({
                'kind': b['kind'], 'name': b['name'], 'units': b['units'],
                'before': a['median'], 'after': b['median'], 'shift': shift,
                'modes_before': a['modes'], 'modes_after': b['modes'],
            })
    old_solver, new_solver = older.get('solver') or {}, newer.get('solver') or {}
    return {
        'moved': moved,
        'only_before': sorted(old_q[k]['name'] for k in set(old_q) - set(new_q)),
        'only_after': sorted(new_q[k]['name'] for k in set(new_q) - set(old_q)),
        'converged_before': old_solver.get('converged'),
        'converged_after': new_solver.get('converged'),
        'convergence_changed': old_solver.get('converged') != new_solver.get('converged'),
    }


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@router.get("/analysis/{analysis_id}/runs")
def list_runs(analysis_id: str, db: Session = Depends(get_db),
              user: User = Depends(current_user)):
    """The analysis's kept runs, newest (the current result) first."""
    owned_or_404(db.query(Analysis).filter(Analysis.id == analysis_id).first(),
                 user, 'That analysis')
    runs = _runs(analysis_id, db)
    current = runs[0].id if runs else None
    return {"success": True, "data": {
        "kept": RUNS_KEPT, "runs": [_summary(run, current) for run in runs]}}


@router.get("/analysis/{analysis_id}/runs/{run_id}")
def get_run(analysis_id: str, run_id: str, db: Session = Depends(get_db),
            user: User = Depends(current_user)):
    """
    One kept run in GET /analysis/{id}'s shape, so the comparison page reads
    it as it reads an analysis. Its id is `analysis_id@run_id` and it carries
    `run` (when, on what code, under what seed), so two runs of one analysis
    can sit side by side; the page labels it in the reader's own time zone.
    """
    owned_or_404(db.query(Analysis).filter(Analysis.id == analysis_id).first(),
                 user, 'That analysis')
    run = (db.query(AnalysisRun)
           .filter(AnalysisRun.id == run_id, AnalysisRun.analysis_id == analysis_id)
           .first())
    if run is None:
        raise HTTPException(status_code=404, detail='That run was not found.')
    payload = dict(run.payload or {})
    payload['id'] = f'{analysis_id}@{run_id}'
    payload['run'] = _summary(run, None)
    return {"success": True, "data": payload}


@router.get("/projects/{project_id}/run-diff")
def project_run_diff(project_id: str, shift_years: float = DEFAULT_SHIFT_YEARS,
                     db: Session = Depends(get_db), user: User = Depends(current_user)):
    """
    For every analysis in the project with two kept runs, what moved between
    the latest and the one before it (diff_runs). Read after a project
    re-run, it lists the analyses the re-run changed.
    """
    owned_or_404(db.query(Project).filter(Project.id == project_id).first(),
                 user, 'That project')
    analyses = (db.query(Analysis)
                .filter(Analysis.project_id == project_id, Analysis.owner_id == user.id)
                .order_by(Analysis.created_at, Analysis.id).all())
    changed, compared = [], 0
    for analysis in analyses:
        runs = _runs(analysis.id, db)[:2]
        if len(runs) < 2:
            continue
        compared += 1
        diff = diff_runs(runs[0].payload or {}, runs[1].payload or {}, shift_years)
        if diff['moved'] or diff['convergence_changed'] or diff['only_before'] or diff['only_after']:
            changed.append({
                'analysis_id': analysis.id, 'name': analysis.name,
                'run_after': runs[0].id, 'run_before': runs[1].id,
                'same_inputs': runs[0].inputs_hash == runs[1].inputs_hash,
                'commits': [runs[1].app_commit, runs[0].app_commit],
                **diff,
            })
    return {"success": True, "data": {
        "shift_years": shift_years, "compared": compared, "changed": changed}}
