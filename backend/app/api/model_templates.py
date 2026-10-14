"""
Model templates: a model's structure and settings, saved without its dates so
it can be applied to another analysis's dates (models.ModelTemplate).

A template is the tree of Sequences, Phases, Boundaries, Hiatuses and their
limits, plus the model settings it was run under. Nothing in it names a date:
a wiggle match's reference date is dropped, and applying a template places
dates by the names of the phases they sit in (the model editor does that,
frontend utils/modelTemplates). Templates belong to the account that saved
them, and travel between accounts and installations as a JSON file, which
holds any number of them.
"""
import copy
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from app.api.auth import current_user
from app.database import get_db
from app.models import ModelTemplate, User
from app.schemas.sample import BayesianConfigInput
from app.services import model_tree as model_tree_service

router = APIRouter()

FILE_FORMAT = 'radical-model-templates'
FILE_VERSION = 1
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TEMPLATES_PER_FILE = 500
MAX_ELEMENTS = 2000
MAX_DEPTH = 50

# What an element keeps in a template. Everything else -- a posterior, a
# run's diagnostics -- is a result, not structure.
_ELEMENT_KEYS = ('element_type', 'name', 'tpq_age_bp', 'taq_age_bp', 'params')
# Params that name a date, which a template has none of.
_DATE_PARAMS = ('reference_sample_id',)


class TemplateInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    model_tree: Dict[str, Any]
    bayesian_config: Optional[Dict[str, Any]] = None

    @field_validator('name')
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('a template needs a name')
        return value

    @field_validator('description')
    @classmethod
    def _description(cls, value: Optional[str]) -> Optional[str]:
        return (value or '').strip() or None


class TemplateUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)


# --------------------------------------------------------------------------
# Cleaning and checking
# --------------------------------------------------------------------------

def clean_tree(root: Dict) -> Dict:
    """
    The structure alone, with fresh ids: every element's type, name, limits
    and settings, nothing that names a date, and no result. Raises ValueError
    for a tree too large or deep to be a model.
    """
    count = 0

    def visit(element: Any, depth: int) -> Dict:
        nonlocal count
        if not isinstance(element, dict):
            raise ValueError('every model element must be an object')
        count += 1
        if count > MAX_ELEMENTS:
            raise ValueError(f'a template holds at most {MAX_ELEMENTS} elements')
        if depth > MAX_DEPTH:
            raise ValueError(f'a template nests at most {MAX_DEPTH} levels deep')
        out = {'id': str(uuid.uuid4())}
        for key in _ELEMENT_KEYS:
            if element.get(key) is not None:
                out[key] = copy.deepcopy(element[key])
        params = out.get('params')
        if isinstance(params, dict):
            params = {k: v for k, v in params.items() if k not in _DATE_PARAMS}
            out['params'] = params or None
            if out['params'] is None:
                del out['params']
        elif 'params' in out:
            raise ValueError('element params must be an object')
        children = element.get('children') or []
        if not isinstance(children, list):
            raise ValueError('element children must be a list')
        out['children'] = [visit(child, depth + 1) for child in children]
        return out

    return visit(root, 0)


def check_tree(root: Dict) -> None:
    """
    The rules a model's structure must follow (model_tree.validate_tree),
    for a structure that has no dates yet. A wiggle match and a deposition
    sequence must hold dates to be valid, so each is checked as if it held
    two; everything else is checked as it stands. Raises ValueError.
    """
    stand_ins = []
    for element in model_tree_service.iter_elements(root):
        if element.get('element_type') in ('wiggle_sequence', 'deposition_sequence'):
            stand_ins += [{'id': f'{element["id"]}-{n}', 'element_id': element['id']}
                          for n in range(2)]
    model_tree_service.validate_tree(root, stand_ins)


def clean_config(config: Optional[Dict]) -> Optional[Dict]:
    """
    The model settings, checked as an analysis's are, less the dispersal
    origin: a place belongs to a site, not to a way of modelling one. None
    stays None -- a template saved without settings applies none -- rather
    than becoming the defaults, which would overwrite the settings of every
    analysis it is applied to.
    """
    if config is None:
        return None
    validated = BayesianConfigInput.model_validate(config)
    return validated.model_dump(exclude={'dispersal_origin'})


def _summary(tree: Dict) -> Dict[str, int]:
    counts = {'phases': 0, 'boundaries': 0, 'elements': 0}
    for element in model_tree_service.iter_elements(tree):
        counts['elements'] += 1
        etype = element.get('element_type')
        if etype in model_tree_service.DATE_HOLDERS:
            counts['phases'] += 1
        elif etype == 'boundary':
            counts['boundaries'] += 1
    return counts


