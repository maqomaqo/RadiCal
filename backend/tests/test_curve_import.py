"""
Importing a calibration curve: services/curve_import.py and the manager half
of api/curves.py.

The refusals matter more here than anywhere else in this codebase. A date list
is short enough to read after importing it; a curve is 9,500 rows that nobody
will ever check by eye, and a wrong one produces plausible, confident,
wrong calendar ages for every sample calibrated against it -- with nothing in
the output to suggest anything happened. So most of what follows is about
files that must NOT be accepted, and about a stored curve not being able to
change under an analysis that has already used it.
"""
import json
import os

import pytest

from app.services import curve_import, curve_registry

# Every test here stores curves; each cleans up after itself. See conftest.
pytestmark = pytest.mark.usefixtures('remove_stored_curves')

def intcal_bytes():
    """The real IntCal20 file, wherever this run found it -- conftest.py
    resolves the repo copy, and docker-compose mounts it at /app/data. Reusing
    that path rather than recomputing one keeps the test running both ways."""
    with open(os.environ['INTCAL_CURVE_PATH'], 'rb') as fh:
        return fh.read()


def curve_text(rows, header=None, comment=None, delimiter=','):
    """A small curve file, for the cases where a real one is too blunt."""
    lines = []
    if comment:
        lines.extend(f'# {line}' for line in comment.splitlines())
    if header:
        lines.append(delimiter.join(header))
    lines.extend(delimiter.join(f'{v:g}' for v in row) for row in rows)
    return '\n'.join(lines) + '\n'


RISING = [(cal, cal * 0.95, 20) for cal in range(0, 1001, 10)]


class TestParsingWhatShouldBeAccepted:

    def test_a_real_14c_file(self):
        result = curve_import.parse_upload('intcal20.14c', intcal_bytes())
        assert result.ok, result.errors
        assert len(result.points) == 9501
        assert result.points[0][0] == 0 and result.points[-1][0] == 55000

    def test_oldest_first_files_are_sorted_not_refused(self):
        """The IntCal files are written oldest-first. That is a convention,
        not a mistake, so it is normalised and noted rather than rejected."""
        result = curve_import.parse_upload('intcal20.14c', intcal_bytes())
        assert any('oldest-first' in note for note in result.notes)
        cals = [p[0] for p in result.points]
        assert cals == sorted(cals)

    def test_five_column_14c_reads_the_first_three(self):
        rows = [(cal, cal * 0.95, 20, 5.5, 1.2) for cal in range(0, 101, 10)]
        result = curve_import.parse_curve(curve_text(rows))
        assert result.ok, result.errors
        assert result.points[1] == (10, 9.5, 20)

    def test_headed_csv_in_any_column_order(self):
        rows = [(20, cal * 0.95, cal) for cal in range(0, 101, 10)]
        text = curve_text(rows, header=['Sigma', '14C age', 'Cal BP'])
        result = curve_import.parse_curve(text)
        assert result.ok, result.errors
        assert result.points[1] == (10, 9.5, 20)
        assert any('Read columns by name' in note for note in result.notes)

    def test_tab_separated(self):
        result = curve_import.parse_curve(curve_text(RISING, delimiter='\t'))
        assert result.ok, result.errors
        assert result.source_format == 'tsv'

    def test_bc_ad_calendar_is_converted(self):
        """A curve authored in a spreadsheet usually has BC/AD years. There is
        no year zero, so 1 BC is 1950 cal BP and AD 1 is 1949."""
        rows = [(1950, 0, 20), (1, 1949 * 0.95, 20), (-1, 1950 * 0.95, 20)]
        result = curve_import.parse_curve(curve_text(rows), calendar='bc_ad')
        assert result.ok, result.errors
        assert [p[0] for p in result.points] == [0, 1949, 1950]

    def test_name_and_citation_are_offered_from_the_header(self):
        """The .14c header is where a curve's citation actually lives -- the
        difference between a stored curve that cites its source and one
        labelled 'intcal13.14c'."""
        result = curve_import.parse_upload('whatever.14c', intcal_bytes())
        assert result.suggested_name == 'IntCal20'
        assert result.suggested_reference == 'Reimer et al. 2020'


