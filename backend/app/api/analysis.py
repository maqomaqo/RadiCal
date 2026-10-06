from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload
from app.database import get_db
from app.schemas.sample import CalibrateRequest, BayesianRequest, GroupCalibrationRequest
from app.services.calibration import (
    REPORTING_CONFIDENCE_LEVELS,
    SIGMA_1,
    SIGMA_2,
    CalibrationCurve,
    get_curve,
)
from app.services import cross_check
from app.services import exact_chain
from app.services import gibbs
from app.services import model_tree as model_tree_service
from app.services import wiggle_match
from app.services import jobs
from app.services import agreement as agreement_service
from app.services import intervals
from app.services import kde
from app.services import failures
from app.services import statistics
from app.api.auth import current_user, owned_or_404
from app.api.curves import authorise_curve_ids
from app.services.model_tree import (
    migrate_from_relationships,
    rows_to_tree,
    tree_to_rows,
    validate_ordinates,
    validate_tree,
    with_implicit_boundaries,
)
from app.models import (
    Analysis, RadiocarbonSample, AnalysisResult, StratigraphicRelationship,
    SampleGroup, Project, ModelElement, User,
)
from typing import Callable, List, Dict, Optional, Tuple
import numpy as np
import uuid

router = APIRouter()

# REPORTING_CONFIDENCE_LEVELS comes from services/calibration, the one place
# the exact 1/2/3-sigma coverages are defined. Unmodelled HPD ranges are
# precomputed at all three so the Results page selector needs no refetch and
# never derives them from a downsampled plot series.


def _validate_project_id(project_id: Optional[str], db: Session, user: User) -> Optional[str]:
    """Return a request's project_id unchanged if valid or absent, raising 404
    if it names no real project -- which would otherwise leave the analysis
    silently unassigned.

    "No real project" includes someone else's, and returns the same 404: a
    project id must not be a way to file an analysis into a project you cannot
    see, nor a way to find out that one exists."""
    if not project_id:
        return None
    owned = (
        db.query(Project.id)
        .filter(Project.id == project_id, Project.owner_id == user.id)
        .first()
    )
    if not owned:
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
    Calibrate one measurement and return every stat the calibration path
    needs: point estimates, the HPD envelope, the multi-range HPD breakdown,
    and a plottable distribution. Shared by calibrate_single and the rerun
    path so the two cannot drift apart.
    """
    cal_ages, probs = curve.calibrate(age_bp, error_1sigma, delta_r, delta_r_error)

    # Calibration curves are non-monotonic, so a single measurement's
    # posterior can be genuinely multimodal. The _ranges lists carry each
    # disjoint range with its own probability share; hpd_68/hpd_95 below are
    # just the overall envelope.
    hpd_68_ranges = curve.get_hpd_ranges(cal_ages, probs, SIGMA_1)
    hpd_95_ranges = curve.get_hpd_ranges(cal_ages, probs, SIGMA_2)
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


def _date_difference_block(samples: List, seed: int = 42) -> Dict:
    """
    How far apart every pair of dates in a group calibration is, and how
    confidently they can be ordered -- Analysis.intervals for an analysis with
    no model.

    No solver is needed: a group calibration asserts no relationship, so the
    dates are independent and drawing each from its own posterior IS a draw
    from the joint. Pairs read oldest-first, matching the results page and
    every export, so a positive difference means the first is the older.
    """
    rng = np.random.default_rng(seed)
    draws: Dict[str, np.ndarray] = {}
    dates = []
    for sample in samples:
        curve = get_curve(sample.curve_id)
        cal_ages, probs = curve.calibrate(
            sample.age_bp, sample.error_1sigma, sample.delta_r, sample.delta_r_error)
        drawn = intervals.draws_from_distribution(
            cal_ages, probs, rng, intervals.DEFAULT_DRAWS)
        draws[sample.id] = drawn
        dates.append({'id': sample.id, 'name': sample.sample_name,
                      'median': float(np.median(drawn))})
    dates.sort(key=lambda d: d['median'], reverse=True)
    return intervals.report_dates(dates, draws)


def _summary_block(samples: List) -> Optional[Dict]:
    """
    Analysis.summaries for a group calibration: a kernel density estimate of
    how its dates are spread through time (services/kde.py). Each date's own
    calibration is what is drawn from, the dates being independent with no
    model to move them. None below kde.MIN_DATES, as the differences are for
    one date.
    """
    calibrated = []
    for sample in samples:
        curve = get_curve(sample.curve_id)
        calibrated.append(curve.calibrate(
            sample.age_bp, sample.error_1sigma, sample.delta_r, sample.delta_r_error))
    summary = kde.summarise(calibrated)
    # A list, as a model's one-per-phase KDEs are; this one is of every date
    # in the analysis, so it belongs to no element.
    return {'kde': [{'element_id': None, 'name': None, **summary}]} if summary else None


def _phase_summary_block(tree: Dict, measurements: List[Dict], solved: Dict,
                         wanted: bool) -> Optional[Dict]:
    """
    Analysis.summaries for a model: one kernel density estimate per Phase,
    from the solver's own joint draws and reflected at the phase's Boundaries
    (kde.report_phases). None unless asked for, or when no phase holds two
    dates.
    """
    if not wanted:
        return None
    draws = {**(solved.get('joint_boundaries') or {}), **(solved.get('joint_draws') or {})}
    records = kde.report_phases(tree, measurements, draws,
                                grid_step=(solved.get('grid') or {}).get('step') or 0.0)
    return {'kde': records} if records else None


def _relabel_summaries(block: Optional[Dict], id_map: Dict[str, str]) -> Optional[Dict]:
    """_relabel_intervals for the summaries block: each KDE names its phase."""
    if not block or not id_map:
        return block
    return {**block, 'kde': [
        {**record, 'element_id': id_map.get(record.get('element_id'), record.get('element_id'))}
        for record in block.get('kde') or []
    ]}


def _calibration_result_row(
    analysis_id: str,
    sample_id: str,
    stats: Dict,
    existing: Optional[AnalysisResult] = None,
) -> AnalysisResult:
    """
    One plain-calibration result as a database row, built from a
    _compute_calibration_stats dict -- no MCMC-only fields like
    agreement_index. Updates `existing` in place if given, otherwise returns a
    new, not-yet-added row.

    Shared by the group-calibration endpoint and both calibration branches of
    _rerun_analysis, so a field added to one cannot go missing from another.
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


def _tree_for_client(root: Optional[Dict]) -> Optional[Dict]:
    """
    The tree as the frontend should receive it: every element's posterior
    without its raw `posterior_samples`.

    The samples are kept in the database, where they cost nothing to leave,
    but sent to the client they cost a great deal. On an 800-date model with
    138 boundaries they were 10 MB of a 10.8 MB response -- and the frontend
    keeps the tree in its draft store, which mirrors itself to sessionStorage
    on every change, so those 10 MB were re-serialised on every keystroke on
    the edit page (60 ms a character). Nothing on the client reads them; the
    per-sample results already have theirs stripped for the same reason
    (see the run response below, and results._posterior_summary).

    A copy, not an edit in place: the tree handed in may be about to be
    persisted, and the database keeps the samples.
    """
    if root is None:
        return None
    out = {k: v for k, v in root.items() if k not in ('posterior', 'children')}
    posterior = root.get('posterior')
    if posterior is not None:
        out['posterior'] = {k: v for k, v in posterior.items() if k != 'posterior_samples'}
    elif 'posterior' in root:
        out['posterior'] = None
    if root.get('children') is not None:
        out['children'] = [_tree_for_client(child) for child in root['children']]
    return out


