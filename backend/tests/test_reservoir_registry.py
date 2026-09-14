"""
The marine reservoir registry and its API: services/reservoir_registry.py and
api/reservoir.py.

Two things are being pinned down, and neither is "the code runs".

**Verification.** The whole argument for mirroring this database locally is
that a published ΔR stays reproducible. A mirror that quietly served numbers
other than the ones an analysis was computed against would defeat that
silently, which is worse than an outage -- so a snapshot that does not hash to
its recorded value must refuse to serve at all, rather than serve with a
warning nobody reads.

**Ownership.** An ingested snapshot is private to whoever ran the ingest, on
the same argument api/curves.py makes about an imported curve: a reservoir
table somebody pulled down this morning has nobody vouching for it. What is
tested is that there is NO route to another account's snapshot -- not listing,
not reading, not deleting -- because each of those is a separate way in and
each got the check separately. Mirrors test_curve_privacy.py.
"""
import os

import pytest

from app.models import MarineReservoirPoint, MarineReservoirSnapshot
from app.services import reservoir_registry
from app.services import reservoir_ingest as ingest


FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'fixtures', 'calib_marine', 'query_response.html')


def _fixture_rows():
    """The captured response, parsed. Real bytes -- see the fixture README."""
    with open(FIXTURE, 'rb') as handle:
        records, declared = ingest.extract_records(handle.read())
    return ingest.records_to_rows(records, declared=declared)


def _client_for(email):
    """A signed-in client for `email`, registering the account if it is new.

    Session-scoped below for the reason test_curve_privacy gives: the suite
    shares one database, so registering per test would 409 on the second."""
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as bare:
        response = bare.post('/api/auth/register',
                             json={'email': email, 'password': 'a good enough password'})
        if response.status_code == 409:
            response = bare.post('/api/auth/login',
                                 json={'email': email, 'password': 'a good enough password'})
        assert response.status_code in (200, 201), response.text
    token = response.json()['data']['token']
    return TestClient(app, headers={'Authorization': f'Bearer {token}'})


@pytest.fixture(scope='session')
def ada():
    return _client_for('ada-reservoir@radical.example.com')


@pytest.fixture(scope='session')
def grace():
    """A second account, so "another user" is a real one rather than a mock."""
    return _client_for('grace-reservoir@radical.example.com')


def _user_id(client):
    return client.get('/api/auth/me').json()['data']['id']


def _store(owner_id, snapshot_suffix, rows=None):
    """
    Put a snapshot in the database directly.

    Through reservoir_ingest.store rather than the API, because the API's
    create route reaches the network and the suite must not. What is under
    test here is the registry over a stored snapshot, and the rows are the
    captured response's own.
    """
    from datetime import datetime
    from app.database import SessionLocal

    if rows is None:
        rows, report = _fixture_rows()
    else:
        report = ingest.IngestReport()
        report.row_count = len(rows)
    report.source_last_changed = datetime(2026, 5, 11)

    with SessionLocal() as db:
        return ingest.store(
            db, rows, report,
            source='calib.org/marine (test)',
            created_by=owner_id,
            citation='Reimer and Reimer 2001',
            source_slug=f'test-{snapshot_suffix}',
        )


