from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas.sample import CalibrateRequest, BayesianRequest
from app.services.calibration import get_curve, CalibrationCurve
from app.services.bayesian import BayesianChronology
from app.services.model_tree import (
    migrate_from_relationships,
    rows_to_tree,
    tree_to_rows,
    validate_tree,
)
from app.models import (
    Analysis, RadiocarbonSample, AnalysisResult, StratigraphicRelationship,
    SampleGroup, Project, ModelElement,
)
from typing import List, Dict, Optional, Tuple
import numpy as np
import uuid

router = APIRouter()

# The confidence levels the Results page offers (its CONFIDENCE_OPTIONS):
# the Gaussian-equivalent 1/2/3-sigma coverages radiocarbon results are
# conventionally reported at. Unmodelled HPD ranges are precomputed at all
# three so switching the selector doesn't need a refetch -- and so the page
# never has to derive them from a downsampled plot series, which is what
# used to make it disagree with the CSV in the last digit.
REPORTING_CONFIDENCE_LEVELS = (0.682, 0.954, 0.997)


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


def _compute_calibration_stats(
    curve: CalibrationCurve,
    age_bp: float,
    error_1sigma: float,
    delta_r: Optional[float] = None,
    delta_r_error: Optional[float] = None,
) -> Dict:
    """
    Calibrate one measurement against `curve` and return every derived
    stat the calibration-analysis path needs: point estimates, the overall
    HPD envelope, the full multi-range HPD breakdown, and a plottable
    distribution. Shared between calibrate_single (a fresh calibration)
    and the analysis rerun path (re-calibrating an already-saved sample
    with its current age_bp/error_1sigma/curve), so the two can't drift
    out of sync with each other.
    """
    cal_ages, probs = curve.calibrate(age_bp, error_1sigma, delta_r, delta_r_error)

    # Full multi-range HPD breakdown (calibration curves are non-monotonic,
    # so the posterior for a single measurement can be genuinely multimodal
    # -- e.g. plateaus/wiggles splitting it into several disjoint
    # calendar-age ranges). hpd_68/hpd_95 below stay as the overall min/max
    # envelope for simple display; hpd_68_ranges/hpd_95_ranges carry the
    # actual per-range breakdown with each range's own probability share,
    # standard OxCal/CALIB-style.
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
    point_estimate = curve.get_point_estimate(cal_ages, probs)
    display_distribution = curve.get_display_distribution(cal_ages, probs)

    return {
        'calibrated_age_bp': calibrated_age_bp,
        'posterior_mean': point_estimate['mean'],
        'posterior_median': point_estimate['median'],
        'posterior_std': point_estimate['std'],
        'hpd_68': hpd_68,
        'hpd_95': hpd_95,
        'hpd_68_ranges': hpd_68_ranges,
        'hpd_95_ranges': hpd_95_ranges,
        'probability_distribution': display_distribution,
    }


def _iter_tree(element: Dict):
    """Depth-first walk of a nested tree dict."""
    yield element
    for child in element.get('children') or []:
        yield from _iter_tree(child)


def _relabel_tree(root: Dict, id_map: Dict[str, str]) -> None:
    """
    Rewrite element ids through `id_map` in place. Used when persisting a
    tree built from a request's client-side ids: the phases carry the
    request's context ids, which have to become the ids the contexts were
    actually saved under.
    """
    for element in _iter_tree(root):
        if element['id'] in id_map:
            element['id'] = id_map[element['id']]


def _persist_tree(analysis_id: str, root: Dict, db: Session) -> None:
    """Replace an analysis's stored model tree with `root`."""
    db.query(ModelElement).filter(
        ModelElement.analysis_id == analysis_id
    ).delete(synchronize_session=False)
    db.flush()
    for row in tree_to_rows(root, analysis_id, ModelElement):
        db.add(row)
    db.flush()