def _relabel_tree(root: Dict, id_map: Dict[str, str],
                  sample_map: Optional[Dict[str, str]] = None) -> None:
    """
    Rewrite element ids through `id_map` in place, turning a request's
    client-side context ids into the ids the contexts were saved under.

    `params` can name a SAMPLE -- a wiggle match records which of its members
    the gaps were measured from -- and those are mapped through `sample_map`,
    which is kept separate from `id_map` deliberately. Element ids and sample
    ids are different namespaces, and running both through one map would let a
    client that happened to reuse an id turn an element into a sample.

    Left unmapped, a saved analysis would name a reference belonging to the
    request rather than to itself, and validate_ordinates would refuse the
    next run of it.
    """
    sample_map = sample_map or {}
    for element in _iter_tree(root):
        if element['id'] in id_map:
            element['id'] = id_map[element['id']]
        params = element.get('params')
        if isinstance(params, dict):
            for key in ('reference_sample_id',):
                if params.get(key) in sample_map:
                    element['params'] = {**params, key: sample_map[params[key]]}


def _relabel_intervals(block: Optional[Dict], id_map: Dict[str, str]) -> Optional[Dict]:
    """
    _relabel_tree's client-id -> persisted-id rewrite, applied to a
    durations-and-differences block.

    Each record names the elements it is about, and its `id` is those element
    ids joined with colons. Mapping every colon-separated part covers both
    without enumerating record shapes: a part that is not a client id passes
    through untouched.
    """
    if not block or not id_map:
        return block

    def rename(value):
        if not isinstance(value, str):
            return value
        return ':'.join(id_map.get(part, part) for part in value.split(':'))

    relabelled = dict(block)
    for key in ('durations', 'differences'):
        relabelled[key] = [
            {**record,
             **{field: rename(record[field])
                for field in ('id', 'element_id', 'from_id', 'to_id')
                if record.get(field)}}
            for record in block.get(key) or []
        ]
    # An age-depth record names its levels, and a level is a DATE or an
    # undated element -- so this one carries sample ids as well as element
    # ids. Left unmapped, the band could not be joined back to the results
    # table it is drawn beside.
    relabelled['age_depth'] = [
        {**record,
         'element_id': rename(record.get('element_id')) if record.get('element_id')
         else record.get('element_id'),
         'levels': [{**level, 'id': rename(level['id'])}
                    for level in record.get('levels') or []]}
        for record in block.get('age_depth') or []
    ]
    activity = block.get('phase_activity')
    if activity:
        relabelled['phase_activity'] = {
            **activity,
            'rows': [{**row, 'id': rename(row['id'])} for row in activity.get('rows') or []],
        }
    return relabelled


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
    The analysis's model tree, migrated from the contexts + pairwise
    relationships representation on first access if it has none.

    Returns (root, linearized). `linearized` flags that the relationship graph
    could not be expressed as a tree and was flattened into one order, which
    is strictly MORE constrained than what it came from -- worth telling the
    user. Migrating lazily on read leaves an untouched analysis alone.
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
    sort_order: int = 0,
) -> RadiocarbonSample:
    """
    One measurement as a database row, shared by the two endpoints that write
    measurements -- creating a Bayesian analysis and replacing a saved one's
    inputs -- so a field cannot be added to one and missed on the other.

    sample_type falls back to what the curve implies, because a marine curve
    with a terrestrial label is a contradiction calibration would resolve the
    wrong way.
    """
    return RadiocarbonSample(
        analysis_id=analysis_id,
        # The caller's position in the submitted list. The client arranges
        # that list deliberately (frontend utils/sortMeasurements), and it is
        # the order the dates are read in within each phase, so it is carried
        # rather than left to however the rows happen to come back.
        sort_order=sort_order,
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
        delta_r_source=getattr(measurement, 'delta_r_source', None),
        latitude=getattr(measurement, 'latitude', None),
        longitude=getattr(measurement, 'longitude', None),
        outlier_prior=getattr(measurement, 'outlier_prior', 0.0),
        outlier_model=getattr(measurement, 'outlier_model', None),
        reservoir_age=measurement.reservoir_age,
        depth_cm=measurement.depth_cm,
        ring_offset=getattr(measurement, 'ring_offset', None),
        ring_offset_error=getattr(measurement, 'ring_offset_error', None),
        location=measurement.location,
        notes=measurement.notes,
        group_id=group_id,
        element_id=element_id,
        tpq_age_bp=measurement.tpq_age_bp,
        taq_age_bp=measurement.taq_age_bp,
    )


