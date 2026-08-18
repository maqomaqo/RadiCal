"""
The one place that knows what calibration curves exist.

Before this module the answer was hardcoded in five places that had no way of
agreeing with each other: the loaders in `calibration.py`, the Pydantic
`Literal` in `schemas/sample.py`, the metadata list in `api/curves.py`, the
OxCal name map in `oxcal_script.py`, and two separate `<MenuItem>` lists in the
frontend. Adding a curve meant finding all five; forgetting one produced either
a curve you could not select, a selection the API rejected, or -- worst --
metadata describing a curve that is not the curve being calibrated against.
That last one had already happened: `api/curves.py` claimed IntCal20 and
SHCal20 held 5,141 points and Marine20 1,341, where the files actually loaded
hold 9,501, 9,501 and 5,501.

So nothing here is asserted about a curve that can be measured from it
instead. `CurveSpec` carries editorial metadata (name, citation, which sample
type the curve suits) written by hand because it cannot be derived, and
everything countable -- point count, calendar and radiocarbon range, grid
step structure, content hash -- computed from the curve that actually loaded.
A stale point count is now not a thing that can be written down.

Resolution is deliberately kept behind `resolve()` rather than exposed as a
dict of loaders, because the next step is curves that live in the database
rather than on disk. `resolve()` takes no session and never will: it opens its
own when it has to (see the module docstring in `api/curves.py` for the
lifecycle rules that make that safe). That keeps the fourteen existing
`get_curve()` call sites -- several inside solvers that have no business
holding a database session -- exactly as they are.
"""
from dataclasses import dataclass, asdict
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
import hashlib
import re
import time

import numpy as np

from sqlalchemy.exc import SQLAlchemyError

from app.database import SessionLocal
from app.models import ProjectSample, RadiocarbonSample, StoredCalibrationCurve
from app.services.calibration import (
    CalibrationCurve,
    load_intcal20,
    load_marine20,
    load_shcal20,
)


@dataclass(frozen=True)
class CurveSpec:
    """
    What is known about one calibration curve.

    The first block is editorial -- it describes the curve to a human and
    cannot be derived from its numbers. The second block is measured from the
    loaded curve every time, and is what `api/curves.py` serves.
    """

    id: str
    name: str
    # Display categorisation, unchanged from the vocabulary the API has always
    # used: terrestrial | marine | southern_hemisphere.
    type: str
    # Which kind of sample this curve is appropriate for. Distinct from `type`
    # because both IntCal20 and SHCal20 are terrestrial curves for
    # terrestrial samples but are not interchangeable, while `type` is what a
    # user is choosing between. The frontend derives its "selecting Marine
    # switches you to a marine curve" rule from this field rather than from
    # its own copy of the mapping.
    sample_type: str
    description: str
    reference: str
    year: int
    doi: Optional[str]
    # Where the curve came from. Only 'builtin' exists today; database-stored
    # imports and mixed curves land here next.
    kind: str
    # Other spellings this curve answers to when read out of a file -- an
    # OxCal `Curve("IntCal20.14c")`, or a CSV cell reading "IntCal". Matched
    # after `_normalise`, so case and punctuation are already handled.
    aliases: Tuple[str, ...]

    # --- measured from the loaded curve ---
    data_points: int
    cal_min: float
    cal_max: float
    c14_min: float
    c14_max: float
    # Human-readable summary of the calendar grid, e.g.
    # "1 yr (0-13,900), 5 yr (13,900-25,000), 10 yr (25,000-55,000)".
    # Calibration curves are not uniformly spaced and the spacing is the
    # first thing a curve's resolution depends on, so it is worth stating.
    resolution: str
    # sha256 over the normalised numeric rows -- comment and whitespace
    # changes do not move it, a changed number does. Lets a rerun notice that
    # the curve underneath a stored analysis is not the curve it was run
    # against, which is currently silent: the three builtin file paths are
    # all overridable by environment variable.
    content_sha256: str

    # Hidden from the pickers but still resolvable, so a rerun of an analysis
    # that used it still works. Builtin curves are never archived.
    archived: bool = False
    # Where an imported curve came from. None for builtins and mixed curves.
    source_filename: Optional[str] = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d['aliases'] = list(self.aliases)
        return d


