from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas.sample import CalibrateRequest, BayesianRequest, GroupCalibrationRequest
from app.services.calibration import get_curve, CalibrationCurve
from app.services import exact_chain
from app.services import gibbs
from app.services import jobs
from app.services import agreement as agreement_service
from app.services.model_tree import (
    migrate_from_relationships,
    rows_to_tree,
    tree_to_rows,
    validate_tree,
    with_implicit_boundaries,
)
from app.models import (
    Analysis, RadiocarbonSample, AnalysisResult, StratigraphicRelationship,
    SampleGroup, Project, ModelElement,
)
from typing import Callable, List, Dict, Optional, Tuple
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


def _calibration_result_row(
    analysis_id: str,
    sample_id: str,
    stats: Dict,
    existing: Optional[AnalysisResult] = None,
) -> AnalysisResult:
    """
    One plain-calibration result (calibrated_age_bp/posterior stats/HPD/
    distribution -- no MCMC-only fields like agreement_index or
    posterior_samples) as a database row, from an already-computed stats
    dict (see _compute_calibration_stats). Updates `existing` in place if
    given, otherwise returns a new, not-yet-added row.

    Shared by the group-calibration endpoint below and both the
    'calibration' and 'group_calibration' branches of _rerun_analysis, so
    a field added to one can't silently go missing from another the way
    _sample_row's docstring says has already happened here before.
    """
    result = existing or AnalysisResult(analysis_id=analysis_id, sample_id=sample_id)
    result.calibrated_age_bp = stats['calibrated_age_bp']
    result.posterior_mean = stats['posterior_mean']
    result.posterior_median = stats['posterior_median']
    result.posterior_std = stats['posterior_std']
    result.hpd_68_min, result.hpd_68_max = stats['hpd_68']
    result.hpd_95_min, result.hpd_95_max = stats['hpd_95']
    result.hpd_68_ranges = stats['hpd_68_ranges']
    result.hpd_95_ranges = stats['hpd_95_ranges']
    result.probability_distribution = stats['probability_distribution']
    return result


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


def _sample_row(
    analysis_id: str,
    measurement,
    group_id: Optional[str] = None,
    element_id: Optional[str] = None,
) -> RadiocarbonSample:
    """
    One measurement as a database row.

    Shared by the two endpoints that write measurements -- creating a
    Bayesian analysis and replacing a saved one's inputs. They used to build
    this separately and drifted twice: first the reservoir offset, then the
    outlier settings, each added to one and not the other. The second time
    it meant editing an analysis silently switched its outlier model off,
    and the only symptom was an empty column in the export.

    sample_type falls back to whatever the curve implies, because a marine
    curve with a terrestrial label is a contradiction the calibration would
    resolve the wrong way.
    """
    return RadiocarbonSample(
        analysis_id=analysis_id,
        sample_name=measurement.sample_name,
        age_bp=measurement.age_bp,
        error_1sigma=measurement.error_1sigma,
        material_type=measurement.material_type,
        sample_type=measurement.sample_type or (
            'marine' if measurement.curve_id == 'marine20' else 'terrestrial'
        ),
        curve_id=measurement.curve_id,
        delta_r=measurement.delta_r,
        delta_r_error=measurement.delta_r_error,
        outlier_prior=getattr(measurement, 'outlier_prior', 0.0),
        outlier_model=getattr(measurement, 'outlier_model', None),
        reservoir_age=measurement.reservoir_age,
        depth_cm=measurement.depth_cm,
        location=measurement.location,
        notes=measurement.notes,
        group_id=group_id,
        element_id=element_id,
        tpq_age_bp=measurement.tpq_age_bp,
        taq_age_bp=measurement.taq_age_bp,
    )


def _analysis_result_row(analysis_id: str, sample_id: str, result: Dict) -> AnalysisResult:
    """
    One per-date result as a database row.

    Shared by the fresh run and the rerun deliberately. The two used to build
    this independently and drifted: agreement_index and outlier_probability
    were added to one and not the other, so a rerun silently blanked both
    columns in every export.
    """
    return AnalysisResult(
        analysis_id=analysis_id,
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
        agreement_index=result.get('agreement_index'),
        outlier_probability=result.get('outlier_probability'),
    )