def _analysis_result_row(analysis_id: str, sample_id: str, result: Dict) -> AnalysisResult:
    """
    One per-date result as a database row, shared by the fresh run and the
    rerun so a field cannot be added to one and missed on the other.
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


def _model_agreement(solved: Dict, measurements: List[Dict]) -> Optional[float]:
    """
    Amodel, from the joint states the solver kept and, where it sampled one,
    each outlier model's scale in those same states (services/agreement.py).
    """
    draws = solved.get('joint_draws')
    if not draws:
        return None
    return agreement_service.model_agreement(
        measurements, draws, solved.get('joint_scales'))


def _reporting_resolution(bayesian_config: Dict) -> float:
    """
    The calendar step, in years, this analysis's modelled ranges and durations
    are reported at: OxCal's Options Resolution, five years unless the analyst
    set another (see BayesianConfigInput.resolution). A config saved before the
    setting existed has no key and gets the default it was run at.
    """
    return float(bayesian_config.get('resolution') or exact_chain.REPORTING_STEP_YEARS)


def _run_wiggle_match(
    measurements: List[Dict], model_tree: Dict, requested: str,
    resolution: float = exact_chain.REPORTING_STEP_YEARS,
) -> Optional[Tuple[Dict, Dict, Dict, Optional[Dict]]]:
    """
    Solve a model that IS one wiggle-matched series, or None if it is not one.

    A wiggle match has a single free parameter and a closed form
    (services/wiggle_match.py), so neither the chain integrator nor the
    sampler is involved: there is no chain to lay out and nothing to converge.

    A wiggle sequence sitting inside a LARGER model is refused rather than
    solved. Its members are one calendar age with fixed gaps added, not
    separate parameters, so the surrounding model's boundaries and span terms
    would have to be told that -- and reading the series as n free dates in a
    phase, which is the only thing the existing solvers could do with it,
    would report a match that was never made. The data model already carries
    everything the composed case needs; what is missing is the inference, and
    saying so is better than guessing at it.
    """
    wiggles = model_tree_service.elements_of_type(model_tree, 'wiggle_sequence')
    if not wiggles:
        return None

    element = model_tree_service.standalone_wiggle_match(model_tree, measurements)
    if element is None:
        raise ValueError(
            'this model puts a wiggle match inside a larger structure, which '
            'this app does not yet solve. A wiggle-matched series has one '
            'calendar age with the measured gaps added to it, and the '
            'surrounding Boundaries and phases would have to model it that '
            'way rather than as separate dates. Run the wiggle match on its '
            'own for now.'
        )

    if requested == 'gibbs':
        raise ValueError(
            'the MH/Gibbs sampler was asked for, but this model is a wiggle '
            'match: it has a single free parameter and a closed form, so '
            'there is nothing to sample and nothing to converge. Leave the '
            'solver on automatic.'
        )

    plan = model_tree_service.plan_model(model_tree, measurements)
    solved = wiggle_match.solve(measurements, plan.wiggle_groups[0], root=model_tree,
                                resolution=resolution)
    element_results = {
        element['id']: {
            'name': model_tree_service.element_label(element),
            'posterior': solved['reference'],
        }
    }
    return solved['measurements'], element_results, {
        'method': 'wiggle_match',
        'requested': requested,
        'resolution': resolution,
        'reason': None,
        'implicit_boundaries': 0,
        # The check that the match is REAL, not a diagnostic beside it: a
        # D_Sequence asserts the ring counts, and if they are wrong the model
        # cannot widen to absorb it the way a Phase can -- it simply fits
        # everything worse. Reported without a pass/fail threshold, since what
        # OxCal quotes a combination against scales with n and this app does
        # not print a number it has not pinned against a real run.
        'combination_agreement': solved['combination_agreement'],
        'series_span': solved['series_span'],
        'gap_units': solved['gap_units'],
        'members': solved['members'],
        'reference_sample_id': solved['reference_sample_id'],
        'grid': solved['grid'],
    }, None
    # No durations block: the spacing of a wiggle match's members is asserted
    # rather than inferred, so its "duration" is the series span, which is an
    # input and is reported as one above.


def _dispersal_spec(bayesian_config: Dict) -> Optional[Dict]:
    """
    What the stored config asks of services/dispersal.py, or None.

    Read from the stored config rather than the request, so a RERUN fits the
    same front from the same point: an origin that changed between a run and
    its rerun would change the speed without changing anything about the
    dates, which is exactly the kind of silent drift a rerun exists to rule
    out.

    An origin is OPTIONAL here where the wave-of-advance model requires one.
    A trend surface assumes no origin at all -- that is what makes it the right
    first look at a dataset, before a hypothesis has been imposed on it -- so
    asking for dispersal without placing a pin is a legitimate request for the
    trend alone, not an incomplete one. An origin left behind by a config the
    analyst later switched off is still not a request: the flag decides.
    """
    if not bayesian_config.get('estimate_dispersal'):
        return None
    origin = bayesian_config.get('dispersal_origin') or {}
    lat, lon = origin.get('lat'), origin.get('lon')
    placed = None if lat is None or lon is None else (float(lat), float(lon))
    return {'origin': placed}


def _cross_check(measurements: List[Dict], model_tree: Dict, sampled: Dict,
                 bayesian_config: Dict) -> Optional[Dict]:
    """
    services/cross_check.check for a sampled run, or None when the analysis
    turned it off. A failure in the check is reported as the check's own
    outcome and never fails the analysis, whose answer stands without it.
    """
    if bayesian_config.get('cross_check') is False:
        return None
    try:
        return cross_check.check(
            measurements, model_tree, sampled,
            uniform_span_prior=bayesian_config.get('uniform_span_prior', True))
    except Exception as error:                   # noqa: BLE001 - reported, not raised
        return {'status': 'not_checked',
                'reason': f'the direct calculation failed ({type(error).__name__})',
                'shape': None, 'threshold': cross_check.DISAGREEMENT, 'checked': 0,
                'worst': None, 'differing': [], 'seconds': None}


def _run_bayesian(
    measurements: List[Dict],
    model_tree: Dict,
    bayesian_config: Dict,
    on_progress: Optional[Callable[[Dict], bool]] = None,
) -> Tuple[Dict, Dict, Dict, Optional[Dict], Optional[Dict]]:
    """
    Solve the model against an explicit model tree, returning (per-sample
    results, per-boundary results, solver record, durations and differences,
    summaries). Shared between a fresh run and a rerun so the two cannot drift
    apart.

    The fourth is None unless bayesian_config.estimate_durations asked for it,
    and the fifth -- a kernel density estimate per phase -- unless
    bayesian_config.estimate_kde did. They are separate returns rather than
    more per-sample fields because every quantity in them is a function of
    several modelled ages at once (see services/intervals.py and
    services/kde.py).

    A chain-shaped model -- a Sequence of Phases separated by Boundaries, the
    ordinary archaeological shape -- is integrated EXACTLY by
    services/exact_chain.py: there is no sampler to converge and no
    calibration lobe to miss. Everything else goes to services/gibbs.py,
    OxCal's own MH/Gibbs scheme, which reproduces the exact solver to under a
    year wherever the exact solver can be asked.

    A model that is not a chain AS AUTHORED is sampled even when supplying the
    transitions its ordering implies would make it exactly solvable, because
    that insertion changes the model: it splits one span term over K dates
    into two over K1 + K2, and K is the exponent in (span)^(-K) that resists a
    date wandering from its group. Measured against OxCal (rms of the modelled
    dates), the oxcal `ordered` fixture gives 1.49 sampled as written against
    2.51 exact on the patched tree, and the charcoal fixture 4.53 against
    10.63. The cost, which the results page states: choosing `exact` on such a
    model cross-checks a different model as well as a different method.

    bayesian_config.solver overrides the choice per analysis. `exact` on a
    model that cannot be integrated raises rather than quietly sampling.

    The returned solver record makes the choice visible. Shape:

        {'method': 'exact' | 'gibbs',
         'requested': what the config asked for ('auto' unless overridden),
         'resolution': the calendar step in years the ranges were reported at,
         'reason': why the model is not exactly solvable AS AUTHORED (None when
                   it is, whichever solver actually ran),
         'implicit_boundaries': how many transitions were supplied to the solver
                   that ran -- always 0 on the sampled path, which never needs
                   them,
         'exact_needs_boundaries': how many WOULD have to be supplied for the
                   exact solver to apply, set only when that is why the sampler
                   ran,
         and for the sampled path: 'sweeps', 'convergence', 'converged',
         'outlier_scale' -- the shared offset scale each outlier model's dates
         inferred, which only the sampler estimates (see services/gibbs.py) --
         and 'cross_check', the Boundaries against a direct calculation of the
         same model (services/cross_check.py), None if turned off}
    """
    requested = bayesian_config.get('solver') or 'auto'
    resolution = _reporting_resolution(bayesian_config)
    estimate_durations = bool(bayesian_config.get('estimate_durations'))
    estimate_kde = bool(bayesian_config.get('estimate_kde'))
    dispersal_spec = _dispersal_spec(bayesian_config)

    # Refused here as well as at the request schema, because a rerun comes
    # from the database rather than from a request and has to be held to the
    # same rule -- a date that lost its ring offset in an edit would otherwise
    # be matched at a gap of zero.
    validate_ordinates(model_tree, measurements)

    wiggle_result = _run_wiggle_match(measurements, model_tree, requested, resolution)
    if wiggle_result is not None:
        # No phase KDE: a wiggle match's members are one age plus fixed gaps,
        # not a sample of events whose spread means anything.
        return (*wiggle_result, None)

    # Each solver gets the tree it can take: the sampler the model as
    # AUTHORED, the exact solver a copy with the implied transitions supplied,
    # since it cannot integrate anything else. The two are therefore not
    # always the same tree -- see the docstring for why that is preferred to
    # the accuracy it would cost, and the results page says when it happens.
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
                # Why it is still not a chain AFTER the transitions were
                # supplied; the original reason has been dealt with, and
                # repeating it would send the user to fix something already
                # fixed.
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
            estimate_durations=estimate_durations,
            dispersal=dispersal_spec,
            resolution=resolution,
        )
        overall = agreement_service.attach(
            solved['measurements'], measurements,
            solved.get('joint_draws'), solved.get('joint_scales'))
        amodel = _model_agreement(solved, measurements)
        return solved['measurements'], solved['boundaries'], {
            'method': 'exact',
            'requested': requested,
            # The calendar step the ranges were reported at, which the results
            # page re-selects them at -- see _reporting_resolution.
            'resolution': resolution,
            'reason': None,
            'implicit_boundaries': exact_needs_boundaries,
            # The two model-level agreement indices, alongside the per-date
            # ones attach() has just written. See services/agreement.py for
            # what separates them: Aoverall combines the per-date indices and
            # so assumes the dates are independent, Amodel is the same index
            # over the joint posterior, where the model's structure has
            # correlated them.
            'model_agreement': amodel,
            'overall_agreement': overall,
        }, solved.get('intervals'), _phase_summary_block(
            exact_tree, measurements, solved, estimate_kde)

    # Only the sampler takes a progress callback: exact integration does not
    # iterate towards an answer, so it has no fraction to report and nothing
    # to interrupt between. A model solved exactly simply goes from queued to
    # completed, which is honest about how it was computed.
    sampled = gibbs.solve(
        measurements, model_tree,
        num_samples=bayesian_config.get('mcmc_samples', 2000),
        uniform_span_prior=bayesian_config.get('uniform_span_prior', True),
        estimate_durations=estimate_durations,
        dispersal=dispersal_spec,
        on_progress=on_progress,
        # Independent chains in parallel processes, pooled; see
        # gibbs.solve_chains for what the extra chains are for.
        chains=int(bayesian_config.get('mcmc_chains') or gibbs.DEFAULT_CHAINS),
        resolution=resolution,
    )
    overall = agreement_service.attach(
        sampled['measurements'], measurements,
        sampled.get('joint_draws'), sampled.get('joint_scales'))
    amodel = _model_agreement(sampled, measurements)
    return sampled['measurements'], sampled['boundaries'], {
        'method': 'gibbs',
        # The sampled Boundaries against a direct calculation of the same
        # model, where the model's shape allows one (services/cross_check.py):
        # the one check that does not compare the sampler with itself.
        'cross_check': _cross_check(measurements, model_tree, sampled, bayesian_config),
        'requested': requested,
        'resolution': resolution,
        # As on the exact path: Aoverall from the per-date indices, Amodel from
        # the run's joint states (services/agreement.py).
        'model_agreement': amodel,
        'overall_agreement': overall,
        # Why the model is not a chain AS AUTHORED, and None when it is one
        # and the sampler ran by choice -- so the results page does not warn
        # about a deliberate, valid decision.
        'reason': reason,
        # Nothing was supplied -- the sampler took the model as written.
        'implicit_boundaries': 0,
        # ...but this is how many the exact solver would have needed: the
        # model COULD have been integrated exactly and was not, because doing
        # so would have changed it. A different statement from `reason`.
        'exact_needs_boundaries': exact_needs_boundaries,
        'sweeps': sampled['sweeps'],
        'convergence': sampled['convergence'],
        'converged': sampled['converged'],
        # How many independent chains were pooled and how they agreed with
        # one another. `convergence` is the pooled first-half/second-half
        # overlap the run stopped on; `rhat` is the Gelman-Rubin ratio it
        # also had to bring under `rhat_target`; the between-chain overlap
        # is information only. All None on a single chain, which has
        # nothing to disagree with.
        'chains': sampled['chains'],
        'sweeps_per_chain': sampled['sweeps_per_chain'],
        'within_chain_convergence': sampled['within_chain_convergence'],
        'per_chain_convergence': sampled['per_chain_convergence'],
        'between_chain_convergence': sampled['between_chain_convergence'],
        'rhat': sampled['rhat'],
        'rhat_target': sampled['rhat_target'],
        # What each deposition sequence inferred about its own rigidity, and
        # whether that posterior ran up against the prior's bound. An
        # age-depth answer is only readable next to the rigidity it was
        # produced under: a high-k model interpolates confidently straight
        # through a plateau and reports a precision the dates do not support.
        # Empty on a model with no deposition sequence in it.
        'deposition': sampled.get('deposition') or [],
        # Each outlier model's shared offset scale, in the log10(years) OxCal
        # reports its own U(0,n) posterior in, so a published run's inferred
        # hyperparameter can be compared directly.
        'outlier_scale': sampled['outlier_scale'],
    }, sampled.get('intervals'), _phase_summary_block(
        model_tree, measurements, sampled, estimate_kde)


def _job_progress(handle: jobs.JobHandle):
    """
    Adapt a JobHandle to the sampler's on_progress protocol.

    Returning False stops the run (raising gibbs.Cancelled), so cancellation
    is a return value rather than an exception thrown mid-sweep. Progress is
    sweeps against the cap, an upper bound most runs converge well short of,
    which is why the UI shows convergence beside the bar.
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
    Build a model tree for an /analysis/bayesian request that describes
    stratigraphy as contexts plus above/below relationships.

    Returns (root, group_boundaries, tree_measurements), where
    group_boundaries maps each context to the pair of boundaries bracketing
    it so results can still be reported per context.
    """
    measurements = [m.model_dump() for m in request.measurements]

    if request.model_tree is not None:
        # The client authored the structure directly, so there are no
        # contexts to bracket: results are reported per boundary.
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
def calibrate_single(measurement: CalibrateRequest, db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    """
    Calibrate a single radiocarbon measurement. The body is validated by
    CalibrateRequest (schemas/sample.py).

    Beyond the measurement it accepts project_id, which files the saved
    analysis under a project (omitting it leaves the analysis unassigned but
    still listed), and save, which when False writes nothing to the database
    for a quick test run and returns a null "id".
    """
    try:
        save = measurement.save
        project_id = _validate_project_id(measurement.project_id, db, user) if save else None

        # The schema checked the id names a real curve; this checks it is one
        # of THIS user's. See api/curves.authorise_curve_ids.
        authorise_curve_ids([measurement.curve_id], user.id)

        # Raises ValueError if the curve is unavailable or empty.
        curve_id = measurement.curve_id
        curve = get_curve(curve_id)

        # Point estimates, HPD ranges, and a plottable distribution.
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
            # Quick test run: nothing touches the database.
            return {"success": True, "data": response_data}

        # Create analysis record in database
        analysis = Analysis(
            name='Single Calibration',
            analysis_type='calibration',
            status='completed',
            bayesian_config={},
            project_id=project_id,
            owner_id=user.id,
        )
        db.add(analysis)
        db.commit()
        db.refresh(analysis)

        # sample_type is None by default in the schema so this fallback --
        # infer "marine" from a marine curve when the caller does not say --
        # still applies.
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
            # the stored row, so an offset left off it would undo the
            # correction the first run made.
            delta_r=measurement.delta_r,
            delta_r_error=measurement.delta_r_error,
            delta_r_source=measurement.delta_r_source,
            latitude=measurement.latitude,
            longitude=measurement.longitude,
            reservoir_age=measurement.reservoir_age,
            depth_cm=measurement.depth_cm,
            location=measurement.location,
            notes=measurement.notes
        )
        db.add(sample)
        db.commit()
        db.refresh(sample)

        # Persisted so GET /api/analysis/{id}, which the Results page uses,
        # comes back with a populated `results` list.
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
        # A refusal this route raised on purpose -- an unavailable curve, a
        # project that is not this user's -- already carries its own status and
        # its own wording. Letting it fall into the handler below would flatten
        # every one of them to a 400 reading "422: ...".
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/analysis/calibrate-group")
def calibrate_group(request: GroupCalibrationRequest, db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    """
    Calibrate several measurements independently -- each exactly as
    /analysis/calibrate would alone -- and save them as one analysis so they
    share the Results page's plot and table in chronological order.

    Where a Bayesian analysis asks what the dates imply together given their
    stratigraphy, this lays independent dates out on one timeline with no
    relationship asserted. Always saved: a set of dates worth calibrating
    together is normally worth keeping.
    """
    try:
        project_id = _validate_project_id(request.project_id, db, user)
        authorise_curve_ids([m.curve_id for m in request.measurements], user.id)

        analysis = Analysis(
            name=request.name,
            analysis_type='group_calibration',
            status='completed',
            # No solver to configure, but the options it takes are recorded
            # so a rerun and an edit round-trip them.
            bayesian_config={'estimate_durations': request.estimate_durations,
                             'estimate_kde': request.estimate_kde},
            project_id=project_id,
            owner_id=user.id,
        )
        db.add(analysis)
        db.flush()

        # No group_id/element_id: a group calibration has no phases.
        saved_samples = []
        for position, measurement in enumerate(request.measurements):
            sample = _sample_row(analysis.id, measurement, sort_order=position)
            db.add(sample)
            db.flush()
            saved_samples.append(sample)

            curve = get_curve(sample.curve_id)
            stats = _compute_calibration_stats(
                curve, sample.age_bp, sample.error_1sigma,
                sample.delta_r, sample.delta_r_error,
            )
            db.add(_calibration_result_row(analysis.id, sample.id, stats))

        # Pairwise differences, when asked for. One date has nothing to differ
        # from, so the option is inert rather than an error.
        if request.estimate_durations and len(saved_samples) > 1:
            analysis.intervals = _date_difference_block(saved_samples)
        if request.estimate_kde:
            analysis.summaries = _summary_block(saved_samples)

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
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/analysis/bayesian")
def bayesian_analysis(request: BayesianRequest, wait: bool = False,
                      db: Session = Depends(get_db),
                      user: User = Depends(current_user)):
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

    The request body is validated by BayesianRequest (schemas/sample.py).

    Accepts the same project_id / save options as /analysis/calibrate. With
    save=False the run still happens in full -- there is no way to preview a
    Bayesian result without running it -- and only the database writes are
    skipped.
    """
    import traceback
    try:
        save = request.save
        project_id = _validate_project_id(request.project_id, db, user) if save else None
        authorise_curve_ids([m.curve_id for m in request.measurements], user.id)

        # Create analysis record
        analysis = Analysis(
            name=request.name,
            analysis_type='bayesian',
            status='running',
            bayesian_config=request.bayesian_config.model_dump(),
            project_id=project_id,
            owner_id=user.id,
        )
        if save:
            db.add(analysis)
            db.commit()
            db.refresh(analysis)
        # A plain string, because the solve may run in another thread against
        # another session where the `analysis` object above is unusable.
        analysis_id = analysis.id if save else None

        # Groups first, so samples can reference their persisted group id via
        # groups_map (client id -> persisted id).
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
        for position, measurement in enumerate(request.measurements):
            if save:
                sample = _sample_row(
                    analysis.id, measurement,
                    group_id=groups_map.get(measurement.group_id) if measurement.group_id else None,
                    sort_order=position,
                )
                db.add(sample)
                db.flush()
                samples_map[measurement.id] = sample.id
        if save:
            db.commit()

        # Persisted so a saved analysis can show which constraints it used.
        # from_sample_id/to_sample_id are translated from the request's
        # client-side ids to the persisted sample/group ids.
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

        def _solve_and_store(db: Session, handle
                             ) -> Tuple[Dict, Dict, Dict, Optional[Dict], Optional[Dict]]:
            """
            Solve the model and write what it produced. Serves both paths:
            called directly for a synchronous run, or handed to the job queue.

            Closes over the request and groups_map/samples_map -- plain data,
            safe to read from another thread -- but NOT over the `analysis`
            row, which is re-fetched from whichever session is running.
            """
            analysis = (db.query(Analysis).filter(Analysis.id == analysis_id).first()
                        if analysis_id else None)
            if save and analysis is None:
                raise ValueError(f"Analysis '{analysis_id}' no longer exists")

            # The solvers take plain dicts, keyed by the request's own
            # client-side ids rather than the persisted ones, because
            # build_model() matches against an `ages` dict keyed the same way.
            root, group_boundaries, tree_measurements = _tree_from_request(request)
            results, boundary_results, solver, interval_block, summary_block = _run_bayesian(
                tree_measurements, root, request.bayesian_config.model_dump(),
                on_progress=_job_progress(handle),
            )
            # Per-context start/end read off the boundaries bracketing each
            # context. Adjacent contexts share a boundary, so one context's end
            # and the next one's start are the SAME parameter.
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

                # Each context's modelled start/end onto its SampleGroup row.
                for client_group_id, ctx in context_results.items():
                    db_group_id = groups_map.get(client_group_id)
                    if not db_group_id:
                        continue
                    group_row = db.query(SampleGroup).filter(SampleGroup.id == db_group_id).first()
                    if group_row:
                        group_row.modeled_start = ctx['start']
                        group_row.modeled_end = ctx['end']

                # The tree itself, with each boundary's posterior on it and
                # ids translated so it lines up with the stored samples.
                _relabel_tree(root, groups_map, samples_map)
                for element in _iter_tree(root):
                    # Any element the solver reported a posterior for, not
                    # boundaries alone: a wiggle match's reference age lives
                    # on its own element, and an undated level's age on its.
                    found = boundary_results.get(element['id'])
                    if found or element['element_type'] == 'boundary':
                        element['posterior'] = found['posterior'] if found else None
                _persist_tree(analysis.id, root, db)
                # Which phase each date ended up in, as decided when the tree
                # was built. Recomputing from groups_map would lose the dates
                # in no context: _tree_from_request gives those an
                # "Unassigned dates" phase whose id is not a context id, so
                # the lookup would yield nothing and the date would be stored
                # pointing at no element at all.
                element_by_measurement = {
                    m['id']: m.get('element_id') for m in tree_measurements
                }
                for measurement in request.measurements:
                    sample_id = samples_map.get(measurement.id)
                    if request.model_tree is not None:
                        # A client-authored tree keeps its phase ids, so the
                        # measurement's element_id already points at one.
                        element_id = measurement.element_id
                    else:
                        # _relabel_tree has just rewritten the tree's context
                        # ids to the persisted ones; a generated phase id is
                        # not in that map and passes through unchanged.
                        assigned = element_by_measurement.get(measurement.id)
                        element_id = groups_map.get(assigned, assigned)
                    if sample_id:
                        db.query(RadiocarbonSample).filter(
                            RadiocarbonSample.id == sample_id
                        ).update({'element_id': element_id}, synchronize_session=False)

                analysis.solver = solver
                # Relabelled alongside the tree: these records name phases by
                # element id, so leaving the client-side ids would file every
                # duration against an element that does not exist.
                # Sample ids as well as context ids: an age-depth record names
                # the dated levels it is about, and those are the analysis's
                # own sample rows.
                analysis.intervals = _relabel_intervals(
                    interval_block, {**groups_map, **samples_map})
                analysis.summaries = _relabel_summaries(summary_block, groups_map)
                analysis.status = 'completed'
                db.commit()
            return results, context_results, solver, interval_block, summary_block

        if save and not wait:
            # Queued. The inputs are persisted and the analysis is readable at
            # its own URL, where the results arrive when the job finishes.
            analysis.status = 'pending'
            db.commit()

            def _work(db: Session, handle) -> Dict:
                worker_analysis = db.query(Analysis).filter(
                    Analysis.id == analysis_id).first()
                if worker_analysis is not None:
                    worker_analysis.status = 'running'
                    db.commit()
                try:
                    _, _, solver, _intervals, _summaries = _solve_and_store(db, handle)
                except gibbs.Cancelled:
                    db.rollback()
                    worker_analysis = db.query(Analysis).filter(
                        Analysis.id == analysis_id).first()
                    if worker_analysis is not None:
                        # Nothing was written, so it is back to never having
                        # been run rather than to an earlier answer.
                        worker_analysis.status = 'pending'
                        db.commit()
                    raise jobs.Cancelled()
                except Exception as exc:
                    db.rollback()
                    worker_analysis = db.query(Analysis).filter(
                        Analysis.id == analysis_id).first()
                    if worker_analysis is not None:
                        worker_analysis.status = 'failed'
                        worker_analysis.error_message = failures.classify(exc).message
                        db.commit()
                    raise
                return {'analysis_id': analysis_id,
                        'solver': solver.get('method')}

            job_id = jobs.submit('analysis_run', analysis_id, _work,
                                 owner_id=user.id)
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

        results, context_results, solver, interval_block, summary_block = _solve_and_store(
            db, jobs.NullHandle())

        # Summary stats plus the binned distribution. The raw
        # posterior_samples array runs to tens of thousands of floats per
        # sample and the frontend never needs it.
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
                # Null unless the run was asked for them; see
                # Analysis.intervals.
                "intervals": interval_block,
                # Likewise; see Analysis.summaries.
                "summaries": summary_block,
                "measurements_count": len(request.measurements)
            }
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        import traceback
        print(f"Bayesian analysis error: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/analysis/{analysis_id}")