@dataclass(frozen=True)
class _Builtin:
    """A curve shipped with the app, loaded from a file in calibration_data/."""

    id: str
    name: str
    type: str
    sample_type: str
    description: str
    reference: str
    year: int
    doi: Optional[str]
    aliases: Tuple[str, ...]


# Order matters twice over: it is the order the API lists curves in, and the
# first entry for a given sample_type is what the frontend switches to when
# that sample type is selected. IntCal20 leading the terrestrial pair
# reproduces the previous hardcoded default exactly.
_BUILTINS: Tuple[_Builtin, ...] = (
    _Builtin(
        id='intcal20',
        name='IntCal20',
        type='terrestrial',
        sample_type='terrestrial',
        description='Northern Hemisphere terrestrial radiocarbon age calibration',
        reference='Reimer et al. 2020',
        year=2020,
        doi='10.1017/RDC.2020.41',
        aliases=('intcal', 'intcal20'),
    ),
    _Builtin(
        id='shcal20',
        name='SHCal20',
        type='southern_hemisphere',
        sample_type='terrestrial',
        description='Southern Hemisphere terrestrial radiocarbon age calibration',
        reference='Hogg et al. 2020',
        year=2020,
        doi='10.1017/RDC.2020.59',
        aliases=('shcal', 'shcal20'),
    ),
    _Builtin(
        id='marine20',
        name='Marine20',
        type='marine',
        sample_type='marine',
        description='Marine radiocarbon age calibration',
        reference='Heaton et al. 2020',
        year=2020,
        doi='10.1017/RDC.2020.68',
        aliases=('marine', 'marine20'),
    ),
)

_BUILTIN_LOADERS = {
    'intcal20': load_intcal20,
    'shcal20': load_shcal20,
    'marine20': load_marine20,
}

# The ids `schemas/sample.py` validates against. Exported as a tuple so that
# module can keep using it as a Pydantic `Literal` -- one list, declared here,
# rather than a second copy maintained alongside this one.
BUILTIN_CURVE_IDS: Tuple[str, ...] = tuple(b.id for b in _BUILTINS)


def _normalise(name: str) -> str:
    """
    A curve name reduced to its comparable core: lowercased, punctuation and
    spacing dropped, and any file extension removed, so 'IntCal20.14c',
    'intcal-20' and 'IntCal 20' all match the same curve.
    """
    text = (name or '').strip().lstrip('=')
    text = text.rsplit('.', 1)[0] if text.lower().endswith(('.14c', '.csv', '.txt')) else text
    return re.sub(r'[^a-z0-9]+', '', text.lower())


def _step_summary(cal_ages: np.ndarray) -> str:
    """
    Describe the calendar grid as the runs of constant step it is actually
    made of, e.g. "1 yr (0-13,900), 5 yr (13,900-25,000)".

    Curves in this family change step several times across their range and
    the change points are informative (they are where the curve's own
    resolution changes). A curve with more runs than are worth listing --
    an imported one need not be regular at all -- is summarised by its range
    of steps instead of being printed at length.
    """
    if len(cal_ages) < 2:
        return 'n/a'

    steps = np.diff(cal_ages)
    runs: List[Tuple[float, float, float]] = []  # (step, from, to)
    start = 0
    for i in range(1, len(steps) + 1):
        if i < len(steps) and np.isclose(steps[i], steps[start]):
            continue
        runs.append((float(steps[start]), float(cal_ages[start]), float(cal_ages[i])))
        start = i

    if len(runs) > 5:
        lo, hi = float(steps.min()), float(steps.max())
        return f'{lo:g}-{hi:g} yr (irregular)'

    return ', '.join(
        f'{step:g} yr ({lo:,.0f}-{hi:,.0f})' for step, lo, hi in runs
    )


