"""
Tests for the marine reservoir ingest: services/reservoir.py's vocabulary and
services/reservoir_ingest.py's extraction, decode, normalisation and hashing.

**No network, and real bytes.** fixtures/calib_marine/query_response.html is
106 rows of what calib.org actually returned on 2026-09-14, inside its own
preamble and table markup -- see that directory's README for what was kept and
why. Every test here runs against those bytes, so the parser is pinned against
the source rather than against a description of it.

The load-bearing tests are the decode pair and the ambiguous-feeding pair.
Both guard against a wrong answer that LOOKS right: a locality silently turned
into question marks, and a compound judgement the source's author deliberately
hedged being filed as one of the two things they declined to choose between.
The header check is the third of that kind -- a source that reordered its
columns would otherwise be read positionally, and latitude would become
longitude with nothing downstream the wiser.
"""
import os

import pytest

from app.services import reservoir
from app.services import reservoir_ingest as ingest


FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'fixtures', 'calib_marine', 'query_response.html')

# The bytes, undecoded. Read once: they are 28 KB and every test wants them.
RAW = open(FIXTURE, 'rb').read()

# The spellings and classes the real database uses, from §2.7 of the plan and
# confirmed against all 2060 rows when this fixture was captured.
EXPECTED_SPELLINGS = 26


def _records():
    return ingest.extract_records(RAW)


def _rows():
    records, declared = _records()
    return ingest.records_to_rows(records, declared=declared)


class TestExtraction:
    """
    Getting the table out of the page, on real markup.

    The response is an HTML document whose cells are not valid in any single
    encoding, so the extraction runs on BYTES and hands each field back
    undecoded -- anything that picked a codec here would have destroyed what
    the next stage exists to recover.
    """

    def test_it_reads_every_row_the_page_declares(self):
        records, declared = _records()
        assert declared == len(records)

    def test_the_columns_are_the_ones_the_query_asked_for(self):
        records, _ = _records()
        assert tuple(records[0]) == ingest.EXPECTED_COLUMNS

    def test_the_fields_come_back_as_undecoded_bytes(self):
        """The contract fetch_records promises. A str here would mean a codec
        had already been chosen for a document that has two."""
        records, _ = _records()
        assert all(isinstance(v, bytes) for v in records[0].values())

    def test_a_response_with_no_table_is_refused(self):
        with pytest.raises(ingest.IngestError) as raised:
            ingest.extract_records(b'<html><body>Query rejected</body></html>')
        assert 'no result table' in str(raised.value)

    def test_a_renamed_column_fails_loudly_rather_than_being_read_positionally(self):
        """
        §11.2's mitigation, and the failure worth preventing is not an outage.
        It is a source that reordered its columns being read by position, so
        that latitude is stored as longitude and every site in the mirror
        moves -- with nothing downstream able to tell.
        """
        tampered = RAW.replace(b'<td>lat</td>', b'<td>latitude</td>', 1)
        with pytest.raises(ingest.IngestError) as raised:
            ingest.extract_records(tampered)
        assert 'columns this ingest asks' in str(raised.value)

    def test_a_short_row_fails_rather_than_being_padded(self):
        # Drop one cell from the first data row.
        first = RAW.index(b'<tr>', RAW.index(b'<td>feeding</td>'))
        end = RAW.index(b'</tr>', first)
        row = RAW[first:end]
        short = row[:row.rindex(b'<td>')]
        with pytest.raises(ingest.IngestError) as raised:
            ingest.extract_records(RAW[:first] + short + RAW[end:])
        assert 'cells' in str(raised.value)


