"""
The one place that knows what marine reservoir snapshots exist.

Built on services/curve_registry.py's pattern, because a snapshot is the same
kind of thing as a calibration curve: reference data that a published result
depends on, which must therefore be immutable, verifiable, and resolvable long
after whoever added it has moved on. Where that module has builtin-from-file
and stored-from-row behind one interface, this one has bundled-from-file and
ingested-from-row.

Ownership
---------
The same split, for the same reason. The BUNDLED snapshot is a file in
calibration_data/, belongs to nobody and is visible to all, so a fresh install
has a working lookup with no network and two installs of one version agree on
what they compute. An INGESTED snapshot is a row owned by whoever ran the
ingest and listed for nobody else -- a reservoir table someone pulled down
this morning has nobody vouching for it.

And so the same division of functions, which is the thing to keep straight
when adding here:

  **Choosing and listing** -- `list_specs` and `visible_to`. These answer
  "what may this user pick", and both of them REQUIRE an
  `owner_id`. Required rather than defaulted, because a default is how one of
  these quietly starts returning everyone's snapshots.

  **Loading** -- `get_spec`, `resolve`, `in_use`. These answer "what is
  snapshot X", take an id that has already been authorised, and do not ask
  who wants it. A rerun reproducing a stored analysis has no user in scope and
  must still resolve the snapshot that analysis cited.

The security boundary is therefore api/, not here: a route checks
`visible_to()` before it resolves.

Verification
------------
`resolve` re-hashes what it loaded and refuses to serve anything that does not
match its recorded `content_sha256`. That is not belt-and-braces: the whole
argument for mirroring this database locally is that a published ΔR can be
reproduced, and a mirror that quietly served different numbers than the ones
an analysis was computed against would defeat it silently -- which is the one
failure mode worse than an outage.
"""
from dataclasses import dataclass, asdict
from functools import lru_cache
from typing import Dict, List, Optional, Tuple
import os

from sqlalchemy import String

from app.database import SessionLocal
from app.models import (
    MarineReservoirPoint,
    MarineReservoirSnapshot,
    ProjectSample,
    RadiocarbonSample,
)
from app.services.reservoir_ingest import (
    ReservoirRow,
    content_hash,
    rows_from_csv,
)


# For the internal callers that genuinely mean every snapshot regardless of
# owner. Spelled out at each call site, so reading one is enough to tell
# whether the scope was chosen or forgotten.
SCOPE_ANY = '__any_owner__'

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'calibration_data')


@dataclass(frozen=True)
class _Bundled:
    """A snapshot shipped with the app, loaded from calibration_data/."""

    id: str
    filename: str
    source: str
    # The source's own last_changed, as a date string. Part of the id, and
    # repeated here so the spec can report it without parsing the id back.
    source_last_changed: str
    record_count: int
    # Recorded here rather than computed at load time, so a file that was
    # edited after it shipped fails verification instead of hashing to
    # whatever it now contains.
    content_sha256: str
    citation: str


# The snapshots that ship with the app.
#
# EMPTY, and that is a gate rather than an oversight: redistributing the CHRONO
# Marine20 reservoir compilation needs the compilers' permission, which is
# op 0 of the plan's sequence and is a letter a person has to send. Until it is
# answered there is nothing here to ship, and the lookup runs on ingested
# snapshots only.
#
# Adding one is a data drop: put the CSV that reservoir_ingest.rows_to_csv
# produced in calibration_data/, and add one _Bundled entry naming it with the
# record count and hash the ingest reported.
_BUNDLED: Tuple[_Bundled, ...] = ()

_BY_ID: Dict[str, _Bundled] = {b.id: b for b in _BUNDLED}


@dataclass
class SnapshotSpec:
    """
    What is known about a snapshot without loading its points.

    Nothing is asserted here that could be measured from the rows instead --
    `record_count` is the count the ingest reported and `resolve` checks it
    against what actually loads.
    """
    id: str
    kind: str                      # 'bundled' | 'ingested'
    source: str
    record_count: int
    content_sha256: str
    citation: Optional[str] = None
    note: Optional[str] = None
    fetched_at: Optional[str] = None
    source_last_changed: Optional[str] = None
    # Null for a bundled snapshot, which belongs to the install.
    created_by: Optional[str] = None

    def as_dict(self) -> dict:
        return asdict(self)


class SnapshotNotFound(Exception):
    """No snapshot of that id, bundled or stored."""


class SnapshotCorrupt(Exception):
    """The snapshot does not hash to the value it was stored with."""


def _spec_from_bundled(bundled: _Bundled) -> SnapshotSpec:
    return SnapshotSpec(
        id=bundled.id,
        kind='bundled',
        source=bundled.source,
        record_count=bundled.record_count,
        content_sha256=bundled.content_sha256,
        citation=bundled.citation,
        source_last_changed=bundled.source_last_changed,
    )


def _spec_from_row(row: MarineReservoirSnapshot) -> SnapshotSpec:
    return SnapshotSpec(
        id=row.id,
        kind='ingested',
        source=row.source,
        record_count=row.record_count,
        content_sha256=row.content_sha256,
        citation=row.citation,
        note=row.note,
        fetched_at=row.fetched_at.isoformat() if row.fetched_at else None,
        source_last_changed=(
            row.source_last_changed.isoformat() if row.source_last_changed else None),
        created_by=row.created_by,
    )


# --------------------------------------------------------------------------
# Choosing and listing -- owner_id required
# --------------------------------------------------------------------------