def content_hash(curve: CalibrationCurve) -> str:
    """
    Hash the curve's numbers, not its file. Two files differing only in their
    header comments describe the same curve and hash the same; a file whose
    numbers were corrected does not.
    """
    digest = hashlib.sha256()
    for cal, c14, err in zip(curve.cal_ages, curve.c14_ages, curve.c14_errors):
        digest.update(f'{cal:.6g},{c14:.6g},{err:.6g}\n'.encode())
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Stored curves
# --------------------------------------------------------------------------
#
# Curves the user imported live in the database, not on disk. Everything below
# resolves them without ever being handed a Session: it opens its own, briefly.
# That is what keeps the fourteen `get_curve()` call sites -- several inside
# solvers with no business holding a session -- exactly as they were.
#
# Two caches, with different rules, because the data and the metadata have
# different lifetimes. A stored curve's NUMBERS are immutable (see
# models.StoredCalibrationCurve), so caching the parsed curve by id can never
# go stale and needs no invalidation. Its editorial metadata is editable, so it
# is read from the row every time and never cached -- a rename is visible at
# once. The alias/existence index sits in between: it is cheap to rebuild but
# consulted per row of an imported CSV, so it is cached for a few seconds and
# invalidated outright whenever this process writes a curve.

_STORED_CURVES: Dict[str, CalibrationCurve] = {}

_INDEX_TTL_SECONDS = 5.0
_index_cache: Optional[Dict[str, str]] = None
_index_built_at: float = 0.0


def invalidate(curve_id: Optional[str] = None) -> None:
    """
    Forget what was cached about stored curves. Called by the endpoints that
    write one.

    With more than one worker process only the writing worker runs this, so
    another worker can briefly still list a just-deleted curve. Everything
    that matters is guarded against it anyway: resolution of a deleted curve
    falls through to the database and raises, and a curve in use cannot be
    deleted at all.
    """
    global _index_cache, _index_built_at
    _index_cache = None
    _index_built_at = 0.0
    if curve_id is None:
        _STORED_CURVES.clear()
        _measured.cache_clear()
    else:
        _STORED_CURVES.pop(curve_id, None)
        _measured.cache_clear()


_curve_table_warned = False


def _no_curve_table(exc: Exception) -> None:
    """
    Report an unreachable curve table once, and carry on with the builtin
    curves.

    Calibrating against IntCal20 does not need a database and must not start
    needing one: the curves ship with the app, and a script, a test importing
    this service on its own, or a database that has not been initialised yet
    should all still be able to calibrate. What is lost when this fires is
    only the user's imported curves, and a request naming one of those fails
    by name a moment later in resolve().
    """
    global _curve_table_warned
    if not _curve_table_warned:
        print(f"⚠ Stored calibration curves are unavailable ({exc}); "
              "continuing with the builtin curves only.")
        _curve_table_warned = True


def _stored_row(curve_id: str) -> Optional[StoredCalibrationCurve]:
    try:
        with SessionLocal() as db:
            return db.query(StoredCalibrationCurve).filter(
                StoredCalibrationCurve.id == curve_id
            ).one_or_none()
    except SQLAlchemyError as exc:
        _no_curve_table(exc)
        return None


def _stored_rows(include_archived: bool = False) -> List[StoredCalibrationCurve]:
    try:
        with SessionLocal() as db:
            query = db.query(StoredCalibrationCurve)
            if not include_archived:
                query = query.filter(StoredCalibrationCurve.archived.is_(False))
            return query.order_by(StoredCalibrationCurve.created_at).all()
    except SQLAlchemyError as exc:
        _no_curve_table(exc)
        return []


def _curve_from_row(row: StoredCalibrationCurve) -> CalibrationCurve:
    """
    Rebuild a stored curve from the file it was imported from, and refuse it
    if the numbers are not the numbers it was stored with.

    The hash check is the point. Storing the source text rather than a parsed
    array keeps the provenance (and the citation in its header comments), but
    it means the curve depends on the parser -- and a parser change years from
    now would otherwise move a curve silently underneath every analysis run
    against it. Here that becomes a loud failure instead.
    """
    from app.services.curve_import import parse_curve  # local: parser imports numpy only

    if row.kind == 'mixed':
        raise ValueError(
            f"Calibration curve '{row.id}' is a mixed curve, which this "
            "version cannot synthesise yet."
        )
    if not row.source_text:
        raise ValueError(f"Calibration curve '{row.id}' has no stored data.")

    options = row.parse_options or {}
    result = parse_curve(
        row.source_text,
        calendar=options.get('calendar', 'cal_bp'),
        columns=options.get('columns'),
    )
    if not result.ok:
        raise ValueError(
            f"Calibration curve '{row.id}' can no longer be read from its "
            f"stored file: {result.errors[0]}"
        )

    curve = CalibrationCurve(
        [p[0] for p in result.points],
        [p[1] for p in result.points],
        [p[2] for p in result.points],
    )
    curve.curve_name = row.name

    digest = content_hash(curve)
    if digest != row.content_sha256:
        raise ValueError(
            f"Calibration curve '{row.id}' does not match the data it was "
            "stored with -- it will not be used. Re-import the curve as a new "
            "one rather than trusting this copy."
        )
    return curve


