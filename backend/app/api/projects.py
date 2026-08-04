from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models import Project, Analysis
from app.schemas.project import ProjectCreate, ProjectUpdate
from app.api.analysis import _rerun_analysis
from typing import Optional

router = APIRouter()


@router.post("/projects")
async def create_project(project: ProjectCreate, db: Session = Depends(get_db)):
    """Create a new project to group analyses under."""
    if not project.name or not project.name.strip():
        raise HTTPException(status_code=400, detail="Project name is required")

    new_project = Project(name=project.name.strip(), description=project.description)
    db.add(new_project)
    db.commit()
    db.refresh(new_project)

    return {
        "success": True,
        "data": {
            "id": new_project.id,
            "name": new_project.name,
            "description": new_project.description,
            "created_at": new_project.created_at.isoformat(),
            "updated_at": new_project.updated_at.isoformat(),
            "analyses_count": 0,
        },
    }


@router.get("/projects")
async def list_projects(db: Session = Depends(get_db)):
    """
    List all projects, each with its analysis count, plus a synthetic
    'Unassigned' bucket for analyses that aren't filed under any project
    (id: null) so the frontend can offer it as a filter/destination
    alongside real projects.
    """
    projects = db.query(Project).order_by(Project.created_at.desc()).all()

    counts = dict(
        db.query(Analysis.project_id, func.count(Analysis.id))
        .group_by(Analysis.project_id)
        .all()
    )

    items = [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "created_at": p.created_at.isoformat(),
            "updated_at": p.updated_at.isoformat(),
            "analyses_count": counts.get(p.id, 0),
        }
        for p in projects
    ]

    return {
        "success": True,
        "data": {
            "items": items,
            "unassigned_count": counts.get(None, 0),
        },
    }


@router.get("/projects/{project_id}")
async def get_project(project_id: str, db: Session = Depends(get_db)):
    """Retrieve a project and the analyses filed under it."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    analyses = (
        db.query(Analysis)
        .filter(Analysis.project_id == project_id)
        .order_by(Analysis.created_at.desc())
        .all()
    )

    return {
        "success": True,
        "data": {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "created_at": project.created_at.isoformat(),
            "updated_at": project.updated_at.isoformat(),
            "analyses": [
                {
                    "id": a.id,
                    "name": a.name,
                    "analysis_type": a.analysis_type,
                    "status": a.status,
                    "created_at": a.created_at.isoformat(),
                    "measurements_count": len(a.measurements),
                }
                for a in analyses
            ],
        },
    }


@router.patch("/projects/{project_id}")
async def update_project(project_id: str, updates: ProjectUpdate, db: Session = Depends(get_db)):
    """Rename a project or edit its description."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if updates.name is not None:
        if not updates.name.strip():
            raise HTTPException(status_code=400, detail="Project name cannot be empty")
        project.name = updates.name.strip()
    if updates.description is not None:
        project.description = updates.description

    db.commit()
    db.refresh(project)

    return {
        "success": True,
        "data": {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "created_at": project.created_at.isoformat(),
            "updated_at": project.updated_at.isoformat(),
        },
    }


@router.post("/projects/{project_id}/rerun")
async def rerun_project(project_id: str, db: Session = Depends(get_db)):
    """
    Recompute every analysis in this project in place, from each one's own
    persisted inputs (see analysis.py's _rerun_analysis) -- one analysis
    failing (e.g. a curve/MCMC error) doesn't abort the rest; failures are
    collected and reported back instead. Runs synchronously in this
    request (no background job queue exists yet), so this can take a
    while for a project with several/large Bayesian analyses.
    """
    project = db.query(Project.id).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    analyses = db.query(Analysis).filter(Analysis.project_id == project_id).all()

    rerun_count = 0
    failed = []
    for analysis in analyses:
        try:
            _rerun_analysis(analysis, db)
            rerun_count += 1
        except Exception as e:
            db.rollback()
            analysis.status = 'failed'
            analysis.error_message = str(e)
            db.commit()
            failed.append({"id": analysis.id, "name": analysis.name, "error": str(e)})

    return {
        "success": True,
        "data": {"rerun_count": rerun_count, "failed": failed},
    }


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, db: Session = Depends(get_db)):
    """
    Delete a project. Analyses filed under it are NOT deleted -- they're
    unassigned (project_id set to NULL) so calibration results are never
    lost just because a project got cleaned up or renamed away.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    reassigned = (
        db.query(Analysis)
        .filter(Analysis.project_id == project_id)
        .update({Analysis.project_id: None})
    )
    db.delete(project)
    db.commit()

    return {
        "success": True,
        "data": {"deleted_project_id": project_id, "unassigned_analyses": reassigned},
    }