class TestVerification:
    def test_a_stored_snapshot_resolves_to_what_went_in(self, ada):
        snapshot = _store(_user_id(ada), 'roundtrip')
        rows = reservoir_registry.resolve(snapshot.id)
        assert len(rows) == snapshot.record_count
        assert ingest.content_hash(rows) == snapshot.content_sha256

    def test_a_tampered_snapshot_refuses_to_serve(self, ada):
        """
        The load-bearing one. A point edited in the database after the fact
        must make the whole snapshot unusable -- not usable-with-a-warning,
        because the numbers are what a published ΔR was computed from and
        quietly different ones are the failure this design exists to prevent.
        """
        from app.database import SessionLocal

        snapshot = _store(_user_id(ada), 'tampered')
        with SessionLocal() as db:
            point = (
                db.query(MarineReservoirPoint)
                .filter(MarineReservoirPoint.snapshot_id == snapshot.id)
                .first()
            )
            point.delta_r = (point.delta_r or 0) + 1
            db.commit()

        with pytest.raises(reservoir_registry.SnapshotCorrupt) as raised:
            reservoir_registry.resolve(snapshot.id)
        assert 'cannot be reproduced' in str(raised.value)

    def test_the_api_reports_a_tampered_snapshot_rather_than_hiding_it(self, ada):
        from app.database import SessionLocal

        snapshot = _store(_user_id(ada), 'tampered-api')
        with SessionLocal() as db:
            point = (
                db.query(MarineReservoirPoint)
                .filter(MarineReservoirPoint.snapshot_id == snapshot.id)
                .first()
            )
            point.latitude = point.latitude + 0.5
            db.commit()

        data = ada.get(f'/api/reservoir/snapshots/{snapshot.id}').json()['data']
        assert data['verified'] is False
        assert 'problem' in data

    def test_an_unknown_snapshot_is_not_found(self):
        with pytest.raises(reservoir_registry.SnapshotNotFound):
            reservoir_registry.resolve('no-such-snapshot-2026-01-01')


class TestOwnership:
    def test_a_snapshot_is_absent_from_another_account_s_list(self, ada, grace):
        snapshot = _store(_user_id(ada), 'adas-private')
        ids = [s['id'] for s in grace.get('/api/reservoir/snapshots').json()['data']['items']]
        assert snapshot.id not in ids

    def test_the_owner_still_sees_their_own(self, ada):
        snapshot = _store(_user_id(ada), 'adas-visible')
        ids = [s['id'] for s in ada.get('/api/reservoir/snapshots').json()['data']['items']]
        assert snapshot.id in ids

    def test_reading_another_account_s_snapshot_is_a_404_not_a_403(self, ada, grace):
        """A 403 would confirm the id exists, which is enough to learn what
        somebody else has ingested."""
        snapshot = _store(_user_id(ada), 'adas-unreadable')
        assert grace.get(f'/api/reservoir/snapshots/{snapshot.id}').status_code == 404

    def test_deleting_another_account_s_snapshot_is_a_404(self, ada, grace):
        snapshot = _store(_user_id(ada), 'adas-undeletable')
        assert grace.delete(f'/api/reservoir/snapshots/{snapshot.id}').status_code == 404
        # And it is still there afterwards.
        assert ada.get(f'/api/reservoir/snapshots/{snapshot.id}').status_code == 200

    def test_the_registry_scopes_listing_by_owner(self, ada, grace):
        snapshot = _store(_user_id(ada), 'scoped')
        mine = [s.id for s in reservoir_registry.list_specs(owner_id=_user_id(ada))]
        theirs = [s.id for s in reservoir_registry.list_specs(owner_id=_user_id(grace))]
        assert snapshot.id in mine
        assert snapshot.id not in theirs

    def test_scope_any_is_how_an_internal_caller_says_it_means_everyone(self, ada):
        snapshot = _store(_user_id(ada), 'scope-any')
        every = [s.id for s in reservoir_registry.list_specs(
            owner_id=reservoir_registry.SCOPE_ANY)]
        assert snapshot.id in every

    def test_an_anonymous_request_is_refused(self, anonymous_client):
        assert anonymous_client.get('/api/reservoir/snapshots').status_code == 401