def _get_or_migrate_tree(analysis: Analysis, db: Session) -> Tuple[Optional[Dict], bool]:
    """
    The analysis's model tree, migrating it from the old contexts + pairwise
    relationships representation on first access if it doesn't have one yet.

    Returns (root, linearized). `linearized` flags that the old relationship
    graph couldn't be expressed as a tree and was flattened into one order,
    which is strictly MORE constrained than what it came from and worth
    telling the user about.

    Migrating here (lazily, on read) rather than in a batch job means an old
    analysis keeps working untouched until something actually looks at it.
    """
    rows = db.query(ModelElement).filter(ModelElement.analysis_id == analysis.id).all()
    existing = rows_to_tree(rows)
    if existing is not None:
        return existing, False

    samples = list(analysis.measurements)
    if not samples:
        return None, False

    groups = db.query(SampleGroup).filter(SampleGroup.analysis_id == analysis.id).all()
    relationships = db.query(StratigraphicRelationship).filter(
        StratigraphicRelationship.analysis_id == analysis.id
    ).all()

    root, linearized, _group_boundaries = migrate_from_relationships(
        [{'id': s.id, 'group_id': s.group_id} for s in samples],
        [{'id': g.id, 'name': g.name, 'tpq_age_bp': g.tpq_age_bp, 'taq_age_bp': g.taq_age_bp}
         for g in groups],
        [{'from_sample_id': r.from_sample_id, 'to_sample_id': r.to_sample_id,
          'from_type': r.from_type, 'to_type': r.to_type, 'relationship': r.relationship}
         for r in relationships],
        lambda: str(uuid.uuid4()),
    )

    # Dates that were in no context need a phase of their own.
    loose = [s for s in samples if not s.group_id]
    if loose:
        loose_id = str(uuid.uuid4())
        root['children'].append({
            'id': loose_id, 'element_type': 'phase', 'name': 'Unassigned dates',
            'tpq_age_bp': None, 'taq_age_bp': None, 'children': [],
        })
        for s in loose:
            s.element_id = loose_id
    for s in samples:
        if s.group_id:
            s.element_id = s.group_id

    _persist_tree(analysis.id, root, db)
    db.commit()
    return root, linearized


def _run_bayesian(
    measurements: List[Dict],
    model_tree: Dict,
    bayesian_config: Dict,
) -> Tuple[Dict, Dict]:
    """
    Run BayesianChronology's build/sample/extract sequence against an
    explicit model tree, returning (per-sample results, per-boundary
    results). Shared between a fresh run and a rerun so the two can't drift
    apart.
    """
    bayesian = BayesianChronology(
        mcmc_samples=bayesian_config.get('mcmc_samples', 2000),
        mcmc_tune=bayesian_config.get('mcmc_tune', 1000),
        mcmc_chains=bayesian_config.get('mcmc_chains', 4),
    )
    model, ages = bayesian.build_model(measurements, model_tree)
    idata, ages_dict = bayesian.sample(model, ages)
    extracted = bayesian.extract_results(idata, ages_dict, measurements=measurements)
    return extracted['measurements'], extracted['boundaries']


