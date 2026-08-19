"""
A project's own date collection, and deploying dates from it into
analyses.

The point of this module: enter a site's radiocarbon dates ONCE against
the project (models.ProjectSample), then deploy subsets of them into as
many analyses as you like, each applying different model criteria
(different contexts, different stratigraphic relationships, different
TPQ/TAQ limiters). Previously a date could only be typed into a single
analysis and had to be re-typed to model it any other way.

Deploying COPIES the values into the analysis's own RadiocarbonSample
rows rather than referencing the collection -- see ProjectSample's
docstring for why (an analysis stays a self-contained snapshot of exactly
what it was run on, so editing the collection can never silently
invalidate an existing result).
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models import Project, ProjectSample, Analysis, RadiocarbonSample, SampleGroup
from app.schemas.sample import (
    ProjectSampleBulkInput,
    ProjectSampleUpdate,
    DeployRequest,
)

router = APIRouter()


def _require_project(project_id: str, db: Session) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _serialize(sample: ProjectSample) -> dict:
    return {
        "id": sample.id,
        "project_id": sample.project_id,
        "sample_name": sample.sample_name,
        "age_bp": sample.age_bp,
        "error_1sigma": sample.error_1sigma,
        "material_type": sample.material_type,
        "sample_type": sample.sample_type,
        "curve_id": sample.curve_id,
        "reservoir_age": sample.reservoir_age,
        "depth_cm": sample.depth_cm,
        "location": sample.location,
        "notes": sample.notes,
        "default_context_name": sample.default_context_name,
        "tpq_age_bp": sample.tpq_age_bp,
        "taq_age_bp": sample.taq_age_bp,
        "created_at": sample.created_at.isoformat() if sample.created_at else None,
    }


@router.get("/projects/{project_id}/samples")
def list_project_samples(project_id: str, db: Session = Depends(get_db)):
    """List every date in this project's collection, oldest entry first."""
    _require_project(project_id, db)
    samples = (
        db.query(ProjectSample)
        .filter(ProjectSample.project_id == project_id)
        .order_by(ProjectSample.created_at.asc())
        .all()
    )
    return {"success": True, "data": {"items": [_serialize(s) for s in samples]}}


@router.post("/projects/{project_id}/samples")
def create_project_samples(
    project_id: str,
    request: ProjectSampleBulkInput,
    db: Session = Depends(get_db),
):
    """
    Add one or more dates to this project's collection. Takes a list
    (rather than a single object) so the UI's bulk-paste path is one
    request rather than N.
    """
    _require_project(project_id, db)

    created = []
    for item in request.samples:
        # Same smart default as the analysis endpoints: infer 'marine' from
        # a marine curve when the caller didn't say explicitly.
        sample_type = item.sample_type or ('marine' if item.curve_id == 'marine20' else 'terrestrial')
        row = ProjectSample(
            project_id=project_id,
            sample_name=item.sample_name,
            age_bp=item.age_bp,
            error_1sigma=item.error_1sigma,
            material_type=item.material_type,
            sample_type=sample_type,
            curve_id=item.curve_id,
            reservoir_age=item.reservoir_age,
            depth_cm=item.depth_cm,
            location=item.location,
            notes=item.notes,
            default_context_name=item.default_context_name,
            tpq_age_bp=item.tpq_age_bp,
            taq_age_bp=item.taq_age_bp,
        )
        db.add(row)
        created.append(row)

    db.commit()
    for row in created:
        db.refresh(row)

    return {"success": True, "data": {"items": [_serialize(s) for s in created]}}


@router.patch("/projects/{project_id}/samples/{sample_id}")
def update_project_sample(
    project_id: str,
    sample_id: str,
    updates: ProjectSampleUpdate,
    db: Session = Depends(get_db),
):
    """
    Edit a date in the collection. Note this deliberately does NOT touch
    any analysis already deployed from it -- those hold their own copies
    (see ProjectSample's docstring).
    """
    _require_project(project_id, db)
    sample = (
        db.query(ProjectSample)
        .filter(ProjectSample.id == sample_id, ProjectSample.project_id == project_id)
        .first()
    )
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found in this project")

    provided = updates.model_dump(exclude_unset=True)
    for field, value in provided.items():
        if field == "default_context_name" and value is not None:
            value = value.strip() or None
        setattr(sample, field, value)

    # The TPQ/TAQ ordering check can't live on ProjectSampleUpdate (a
    # partial update may carry only one of the pair), so apply it here
    # against the merged result -- same rule as MeasurementInput's.
    if sample.tpq_age_bp is not None and sample.taq_age_bp is not None:
        if sample.taq_age_bp > sample.tpq_age_bp:
            db.rollback()
            raise HTTPException(
                status_code=422,
                detail=(
                    "taq_age_bp (minimum age -- terminus ante quem) cannot be "
                    "greater than tpq_age_bp (maximum age -- terminus post quem); "
                    "the valid range [taq_age_bp, tpq_age_bp] would be empty"
                ),
            )

    db.commit()
    db.refresh(sample)
    return {"success": True, "data": _serialize(sample)}


