"""
Tests for the marine reservoir ingest: services/reservoir.py's vocabulary and
services/reservoir_ingest.py's decode, normalisation and hashing.

No network, by construction. The fixture in fixtures/calib_marine is
SYNTHESISED rather than captured -- calib.org was unreachable from where this
was written -- so what these tests pin down is everything downstream of the
fetch, which is everything that is actually written. See that directory's
README and reservoir_ingest.fetch_records for what is deliberately missing
and why.

The load-bearing tests here are the decode pair and the ambiguous-feeding
pair. Both guard against a wrong answer that LOOKS right: a locality silently
turned into question marks, and a compound judgement the source's author
deliberately hedged being filed as one of the two things they declined to
choose between.
"""
import pytest

from app.services import reservoir
from app.services import reservoir_ingest as ingest
from tests.fixtures.calib_marine import records as fixture


class TestFeedingNormalisation:
    def test_every_spelling_the_source_uses_folds_to_a_known_class(self):
        got = [reservoir.normalise_feeding(s.decode()) for s in fixture.FEEDING_SPELLINGS]
        assert got == fixture.EXPECTED_CLASSES
        assert set(got) <= set(reservoir.FEEDING_CLASSES)

    def test_all_twenty_five_spellings_are_covered(self):
        """The count is from §2.7, measured against the real database. If the
        fixture grows a spelling, the expectation has to grow with it."""
        assert len(fixture.FEEDING_SPELLINGS) == 25
        assert len(fixture.EXPECTED_CLASSES) == 25

    def test_the_truncated_compounds_become_ambiguous_and_stay_there(self):
        """
        The one that matters. 'Deposit or suspe' starts with 'deposit', so a
        table consulted before the prefixes would file a deliberate hedge as a
        deposit feeder -- deciding the question its author declined to decide,
        and doing it silently.
        """
        for prefix in reservoir.AMBIGUOUS_PREFIXES:
            assert reservoir.normalise_feeding(prefix) == 'ambiguous'

    def test_an_untruncated_compound_is_still_ambiguous(self):
        """The truncation is the source's column width, not the author's
        meaning. A source that widened the column would send the whole phrase,
        and it is no less a hedge for being complete."""
        assert reservoir.normalise_feeding('Deposit or suspension feeder') == 'ambiguous'

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
    def test_utf8_and_cp1252_in_one_fetch(self):
        """
        The reason the decode is per FIELD. One response carries both, so
        neither codec applied to the whole document is right: cp1252 mangles
        Håkansson and UTF-8 with errors='replace' turns a Danish harbour into
        Thybor?n.
        """
        assert ingest.decode_field(fixture.UTF8_LOCALITY) == ('Håkansson', 'utf-8')
        assert ingest.decode_field(fixture.CP1252_LOCALITY) == ('Thyborøn', 'cp1252')

    def test_a_byte_valid_in_neither_is_kept_rather_than_replaced(self):
        text, how = ingest.decode_field(fixture.UNDECODABLE_LOCALITY)
        assert how == 'escaped'
        # Recoverable, and visibly odd. What it must NOT be is U+FFFD, which
        # loses the byte and looks like a rendering problem.
        assert '\\x81' in text
        assert '�' not in text

    def test_an_already_decoded_value_passes_through(self):
        assert ingest.decode_field('Strangford') == ('Strangford', 'text')

    def test_nothing_stays_nothing(self):
        assert ingest.decode_field(None) == (None, 'text')