def _tree_from_request(request: BayesianRequest) -> Tuple[Dict, Dict, List[Dict]]:
    """
    Build a model tree for an incoming /analysis/bayesian request, which
    still describes stratigraphy as contexts plus above/below relationships.

    Returns (root, group_boundaries, tree_measurements) -- group_boundaries
    maps each context to the pair of boundaries now bracketing it, so results
    can still be reported per context.
    """
    measurements = [m.model_dump() for m in request.measurements]

    if request.model_tree is not None:
        # The client authored the structure directly -- use it as given.
        # There are no contexts to bracket, so nothing maps to the old
        # per-context start/end shape; results are reported per boundary.
        root = request.model_tree
        tree_measurements = [
            {**m, 'element_id': m.get('element_id')} for m in measurements
        ]
        return root, {}, tree_measurements

    groups = [g.model_dump() for g in request.groups]
    relationships = [r.model_dump() for r in request.relationships]

    root, _linearized, group_boundaries = migrate_from_relationships(
        measurements, groups, relationships, lambda: str(uuid.uuid4()),
    )
    tree_measurements = [
        {**m, 'element_id': m.get('group_id')} for m in measurements
    ]
    loose = [m for m in tree_measurements if not m.get('element_id')]
    if loose:
        loose_id = str(uuid.uuid4())
        root['children'].append({
            'id': loose_id, 'element_type': 'phase', 'name': 'Unassigned dates',
            'tpq_age_bp': None, 'taq_age_bp': None, 'children': [],
        })
        for m in loose:
            m['element_id'] = loose_id
    return root, group_boundaries, tree_measurements


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

        # Point estimates, HPD ranges, and a plottable distribution -- see
        # _compute_calibration_stats for the full breakdown (shared with
        # the analysis rerun path so the two can't drift out of sync).
        stats = _compute_calibration_stats(
            curve, measurement.age_bp, measurement.error_1sigma,
            measurement.delta_r, measurement.delta_r_error,
        )
        calibrated_age_bp = stats['calibrated_age_bp']
        point_estimate = {'mean': stats['posterior_mean'], 'median': stats['posterior_median'], 'std': stats['posterior_std']}
        hpd_68, hpd_95 = stats['hpd_68'], stats['hpd_95']
        hpd_68_ranges, hpd_95_ranges = stats['hpd_68_ranges'], stats['hpd_95_ranges']
        display_distribution = stats['probability_distribution']

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
            # Persisted, not just applied above: a rerun recalibrates from
            # the stored row, so an offset left off it would quietly undo
            # the correction the first run made.
            delta_r=measurement.delta_r,
            delta_r_error=measurement.delta_r_error,
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
                    # Accepted by the schema and carried by dates deployed
                    # from a project collection, so dropping them here lost
                    # data that had been recorded elsewhere.
                    delta_r=measurement.delta_r,
                    delta_r_error=measurement.delta_r_error,
                    reservoir_age=measurement.reservoir_age,
                    depth_cm=measurement.depth_cm,
                    location=measurement.location,
                    notes=measurement.notes,
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

        # _run_bayesian works with plain dicts (measurements keyed by
        # 'id'/'age_bp'/etc, relationships keyed by 'from_sample_id' etc)
        # -- converting the validated Pydantic models to dicts here keeps
        # bayesian.py's existing dict-based interface untouched. This uses
        # the request's own client-side ids throughout (not the persisted
        # DB ids), since build_model() needs to match against the internal
        # `ages` dict, which is also keyed by the client-side measurement id.
        root, group_boundaries, tree_measurements = _tree_from_request(request)
        results, boundary_results = _run_bayesian(
            tree_measurements, root, request.bayesian_config.model_dump(),
        )
        # Report per-context start/end from the boundaries now bracketing each
        # context, preserving the existing response shape. Note that adjacent
        # contexts share a boundary, so one context's end and the next one's
        # start are now the SAME parameter rather than two independent ones.
        context_results = {}
        for group_id, (start_bid, end_bid) in group_boundaries.items():
            start, end = boundary_results.get(start_bid), boundary_results.get(end_bid)
            if not start or not end:
                continue
            context_results[group_id] = {
                'name': next((g.name for g in request.groups if g.id == group_id), None),
                'start': start['posterior'],
                'end': end['posterior'],
            }

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

            # Persist each context's modelled start/end onto its SampleGroup
            # row (client-side group id -> persisted DB id via groups_map).
            for client_group_id, ctx in context_results.items():
                db_group_id = groups_map.get(client_group_id)
                if not db_group_id:
                    continue
                group_row = db.query(SampleGroup).filter(SampleGroup.id == db_group_id).first()
                if group_row:
                    group_row.modeled_start = ctx['start']
                    group_row.modeled_end = ctx['end']

            # Persist the model tree itself, with each boundary's posterior on
            # it, translating the request's client-side ids to the persisted
            # ones so the stored tree lines up with the stored samples.
            _relabel_tree(root, groups_map)
            for element in _iter_tree(root):
                if element['element_type'] == 'boundary':
                    found = boundary_results.get(element['id'])
                    element['posterior'] = found['posterior'] if found else None
            _persist_tree(analysis.id, root, db)
            # Which phase each date ended up in, as decided when the tree was
            # built. Recomputing it from groups_map instead would lose the
            # dates that belong to no context: _tree_from_request gives those
            # a phase of their own ("Unassigned dates"), and that phase's id
            # is not a context id, so a groups_map lookup yields nothing and
            # the date is stored pointing at no element at all -- which the
            # next edit rejects as "not assigned to any model element".
            element_by_measurement = {
                m['id']: m.get('element_id') for m in tree_measurements
            }
            for measurement in request.measurements:
                sample_id = samples_map.get(measurement.id)
                if request.model_tree is not None:
                    # Client-authored tree: its phase ids are used as-is, so
                    # the measurement's own element_id already points at one.
                    element_id = measurement.element_id
                else:
                    # The tree carries client-side context ids, which
                    # _relabel_tree has just rewritten to the ids the contexts
                    # were saved under; a generated phase id isn't in that map
                    # and passes through unchanged.
                    assigned = element_by_measurement.get(measurement.id)
                    element_id = groups_map.get(assigned, assigned)
                if sample_id:
                    db.query(RadiocarbonSample).filter(
                        RadiocarbonSample.id == sample_id
                    ).update({'element_id': element_id}, synchronize_session=False)

            analysis.status = 'completed'
            db.commit()

        # Prepare response with summary stats + the compact plottable
        # distribution (exclude the huge raw posterior_samples array, which
        # can be tens of thousands of floats per sample -- the frontend only
        # ever needs the binned distribution to render the plot)
        def _without_raw_samples(stats: Dict) -> Dict:
            return {k: v for k, v in stats.items() if k != 'posterior_samples'}

        results_summary = {
            measurement_id: _without_raw_samples(result)
            for measurement_id, result in results.items()
        }
        context_summary = {
            group_id: {
                'name': ctx['name'],
                'start': _without_raw_samples(ctx['start']),
                'end': _without_raw_samples(ctx['end']),
            }
            for group_id, ctx in context_results.items()
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
                "contexts": context_summary,
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

    # Only bayesian analyses have a stratigraphic model; a single calibration
    # has nothing to structure.
    if analysis.analysis_type == 'bayesian':
        model_tree, tree_linearized = _get_or_migrate_tree(analysis, db)
    else:
        model_tree, tree_linearized = None, False

    groups_payload = [
        {
            "id": g.id,
            "name": g.name,
            "tpq_age_bp": g.tpq_age_bp,
            "taq_age_bp": g.taq_age_bp,
            # Modelled start/end of this context's own span -- superseded by
            # per-boundary posteriors on the tree, kept for analyses run
            # before those existed.
            "modeled_start": g.modeled_start,
            "modeled_end": g.modeled_end,
        }
        for g in groups
    ]
    if not groups_payload and model_tree:
        groups_payload = [
            {
                "id": element['id'],
                "name": element.get('name') or 'Phase',
                "tpq_age_bp": element.get('tpq_age_bp'),
                "taq_age_bp": element.get('taq_age_bp'),
                "modeled_start": None,
                "modeled_end": None,
            }
            for element in _iter_tree(model_tree)
            if element.get('element_type') == 'phase'
        ]

    # For a Bayesian analysis, also compute each sample's plain/unmodelled
    # calibration (i.e. what calibrating it alone, with no stratigraphic
    # constraints, would give) -- lets the Results page show "unmodelled
    # vs modelled" side by side, the standard way to see what effect the
    # Bayesian model actually had. Computed fresh here rather than
    # persisted: it's a cheap analytic calibration (no MCMC), and doing it
    # at read time means it's always consistent with the sample's current
    # age_bp/error_1sigma/curve_id with nothing to keep in sync. Not
    # meaningful for a calibration-only analysis, where the single stored
    # `probability_distribution` already *is* the unmodelled result.
    #
    # The HPD ranges themselves are returned, not the plottable series they
    # used to be derived from. The page only ever used that series to
    # recompute these ranges, and doing so client-side worked from a
    # distribution downsampled to at most 200 points -- so the figure on
    # screen disagreed in its last digits with the CSV, which computes the
    # same thing over the curve's full grid. Same calibration, two
    # resolutions. Now there is one.
    unmodelled_by_sample_id: Dict[str, list] = {}
    if analysis.analysis_type == 'bayesian':
        for r in results:
            sample = samples_by_id.get(r.sample_id)
            if not sample:
                continue
            try:
                curve = get_curve(sample.curve_id)
                cal_ages, probs = curve.calibrate(
                    sample.age_bp, sample.error_1sigma,
                    sample.delta_r, sample.delta_r_error,
                )
                unmodelled_by_sample_id[r.sample_id] = [
                    {'confidence': level, 'ranges': curve.get_hpd_ranges(cal_ages, probs, level)}
                    for level in REPORTING_CONFIDENCE_LEVELS
                ]
            except Exception:
                # A sample whose curve_id can't be resolved (e.g. an old
                # analysis predating a curve rename) shouldn't break the
                # whole results page -- the modelled result is unaffected
                # either way, this just leaves that one row's unmodelled
                # column empty.
                continue

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
            # The MCMC settings this analysis was configured with -- returned
            # so duplicating or editing it round-trips them instead of
            # silently resetting to the schema defaults.
            "bayesian_config": analysis.bayesian_config or {},
            # The explicit model structure (nested sequences/phases/
            # boundaries/hiatuses), migrated from the old contexts +
            # relationships on first access. Boundary elements carry their
            # own posterior once the analysis has been run.
            "model_tree": model_tree,
            # True when the old relationship graph couldn't be expressed as a
            # tree and was flattened into one order -- strictly MORE
            # constrained than what it came from, so worth checking.
            "model_tree_linearized": tree_linearized,
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
                    # What the date was measured on and against. Returned
                    # because the edit page rebuilds its draft from this
                    # payload and resubmits it: omitting them made
                    # loadFromAnalysis fall back to its defaults, so editing
                    # an analysis rewrote every material as "unknown" and --
                    # worse, since it changes the result -- recalibrated
                    # marine samples against the terrestrial curve.
                    "material_type": s.material_type,
                    "sample_type": s.sample_type,
                    "curve_id": s.curve_id,
                    "delta_r": s.delta_r,
                    "delta_r_error": s.delta_r_error,
                    "reservoir_age": s.reservoir_age,
                    "depth_cm": s.depth_cm,
                    "location": s.location,
                    "notes": s.notes,
                    "group_id": s.group_id,
                    "element_id": s.element_id,
                    "tpq_age_bp": s.tpq_age_bp,
                    "taq_age_bp": s.taq_age_bp,
                }
                for s in analysis.measurements
            ],
            # The contexts a date can belong to. For an analysis whose
            # structure was authored as a tree there are no SampleGroup rows,
            # so these are derived from the tree's phases -- keeping the
            # "which context is this date in" displays working off one
            # representation rather than requiring duplicate storage.
            "groups": groups_payload,
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
                    "probability_distribution": r.probability_distribution,
                    # [{confidence, ranges}] at each level the page offers,
                    # computed over the curve's full grid -- see above.
                    "unmodelled_hpd_ranges": unmodelled_by_sample_id.get(r.sample_id),
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