class TestFeedingNormalisation:
    def test_every_spelling_in_the_real_response_folds_to_a_known_class(self):
        records, _ = _records()
        spellings = set()
        for record in records:
            text, _how = ingest.decode_field(record['feeding'])
            spellings.add(text)
        assert len(spellings) == EXPECTED_SPELLINGS

        classes = {reservoir.normalise_feeding(s) for s in spellings}
        assert classes <= set(reservoir.FEEDING_CLASSES)

    def test_nothing_in_the_real_response_is_unrecognised(self):
        """
        The table in services/reservoir.py was written from the plan's prose
        before the source was reachable. This is the check that it is right
        about the actual database: every spelling in it is one the table
        knows, so nothing is quietly filed under 'unknown'.
        """
        _, report = _rows()
        assert report.unrecognised_feeding == {}

    def test_the_class_counts_match_what_was_measured(self):
        """
        Proportions in the fixture are not the database's -- the rows were
        chosen for coverage -- but every class must be represented, because a
        fold that silently produced none of one would pass every other test
        here.
        """
        rows, _ = _rows()
        present = {r.feeding for r in rows}
        assert present == set(reservoir.FEEDING_CLASSES)

    def test_the_truncated_compounds_become_ambiguous_and_stay_there(self):
        """
        The one that matters. 'Deposit or suspe' starts with 'deposit', so a
        table consulted before the prefixes would file a deliberate hedge as a
        deposit feeder -- deciding the question its author declined to decide,
        and doing it silently.
        """
        for prefix in reservoir.AMBIGUOUS_PREFIXES:
            assert reservoir.normalise_feeding(prefix) == 'ambiguous'

    def test_all_five_hedges_are_in_the_real_response(self):
        rows, _ = _rows()
        hedged = {r.feeding_raw for r in rows if r.feeding == 'ambiguous'}
        assert len(hedged) == 5

    def test_an_untruncated_compound_is_still_ambiguous(self):
        """The truncation is the source's column width, not the author's
        meaning. A source that widened the column would send the whole phrase,
        and it is no less a hedge for being complete."""
        assert reservoir.normalise_feeding('Deposit or suspension feeder') == 'ambiguous'

    def test_the_non_breaking_space_spelling_is_not_a_new_class(self):
        """
        The real database contains '\xa0deposit' -- a deposit feeder with a
        non-breaking space in front of it. It folds with the others because
        strip() takes U+00A0, and this test exists because that is a property
        of Python's whitespace definition rather than something obvious.
        """
        assert reservoir.normalise_feeding('\xa0deposit') == 'deposit'
        assert reservoir.is_recognised_feeding('\xa0deposit') is True

    @pytest.mark.parametrize('spelling', ['SUSPENSION', ' suspension ', 'Filter'])
    def test_case_and_whitespace_do_not_make_a_new_class(self, spelling):
        assert reservoir.normalise_feeding(spelling) == 'suspension'

    @pytest.mark.parametrize('empty', [None, '', '   ', 'NULL', 'null'])
    def test_an_absent_habit_is_unknown_rather_than_an_error(self, empty):
        assert reservoir.normalise_feeding(empty) == 'unknown'

    def test_a_novel_spelling_normalises_to_unknown(self):
        assert reservoir.normalise_feeding('chemosymbiotic') == 'unknown'

    def test_but_a_novel_spelling_is_not_RECOGNISED(self):
        """
        The distinction the ingest report is built on. Both a null and a word
        nobody has seen normalise to 'unknown'; only one of them means the
        source has grown a vocabulary, and that one has to be countable.
        """
        assert reservoir.is_recognised_feeding('unknown') is True
        assert reservoir.is_recognised_feeding(None) is True
        assert reservoir.is_recognised_feeding('Deposit or suspe') is True
        assert reservoir.is_recognised_feeding('chemosymbiotic') is False


class TestDecoding:
    def test_both_encodings_are_in_one_real_response(self):
        """
        The reason the decode is per FIELD, on the bytes that prove it. This
        response carries correct multi-byte UTF-8 and raw cp1252 in the same
        document, so neither codec applied to the whole thing is right:
        cp1252 mangles Håkansson, and UTF-8 with errors='replace' turns a
        Danish harbour into Thybor?n.
        """
        records, _ = _records()
        kinds = set()
        for record in records:
            for value in record.values():
                _text, how = ingest.decode_field(value)
                if how in ('utf-8', 'cp1252'):
                    kinds.add(how)
        assert kinds == {'utf-8', 'cp1252'}

    def test_the_cp1252_localities_come_out_right(self):
        rows, _ = _rows()
        localities = {r.locality for r in rows if r.locality}
        assert any('Thyborøn' in name for name in localities)
        assert not any('\ufffd' in name for name in localities)

    def test_the_utf8_references_come_out_right(self):
        rows, _ = _rows()
        references = {r.reference for r in rows if r.reference}
        assert any('Håkansson' in name for name in references)

    def test_the_report_counts_the_fallbacks(self):
        """All 60 rows of the real response that need one are in this fixture,
        so this number is the database's, not the fixture's."""
        _, report = _rows()
        assert report.cp1252_fallbacks == 60
        assert report.undecodable_fields == 0

    def test_a_byte_valid_in_neither_is_kept_rather_than_replaced(self):
        """
        Not present in this response -- every one of its 60 fallbacks is valid
        cp1252 -- but cp1252 has five undefined bytes of its own, so the
        branch is reachable and must not produce U+FFFD.
        """
        text, how = ingest.decode_field(b'Ker\x81guelen')
        assert how == 'escaped'
        assert '\\x81' in text
        assert '\ufffd' not in text

    def test_an_already_decoded_value_passes_through(self):
        assert ingest.decode_field('Strangford') == ('Strangford', 'text')

    def test_nothing_stays_nothing(self):
        assert ingest.decode_field(None) == (None, 'text')


