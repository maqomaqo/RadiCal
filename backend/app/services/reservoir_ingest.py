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

    fetch_records()      one HTTP session and one query
    extract_records()    the source's table, as undecoded bytes per field
    records_to_rows()    per-field decode, feeding normalisation, validation
    content_hash()       sha256 over the normalised rows
    store()              one snapshot row and its points, in one transaction

Only the first needs a network, and nothing below it knows that a network
exists -- which is what lets the tests run the whole rest of the pipeline
against a captured response (tests/fixtures/calib_marine/).

The two stages either side of the decode are where the care goes, because
both have a failure mode that looks like success. extract_records checks the
response's header row against EXPECTED_COLUMNS and refuses anything else,
since a source that reordered its columns read positionally would store
latitude as longitude. decode_field works per FIELD, since one response
carries both correct UTF-8 and raw cp1252 and either codec applied to the
whole document silently mangles the other kind.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import hashlib
import html
import re

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
SOURCE_QUERY = (
    'select mapno,lon,lat,deltar,deltarerr,reference,locality,subregion,notes,'
    'collectionyear,reservoirage,c14age,c14err,labid,last_changed,genus,species,'
    'feeding from details left join taxa using (taxano)'
)

# The columns SOURCE_QUERY asks for, in order. The response's header row is
# checked against this EXACTLY: a source that has renamed, reordered or
# dropped a column is a source that has changed, and a parser that shrugs at
# that produces a mirror silently missing a field or -- worse -- with latitude
# read out of the longitude column.
EXPECTED_COLUMNS = (
    'mapno', 'lon', 'lat', 'deltar', 'deltarerr', 'reference', 'locality',
    'subregion', 'notes', 'collectionyear', 'reservoirage', 'c14age',
    'c14err', 'labid', 'last_changed', 'genus', 'species', 'feeding',
)

# Where the query interface lives. http rather than https deliberately: the
# host's TLS is not reachable, and this fetch carries no credentials and no
# coordinates -- it pulls a published reference table down whole. Nothing
# about a user's data is in the request, which is the property that makes
# plain HTTP acceptable here and would not make it acceptable anywhere else
# in this app.
QUERY_URL = 'http://calib.org/marine/query/query.php'
SESSION_URL = 'http://calib.org/marine/query/'

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
    # Rows whose longitude was outside ±180 and was wrapped back onto the
    # earth. Three in the 2060 captured on 2026-09-14, all real places written
    # with an unwrapped longitude. Counted rather than silent: it is a shape
    # of the source, and a number that jumped would be worth looking at.
    wrapped_longitudes: int = 0
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
            'wrapped_longitudes': self.wrapped_longitudes,
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
    raw cp1252 bytes (`Thyborøn` arriving as `Thybor\\xf8n`) -- 60 fields of the
    2060 rows captured on 2026-09-14 fail a strict UTF-8 decode, every one of
    them valid cp1252. Reading the whole document as cp1252 fixes
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


# The response is an HTML table. Matched with regular expressions rather than
# an HTML parser, and the reason is the per-field decode: the document is not
# valid in any single encoding (see decode_field), so handing it to a parser
# that wants a str means choosing a codec for the whole thing, which is the
# one thing that cannot be done here. These run on BYTES and hand each cell
# back as bytes, still undecoded.
#
# The markup they run against is plain and machine-generated -- `<tr>\n<td>
# ...</td>...</tr>` with no nesting inside a cell -- which is what makes this
# tractable. The header check below is what catches it changing.
_TABLE_START = re.compile(rb'<table\s+border\s*>', re.I)
_ROW = re.compile(rb'<tr>\s*(<td>.*?</td>)\s*</tr>', re.I | re.S)
_CELL = re.compile(rb'<td>(.*?)</td>', re.I | re.S)
# 'N records', which the page prints above the table. Checked against the rows
# actually parsed: a mismatch means the extraction lost some, and a mirror
# quietly missing 40 measurements would give quietly wrong averages.
_DECLARED_COUNT = re.compile(rb'(\d+)\s+records', re.I)