def _run_bayesian(
    measurements: List[Dict],
    model_tree: Dict,
    bayesian_config: Dict,
    on_progress: Optional[Callable[[Dict], bool]] = None,
) -> Tuple[Dict, Dict, Dict]:
    """
    Solve the model against an explicit model tree, returning (per-sample
    results, per-boundary results, solver record). Shared between a fresh run
    and a rerun so the two can't drift apart.

    A chain-shaped model -- a Sequence of Phases separated by Boundaries, the
    ordinary archaeological shape -- is integrated EXACTLY by
    services/exact_chain.py rather than sampled. There is no sampler to
    converge and no calibration lobe to miss, so that stays the default
    wherever it applies.

    Everything else goes to services/gibbs.py, OxCal's own MH/Gibbs scheme.
    That replaces pm.sample_smc, which was wrong here for a structural reason
    rather than a tuning one: PyMC tempers pm.Potential along with the
    likelihood, and the model it sampled expressed the entire structure as
    pm.Potential, so the ordering and phase priors were simply absent at the
    start of the annealing path. On the Nenthorn fixture it separated two
    ordered groups by 57 years where the answer is 19 and OxCal reports 13 --
    at r_hat 1.01, every chain agreeing on the same wrong answer. The Gibbs
    sampler targets the true posterior from the first sweep and reproduces the
    exact solver to under a year wherever the exact solver can be asked.

    A model that is not a chain AS AUTHORED goes to the sampler, even when
    supplying the transitions its ordering implies would make it exactly solvable.
    Both remain available -- `exact` supplies them and runs -- but that insertion
    is a real change to the model rather than a reformulation, and it is the wrong
    default: it splits one span term over K dates into two over K1 + K2, and K is
    the exponent in (span)^(-K) that resists a date wandering away from its group.
    Measured against OxCal, rms of the modelled dates: on tests/fixtures/oxcal's
    `ordered` run 1.49 sampled on the model as written against 2.51 exact on the
    patched one, and on the charcoal fixture, where a declared residual date needs
    that resistance most, 4.53 against 10.63.

    The cost, which the results page states rather than hides: the two solvers are
    then no longer given the same tree, so choosing `exact` on such a model
    cross-checks a different model as well as a different method.

    Either choice can be overridden per analysis via bayesian_config.solver --
    see BayesianConfigInput.solver for what each value means and why the
    override exists. `exact` on a model that cannot be integrated raises rather
    than sampling instead: asking for an exact answer and quietly getting a
    sampled one is the silent-fallback problem this all exists to remove.

    The returned solver record is what makes the choice visible; the fallback
    used to be silent, and the shapes that trigger it are not exotic -- a
    sequence of phases with no Boundary anywhere, or two parallel excavation
    areas. Shape:

        {'method': 'exact' | 'gibbs',
         'requested': what the config asked for ('auto' unless overridden),
         'reason': why the model is not exactly solvable AS AUTHORED (None when
                   it is, whichever solver actually ran),
         'implicit_boundaries': how many transitions were supplied to the solver
                   that ran -- always 0 on the sampled path, which never needs
                   them,
         'exact_needs_boundaries': how many WOULD have to be supplied for the
                   exact solver to apply, set only when that is why the sampler
                   ran,
         and for the sampled path: 'sweeps', 'convergence', 'converged', and
         'outlier_scale' -- the shared offset scale each outlier model's dates
         inferred, which only the sampler estimates (see services/gibbs.py)}
    """
    requested = bayesian_config.get('solver') or 'auto'

    # Each solver gets the tree it can actually take: the sampler the model as
    # AUTHORED, the exact solver a copy with the transitions the ordering implies
    # supplied, because it cannot integrate anything else.
    #
    # The two are therefore not always the same tree, which is deliberate and is a
    # reversal -- they used to share the patched one so that the override was a
    # pure method-vs-method cross-check. That property is worth less than the
    # accuracy it cost. Supplying a transition is not a reformulation: it splits
    # one span term over K dates into two over K1 + K2, weakening the exponent that
    # holds a group together, and it is measurably the worse answer (see this
    # function's docstring for the numbers). The results page says when the two
    # solvers would be comparing different models.
    chain, reason = exact_chain.layout_with_reason(model_tree, measurements)
    authored_is_chain = chain is not None
    exact_tree = model_tree
    exact_needs_boundaries = 0
    if chain is None:
        # A Sequence that orders two groups with no Boundary between them is
        # not a chain. Supplying the transition it implies makes it solvable
        # here -- at a price. See model_tree.with_implicit_boundaries.
        candidate, inserted = with_implicit_boundaries(model_tree)
        if inserted:
            patched, patched_reason = exact_chain.layout_with_reason(candidate, measurements)
            if patched is not None:
                chain, exact_tree = patched, candidate
                exact_needs_boundaries = len(inserted)
            else:
                # Report why the model is still not a chain AFTER the
                # transitions were supplied -- the original reason was
                # "groups ordered with no Boundary between them", which
                # inserting them has already dealt with, so repeating it
                # would send the user to fix something that is now fixed.
                reason = patched_reason

    if requested == 'exact' and chain is None:
        raise ValueError(
            'this model cannot be integrated exactly, so the exact solver was '
            f'not used: {reason}. Either fix that, or choose the MH/Gibbs '
            'sampler, which has no such restriction.'
        )

    # `auto` takes the exact answer only where it needs no help. Asking for it
    # explicitly still gets it, transitions and all -- that is what the override
    # is for.
    if chain is not None and (requested == 'exact'
                              or (requested != 'gibbs' and authored_is_chain)):
        solved = exact_chain.solve(
            measurements, exact_tree, chain,
            num_samples=bayesian_config.get('mcmc_samples', 2000),
            uniform_span_prior=bayesian_config.get('uniform_span_prior', True),
        )
        agreement_service.attach(solved['measurements'], measurements)
        return solved['measurements'], solved['boundaries'], {
            'method': 'exact',
            'requested': requested,
            'reason': None,
            'implicit_boundaries': exact_needs_boundaries,
        }

    # Only the sampler takes a progress callback: exact integration does not
    # iterate towards an answer, so it has no fraction to report and nothing
    # to interrupt between. A model solved exactly simply goes from queued to
    # completed, which is honest about how it was computed.
    sampled = gibbs.solve(
        measurements, model_tree,
        num_samples=bayesian_config.get('mcmc_samples', 2000),
        uniform_span_prior=bayesian_config.get('uniform_span_prior', True),
        on_progress=on_progress,
    )
    agreement_service.attach(sampled['measurements'], measurements)
    return sampled['measurements'], sampled['boundaries'], {
        'method': 'gibbs',
        'requested': requested,
        # Why the model is not a chain AS AUTHORED. None when it is one and the
        # sampler ran by choice: reporting a "why it could not be solved exactly"
        # against a model that could would be simply false, and would make the
        # results page warn about a deliberate, valid decision.
        'reason': reason,
        # Nothing was supplied -- the sampler took the model as written.
        'implicit_boundaries': 0,
        # ...but this is how many the exact solver would have needed, which is a
        # different statement from `reason` and has to stay distinguishable from
        # it: here the model COULD have been integrated exactly, and was not,
        # because doing so would have changed it.
        'exact_needs_boundaries': exact_needs_boundaries,
        'sweeps': sampled['sweeps'],
        'convergence': sampled['convergence'],
        'converged': sampled['converged'],
        # What the data said about each outlier model's shared offset scale, in
        # the log10(years) OxCal reports its own U(0,n) posterior in -- so an
        # analyst reproducing a published run can compare the inferred
        # hyperparameter, not just the dates it fed into.
        'outlier_scale': sampled['outlier_scale'],
    }