class TestParsingWhatMustBeRefused:

    def _errors(self, **kwargs):
        result = curve_import.parse_curve(**kwargs)
        assert not result.ok
        assert result.points == []          # refuse() drops a partial curve
        return ' '.join(result.errors)

    def test_non_numeric_cell(self):
        rows = curve_text(RISING).splitlines()
        rows[3] = '30,abc,20'
        assert 'not a number' in self._errors(text='\n'.join(rows))

    def test_non_positive_sigma(self):
        text = curve_text([(0, 0, 20), (10, 9, 0), (20, 19, 20)])
        assert 'must be positive' in self._errors(text=text)

    def test_duplicate_calendar_age(self):
        text = curve_text([(0, 0, 20), (10, 9, 20), (10, 12, 20)])
        assert 'duplicated calendar age' in self._errors(text=text)

    def test_post_bomb_negative_cal_bp(self):
        """Post-bomb curves need an F14C likelihood this app does not have.
        Refusing says so; accepting would calibrate them as ordinary ages."""
        text = curve_text([(-5, -100, 10), (0, 0, 20), (10, 9, 20)])
        message = self._errors(text=text)
        assert 'F14C' in message and 'negative' in message

    def test_f14c_in_the_radiocarbon_column(self):
        text = curve_text([(0, 1.0, 0.003), (10, 0.99, 0.003), (20, 0.98, 0.003)])
        assert 'F14C' in self._errors(text=text)

    def test_too_few_points_to_interpolate(self):
        assert 'at least two points' in self._errors(text=curve_text([(0, 100, 20)]))

    def test_headed_csv_missing_a_column(self):
        text = curve_text([(0, 100), (10, 110)], header=['Cal BP', '14C age'])
        message = self._errors(text=text)
        assert 'error' in message

    def test_empty_file(self):
        assert 'no data rows' in self._errors(text='# only a comment\n\n')

    def test_a_wall_of_errors_stops_rather_than_listing_everything(self):
        text = '\n'.join('x,y,z' for _ in range(500))
        result = curve_import.parse_curve(text)
        assert not result.ok
        assert len(result.errors) <= curve_import.MAX_REPORTED_ERRORS + 1


class TestPreviewWritesNothing:

    def test_preview_reports_without_storing(self, client):
        before = client.get('/api/curves').json()['data']['total']
        response = client.post(
            '/api/curves/import/preview',
            files={'file': ('intcal20.14c', intcal_bytes(), 'text/plain')},
        )
        assert response.status_code == 200
        data = response.json()['data']
        assert data['ok'] is True
        assert data['summary']['data_points'] == 9501
        assert len(data['preview']) > 0
        assert data['suggested_id'] == 'intcal20'
        assert client.get('/api/curves').json()['data']['total'] == before

    def test_an_unreadable_file_is_still_a_successful_request(self, client):
        """The upload worked; its answer is what is wrong with the file. A 4xx
        here would make 'the request broke' indistinguishable from 'row 12 has
        a letter in the age column'."""
        response = client.post(
            '/api/curves/import/preview',
            files={'file': ('bad.csv', b'0,0,20\n10,9,-1\n', 'text/plain')},
        )
        assert response.status_code == 200
        assert response.json()['data']['ok'] is False
        assert response.json()['data']['errors']

    def test_an_empty_upload_is_a_bad_request(self, client):
        response = client.post(
            '/api/curves/import/preview',
            files={'file': ('empty.14c', b'', 'text/plain')},
        )
        assert response.status_code == 400


def store_curve(client, curve_id, name, text=None, **meta):
    body = {'name': name, 'type': 'terrestrial', 'sample_type': 'terrestrial'}
    body.update(meta)
    return client.post(
        '/api/curves',
        files={'file': (f'{curve_id}.14c', (text or curve_text(RISING)).encode(), 'text/plain')},
        data={'metadata': json.dumps(body), 'curve_id': curve_id},
    )


class TestStoringACurve:

    def test_stored_curve_joins_the_list_and_can_be_calibrated_against(self, client):
        response = store_curve(client, 'testcurve1', 'Test Curve 1')
        assert response.status_code == 201, response.text
        spec = response.json()['data']
        assert spec['data_points'] == len(RISING)
        assert spec['kind'] == 'imported'

        listed = [c['id'] for c in client.get('/api/curves').json()['data']['items']]
        assert 'testcurve1' in listed

        # The point of all of it: a date can now be calibrated against it.
        result = client.post('/api/analysis/calibrate', json={
            'sample_name': 'S1', 'age_bp': 500, 'error_1sigma': 25,
            'material_type': 'wood', 'curve_id': 'testcurve1', 'save': False,
        })
        assert result.status_code == 200, result.text

    def test_the_id_must_be_usable_in_a_url_and_a_database_key(self, client):
        response = store_curve(client, 'Not A Slug!', 'Bad Slug')
        assert response.status_code == 422
        assert 'not a usable curve id' in response.text

    def test_an_id_already_taken_is_refused_rather_than_overwriting(self, client):
        store_curve(client, 'testcurve2', 'Test Curve 2')
        again = store_curve(client, 'testcurve2', 'Test Curve 2 corrected')
        assert again.status_code == 409
        assert 'immutable' in again.text

    def test_a_builtin_name_cannot_be_shadowed(self, client):
        assert store_curve(client, 'intcal20', 'My IntCal20').status_code == 409

    def test_an_alias_that_names_two_curves_is_refused(self, client):
        store_curve(client, 'testcurve3', 'Test Curve 3', aliases=['TC3'])
        clash = store_curve(client, 'testcurve4', 'Test Curve 4', aliases=['tc-3'])
        assert clash.status_code == 409
        assert 'ambiguous' in clash.text

    def test_an_unreadable_file_is_not_stored(self, client):
        response = client.post(
            '/api/curves',
            files={'file': ('bad.14c', b'0,0,20\n10,9,0\n', 'text/plain')},
            data={'metadata': json.dumps({'name': 'Bad'}), 'curve_id': 'testbad1'},
        )
        assert response.status_code == 422
        assert client.get('/api/curves/testbad1').status_code == 404


