"""
A stand-in for one fetch of the marine reservoir database.

SYNTHESISED, not captured -- see README.md in this directory for what that
costs and why it was necessary. Every row here exists to demonstrate one
property the real source has, and says which in a comment, so a reader can
tell what the ingest tests are actually pinning down.

Values are bytes, as fetch_records' contract requires: decode_field must see
the original bytes to do its per-field job.
"""

# The 25 spellings the source uses for what is really seven classes, exactly
# as §2.7 of the plan lists them. Order matters only in that the test asserts
# the whole set folds correctly, so all 25 must be here.
FEEDING_SPELLINGS = [
    # suspension (3)
    b'suspension', b'Suspension', b'filter',
    # unknown (4 -- including the literal string NULL and an empty cell)
    b'unknown', b'Unknown', b'NULL', b'',
    # deposit (2)
    b'Deposit', b'deposit',
    # carnivore (5)
    b'Carnivore', b'carnivore', b'predator', b'Scavenger', b'Omnivore',
    # grazer (5)
    b'herbivore', b'Herbivore', b'browser', b'Algal Grazer', b'commensalism',
    # migratory (1)
    b'migratory',
    # ambiguous (5) -- compound judgements the source truncates at sixteen
    # characters. These are the ones that must NOT be resolved into a class.
    b'Deposit or suspe', b'Suspension/Depos', b'Deposit feeder o',
    b'Herbivore/Deposi', b'Carnivore/scaven',
]

# What each of the above must normalise to, in the same order.
EXPECTED_CLASSES = (
    ['suspension'] * 3
    + ['unknown'] * 4
    + ['deposit'] * 2
    + ['carnivore'] * 5
    + ['grazer'] * 5
    + ['migratory']
    + ['ambiguous'] * 5
)

# Localities chosen for their bytes rather than their geography.
#
# The first is correct UTF-8 and the second is raw cp1252 in the SAME
# response, which is the whole reason the decode is per field: reading the
# document as cp1252 mangles Håkansson, and reading it as UTF-8 with
# errors='replace' turns Thyborøn into Thybor?n.
UTF8_LOCALITY = 'Håkansson'.encode('utf-8')
CP1252_LOCALITY = 'Thyborøn'.encode('cp1252')
# Valid in neither: 0x81 is undefined in cp1252 and not a UTF-8 lead byte.
UNDECODABLE_LOCALITY = b'Ker\x81guelen'


def _record(map_no, **overrides):
    """One result row, with sane defaults for everything not being tested."""
    record = {
        'mapno': str(map_no).encode(),
        'lat': b'54.5',
        'lon': b'-5.6',
        'deltar': b'-120',
        'deltarerr': b'40',
        'reference': b'Reimer and Reimer 2001',
        'locality': b'Strangford Lough',
        'subregion': b'Irish Sea',
        'notes': b'',
        'collectionyear': b'1954',
        'reservoirage': b'405',
        'c14age': b'820',
        'c14err': b'35',
        'labid': b'UB-1234',
        'genus': b'Ostrea',
        'species': b'edulis',
        'feeding': b'suspension',
    }
    record.update(overrides)
    return record


def records():
    """
    A fetch: one row per feeding spelling, plus the encoding and missing-value
    cases. Map numbers are sequential and unique, as the source's are.
    """
    rows = [
        _record(100 + i, feeding=spelling)
        for i, spelling in enumerate(FEEDING_SPELLINGS)
    ]

    rows += [
        # The two encodings, side by side in one fetch.
        _record(200, locality=UTF8_LOCALITY),
        _record(201, locality=CP1252_LOCALITY),
        # And the byte that is neither, which must survive as escaped text
        # rather than becoming a row of question marks.
        _record(202, locality=UNDECODABLE_LOCALITY),
        # A ΔR with no uncertainty. Real, and the reason delta_r_error is
        # nullable: a weighted mean cannot use this row.
        _record(300, deltarerr=b''),
        # The source writes an absent value several ways, and none is a zero.
        _record(301, deltarerr=b'NULL', reservoirage=b'-', notes=b'NULL'),
        # No taxon at all: the join is a LEFT one, and a measurement with an
        # unknown organism is still a measurement.
        _record(302, genus=b'', species=b'', feeding=b''),
    ]
    return rows


# A spelling services/reservoir.py has never seen. Not part of records():
# the test adds it, because what is being pinned down is that a NEW
# vocabulary in the source is counted rather than silently filed as unknown.
NOVEL_FEEDING = b'chemosymbiotic'