def get_analysis(analysis_id: str, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    """Retrieve analysis results"""
    analysis = owned_or_404(
        db.query(Analysis).filter(Analysis.id == analysis_id).first(),
        user, 'That analysis')

    results = db.query(AnalysisResult).filter(AnalysisResult.analysis_id == analysis_id).all()
    samples_by_id = {s.id: s for s in analysis.measurements}

    relationships = db.query(StratigraphicRelationship).filter(
        StratigraphicRelationship.analysis_id == analysis_id
    ).all()
    groups = db.query(SampleGroup).filter(SampleGroup.analysis_id == analysis_id).all()

    # Only a bayesian analysis has a stratigraphic model to report.
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
            # Superseded by per-boundary posteriors on the tree; kept for
            # analyses run before those existed.
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

    # Each sample's unmodelled calibration -- what calibrating it alone would
    # give -- so the Results page can show unmodelled against modelled.
    # Computed fresh rather than persisted: it is a cheap analytic
    # calibration, and doing it at read time keeps it consistent with the
    # sample's current age_bp/error_1sigma/curve_id. Meaningless for a
    # calibration-only analysis, where the stored distribution already IS the
    # unmodelled result.
    #
    # HPD ranges are computed over the curve's full grid. A plottable series
    # is kept alongside them for the optional "prior" shadow behind the
    # modelled posterior, which the ranges alone cannot draw.
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
                # A sample whose curve_id no longer resolves should not break
                # the results page; the modelled result is unaffected, and
                # only that row's unmodelled column is left empty.
                continue

    return {
        "success": True,
        "data": {
            "id": analysis.id,
            "name": analysis.name,
            "analysis_type": analysis.analysis_type,
            "status": analysis.status,
            # Why a failed run failed. One way to fail is a deliberate choice
            # -- requiring the exact solver on a model that cannot support it
            # -- where the reason is the whole point.
            "error_message": analysis.error_message,
            "created_at": analysis.created_at.isoformat(),
            "measurements_count": len(analysis.measurements),
            "project_id": analysis.project_id,
            "project_name": analysis.project.name if analysis.project else None,
            # Returned so duplicating or editing the analysis round-trips its
            # settings instead of resetting to the schema defaults.
            "bayesian_config": analysis.bayesian_config or {},
            # How these results were computed: the exact solver, or the
            # MH/Gibbs sampler and why the model needed it. NULL for an
            # analysis run before this was recorded, which the Results page
            # renders as "not recorded" rather than claiming either method.
            "solver": analysis.solver,
            # Durations and separations, present only when the run was asked
            # for them. Null otherwise, which the Results page answers by
            # offering to re-run rather than showing an empty table.
            "intervals": analysis.intervals,
            # Summaries of the dates as a set -- a kernel density estimate --
            # present only when the run was asked for one.
            "summaries": analysis.summaries,
            # Whether each Phase's dates -- or a group calibration's -- could
            # be one event (services/statistics.py). Computed on read from the
            # stored ages, so an analysis saved before it existed has it too.
            "same_event": statistics.same_event_for_samples(
                analysis.analysis_type, model_tree, analysis.measurements),
            # The model structure, migrated from contexts + relationships on
            # first access. Boundary elements carry their own posterior once
            # the analysis has been run, as do a wiggle match and an undated
            # level; `params` carries the settings belonging to one kind of
            # element (see model_tree.ELEMENT_PARAMS).
            "model_tree": _tree_for_client(model_tree),
            # True when the relationship graph could not be expressed as a
            # tree and was flattened into one order -- strictly MORE
            # constrained than what it came from, so worth checking.
            "model_tree_linearized": tree_linearized,
            # Per-sample data and the stratigraphy this analysis used, so the
            # Results page can render a real results table and a read-only
            # Harris matrix of the constraints that went into the model.
            "measurements": [
                {
                    "id": s.id,
                    "sample_name": s.sample_name,
                    "age_bp": s.age_bp,
                    "error_1sigma": s.error_1sigma,
                    # What the date was measured on and against. The edit page
                    # rebuilds its draft from this payload and resubmits it,
                    # so anything omitted here falls back to a default and is
                    # silently rewritten by an edit.
                    "material_type": s.material_type,
                    "sample_type": s.sample_type,
                    "curve_id": s.curve_id,
                    "delta_r": s.delta_r,
                    "delta_r_error": s.delta_r_error,
                    # Same reason again: a derived offset that lost its
                    # derivation is an offset nobody can account for.
                    "delta_r_source": s.delta_r_source,
                    # Same reason: left out here, a date would be unplaced by
                    # the next edit of the analysis it belongs to.
                    "latitude": s.latitude,
                    "longitude": s.longitude,
                    # Same reason: omitted here, an outlier setting would be
                    # switched off by editing the analysis.
                    "outlier_prior": s.outlier_prior,
                    "outlier_model": s.outlier_model,
                    "reservoir_age": s.reservoir_age,
                    "depth_cm": s.depth_cm,
                    # Same reason again: a wiggle match IS its ring offsets,
                    # so leaving them out here would have an edit resubmit the
                    # series with every gap blank.
                    "ring_offset": s.ring_offset,
                    "ring_offset_error": s.ring_offset_error,
                    "location": s.location,
                    "notes": s.notes,
                    "group_id": s.group_id,
                    "element_id": s.element_id,
                    "tpq_age_bp": s.tpq_age_bp,
                    "taq_age_bp": s.taq_age_bp,
                }
                for s in analysis.measurements
            ],
            # The contexts a date can belong to. An analysis authored as a
            # tree has no SampleGroup rows, so these come from the tree's
            # phases rather than being stored twice.
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
                    # [{confidence, ranges}] at each level the page offers.
                    "unmodelled_hpd_ranges": unmodelled_by_sample_id.get(r.sample_id, {}).get('hpd_ranges'),
                    # The same calibration as a plottable series, for the
                    # optional "prior" shadow behind the modelled posterior.
                    "unmodelled_distribution": unmodelled_by_sample_id.get(r.sample_id, {}).get('distribution'),
                    # Diagnostics, so the results page can flag a suspect date
                    # in place. Null means not computed -- for the outlier
                    # probability, a date with no outlier model -- which is
                    # not the same as a clean result.
                    "agreement_index": r.agreement_index,
                    "outlier_probability": r.outlier_probability,
                }
                for r in results
            ]
        }
    }

