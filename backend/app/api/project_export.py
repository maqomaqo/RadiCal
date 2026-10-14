"""
A whole project as one zip (feature spec W4): every analysis's results, its
inputs, the project's date collection, and a summary table across them.

The zip is also the package another installation imports
(services/project_import.py): `inputs/` is enough to rebuild each analysis,
`curves/` carries every curve that is not shipped with the app, and `runs/`
the run each result came from, so the importer can set its own run beside it.
`manifest.format_version` says which of these a package holds.

Built from the per-analysis exports rather than beside them. Each analysis's
JSON is byte for byte what its own Export JSON button gives, so a script that
reads one (the AD1250-1450 regime's score_radical.py, say) reads a directory of
them unchanged, and the zip cannot report a different run from the page.

Read-only, like every export: an analysis with no stored model tree exports
without one rather than being migrated here.

Synchronous. The assembly is database reads and arithmetic on stored results
-- no solver, no plots -- so the 274-analysis regime is seconds. The PDF, which
draws a figure per parameter, is deliberately left out for that reason.
"""
import csv
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.api import results as results_api
from app.api import run_history
from app.models import (
    Analysis, AnalysisRun, ModelElement, Project, ProjectSample, StoredCalibrationCurve, User,
)
from app.services import curve_registry
from app.services import date_format as date_format_service
from app.services.calibration import SIGMA_2
from app.services.model_tree import rows_to_tree

# The fields a date is entered with, as an analysis stores them -- everything
# needed to put the same run back together, and no result.
_INPUT_FIELDS = (
    'id', 'sample_name', 'age_bp', 'error_1sigma', 'material_type', 'sample_type',
    'curve_id', 'delta_r', 'delta_r_error', 'delta_r_source', 'reservoir_age',
    'latitude', 'longitude', 'location', 'notes', 'depth_cm', 'ring_offset',
    'ring_offset_error', 'inbuilt_years', 'inbuilt_shape', 'outlier_prior',
    'outlier_model', 'group_id', 'element_id', 'tpq_age_bp', 'taq_age_bp',
)

# What a package says it is. Version 0 is the zip as first shipped, with no
# format key: inputs but no curves, runs or version stamp.
PACKAGE_FORMAT = 'radical-project'
PACKAGE_VERSION = 1

# A stored curve as a package carries it: enough to store the same numbers
# again, and the hash that proves they are the same.
_CURVE_FIELDS = (
    'id', 'kind', 'name', 'description', 'reference', 'doi', 'type', 'sample_type',
    'aliases', 'source_filename', 'source_text', 'parse_options', 'recipe',
    'content_sha256',
)

# The collection as the CSV importer reads it (date_import.TEMPLATE_COLUMNS),
# so the file goes straight back in; `phase` is the collection's
# default_context_name. The importer's template has no sample type or
# location, so those follow it, where a spreadsheet keeps them and the
# importer passes them by.
_SAMPLE_COLUMNS = [
    ('sample_name', 'sample_name'), ('age_bp', 'age_bp'),
    ('error_1sigma', 'error_1sigma'), ('material_type', 'material_type'),
    ('curve_id', 'curve_id'), ('phase', 'default_context_name'),
    ('delta_r', 'delta_r'), ('delta_r_error', 'delta_r_error'),
    ('tpq_age_bp', 'tpq_age_bp'), ('taq_age_bp', 'taq_age_bp'),
    ('latitude', 'latitude'), ('longitude', 'longitude'), ('notes', 'notes'),
    ('sample_type', 'sample_type'), ('location', 'location'),
    ('depth_cm', 'depth_cm'), ('ring_offset', 'ring_offset'),
    ('ring_offset_error', 'ring_offset_error'), ('inbuilt_years', 'inbuilt_years'),
    ('inbuilt_shape', 'inbuilt_shape'),
]

# The collection as a package carries it: every field a date is stored with,
# so the importer can store the same row. samples.csv beside it is for people.
COLLECTION_FIELDS = (
    'sample_name', 'age_bp', 'error_1sigma', 'material_type', 'sample_type',
    'curve_id', 'reservoir_age', 'delta_r', 'delta_r_error', 'delta_r_source',
    'latitude', 'longitude', 'depth_cm', 'ring_offset', 'ring_offset_error',
    'inbuilt_years', 'inbuilt_shape', 'location', 'notes', 'default_context_name',
    'tpq_age_bp', 'taq_age_bp',
)

