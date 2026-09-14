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
import pytest

from app.models import MarineReservoirPoint, MarineReservoirSnapshot
from app.services import reservoir_registry
from app.services import reservoir_ingest as ingest
from tests.fixtures.calib_marine import records as fixture


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
    create route cannot run: fetch_records is deliberately unimplemented. What
    is under test here is everything downstream of the fetch, and this is the
    seam the plan puts it behind.
    """
    from datetime import datetime
    from app.database import SessionLocal

    if rows is None:
        rows, report = ingest.records_to_rows(fixture.records())
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


class TestTheIngestRouteIsHonest:
    def test_it_answers_501_rather_than_pretending(self, ada):
        """
        fetch_records is unimplemented on purpose, and the route says so with
        a status that means it. What it must not do is answer 200 with an
        empty snapshot, which a caller would take for a working mirror.
        """
        response = ada.post('/api/reservoir/snapshots')
        assert response.status_code == 501
        assert 'not implemented' in response.json()['detail']

    def test_nothing_was_written(self, ada):
        before = len(ada.get('/api/reservoir/snapshots').json()['data']['items'])
        ada.post('/api/reservoir/snapshots')
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
        rows, _ = ingest.records_to_rows(fixture.records())
        text = ingest.rows_to_csv(rows)
        back = ingest.rows_from_csv(text)
        assert ingest.content_hash(back) == ingest.content_hash(rows)

    def test_the_csv_keeps_what_the_normalisation_decided(self):
        rows, _ = ingest.records_to_rows(fixture.records())
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