def _hpd_segments(ranges) -> List[List[float]]:
    """
    A stored HPD region as bare [min, max] pairs, oldest range first.

    Kept disjoint rather than collapsed to one min/max: a calibrated HPD is
    genuinely multi-modal wherever the curve plateaus, and a single span would
    claim the probability sitting in the gap between two peaks.
    """
    if not ranges:
        return []
    out = [
        [float(r["min"]), float(r["max"])]
        for r in ranges
        if r.get("min") is not None and r.get("max") is not None
    ]
    out.sort(key=lambda seg: -seg[1])
    return out


def _analysis_intervals(results) -> List[Dict]:
    """
    Per-sample HPD geometry for a list row: two sets of segments and the
    point estimate, and none of the posterior arrays the detail endpoint
    carries. No sample name, since bars at list size carry no labels.

    Results with no stored ranges are omitted rather than sent as nulls: an
    empty list is already what "nothing to draw" means downstream.
    """
    out = []
    for r in results:
        h95 = _hpd_segments(r.hpd_95_ranges)
        if not h95:
            continue
        out.append({
            "h68": _hpd_segments(r.hpd_68_ranges),
            "h95": h95,
            "median": r.posterior_median,
        })
    return out


def _analysis_span(results) -> Optional[List[float]]:
    """
    [min, max] cal BP across every sample's 95% HPD, or None when nothing has
    been solved yet. The envelope of the intervals above rather than an
    interval itself, so several analyses can share one calendar axis.
    """
    edges = [v for r in results for seg in _hpd_segments(r.hpd_95_ranges) for v in seg]
    return [min(edges), max(edges)] if edges else None