class TestStoredCurvesAreReReadTheSameWay:

    def test_the_stored_column_mapping_is_used_rather_than_redetected(self, client):
        """
        A stored curve records which column held what, and re-reading it uses
        that record. Otherwise the curve would depend on whatever the
        detection code guesses at the time it is next loaded -- which is the
        thing `content_sha256` exists to catch, and would rather not have to.
        """
        text = curve_text(
            [(20, cal * 0.95, cal) for cal in range(0, 101, 10)],
            header=['Sigma', '14C age', 'Cal BP'],
        )
        assert store_curve(client, 'testcurve15', 'Test Curve 15', text=text).status_code == 201

        from app.database import SessionLocal
        from app.models import StoredCalibrationCurve
        with SessionLocal() as db:
            stored = db.get(StoredCalibrationCurve, 'testcurve15')
            assert stored.parse_options['columns'] == {'cal_bp': 2, 'c14_age': 1, 'error': 0}

        curve_registry.invalidate('testcurve15')
        curve = curve_registry.resolve('testcurve15')
        assert list(curve.cal_ages[:3]) == [0, 10, 20]
        assert list(curve.c14_errors[:3]) == [20, 20, 20]


class TestImportedCurvesAreFoundByName:

    def test_a_csv_naming_an_imported_curve_imports(self, client):
        store_curve(client, 'intcal13', 'IntCal13', aliases=['IntCal13.14c'])
        assert curve_registry.resolve_alias('IntCal13') == 'intcal13'
        assert curve_registry.resolve_alias('IntCal13.14c') == 'intcal13'

        from app.services.oxcal_script import _curve_id
        assert _curve_id('=IntCal13') == 'intcal13'

    def test_a_curve_still_missing_is_still_refused(self, client):
        from app.services.date_import import _curve
        errors = []
        assert _curve('IntCal09', 1, errors) is None
        assert 'IntCal09' in errors[0]


class TestCurveDataCannotChangeUnderAnAnalysis:

    def test_patch_edits_the_description_and_not_the_numbers(self, client):
        store_curve(client, 'testcurve5', 'Test Curve 5')
        before = client.get('/api/curves/testcurve5').json()['data']

        response = client.patch('/api/curves/testcurve5', json={
            'description': 'Corrected description',
            # Not a field on the update schema. Silently ignored rather than
            # applied -- the assertion below is what proves it.
            'data_points': 3,
        })
        assert response.status_code == 200
        after = response.json()['data']
        assert after['description'] == 'Corrected description'
        assert after['content_sha256'] == before['content_sha256']
        assert after['data_points'] == before['data_points']

    def test_a_rename_is_visible_immediately(self, client):
        store_curve(client, 'testcurve6', 'Test Curve 6')
        client.patch('/api/curves/testcurve6', json={'name': 'Renamed'})
        listed = {c['id']: c['name'] for c in client.get('/api/curves').json()['data']['items']}
        assert listed['testcurve6'] == 'Renamed'

    def test_stored_data_that_no_longer_matches_its_hash_is_refused(self, client):
        """
        The check that makes storing the source text rather than parsed rows
        safe: a parser change years from now must not move a curve underneath
        an analysis run against it.
        """
        store_curve(client, 'testcurve7', 'Test Curve 7')
        from app.database import SessionLocal
        from app.models import StoredCalibrationCurve
        with SessionLocal() as db:
            row = db.get(StoredCalibrationCurve, 'testcurve7')
            row.source_text = curve_text([(0, 0, 20), (10, 500, 20), (20, 900, 20)])
            db.commit()
        curve_registry.invalidate('testcurve7')

        with pytest.raises(ValueError) as exc:
            curve_registry.resolve('testcurve7')
        assert 'does not match the data it was stored with' in str(exc.value)

    def test_builtin_curves_are_not_editable(self, client):
        assert client.patch('/api/curves/intcal20', json={'name': 'Mine'}).status_code == 403
        assert client.delete('/api/curves/shcal20').status_code == 403