class TestRecordsToRows:
    def test_the_real_response_parses_cleanly(self):
        rows, report = _rows()
        assert report.ok, report.errors
        assert report.row_count == len(rows)

    def test_an_empty_cell_is_none_rather_than_a_string(self):
        """The source writes an empty cell as &nbsp;, which has to become
        nothing at all -- not the string '\xa0', and not a zero."""
        rows, _ = _rows()
        assert any(r.subregion is None for r in rows)
        assert not any(r.subregion == '\xa0' for r in rows)

    def test_the_report_counts_rows_with_no_uncertainty(self):
        """The size of the hole in the database: a weighted mean cannot use
        these, so the lookup will have to exclude them and say so."""
        _, report = _rows()
        assert report.without_error == 3

    def test_a_longitude_off_the_map_is_wrapped_rather_than_refused(self):
        """
        Three rows of the real 2060 carry one: the Chatham Islands at 183.5
        and Fiji at -181. They are real places written with an unwrapped
        longitude, and the source's own map plots them correctly because
        Google Maps wraps too. Refusing them -- which this ingest did until
        it was first run against the whole database -- rejects the entire
        fetch over three good rows.
        """
        rows, report = _rows()
        assert report.ok, report.errors
        assert report.wrapped_longitudes == 3
        assert all(-180 <= r.longitude <= 180 for r in rows)

        chathams = next(r for r in rows if r.map_no == 460)
        assert chathams.longitude == -176.5
        fiji = next(r for r in rows if r.map_no == 1349)
        assert fiji.longitude == 179.0

    def test_a_latitude_off_the_map_is_still_refused(self):
        """
        Longitude is modular and 183.5 has exactly one reading. Latitude is
        not: 91 is not a place expressed oddly, it is a mistake, and wrapping
        it would invent a position.
        """
        records, _ = _records()
        broken = dict(records[0])
        broken['lat'] = b'91'
        _, report = ingest.records_to_rows([broken])
        assert not report.ok
        assert any('not a latitude' in e for e in report.errors)

    def test_the_source_last_changed_is_the_latest_the_rows_carry(self):
        """What identifies the snapshot. Taken from the rows rather than from
        a second query."""
        records, declared = _records()
        rows, report = ingest.records_to_rows(records, declared=declared)
        assert report.source_last_changed is not None
        assert report.source_last_changed.year >= 2020

    def test_the_source_string_is_kept_beside_the_class(self):
        """The normalisation is a judgement, so the evidence for it stays."""
        rows, _ = _rows()
        hedged = [r for r in rows if r.feeding == 'ambiguous']
        assert hedged and all(r.feeding_raw for r in hedged)

    def test_a_row_count_that_disagrees_with_the_page_is_refused(self):
        """
        The page prints its own count above the table. A mismatch means the
        extraction lost rows, and a mirror quietly missing forty measurements
        gives quietly wrong averages.
        """
        records, declared = _records()
        _, report = ingest.records_to_rows(records[:-1], declared=declared)
        assert not report.ok
        assert 'partial mirror' in report.errors[-1]

    @pytest.mark.parametrize('missing', ['mapno', 'lat', 'lon'])
    def test_a_row_with_no_position_or_identifier_fails_the_fetch(self, missing):
        records, _ = _records()
        broken = dict(records[0])
        broken[missing] = b''
        _, report = ingest.records_to_rows([broken])
        assert not report.ok
        assert any(missing in e for e in report.errors)

    def test_a_repeated_map_number_fails_rather_than_picking_a_winner(self):
        """map_no identifies a point and is this app's key within a snapshot.
        Two rows claiming one is a source that has changed shape."""
        records, _ = _records()
        _, report = ingest.records_to_rows([records[0], dict(records[0])])
        assert not report.ok
        assert any('already appeared' in e for e in report.errors)

    def test_every_bad_row_is_reported_not_just_the_first(self):
        records, _ = _records()
        first, second = dict(records[0]), dict(records[1])
        first['lat'] = b''
        second['lat'] = b'91'
        _, report = ingest.records_to_rows([first, second])
        assert len(report.errors) == 2

    def test_an_empty_fetch_is_an_error_rather_than_an_empty_snapshot(self):
        """A mirror of nothing would serve nothing and say it was fine."""
        _, report = ingest.records_to_rows([])
        assert not report.ok

    def test_a_novel_feeding_spelling_is_counted_not_absorbed(self):
        records, _ = _records()
        novel = dict(records[0])
        novel['mapno'] = b'999999'
        novel['feeding'] = b'chemosymbiotic'
        rows, report = ingest.records_to_rows(records + [novel])

        assert report.unrecognised_feeding == {'chemosymbiotic': 1}
        # Still stored, as unknown, with the source's own word beside it --
        # the row is a real measurement and dropping it would be worse.
        stored = next(r for r in rows if r.map_no == 999999)
        assert (stored.feeding, stored.feeding_raw) == ('unknown', 'chemosymbiotic')