@router.delete("/analysis/{analysis_id}")
async def delete_analysis(analysis_id: str, db: Session = Depends(get_db)):
    """
    Permanently delete an analysis and everything tied to it (samples,
    results, stratigraphic relationships, groups). Unlike project deletion,
    there's no "unassign and keep" option -- a calibration/Bayesian result
    has no meaning outside the analysis it belongs to.

    Analysis.measurements/results cascade via the ORM relationship, but
    SampleGroup and StratigraphicRelationship reference analysis_id
    directly with no cascade configured (see models.py), and neither has
    an ON DELETE CASCADE at the DB level -- so they're deleted explicitly
    here, in dependency order: results and relationships first (nothing
    references them), then samples (referenced by results and by group_id),
    then groups (referenced by samples), then the analysis itself.
    """
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    db.query(AnalysisResult).filter(AnalysisResult.analysis_id == analysis_id).delete(synchronize_session=False)
    db.query(StratigraphicRelationship).filter(StratigraphicRelationship.analysis_id == analysis_id).delete(synchronize_session=False)
    db.query(ModelElement).filter(ModelElement.analysis_id == analysis_id).delete(synchronize_session=False)
    db.query(RadiocarbonSample).filter(RadiocarbonSample.analysis_id == analysis_id).delete(synchronize_session=False)
    db.query(SampleGroup).filter(SampleGroup.analysis_id == analysis_id).delete(synchronize_session=False)
    db.delete(analysis)
    db.commit()

    return {"success": True, "data": {"deleted_analysis_id": analysis_id}}


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