# One row per modelled transition or duration, per analysis: long rather than
# wide, so an analysis with forty Boundaries and one with two share a header.
# A calendar age and a length of time never share a column (as in the CSV
# export), so a Boundary fills the BP pair and a duration the years pair.
SUMMARY_HEADER = [
    'Analysis', 'Analysis ID', 'Type', 'Status', 'Solver', 'Converged',
    'Convergence', 'R-hat', 'Sweeps', 'Seed', 'Cross-check', 'A_model', 'A_overall',
    'log10 evidence',
    'Quantity', 'Name', 'Median BP', '95.4% HPD BP', 'Median (yr)', '95.4% Range (yr)',
]

_DURATION_KINDS = {
    'sequence_duration': 'Sequence duration',
    'phase_duration': 'Phase duration',
    'hiatus_duration': 'Hiatus duration',
    'sample_duration': 'Dated events duration',
}


def _slug(text: str, limit: int = 60) -> str:
    """A name safe in a file name on every system the zip will be opened on."""
    slug = re.sub(r'[^A-Za-z0-9._-]+', '-', text or '').strip('-.')
    return (slug or 'analysis')[:limit]


def _number(value, places: int = 0) -> str:
    return '' if value is None else f'{value:.{places}f}'


def _strip_results(element: Dict) -> Dict:
    """A model tree with every posterior taken off: inputs only."""
    out = {k: v for k, v in element.items() if k not in ('posterior', 'children')}
    out['children'] = [_strip_results(child) for child in element.get('children') or []]
    return out


def analysis_inputs(analysis: Analysis, db: Session) -> Dict:
    """What an analysis was run from: its settings, its dates, its model."""
    tree = rows_to_tree(
        db.query(ModelElement).filter(ModelElement.analysis_id == analysis.id).all())
    return {
        'id': analysis.id,
        'name': analysis.name,
        'analysis_type': analysis.analysis_type,
        'bayesian_config': analysis.bayesian_config or {},
        'measurements': [{f: getattr(s, f, None) for f in _INPUT_FIELDS}
                         for s in analysis.measurements],
        'model_tree': _strip_results(tree) if tree else None,
    }


def _round_positions(value, precision: str):
    """
    Every latitude and longitude in a JSON-shaped value, as the project allows
    them to leave it (results._rounded_coordinate). The package carries a
    run's whole record and every date's inputs, and each of them holds the
    dates' positions.
    """
    if precision == 'full':
        return value
    if isinstance(value, dict):
        return {k: (results_api._rounded_coordinate(v, precision)
                    if k in ('latitude', 'longitude') and isinstance(v, (int, float))
                    else _round_positions(v, precision))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_round_positions(v, precision) for v in value]
    return value


def package_inputs(analysis: Analysis, db: Session) -> Dict:
    """
    analysis_inputs, plus what rebuilding the analysis elsewhere needs and the
    run fingerprint does not: each date's ΔR provenance, and the structure of
    a Bayesian analysis stored before model trees, built the way reading it
    would build it but without writing it.

    Kept apart from analysis_inputs because that is what run_history hashes,
    and a field added there would make every existing run read as having
    different inputs from the next one.
    """
    from app.api.analysis import migrated_tree

    inputs = analysis_inputs(analysis, db)
    for row, sample in zip(inputs['measurements'], analysis.measurements):
        row['delta_r_source'] = sample.delta_r_source
    if inputs['model_tree'] is None and analysis.analysis_type == 'bayesian':
        migrated = migrated_tree(analysis, db)
        if migrated is not None:
            root, _linearized, element_ids = migrated
            inputs['model_tree'] = _strip_results(root)
            for row in inputs['measurements']:
                row['element_id'] = element_ids.get(row['id'])
    return inputs


def author_run(analysis: Analysis, db: Session) -> Optional[Dict]:
    """
    The run the analysis's current results came from, as run_history kept it,
    or None. Only while it still describes the analysis: a completed analysis
    whose inputs have not changed since. An edited, unrun analysis has no
    result to replicate.
    """
    if analysis.status != 'completed':
        return None
    run = (db.query(AnalysisRun)
           .filter(AnalysisRun.analysis_id == analysis.id)
           .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
           .first())
    if run is None or run.inputs_hash != run_history._inputs_hash(analysis, db):
        return None
    return {
        'created_at': run.created_at.isoformat() if run.created_at else None,
        'app_commit': run.app_commit,
        'payload': run.payload,
    }


