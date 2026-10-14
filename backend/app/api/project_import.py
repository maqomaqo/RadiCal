"""
Reading a project package (api/project_export.build_zip) back in, as a new
project: its date collection, every analysis's inputs, the curves they need,
and the run each result came from.

Two steps, as every import here is: a preview that stores nothing and lists
what is wrong, then the import itself, which re-reads the same file rather
than trusting anything the client sends back. It is all or nothing -- one bad
analysis fails the package, with every problem listed.

What arrives is inputs, not results. Each analysis lands 'pending' and is run
here, on this installation's code and curves. The author's run, when the
package carries it, is kept as the analysis's previous run, so the project's
re-run report and the Results page's earlier-run comparison show where this
installation's answer differs from the author's.

Curves are matched by their numbers, not their names: a curve whose hash
matches one of the importer's own is reused, and any other is stored for
them. A builtin whose hash differs from the package's is allowed, with a
warning, since a different IntCal20 file is a reason results may differ and
not a reason to refuse.
"""
import copy
import io
import json
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api import run_history
from app.api.results import COORDINATE_PRECISIONS
from app.api.analysis import _iter_tree, _persist_tree, _relabel_tree, _sample_row
from app.api.auth import current_user
from app.api.project_export import PACKAGE_FORMAT, PACKAGE_VERSION
from app.database import get_db
from app.models import Analysis, AnalysisRun, Project, ProjectSample, StoredCalibrationCurve, User
from app.schemas.sample import BayesianConfigInput, BayesianRequest, ProjectSampleInput
from app.services import curve_import, curve_mix, curve_registry, date_import
from app.services.calibration import CalibrationCurve

router = APIRouter()

MAX_PACKAGE_BYTES = 100 * 1024 * 1024
# A zip states its members' sizes before they are read, so a package that
# would unpack to something absurd is refused without unpacking it.
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
MAX_MEMBERS = 20000

ANALYSIS_TYPES = ('calibration', 'group_calibration', 'bayesian')

# A placeholder for validation only: a date on a curve the import has yet to
# store cannot name it, since the schema checks the curve exists. It never
# reaches a stored row.
_PLACEHOLDER_CURVE = 'intcal20'


@dataclass
class _Curve:
    """One curve the package names, and what this import will do with it."""
    record: Dict[str, Any]
    action: str              # 'shipped' | 'reuse' | 'add'
    local_id: Optional[str]  # set for shipped and reuse; for add, when stored
    curve: Optional[CalibrationCurve] = None


@dataclass
class _Analysis:
    stem: str
    name: str
    analysis_type: str
    request: BayesianRequest
    # The stored values of outlier_prior/outlier_model, which the schema
    # would fill in: kept as they were, so the import stores what was sent.
    outliers: List[Tuple[Any, Any]]
    curve_ids: List[str]
    model_tree: Optional[Dict]
    config: Dict
    run: Optional[Dict]
    source_id: Optional[str]


@dataclass
class ImportPlan:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    manifest: Dict[str, Any] = field(default_factory=dict)
    curves: Dict[str, _Curve] = field(default_factory=dict)
    analyses: List[_Analysis] = field(default_factory=list)
    samples: List[Tuple[ProjectSampleInput, str]] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        project = self.manifest.get('project') or {}
        return {
            'ok': not self.errors,
            'errors': self.errors,
            'warnings': self.warnings,
            'notes': self.notes,
            'project_name': project.get('name') or 'Imported project',
            'project_description': project.get('description'),
            'exported_at': self.manifest.get('exported_at'),
            'app_commit': self.manifest.get('app_commit'),
            'format_version': self.manifest.get('format_version', 0),
            'samples': len(self.samples),
            'analyses': [{
                'name': a.name,
                'analysis_type': a.analysis_type,
                'measurements': len(a.request.measurements),
                'has_run': a.run is not None,
            } for a in self.analyses],
            'curves': [{
                'id': c.record['id'],
                'name': c.record.get('name') or c.record['id'],
                'kind': c.record.get('kind'),
                'action': c.action,
            } for c in self.curves.values()],
        }


# --------------------------------------------------------------------------
# Reading the zip
# --------------------------------------------------------------------------

