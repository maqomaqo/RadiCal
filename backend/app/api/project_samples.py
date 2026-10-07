"""
A project's own date collection, and deploying dates from it into analyses.

Enter a site's dates ONCE against the project (models.ProjectSample), then
deploy subsets into as many analyses as you like, each with its own contexts,
relationships and limiters.

Deploying COPIES the values into the analysis's own RadiocarbonSample rows
rather than referencing the collection, so an analysis stays a self-contained
snapshot of what it was run on. See ProjectSample's docstring.
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models import Project, ProjectSample, Analysis, RadiocarbonSample, SampleGroup, User
from app.api.auth import current_user, owned_or_404
from app.api.curves import authorise_curve_ids
from app.schemas.sample import (
    ProjectSampleBulkInput,
    ProjectSampleBulkDelete,
    ProjectSampleBulkUpdate,
    ProjectSampleUpdate,
    DeployRequest,
)
from app.services import curve_registry

router = APIRouter()


def _require_project(project_id: str, db: Session, user: User) -> Project:
    """
    The project, if it is this user's.

    Every route in this file goes through here, which is why the ownership
    check lives here and not in each of them: a ProjectSample carries no owner
    of its own -- a collected date has no meaning outside its project -- so the
    project IS the access boundary for everything below.

    Someone else's project is a 404, like a missing one. See
    api/auth.owned_or_404.
    """
    return owned_or_404(
        db.query(Project).filter(Project.id == project_id).first(),
        user, 'That project')


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
        "delta_r": sample.delta_r,
        "delta_r_error": sample.delta_r_error,
        # NULL means hand-entered, which is not the same as unknown: it is the
        # app saying it does not know where this number came from, honestly.
        "delta_r_source": sample.delta_r_source,
        # Full precision, always. project.coordinate_precision governs what
        # leaves the app in an EXPORT or a figure (api/results.py), not what
        # the app's own UI reads back to the owner of the project.
        "latitude": sample.latitude,
        "longitude": sample.longitude,
        "depth_cm": sample.depth_cm,
        # The wiggle-match ordinate. A property of the SAMPLE -- which ring of
        # which timber it came from -- so it belongs to the collection rather
        # than to any one analysis, and deploying carries it across.
        "ring_offset": sample.ring_offset,
        "ring_offset_error": sample.ring_offset_error,
        # Also a property of the sample -- what it is made of and how old that
        # material may be -- so the collection holds it and deploying carries it.
        "inbuilt_years": sample.inbuilt_years,
        "inbuilt_shape": sample.inbuilt_shape,
        "location": sample.location,
        "notes": sample.notes,
        "default_context_name": sample.default_context_name,
        "tpq_age_bp": sample.tpq_age_bp,
        "taq_age_bp": sample.taq_age_bp,
        "created_at": sample.created_at.isoformat() if sample.created_at else None,
    }


@router.get("/projects/{project_id}/samples")
def list_project_samples(project_id: str, db: Session = Depends(get_db),
                         user: User = Depends(current_user)):
    """List every date in this project's collection, oldest entry first."""
    _require_project(project_id, db, user)
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
    user: User = Depends(current_user),
):
    """
    Add dates to this project's collection. Takes a list so the UI's
    bulk-paste path is one request rather than N.
    """
    _require_project(project_id, db, user)
    # Every curve named here must be one this user may calibrate against.
    # The schema has already checked the ids name real curves; this is the
    # separate question of whether they are theirs. See api/curves.py.
    authorise_curve_ids([item.curve_id for item in request.samples], user.id)

    created = []
    for item in request.samples:
        # As on the analysis endpoints: infer 'marine' from a marine curve
        # when the caller does not say.
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
            delta_r=item.delta_r,
            delta_r_error=item.delta_r_error,
            latitude=item.latitude,
            longitude=item.longitude,
            depth_cm=item.depth_cm,
            ring_offset=item.ring_offset,
            ring_offset_error=item.ring_offset_error,
            inbuilt_years=item.inbuilt_years,
            inbuilt_shape=item.inbuilt_shape,
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
    user: User = Depends(current_user),
):
    """
    Edit a date in the collection. Analyses already deployed from it hold
    their own copies and are untouched (see ProjectSample).
    """
    _require_project(project_id, db, user)
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

    # A ΔR edited here is a ΔR typed by a person, so any derivation stored
    # against it stops being true of it. This form has no way to send one --
    # deriving a value is the lookup's job, through the bulk path -- so
    # touching either half clears it. See §5.5: provenance that survives an
    # edit is a lie about the number beside it.
    if 'delta_r' in provided or 'delta_r_error' in provided:
        sample.delta_r_source = None

    # The ordering check cannot live on ProjectSampleUpdate, which may carry
    # only one of the pair, so it is applied to the merged result here.
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

    # Likewise the both-or-neither coordinate rule: a PATCH carrying only a
    # longitude is correcting one half of a position that is already there,
    # which is legitimate, so what has to hold is the MERGED row.
    if (sample.latitude is None) != (sample.longitude is None):
        db.rollback()
        present, missing = (
            ('latitude', 'longitude') if sample.longitude is None
            else ('longitude', 'latitude'))
        raise HTTPException(
            status_code=422,
            detail=(
                f"this edit would leave the date with a {present} and no "
                f"{missing} -- a position needs both halves, so send the other "
                "one too, or clear them together"
            ),
        )

    db.commit()
    db.refresh(sample)
    return {"success": True, "data": _serialize(sample)}


