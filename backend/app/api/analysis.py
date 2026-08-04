from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas.sample import CalibrateRequest, BayesianRequest
from app.services.calibration import get_curve
from app.services.bayesian import BayesianChronology
from app.models import Analysis, RadiocarbonSample, AnalysisResult, StratigraphicRelationship, SampleGroup, Project
from typing import List, Dict, Optional
import numpy as np
import uuid

router = APIRouter()


def _validate_project_id(project_id: Optional[str], db: Session) -> Optional[str]:
    """Look up a project_id supplied on an analysis request. Returns it
    unchanged if valid/absent, raises 404 if it doesn't correspond to a
    real project (a typo'd or stale project_id would otherwise silently
    leave the analysis unassigned instead of surfacing the mistake)."""
    if not project_id:
        return None
    if not db.query(Project.id).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    return project_id

@router.post("/analysis/calibrate")
async def calibrate_single(measurement: CalibrateRequest, db: Session = Depends(get_db)):
    """
    Calibrate a single radiocarbon measurement.

    The request body is validated by CalibrateRequest (schemas/sample.py):
    age_bp/error_1sigma must be positive and within a sane range,
    sample_type and curve_id must be one of the recognized values, and
    sample_name can't be blank. Previously none of this was enforced --
    a raw Dict body meant negative ages, zero error, or a typo'd curve_id
    all passed straight through to calibration and either produced a
    nonsense result or a bare KeyError instead of a clear 422.

    Accepts two fields beyond the measurement itself:
    - project_id: file the (saved) analysis under an existing project.
      Leaving it out files the analysis as unassigned; it still shows up
      in the analysis list and can be assigned to a project later.
    - save (default True): set to False for a quick test run that
      shouldn't clutter the project/analysis list at all -- when False,
      nothing is written to the database (no Analysis, sample, or result
      rows), and the response's "id" is null since there's no persisted
      record to link to.
    """
    try:
        save = measurement.save
        project_id = _validate_project_id(measurement.project_id, db) if save else None

        # Load appropriate curve (raises ValueError if unavailable/empty
        # or, now, if curve_id somehow isn't one of the recognized ones --
        # though CalibrateRequest already rejects that before we get here).
        curve_id = measurement.curve_id
        curve = get_curve(curve_id)

        cal_ages, probs = curve.calibrate(
            measurement.age_bp,
            measurement.error_1sigma
        )
        
        # Full multi-range HPD breakdown (calibration curves are
        # non-monotonic, so the posterior for a single measurement can be
        # genuinely multimodal -- e.g. plateaus/wiggles splitting it into
        # several disjoint calendar-age ranges). hpd_68/hpd_95 below stay as
        # the overall min/max envelope for simple display; hpd_68_ranges/
        # hpd_95_ranges carry the actual per-range breakdown with each
        # range's own probability share, standard OxCal/CALIB-style.
        hpd_68_ranges = curve.get_hpd_ranges(cal_ages, probs, 0.68)
        hpd_95_ranges = curve.get_hpd_ranges(cal_ages, probs, 0.95)
        hpd_68 = (
            min(r['min'] for r in hpd_68_ranges),
            max(r['max'] for r in hpd_68_ranges),
        )
        hpd_95 = (
            min(r['min'] for r in hpd_95_ranges),
            max(r['max'] for r in hpd_95_ranges),
        )
        calibrated_age_bp = float(cal_ages[np.argmax(probs)])

        # Point estimates (mean/median/std) of the calibrated posterior --
        # previously only the mode (calibrated_age_bp) and HPD ranges were
        # computed here, so single-calibration results were persisted with
        # posterior_mean/median/std left at their nullable-column default of
        # None. That made the Results table's "Posterior Mean" column show
        # "N/A" for every single-calibration analysis, even though the
        # calibrated distribution has a perfectly well-defined mean.
        point_estimate = curve.get_point_estimate(cal_ages, probs)

        # Distribution windowed to where the probability mass actually is,
        # not an arbitrary slice of the curve's first 100 (oldest) grid
        # points -- that slice was almost always nowhere near the sample's
        # actual calendar age, so the frontend plot would render as flat.
        display_distribution = curve.get_display_distribution(cal_ages, probs)
        
        response_data = {
            "id": None,
            "name": "Single Calibration",
            "analysis_type": "calibration",
            "status": "completed",
            "saved": False,
            "project_id": None,
            "calibrated_age_bp": calibrated_age_bp,
            "posterior_mean": point_estimate['mean'],
            "posterior_median": point_estimate['median'],
            "posterior_std": point_estimate['std'],
            "hpd_68": {"min": float(hpd_68[0]), "max": float(hpd_68[1])},
            "hpd_95": {"min": float(hpd_95[0]), "max": float(hpd_95[1])},
            "hpd_68_ranges": hpd_68_ranges,
            "hpd_95_ranges": hpd_95_ranges,
            "probability_distribution": display_distribution,
            "measurements_count": 1,
            "results": []
        }

        if not save:
            # Quick test run -- nothing touches the database, so it never
            # appears in a project or in the analysis list.
            return {"success": True, "data": response_data}

        # Create analysis record in database
        analysis = Analysis(
            name='Single Calibration',
            analysis_type='calibration',
            status='completed',
            bayesian_config={},
            project_id=project_id
        )
        db.add(analysis)
        db.commit()
        db.refresh(analysis)

        # Persist the sample itself too -- previously calibrate_single never
        # created a RadiocarbonSample row at all, so this analysis had a
        # result with no underlying measurement on record, and
        # measurements_count in every response was a hardcoded `1` rather
        # than reflecting anything actually stored.
        #
        # sample_type defaults to None in the schema (rather than always
        # "terrestrial") specifically so this smart default -- infer
        # "marine" from a marine curve when the caller doesn't say
        # otherwise -- still applies.
        sample_type = measurement.sample_type or ('marine' if curve_id == 'marine20' else 'terrestrial')
        sample = RadiocarbonSample(
            analysis_id=analysis.id,
            sample_name=measurement.sample_name,
            age_bp=measurement.age_bp,
            error_1sigma=measurement.error_1sigma,
            material_type=measurement.material_type,
            sample_type=sample_type,
            curve_id=curve_id,
            reservoir_age=measurement.reservoir_age,
            depth_cm=measurement.depth_cm,
            location=measurement.location,
            notes=measurement.notes
        )
        db.add(sample)
        db.commit()
        db.refresh(sample)

        # Persist the result too -- previously only the Analysis row was
        # saved, so re-fetching this analysis later via GET
        # /api/analysis/{id} (which is what the Results page actually uses)
        # would always come back with an empty `results` list.
        analysis_result = AnalysisResult(
            analysis_id=analysis.id,
            sample_id=sample.id,
            calibrated_age_bp=calibrated_age_bp,
            posterior_mean=point_estimate['mean'],
            posterior_median=point_estimate['median'],
            posterior_std=point_estimate['std'],
            hpd_68_min=hpd_68[0],
            hpd_68_max=hpd_68[1],
            hpd_95_min=hpd_95[0],
            hpd_95_max=hpd_95[1],
            hpd_68_ranges=hpd_68_ranges,
            hpd_95_ranges=hpd_95_ranges,
            probability_distribution=display_distribution
        )
        db.add(analysis_result)
        db.commit()
        db.refresh(analysis)

        response_data["id"] = str(analysis.id)
        response_data["saved"] = True
        response_data["project_id"] = analysis.project_id
        response_data["measurements_count"] = len(analysis.measurements)

        return {"success": True, "data": response_data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/analysis/bayesian")
async def bayesian_analysis(request: BayesianRequest, db: Session = Depends(get_db)):
    """
    Perform Bayesian chronological analysis.

    The request body is validated by BayesianRequest (schemas/sample.py):
    every measurement goes through the same checks as /analysis/calibrate
    (age_bp/error_1sigma bounds, known curve_id, non-blank sample_name),
    at least one measurement is required, relationship values are
    restricted to the three bayesian.py actually implements
    (above/below/equal -- previously an unrecognized value like a typo'd
    "beneath" was silently dropped with no error and no effect on the
    model, since the if/elif chain that applies constraints has no else
    branch), and mcmc_samples/tune/chains are bounded to a sane range
    instead of accepting e.g. 0 or a negative chain count.

    Accepts the same project_id / save options as /analysis/calibrate.
    Note that with save=False the MCMC run still happens in full (there's
    no way to preview a Bayesian sequence result without actually running
    it) -- only the database writes are skipped, so nothing is left
    behind once the response is returned.
    """
    import traceback
    try:
        save = request.save
        project_id = _validate_project_id(request.project_id, db) if save else None

        # Create analysis record
        analysis = Analysis(
            name=request.name,
            analysis_type='bayesian',
            status='running',
            bayesian_config=request.bayesian_config.model_dump(),
            project_id=project_id
        )
        if save:
            db.add(analysis)
            db.commit()
            db.refresh(analysis)

        # Create group (phase) records first, so samples can reference their
        # real DB group id via groups_map (client id -> persisted id).
        groups_map = {}
        if save:
            for group in request.groups:
                group_row = SampleGroup(
                    analysis_id=analysis.id,
                    name=group.name,
                    tpq_age_bp=group.tpq_age_bp,
                    taq_age_bp=group.taq_age_bp,
                )
                db.add(group_row)
                db.flush()
                groups_map[group.id] = group_row.id
            db.commit()

        # Create sample records
        samples_map = {}
        for measurement in request.measurements:
            if save:
                sample_type = measurement.sample_type or (
                    'marine' if measurement.curve_id == 'marine20' else 'terrestrial'
                )
                sample = RadiocarbonSample(
                    analysis_id=analysis.id,
                    sample_name=measurement.sample_name,
                    age_bp=measurement.age_bp,
                    error_1sigma=measurement.error_1sigma,
                    material_type=measurement.material_type,
                    sample_type=sample_type,
                    curve_id=measurement.curve_id,
                    group_id=groups_map.get(measurement.group_id) if measurement.group_id else None,
                    tpq_age_bp=measurement.tpq_age_bp,
                    taq_age_bp=measurement.taq_age_bp,
                )
                db.add(sample)
                db.flush()
                samples_map[measurement.id] = sample.id
        if save:
            db.commit()

        # Persist the stratigraphic relationships themselves too --
        # previously these were only ever used transiently to build the
        # MCMC model and never written to stratigraphic_relationships at
        # all, so there was no way to review or re-display which
        # constraints a saved analysis actually used. from_sample_id/
        # to_sample_id are translated from the request's client-side ids
        # to their real persisted sample/group ids.
        if save:
            def _resolve_id(client_id: str, node_type: str) -> str:
                return groups_map[client_id] if node_type == 'group' else samples_map[client_id]

            for rel in request.relationships:
                db.add(StratigraphicRelationship(
                    analysis_id=analysis.id,
                    from_sample_id=_resolve_id(rel.from_sample_id, rel.from_type),
                    to_sample_id=_resolve_id(rel.to_sample_id, rel.to_type),
                    from_type=rel.from_type,
                    to_type=rel.to_type,
                    relationship=rel.relationship,
                ))
            db.commit()

        bayesian = BayesianChronology(
            mcmc_samples=request.bayesian_config.mcmc_samples,
            mcmc_tune=request.bayesian_config.mcmc_tune,
            mcmc_chains=request.bayesian_config.mcmc_chains
        )

        # build_model() works with plain dicts (measurements keyed by
        # 'id'/'age_bp'/etc, relationships keyed by 'from_sample_id' etc)
        # -- converting the validated Pydantic models to dicts here keeps
        # bayesian.py's existing dict-based interface untouched. This uses
        # the request's own client-side ids throughout (not the persisted
        # DB ids), since build_model() needs to match against `ages`,
        # which is also keyed by the client-side measurement id.
        model, ages = bayesian.build_model(
            [m.model_dump() for m in request.measurements],
            [r.model_dump() for r in request.relationships],
            [g.model_dump() for g in request.groups],
        )

        idata, ages_dict = bayesian.sample(model, ages)
        results = bayesian.extract_results(idata, ages_dict)

        if save:
            # Store results in database using the sample IDs
            for measurement_id, result in results.items():
                sample_id = samples_map.get(measurement_id, measurement_id)
                analysis_result = AnalysisResult(
                    analysis_id=analysis.id,
                    sample_id=sample_id,
                    posterior_mean=result['posterior_mean'],
                    posterior_median=result['posterior_median'],
                    posterior_std=result['posterior_std'],
                    hpd_68_min=result['hpd_68'][0],
                    hpd_68_max=result['hpd_68'][1],
                    hpd_95_min=result['hpd_95'][0],
                    hpd_95_max=result['hpd_95'][1],
                    hpd_68_ranges=result['hpd_68_ranges'],
                    hpd_95_ranges=result['hpd_95_ranges'],
                    posterior_samples=result['posterior_samples'],
                    probability_distribution=result['probability_distribution']
                )
                db.add(analysis_result)

            analysis.status = 'completed'
            db.commit()

        # Prepare response with summary stats + the compact plottable
        # distribution (exclude the huge raw posterior_samples array, which
        # can be tens of thousands of floats per sample -- the frontend only
        # ever needs the binned distribution to render the plot)
        results_summary = {}
        for measurement_id, result in results.items():
            results_summary[measurement_id] = {
                'posterior_mean': result['posterior_mean'],
                'posterior_median': result['posterior_median'],
                'posterior_std': result['posterior_std'],
                'hpd_68': result['hpd_68'],
                'hpd_95': result['hpd_95'],
                'hpd_68_ranges': result['hpd_68_ranges'],
                'hpd_95_ranges': result['hpd_95_ranges'],
                'probability_distribution': result['probability_distribution']
            }

        return {
            "success": True,
            "data": {
                "id": str(analysis.id) if save else None,
                "name": request.name,
                "analysis_type": "bayesian",
                "status": "completed",
                "saved": save,
                "project_id": (analysis.project_id if save else None),
                "results": results_summary,
                "measurements_count": len(request.measurements)
            }
        }
    except Exception as e:
        db.rollback()
        import traceback
        print(f"Bayesian analysis error: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/analysis/{analysis_id}")
async def get_analysis(analysis_id: str, db: Session = Depends(get_db)):
    """Retrieve analysis results"""
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    results = db.query(AnalysisResult).filter(AnalysisResult.analysis_id == analysis_id).all()
    samples_by_id = {s.id: s for s in analysis.measurements}

    relationships = db.query(StratigraphicRelationship).filter(
        StratigraphicRelationship.analysis_id == analysis_id
    ).all()
    groups = db.query(SampleGroup).filter(SampleGroup.analysis_id == analysis_id).all()

    return {
        "success": True,
        "data": {
            "id": analysis.id,
            "name": analysis.name,
            "analysis_type": analysis.analysis_type,
            "status": analysis.status,
            "created_at": analysis.created_at.isoformat(),
            "measurements_count": len(analysis.measurements),
            "project_id": analysis.project_id,
            "project_name": analysis.project.name if analysis.project else None,
            # Real per-sample data, plus the stratigraphy (groups/
            # relationships/TPQ-TAQ) actually used for this analysis, if
            # any -- lets the Results page render a real (not "Sample N"
            # placeholder) results table and, for a bayesian analysis, a
            # read-only Harris matrix diagram of the constraints that went
            # into the model.
            "measurements": [
                {
                    "id": s.id,
                    "sample_name": s.sample_name,
                    "age_bp": s.age_bp,
                    "error_1sigma": s.error_1sigma,
                    "group_id": s.group_id,
                    "tpq_age_bp": s.tpq_age_bp,
                    "taq_age_bp": s.taq_age_bp,
                }
                for s in analysis.measurements
            ],
            "groups": [
                {
                    "id": g.id,
                    "name": g.name,
                    "tpq_age_bp": g.tpq_age_bp,
                    "taq_age_bp": g.taq_age_bp,
                }
                for g in groups
            ],
            "relationships": [
                {
                    "id": r.id,
                    "from_sample_id": r.from_sample_id,
                    "to_sample_id": r.to_sample_id,
                    "from_type": r.from_type,
                    "to_type": r.to_type,
                    "relationship": r.relationship,
                }
                for r in relationships
            ],
            "results": [
                {
                    "sample_id": r.sample_id,
                    "sample_name": samples_by_id[r.sample_id].sample_name if r.sample_id in samples_by_id else None,
                    "posterior_mean": r.posterior_mean,
                    "posterior_median": r.posterior_median,
                    "posterior_std": r.posterior_std,
                    "hpd_68": [r.hpd_68_min, r.hpd_68_max] if r.hpd_68_min is not None else None,
                    "hpd_95": [r.hpd_95_min, r.hpd_95_max] if r.hpd_95_min is not None else None,
                    "hpd_68_ranges": r.hpd_68_ranges,
                    "hpd_95_ranges": r.hpd_95_ranges,
                    "probability_distribution": r.probability_distribution
                }
                for r in results
            ]
        }
    }

@router.get("/analysis")
async def list_analyses(
    page: int = 1,
    per_page: int = 10,
    project_id: Optional[str] = None,
    unassigned: bool = False,
    db: Session = Depends(get_db),
):
    """
    List analyses, most recent first.

    By default returns analyses across all projects (used for the
    dashboard's short "recent activity" list). Pass project_id to scope
    to one project (used by the project detail page), or unassigned=true
    to list only analyses that aren't filed under any project.
    """
    query = db.query(Analysis)
    if unassigned:
        query = query.filter(Analysis.project_id.is_(None))
    elif project_id:
        query = query.filter(Analysis.project_id == project_id)

    total = query.count()
    analyses = query.order_by(Analysis.created_at.desc()).offset(
        (page - 1) * per_page
    ).limit(per_page).all()

    return {
        "success": True,
        "data": {
            "items": [
                {
                    "id": a.id,
                    "name": a.name,
                    "analysis_type": a.analysis_type,
                    "status": a.status,
                    "created_at": a.created_at.isoformat(),
                    "measurements_count": len(a.measurements),
                    "project_id": a.project_id,
                    "project_name": a.project.name if a.project else None,
                }
                for a in analyses
            ],
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page
        }
    }


@router.patch("/analysis/{analysis_id}")
async def update_analysis(analysis_id: str, updates: Dict, db: Session = Depends(get_db)):
    """
    Update an analysis's project assignment (and/or name). Used to file an
    existing analysis into a project after the fact, or move it between
    projects -- pass project_id: null to unassign it.
    """
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    if 'project_id' in updates:
        analysis.project_id = _validate_project_id(updates['project_id'], db)
    if updates.get('name'):
        analysis.name = updates['name']

    db.commit()
    db.refresh(analysis)

    return {
        "success": True,
        "data": {
            "id": analysis.id,
            "name": analysis.name,
            "project_id": analysis.project_id,
            "project_name": analysis.project.name if analysis.project else None,
        },
    }