class TestDeletion:
    def test_an_unused_snapshot_can_be_deleted(self, ada):
        snapshot = _store(_user_id(ada), 'disposable')
        assert ada.delete(f'/api/reservoir/snapshots/{snapshot.id}').status_code == 200
        assert ada.get(f'/api/reservoir/snapshots/{snapshot.id}').status_code == 404

    def test_a_snapshot_a_date_cites_cannot_be_deleted(self, ada):
        """
        The rule that keeps a published ΔR reproducible. Unlike a stored curve
        there is no archive flag to fall back on -- there are no pickers for a
        snapshot to clutter -- so the refusal is the whole mechanism.
        """
        from app.database import SessionLocal
        from app.models import Project, ProjectSample

        snapshot = _store(_user_id(ada), 'cited')
        with SessionLocal() as db:
            project = Project(name='Cites a snapshot', owner_id=_user_id(ada))
            db.add(project)
            db.flush()
            db.add(ProjectSample(
                project_id=project.id,
                sample_name='NZ-7756',
                age_bp=970, error_1sigma=47,
                material_type='marine shell', sample_type='marine',
                curve_id='marine20',
                delta_r=-7, delta_r_error=45,
                delta_r_source={'snapshot_id': snapshot.id, 'method': 'nearest_n'},
            ))
            db.commit()

        assert reservoir_registry.in_use(snapshot.id) == 1

        response = ada.delete(f'/api/reservoir/snapshots/{snapshot.id}')
        assert response.status_code == 409
        assert snapshot.id in response.json()['detail']
        assert 'reproduced' in response.json()['detail']

    def test_a_snapshot_nothing_cites_reports_no_use(self, ada):
        snapshot = _store(_user_id(ada), 'uncited')
        assert reservoir_registry.in_use(snapshot.id) == 0


class TestTheIngestRouteDoesNotReachTheNetworkInTests:
    """
    The suite must never call calib.org: it is somebody else's server, and a
    test run that hammered it would be exactly the behaviour the one-query
    rule exists to prevent. So the fetch is stubbed here, and what is checked
    is how the route treats each kind of failure.
    """

    def test_an_unreachable_host_is_a_503(self, ada, monkeypatch):
        def unreachable(*_a, **_k):
            raise ingest.IngestUnavailable('calib.org could not be reached')
        monkeypatch.setattr(ingest, 'fetch_records', unreachable)

        response = ada.post('/api/reservoir/snapshots')
        assert response.status_code == 503

    def test_a_response_that_is_not_this_database_is_a_502(self, ada, monkeypatch):
        """Different from unreachable, and the difference matters: one is a
        network to try again on, the other is a source that has changed shape
        and needs a person to look at it."""
        def wrong_shape(*_a, **_k):
            raise ingest.IngestError('The result table does not have the columns')
        monkeypatch.setattr(ingest, 'fetch_records', wrong_shape)

        response = ada.post('/api/reservoir/snapshots')
        assert response.status_code == 502

    def test_a_successful_fetch_stores_and_reports(self, ada, monkeypatch):
        """The whole route, on the captured response, with no network."""
        with open(FIXTURE, 'rb') as handle:
            raw = handle.read()
        monkeypatch.setattr(
            ingest, 'fetch_records', lambda *a, **k: ingest.extract_records(raw))

        response = ada.post('/api/reservoir/snapshots')
        assert response.status_code == 200, response.text
        data = response.json()['data']
        assert data['snapshot']['kind'] == 'ingested'
        assert data['report']['row_count'] == data['snapshot']['record_count']
        # The counts a person needs to judge the SOURCE, not just the fetch.
        assert data['report']['cp1252_fallbacks'] == 60
        assert data['report']['unrecognised_feeding'] == {}

        # And it verifies on the way back out.
        stored = ada.get(f"/api/reservoir/snapshots/{data['snapshot']['id']}")
        assert stored.json()['data']['verified'] is True

    def test_a_source_that_will_not_parse_writes_nothing(self, ada, monkeypatch):
        with open(FIXTURE, 'rb') as handle:
            records, declared = ingest.extract_records(handle.read())
        broken = [dict(records[0])]
        broken[0]['lat'] = b''
        monkeypatch.setattr(ingest, 'fetch_records', lambda *a, **k: (broken, 1))

        before = len(ada.get('/api/reservoir/snapshots').json()['data']['items'])
        response = ada.post('/api/reservoir/snapshots')
        assert response.status_code == 200
        assert response.json()['success'] is False
        assert response.json()['data']['errors']
        after = len(ada.get('/api/reservoir/snapshots').json()['data']['items'])
        assert before == after