@router.delete("/projects/{project_id}/samples/{sample_id}")
def delete_project_sample(project_id: str, sample_id: str, db: Session = Depends(get_db),
                          user: User = Depends(current_user)):
    """
    Remove a date from the collection. Analyses already deployed from it
    keep their own copies and are unaffected.
    """
    _require_project(project_id, db, user)
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


def _is_marine(sample: ProjectSample) -> bool:
    """
    Whether a Delta-R means anything for this date. The declared sample type
    answers it when set, otherwise the curve does, so an imported marine curve
    counts the same as Marine20. Mirrors isMarineMeasurement in the frontend's
    MeasurementFields.
    """
    if (sample.sample_type or 'terrestrial') == 'marine':
        return True
    try:
        return curve_registry.get_spec(sample.curve_id).sample_type == 'marine'
    except Exception:
        # An unresolvable curve is no evidence either way, and the declared
        # type has already had its say.
        return False


@router.patch("/projects/{project_id}/samples")
def bulk_update_project_samples(
    project_id: str,
    request: ProjectSampleBulkUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """
    Apply one value across a selection of this project's dates. None of the
    three is a plain assignment.

    **curve_id** carries the sample type with it, since a date calibrated
    against a marine curve is marine; the single-date form keeps the two in
    step in the other direction.

    **delta_r** is applied only to the marine dates in the selection, and the
    response says how many were skipped. On a terrestrial date the offset is
    not merely unnecessary but wrong -- it would be applied at calibration and
    move the result. Skipping rather than refusing lets a mixed selection
    work, which matters because "everything from this site" is how dates
    sharing an offset are naturally selected.

    **delta_r_source** rides with delta_r and is cleared by its absence. A ΔR
    the lookup derived arrives with the record that reproduces it; a ΔR
    somebody typed arrives without one, and that clears whatever was there.
    Provenance surviving a hand edit would describe a number it did not
    produce -- see §5.5 of the spatial plan.

    **latitude/longitude** go to the whole selection, marine or not, because
    where a date came from is a fact about the place rather than about the
    material. This is the path the map's "place on map" uses: dates from one
    site share a position, so a pin is one request.

    `delta_r: null` clears the offset and its uncertainty, and a null
    coordinate pair unplaces the selection; omitting the key leaves them
    alone. The endpoint reads `model_fields_set` rather than checking for
    None, so the two stay distinguishable.

    Analyses already deployed from these dates are untouched.
    """
    _require_project(project_id, db, user)

    samples = (
        db.query(ProjectSample)
        .filter(ProjectSample.project_id == project_id,
                ProjectSample.id.in_(request.sample_ids))
        .all()
    )
    found = {s.id for s in samples}
    missing = [sid for sid in request.sample_ids if sid not in found]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"These dates are not in this project's collection: {', '.join(missing)}",
        )

    sent = request.model_fields_set
    changed, skipped, type_changed = set(), [], 0

    if 'curve_id' in sent:
        authorise_curve_ids([request.curve_id], user.id)
        spec = curve_registry.get_spec(request.curve_id)
        sample_type = 'marine' if spec.sample_type == 'marine' else 'terrestrial'
        for sample in samples:
            sample.curve_id = request.curve_id
            if (sample.sample_type or 'terrestrial') != sample_type:
                sample.sample_type = sample_type
                type_changed += 1
            changed.add(sample.id)

    if 'latitude' in sent:
        # Unlike delta_r below, this applies to the WHOLE selection: where a
        # date came from is a fact about the place, and a terrestrial date has
        # a position just as much as a marine one does. The schema has already
        # checked both halves arrived together.
        for sample in samples:
            sample.latitude = request.latitude
            sample.longitude = request.longitude
            changed.add(sample.id)

    if 'delta_r' in sent:
        for sample in samples:
            # Read AFTER any curve change above, so one request assigning a
            # marine curve and an offset applies the offset to the dates it
            # just made marine.
            if _is_marine(sample):
                sample.delta_r = request.delta_r
                sample.delta_r_error = (
                    request.delta_r_error if request.delta_r is not None else None)
                # An offset arriving WITHOUT a derivation is a hand edit, and
                # any provenance already stored is now a lie about the number
                # beside it. Cleared rather than kept: a record saying this
                # value came from five map numbers, sitting next to a value
                # somebody typed, is worse than no record at all.
                sample.delta_r_source = request.delta_r_source
                changed.add(sample.id)
            else:
                skipped.append(sample.id)

    # skipped_sample_ids is specifically "the offset did not land here", and a
    # terrestrial date in a request that also carried a coordinate can be in
    # both lists: it was skipped for the offset and updated for the position.
    updated = [s.id for s in samples if s.id in changed]

    db.commit()
    for sample in samples:
        db.refresh(sample)

    return {
        "success": True,
        "data": {
            "updated_count": len(updated),
            "skipped_count": len(skipped),
            "skipped_sample_ids": skipped,
            "sample_type_changed_count": type_changed,
            "items": [_serialize(s) for s in samples],
        },
    }


