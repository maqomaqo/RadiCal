"""
Turning a fetch of the CHRONO/calib.org marine reservoir database into a
snapshot this app can serve.

The ingest is EXPLICIT. It is never on a timer, never on startup, and never a
side effect of a lookup: it is the only code in the app that talks to the
internet, and it does so because a person asked it to. It is also
all-or-nothing in the sense services/date_import.py already means it -- a
fetch that fails to parse is not written, and the report lists every problem
rather than the first.

The pipeline is four stages, and they are separate functions because only the
first needs a network:

    fetch_records()      the HTTP session and the source's own table shape
    records_to_rows()    per-field decode, feeding normalisation, validation
    content_hash()       sha256 over the normalised rows
    store()              one snapshot row and its points, in one transaction

**fetch_records is not implemented.** See its docstring: it is the one part of
this that cannot be written without the source in front of you, and guessing
at a response shape is precisely the failure the importer's rules elsewhere in
this codebase refuse. Everything below it is written, tested, and independent
of how the bytes arrive -- a caller that has records from any source can use
the rest of this module today, which is how the tests exercise it.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import hashlib

from app.models import MarineReservoirPoint, MarineReservoirSnapshot
from app.services.reservoir import is_recognised_feeding, normalise_feeding


# The query the ingest issues against the source. Stated here rather than
# built at the call site because it is part of what a snapshot IS: genus,
# species and feeding live in the taxa table, so a snapshot without this join
# could not answer the question the feeding filter asks.
#
# A LEFT join: a details row whose taxon is unknown is still a reservoir
# measurement with a position and an offset, and dropping it would silently
# shrink the database.
SOURCE_QUERY = """
select d.mapno, d.lon, d.lat, d.deltar, d.deltarerr, d.reference, d.locality,
       d.subregion, d.notes, d.collectionyear, d.reservoirage,
       d.c14age, d.c14err, d.labid, t.genus, t.species, t.feeding