def _serialize(template: ModelTemplate) -> Dict:
    return {
        'id': template.id,
        'name': template.name,
        'description': template.description,
        'model_tree': template.model_tree,
        'bayesian_config': template.bayesian_config,
        'created_at': template.created_at.isoformat() if template.created_at else None,
        'updated_at': template.updated_at.isoformat() if template.updated_at else None,
        **_summary(template.model_tree),
    }


def _prepare(data: Dict) -> Tuple[str, Optional[str], Dict, Optional[Dict]]:
    """A template as sent or as read from a file: checked, cleaned, ready to
    store. Raises ValueError or ValidationError."""
    template = TemplateInput.model_validate(data)
    tree = clean_tree(template.model_tree)
    check_tree(tree)
    return template.name, template.description, tree, clean_config(template.bayesian_config)


def _owned(template_id: str, db: Session, user: User) -> ModelTemplate:
    template = db.query(ModelTemplate).filter(
        ModelTemplate.id == template_id, ModelTemplate.owner_id == user.id).first()
    if template is None:
        raise HTTPException(status_code=404, detail='That template was not found.')
    return template


def _messages(exc: Exception) -> List[str]:
    if isinstance(exc, ValidationError):
        out = []
        for error in exc.errors():
            where = '.'.join(str(p) for p in error.get('loc') or ())
            message = error.get('msg', 'invalid').removeprefix('Value error, ')
            out.append(f'{where}: {message}' if where else message)
        return out
    return [str(exc)]


# --------------------------------------------------------------------------
# Library
# --------------------------------------------------------------------------

@router.get('/model-templates')
def list_templates(db: Session = Depends(get_db), user: User = Depends(current_user)):
    templates = (db.query(ModelTemplate).filter(ModelTemplate.owner_id == user.id)
                 .order_by(ModelTemplate.name, ModelTemplate.created_at).all())
    return {'success': True, 'data': {'items': [_serialize(t) for t in templates]}}


@router.post('/model-templates')
def create_template(request: TemplateInput, db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    """
    Save a model's structure and settings as a template. Whatever names a
    date or records a result is dropped, and the tree gets ids of its own.
    """
    try:
        name, description, tree, config = _prepare(request.model_dump())
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail='; '.join(_messages(exc)))
    template = ModelTemplate(owner_id=user.id, name=name, description=description,
                             model_tree=tree, bayesian_config=config)
    db.add(template)
    db.commit()
    db.refresh(template)
    return {'success': True, 'data': _serialize(template)}