@router.delete("/projects/{project_id}/samples")
def bulk_delete_project_samples(
    project_id: str,
    request: ProjectSampleBulkDelete,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """
    Remove several dates from the collection at once.

    All-or-nothing on the ids: an id not in this project fails the whole
    request, because a caller cannot recover from a partial delete without
    diffing the collection afterwards.

    The ids travel in the body rather than the query string, which has a
    length limit 500 uuids (about 18KB) would exceed. An intermediary that
    strips the body produces a 422, not a silent no-op.

    Analyses already deployed from these dates are unaffected.
    """
    _require_project(project_id, db, user)

    samples = (
        db.query(ProjectSample)
        .filter(ProjectSample.project_id == project_id,
                ProjectSample.id.in_(request.sample_ids))
        .all()
    )
    found = {s.id for s in samples}
    missing = [sid for sid in request.sample_ids if sid not in found]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"These dates are not in this project's collection: {', '.join(missing)}",
        )

    for sample in samples:
        db.delete(sample)
    db.commit()

    return {
        "success": True,
        "data": {"deleted_count": len(found), "deleted_sample_ids": sorted(found)},
    }


@router.post("/projects/{project_id}/deploy")
def deploy_samples_to_analysis(
    project_id: str,
    request: DeployRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """
    Create a new analysis in this project from a selection of its dates.

    Each date's values are copied into a fresh RadiocarbonSample, and dates
    sharing a default_context_name are grouped into one SampleGroup so they
    arrive already organised into contexts. Everything is then editable
    without touching the collection.

    Created with status 'pending' -- inputs but no results. Run it with
    POST /analysis/{id}/rerun, typically after adjusting its stratigraphy.

    Deploying more than one date as `calibration` produces a
    `group_calibration`; `data.analysis_type` reports the type created.
    """
    _require_project(project_id, db, user)

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

    # 'calibration' means ONE date calibrated on its own. Several independent
    # dates with no model between them is what 'group_calibration' is, so that
    # is what the caller gets; the response reports the type created rather
    # than echoing the one asked for.
    analysis_type = request.analysis_type
    if analysis_type == 'calibration' and len(ordered) > 1:
        analysis_type = 'group_calibration'

    analysis = Analysis(
        name=request.name,
        analysis_type=analysis_type,
        status='pending',
        bayesian_config={},
        project_id=project_id,
        owner_id=user.id,
    )
    db.add(analysis)
    db.flush()

    # One SampleGroup per distinct default_context_name. TPQ/TAQ stay
    # per-sample, as the collection's defaults are: a context-wide limiter is
    # set deliberately in the stratigraphy editor.
    groups_by_name = {}
    for src in ordered:
        name = (src.default_context_name or '').strip()
        if not name or name in groups_by_name:
            continue
        group = SampleGroup(analysis_id=analysis.id, name=name)
        db.add(group)
        db.flush()
        groups_by_name[name] = group.id

    for position, src in enumerate(ordered):
        name = (src.default_context_name or '').strip()
        db.add(RadiocarbonSample(
            analysis_id=analysis.id,
            # `ordered` is the deploy order the caller chose; carrying it as
            # sort_order means the analysis opens with the dates arranged the
            # way they were deployed rather than in an arbitrary one.
            sort_order=position,
            sample_name=src.sample_name,
            age_bp=src.age_bp,
            error_1sigma=src.error_1sigma,
            material_type=src.material_type,
            sample_type=src.sample_type,
            curve_id=src.curve_id,
            reservoir_age=src.reservoir_age,
            # Carried across: a reservoir offset belongs to where the sample
            # came from, so a deployed date arrives already corrected.
            delta_r=src.delta_r,
            delta_r_error=src.delta_r_error,
            # Carried too: a deployed date whose ΔR kept its value but lost
            # its derivation would be an offset nobody could account for.
            delta_r_source=src.delta_r_source,
            # And for the same reason: where the sample came from travels with
            # it. An analysis that lost the coordinates could not be mapped,
            # and a dispersal model reads them from its own deployed copies.
            latitude=src.latitude,
            longitude=src.longitude,
            depth_cm=src.depth_cm,
            # Carried across for the same reason as Delta-R above: a ring
            # offset is where the sample sits in its own series, and a
            # deployed date that lost it is no longer a member of one.
            ring_offset=src.ring_offset,
            ring_offset_error=src.ring_offset_error,
            # And the inbuilt-age allowance: how old the material may be is a
            # fact about the sample, wherever it is modelled.
            inbuilt_years=src.inbuilt_years,
            inbuilt_shape=src.inbuilt_shape,
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