def extract_records(raw: bytes) -> Tuple[List[Dict[str, bytes]], Optional[int]]:
    """
    The result table out of one query response, as undecoded bytes per field.

    Returns the records and the count the page declared, so the caller can
    check one against the other.

    Fails loudly on anything that is not the table SOURCE_QUERY asks for. That
    is the plan's §11.2 mitigation and it is the whole defence here: the host
    is an unversioned PHP application with no API, and the failure mode to
    avoid is not an outage -- it is a response whose columns moved being read
    positionally and stored as a mirror in which latitude is longitude.
    """
    start = _TABLE_START.search(raw)
    if not start:
        raise IngestError(
            'The response has no result table in it. The query interface may '
            'have changed, or the query may have been rejected.')

    declared_match = _DECLARED_COUNT.search(raw[:start.start()])
    declared = int(declared_match.group(1)) if declared_match else None

    body = raw[start.end():]
    rows = [
        [cell for cell in _CELL.findall(match.group(1))]
        for match in _ROW.finditer(body)
    ]
    if not rows:
        raise IngestError('The response has a result table with no rows in it.')

    header = [c.strip().lower().decode('ascii', 'replace') for c in rows[0]]
    if tuple(header) != EXPECTED_COLUMNS:
        raise IngestError(
            'The result table does not have the columns this ingest asks '
            f'for. Found {header}; wanted {list(EXPECTED_COLUMNS)}. The '
            'source has changed shape and the parser must be updated rather '
            'than the rows read positionally.')

    records: List[Dict[str, bytes]] = []
    for index, cells in enumerate(rows[1:], start=1):
        if len(cells) != len(EXPECTED_COLUMNS):
            raise IngestError(
                f'Row {index} of the result table has {len(cells)} cells, not '
                f'{len(EXPECTED_COLUMNS)}. Refusing to read it positionally.')
        records.append(dict(zip(EXPECTED_COLUMNS, cells)))

    return records, declared


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


def _wrap_longitude(value: float) -> float:
    """
    A longitude outside ±180 brought back onto the earth.

    Three rows of the 2060 captured on 2026-09-14 carry one: map 460 and 461
    at 183.5 (the Chatham Islands, which is 176.5 West) and map 1349 at -181
    (Fiji, which is 179 East). They are not bad data -- they are real places
    written with an unwrapped longitude, and the source's own map plots them
    correctly because Google Maps wraps too.

    Wrapping is not a guess, which is why this is allowed where the importer
    refuses to guess at a DMS coordinate: longitude is modular, 183.5 and
    -176.5 are the same meridian, and there is exactly one answer. LATITUDE
    has no such identity -- 91 is not a latitude, it is a mistake -- so it is
    still refused.
    """
    if -180 <= value <= 180:
        return value
    return ((value + 180) % 360) - 180


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """
    A 'YYYY-MM-DD HH:MM:SS' stamp, or None.

    None rather than an error for anything else: last_changed is metadata
    about the source's own housekeeping, and a row with an odd one is still a
    perfectly good measurement. The consequence of losing one is a snapshot
    identified by a slightly earlier date, which is visible; refusing the
    whole fetch over it would not be proportionate.
    """
    text = _text(value)
    if text is None:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
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
    declared: Optional[int] = None,
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
            # AFTER the decode, never before: the entities are ASCII, but the
            # bytes around them are not necessarily anything, and unescaping
            # first would mean having already chosen a codec. The source
            # writes an empty cell as &nbsp;, which becomes U+00A0 here and
            # then nothing at all in _text, whose strip() takes it.
            decoded[key] = html.unescape(text) if text is not None else None

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
        if not (-90 <= latitude <= 90):
            report.errors.append(
                f"Row {index} (map no {int(map_no)}): latitude {latitude} is "
                "not a latitude")
            continue
        if not (-180 <= longitude <= 180):
            # Wrapped rather than refused -- see _wrap_longitude. Counted,
            # because it is a shape of the source worth being able to see.
            longitude = _wrap_longitude(longitude)
            report.wrapped_longitudes += 1

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

        # What the SOURCE says about its own currency. The maximum across the
        # rows is what identifies the snapshot, so it is collected here rather
        # than in a second query.
        changed = _parse_timestamp(decoded.get('last_changed'))
        if changed is not None and (
                report.source_last_changed is None
                or changed > report.source_last_changed):
            report.source_last_changed = changed

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
    if declared is not None and report.ok and len(rows) != declared:
        # The page prints its own row count above the table. A mismatch means
        # the extraction lost rows, and a mirror quietly missing forty
        # measurements gives quietly wrong averages -- the failure this whole
        # design exists to prevent.
        report.errors.append(
            f'The source said {declared} records and {len(rows)} were read. '
            'Refusing to store a partial mirror.')
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
# The bundled snapshot's file format
# --------------------------------------------------------------------------
#
# The column order of calibration_data/marine_reservoir_YYYY-MM-DD.csv. A
# plain CSV rather than the source's own format, because what ships is the
# NORMALISED rows -- the decode and the feeding fold have already happened,
# and shipping the raw response would mean re-running a parser at load time on
# every install, which is the thing content_sha256 exists to make unnecessary.
CSV_COLUMNS = (
    'map_no', 'latitude', 'longitude', 'delta_r', 'delta_r_error',
    'reference', 'locality', 'subregion', 'notes',
    'c14_age', 'c14_error', 'lab_id', 'collection_year', 'reservoir_age',
    'genus', 'species', 'feeding', 'feeding_raw',
)