@router.get("/analysis")
def list_analyses(
    page: int = 1,
    per_page: int = 10,
    project_id: Optional[str] = None,
    unassigned: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """
    List analyses, most recent first, across all projects by default. Pass
    project_id to scope to one, or unassigned=true for those filed under none.
    """
    # The owner filter goes on the base query, before any of the optional
    # narrowing below, so no combination of parameters can widen past it.
    query = db.query(Analysis).filter(Analysis.owner_id == user.id)
    if unassigned:
        query = query.filter(Analysis.project_id.is_(None))
    elif project_id:
        query = query.filter(Analysis.project_id == project_id)

    total = query.count()
    # Eager-loaded: the serializer reads every result's HPD ranges to build
    # each analysis's calendar span, which would be one SELECT per analysis.
    # Only the columns it reads, though: a result row also carries the
    # posterior's full density curve and, for a modelled date, its raw MCMC
    # draws -- tens of thousands of floats each, parsed from JSON for every
    # date on the page and then thrown away. The project name is eager for
    # the same one-SELECT-per-row reason.
    analyses = query.order_by(Analysis.created_at.desc()).options(
        selectinload(Analysis.results).load_only(
            AnalysisResult.analysis_id,
            AnalysisResult.posterior_median,
            AnalysisResult.hpd_68_ranges,
            AnalysisResult.hpd_95_ranges,
        ),
        selectinload(Analysis.project).load_only(Project.name),
    ).offset(
        (page - 1) * per_page
    ).limit(per_page).all()

    # Counted, not loaded: len(a.measurements) fetched every date of every
    # analysis on the page, one query per analysis, to report a number.
    page_ids = [a.id for a in analyses]
    measurement_counts = dict(
        db.query(RadiocarbonSample.analysis_id, func.count(RadiocarbonSample.id))
        .filter(RadiocarbonSample.analysis_id.in_(page_ids))
        .group_by(RadiocarbonSample.analysis_id)
        .all()
    ) if page_ids else {}

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
                    "measurements_count": measurement_counts.get(a.id, 0),
                    "project_id": a.project_id,
                    "project_name": a.project.name if a.project else None,
                    # The calendar answer, so a list shows what each analysis
                    # FOUND rather than only that it finished. Both come from
                    # the same result rows, so the span cannot disagree with
                    # the intervals drawn inside it.
                    "calendar_span": _analysis_span(a.results),
                    "intervals": _analysis_intervals(a.results),
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
def delete_analysis(analysis_id: str, db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    """
    Permanently delete an analysis and everything tied to it. Unlike project
    deletion there is no "unassign and keep": a result has no meaning outside
    its analysis.

    SampleGroup, StratigraphicRelationship and ModelElement have no cascade at
    either the ORM or the DB level, so everything is deleted explicitly in
    dependency order: results and relationships, then samples, then groups,
    then the analysis.
    """
    analysis = owned_or_404(
        db.query(Analysis).filter(Analysis.id == analysis_id).first(),
        user, 'That analysis')

    db.query(AnalysisResult).filter(AnalysisResult.analysis_id == analysis_id).delete(synchronize_session=False)
    db.query(StratigraphicRelationship).filter(StratigraphicRelationship.analysis_id == analysis_id).delete(synchronize_session=False)
    db.query(ModelElement).filter(ModelElement.analysis_id == analysis_id).delete(synchronize_session=False)
    db.query(RadiocarbonSample).filter(RadiocarbonSample.analysis_id == analysis_id).delete(synchronize_session=False)
    db.query(SampleGroup).filter(SampleGroup.analysis_id == analysis_id).delete(synchronize_session=False)
    db.delete(analysis)
    db.commit()

    return {"success": True, "data": {"deleted_analysis_id": analysis_id}}


@router.patch("/analysis/{analysis_id}")
def update_analysis(analysis_id: str, updates: Dict, db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    """
    Update an analysis's project assignment and/or name. Pass
    project_id: null to unassign it.
    """
    analysis = owned_or_404(
        db.query(Analysis).filter(Analysis.id == analysis_id).first(),
        user, 'That analysis')

    if 'project_id' in updates:
        analysis.project_id = _validate_project_id(updates['project_id'], db, user)
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
    Recompute a saved analysis's results from its own persisted inputs. No
    new input rows are written, only the results. Used by both the
    single-analysis and project-level rerun endpoints.

    Raises on failure and commits only on success, so callers are responsible
    for catching, marking the analysis 'failed', and rolling back.
    """
    samples = list(analysis.measurements)
    if not samples:
        raise ValueError("Analysis has no measurements to rerun")

    if analysis.analysis_type in ('calibration', 'group_calibration'):
        # Every sample, for both types: the work is identical -- each date
        # calibrated against its own curve with no model relating them -- so
        # the difference is only what the results page calls it. Looping
        # rather than reading samples[0] also repairs an analysis stored as
        # 'calibration' with several dates.
        #
        # Results are matched to samples by id, so a rerun updates each date's
        # row rather than accumulating duplicates. The member set never
        # changes between a save and a rerun.
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
        # Recomputed rather than left as it was: a rerun exists to pick up an
        # edited date, and a difference is not still true once one has moved.
        analysis.intervals = (
            _date_difference_block(samples)
            if (analysis.bayesian_config or {}).get('estimate_durations')
            and len(samples) > 1 else None
        )
        analysis.summaries = (
            _summary_block(samples)
            if (analysis.bayesian_config or {}).get('estimate_kde') else None
        )

    elif analysis.analysis_type == 'bayesian':
        groups = db.query(SampleGroup).filter(SampleGroup.analysis_id == analysis.id).all()
        relationships = db.query(StratigraphicRelationship).filter(
            StratigraphicRelationship.analysis_id == analysis.id
        ).all()

        # From the analysis's own persisted tree, migrated from contexts +
        # relationships on first access.
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
                # Where the date is. Carried for services/dispersal.py, which
                # derives a site's position from the dates inside it -- so a
                # run that dropped these would report "no coordinates" on a
                # collection that has them.
                'latitude': s.latitude,
                'longitude': s.longitude,
                'tpq_age_bp': s.tpq_age_bp,
                'taq_age_bp': s.taq_age_bp,
                'outlier_prior': s.outlier_prior,
                'outlier_model': s.outlier_model,
                # The ordinates an ordinate-bearing sequence is built on. Not
                # optional extras: a wiggle match IS its ring offsets and a
                # deposition sequence IS its depths, so a rerun that dropped
                # them would solve a different model from the one that was
                # saved.
                'depth_cm': s.depth_cm,
                'ring_offset': s.ring_offset,
                'ring_offset_error': s.ring_offset_error,
            }
            for s in samples
        ]

        results, boundary_results, solver, interval_block, summary_block = _run_bayesian(
            measurement_dicts, root, analysis.bayesian_config or {},
            on_progress=on_progress,
        )
        analysis.solver = solver
        # No relabelling, unlike a fresh run: the tree came out of the
        # database, so every id is already the persisted one.
        analysis.intervals = interval_block
        analysis.summaries = summary_block

        # Each boundary's posterior on its element, mirrored onto the
        # per-context start/end for anything still reading that.
        for element_row in db.query(ModelElement).filter(
            ModelElement.analysis_id == analysis.id
        ).all():
            found = boundary_results.get(element_row.id)
            # Cleared as well as written, and for every element type that can
            # carry one: a rerun whose model no longer produces a posterior
            # here must not leave the last run's behind.
            if found or element_row.element_type in (
                    'boundary', 'wiggle_sequence', 'interpolated_depth'):
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

        # Explicitly cleared for a group not in context_results, so stale
        # data cannot linger.
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
    from its own persisted inputs. Takes an id rather than an Analysis
    because the row must be loaded in the worker's own session.
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
            # _rerun_analysis commits only on success, so a cancelled run
            # leaves the analysis with the results it already had.
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
                analysis.error_message = failures.classify(exc).message
                db.commit()
            raise
        return {'analysis_id': analysis_id, 'solver': (analysis.solver or {}).get('method')}
    return work


# No `status_code=202` on the decorator: that would apply to BOTH branches,
# and wait=true answers 200 with a finished result. The queued branch sets 202
# on its own response.
@router.post("/analysis/{analysis_id}/rerun")
def rerun_analysis(analysis_id: str, wait: bool = False,
                   db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    """
    Recompute a saved analysis's results in place from its own persisted
    inputs -- same id and URL, old results replaced.

    Queued rather than run here, since a Bayesian model takes tens of seconds
    and can take minutes (docs/ASYNC_JOBS_PLAN.md): answers 202 with a job_id
    to poll at GET /api/jobs/{job_id}.

    `wait=true` runs it in the request and answers 200 when finished. Kept
    permanently, because this app has no client library and anything
    scripting it from outside a browser has no event loop to poll from.
    """
    analysis = owned_or_404(
        db.query(Analysis).filter(Analysis.id == analysis_id).first(),
        user, 'That analysis')

    if wait:
        try:
            _rerun_analysis(analysis, db)
        except Exception as e:
            db.rollback()
            # Both the stored reason and the response say the same thing: a
            # synchronous re-run reports through the HTTP error, the queued one
            # through the analysis, and the two must not describe one failure
            # differently.
            failure = failures.classify(e)
            analysis.status = 'failed'
            analysis.error_message = failure.message
            db.commit()
            raise HTTPException(status_code=400, detail=failure.message)
        return {"success": True, "data": {"id": analysis.id, "status": analysis.status}}

    analysis.status = 'pending'
    analysis.error_message = None
    db.commit()
    job_id = jobs.submit('analysis_run', analysis.id, _run_analysis_job(analysis.id),
                         owner_id=user.id)
    return JSONResponse(status_code=202, content={
        "success": True,
        "data": {"id": analysis.id, "status": "queued", "job_id": job_id},
    })


@router.put("/analysis/{analysis_id}/model-tree")
def replace_model_tree(analysis_id: str, payload: Dict, db: Session = Depends(get_db),
                       user: User = Depends(current_user)):
    """
    Replace an analysis's model structure (services/model_tree.py).

    Body: {"model_tree": {...}, "sample_elements": {sample_id: element_id}}.
    `sample_elements` reassigns which phase each date belongs to; dates left
    out keep their current assignment.

    Validated by model_tree.validate_tree before anything is written, so a
    structurally impossible model is rejected with a 422 rather than
    surfacing later as a confusing MCMC failure. Existing results are cleared
    and status returns to 'pending', since they were computed under the
    previous structure.
    """
    analysis = owned_or_404(
        db.query(Analysis).filter(Analysis.id == analysis_id).first(),
        user, 'That analysis')

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
            'ring_offset': s.ring_offset,
            'ring_offset_error': s.ring_offset_error,
            'depth_cm': s.depth_cm,
            'outlier_prior': s.outlier_prior,
        }
        for s in samples
    ]
    try:
        validate_tree(root, prospective)
        # A structure that needs an ordinate the dates do not carry is
        # refused here too. Moving a date into a wiggle match is exactly the
        # edit that leaves it without a gap, and this is the screen it is
        # made on.
        validate_ordinates(root, prospective)
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
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

    rows = db.query(ModelElement).filter(ModelElement.analysis_id == analysis_id).all()
    return {
        "success": True,
        "data": {
            "id": analysis.id,
            "status": analysis.status,
            "model_tree": _tree_for_client(rows_to_tree(rows)),
        },
    }


@router.post("/analysis/{analysis_id}/duplicate")
def duplicate_analysis(analysis_id: str, db: Session = Depends(get_db),
                       user: User = Depends(current_user)):
    """
    Deep-copy an analysis's INPUTS into a new analysis, so a variant can be
    built from an existing one -- "the same dates, but grouped differently".

    Copies the samples, contexts and stratigraphic relationships, remapping
    ids to the new rows'. Results are NOT copied and status is 'pending', so
    the copy can be edited (PUT /analysis/{id}/inputs) and then run
    (POST /analysis/{id}/rerun).
    """
    source = owned_or_404(
        db.query(Analysis).filter(Analysis.id == analysis_id).first(),
        user, 'That analysis')

    copy = Analysis(
        name=f"{source.name} (copy)",
        analysis_type=source.analysis_type,
        status='pending',
        bayesian_config=source.bayesian_config,
        project_id=source.project_id,
        # The copier's, not the source's. They are the same person today --
        # you can only duplicate what you can see -- and writing it explicitly
        # is what keeps that true if sharing is ever added.
        owner_id=user.id,
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
            # modeled_start/end left null: they are results, and this copy
            # has not been run.
        )
        db.add(new_group)
        db.flush()
        groups_map[group.id] = new_group.id

    samples_map: Dict[str, str] = {}
    # source.measurements arrives in the original's own order (see the
    # relationship's order_by), so enumerating it hands the copy that order.
    for position, sample in enumerate(source.measurements):
        # A stored row carries the same attribute names a request
        # measurement does, so duplicating uses the one builder too.
        new_sample = _sample_row(
            copy.id, sample,
            group_id=groups_map.get(sample.group_id) if sample.group_id else None,
            element_id=groups_map.get(sample.element_id or sample.group_id)
            if (sample.element_id or sample.group_id) else None,
            sort_order=position,
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
            # Dropped rather than copied dangling. The columns are
            # polymorphic, so no FK enforces this.
            continue
        db.add(StratigraphicRelationship(
            analysis_id=copy.id,
            from_sample_id=from_id,
            to_sample_id=to_id,
            from_type=rel.from_type,
            to_type=rel.to_type,
            relationship=rel.relationship,
        ))

    # Element ids are remapped so the copy's phases are its own rows and its
    # dates point at them. Boundary posteriors are dropped as results.
    source_tree = rows_to_tree(
        db.query(ModelElement).filter(ModelElement.analysis_id == source.id).all()
    )
    if source_tree:
        element_map = dict(groups_map)
        for element in _iter_tree(source_tree):
            if element['id'] not in element_map:
                element_map[element['id']] = str(uuid.uuid4())
            element['posterior'] = None
        # Sample ids alongside, in their own map: a wiggle match's params name
        # one of its members, and the copy's members are the copy's rows.
        _relabel_tree(source_tree, element_map, samples_map)
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
    user: User = Depends(current_user),
):
    """
    Replace a saved analysis's inputs -- measurements, contexts,
    relationships, config -- wholesale: the "modify" half of
    duplicate-and-modify.

    Reuses BayesianRequest for validation, so an edit gets the same checks a
    fresh analysis does: cycle detection, dangling references and TPQ/TAQ
    contradictions are rejected with a 422 before anything is written.

    Existing results are DELETED and each context's modelled start/end
    cleared, since they came from the old inputs; status returns to 'pending'
    until the analysis is re-run.

    `analysis_type` in the body CONVERTS this analysis rather than creating a
    second one. Omitting it leaves the type alone, which is what an ordinary
    same-type edit means.
    """
    analysis = owned_or_404(
        db.query(Analysis).filter(Analysis.id == analysis_id).first(),
        user, 'That analysis')
    # Checked BEFORE anything is deleted below: an edit naming a curve this
    # user cannot use must leave the analysis exactly as it was, not wipe its
    # results and then refuse.
    authorise_curve_ids([m.curve_id for m in request.measurements], user.id)

    try:
        # Order matters (see delete_analysis): results and relationships
        # first, then samples, then the groups the samples referenced.
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
        # The tree describes the structure being replaced, and is rebuilt
        # from the new contexts/relationships on the next read or run
        # (_get_or_migrate_tree). Leaving it would point phases at deleted
        # contexts and dates at elements that no longer exist.
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
        for position, measurement in enumerate(request.measurements):
            if request.model_tree is not None:
                # A client-authored tree keeps its phase ids, so element_id
                # already names the phase. Falling back to group_id would
                # unassign every date, as such an analysis has no contexts.
                element_id = measurement.element_id
            else:
                element_id = groups_map.get(measurement.group_id) if measurement.group_id else None
            row = _sample_row(
                analysis.id, measurement,
                group_id=groups_map.get(measurement.group_id) if measurement.group_id else None,
                element_id=element_id,
                sort_order=position,
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

        # An authored tree is the authoritative structure, so it is stored
        # rather than left deleted. Otherwise the next read rebuilds a tree
        # from the contexts and an empty relationship set, inventing an
        # ordering and dropping every date into "Unassigned dates".
        if request.model_tree is not None:
            root = request.model_tree
            _relabel_tree(root, groups_map)
            _persist_tree(analysis.id, root, db)

        if request.name:
            analysis.name = request.name
        if request.analysis_type:
            # 'calibration' means ONE date on its own, so several
            # independent dates are promoted to a group calibration rather
            # than stored under a label they do not fit. The response reports
            # the type actually stored.
            analysis.analysis_type = (
                'group_calibration'
                if request.analysis_type == 'calibration' and len(request.measurements) > 1
                else request.analysis_type
            )
        analysis.bayesian_config = request.bayesian_config.model_dump()
        analysis.status = 'pending'
        analysis.error_message = None
        # Both describe the structure just replaced: the solver would label
        # a pending analysis "exact" for a model that no longer exists, and
        # every duration names a phase the new model may not have. The rerun
        # sets the solver again.
        analysis.solver = None
        analysis.intervals = None
        analysis.summaries = None
        db.commit()
    except HTTPException:
        db.rollback()
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
            # Not always the type asked for -- see the promotion above.
            "analysis_type": analysis.analysis_type,
            "measurements_count": len(request.measurements),
        },
    }
