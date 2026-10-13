"""
A whole project as one zip (feature spec W4): every analysis's results, its
inputs, the project's date collection, and a summary table across them.

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
from app.models import Analysis, ModelElement, Project, ProjectSample, User
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
    ('depth_cm', 'depth_cm'), ('inbuilt_years', 'inbuilt_years'),
    ('inbuilt_shape', 'inbuilt_shape'),
]

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


def samples_csv(samples: List[ProjectSample]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([column for column, _ in _SAMPLE_COLUMNS])
    for sample in samples:
        writer.writerow(['' if getattr(sample, field) is None else getattr(sample, field)
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


def build_zip(project: Project, analyses: List[Analysis], samples: List[ProjectSample],
              db: Session, user: User, date_format: Optional[str] = None,
              p_before_bp: Optional[float] = None) -> bytes:
    """
    The project as a zip:

        manifest.json        what was exported, when, and how it reads
        summary.csv          one row per Boundary or duration, per analysis
        samples.csv          the date collection, re-importable
        analyses/NNN-name-id.json   each analysis's Export JSON, unchanged
        inputs/NNN-name-id.json     what each was run from

    `date_format` adds formatted calendar columns to the summary as the CSV
    export does; `p_before_bp` adds a "P Before" column there and the same
    values to every analysis's JSON, as the results page's column does.
    """
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
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for index, analysis in enumerate(analyses, start=1):
            stem = f'{index:03d}-{_slug(analysis.name)}-{analysis.id[:8]}.json'
            exported = results_api.export_json(
                analysis.id, p_before_bp=threshold, db=db, user=user)
            archive.writestr(f'analyses/{stem}', json.dumps(exported, indent=1, default=str))
            archive.writestr(f'inputs/{stem}',
                             json.dumps(analysis_inputs(analysis, db), indent=1, default=str))
            p_values = (results_api._p_before(analysis.id, db, threshold)
                        if threshold is not None else None)
            for row in summary_rows(analysis, db, date_format, p_values):
                writer.writerow(row)
            listed.append({'id': analysis.id, 'name': analysis.name,
                           'analysis_type': analysis.analysis_type,
                           'status': analysis.status, 'file': stem})

        archive.writestr('summary.csv', summary.getvalue())
        archive.writestr('samples.csv', samples_csv(samples))
        archive.writestr('manifest.json', json.dumps({
            'project': {'id': project.id, 'name': project.name},
            'exported_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'date_format': date_format,
            'p_before_bp': threshold,
            'reporting_level': SIGMA_2,
            'analyses': listed,
            'samples': len(samples),
            'notes': (
                'analyses/ holds each analysis exactly as its Results page '
                'Export > JSON gives it; inputs/ what it was run from. Calendar ages are cal BP '
                'unless a column says otherwise; durations are in years.'
            ),
        }, indent=1))
    return buffer.getvalue()