@router.delete("/projects/{project_id}/samples/{sample_id}")
def delete_project_sample(project_id: str, sample_id: str, db: Session = Depends(get_db)):
    """
    Remove a date from the collection. Analyses already deployed from it
    keep their own copies and are unaffected.
    """
    _require_project(project_id, db)
    sample = (
        db.query(ProjectSample)
        .filter(ProjectSample.id == sample_id, ProjectSample.project_id == project_id)
        .first()
    )
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found in this project")

    db.delete(sample)
    db.commit()
    return {"success": True, "data": {"deleted_sample_id": sample_id}}


@router.post("/projects/{project_id}/deploy")
def deploy_samples_to_analysis(
    project_id: str,
    request: DeployRequest,
    db: Session = Depends(get_db),
):
    """
    Create a new analysis in this project from a selection of its collected
    dates.

    Each selected date's values are copied into a fresh RadiocarbonSample
    owned by the new analysis, and dates sharing a default_context_name are
    grouped into one SampleGroup so they arrive already organised into
    contexts. Everything is then freely editable on the new analysis
    without touching the collection.

    The analysis is created with status 'pending': it has inputs but has
    not been run. Run it with POST /analysis/{id}/rerun (which computes
    from persisted inputs), typically after adjusting its stratigraphy.
    """
    _require_project(project_id, db)

    samples = (
        db.query(ProjectSample)
        .filter(ProjectSample.project_id == project_id, ProjectSample.id.in_(request.sample_ids))
        .all()
    )
    found = {s.id for s in samples}
    missing = [sid for sid in request.sample_ids if sid not in found]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"These dates are not in this project's collection: {', '.join(missing)}",
        )

    # Preserve the caller's ordering rather than the DB's.
    by_id = {s.id: s for s in samples}
    ordered = [by_id[sid] for sid in request.sample_ids]

    analysis = Analysis(
        name=request.name,
        analysis_type=request.analysis_type,
        status='pending',
        bayesian_config={},
        project_id=project_id,
    )
    db.add(analysis)
    db.flush()

    # One SampleGroup per distinct default_context_name. TPQ/TAQ stay on the
    # individual samples here rather than being hoisted onto the context --
    # the collection's defaults are per-date, and a context-wide limiter is
    # something the user sets deliberately in the stratigraphy editor.
    groups_by_name = {}
    for src in ordered:
        name = (src.default_context_name or '').strip()
        if not name or name in groups_by_name:
            continue
        group = SampleGroup(analysis_id=analysis.id, name=name)
        db.add(group)
        db.flush()
        groups_by_name[name] = group.id

    for src in ordered:
        name = (src.default_context_name or '').strip()
        db.add(RadiocarbonSample(
            analysis_id=analysis.id,
            sample_name=src.sample_name,
            age_bp=src.age_bp,
            error_1sigma=src.error_1sigma,
            material_type=src.material_type,
            sample_type=src.sample_type,
            curve_id=src.curve_id,
            reservoir_age=src.reservoir_age,
            depth_cm=src.depth_cm,
            location=src.location,
            notes=src.notes,
            group_id=groups_by_name.get(name),
            tpq_age_bp=src.tpq_age_bp,
            taq_age_bp=src.taq_age_bp,
        ))

    db.commit()
    db.refresh(analysis)

    return {
        "success": True,
        "data": {
            "id": analysis.id,
            "name": analysis.name,
            "analysis_type": analysis.analysis_type,
            "status": analysis.status,
            "project_id": analysis.project_id,
            "measurements_count": len(ordered),
            "contexts_created": len(groups_by_name),
        },
    }