class TestTheBundledSnapshot:
    def test_there_is_none_yet_and_the_list_says_so(self, ada):
        """
        Gated on op 0 of the plan -- redistributing the CHRONO compilation
        needs its compilers' permission, which is a letter a person sends.
        The flag is reported rather than inferred because it changes what a
        fresh install can do at all.
        """
        data = ada.get('/api/reservoir/snapshots').json()['data']
        assert data['has_bundled'] is False
        assert all(s['kind'] == 'ingested' for s in data['items'])

    def test_the_csv_format_round_trips(self):
        """
        The file op 3d will ship. Written and read here so that adding it is a
        data drop rather than new code -- and so the format is pinned before
        anything depends on it.
        """
        rows, _ = _fixture_rows()
        text = ingest.rows_to_csv(rows)
        back = ingest.rows_from_csv(text)
        assert ingest.content_hash(back) == ingest.content_hash(rows)

    def test_the_csv_keeps_what_the_normalisation_decided(self):
        rows, _ = _fixture_rows()
        back = ingest.rows_from_csv(ingest.rows_to_csv(rows))
        hedged = [r for r in back if r.feeding == 'ambiguous']
        assert len(hedged) == 5
        # The source's own word survives the trip, which is what a reader
        # needs to check why one of them is ambiguous.
        assert all(r.feeding_raw for r in hedged)

    def test_a_file_with_the_wrong_columns_is_refused(self):
        """Reading it positionally would silently swap latitude for
        longitude, which is a class of bug nothing downstream would catch."""
        with pytest.raises(ingest.IngestError) as raised:
            ingest.rows_from_csv('lat,lon\n54.5,-5.6\n')
        assert 'expected columns' in str(raised.value)

    def test_a_file_with_a_header_and_no_rows_is_refused(self):
        text = ingest.rows_to_csv([]).strip()
        with pytest.raises(ingest.IngestError):
            ingest.rows_from_csv(text + '\n')


class TestStoring:
    def test_a_failed_parse_writes_nothing(self, ada):
        """All-or-nothing, in the sense date_import already means it: a
        reservoir table missing the rows that failed would give quietly wrong
        averages, which is worse than no table."""
        from app.database import SessionLocal

        report = ingest.IngestReport()
        report.errors.append('something was wrong with the source')
        with SessionLocal() as db:
            with pytest.raises(ingest.IngestError):
                ingest.store(db, [], report, source='x', created_by=_user_id(ada))

    def test_the_same_snapshot_cannot_be_stored_twice(self, ada):
        """
        An id is the source plus the source's own last-changed date, so a
        second ingest of an unchanged database produces an id that already
        exists -- and re-ingesting is not editing.
        """
        _store(_user_id(ada), 'duplicate')
        with pytest.raises(ingest.IngestError) as raised:
            _store(_user_id(ada), 'duplicate')
        assert 'already stored' in str(raised.value)