@router.patch('/model-templates/{template_id}')
def update_template(template_id: str, request: TemplateUpdate,
                    db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Rename a template or change its description. Its structure does not
    change: save the model again as a new template for that."""
    template = _owned(template_id, db, user)
    if request.name is not None:
        if not request.name.strip():
            raise HTTPException(status_code=422, detail='A template needs a name.')
        template.name = request.name.strip()
    if 'description' in request.model_fields_set:
        template.description = (request.description or '').strip() or None
    db.commit()
    db.refresh(template)
    return {'success': True, 'data': _serialize(template)}


@router.delete('/model-templates/{template_id}')
def delete_template(template_id: str, db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    """Delete a template. Analyses built from it keep their own copy."""
    db.delete(_owned(template_id, db, user))
    db.commit()
    return {'success': True, 'data': {'id': template_id}}


@router.get('/model-templates/export')
def export_templates(template: Optional[List[str]] = Query(default=None),
                     db: Session = Depends(get_db), user: User = Depends(current_user)):
    """
    Templates as one JSON file, which POST /import/model-templates reads
    back in here or on another installation. `template` (repeatable) chooses
    which; without it, all of them.
    """
    query = db.query(ModelTemplate).filter(ModelTemplate.owner_id == user.id)
    if template:
        query = query.filter(ModelTemplate.id.in_(template))
    templates = query.order_by(ModelTemplate.name, ModelTemplate.created_at).all()
    if template and len(templates) != len(set(template)):
        raise HTTPException(status_code=404, detail='Some of the chosen templates were not found.')
    content = json.dumps({
        'format': FILE_FORMAT,
        'format_version': FILE_VERSION,
        'exported_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'templates': [{'name': t.name, 'description': t.description,
                       'model_tree': t.model_tree, 'bayesian_config': t.bayesian_config}
                      for t in templates],
    }, indent=1)
    filename = 'model-templates.json'
    return Response(content=content, media_type='application/json',
                    headers={'Content-Disposition': f'attachment; filename={filename}'})


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------

def _shape(tree: Dict) -> Any:
    """A tree with its ids taken out, for telling two templates apart."""
    return {**{k: v for k, v in tree.items() if k not in ('id', 'children')},
            'children': [_shape(c) for c in tree.get('children') or []]}


def plan_template_import(content: bytes, owner_id: str, db: Session) -> Dict:
    """
    What a templates file holds and what importing it would do, storing
    nothing. A template identical to one already saved under its name is
    skipped; one whose name is taken by a different template is added under
    a new name.
    """
    errors: List[str] = []
    plan: List[Dict] = []
    try:
        data = json.loads(content.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {'ok': False, 'errors': ['This is not a model templates file. Use a .json '
                                        'file saved with Export in the templates list.'],
                'templates': []}
    if not isinstance(data, dict) or data.get('format') != FILE_FORMAT:
        return {'ok': False, 'errors': ['This is not a RadiCal model templates file.'],
                'templates': []}
    version = data.get('format_version')
    if not isinstance(version, int) or version > FILE_VERSION:
        return {'ok': False, 'errors': ['This file was made by a newer version of RadiCal. '
                                        'Update this installation to import it.'],
                'templates': []}
    entries = data.get('templates')
    if not isinstance(entries, list) or not entries:
        return {'ok': False, 'errors': ['The file holds no templates.'], 'templates': []}
    if len(entries) > MAX_TEMPLATES_PER_FILE:
        return {'ok': False, 'errors': [f'A file holds at most {MAX_TEMPLATES_PER_FILE} '
                                        'templates.'], 'templates': []}

    existing = db.query(ModelTemplate).filter(ModelTemplate.owner_id == owner_id).all()
    taken = {t.name.lower() for t in existing}
    same = {(t.name.lower(), json.dumps(_shape(t.model_tree), sort_keys=True),
             json.dumps(t.bayesian_config, sort_keys=True)) for t in existing}
    for index, entry in enumerate(entries, start=1):
        label = (entry.get('name') if isinstance(entry, dict) else None) or f'template {index}'
        try:
            if not isinstance(entry, dict):
                raise ValueError('not a template')
            name, description, tree, config = _prepare(entry)
        except (ValueError, ValidationError) as exc:
            errors.extend(f"'{label}': {m}" for m in _messages(exc))
            continue
        key = (name.lower(), json.dumps(_shape(tree), sort_keys=True),
               json.dumps(config, sort_keys=True))
        if key in same:
            plan.append({'name': name, 'action': 'same', 'saved_as': name, **_summary(tree)})
            continue
        saved_as = name
        n = 2
        while saved_as.lower() in taken:
            saved_as = f'{name} ({n})'
            n += 1
        taken.add(saved_as.lower())
        same.add(key)
        plan.append({'name': name, 'action': 'add' if saved_as == name else 'rename',
                     'saved_as': saved_as, 'description': description,
                     'model_tree': tree, 'bayesian_config': config, **_summary(tree)})
    return {'ok': not errors, 'errors': errors, 'templates': plan}


def _public(plan: Dict) -> Dict:
    return {**plan, 'templates': [{k: v for k, v in t.items()
                                   if k not in ('model_tree', 'bayesian_config', 'description')}
                                  for t in plan['templates']]}


def _read_limited(content: bytes) -> None:
    if not content:
        raise HTTPException(status_code=400, detail='The uploaded file is empty.')
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail=(
            f'The file is {len(content) // 1024} KB; the limit is {MAX_FILE_BYTES // 1024} KB.'))


@router.post('/import/model-templates/preview')
async def preview_template_import(file: UploadFile = File(...),
                                  db: Session = Depends(get_db),
                                  user: User = Depends(current_user)):
    """What a templates file holds and what importing it would do. Answers
    200 for a file with problems; its answer is `data.errors`."""
    content = await file.read()
    _read_limited(content)
    plan = await run_in_threadpool(plan_template_import, content, user.id, db)
    return {'success': True, 'data': _public(plan)}


@router.post('/import/model-templates')
async def import_templates(file: UploadFile = File(...), db: Session = Depends(get_db),
                           user: User = Depends(current_user)):
    """Add a templates file's templates to this account, all or none."""
    content = await file.read()
    _read_limited(content)
    return await run_in_threadpool(_import_templates, content, user.id, db)


def _import_templates(content: bytes, owner_id: str, db: Session) -> Dict:
    plan = plan_template_import(content, owner_id, db)
    if not plan['ok']:
        raise HTTPException(status_code=422, detail={
            'message': 'The templates could not be imported.', 'errors': plan['errors']})
    added = 0
    for item in plan['templates']:
        if item['action'] == 'same':
            continue
        db.add(ModelTemplate(owner_id=owner_id, name=item['saved_as'],
                             description=item.get('description'),
                             model_tree=item['model_tree'],
                             bayesian_config=item['bayesian_config']))
        added += 1
    db.commit()
    return {'success': True, 'data': {
        'added': added, 'skipped': len(plan['templates']) - added}}