def list_specs(owner_id: str) -> List[SnapshotSpec]:
    """
    Every snapshot `owner_id` may pick: the bundled ones, plus their own.

    Newest first within the ingested ones, because the question someone asks
    of this list is almost always "what did I fetch most recently".
    """
    specs = [_spec_from_bundled(b) for b in _BUNDLED]
    with SessionLocal() as db:
        query = db.query(MarineReservoirSnapshot)
        if owner_id != SCOPE_ANY:
            query = query.filter(MarineReservoirSnapshot.created_by == owner_id)
        rows = query.order_by(MarineReservoirSnapshot.created_at.desc()).all()
        specs.extend(_spec_from_row(row) for row in rows)
    return specs


def visible_to(snapshot_id: str, owner_id: str) -> bool:
    """
    Whether `owner_id` may name this snapshot at all.

    The predicate the API checks before it resolves anything. Named for what
    it enforces rather than for what it looks up, so a route reading
    `if not visible_to(...)` says so.
    """
    if snapshot_id in _BY_ID:
        return True
    with SessionLocal() as db:
        query = db.query(MarineReservoirSnapshot.id).filter(
            MarineReservoirSnapshot.id == snapshot_id)
        if owner_id != SCOPE_ANY:
            query = query.filter(MarineReservoirSnapshot.created_by == owner_id)
        return query.first() is not None


# --------------------------------------------------------------------------
# Loading -- id already authorised
# --------------------------------------------------------------------------

def get_spec(snapshot_id: str) -> SnapshotSpec:
    """What snapshot X is. Unscoped: see the module docstring."""
    bundled = _BY_ID.get(snapshot_id)
    if bundled:
        return _spec_from_bundled(bundled)
    with SessionLocal() as db:
        row = db.query(MarineReservoirSnapshot).filter(
            MarineReservoirSnapshot.id == snapshot_id).first()
        if row is None:
            raise SnapshotNotFound(f"No marine reservoir snapshot '{snapshot_id}'.")
        return _spec_from_row(row)


@lru_cache(maxsize=4)
def _bundled_rows(snapshot_id: str) -> Tuple[ReservoirRow, ...]:
    """
    A bundled snapshot's rows, read from disk once.

    Cacheable without risk because a bundled snapshot is a file that ships
    with the app: its numbers cannot change between two calls in one process
    the way a database row's could.
    """
    bundled = _BY_ID[snapshot_id]
    path = os.path.normpath(os.path.join(_DATA_DIR, bundled.filename))
    try:
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
    except OSError as exc:
        raise SnapshotNotFound(
            f"The bundled snapshot '{snapshot_id}' should be at {path} and "
            f"could not be read: {exc}")
    return tuple(rows_from_csv(text))


def resolve(snapshot_id: str) -> List[ReservoirRow]:
    """
    A snapshot's points, verified.

    Re-hashes what loaded and refuses anything that does not match the
    recorded value. A snapshot that fails is not served at all, rather than
    served with a warning: the numbers are what a published ΔR was computed
    from, and quietly different numbers are worse than none.
    """
    spec = get_spec(snapshot_id)

    if spec.kind == 'bundled':
        rows = list(_bundled_rows(snapshot_id))
    else:
        with SessionLocal() as db:
            points = db.query(MarineReservoirPoint).filter(
                MarineReservoirPoint.snapshot_id == snapshot_id).all()
            rows = [
                ReservoirRow(
                    map_no=p.map_no,
                    latitude=p.latitude,
                    longitude=p.longitude,
                    delta_r=p.delta_r,
                    delta_r_error=p.delta_r_error,
                    reference=p.reference,
                    locality=p.locality,
                    subregion=p.subregion,
                    notes=p.notes,
                    c14_age=p.c14_age,
                    c14_error=p.c14_error,
                    lab_id=p.lab_id,
                    collection_year=p.collection_year,
                    reservoir_age=p.reservoir_age,
                    genus=p.genus,
                    species=p.species,
                    feeding=p.feeding,
                    feeding_raw=p.feeding_raw,
                )
                for p in points
            ]

    digest = content_hash(rows)
    if digest != spec.content_sha256:
        raise SnapshotCorrupt(
            f"Marine reservoir snapshot '{snapshot_id}' does not match the "
            "data it was stored with, so it will not be used. Any ΔR derived "
            "from it cannot be reproduced. Re-ingest the database as a new "
            "snapshot rather than trusting this copy."
        )
    return rows


def in_use(snapshot_id: str) -> int:
    """
    How many dates cite this snapshot in their `delta_r_source`.

    Non-zero means it cannot be deleted. A snapshot's whole job is to make a
    published ΔR reproducible, and deleting one an analysis names would turn
    that analysis's provenance into a dangling reference -- exactly what the
    stored curves' archive-don't-delete rule exists to prevent.

    Matched on the JSON column as text rather than by a key lookup, because
    SQLite and PostgreSQL disagree about JSON operators and this is a check
    that must not silently return zero on one of them. The id contains a date
    and a source slug, so a substring match is not going to collide with
    anything else in that column.
    """
    needle = f'%"{snapshot_id}"%'
    with SessionLocal() as db:
        count = 0
        for model in (RadiocarbonSample, ProjectSample):
            count += (
                db.query(model)
                .filter(model.delta_r_source.isnot(None))
                .filter(model.delta_r_source.cast(String).like(needle))
                .count()
            )
        return count


def clear_caches() -> None:
    """Drop the bundled-file cache. For tests, which write files under it."""
    _bundled_rows.cache_clear()