def _job_progress(handle: jobs.JobHandle):
    """
    Adapt a JobHandle to the sampler's on_progress protocol.

    The sampler's contract is that returning False stops the run (raising
    gibbs.Cancelled), so cancellation is a return value here rather than an
    exception thrown through the middle of a sweep loop.

    Progress is sweeps against the cap, which is an upper bound: most runs
    converge long before it and finish at a fraction. The bar is therefore
    accompanied by the achieved convergence rather than left to imply that a
    run stopping at 30% was cut short.
    """
    def report(info: Dict) -> bool:
        cap = info.get('max_sweeps') or 0
        handle.update(
            progress=(info.get('sweeps', 0) / cap) if cap else None,
            phase=info.get('phase'),
            sweeps=info.get('sweeps'),
            max_sweeps=cap or None,
            convergence=info.get('convergence'),
        )
        return not handle.cancelled
    return report


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
def calibrate_single(measurement: CalibrateRequest, db: Session = Depends(get_db)):
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


@router.post("/analysis/calibrate-group")
def calibrate_group(request: GroupCalibrationRequest, db: Session = Depends(get_db)):
    """
    Calibrate several radiocarbon measurements independently -- each
    exactly as /analysis/calibrate would calibrate it alone -- and save
    them as one analysis, so they land on the Results page's shared-axis
    plot and results table together, in chronological order.

    Where a Bayesian analysis asks "what do these dates imply together,
    given how they relate stratigraphically", this is the plainer
    question: lay several independent dates out on one timeline, with no
    relationship asserted between them. Always saved -- unlike single
    calibration there is no quick-preview mode here, on the same
    reasoning the Bayesian endpoint doesn't have one either: a set of
    dates worth calibrating together is normally worth keeping.
    """
    try:
        project_id = _validate_project_id(request.project_id, db)

        analysis = Analysis(
            name=request.name,
            analysis_type='group_calibration',
            status='completed',
            bayesian_config={},
            project_id=project_id,
        )
        db.add(analysis)
        db.flush()

        # No group_id/element_id passed through -- a group calibration has
        # no phases or model tree for a measurement to belong to.
        for measurement in request.measurements:
            sample = _sample_row(analysis.id, measurement)
            db.add(sample)
            db.flush()

            curve = get_curve(sample.curve_id)
            stats = _compute_calibration_stats(
                curve, sample.age_bp, sample.error_1sigma,
                sample.delta_r, sample.delta_r_error,
            )
            db.add(_calibration_result_row(analysis.id, sample.id, stats))

        db.commit()
        db.refresh(analysis)

        return {
            "success": True,
            "data": {
                "id": analysis.id,
                "name": analysis.name,
                "analysis_type": "group_calibration",
                "status": "completed",
                "project_id": analysis.project_id,
                "measurements_count": len(request.measurements),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/analysis/bayesian")
def bayesian_analysis(request: BayesianRequest, wait: bool = False,
                      db: Session = Depends(get_db)):
    """
    Perform Bayesian chronological analysis.

    The inputs are validated and persisted in THIS request -- so a malformed
    model still answers 422 immediately, as it always did -- and the solve is
    then queued, answering 202 with a job_id to poll at
    GET /api/jobs/{job_id}. See docs/ASYNC_JOBS_PLAN.md.

    Two cases stay synchronous. `wait=true` asks for it (see
    rerun_analysis), and `save=false` has no choice: nothing is persisted, so
    there is no analysis for a later poll to fetch results from and no row to
    hang a job on. A quick test run is answered in full, as before.

    The request body is validated by BayesianRequest (schemas/sample.py):
    every measurement goes through the same checks as /analysis/calibrate
    (age_bp/error_1sigma bounds, known curve_id, non-blank sample_name),
    at least one measurement is required, relationship values are
    restricted to the three the solvers actually implement
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
        # Captured now, as a plain string. The solve may run in another
        # thread against another session, where the `analysis` object above
        # is not usable but its id still is. None when nothing was saved.
        analysis_id = analysis.id if save else None

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
                sample = _sample_row(
                    analysis.id, measurement,
                    group_id=groups_map.get(measurement.group_id) if measurement.group_id else None,
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

        def _solve_and_store(db: Session, handle) -> Tuple[Dict, Dict, Dict]:
            """
            Solve the model and write what it produced.

            Split out of the handler body so the same code serves both
            paths: called directly for a synchronous run, or handed to the
            job queue for the usual one. It closes over the request and
            over groups_map/samples_map -- plain data and dicts of ids, all
            of it safe to read from another thread -- but NOT over the
            `analysis` row, which belongs to the request's session and is
            re-fetched here from whichever session is running.
            """
            analysis = (db.query(Analysis).filter(Analysis.id == analysis_id).first()
                        if analysis_id else None)
            if save and analysis is None:
                raise ValueError(f"Analysis '{analysis_id}' no longer exists")

            # _run_bayesian works with plain dicts (measurements keyed by
            # 'id'/'age_bp'/etc, relationships keyed by 'from_sample_id' etc)
            # -- converting the validated Pydantic models to dicts here keeps
            # the solvers' existing dict-based interface untouched. This uses
            # the request's own client-side ids throughout (not the persisted
            # DB ids), since build_model() needs to match against the internal
            # `ages` dict, which is also keyed by the client-side measurement id.
            root, group_boundaries, tree_measurements = _tree_from_request(request)
            results, boundary_results, solver = _run_bayesian(
                tree_measurements, root, request.bayesian_config.model_dump(),
                on_progress=_job_progress(handle),
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
                    db.add(_analysis_result_row(analysis.id, sample_id, result))

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

                analysis.solver = solver
                analysis.status = 'completed'
                db.commit()
            return results, context_results, solver

        if save and not wait:
            # Queued. The inputs are already persisted and the analysis is
            # readable at its own URL, which is where the client is sent;
            # the results arrive there when the job finishes.
            analysis.status = 'pending'
            db.commit()

            def _work(db: Session, handle) -> Dict:
                worker_analysis = db.query(Analysis).filter(
                    Analysis.id == analysis_id).first()
                if worker_analysis is not None:
                    worker_analysis.status = 'running'
                    db.commit()
                try:
                    _, _, solver = _solve_and_store(db, handle)
                except gibbs.Cancelled:
                    db.rollback()
                    worker_analysis = db.query(Analysis).filter(
                        Analysis.id == analysis_id).first()
                    if worker_analysis is not None:
                        # Nothing was written, so it is back to never having
                        # been run rather than to some earlier answer.
                        worker_analysis.status = 'pending'
                        db.commit()
                    raise jobs.Cancelled()
                except Exception as exc:
                    db.rollback()
                    worker_analysis = db.query(Analysis).filter(
                        Analysis.id == analysis_id).first()
                    if worker_analysis is not None:
                        worker_analysis.status = 'failed'
                        worker_analysis.error_message = str(exc)
                        db.commit()
                    raise
                return {'analysis_id': analysis_id,
                        'solver': solver.get('method')}

            job_id = jobs.submit('analysis_run', analysis_id, _work)
            return JSONResponse(status_code=202, content={
                "success": True,
                "data": {
                    "id": analysis_id,
                    "name": request.name,
                    "analysis_type": "bayesian",
                    "status": "queued",
                    "saved": True,
                    "project_id": project_id,
                    "job_id": job_id,
                    "measurements_count": len(request.measurements),
                },
            })

        results, context_results, solver = _solve_and_store(db, jobs.NullHandle())

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
                "solver": solver,
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
def get_analysis(analysis_id: str, db: Session = Depends(get_db)):
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
    # The HPD ranges themselves are returned, computed over the curve's full
    # grid (see the resolution note below). A plottable series is ALSO kept
    # here -- capped the same way `probability_distribution` already is --
    # for the "prior" shadow the Results page can optionally overlay behind
    # the modelled posterior; the HPD ranges alone can't draw that.
    unmodelled_by_sample_id: Dict[str, dict] = {}
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
                unmodelled_by_sample_id[r.sample_id] = {
                    'hpd_ranges': [
                        {'confidence': level, 'ranges': curve.get_hpd_ranges(cal_ages, probs, level)}
                        for level in REPORTING_CONFIDENCE_LEVELS
                    ],
                    'distribution': curve.get_display_distribution(cal_ages, probs),
                }
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
            # Why a failed run failed. Stored on the row since reruns existed,
            # but never returned until now, so a 'failed' analysis gave the page
            # nothing to show beyond the word "failed" -- and one of the ways to
            # fail is now a deliberate choice (requiring the exact solver on a
            # model that cannot support it), where the reason is the whole point.
            "error_message": analysis.error_message,
            "created_at": analysis.created_at.isoformat(),
            "measurements_count": len(analysis.measurements),
            "project_id": analysis.project_id,
            "project_name": analysis.project.name if analysis.project else None,
            # The MCMC settings this analysis was configured with -- returned
            # so duplicating or editing it round-trips them instead of
            # silently resetting to the schema defaults.
            "bayesian_config": analysis.bayesian_config or {},
            # How these results were actually computed: the exact solver, or
            # the SMC fallback and why the model needed it. NULL for an
            # analysis run before this was recorded, which the Results page
            # renders as "not recorded" rather than claiming either method.
            "solver": analysis.solver,
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
                    # Same reason: the edit page resubmits this payload, so
                    # an outlier setting left out here would be silently
                    # switched off by editing the analysis.
                    "outlier_prior": s.outlier_prior,
                    "outlier_model": s.outlier_model,
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
                    "unmodelled_hpd_ranges": unmodelled_by_sample_id.get(r.sample_id, {}).get('hpd_ranges'),
                    # The same unmodelled calibration as a plottable series,
                    # for the optional "prior" shadow behind the modelled
                    # posterior on the Results page's plots.
                    "unmodelled_distribution": unmodelled_by_sample_id.get(r.sample_id, {}).get('distribution'),
                    # Diagnostics, so the results page can flag a suspect
                    # date in place rather than making someone download the
                    # CSV to find out. Null means not computed -- for
                    # agreement, an analysis that predates it; for the
                    # outlier probability, a date with no outlier model --
                    # which is not the same as a clean result.
                    "agreement_index": r.agreement_index,
                    "outlier_probability": r.outlier_probability,
                }
                for r in results
            ]
        }
    }

@router.get("/analysis")
def list_analyses(
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
def delete_analysis(analysis_id: str, db: Session = Depends(get_db)):
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
def update_analysis(analysis_id: str, updates: Dict, db: Session = Depends(get_db)):
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


def _rerun_analysis(analysis: Analysis, db: Session,
                    on_progress: Optional[Callable[[Dict], bool]] = None) -> None:
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
        existing = db.query(AnalysisResult).filter(AnalysisResult.analysis_id == analysis.id).first()
        result = _calibration_result_row(analysis.id, sample.id, stats, existing)
        if not existing:
            db.add(result)

    elif analysis.analysis_type == 'group_calibration':
        # Unlike 'calibration', every sample is recalibrated -- a group
        # calibration has no single "the" sample, and its member set never
        # changes between a save and a rerun (that goes through duplicate
        # or a fresh calibrate-group instead), so this is always exactly
        # one result per existing sample, matched by id.
        existing_by_sample = {
            r.sample_id: r
            for r in db.query(AnalysisResult).filter(AnalysisResult.analysis_id == analysis.id).all()
        }
        for sample in samples:
            curve = get_curve(sample.curve_id)
            stats = _compute_calibration_stats(
                curve, sample.age_bp, sample.error_1sigma,
                sample.delta_r, sample.delta_r_error,
            )
            existing = existing_by_sample.get(sample.id)
            result = _calibration_result_row(analysis.id, sample.id, stats, existing)
            if not existing:
                db.add(result)

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
                'outlier_prior': s.outlier_prior,
                'outlier_model': s.outlier_model,
            }
            for s in samples
        ]

        results, boundary_results, solver = _run_bayesian(
            measurement_dicts, root, analysis.bayesian_config or {},
            on_progress=on_progress,
        )
        analysis.solver = solver

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
            db.add(_analysis_result_row(analysis.id, sample_id, result))

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


def _run_analysis_job(analysis_id: str):
    """
    The work function for an 'analysis_run' job: solve one saved analysis
    from its own persisted inputs.

    Takes an id rather than an Analysis, because the row has to be loaded in
    the worker's own session -- the request's was closed when its response
    was sent.
    """
    def work(db: Session, handle: jobs.JobHandle) -> Dict:
        analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
        if analysis is None:
            raise ValueError(f"Analysis '{analysis_id}' no longer exists")
        analysis.status = 'running'
        analysis.error_message = None
        db.commit()
        try:
            _rerun_analysis(analysis, db, on_progress=_job_progress(handle))
        except gibbs.Cancelled:
            # A cancelled run wrote nothing -- _rerun_analysis commits only
            # on success -- so the analysis keeps the results it already had
            # and goes back to saying so.
            db.rollback()
            analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
            if analysis is not None:
                analysis.status = 'completed' if analysis.results else 'pending'
                db.commit()
            raise jobs.Cancelled()
        except Exception as exc:
            db.rollback()
            analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
            if analysis is not None:
                analysis.status = 'failed'
                analysis.error_message = str(exc)
                db.commit()
            raise
        return {'analysis_id': analysis_id, 'solver': (analysis.solver or {}).get('method')}
    return work


# Not `status_code=202` on the decorator: that would be the route's default
# for BOTH branches, and the wait=true branch answers 200 with a finished
# result. The queued branch sets 202 on its own response instead.
@router.post("/analysis/{analysis_id}/rerun")
def rerun_analysis(analysis_id: str, wait: bool = False,
                   db: Session = Depends(get_db)):
    """
    Recompute an already-saved analysis's results in place, from its own
    persisted inputs -- same analysis id/URL, old results replaced. Useful
    e.g. after a change to the underlying model (like the Boundary
    parameter change for context Modelled Start/End) to refresh stale
    results without re-entering every input from scratch.

    Queued rather than run here: a Bayesian model takes tens of seconds and
    can take minutes (see docs/ASYNC_JOBS_PLAN.md). Answers 202 with a
    job_id to poll at GET /api/jobs/{job_id}.

    `wait=true` runs it in the request and answers 200 when it is finished,
    which is the old behaviour. Kept permanently, not as a migration aid:
    this app has no client library, so anything scripting it from outside a
    browser has no event loop to poll from.
    """
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    if wait:
        try:
            _rerun_analysis(analysis, db)
        except Exception as e:
            db.rollback()
            analysis.status = 'failed'
            analysis.error_message = str(e)
            db.commit()
            raise HTTPException(status_code=400, detail=str(e))
        return {"success": True, "data": {"id": analysis.id, "status": analysis.status}}

    analysis.status = 'pending'
    analysis.error_message = None
    db.commit()
    job_id = jobs.submit('analysis_run', analysis.id, _run_analysis_job(analysis.id))
    return JSONResponse(status_code=202, content={
        "success": True,
        "data": {"id": analysis.id, "status": "queued", "job_id": job_id},
    })


@router.put("/analysis/{analysis_id}/model-tree")
def replace_model_tree(analysis_id: str, payload: Dict, db: Session = Depends(get_db)):
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
def duplicate_analysis(analysis_id: str, db: Session = Depends(get_db)):
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
        # A stored row carries the same attribute names a request
        # measurement does, so duplicating goes through the one builder too
        # -- this copy had already fallen behind on the outlier settings.
        new_sample = _sample_row(
            copy.id, sample,
            group_id=groups_map.get(sample.group_id) if sample.group_id else None,
            element_id=groups_map.get(sample.element_id or sample.group_id)
            if (sample.element_id or sample.group_id) else None,
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
def replace_analysis_inputs(
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
            if request.model_tree is not None:
                # Client-authored tree: its phase ids are stored as-is, so the
                # measurement's own element_id already names the phase it sits
                # in. Falling back to group_id here would silently unassign
                # every date, since a tree-authored analysis has no contexts.
                element_id = measurement.element_id
            else:
                element_id = groups_map.get(measurement.group_id) if measurement.group_id else None
            row = _sample_row(
                analysis.id, measurement,
                group_id=groups_map.get(measurement.group_id) if measurement.group_id else None,
                element_id=element_id,
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
        # The recorded solver describes the structure just replaced, and
        # editing the structure is exactly what changes which solver applies
        # -- keeping it would label the pending analysis "exact" on the
        # strength of a model that no longer exists. The rerun sets it again.
        analysis.solver = None
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