class TestContentHash:
    def test_two_parses_of_one_response_hash_the_same(self):
        first, _ = _rows()
        second, _ = _rows()
        assert ingest.content_hash(first) == ingest.content_hash(second)

    def test_row_order_does_not_change_the_hash(self):
        """Two fetches differing only in the order the source returned rows
        describe one database. Sorting by the source's own identifier is what
        makes the hash a property of the data."""
        rows, _ = _rows()
        assert ingest.content_hash(rows) == ingest.content_hash(list(reversed(rows)))

    def test_a_changed_offset_changes_the_hash(self):
        rows, _ = _rows()
        moved = list(rows)
        moved[0] = ingest.ReservoirRow(
            **{**moved[0].__dict__, 'delta_r': (moved[0].delta_r or 0) + 1})
        assert ingest.content_hash(moved) != ingest.content_hash(rows)

    def test_a_changed_position_changes_the_hash(self):
        rows, _ = _rows()
        moved = list(rows)
        moved[0] = ingest.ReservoirRow(
            **{**moved[0].__dict__, 'latitude': moved[0].latitude + 0.5})
        assert ingest.content_hash(moved) != ingest.content_hash(rows)

    def test_a_tidied_up_spelling_does_not(self):
        """
        The hash is over the NUMBERS and the normalised class. A source that
        fixes the capitalisation of 'Deposit' has not changed a measurement,
        and a snapshot that refused to load over it would be refusing for no
        reason.
        """
        records, declared = _records()
        tidied = [
            {**r, 'feeding': r['feeding'].lower()} for r in records
        ]
        first, _ = ingest.records_to_rows(records, declared=declared)
        second, _ = ingest.records_to_rows(tidied, declared=declared)
        assert ingest.content_hash(first) == ingest.content_hash(second)


class TestSnapshotId:
    def test_it_names_the_source_and_the_sources_own_date(self):
        from datetime import datetime
        assert ingest.snapshot_id(
            'calib-marine20', datetime(2026, 5, 11)) == 'calib-marine20-2026-05-11'

    def test_two_installs_ingesting_one_unchanged_database_agree(self):
        """Which is the point: the id is what makes "are we looking at the
        same numbers?" answerable between installations."""
        from datetime import datetime
        changed = datetime(2026, 5, 11)
        assert ingest.snapshot_id('calib-marine20', changed) == ingest.snapshot_id(
            'calib-marine20', changed)


class TestTheQueryIsTheOneTheSchemaNeeds:
    def test_it_asks_for_every_column_the_extraction_expects(self):
        for column in ingest.EXPECTED_COLUMNS:
            assert column in ingest.SOURCE_QUERY

    def test_it_is_a_left_join(self):
        """A details row whose taxon is unknown is still a measurement with a
        position and an offset. An inner join would silently shrink the
        database."""
        assert 'left join taxa' in ingest.SOURCE_QUERY
