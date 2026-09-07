"""
The one place that knows what calibration curves exist.

Nothing is ASSERTED here about a curve that can be measured from it instead.
`CurveSpec` carries editorial metadata -- name, citation, which sample type the
curve suits -- written by hand because it cannot be derived, and everything
countable is computed from the curve that actually loaded. A stale point count
is not something that can be written down.

Resolution sits behind `resolve()` rather than a dict of loaders, because a
curve may live in the database rather than on disk. `resolve()` takes no
session and never will: it opens its own when it has to (see the module
docstring in `api/curves.py` for the lifecycle rules that make that safe), so
the `get_curve()` call sites -- several inside solvers with no business holding
a session -- stay as they are.

Ownership
---------
A stored curve belongs to the account that imported it and is invisible to
every other. The three builtins belong to nobody and are visible to all.

That splits this module's functions cleanly in two, and the split is the thing
to keep straight when adding to it:

  **Choosing and listing** -- `list_specs`, `curve_ids`, `known_ids`,
  `exists`, `resolve_alias`, `find_mixed`. These answer "what may this user
  pick", so every one of them REQUIRES an `owner_id` and filters on it. The
  argument is required rather than defaulted precisely because a default is
  how one of these quietly starts returning everyone's curves again.

  **Loading** -- `resolve`, `get_spec`, `usage`, `content_hash`. These answer
  "what is curve X", take an id that has already been authorised, and do not
  ask who wants it. A solver re-running a stored analysis has no user in
  scope and must still load the curve that analysis was computed against.

The security boundary is therefore api/, not here: a route checks
`visible_to()` before it resolves. Putting it the other way round -- making
`resolve` scoped -- would mean threading a user id through both solvers, the
PDF renderer and the exporters to reach code whose only job is to read a file.

`SCOPE_ANY` exists for the handful of internal callers that genuinely mean
every curve regardless of owner (`usage`, and the alias index the importer
builds). It is spelled out at each call site so that reading one is enough to
tell whether the scope was chosen or forgotten.
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
from app.models import Analysis, Project, ProjectSample, RadiocarbonSample, StoredCalibrationCurve
from app.services.calibration import (
    CalibrationCurve,
    load_intcal20,
    load_marine20,
    load_shcal20,
)


# Passed as `owner_id` by a caller that genuinely means "every curve, whoever
# imported it". Spelled out rather than defaulted: see the module docstring.
SCOPE_ANY = '__any_owner__'


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
    # Display categorisation: terrestrial | marine | southern_hemisphere.
    type: str
    # Which kind of sample this curve suits. Distinct from `type`: IntCal20
    # and SHCal20 are both terrestrial but not interchangeable, and `type` is
    # what the user chooses between. The frontend's "selecting Marine switches
    # you to a marine curve" rule reads this rather than its own mapping.
    sample_type: str
    description: str
    reference: str
    year: int
    doi: Optional[str]
    # 'builtin', 'imported' (a file) or 'mixed' (a recipe over two curves).
    kind: str
    # Other spellings this curve answers to when read out of a file -- an
    # OxCal `Curve("IntCal20.14c")`, a CSV cell reading "IntCal". Matched after
    # `_normalise`, so case and punctuation are already handled.
    aliases: Tuple[str, ...]

    # --- measured from the loaded curve ---
    data_points: int
    cal_min: float
    cal_max: float
    c14_min: float
    c14_max: float
    # Summary of the calendar grid, e.g. "1 yr (0-13,900), 5 yr
    # (13,900-25,000)". Curves are not uniformly spaced, and the spacing is
    # what a curve's resolution depends on first.
    resolution: str
    # sha256 over the normalised numeric rows: comment and whitespace changes
    # do not move it, a changed number does. Lets a rerun notice that the curve
    # under a stored analysis is not the one it was run against -- the builtin
    # file paths are all overridable by environment variable.
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


# Order matters twice: it is the order the API lists curves in, and the first
# entry for a given sample_type is what the frontend switches to when that
# sample type is selected.
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

# The builtin ids, exported as one list declared here rather than a second
# copy maintained alongside this one. See schemas.sample.VALID_CURVE_IDS.
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

    Curves in this family change step several times across their range, and
    the change points are where the curve's own resolution changes. A curve
    with too many runs to list -- an imported one need not be regular at all --
    is summarised by its range of steps instead.
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
# Imported curves live in the database, not on disk. Everything below resolves
# them without ever being handed a Session: it opens its own, briefly.
#
# Two caches, with different rules, because the data and the metadata have
# different lifetimes. A stored curve's NUMBERS are immutable (see
# models.StoredCalibrationCurve), so caching the parsed curve by id can never
# go stale. Its editorial metadata is editable, so it is read from the row
# every time and never cached. The alias/existence index sits in between: cheap
# to rebuild but consulted per row of an imported CSV, so it is cached for a
# few seconds and invalidated whenever this process writes a curve.

_STORED_CURVES: Dict[str, CalibrationCurve] = {}

_INDEX_TTL_SECONDS = 5.0
# owner_id -> (name -> curve id). Keyed by owner: see _stored_index.
_index_cache: Optional[Dict[str, Dict[str, str]]] = None
_index_built_at: float = 0.0


def invalidate(curve_id: Optional[str] = None) -> None:
    """
    Forget what was cached about stored curves. Called by the endpoints that
    write one.

    With several worker processes only the writing one runs this, so another
    can briefly still list a just-deleted curve. That is harmless: resolving a
    deleted curve falls through to the database and raises, and a curve in use
    cannot be deleted at all.
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
    Report an unreachable curve table once and carry on with the builtins.

    Calibrating against IntCal20 does not need a database and must not start
    needing one: a script, a test importing this service alone, or an
    uninitialised database should all still calibrate. Only imported curves are
    lost, and a request naming one fails by name in resolve().
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