class TestLifecycle:

    def test_an_unused_curve_can_be_deleted(self, client):
        store_curve(client, 'testcurve8', 'Test Curve 8')
        assert client.delete('/api/curves/testcurve8').status_code == 200
        assert client.get('/api/curves/testcurve8').status_code == 404

    def test_a_curve_in_use_is_refused_and_the_refusal_names_what_uses_it(self, client):
        store_curve(client, 'testcurve9', 'Test Curve 9')
        run = client.post('/api/analysis/calibrate', json={
            'sample_name': 'S1', 'age_bp': 500, 'error_1sigma': 25,
            'material_type': 'wood', 'curve_id': 'testcurve9', 'save': True,
        })
        assert run.status_code == 200, run.text

        response = client.delete('/api/curves/testcurve9')
        assert response.status_code == 409
        detail = response.json()['detail']
        assert detail['usage']['analysis_ids']
        assert 'Archive it instead' in detail['message']

    def test_archiving_hides_a_curve_without_withdrawing_it(self, client):
        store_curve(client, 'testcurve10', 'Test Curve 10')
        assert client.delete('/api/curves/testcurve10?archive=true').status_code == 200

        listed = [c['id'] for c in client.get('/api/curves').json()['data']['items']]
        assert 'testcurve10' not in listed
        with_archived = [
            c['id'] for c in
            client.get('/api/curves?include_archived=true').json()['data']['items']
        ]
        assert 'testcurve10' in with_archived

        # Still resolvable: an analysis that used it has to still rerun.
        assert len(curve_registry.resolve('testcurve10').cal_ages) == len(RISING)
        rerun = client.post('/api/analysis/calibrate', json={
            'sample_name': 'S1', 'age_bp': 500, 'error_1sigma': 25,
            'material_type': 'wood', 'curve_id': 'testcurve10', 'save': False,
        })
        assert rerun.status_code == 200

    def test_restore_brings_it_back(self, client):
        store_curve(client, 'testcurve11', 'Test Curve 11')
        client.delete('/api/curves/testcurve11?archive=true')
        assert client.post('/api/curves/testcurve11/restore').status_code == 200
        listed = [c['id'] for c in client.get('/api/curves').json()['data']['items']]
        assert 'testcurve11' in listed

    def test_usage_is_reportable_before_trying_to_delete(self, client):
        store_curve(client, 'testcurve12', 'Test Curve 12')
        usage = client.get('/api/curves/testcurve12/usage').json()['data']
        assert usage['in_use'] is False
        assert usage['analysis_ids'] == []


class TestExport:

    def test_an_imported_curve_comes_back_exactly_as_uploaded(self, client):
        text = curve_text(RISING, comment='Test Curve 13\nSomeone et al. 2026')
        store_curve(client, 'testcurve13', 'Test Curve 13', text=text)
        response = client.get('/api/curves/testcurve13/export')
        assert response.status_code == 200
        assert response.text == text

    def test_a_builtin_is_rendered_into_the_same_format(self, client):
        response = client.get('/api/curves/marine20/export')
        assert response.status_code == 200
        assert response.text.startswith('#')
        rows = [line for line in response.text.splitlines() if not line.startswith('#')]
        assert len(rows) == 5501


class TestRequestValidation:

    def test_an_imported_curve_is_accepted_on_a_date(self, client):
        store_curve(client, 'testcurve14', 'Test Curve 14')
        response = client.post('/api/analysis/calibrate', json={
            'sample_name': 'S1', 'age_bp': 400, 'error_1sigma': 20,
            'material_type': 'wood', 'curve_id': 'testcurve14', 'save': False,
        })
        assert response.status_code == 200, response.text

    def test_an_unknown_curve_is_still_rejected(self, client):
        """The guarantee that predates imported curves and must survive them:
        a typo'd curve_id fails loudly rather than calibrating against
        IntCal20 and saying nothing."""
        response = client.post('/api/analysis/calibrate', json={
            'sample_name': 'S1', 'age_bp': 400, 'error_1sigma': 20,
            'material_type': 'wood', 'curve_id': 'marine2020', 'save': False,
        })
        assert response.status_code == 422
        assert 'marine2020' in response.text