def resolve(curve_id: str) -> CalibrationCurve:
    """
    Load a curve by id, builtin or stored.

    Raises ValueError for an id this app has no curve for, rather than
    substituting a default -- see `calibration.get_curve`, which delegates
    here and whose docstring explains at length why a silent fallback to
    IntCal20 is the worst available behaviour. An ARCHIVED curve still
    resolves: it is hidden from the pickers, not withdrawn, so that rerunning
    an analysis that used it still works.
    """
    loader = _BUILTIN_LOADERS.get(curve_id)
    if loader is not None:
        curve = loader()
        if curve is None or len(curve.cal_ages) == 0:
            raise ValueError(f"Calibration curve '{curve_id}' data is not available")
        return curve

    cached = _STORED_CURVES.get(curve_id)
    if cached is not None:
        return cached

    row = _stored_row(curve_id)
    if row is None:
        raise ValueError(
            f"Unknown calibration curve '{curve_id}'. "
            f"Supported curves: {', '.join(sorted(_BUILTIN_LOADERS))}"
        )

    curve = _curve_from_row(row)
    _STORED_CURVES[curve_id] = curve
    return curve


@lru_cache(maxsize=None)
def _measured(curve_id: str) -> Dict[str, Any]:
    """
    The countable half of a curve's description, measured from the curve
    itself. Cached separately from the editorial half, which is editable and
    so is read from the row on every request.
    """
    curve = resolve(curve_id)
    return {
        'data_points': len(curve.cal_ages),
        'cal_min': float(curve.cal_ages.min()),
        'cal_max': float(curve.cal_ages.max()),
        'c14_min': float(curve.c14_ages.min()),
        'c14_max': float(curve.c14_ages.max()),
        'resolution': _step_summary(curve.cal_ages),
        'content_sha256': content_hash(curve),
    }


def _spec_from_builtin(builtin: _Builtin) -> CurveSpec:
    return CurveSpec(
        id=builtin.id,
        name=builtin.name,
        type=builtin.type,
        sample_type=builtin.sample_type,
        description=builtin.description,
        reference=builtin.reference,
        year=builtin.year,
        doi=builtin.doi,
        kind='builtin',
        aliases=builtin.aliases,
        **_measured(builtin.id),
    )


def _spec_from_row(row: StoredCalibrationCurve) -> CurveSpec:
    return CurveSpec(
        id=row.id,
        name=row.name,
        type=row.type or 'terrestrial',
        sample_type=row.sample_type or 'terrestrial',
        description=row.description or '',
        reference=row.reference or '',
        year=row.created_at.year if row.created_at else 0,
        doi=row.doi,
        kind=row.kind,
        aliases=tuple(row.aliases or ()),
        archived=bool(row.archived),
        source_filename=row.source_filename,
        **_measured(row.id),
    )


def get_spec(curve_id: str) -> CurveSpec:
    """One curve's full description: editorial metadata as it stands now, and
    everything countable measured from the curve itself."""
    builtin = next((b for b in _BUILTINS if b.id == curve_id), None)
    if builtin is not None:
        return _spec_from_builtin(builtin)

    row = _stored_row(curve_id)
    if row is None:
        raise ValueError(
            f"Unknown calibration curve '{curve_id}'. "
            f"Supported curves: {', '.join(curve_ids())}"
        )
    return _spec_from_row(row)


def list_specs(include_archived: bool = False) -> List[CurveSpec]:
    """
    Every curve this app can calibrate against, builtins first and imports in
    the order they were added. A curve that cannot be loaded -- a missing
    file, a stored curve whose data no longer matches its hash -- is left out
    rather than listed as something the user can select and then fail on.
    """
    specs = []
    for builtin in _BUILTINS:
        try:
            specs.append(_spec_from_builtin(builtin))
        except ValueError:
            continue
    for row in _stored_rows(include_archived=include_archived):
        try:
            specs.append(_spec_from_row(row))
        except ValueError:
            continue
    return specs