def curve_records(curve_ids, db: Session) -> List[Dict]:
    """
    Every curve these ids need, parents of a mixture included, parents first.
    A builtin is named with its hash only, since every installation ships it;
    a stored curve is carried whole.
    """
    records: Dict[str, Dict] = {}

    def visit(curve_id: str, depth: int = 0) -> None:
        if not curve_id or curve_id in records or depth > 8:
            return
        try:
            spec = curve_registry.get_spec(curve_id)
        except ValueError:
            # A curve that no longer loads is left out rather than failing the
            # export; importing the package then names it as missing.
            return
        if spec.kind == 'builtin':
            records[curve_id] = {'id': curve_id, 'kind': 'builtin', 'name': spec.name,
                                 'content_sha256': spec.content_sha256}
            return
        row = db.query(StoredCalibrationCurve).filter(
            StoredCalibrationCurve.id == curve_id).one()
        for parent in ((row.recipe or {}).get('a'), (row.recipe or {}).get('b')):
            visit(parent, depth + 1)
        records[curve_id] = {field: getattr(row, field) for field in _CURVE_FIELDS}

    for curve_id in sorted({c for c in curve_ids if c}):
        visit(curve_id)
    return list(records.values())


def collection_records(samples: List[ProjectSample], precision: str = 'full') -> List[Dict]:
    return _round_positions(
        [{f: getattr(sample, f) for f in COLLECTION_FIELDS} for sample in samples], precision)


def samples_csv(samples: List[ProjectSample], precision: str = 'full') -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([column for column, _ in _SAMPLE_COLUMNS])
    for record in collection_records(samples, precision):
        writer.writerow(['' if record.get(field) is None else record[field]
                         for _, field in _SAMPLE_COLUMNS])
    return output.getvalue()


def _solver_cells(analysis: Analysis) -> List:
    solver = analysis.solver or {}
    method = solver.get('method') or ''
    if analysis.analysis_type != 'bayesian':
        method = 'calibration'
    cross = solver.get('cross_check') or {}
    converged = solver.get('converged')
    return [
        method,
        '' if converged is None else ('yes' if converged else 'no'),
        _number(solver.get('convergence'), 3),
        _number(solver.get('rhat'), 3),
        solver.get('sweeps') or '',
        '' if solver.get('seed') is None else solver['seed'],
        cross.get('status') or '',
        _number(solver.get('model_agreement'), 1),
        _number(solver.get('overall_agreement'), 1),
        _number((solver.get('evidence') or {}).get('log10'), 3),
    ]


def summary_rows(analysis: Analysis, db: Session, date_format: str,
                 p_values: Optional[Dict]) -> List[List]:
    """
    The analysis's Boundaries (and anything else in its tree with a posterior
    of its own) in reading order, then its durations. An analysis with
    neither still gets one row, so the summary lists every analysis.
    """
    era = date_format in date_format_service.ERA_LABELS
    head = [analysis.name, analysis.id, analysis.analysis_type, analysis.status,
            *_solver_cells(analysis)]
    rows: List[List] = []

    def extra(posterior: Optional[Dict], element_id: Optional[str]) -> List:
        cells = []
        if era:
            cells += [
                date_format_service.format_age((posterior or {}).get('posterior_median'),
                                               date_format) if posterior else '',
                date_format_service.format_hpd_ranges(
                    (posterior or {}).get('hpd_95_ranges'), date_format) if posterior else '',
            ]
        if p_values is not None:
            p = p_values['elements'].get(element_id) if element_id else None
            cells.append('' if p is None else f'{p:.6f}')
        return cells

    tree = rows_to_tree(
        db.query(ModelElement).filter(ModelElement.analysis_id == analysis.id).all())
    stack = [tree] if tree else []
    ordered: List[Dict] = []
    while stack:
        element = stack.pop(0)
        ordered.append(element)
        stack = list(element.get('children') or []) + stack
    for element in ordered:
        posterior = element.get('posterior') or {}
        if not posterior.get('probability_distribution'):
            continue
        kind = (element.get('element_type') or 'element').replace('_', ' ').capitalize()
        rows.append(head + [
            kind, element.get('name') or '',
            _number(posterior.get('posterior_median')),
            results_api._format_hpd_ranges(posterior.get('hpd_95_ranges'), None, None),
            '', '',
        ] + extra(posterior, element['id']))

    for record in ((analysis.intervals or {}).get('durations') or []):
        years = record.get('years') or {}
        rows.append(head + [
            _DURATION_KINDS.get(record.get('kind'), record.get('kind') or ''),
            record.get('name') or '', '', '',
            _number(years.get('median')), results_api._years_ranges(years.get('hpd_95_ranges')),
        ] + extra(None, None))

    if not rows:
        rows.append(head + ['', '', '', '', '', ''] + extra(None, None))
    return rows