from details d left join taxa t using (taxano)
""".strip()

# The fields a record must carry for the row to mean anything. Everything else
# is descriptive and may be absent.
REQUIRED_FIELDS = ('mapno', 'lat', 'lon')


class IngestError(Exception):
    """The fetch produced something that is not this database."""


class IngestUnavailable(IngestError):
    """The fetch cannot be performed from here. See fetch_records."""


@dataclass
class ReservoirRow:
    """One normalised measurement, ready to store or to hash."""
    map_no: int
    latitude: float
    longitude: float
    delta_r: Optional[float] = None
    delta_r_error: Optional[float] = None
    reference: Optional[str] = None
    locality: Optional[str] = None
    subregion: Optional[str] = None
    notes: Optional[str] = None
    c14_age: Optional[float] = None
    c14_error: Optional[float] = None
    lab_id: Optional[str] = None
    collection_year: Optional[int] = None
    reservoir_age: Optional[float] = None
    genus: Optional[str] = None
    species: Optional[str] = None
    feeding: Optional[str] = None
    feeding_raw: Optional[str] = None


@dataclass
class IngestReport:
    """
    What the fetch found, in the terms a person needs to judge it.

    Honest about the source rather than reassuring about the fetch. The counts
    that matter are the ones that say how much of this database is unusable or
    unfamiliar: rows with no uncertainty cannot enter a weighted mean, rows
    needing a cp1252 fallback say the source's encoding is still mixed, and an
    unrecognised feeding spelling says its vocabulary has grown since §2.7 was
    measured.
    """
    row_count: int = 0
    source_last_changed: Optional[datetime] = None
    content_sha256: str = ''
    # Rows carrying a ΔR but no uncertainty on it. The lookup must exclude
    # these from any weighted mean, so this is the size of the hole.
    without_error: int = 0
    # Fields that failed a strict UTF-8 decode and were read as cp1252. Not a
    # problem -- it is handled -- but a number that going to zero would mean
    # the source had been cleaned up, and going up means it has not.
    cp1252_fallbacks: int = 0
    # Fields that failed both, kept as escaped text rather than replaced by
    # U+FFFD. A locality name must never be quietly turned into question
    # marks.
    undecodable_fields: int = 0
    # Feeding spellings services/reservoir.py has never seen, with how many
    # rows carried each. Reported rather than absorbed: they all normalise to
    # 'unknown', and a silent 'unknown' is how a source's new vocabulary
    # becomes 300 rows nobody looks at.
    unrecognised_feeding: Dict[str, int] = field(default_factory=dict)
    # Every problem found, not just the first. A non-empty list means nothing
    # was written.
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> Dict[str, Any]:
        return {
            'ok': self.ok,
            'row_count': self.row_count,
            'source_last_changed': (
                self.source_last_changed.isoformat() if self.source_last_changed else None),
            'content_sha256': self.content_sha256,
            'without_error': self.without_error,
            'cp1252_fallbacks': self.cp1252_fallbacks,
            'undecodable_fields': self.undecodable_fields,
            'unrecognised_feeding': dict(self.unrecognised_feeding),
            'errors': list(self.errors),
        }


# --------------------------------------------------------------------------
# Decoding
# --------------------------------------------------------------------------

def decode_field(raw: Any) -> Tuple[Optional[str], str]:
    """
    One field's text, and how it had to be read: 'utf-8', 'cp1252', 'escaped',
    or 'text' for a value that arrived already decoded.

    **Per field, not per document, and that is the whole point.** One response
    from this source contains both correctly-encoded UTF-8 (`Håkansson`) and
    raw cp1252 bytes (`Thyborøn` arriving as `Thybor\\xf8n`) -- 54 rows of 2060
    fail a strict UTF-8 decode. Reading the whole document as cp1252 fixes
    those and breaks the others; reading it as UTF-8 with errors='replace'
    turns a Danish harbour into `Thybor?n`. Trying each field both ways is the
    only reading that is right about both.

    A field that is valid in neither is kept as its Python bytes repr rather
    than replaced by U+FFFD, and counted, so a mangled locality name stays
    recoverable and visible instead of becoming a row of question marks.
    """
    if raw is None:
        return None, 'text'
    if isinstance(raw, str):
        return raw, 'text'
    if not isinstance(raw, (bytes, bytearray)):
        return str(raw), 'text'

    data = bytes(raw)
    try:
        return data.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        pass
    try:
        return data.decode('cp1252'), 'cp1252'
    except UnicodeDecodeError:
        # cp1252 has five undefined bytes of its own (0x81, 0x8d, 0x8f, 0x90,
        # 0x9d), so this branch is reachable and not paranoia.
        return repr(data)[2:-1], 'escaped'


def _number(value: Optional[str]) -> Optional[float]:
    """A numeric field, or None. The source writes an absent value several
    ways -- empty, 'NULL', a lone dash -- and none of them is a zero."""
    if value is None:
        return None
    text = value.strip()
    if not text or text.upper() == 'NULL' or text in ('-', '--'):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    if not text or text.upper() == 'NULL':
        return None
    return text


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

def records_to_rows(
    records: Iterable[Dict[str, Any]],
    report: Optional[IngestReport] = None,
) -> Tuple[List[ReservoirRow], IngestReport]:
    """
    Raw records from the source into normalised rows, with the report that
    describes them.

    A record is a mapping of the SOURCE_QUERY's column names to values, each
    either bytes (decoded here) or a str (taken as-is). Whoever produced them
    is not this function's business, which is what lets the tests drive it
    without a network.

    Every problem is collected rather than raised: a caller writes nothing
    when `report.errors` is non-empty, and the person reading it fixes the
    whole file in one pass.
    """
    report = report or IngestReport()
    rows: List[ReservoirRow] = []
    seen: Dict[int, int] = {}

    for index, record in enumerate(records, start=1):
        decoded: Dict[str, Optional[str]] = {}
        for key, value in record.items():
            text, how = decode_field(value)
            if how == 'cp1252':
                report.cp1252_fallbacks += 1
            elif how == 'escaped':
                report.undecodable_fields += 1
            decoded[key] = text

        missing = [f for f in REQUIRED_FIELDS if _text(decoded.get(f)) is None]
        if missing:
            report.errors.append(
                f"Row {index}: {', '.join(missing)} missing -- a reservoir "
                "point needs a map number and a position")
            continue

        map_no = _number(decoded.get('mapno'))
        latitude = _number(decoded.get('lat'))
        longitude = _number(decoded.get('lon'))
        if map_no is None or latitude is None or longitude is None:
            report.errors.append(
                f"Row {index}: map number and position must be numbers, got "
                f"mapno={decoded.get('mapno')!r}, lat={decoded.get('lat')!r}, "
                f"lon={decoded.get('lon')!r}")
            continue
        if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
            report.errors.append(
                f"Row {index} (map no {int(map_no)}): position "
                f"{latitude}, {longitude} is not on the earth")
            continue

        key = int(map_no)
        if key in seen:
            # The map number is the source's own identifier and this app's
            # primary key within a snapshot. Two rows claiming one is not
            # something to resolve by picking a winner.
            report.errors.append(
                f"Row {index}: map number {key} already appeared at row "
                f"{seen[key]} -- it identifies a point and cannot repeat")
            continue
        seen[key] = index

        feeding_raw = _text(decoded.get('feeding'))
        if not is_recognised_feeding(feeding_raw):
            spelling = (feeding_raw or '').strip()
            report.unrecognised_feeding[spelling] = (
                report.unrecognised_feeding.get(spelling, 0) + 1)

        delta_r = _number(decoded.get('deltar'))
        delta_r_error = _number(decoded.get('deltarerr'))
        if delta_r is not None and delta_r_error is None:
            report.without_error += 1

        collection_year = _number(decoded.get('collectionyear'))

        rows.append(ReservoirRow(
            map_no=key,
            latitude=latitude,
            longitude=longitude,
            delta_r=delta_r,
            delta_r_error=delta_r_error,
            reference=_text(decoded.get('reference')),
            locality=_text(decoded.get('locality')),
            subregion=_text(decoded.get('subregion')),
            notes=_text(decoded.get('notes')),
            c14_age=_number(decoded.get('c14age')),
            c14_error=_number(decoded.get('c14err')),
            lab_id=_text(decoded.get('labid')),
            collection_year=int(collection_year) if collection_year is not None else None,
            reservoir_age=_number(decoded.get('reservoirage')),
            genus=_text(decoded.get('genus')),
            species=_text(decoded.get('species')),
            feeding=normalise_feeding(feeding_raw),
            feeding_raw=feeding_raw,
        ))

    report.row_count = len(rows)
    if not rows and not report.errors:
        report.errors.append('The fetch returned no rows at all.')
    return rows, report


# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------

def content_hash(rows: Iterable[ReservoirRow]) -> str:
    """
    sha256 over the NUMBERS, as curve_registry.content_hash does it for a
    curve, and for the same reason: two fetches that differ only in the order
    the source happened to return rows describe one database and must hash the
    same, while a fetch whose offsets were corrected must not.

    Sorted by map number, which is the source's own identifier, so the order
    is a property of the data rather than of the request. Formatted with %.6g
    so a float that round-trips differently through two parsers does not read
    as a changed measurement.
    """
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda r: r.map_no):
        digest.update('|'.join([
            str(row.map_no),
            f'{row.latitude:.6g}',
            f'{row.longitude:.6g}',
            '' if row.delta_r is None else f'{row.delta_r:.6g}',
            '' if row.delta_r_error is None else f'{row.delta_r_error:.6g}',
            # The normalised class, not the raw string: a source that fixes
            # the capitalisation of 'Deposit' has not changed a measurement.
            row.feeding or '',
        ]).encode() + b'\n')
    return digest.hexdigest()


def snapshot_id(source_slug: str, last_changed: Optional[datetime]) -> str:
    """
    The snapshot's id: its source and the source's OWN last-changed date, not
    the date we fetched it.

    Two people ingesting the same unchanged database on different days get the
    same id, which is what makes "are we looking at the same numbers?"
    answerable between installations. Falling back to the fetch date when the
    source says nothing is the honest alternative: it records what is actually
    known.
    """
    stamp = (last_changed or datetime.utcnow()).strftime('%Y-%m-%d')
    return f'{source_slug}-{stamp}'


# --------------------------------------------------------------------------
# Storing
# --------------------------------------------------------------------------

def store(
    db,
    rows: List[ReservoirRow],
    report: IngestReport,
    *,
    source: str,
    created_by: Optional[str],
    citation: Optional[str] = None,
    note: Optional[str] = None,
    source_slug: str = 'calib-marine20',
) -> MarineReservoirSnapshot:
    """
    Write one snapshot and its points, or nothing.

    Refuses a report carrying errors rather than writing a partial mirror: a
    reservoir table missing the rows that failed to parse would give quietly
    wrong averages, which is worse than no table.

    Refuses a duplicate id too. An id is the source plus the source's own
    last-changed date, so a second ingest of an unchanged database produces
    the id that already exists -- and re-ingesting is not editing. The caller
    is expected to report that as "you already have this one".
    """
    if not report.ok:
        raise IngestError(
            'The fetch did not parse cleanly, so nothing was stored: '
            + '; '.join(report.errors[:5]))

    identifier = snapshot_id(source_slug, report.source_last_changed)
    if db.query(MarineReservoirSnapshot).filter(
            MarineReservoirSnapshot.id == identifier).first():
        raise IngestError(
            f"Snapshot '{identifier}' is already stored. A snapshot's numbers "
            "never change, so re-fetching an unchanged database has nothing "
            "to add.")

    report.content_sha256 = content_hash(rows)

    snapshot = MarineReservoirSnapshot(
        id=identifier,
        source=source,
        fetched_at=datetime.utcnow(),
        source_last_changed=report.source_last_changed,
        record_count=len(rows),
        content_sha256=report.content_sha256,
        citation=citation,
        note=note,
        created_by=created_by,
    )
    db.add(snapshot)
    for row in rows:
        db.add(MarineReservoirPoint(
            snapshot_id=identifier,
            map_no=row.map_no,
            latitude=row.latitude,
            longitude=row.longitude,
            delta_r=row.delta_r,
            delta_r_error=row.delta_r_error,
            reference=row.reference,
            locality=row.locality,
            subregion=row.subregion,
            notes=row.notes,
            c14_age=row.c14_age,
            c14_error=row.c14_error,
            lab_id=row.lab_id,
            collection_year=row.collection_year,
            reservoir_age=row.reservoir_age,
            genus=row.genus,
            species=row.species,
            feeding=row.feeding,
            feeding_raw=row.feeding_raw,
        ))
    db.commit()
    db.refresh(snapshot)
    return snapshot


# --------------------------------------------------------------------------
# Fetching -- the one part that needs the source in front of you
# --------------------------------------------------------------------------

def fetch_records(*_args, **_kwargs) -> List[Dict[str, Any]]:
    """
    Fetch SOURCE_QUERY from calib.org and return one mapping per result row.

    **Deliberately not implemented.** Writing this needs the source's actual
    response in front of you, and it was not reachable from the environment
    the rest of this module was written in. The alternative was to guess at a
    table shape from a description, which is the failure services/date_import
    refuses by name: read a construct as the nearest thing implemented and you
    get a result that is confident and wrong. A reservoir mirror that is
    confidently wrong would move published dates.

    The contract for whoever completes it:

    - Return a list of dicts keyed by SOURCE_QUERY's column names -- mapno,
      lat, lon, deltar, deltarerr, reference, locality, subregion, notes,
      collectionyear, reservoirage, c14age, c14err, labid, genus, species,
      feeding.
    - Return each text value as **bytes**, undecoded. decode_field must see
      the original bytes to do its per-field job; decoding upstream with one
      codec is what this pipeline exists to avoid.
    - Set `report.source_last_changed` from max(details.last_changed) across
      the result. It is what a snapshot is identified by.
    - Issue ONE query. This is a courtesy interface on an Apache 2.4.61 /
      PHP 5.6.40 host; do not retry in a loop.
    - Fail loudly on a table shape that is not this one -- a missing column or
      an unexpected row width is a source that has changed, and a parser that
      shrugs at it produces a mirror silently missing a field.

    Everything downstream of this -- records_to_rows, content_hash, store --
    is written and tested, and takes records from any source.
    """
    raise IngestUnavailable(
        'Fetching from calib.org is not implemented: the source could not be '
        'reached when this was written, and a parser guessed at a response '
        'shape would be confidently wrong about numbers that move published '
        'dates. See the docstring for the contract, and use the bundled '
        'snapshot in the meantime.'
    )
