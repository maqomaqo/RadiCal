from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models import Project, ProjectSample, Analysis, RadiocarbonSample, User
from app.schemas.project import ProjectCreate, ProjectUpdate
from app.api.analysis import _rerun_analysis
from app.api.auth import current_user, owned_or_404
from app.api import project_export
from app.services import jobs
from app.services import failures
from typing import Dict, Optional

router = APIRouter()


def _project_settings(payload: Dict, project: Project) -> Dict:
    """
    The two coordinate settings, added to a single project's payload.

    Carried on every view of one project rather than fetched separately,
    because the page that shows a coordinate is the page that has to say at
    what precision it will leave and whether the map may fetch tiles. Left off
    the project LIST, which shows neither.
    """
    return {
        **payload,
        "coordinate_precision": project.coordinate_precision or 'full',
        "map_tiles_enabled": bool(project.map_tiles_enabled),
    }


@router.post("/projects")
def create_project(
    project: ProjectCreate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Create a new project to group analyses under."""
    if not project.name or not project.name.strip():
        raise HTTPException(status_code=400, detail="Project name is required")

    new_project = Project(
        name=project.name.strip(), description=project.description, owner_id=user.id)
    db.add(new_project)
    db.commit()
    db.refresh(new_project)

    return {
        "success": True,
        "data": _project_settings({
            "id": new_project.id,
            "name": new_project.name,
            "description": new_project.description,
            "created_at": new_project.created_at.isoformat(),
            "updated_at": new_project.updated_at.isoformat(),
            "last_activity": new_project.updated_at.isoformat(),
            "analyses_count": 0,
            "status_counts": {"completed": 0, "running": 0, "failed": 0, "pending": 0},
            "samples_count": 0,
        }, new_project),
    }


@router.get("/projects")
def list_projects(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """
    List all projects with the numbers the project grid ranks, sorts and draws
    with, plus an 'Unassigned' count so the frontend can offer that bucket
    alongside real projects.

    Every number here comes from a grouped query over the whole table rather
    than a query per project, so the page costs the same at four projects and
    at four hundred.
    """
    projects = (
        db.query(Project)
        .filter(Project.owner_id == user.id)
        .order_by(Project.created_at.desc())
        .all()
    )

    # (project_id, status) -> count. Summed per project this IS the analysis
    # count, so the status breakdown and the total cannot disagree -- they are
    # the same query. A project_id of None is the Unassigned bucket.
    status_rows = (
        db.query(Analysis.project_id, Analysis.status, func.count(Analysis.id))
        .filter(Analysis.owner_id == user.id)
        .group_by(Analysis.project_id, Analysis.status)
        .all()
    )
    by_status: Dict[Optional[str], Dict[str, int]] = {}
    for project_id, status, count in status_rows:
        by_status.setdefault(project_id, {})[status or "pending"] = count

    # Latest analysis touched in each project. Taken against the project's own
    # updated_at below rather than instead of it: renaming or re-describing a
    # project is activity too, and a project with no analyses yet would
    # otherwise report no activity at all despite having just been created.
    last_analysis = dict(
        db.query(Analysis.project_id, func.max(Analysis.updated_at))
        .filter(Analysis.owner_id == user.id)
        .group_by(Analysis.project_id)
        .all()
    )

    sample_counts = dict(
        # ProjectSample carries no owner of its own -- a collected date has no
        # meaning outside its project -- so it is scoped through the join
        # instead. One place to be wrong, and this is it.
        db.query(ProjectSample.project_id, func.count(ProjectSample.id))
        .join(Project, Project.id == ProjectSample.project_id)
        .filter(Project.owner_id == user.id)
        .group_by(ProjectSample.project_id)
        .all()
    )

    items = []
    for p in projects:
        counts = by_status.get(p.id, {})
        latest = last_analysis.get(p.id)
        activity = max(latest, p.updated_at) if latest and p.updated_at else (latest or p.updated_at)
        items.append(
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "created_at": p.created_at.isoformat(),
                "updated_at": p.updated_at.isoformat(),
                "last_activity": activity.isoformat() if activity else None,
                "analyses_count": sum(counts.values()),
                # The four states Analysis.status is constrained to. Reported
                # in full, zeros included, so the client never has to decide
                # whether a missing key means none or means unknown.
                "status_counts": {
                    "completed": counts.get("completed", 0),
                    "running": counts.get("running", 0),
                    "failed": counts.get("failed", 0),
                    "pending": counts.get("pending", 0),
                },
                "samples_count": sample_counts.get(p.id, 0),
            }
        )

    return {
        "success": True,
        "data": {
            "items": items,
            "unassigned_count": sum(by_status.get(None, {}).values()),
        },
    }


@router.get("/projects/{project_id}")
def get_project(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Retrieve a project and the analyses filed under it."""
    project = owned_or_404(
        db.query(Project).filter(Project.id == project_id).first(),
        user, 'That project')

    analyses = (
        db.query(Analysis)
        .filter(Analysis.project_id == project_id)
        .filter(Analysis.owner_id == user.id)
        .order_by(Analysis.created_at.desc())
        .all()
    )

    # The two measures the project card already promises, so the page a card
    # opens into can restate them rather than silently dropping them. Both are
    # computed the same way `list_projects` computes them -- activity is the
    # latest of the project's own edit and any of its analyses', because
    # renaming a project is activity too -- so a card and its own page cannot
    # report different numbers for the same project.
    # One grouped count rather than len(a.measurements) per analysis, which
    # loaded every date of every analysis in the project to count them.
    measurement_counts = dict(
        db.query(RadiocarbonSample.analysis_id, func.count(RadiocarbonSample.id))
        .filter(RadiocarbonSample.analysis_id.in_([a.id for a in analyses]))
        .group_by(RadiocarbonSample.analysis_id)
        .all()
    ) if analyses else {}

    samples_count = (
        db.query(func.count(ProjectSample.id))
        .filter(ProjectSample.project_id == project_id)
        .scalar()
        or 0
    )
    latest = max((a.updated_at for a in analyses if a.updated_at), default=None)
    activity = max(latest, project.updated_at) if latest and project.updated_at else (latest or project.updated_at)

    return {
        "success": True,
        "data": _project_settings({
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "created_at": project.created_at.isoformat(),
            "updated_at": project.updated_at.isoformat(),
            "last_activity": activity.isoformat() if activity else None,
            "samples_count": samples_count,
            "analyses": [
                {
                    "id": a.id,
                    "name": a.name,
                    "analysis_type": a.analysis_type,
                    "status": a.status,
                    "created_at": a.created_at.isoformat(),
                    "measurements_count": measurement_counts.get(a.id, 0),
                }
                for a in analyses
            ],
        }, project),
    }


@router.patch("/projects/{project_id}")
def update_project(
    project_id: str,
    updates: ProjectUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """
    Rename a project, edit its description, or change one of its two
    coordinate settings.

    The settings are not metadata. `coordinate_precision` decides how
    precisely this project's positions may leave the app in an export or a
    figure, and `map_tiles_enabled` whether the map may ask a third-party tile
    server for imagery of them. The schema's Literal is what keeps the first
    to the four values the exporter can actually apply.
    """
    project = owned_or_404(
        db.query(Project).filter(Project.id == project_id).first(),
        user, 'That project')

    if updates.name is not None:
        if not updates.name.strip():
            raise HTTPException(status_code=400, detail="Project name cannot be empty")
        project.name = updates.name.strip()
    if updates.description is not None:
        project.description = updates.description
    if updates.coordinate_precision is not None:
        project.coordinate_precision = updates.coordinate_precision
    if updates.map_tiles_enabled is not None:
        project.map_tiles_enabled = updates.map_tiles_enabled

    db.commit()
    db.refresh(project)

    return {
        "success": True,
        "data": _project_settings({
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "created_at": project.created_at.isoformat(),
            "updated_at": project.updated_at.isoformat(),
        }, project),
    }


def _rerun_project_job(project_id: str, owner_id: str):
    """
    The work function for a 'project_rerun' job: every analysis in one
    project, in turn.

    One failure does not abort the rest -- a project is a filing cabinet, not
    a transaction -- and failures are collected in the job's detail. Progress
    counts analyses rather than sweeps, since each model converges at its own
    length.
    """
    def work(db: Session, handle: jobs.JobHandle) -> Dict:
        # Scoped to the owner as well as the project. The job runs in its own
        # session long after the request that authorised it, so it re-states
        # the constraint rather than trusting that nothing has changed.
        analyses = (
            db.query(Analysis)
            .filter(Analysis.project_id == project_id)
            .filter(Analysis.owner_id == owner_id)
            .all()
        )
        total = len(analyses)
        rerun_count = 0
        failed = []

        handle.update(progress=0.0, force=True, completed=0, total=total, failed=[])
        for analysis in analyses:
            # Between analyses, not within one: the sampler checks
            # mid-solve, and this is where a partial rerun is coherent.
            handle.check_cancelled()
            try:
                _rerun_analysis(analysis, db)
                rerun_count += 1
            except jobs.Cancelled:
                raise
            except Exception as e:
                db.rollback()
                failure = failures.classify(e)
                analysis.status = 'failed'
                analysis.error_message = failure.message
                db.commit()
                failed.append({"id": analysis.id, "name": analysis.name,
                               "error": failure.message})
            handle.update(
                progress=(rerun_count + len(failed)) / total if total else 1.0,
                force=True,
                completed=rerun_count + len(failed), total=total, failed=failed,
            )

        return {"rerun_count": rerun_count, "total": total, "failed": failed,
                "completed": rerun_count + len(failed)}
    return work


# See analysis.rerun_analysis for why 202 is set on the response rather than
# declared on the route.
@router.post("/projects/{project_id}/rerun")
def rerun_project(
    project_id: str,
    wait: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """
    Recompute every analysis in this project in place from its own persisted
    inputs (analysis._rerun_analysis). One failure does not abort the rest;
    failures are collected and reported back.

    Queued rather than run here: a Bayesian run per analysis back to back
    takes minutes, well past what a reverse proxy or browser will hold.
    Answers 202 with a job_id whose detail carries {completed, total, failed}.
    `wait=true` runs it in the request -- see analysis.rerun_analysis.
    """
    owned_or_404(
        db.query(Project).filter(Project.id == project_id).first(),
        user, 'That project')

    if wait:
        analyses = (
            db.query(Analysis)
            .filter(Analysis.project_id == project_id)
            .filter(Analysis.owner_id == user.id)
            .all()
        )
        rerun_count = 0
        failed = []
        for analysis in analyses:
            try:
                _rerun_analysis(analysis, db)
                rerun_count += 1
            except Exception as e:
                db.rollback()
                failure = failures.classify(e)
                analysis.status = 'failed'
                analysis.error_message = failure.message
                db.commit()
                failed.append({"id": analysis.id, "name": analysis.name,
                               "error": failure.message})
        return {
            "success": True,
            "data": {"rerun_count": rerun_count, "failed": failed},
        }

    job_id = jobs.submit(
        'project_rerun', project_id, _rerun_project_job(project_id, user.id),
        owner_id=user.id)
    return JSONResponse(status_code=202, content={
        "success": True,
        "data": {"project_id": project_id, "status": "queued", "job_id": job_id},
    })


@router.get("/projects/{project_id}/export")
def export_project(
    project_id: str,
    date_format: Optional[str] = None,
    p_before_bp: Optional[float] = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """
    The whole project as one zip (api/project_export.build_zip): every
    analysis's Export JSON, what each was run from, the date collection and a
    summary table across them. `date_format` and `p_before_bp` read as they do
    on a single analysis's exports.
    """
    project = owned_or_404(
        db.query(Project).filter(Project.id == project_id).first(),
        user, 'That project')
    analyses = (
        db.query(Analysis)
        .filter(Analysis.project_id == project_id)
        .filter(Analysis.owner_id == user.id)
        .order_by(Analysis.created_at, Analysis.id)
        .all()
    )
    samples = (
        db.query(ProjectSample)
        .filter(ProjectSample.project_id == project_id)
        .order_by(ProjectSample.created_at, ProjectSample.id)
        .all()
    )
    content = project_export.build_zip(
        project, analyses, samples, db, user,
        date_format=date_format, p_before_bp=p_before_bp)
    name = project_export._slug(project.name or 'project')
    return Response(
        content=content,
        media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename=project-{name}.zip'},
    )


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """
    Delete a project. Its analyses are NOT deleted, only unassigned, so
    results are never lost to a project being cleaned up.

    The project's date COLLECTION is deleted with it via the cascade on
    Project.samples: a collected date has no meaning outside its project, and
    any analysis deployed from it holds its own copy of the values.
    """
    project = owned_or_404(
        db.query(Project).filter(Project.id == project_id).first(),
        user, 'That project')

    reassigned = (
        db.query(Analysis)
        .filter(Analysis.project_id == project_id)
        .filter(Analysis.owner_id == user.id)
        .update({Analysis.project_id: None})
    )
    db.delete(project)
    db.commit()

    return {
        "success": True,
        "data": {"deleted_project_id": project_id, "unassigned_analyses": reassigned},
    }