def build_zip(project: Optional[Project], analyses: List[Analysis],
              samples: List[ProjectSample], db: Session, user: User,
              date_format: Optional[str] = None, p_before_bp: Optional[float] = None,
              selection: bool = False) -> bytes:
    """
    The project as a zip:

        manifest.json        what was exported, when, and how it reads
        summary.csv          one row per Boundary or duration, per analysis
        samples.csv          the date collection, re-importable
        samples.json         the same dates with every stored field
        analyses/NNN-name-id.json   each analysis's Export JSON, unchanged
        inputs/NNN-name-id.json     what each was run from
        runs/NNN-name-id.json       the run its results came from, if current
        curves/ID.json              each curve used that is not shipped

    `project` is None for the Unassigned list, which has no project row.
    `selection` records that only some of the project's analyses are here.
    Positions leave at the project's coordinate precision everywhere they
    appear: the inputs and runs carry them as well as the results.

    `date_format` adds formatted calendar columns to the summary as the CSV
    export does; `p_before_bp` adds a "P Before" column there and the same
    values to every analysis's JSON, as the results page's column does.
    """
    precision = (project.coordinate_precision if project else None) or 'full'
    date_format = date_format_service.normalise(date_format)
    threshold = results_api._threshold(p_before_bp)

    summary = io.StringIO()
    writer = csv.writer(summary)
    header = list(SUMMARY_HEADER)
    era = date_format_service.ERA_LABELS.get(date_format)
    if era:
        header += [f'Median ({era})', f'95.4% HPD ({era})']
    if threshold is not None:
        header.append(results_api._p_before_header(threshold, date_format))
    writer.writerow(header)

    buffer = io.BytesIO()
    listed = []
    curve_ids = {s.curve_id for s in samples}
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for index, analysis in enumerate(analyses, start=1):
            stem = f'{index:03d}-{_slug(analysis.name)}-{analysis.id[:8]}.json'
            exported = results_api.export_json(
                analysis.id, p_before_bp=threshold, db=db, user=user)
            archive.writestr(f'analyses/{stem}', json.dumps(exported, indent=1, default=str))
            archive.writestr(f'inputs/{stem}', json.dumps(
                _round_positions(package_inputs(analysis, db), precision),
                indent=1, default=str))
            run = author_run(analysis, db)
            if run is not None:
                archive.writestr(f'runs/{stem}', json.dumps(
                    _round_positions(run, precision), indent=1, default=str))
            curve_ids.update(s.curve_id for s in analysis.measurements)
            p_values = (results_api._p_before(analysis.id, db, threshold)
                        if threshold is not None else None)
            for row in summary_rows(analysis, db, date_format, p_values):
                writer.writerow(row)
            listed.append({'id': analysis.id, 'name': analysis.name,
                           'analysis_type': analysis.analysis_type,
                           'status': analysis.status, 'file': stem,
                           'measurements': len(analysis.measurements),
                           'has_run': run is not None})

        curves = curve_records(curve_ids, db)
        for record in curves:
            if record['kind'] != 'builtin':
                archive.writestr(f"curves/{record['id']}.json",
                                 json.dumps(record, indent=1, default=str))

        archive.writestr('summary.csv', summary.getvalue())
        archive.writestr('samples.csv', samples_csv(samples, precision))
        archive.writestr('samples.json', json.dumps(
            collection_records(samples, precision), indent=1, default=str))
        archive.writestr('manifest.json', json.dumps({
            'format': PACKAGE_FORMAT,
            'format_version': PACKAGE_VERSION,
            'app_commit': run_history.app_commit(),
            'project': ({'id': project.id, 'name': project.name,
                         'description': project.description} if project
                        else {'id': None, 'name': 'Unassigned', 'description': None}),
            'selection': selection,
            'coordinate_precision': precision,
            'curves': [{k: record[k] for k in ('id', 'kind', 'name', 'content_sha256')}
                       for record in curves],
            'exported_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'date_format': date_format,
            'p_before_bp': threshold,
            'reporting_level': SIGMA_2,
            'analyses': listed,
            'samples': len(samples),
            'notes': (
                'analyses/ holds each analysis exactly as its Results page '
                'Export > JSON gives it; inputs/ what it was run from; runs/ the run its '
                'results came from; curves/ every curve used that RadiCal does not ship. '
                'Projects > Import project reads this file back in. Calendar ages are cal BP '
                'unless a column says otherwise; durations are in years.'
            ),
        }, indent=1))
    return buffer.getvalue()