class TestRecordsToRows:
    def test_the_fixture_parses_cleanly(self):
        rows, report = ingest.records_to_rows(fixture.records())
        assert report.ok, report.errors
        assert report.row_count == len(rows) == len(fixture.records())

    def test_the_report_counts_the_cp1252_fallbacks(self):
        _, report = ingest.records_to_rows(fixture.records())
        # One locality in the fixture is raw cp1252; the undecodable one is
        # counted separately rather than lumped in with it.
        assert report.cp1252_fallbacks == 1
        assert report.undecodable_fields == 1

    def test_the_report_counts_rows_with_no_uncertainty(self):
        """The size of the hole in the database: a weighted mean cannot use
        these, so the lookup will have to exclude them and say so."""
        _, report = ingest.records_to_rows(fixture.records())
        assert report.without_error == 2

    def test_a_novel_feeding_spelling_is_counted_not_absorbed(self):
        records = fixture.records()
        records.append({**records[0], 'mapno': b'999', 'feeding': fixture.NOVEL_FEEDING})
        rows, report = ingest.records_to_rows(records)

        assert report.ok, report.errors
        assert report.unrecognised_feeding == {'chemosymbiotic': 1}
        # It is still stored, as unknown, with the source's own word beside it
        # -- the row is a real measurement and dropping it would be worse.
        novel = next(r for r in rows if r.map_no == 999)
        assert (novel.feeding, novel.feeding_raw) == ('unknown', 'chemosymbiotic')

    def test_the_source_string_is_kept_beside_the_class(self):
        """The normalisation is a judgement, so the evidence for it stays."""
        rows, _ = ingest.records_to_rows(fixture.records())
        hedged = [r for r in rows if r.feeding == 'ambiguous']
        assert len(hedged) == 5
        assert all(r.feeding_raw for r in hedged)

    def test_the_several_spellings_of_absent_are_all_none(self):
        rows, _ = ingest.records_to_rows(fixture.records())
        row = next(r for r in rows if r.map_no == 301)
        assert row.delta_r_error is None
        assert row.reservoir_age is None
        assert row.notes is None
        # And not zero, which is a measurement.
        assert row.delta_r == -120

    def test_a_row_with_no_taxon_survives_the_left_join(self):
        rows, _ = ingest.records_to_rows(fixture.records())
        row = next(r for r in rows if r.map_no == 302)
        assert (row.genus, row.species) == (None, None)
        assert row.feeding == 'unknown'
        assert row.delta_r == -120

    @pytest.mark.parametrize('missing', ['mapno', 'lat', 'lon'])
    def test_a_row_with_no_position_or_identifier_fails_the_fetch(self, missing):
        records = [dict(fixture.records()[0])]
        records[0][missing] = b''
        _, report = ingest.records_to_rows(records)
        assert not report.ok
        assert any(missing in e for e in report.errors)

    def test_a_position_off_the_earth_fails_the_fetch(self):
        records = [dict(fixture.records()[0])]
        records[0]['lat'] = b'95'
        _, report = ingest.records_to_rows(records)
        assert not report.ok
        assert any('not on the earth' in e for e in report.errors)

    def test_a_repeated_map_number_fails_rather_than_picking_a_winner(self):
        """map_no identifies a point and is this app's key within a snapshot.
        Two rows claiming one is a source that has changed shape."""
        records = fixture.records()
        records.append({**records[0]})
        _, report = ingest.records_to_rows(records)
        assert not report.ok
        assert any('already appeared' in e for e in report.errors)

    def test_every_bad_row_is_reported_not_just_the_first(self):
        records = [dict(r) for r in fixture.records()[:3]]
        records[0]['lat'] = b''
        records[1]['lon'] = b'999'
        _, report = ingest.records_to_rows(records)
        assert len(report.errors) == 2

    def test_an_empty_fetch_is_an_error_rather_than_an_empty_snapshot(self):
        """A mirror of nothing would serve nothing and say it was fine."""
        _, report = ingest.records_to_rows([])
        assert not report.ok


class TestContentHash:
    def test_two_parses_of_one_fetch_hash_the_same(self):
        first, _ = ingest.records_to_rows(fixture.records())
        second, _ = ingest.records_to_rows(fixture.records())
        assert ingest.content_hash(first) == ingest.content_hash(second)

    def test_row_order_does_not_change_the_hash(self):
        """Two fetches differing only in the order the source returned rows
        describe one database. Sorting by the source's own identifier is what
        makes the hash a property of the data."""
        rows, _ = ingest.records_to_rows(fixture.records())
        assert ingest.content_hash(rows) == ingest.content_hash(list(reversed(rows)))

    def test_a_changed_offset_changes_the_hash(self):
        rows, _ = ingest.records_to_rows(fixture.records())
        moved = list(rows)
        moved[0] = ingest.ReservoirRow(**{**moved[0].__dict__, 'delta_r': -121})
        assert ingest.content_hash(moved) != ingest.content_hash(rows)

    def test_a_tidied_up_spelling_does_not(self):
        """
        The hash is over the NUMBERS and the normalised class. A source that
        fixes the capitalisation of 'Deposit' has not changed a measurement,
        and a snapshot that refused to load over it would be refusing for no
        reason.
        """
        records = fixture.records()
        tidied = [
            {**r, 'feeding': r['feeding'].lower()} for r in records
        ]
        first, _ = ingest.records_to_rows(records)
        second, _ = ingest.records_to_rows(tidied)
        assert ingest.content_hash(first) == ingest.content_hash(second)

    def test_a_changed_position_changes_the_hash(self):
        rows, _ = ingest.records_to_rows(fixture.records())
        moved = list(rows)
        moved[0] = ingest.ReservoirRow(**{**moved[0].__dict__, 'latitude': 54.6})
        assert ingest.content_hash(moved) != ingest.content_hash(rows)


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


class TestTheFetchIsHonestAboutBeingUnwritten:
    def test_it_refuses_rather_than_returning_something_plausible(self):
        """
        fetch_records is deliberately unimplemented: calib.org was unreachable
        when this was written, and a parser guessed at a response shape would
        be confidently wrong about numbers that move published dates. What it
        must never do is return an empty list, which a caller would store as a
        snapshot of nothing.
        """
        with pytest.raises(ingest.IngestUnavailable) as raised:
            ingest.fetch_records()
        assert 'not implemented' in str(raised.value)