def _rerun_analysis(analysis: Analysis, db: Session) -> None:
    """
    Recompute an already-saved analysis's results from its own persisted
    inputs -- no new Analysis/RadiocarbonSample/SampleGroup rows, since
    the inputs aren't changing, only the computed results. Used by both
    the single-analysis and project-level rerun endpoints below.

    Raises on failure (curve/MCMC error, no samples, etc); callers are
    responsible for catching it, marking the analysis 'failed', and
    rolling back -- this function itself only commits on success.
    """
    samples = list(analysis.measurements)
    if not samples:
        raise ValueError("Analysis has no measurements to rerun")

    if analysis.analysis_type == 'calibration':
        sample = samples[0]
        curve = get_curve(sample.curve_id)
        stats = _compute_calibration_stats(
            curve, sample.age_bp, sample.error_1sigma,
            sample.delta_r, sample.delta_r_error,
        )

        result = db.query(AnalysisResult).filter(AnalysisResult.analysis_id == analysis.id).first()
        if not result:
            result = AnalysisResult(analysis_id=analysis.id, sample_id=sample.id)
            db.add(result)
        result.calibrated_age_bp = stats['calibrated_age_bp']
        result.posterior_mean = stats['posterior_mean']
        result.posterior_median = stats['posterior_median']
        result.posterior_std = stats['posterior_std']
        result.hpd_68_min, result.hpd_68_max = stats['hpd_68']
        result.hpd_95_min, result.hpd_95_max = stats['hpd_95']
        result.hpd_68_ranges = stats['hpd_68_ranges']
        result.hpd_95_ranges = stats['hpd_95_ranges']
        result.probability_distribution = stats['probability_distribution']

    elif analysis.analysis_type == 'bayesian':
        groups = db.query(SampleGroup).filter(SampleGroup.analysis_id == analysis.id).all()
        relationships = db.query(StratigraphicRelationship).filter(
            StratigraphicRelationship.analysis_id == analysis.id
        ).all()

        # The model structure comes from the analysis's own persisted tree,
        # migrated from the old contexts + relationships on first access.
        root, _linearized = _get_or_migrate_tree(analysis, db)
        if root is None:
            raise ValueError("Analysis has no model structure to run")

        measurement_dicts = [
            {
                'id': s.id,
                'age_bp': s.age_bp,
                'error_1sigma': s.error_1sigma,
                'sample_name': s.sample_name,
                'curve_id': s.curve_id,
                'delta_r': s.delta_r,
                'delta_r_error': s.delta_r_error,
                'element_id': s.element_id or s.group_id,
                'tpq_age_bp': s.tpq_age_bp,
                'taq_age_bp': s.taq_age_bp,
            }
            for s in samples
        ]

        results, boundary_results = _run_bayesian(
            measurement_dicts, root, analysis.bayesian_config or {},
        )

        # Store each boundary's posterior on its element, and mirror it onto
        # the old per-context start/end for anything still reading that.
        for element_row in db.query(ModelElement).filter(
            ModelElement.analysis_id == analysis.id
        ).all():
            found = boundary_results.get(element_row.id)
            if element_row.element_type == 'boundary':
                element_row.posterior = found['posterior'] if found else None

        context_results = {}
        children = root.get('children') or []
        for index, child in enumerate(children):
            if child.get('element_type') != 'phase':
                continue
            before = children[index - 1] if index > 0 else None
            after = children[index + 1] if index + 1 < len(children) else None
            start = boundary_results.get(before['id']) if before and before.get('element_type') == 'boundary' else None
            end = boundary_results.get(after['id']) if after and after.get('element_type') == 'boundary' else None
            if start and end:
                context_results[child['id']] = {
                    'name': child.get('name'),
                    'start': start['posterior'],
                    'end': end['posterior'],
                }

        db.query(AnalysisResult).filter(AnalysisResult.analysis_id == analysis.id).delete(synchronize_session=False)
        for sample_id, result in results.items():
            db.add(AnalysisResult(
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
                probability_distribution=result['probability_distribution'],
            ))

        # Refresh every group's modelled start/end -- explicitly cleared
        # for a group no longer in context_results (shouldn't happen since
        # the member set is unchanged between reruns, but keeps stale data
        # from lingering if it ever did).
        for group_row in groups:
            ctx = context_results.get(group_row.id)
            group_row.modeled_start = ctx['start'] if ctx else None
            group_row.modeled_end = ctx['end'] if ctx else None

    else:
        raise ValueError(f"Unknown analysis_type '{analysis.analysis_type}'")

    analysis.status = 'completed'
    analysis.error_message = None
    db.commit()