def _open(content: bytes, plan: ImportPlan) -> Optional[Dict[str, bytes]]:
    if not content:
        plan.errors.append('The file is empty.')
        return None
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        plan.errors.append(
            'This is not a project package. Use a .zip saved from a project page '
            '(Export) in RadiCal.')
        return None
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS or sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
        plan.errors.append('The package is too large to import.')
        return None
    names = {i.filename for i in infos}
    if 'manifest.json' not in names or not any(n.startswith('inputs/') for n in names):
        plan.errors.append(
            'This zip is not a RadiCal project package: it has no manifest.json and '
            'inputs/ folder. Use a .zip saved from a project page (Export).')
        return None
    wanted = ('manifest.json', 'samples.json', 'samples.csv')
    return {i.filename: archive.read(i) for i in infos
            if i.filename in wanted
            or (i.filename.endswith('.json')
                and i.filename.split('/')[0] in ('inputs', 'runs', 'curves'))}


def _json(files: Dict[str, bytes], name: str, plan: ImportPlan) -> Any:
    try:
        return json.loads(files[name].decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        plan.errors.append(f'{name} could not be read: {exc}.')
        return None


def _describe(exc: ValidationError) -> List[str]:
    """A pydantic error as short sentences, located where that helps."""
    out = []
    for error in exc.errors():
        where = '.'.join(str(part) for part in error.get('loc') or ())
        message = error.get('msg', 'invalid').removeprefix('Value error, ')
        out.append(f'{where}: {message}' if where else message)
    return out


# --------------------------------------------------------------------------
# Curves
# --------------------------------------------------------------------------

def _build_curve(record: Dict, curves: Dict[str, _Curve]) -> CalibrationCurve:
    """The numbers a carried curve stands for, built from what it carries."""
    if record.get('kind') == 'mixed':
        recipe = record.get('recipe') or {}
        parents = []
        for key in ('a', 'b'):
            parent = curves.get(recipe.get(key))
            if parent is None:
                raise ValueError(f"it mixes '{recipe.get(key)}', which the package does not carry")
            parents.append(parent.curve if parent.curve is not None
                           else curve_registry.resolve(parent.local_id))
        return curve_mix.mix(parents[0], parents[1], float(recipe.get('percent_a', 50.0)),
                             float(recipe.get('percent_a_error', 0.0)),
                             name=record.get('name') or 'Mixed')
    options = record.get('parse_options') or {}
    result = curve_import.parse_curve(record.get('source_text') or '',
                                      calendar=options.get('calendar', 'cal_bp'),
                                      columns=options.get('columns'))
    if not result.ok:
        raise ValueError(result.errors[0])
    return CalibrationCurve([p[0] for p in result.points], [p[1] for p in result.points],
                            [p[2] for p in result.points])


def _plan_curves(files: Dict[str, bytes], plan: ImportPlan, owner_id: str, db: Session) -> None:
    for entry in plan.manifest.get('curves') or []:
        if entry.get('kind') != 'builtin':
            continue
        curve_id = entry.get('id')
        if curve_id not in curve_registry.BUILTIN_CURVE_IDS:
            plan.errors.append(f"The package names a shipped curve '{curve_id}' that this "
                               'installation does not have.')
            continue
        plan.curves[curve_id] = _Curve(entry, 'shipped', curve_id)
        local = curve_registry.get_spec(curve_id).content_sha256
        if entry.get('content_sha256') and entry['content_sha256'] != local:
            plan.warnings.append(
                f"{entry.get('name') or curve_id} here is not the same file the package "
                'was made with, so results may differ from the author\'s.')

    records = []
    for name in sorted(n for n in files if n.startswith('curves/')):
        record = _json(files, name, plan)
        if isinstance(record, dict) and record.get('id'):
            records.append(record)

    own = db.query(StoredCalibrationCurve).filter(
        StoredCalibrationCurve.created_by == owner_id).all()
    by_hash = {row.content_sha256: row.id for row in own}

    # Parents before the mixtures that need them. The export writes them in
    # that order already; this does not rely on it.
    pending = list(records)
    while pending:
        progressed = False
        for record in list(pending):
            recipe = record.get('recipe') or {}
            if record.get('kind') == 'mixed' and not all(
                    recipe.get(k) in plan.curves or recipe.get(k) in curve_registry.BUILTIN_CURVE_IDS
                    for k in ('a', 'b')):
                continue
            for key in ('a', 'b'):
                parent = recipe.get(key)
                if record.get('kind') == 'mixed' and parent not in plan.curves:
                    plan.curves[parent] = _Curve({'id': parent, 'kind': 'builtin',
                                                  'name': parent}, 'shipped', parent)
            pending.remove(record)
            progressed = True
            label = record.get('name') or record['id']
            try:
                built = _build_curve(record, plan.curves)
            except ValueError as exc:
                plan.errors.append(f"The curve '{label}' could not be read: {exc}.")
                continue
            digest = curve_registry.content_hash(built)
            if digest != record.get('content_sha256'):
                plan.errors.append(
                    f"The curve '{label}' does not match its own checksum, so the package "
                    'has been altered or damaged. It will not be imported.')
                continue
            if digest in by_hash:
                plan.curves[record['id']] = _Curve(record, 'reuse', by_hash[digest], built)
            else:
                plan.curves[record['id']] = _Curve(record, 'add', None, built)
        if not progressed:
            for record in pending:
                plan.errors.append(
                    f"The curve '{record.get('name') or record['id']}' mixes a curve the "
                    'package does not carry.')
            break


def _curve_for(curve_id: str, plan: ImportPlan, owner_id: str, where: str) -> Optional[str]:
    """
    The id a date's curve is validated under, registering what a version 0
    package (which carried no curves) names: a shipped curve, or one of the
    importer's own under that id. Anything else is an error, naming it.
    """
    known = plan.curves.get(curve_id)
    if known is None:
        if curve_id in curve_registry.BUILTIN_CURVE_IDS:
            known = plan.curves[curve_id] = _Curve(
                {'id': curve_id, 'kind': 'builtin', 'name': curve_id}, 'shipped', curve_id)
        elif curve_registry.visible_to(curve_id, owner_id):
            known = plan.curves[curve_id] = _Curve(
                {'id': curve_id, 'kind': 'stored', 'name': curve_id}, 'reuse', curve_id)
        else:
            plan.errors.append(
                f"{where} uses the curve '{curve_id}', which the package does not carry. "
                'Import that curve on the Curves page first.')
            return None
    return known.local_id if known.action != 'add' else _PLACEHOLDER_CURVE


# --------------------------------------------------------------------------
# Dates and analyses
# --------------------------------------------------------------------------

def _plan_samples(files: Dict[str, bytes], plan: ImportPlan, owner_id: str) -> None:
    if 'samples.json' in files:
        records = _json(files, 'samples.json', plan) or []
    elif 'samples.csv' in files:
        # A version 0 package: the CSV is all it has, read by the ordinary
        # importer, whose curve names must already resolve here.
        parsed = date_import.parse_upload('samples.csv', files['samples.csv'], owner_id)
        if parsed.errors:
            # An empty collection is written as a header and nothing else,
            # which the importer reports as having no dates.
            plan.errors.extend(f'samples.csv: {e}' for e in parsed.errors
                               if e != 'No dates found in the file.')
            return
        records = [{**d, 'default_context_name': d.get('phase_name')} for d in parsed.dates]
    else:
        return
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            plan.errors.append(f'Collection date {index} is not a date.')
            continue
        where = f"Collection date '{record.get('sample_name') or index}'"
        curve_id = record.get('curve_id') or 'intcal20'
        validated_curve = _curve_for(curve_id, plan, owner_id, where)
        if validated_curve is None:
            continue
        fields = {k: v for k, v in record.items()
                  if k in ProjectSampleInput.model_fields and v is not None}
        try:
            sample = ProjectSampleInput.model_validate({**fields, 'curve_id': validated_curve})
        except ValidationError as exc:
            plan.errors.extend(f'{where}: {m}' for m in _describe(exc))
            continue
        plan.samples.append((sample, curve_id))


def _merged_config(stored: Dict) -> Dict:
    """
    The analysis's settings, every known one validated and every unknown one
    kept: a key this version does not model is still the author's setting,
    and dropping it would change what the analysis is.
    """
    validated = BayesianConfigInput.model_validate(stored).model_dump()
    return {key: validated.get(key, value) for key, value in stored.items()}


def _plan_analysis(name: str, inputs: Dict, run: Optional[Dict], plan: ImportPlan,
                   owner_id: str) -> None:
    label = inputs.get('name') or name
    where = f"Analysis '{label}'"
    analysis_type = inputs.get('analysis_type')
    if analysis_type not in ANALYSIS_TYPES:
        plan.errors.append(f'{where} has an unknown type, {analysis_type!r}.')
        return
    measurements = inputs.get('measurements') or []
    if not measurements:
        plan.errors.append(f'{where} has no dates.')
        return

    curve_ids, outliers, rows = [], [], []
    for index, m in enumerate(measurements):
        if not isinstance(m, dict):
            plan.errors.append(f'{where}: date {index + 1} is not a date.')
            return
        curve_id = m.get('curve_id') or 'intcal20'
        validated_curve = _curve_for(curve_id, plan, owner_id,
                                     f"{where}, date '{m.get('sample_name')}'")
        if validated_curve is None:
            return
        curve_ids.append(curve_id)
        outliers.append((m.get('outlier_prior'), m.get('outlier_model')))
        row = {k: v for k, v in m.items() if v is not None and k not in ('group_id',)}
        row['id'] = str(m.get('id') or f'date-{index}')
        row['curve_id'] = validated_curve
        rows.append(row)

    tree = inputs.get('model_tree')
    if tree is None:
        for row in rows:
            row.pop('element_id', None)
    try:
        config = _merged_config(inputs.get('bayesian_config') or {})
        request = BayesianRequest.model_validate({
            'name': label,
            'measurements': rows,
            'model_tree': copy.deepcopy(tree),
            'bayesian_config': config,
            'analysis_type': analysis_type,
        })
    except ValidationError as exc:
        plan.errors.extend(f'{where}: {m}' for m in _describe(exc))
        return
    except ValueError as exc:
        plan.errors.append(f'{where}: {exc}')
        return
    plan.analyses.append(_Analysis(
        stem=name, name=label, analysis_type=analysis_type, request=request,
        outliers=outliers, curve_ids=curve_ids, model_tree=tree, config=config,
        run=run if isinstance(run, dict) and isinstance(run.get('payload'), dict) else None,
        source_id=inputs.get('id')))


def plan_import(content: bytes, owner_id: str, db: Session) -> ImportPlan:
    """Read a package and decide everything the import would do, storing nothing."""
    plan = ImportPlan()
    files = _open(content, plan)
    if files is None:
        return plan
    manifest = _json(files, 'manifest.json', plan)
    if not isinstance(manifest, dict):
        plan.errors.append('manifest.json is not a package manifest.')
        return plan
    plan.manifest = manifest
    if manifest.get('format', PACKAGE_FORMAT) != PACKAGE_FORMAT:
        plan.errors.append('This zip is not a RadiCal project package.')
        return plan
    version = manifest.get('format_version', 0)
    if not isinstance(version, int) or version > PACKAGE_VERSION:
        plan.errors.append('This package was made by a newer version of RadiCal. '
                           'Update this installation to import it.')
        return plan
    if version == 0:
        plan.notes.append('This package was made before packages carried their curves and '
                          'runs. Its analyses import without the author\'s results to '
                          'compare against.')

    _plan_curves(files, plan, owner_id, db)
    _plan_samples(files, plan, owner_id)
    for name in sorted(n for n in files if n.startswith('inputs/')):
        inputs = _json(files, name, plan)
        if not isinstance(inputs, dict):
            continue
        stem = name.split('/', 1)[1]
        run_name = f'runs/{stem}'
        run = _json(files, run_name, plan) if run_name in files else None
        _plan_analysis(stem, inputs, run, plan, owner_id)

    if not plan.analyses and not plan.samples and not plan.errors:
        plan.errors.append('The package holds no analyses and no dates.')

    precision = manifest.get('coordinate_precision') or 'full'
    if precision != 'full' and any((a.config or {}).get('estimate_dispersal')
                                   for a in plan.analyses):
        plan.warnings.append(
            'Positions in this package were rounded before it was shared, so spatial '
            'results may differ from the author\'s.')
    added = [c for c in plan.curves.values() if c.action == 'add']
    if added:
        plan.notes.append(
            f"{len(added)} curve{'s' if len(added) != 1 else ''} will be added to your "
            'curves: ' + ', '.join(c.record.get('name') or c.record['id'] for c in added) + '.')
    without_run = [a.name for a in plan.analyses if a.run is None]
    if version > 0 and without_run:
        plan.notes.append(
            f"{len(without_run)} of {len(plan.analyses)} analyses have no result from the "
            'author to compare against (not run, or edited since).')
    return plan


# --------------------------------------------------------------------------
# Storing it
# --------------------------------------------------------------------------

def _free_curve_id(base: str, owner_id: str, taken: set) -> str:
    for n in range(1, 1000):
        candidate = base if n == 1 else f'{base}-{n}'
        if candidate not in taken and curve_registry.resolve_alias(candidate, owner_id) is None:
            return candidate
    raise HTTPException(status_code=409, detail=f"Could not find a free curve id for '{base}'.")


def _store_curves(plan: ImportPlan, owner_id: str, db: Session) -> None:
    taken = {row[0] for row in db.query(StoredCalibrationCurve.id).all()}
    taken.update(curve_registry.BUILTIN_CURVE_IDS)
    for curve in plan.curves.values():
        if curve.action != 'add':
            continue
        record = curve.record
        local_id = _free_curve_id(record['id'], owner_id, taken)
        taken.add(local_id)
        aliases = [a for a in (record.get('aliases') or [])
                   if curve_registry.resolve_alias(a, owner_id) is None]
        name = record.get('name') or local_id
        if curve_registry.resolve_alias(name, owner_id) is not None:
            name = f'{name} (imported)'
        recipe = record.get('recipe')
        if recipe:
            recipe = {**recipe, 'a': plan.curves[recipe['a']].local_id,
                      'b': plan.curves[recipe['b']].local_id}
        db.add(StoredCalibrationCurve(
            id=local_id, kind=record.get('kind') or 'imported', name=name,
            description=record.get('description'), reference=record.get('reference'),
            doi=record.get('doi'), type=record.get('type') or 'terrestrial',
            sample_type=record.get('sample_type') or 'terrestrial',
            source_text=record.get('source_text'),
            source_filename=record.get('source_filename'),
            parse_options=record.get('parse_options'), recipe=recipe,
            content_sha256=record['content_sha256'], aliases=aliases, archived=False,
            created_by=owner_id,
        ))
        curve.local_id = local_id
        db.flush()


def _swap_ids(value: Any, mapping: Dict[str, str]) -> Any:
    """
    Every id in a stored run rewritten to this import's: as whole strings,
    as dict keys, and as the colon-joined pairs a duration's id is made of.
    The ids are UUIDs, so nothing else in a run can be mistaken for one.
    """
    if isinstance(value, str):
        if value in mapping:
            return mapping[value]
        if ':' in value:
            parts = value.split(':')
            if any(p in mapping for p in parts):
                return ':'.join(mapping.get(p, p) for p in parts)
        return value
    if isinstance(value, dict):
        return {_swap_ids(k, mapping): _swap_ids(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [_swap_ids(v, mapping) for v in value]
    return value


def _when(text: Optional[str]) -> datetime:
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None) if text else datetime.utcnow()
    except ValueError:
        return datetime.utcnow()


def _store_analysis(item: _Analysis, project: Project, plan: ImportPlan,
                    owner_id: str, db: Session) -> Analysis:
    request = item.request
    analysis_type = item.analysis_type
    if analysis_type == 'calibration' and len(request.measurements) > 1:
        analysis_type = 'group_calibration'
    analysis = Analysis(name=item.name, analysis_type=analysis_type, status='pending',
                        bayesian_config=item.config, project_id=project.id,
                        owner_id=owner_id)
    db.add(analysis)
    db.flush()

    element_map: Dict[str, str] = {}
    tree = copy.deepcopy(item.model_tree) if item.model_tree else None
    if tree is not None:
        for element in _iter_tree(tree):
            element_map[element['id']] = str(uuid.uuid4())

    samples_map: Dict[str, str] = {}
    for position, (measurement, curve_id, outliers) in enumerate(
            zip(request.measurements, item.curve_ids, item.outliers)):
        row = _sample_row(analysis.id, measurement,
                          element_id=element_map.get(measurement.element_id),
                          sort_order=position)
        row.curve_id = plan.curves[curve_id].local_id
        row.outlier_prior, row.outlier_model = outliers
        db.add(row)
        db.flush()
        samples_map[measurement.id] = row.id

    if tree is not None:
        _relabel_tree(tree, element_map, samples_map)
        _persist_tree(analysis.id, tree, db)

    if item.run is not None:
        db.flush()
        db.refresh(analysis)
        mapping = {**element_map, **samples_map}
        if item.source_id:
            mapping[item.source_id] = analysis.id
        source_project = (plan.manifest.get('project') or {}).get('id')
        if source_project:
            mapping[source_project] = project.id
        # The author's inputs, rebuilt here. Fingerprinted as this analysis's
        # own, which is what they now are: a run of them on this installation
        # is a re-run of the same inputs, and the report should say so.
        db.add(AnalysisRun(
            analysis_id=analysis.id,
            created_at=_when(item.run.get('created_at')),
            app_commit=item.run.get('app_commit'),
            inputs_hash=run_history._inputs_hash(analysis, db),
            payload=_swap_ids(item.run['payload'], mapping),
        ))
    return analysis


def commit_import(plan: ImportPlan, name: str, owner_id: str, db: Session) -> Project:
    precision = plan.manifest.get('coordinate_precision') or 'full'
    project = Project(
        name=name,
        description=(plan.manifest.get('project') or {}).get('description'),
        owner_id=owner_id,
        coordinate_precision=precision if precision in COORDINATE_PRECISIONS else 'full',
    )
    db.add(project)
    db.flush()
    _store_curves(plan, owner_id, db)
    for sample, curve_id in plan.samples:
        data = sample.model_dump()
        data['curve_id'] = plan.curves[curve_id].local_id
        data['sample_type'] = data.get('sample_type') or (
            'marine' if data['curve_id'] == 'marine20' else 'terrestrial')
        db.add(ProjectSample(project_id=project.id, **{
            k: v for k, v in data.items() if hasattr(ProjectSample, k)}))
    for item in plan.analyses:
        _store_analysis(item, project, plan, owner_id, db)
    return project


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

def _read_limited(content: bytes) -> None:
    if len(content) > MAX_PACKAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f'The file is {len(content) // (1024 * 1024)} MB; the limit is '
                   f'{MAX_PACKAGE_BYTES // (1024 * 1024)} MB.')


@router.post("/import/project/preview")
async def preview_project_import(file: UploadFile = File(...),
                                 user: User = Depends(current_user),
                                 db: Session = Depends(get_db)):
    """
    What a project package holds and what importing it would do, storing
    nothing. Answers 200 for a package with problems, as /import/dates does:
    the request worked, and its answer is `data.errors`.
    """
    content = await file.read()
    _read_limited(content)
    plan = await run_in_threadpool(plan_import, content, user.id, db)
    return {"success": True, "data": {**plan.summary(), 'filename': file.filename}}


@router.post("/import/project")
async def import_project(file: UploadFile = File(...),
                         name: Optional[str] = Form(None),
                         user: User = Depends(current_user),
                         db: Session = Depends(get_db)):
    """
    Import a project package as a new project. Takes the file again rather
    than the preview, for the reason POST /curves gives: the client is not a
    route by which data reaches the database. Refused whole, with the
    preview's errors, if anything in it is wrong.

    Every analysis arrives 'pending'. POST /projects/{id}/rerun runs them.
    """
    content = await file.read()
    _read_limited(content)
    return await run_in_threadpool(_import_project, content, name, user.id, db)


def _import_project(content: bytes, name: Optional[str], owner_id: str, db: Session):
    plan = plan_import(content, owner_id, db)
    if plan.errors:
        raise HTTPException(status_code=422, detail={
            'message': 'The package could not be imported.', 'errors': plan.errors})
    name = (name or '').strip() or plan.summary()['project_name']
    try:
        project = commit_import(plan, name[:200], owner_id, db)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f'The package could not be imported: {exc}')
    finally:
        curve_registry.invalidate()
    return {"success": True, "data": {
        'project_id': project.id,
        'name': project.name,
        'analyses': len(plan.analyses),
        'samples': len(plan.samples),
        'curves_added': sum(1 for c in plan.curves.values() if c.action == 'add'),
        'with_author_run': sum(1 for a in plan.analyses if a.run is not None),
    }}