class TestTheLookupEndpoint:
    """
    POST /api/reservoir/lookup, over a stored snapshot.

    The endpoint's own job is small -- resolve, verify, hand to
    services/reservoir.lookup -- so what is tested here is the boundary: that
    it will not derive from a snapshot it cannot verify, that someone else's
    snapshot is not reachable through it, and that it returns the provenance
    record rather than leaving the caller to rebuild one.
    """

    def _placed(self, client, tag):
        snapshot = _store(_user_id(client), f'lookup-{tag}')
        # A position inside the fixture's own spread, so there is something
        # near it to find.
        rows, _ = _fixture_rows()
        anchor = next(r for r in rows if r.delta_r is not None and r.delta_r_error)
        return snapshot, anchor

    def test_it_derives_a_value_and_says_what_from(self, ada):
        snapshot, anchor = self._placed(ada, 'basic')
        response = ada.post('/api/reservoir/lookup', json={
            'latitude': anchor.latitude, 'longitude': anchor.longitude,
            'snapshot_id': snapshot.id, 'n': 5,
        })
        assert response.status_code == 200, response.text
        data = response.json()['data']

        assert data['delta_r'] is not None
        assert data['delta_r_error'] is not None
        assert data['n'] >= 2
        assert data['nearest_km'] is not None
        assert len(data['points']) >= 2

    def test_it_returns_the_record_to_store_if_the_value_is_applied(self, ada):
        """Built here rather than on apply, so the provenance describes the
        lookup the user actually looked at -- a second computation could
        differ if the snapshot changed underneath them."""
        snapshot, anchor = self._placed(ada, 'provenance')
        data = ada.post('/api/reservoir/lookup', json={
            'latitude': anchor.latitude, 'longitude': anchor.longitude,
            'snapshot_id': snapshot.id, 'n': 5,
        }).json()['data']

        record = data['delta_r_source']
        assert record['snapshot_id'] == snapshot.id
        assert record['map_nos']
        assert record['centre'] == {'lat': anchor.latitude, 'lon': anchor.longitude}
        assert record['computed_at']

    def test_the_counterfactual_comes_back_whether_or_not_it_was_asked_for(self, ada):
        snapshot, anchor = self._placed(ada, 'counterfactual')
        data = ada.post('/api/reservoir/lookup', json={
            'latitude': anchor.latitude, 'longitude': anchor.longitude,
            'snapshot_id': snapshot.id, 'n': 10,
        }).json()['data']

        assert 'suspension_equivalent' in data
        assert data['feeding_shift_warn_years'] == 35.0
        assert 'feeding_shift_matters' in data

    def test_it_writes_nothing(self, ada):
        """Deriving a value and applying one are separate acts. This endpoint
        only answers a question."""
        snapshot, anchor = self._placed(ada, 'readonly')
        before = ada.get(f'/api/reservoir/snapshots/{snapshot.id}').json()['data']
        ada.post('/api/reservoir/lookup', json={
            'latitude': anchor.latitude, 'longitude': anchor.longitude,
            'snapshot_id': snapshot.id, 'n': 5,
        })
        after = ada.get(f'/api/reservoir/snapshots/{snapshot.id}').json()['data']
        assert before['content_sha256'] == after['content_sha256']
        assert after['in_use_by'] == 0

    def test_another_account_cannot_derive_from_your_snapshot(self, ada, grace):
        """A 404, as everywhere else: an id must not be usable to discover
        that somebody else's snapshot exists."""
        snapshot, anchor = self._placed(ada, 'private')
        response = grace.post('/api/reservoir/lookup', json={
            'latitude': anchor.latitude, 'longitude': anchor.longitude,
            'snapshot_id': snapshot.id, 'n': 5,
        })
        assert response.status_code == 404

    def test_a_tampered_snapshot_will_not_derive_anything(self, ada):
        """
        The refusal that matters most. A snapshot that no longer hashes to its
        recorded value must not produce a number somebody then publishes.
        """
        from app.database import SessionLocal

        snapshot, anchor = self._placed(ada, 'tampered')
        with SessionLocal() as db:
            point = (
                db.query(MarineReservoirPoint)
                .filter(MarineReservoirPoint.snapshot_id == snapshot.id)
                .first()
            )
            point.delta_r = (point.delta_r or 0) + 5
            db.commit()

        response = ada.post('/api/reservoir/lookup', json={
            'latitude': anchor.latitude, 'longitude': anchor.longitude,
            'snapshot_id': snapshot.id, 'n': 5,
        })
        assert response.status_code == 409
        assert 'reproduced' in response.json()['detail']

    def test_nothing_within_range_is_a_422_that_says_so(self, ada):
        snapshot, _anchor = self._placed(ada, 'empty')
        response = ada.post('/api/reservoir/lookup', json={
            'latitude': 0, 'longitude': 0,
            'snapshot_id': snapshot.id, 'n': 5, 'max_km': 1,
        })
        assert response.status_code == 422
        assert '1 km' in response.json()['detail']

    def test_a_position_off_the_earth_is_refused_by_the_schema(self, ada):
        snapshot, _anchor = self._placed(ada, 'offearth')
        assert ada.post('/api/reservoir/lookup', json={
            'latitude': 91, 'longitude': 0, 'snapshot_id': snapshot.id,
        }).status_code == 422

    def test_an_anonymous_request_is_refused(self, anonymous_client):
        assert anonymous_client.post('/api/reservoir/lookup', json={
            'latitude': 54.5, 'longitude': -5.6, 'snapshot_id': 'anything',
        }).status_code == 401