@router.post("/analysis/{analysis_id}/rerun")
async def rerun_analysis(analysis_id: str, db: Session = Depends(get_db)):
    """
    Recompute an already-saved analysis's results in place, from its own
    persisted inputs -- same analysis id/URL, old results replaced. Useful
    e.g. after a change to the underlying model (like the Boundary
    parameter change for context Modelled Start/End) to refresh stale
    results without re-entering every input from scratch.
    """
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    try:
        _rerun_analysis(analysis, db)
    except Exception as e:
        db.rollback()
        analysis.status = 'failed'
        analysis.error_message = str(e)
        db.commit()
        raise HTTPException(status_code=400, detail=str(e))

    return {"success": True, "data": {"id": analysis.id, "status": analysis.status}}


@router.put("/analysis/{analysis_id}/model-tree")
async def replace_model_tree(analysis_id: str, payload: Dict, db: Session = Depends(get_db)):
    """
    Replace an analysis's model structure -- the nested tree of Sequences,
    Phases, Boundaries and Hiatuses (see services/model_tree.py).

    Body: {"model_tree": {...}, "sample_elements": {sample_id: element_id}}.
    `sample_elements` reassigns which phase each date belongs to; dates left
    out keep their current assignment.

    Validated by model_tree.validate_tree before anything is written, so a
    structurally impossible model (a hiatus not between boundaries, a date
    attached to a boundary, a duplicated element id) is rejected with a 422
    rather than surfacing much later as a confusing MCMC failure.

    Any existing results are cleared and status returns to 'pending': they
    were computed under the previous structure and would otherwise be shown
    alongside a model that no longer produced them.
    """
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    root = payload.get('model_tree')
    if not root:
        raise HTTPException(status_code=422, detail="model_tree is required")

    assignments = payload.get('sample_elements') or {}
    samples = list(analysis.measurements)
    by_id = {s.id: s for s in samples}
    for sample_id, element_id in assignments.items():
        if sample_id not in by_id:
            raise HTTPException(
                status_code=422,
                detail=f"sample '{sample_id}' does not belong to this analysis",
            )

    prospective = [
        {
            'id': s.id,
            'sample_name': s.sample_name,
            'element_id': assignments.get(s.id, s.element_id or s.group_id),
        }
        for s in samples
    ]
    try:
        validate_tree(root, prospective)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        for sample_id, element_id in assignments.items():
            by_id[sample_id].element_id = element_id
        _persist_tree(analysis_id, root, db)
        db.query(AnalysisResult).filter(
            AnalysisResult.analysis_id == analysis_id
        ).delete(synchronize_session=False)
        analysis.status = 'pending'
        analysis.error_message = None
        db.commit()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

    rows = db.query(ModelElement).filter(ModelElement.analysis_id == analysis_id).all()
    return {
        "success": True,
        "data": {"id": analysis.id, "status": analysis.status, "model_tree": rows_to_tree(rows)},
    }