def _stored_rows(owner_id: str,
                 include_archived: bool = False) -> List[StoredCalibrationCurve]:
    """
    The stored curves `owner_id` may see, oldest first.

    `owner_id` is positional and required so that every caller has to say
    whose curves it wants; SCOPE_ANY is the way to say "everyone's".
    """
    try:
        with SessionLocal() as db:
            query = db.query(StoredCalibrationCurve)
            if owner_id != SCOPE_ANY:
                query = query.filter(StoredCalibrationCurve.created_by == owner_id)
            if not include_archived:
                query = query.filter(StoredCalibrationCurve.archived.is_(False))
            return query.order_by(StoredCalibrationCurve.created_at).all()
    except SQLAlchemyError as exc:
        _no_curve_table(exc)
        return []


def _curve_from_row(row: StoredCalibrationCurve) -> CalibrationCurve:
    """
    Rebuild a stored curve from the file it was imported from, refusing it if
    the numbers are not the ones it was stored with.

    The hash check is the point. Storing the source text rather than a parsed
    array keeps the provenance and the citation in its header, but makes the
    curve depend on the parser -- so a later parser change would otherwise move
    a curve silently underneath every analysis run against it.
    """
    from app.services.curve_import import parse_curve  # local: parser imports numpy only

    if row.kind == 'mixed':
        return _mixed_from_row(row)
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


# A mix of a mix is allowed, as in OxCal, but bounded so a hand-edited row
# pointing a curve at itself fails readably rather than as a recursion error.
_MAX_MIX_DEPTH = 4


def _mixed_from_row(row: StoredCalibrationCurve, depth: int = 0) -> CalibrationCurve:
    """
    Synthesise a mixed curve from its recipe.

    Its parents resolve through the ordinary path, so a mix of an imported
    curve works and an archived ingredient still resolves -- archiving hides a
    curve from the pickers rather than withdrawing it.
    """
    from app.services.curve_mix import mix

    if depth > _MAX_MIX_DEPTH:
        raise ValueError(
            f"Calibration curve '{row.id}' mixes curves more than "
            f"{_MAX_MIX_DEPTH} deep; it probably refers back to itself."
        )

    recipe = row.recipe or {}
    a_id, b_id = recipe.get('a'), recipe.get('b')
    if not a_id or not b_id:
        raise ValueError(f"Calibration curve '{row.id}' has no usable recipe.")
    if a_id == row.id or b_id == row.id:
        raise ValueError(f"Calibration curve '{row.id}' refers to itself.")

    try:
        curve = mix(
            resolve(a_id),
            resolve(b_id),
            float(recipe.get('percent_a', 50.0)),
            float(recipe.get('percent_a_error', 0.0)),
            name=row.name,
        )
    except ValueError as exc:
        raise ValueError(f"Calibration curve '{row.id}' cannot be built: {exc}")

    digest = content_hash(curve)
    if row.content_sha256 and digest != row.content_sha256:
        raise ValueError(
            f"Calibration curve '{row.id}' no longer matches the data it was "
            f"stored with -- one of the curves it mixes ({a_id}, {b_id}) has "
            "changed underneath it. Build the mixture again as a new curve "
            "rather than trusting this one."
        )
    return curve