def curve_ids(include_archived: bool = False) -> Tuple[str, ...]:
    """The ids currently resolvable, in listing order."""
    return tuple(spec.id for spec in list_specs(include_archived=include_archived))


def known_ids() -> Tuple[str, ...]:
    """
    Every curve id that resolves, archived ones included, WITHOUT loading any
    of them. Used where only the names are wanted -- an error message listing
    what is available should not parse three curve files to write itself.
    """
    try:
        with SessionLocal() as db:
            stored = [row[0] for row in db.query(StoredCalibrationCurve.id).all()]
    except SQLAlchemyError as exc:
        _no_curve_table(exc)
        stored = []
    return BUILTIN_CURVE_IDS + tuple(stored)


def exists(curve_id: str) -> bool:
    """
    Whether a curve id can be calibrated against. Used by the request schemas,
    which is why builtins are answered without touching the database: a
    payload of forty dates all on IntCal20 should cost no queries at all.
    """
    if curve_id in _BUILTIN_LOADERS:
        return True
    return curve_id in _stored_index().values()


def _stored_index() -> Dict[str, str]:
    """
    Name -> id for every stored curve, including archived ones and their
    aliases. Cached briefly rather than per-request: it is consulted once per
    row of an imported CSV, and rebuilding it per row would turn a 500-row
    import into 500 queries.
    """
    global _index_cache, _index_built_at
    now = time.monotonic()
    if _index_cache is not None and (now - _index_built_at) < _INDEX_TTL_SECONDS:
        return _index_cache

    index: Dict[str, str] = {}
    for row in _stored_rows(include_archived=True):
        index[_normalise(row.id)] = row.id
        index[_normalise(row.name)] = row.id
        for alias in (row.aliases or ()):
            index[_normalise(alias)] = row.id
    _index_cache = index
    _index_built_at = now
    return index


@lru_cache(maxsize=None)
def _builtin_index() -> Dict[str, str]:
    index: Dict[str, str] = {}
    for builtin in _BUILTINS:
        index[_normalise(builtin.id)] = builtin.id
        index[_normalise(builtin.name)] = builtin.id
        for alias in builtin.aliases:
            index[_normalise(alias)] = builtin.id
    return index


def resolve_alias(name: str) -> Optional[str]:
    """
    A curve name as written in a file -- an OxCal `Curve("=SHCal20")`, a CSV
    cell reading 'IntCal', a filename 'intcal20.14c' -- mapped to the id this
    app loads it under, or None if it names no curve we have.

    Returning None rather than a default is the point: `date_import` and
    `oxcal_script` both turn that None into an error naming the curve that
    was asked for, because a file calibrated against a curve we do not have
    is not a file we can honour by quietly using a different one. Importing
    that curve is what makes such a file readable -- which is why stored
    curves answer to their aliases here too.
    """
    key = _normalise(name)
    if not key:
        return None
    builtin = _builtin_index().get(key)
    if builtin is not None:
        return builtin
    return _stored_index().get(key)


def usage(curve_id: str) -> Dict[str, Any]:
    """
    What is already calibrated against this curve.

    Deleting a curve out from under a stored analysis would leave a saved
    result that can be displayed but never reproduced, so this is what
    api/curves.py checks before allowing it -- and what it reports back
    instead of a bare refusal, since "used by 3 analyses" is actionable and
    "409" is not.
    """
    with SessionLocal() as db:
        analysis_ids = [
            row[0] for row in db.query(RadiocarbonSample.analysis_id)
            .filter(RadiocarbonSample.curve_id == curve_id)
            .distinct().all()
        ]
        project_ids = [
            row[0] for row in db.query(ProjectSample.project_id)
            .filter(ProjectSample.curve_id == curve_id)
            .distinct().all()
        ]
        # A mixed curve holds its parents by id, so an ingredient counts as
        # in use for as long as the mix exists.
        mixed = [
            row.id for row in db.query(StoredCalibrationCurve)
            .filter(StoredCalibrationCurve.kind == 'mixed').all()
            if curve_id in ((row.recipe or {}).get('a'), (row.recipe or {}).get('b'))
        ]

    return {
        'analysis_ids': analysis_ids,
        'project_ids': project_ids,
        'mixed_curve_ids': mixed,
        'in_use': bool(analysis_ids or project_ids or mixed),
    }