@router.post("/analysis/{analysis_id}/duplicate")
async def duplicate_analysis(analysis_id: str, db: Session = Depends(get_db)):
    """
    Deep-copy an analysis's INPUTS into a brand-new analysis, so a variant
    can be built from an existing one instead of re-entering every date by
    hand ("the same dates, but grouped differently").

    Copies the samples, contexts (SampleGroups) and stratigraphic
    relationships, remapping old ids to the new rows' ids -- the same
    groups_map/samples_map translation bayesian_analysis does when first
    persisting a request. Results are deliberately NOT copied and status is
    'pending': the copy has inputs but has not been run, so it can be
    edited first (PUT /analysis/{id}/inputs) and then run
    (POST /analysis/{id}/rerun).
    """
    source = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Analysis not found")

    copy = Analysis(
        name=f"{source.name} (copy)",
        analysis_type=source.analysis_type,
        status='pending',
        bayesian_config=source.bayesian_config,
        project_id=source.project_id,
    )
    db.add(copy)
    db.flush()

    groups_map: Dict[str, str] = {}
    for group in db.query(SampleGroup).filter(SampleGroup.analysis_id == source.id).all():
        new_group = SampleGroup(
            analysis_id=copy.id,
            name=group.name,
            tpq_age_bp=group.tpq_age_bp,
            taq_age_bp=group.taq_age_bp,
            # modeled_start/end deliberately left null -- they're results,
            # and this copy hasn't been run.
        )
        db.add(new_group)
        db.flush()
        groups_map[group.id] = new_group.id

    samples_map: Dict[str, str] = {}
    for sample in source.measurements:
        new_sample = RadiocarbonSample(
            analysis_id=copy.id,
            sample_name=sample.sample_name,
            age_bp=sample.age_bp,
            error_1sigma=sample.error_1sigma,
            material_type=sample.material_type,
            sample_type=sample.sample_type,
            curve_id=sample.curve_id,
            delta_r=sample.delta_r,
            delta_r_error=sample.delta_r_error,
            reservoir_age=sample.reservoir_age,
            depth_cm=sample.depth_cm,
            location=sample.location,
            notes=sample.notes,
            group_id=groups_map.get(sample.group_id) if sample.group_id else None,
            element_id=groups_map.get(sample.element_id or sample.group_id)
            if (sample.element_id or sample.group_id) else None,
            tpq_age_bp=sample.tpq_age_bp,
            taq_age_bp=sample.taq_age_bp,
        )
        db.add(new_sample)
        db.flush()
        samples_map[sample.id] = new_sample.id

    for rel in db.query(StratigraphicRelationship).filter(
        StratigraphicRelationship.analysis_id == source.id
    ).all():
        lookup = groups_map if rel.from_type == 'group' else samples_map
        to_lookup = groups_map if rel.to_type == 'group' else samples_map
        from_id = lookup.get(rel.from_sample_id)
        to_id = to_lookup.get(rel.to_sample_id)
        if not from_id or not to_id:
            # A relationship whose endpoint no longer resolves (shouldn't
            # happen, but the columns are polymorphic with no FK to enforce
            # it) is dropped rather than copied dangling.
            continue
        db.add(StratigraphicRelationship(
            analysis_id=copy.id,
            from_sample_id=from_id,
            to_sample_id=to_id,
            from_type=rel.from_type,
            to_type=rel.to_type,
            relationship=rel.relationship,
        ))

    # Copy the model structure too, remapping element ids so the copy's
    # phases are its own rows and its dates point at them. Boundary
    # posteriors are dropped -- they're results, and the copy hasn't run.
    source_tree = rows_to_tree(
        db.query(ModelElement).filter(ModelElement.analysis_id == source.id).all()
    )
    if source_tree:
        element_map = dict(groups_map)
        for element in _iter_tree(source_tree):
            if element['id'] not in element_map:
                element_map[element['id']] = str(uuid.uuid4())
            element['posterior'] = None
        _relabel_tree(source_tree, element_map)
        _persist_tree(copy.id, source_tree, db)
        for old_sample in source.measurements:
            new_id = samples_map.get(old_sample.id)
            source_element = old_sample.element_id or old_sample.group_id
            if new_id and source_element:
                db.query(RadiocarbonSample).filter(
                    RadiocarbonSample.id == new_id
                ).update({'element_id': element_map.get(source_element)},
                         synchronize_session=False)

    db.commit()
    db.refresh(copy)

    return {
        "success": True,
        "data": {
            "id": copy.id,
            "name": copy.name,
            "analysis_type": copy.analysis_type,
            "status": copy.status,
            "project_id": copy.project_id,
            "source_analysis_id": source.id,
        },
    }