def resolve(curve_id: str) -> CalibrationCurve:
    """
    Load a curve by id, builtin or stored.

    Raises ValueError for an id this app has no curve for rather than
    substituting a default; see `calibration.get_curve`. An ARCHIVED curve
    still resolves, being hidden from the pickers rather than withdrawn, so a
    rerun of an analysis that used it still works.
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
    itself. Cached separately from the editorial half, which is editable and so
    read from the row on every request.
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


def list_specs(owner_id: str, include_archived: bool = False) -> List[CurveSpec]:
    """
    Every curve `owner_id` may calibrate against: the three builtins, then
    their own imports in the order they added them. One that cannot be loaded
    -- a missing file, a stored curve no longer matching its hash -- is left
    out rather than offered for the user to select and then fail on.

    Another account's imports are not here, which is the point: a picker built
    from this must not offer a curve belonging to someone else, and a curve
    somebody is still developing must not read as a supported option.
    """
    specs = []
    for builtin in _BUILTINS:
        try:
            specs.append(_spec_from_builtin(builtin))
        except ValueError:
            continue
    for row in _stored_rows(owner_id, include_archived=include_archived):
        try:
            specs.append(_spec_from_row(row))
        except ValueError:
            continue
    return specs


def curve_ids(owner_id: str, include_archived: bool = False) -> Tuple[str, ...]:
    """The ids `owner_id` may resolve, in listing order."""
    return tuple(
        spec.id for spec in list_specs(owner_id, include_archived=include_archived)
    )


def known_ids(owner_id: str) -> Tuple[str, ...]:
    """
    Every curve id `owner_id` may resolve, archived included, WITHOUT loading
    any of them: an error message listing what is available should not parse
    three curve files to write itself.
    """
    try:
        with SessionLocal() as db:
            query = db.query(StoredCalibrationCurve.id)
            if owner_id != SCOPE_ANY:
                query = query.filter(StoredCalibrationCurve.created_by == owner_id)
            stored = [row[0] for row in query.all()]
    except SQLAlchemyError as exc:
        _no_curve_table(exc)
        stored = []
    return BUILTIN_CURVE_IDS + tuple(stored)


def exists(curve_id: str, owner_id: str) -> bool:
    """
    Whether `owner_id` may calibrate against `curve_id`. Builtins are answered
    without touching the database, so a payload of forty dates all on IntCal20
    costs no queries.
    """
    if curve_id in _BUILTIN_LOADERS:
        return True
    return curve_id in _stored_index(owner_id).values()


def visible_to(curve_id: str, owner_id: str) -> bool:
    """
    Whether `owner_id` is allowed to name this curve at all.

    The authorisation predicate the API checks before it resolves anything --
    see the Ownership section of the module docstring. Distinct from `exists`
    only in intent, and named separately because a route reading
    `if not visible_to(...)` says what it is enforcing, where `if not
    exists(...)` reads as a typo check.
    """
    return exists(curve_id, owner_id)


def _stored_index(owner_id: str) -> Dict[str, str]:
    """
    Name -> id for the stored curves `owner_id` may see, archived ones and
    aliases included.

    Cached briefly because it is consulted once per row of an imported CSV, and
    rebuilding it per row would turn a 500-row import into 500 queries. Keyed
    BY OWNER: one shared cache would hand whichever user warmed it second the
    curves of whoever warmed it first, which is the whole bug this scoping
    exists to prevent.
    """
    global _index_cache, _index_built_at
    now = time.monotonic()

    # The whole cache ages out together rather than per owner: a dictionary of
    # timestamps is more bookkeeping than a value that lives a few seconds is
    # worth.
    if _index_cache is None or (now - _index_built_at) >= _INDEX_TTL_SECONDS:
        _index_cache = {}
        _index_built_at = now
    elif owner_id in _index_cache:
        return _index_cache[owner_id]

    index: Dict[str, str] = {}
    for row in _stored_rows(owner_id, include_archived=True):
        index[_normalise(row.id)] = row.id
        index[_normalise(row.name)] = row.id
        for alias in (row.aliases or ()):
            index[_normalise(alias)] = row.id
    _index_cache[owner_id] = index
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


def resolve_alias(name: str, owner_id: str) -> Optional[str]:
    """
    A curve name as written in a file -- an OxCal `Curve("=SHCal20")`, a CSV
    cell reading 'IntCal', a filename 'intcal20.14c' -- mapped to the id this
    app loads it under, or None if it names no curve we have.

    None rather than a default: `date_import` and `oxcal_script` both turn it
    into an error naming the curve that was asked for, because a file
    calibrated against a curve we do not have cannot be honoured by quietly
    using a different one. Importing that curve is what makes the file
    readable, which is why stored curves answer to their aliases here too.
    """
    key = _normalise(name)
    if not key:
        return None
    builtin = _builtin_index().get(key)
    if builtin is not None:
        return builtin
    return _stored_index(owner_id).get(key)


# How close a stored mixture must be to a requested one to be the same.
# Percentages in .oxcal files are written to whole numbers, so anything within
# a tenth is the same recipe written differently.
MIX_MATCH_TOLERANCE = 0.1


def find_mixed(a_id: str, b_id: str, percent_a: float, percent_a_error: float,
               owner_id: str) -> Optional[str]:
    """
    A mixed curve of `owner_id`'s built from this recipe, or None.

    Both orderings are checked with the percentage flipped: 30% IntCal20 with
    Marine20 IS 70% Marine20 with IntCal20.

    Used twice, which is why it lives here rather than in either caller: the
    OxCal importer matches a file's Mix_Curves against what this app has, and
    the create endpoint refuses a duplicate -- two curves with one recipe would
    be the same numbers under two names, making that matching arbitrary.

    Scoped, and "duplicate" is therefore per user: two accounts may each hold
    their own 30/70 mixture. They are the same numbers, but a curve one of them
    cannot see is not a duplicate they could have reused -- and refusing the
    second would report the existence of the first.
    """
    try:
        with SessionLocal() as db:
            query = db.query(StoredCalibrationCurve).filter(
                StoredCalibrationCurve.kind == 'mixed'
            )
            if owner_id != SCOPE_ANY:
                query = query.filter(StoredCalibrationCurve.created_by == owner_id)
            rows = query.order_by(StoredCalibrationCurve.created_at).all()
            recipes = [(row.id, dict(row.recipe or {})) for row in rows]
    except SQLAlchemyError as exc:
        _no_curve_table(exc)
        return None

    for curve_id, recipe in recipes:
        stored_error = float(recipe.get('percent_a_error', 0.0) or 0.0)
        if abs(stored_error - percent_a_error) > MIX_MATCH_TOLERANCE:
            continue
        stored_percent = float(recipe.get('percent_a', 0.0) or 0.0)
        forward = (recipe.get('a') == a_id and recipe.get('b') == b_id
                   and abs(stored_percent - percent_a) <= MIX_MATCH_TOLERANCE)
        reversed_ = (recipe.get('a') == b_id and recipe.get('b') == a_id
                     and abs(stored_percent - (100.0 - percent_a)) <= MIX_MATCH_TOLERANCE)
        if forward or reversed_:
            return curve_id
    return None


def usage(curve_id: str, owner_id: Optional[str] = None) -> Dict[str, Any]:
    """
    What is already calibrated against this curve.

    Deleting a curve out from under a stored analysis would leave a result that
    can be displayed but never reproduced, so api/curves.py checks this first
    and reports it back: "used by 3 analyses" is actionable where "409" is not.

    **The counts are always global, across every user.** Curves are shared
    reference data, so whether one may be deleted is a question about all of
    them -- scoping this to the caller would let one user delete a curve
    another user's stored results were computed against, which is the exact
    thing this function exists to prevent.

    `owner_id` PARTITIONS rather than filters. The id lists come back holding
    only that user's own analyses and projects, with everyone else's reduced to
    a count under `others_count` -- so the answer stays complete without
    handing one user a list of another's row ids.
    """
    with SessionLocal() as db:
        analysis_rows = [
            row[0] for row in db.query(RadiocarbonSample.analysis_id)
            .filter(RadiocarbonSample.curve_id == curve_id)
            .distinct().all()
        ]
        project_rows = [
            row[0] for row in db.query(ProjectSample.project_id)
            .filter(ProjectSample.curve_id == curve_id)
            .distinct().all()
        ]

        analysis_ids, project_ids, others = analysis_rows, project_rows, 0
        if owner_id is not None:
            mine = {
                row[0] for row in db.query(Analysis.id)
                .filter(Analysis.id.in_(analysis_rows or ['']),
                        Analysis.owner_id == owner_id).all()
            }
            my_projects = {
                row[0] for row in db.query(Project.id)
                .filter(Project.id.in_(project_rows or ['']),
                        Project.owner_id == owner_id).all()
            }
            others = (len(analysis_rows) - len(mine)) + (len(project_rows) - len(my_projects))
            analysis_ids = [a for a in analysis_rows if a in mine]
            project_ids = [p for p in project_rows if p in my_projects]
        # A mixed curve holds its parents by id, so an ingredient is in use
        # for as long as the mix exists.
        mixed = [
            row.id for row in db.query(StoredCalibrationCurve)
            .filter(StoredCalibrationCurve.kind == 'mixed').all()
            if curve_id in ((row.recipe or {}).get('a'), (row.recipe or {}).get('b'))
        ]

    return {
        'analysis_ids': analysis_ids,
        'project_ids': project_ids,
        'mixed_curve_ids': mixed,
        # Other people's use of this curve, as a bare count. It has to reach
        # the caller somehow -- it is why their delete was refused -- but it
        # reaches them as a number, not as a list of ids.
        'others_count': others,
        # The global answer, which is the one that decides whether a delete is
        # allowed. Never derived from the (possibly partitioned) lists above.
        'in_use': bool(analysis_rows or project_rows or mixed),
    }