def rows_to_csv(rows: Iterable[ReservoirRow]) -> str:
    """
    The normalised rows as the file that ships in calibration_data/.

    Sorted by map number, as content_hash sorts them, so the file and the hash
    are in the same order and a diff between two snapshots reads as a change
    in the data rather than a reshuffle.
    """
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_COLUMNS)
    for row in sorted(rows, key=lambda r: r.map_no):
        writer.writerow([
            '' if getattr(row, column) is None else getattr(row, column)
            for column in CSV_COLUMNS
        ])
    return output.getvalue()


def rows_from_csv(text: str) -> List[ReservoirRow]:
    """
    Read a bundled snapshot back.

    Deliberately strict about its header: a file whose columns are not these
    is not this format, and reading it positionally would silently swap
    latitude for longitude. Raises rather than guessing.
    """
    import csv
    import io

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise IngestError('The bundled snapshot file is empty.')
    if tuple(h.strip() for h in header) != CSV_COLUMNS:
        raise IngestError(
            'The bundled snapshot does not have the expected columns. Found '
            f'{header!r}; wanted {list(CSV_COLUMNS)!r}.')

    numeric = {'latitude', 'longitude', 'delta_r', 'delta_r_error',
               'c14_age', 'c14_error', 'reservoir_age'}
    rows: List[ReservoirRow] = []
    for line, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):
            continue
        if len(values) != len(CSV_COLUMNS):
            raise IngestError(
                f'Row {line} of the bundled snapshot has {len(values)} values, '
                f'not {len(CSV_COLUMNS)}.')
        record = dict(zip(CSV_COLUMNS, values))
        try:
            rows.append(ReservoirRow(
                map_no=int(record['map_no']),
                latitude=float(record['latitude']),
                longitude=float(record['longitude']),
                delta_r=_number(record['delta_r']),
                delta_r_error=_number(record['delta_r_error']),
                reference=_text(record['reference']),
                locality=_text(record['locality']),
                subregion=_text(record['subregion']),
                notes=_text(record['notes']),
                c14_age=_number(record['c14_age']),
                c14_error=_number(record['c14_error']),
                lab_id=_text(record['lab_id']),
                collection_year=(
                    int(float(record['collection_year']))
                    if record['collection_year'].strip() else None),
                reservoir_age=_number(record['reservoir_age']),
                genus=_text(record['genus']),
                species=_text(record['species']),
                feeding=_text(record['feeding']),
                feeding_raw=_text(record['feeding_raw']),
            ))
        except ValueError as exc:
            raise IngestError(
                f'Row {line} of the bundled snapshot could not be read: {exc}')
    if not rows:
        raise IngestError('The bundled snapshot has a header and no rows.')
    return rows


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

def fetch_records(timeout: int = 180) -> Tuple[List[Dict[str, bytes]], Optional[int]]:
    """
    Fetch SOURCE_QUERY from calib.org and return one record per result row,
    each field still as undecoded BYTES, plus the row count the page declared.

    Bytes rather than text, and that is the contract rather than an
    implementation detail: the response is not valid in any single encoding
    (see decode_field), so anything that decodes it wholesale here has already
    destroyed what the next stage exists to recover.

    ONE query. This is a courtesy interface offered for interactive use on an
    Apache / PHP host that owes this app nothing; it is not an API, there is
    no rate limit published, and the right way to treat it is as though a
    person were typing into the form. The session GET before it is what the
    form does -- the page reports "Connected to marine20 database" -- and is
    the only other request made.

    Raises IngestUnavailable when the host cannot be reached, which is a
    different thing from a response that will not parse: one is a network to
    try again on, the other is a source that has changed shape.
    """
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - requests is a dependency
        raise IngestUnavailable(f'The HTTP client is unavailable: {exc}')

    session = requests.Session()
    session.headers.update({
        # Named, and saying what it is doing, because this is somebody else's
        # server and an anonymous scraper is how a courtesy interface gets
        # closed. Worth adding a contact URL here if this app ever has one.
        'User-Agent': (
            'RadiCal/1.0 (radiocarbon analysis; marine reservoir mirror; '
            'one query per ingest)'
        ),
    })

    try:
        # What the form does: the query endpoint answers in the context of a
        # session, and the page says so out loud.
        session.get(SESSION_URL, timeout=timeout)
        response = session.post(
            QUERY_URL, data={'query': SOURCE_QUERY}, timeout=timeout)
    except Exception as exc:
        raise IngestUnavailable(
            f'calib.org could not be reached: {exc}. The mirror already '
            'stored, if any, is unaffected -- a failed fetch breaks refresh, '
            'never analysis.')

    if response.status_code != 200:
        raise IngestUnavailable(
            f'calib.org answered {response.status_code} to the query. '
            'Nothing was stored.')

    # response.content, never response.text: requests would guess a codec for
    # the whole document from a header that is wrong about 60 of its fields.
    return extract_records(response.content)