@router.put("/analysis/{analysis_id}/inputs")
async def replace_analysis_inputs(
    analysis_id: str,
    request: BayesianRequest,
    db: Session = Depends(get_db),
):
    """
    Replace a saved analysis's inputs (measurements, contexts,
    stratigraphic relationships, MCMC config) wholesale -- the "modify"
    half of duplicate-and-modify. Until this existed an analysis's inputs
    were fixed at creation, so varying a model meant rebuilding it from
    scratch.

    Reuses BayesianRequest for validation, so an edit gets exactly the same
    checks a fresh analysis does: cycle detection, dangling group/
    relationship references, and TPQ/TAQ contradictions are all rejected
    with a 422 before anything is written.

    Any existing results are DELETED and each context's modelled start/end
    cleared, since they were computed from the old inputs and would
    otherwise be silently mismatched against what's now displayed
    alongside them; status goes back to 'pending' until the analysis is
    re-run.
    """
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    try:
        # Drop the old inputs and results. Order matters (see
        # delete_analysis): results and relationships first, then samples,
        # then the groups the samples referenced.
        db.query(AnalysisResult).filter(
            AnalysisResult.analysis_id == analysis_id
        ).delete(synchronize_session=False)
        db.query(StratigraphicRelationship).filter(
            StratigraphicRelationship.analysis_id == analysis_id
        ).delete(synchronize_session=False)
        db.query(RadiocarbonSample).filter(
            RadiocarbonSample.analysis_id == analysis_id
        ).delete(synchronize_session=False)
        db.query(SampleGroup).filter(
            SampleGroup.analysis_id == analysis_id
        ).delete(synchronize_session=False)
        # The model tree describes the structure being replaced, so it goes
        # too -- it is rebuilt from the new contexts/relationships on the
        # next read or run (_get_or_migrate_tree). Leaving it would point
        # phases at deleted contexts and dates at elements that no longer
        # exist.
        db.query(ModelElement).filter(
            ModelElement.analysis_id == analysis_id
        ).delete(synchronize_session=False)
        db.flush()

        groups_map: Dict[str, str] = {}
        for group in request.groups:
            row = SampleGroup(
                analysis_id=analysis.id,
                name=group.name,
                tpq_age_bp=group.tpq_age_bp,
                taq_age_bp=group.taq_age_bp,
            )
            db.add(row)
            db.flush()
            groups_map[group.id] = row.id

        samples_map: Dict[str, str] = {}
        for measurement in request.measurements:
            sample_type = measurement.sample_type or (
                'marine' if measurement.curve_id == 'marine20' else 'terrestrial'
            )
            if request.model_tree is not None:
                # Client-authored tree: its phase ids are stored as-is, so the
                # measurement's own element_id already names the phase it sits
                # in. Falling back to group_id here would silently unassign
                # every date, since a tree-authored analysis has no contexts.
                element_id = measurement.element_id
            else:
                element_id = groups_map.get(measurement.group_id) if measurement.group_id else None
            row = RadiocarbonSample(
                analysis_id=analysis.id,
                sample_name=measurement.sample_name,
                age_bp=measurement.age_bp,
                error_1sigma=measurement.error_1sigma,
                material_type=measurement.material_type,
                sample_type=sample_type,
                curve_id=measurement.curve_id,
                # Replacing the inputs must not quietly drop fields the
                # request carried; these used to be nulled on every edit.
                delta_r=measurement.delta_r,
                delta_r_error=measurement.delta_r_error,
                reservoir_age=measurement.reservoir_age,
                depth_cm=measurement.depth_cm,
                location=measurement.location,
                notes=measurement.notes,
                group_id=groups_map.get(measurement.group_id) if measurement.group_id else None,
                element_id=element_id,
                tpq_age_bp=measurement.tpq_age_bp,
                taq_age_bp=measurement.taq_age_bp,
            )
            db.add(row)
            db.flush()
            samples_map[measurement.id] = row.id

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

        # An explicitly authored tree is the authoritative structure (see
        # BayesianRequest.model_tree), so store it instead of letting the
        # deletion above stand. Without this the next read/run rebuilds a
        # tree from the contexts and an empty relationship set, which does
        # not merely lose the ordering -- it invents a new one, and every
        # date lands in a synthetic "Unassigned dates" phase.
        if request.model_tree is not None:
            root = request.model_tree
            _relabel_tree(root, groups_map)
            _persist_tree(analysis.id, root, db)

        if request.name:
            analysis.name = request.name
        analysis.bayesian_config = request.bayesian_config.model_dump()
        analysis.status = 'pending'
        analysis.error_message = None
        db.commit()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "success": True,
        "data": {
            "id": analysis.id,
            "name": analysis.name,
            "status": analysis.status,
            "measurements_count": len(request.measurements),
        },
    }
