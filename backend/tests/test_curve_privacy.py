"""
A stored calibration curve belongs to one account: api/curves.py, and the
`owner_id` scoping through services/curve_registry.py.

This started the other way round -- curves shared as reference data, with only
editing restricted to whoever imported one -- and these tests are the reversal.
The reasons are the ones that only appear with more than one user on an
install: a curve somebody is still developing must not sit in everyone's picker
looking supported, and a curve imported for one site years ago must not become
an app-wide default a newcomer picks by mistake.

What is actually being pinned is not "the list is filtered". It is that there
is NO route by which one account reaches another's curve -- listing, reading,
plotting, exporting, calibrating against it, or naming it in an imported file.
Each of those is a separate way in, and each got the check separately, so each
is tested separately. A test that only checked GET /api/curves would have
passed against a build where /curves/{id}/points served anything asked for.

The three builtin curves are the deliberate exception and are asserted to stay
visible to everybody, because a scoping change that quietly hid IntCal20 would
break every analysis in the app.
"""
import io

import pytest

from app.services import curve_registry


CURVE_FILE = b"""# A tiny test curve
0 0 5
100 105 5
200 215 5
300 320 5
400 430 5
"""


def _client_for(email):
    """
    A signed-in client for `email`, registering the account if it is new.

    Session-scoped below, and it has to be: the suite shares one database for
    the whole run, so a function-scoped fixture would try to register the same
    address once per test and get a 409 on the second. Reusing the account also
    means curves imported by one test are still there for the next, which is
    what several of these need -- the alias and id-collision tests are about
    two accounts' curves coexisting.
    """
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
def alice():
    """A client for one account, distinct from the suite's own."""
    return _client_for('alice-curves@radical.example.com')


@pytest.fixture(scope='session')
def bob():
    """A second account, so "another user" is a real one rather than a mock."""
    return _client_for('bob-curves@radical.example.com')


def _import(client, curve_id, name=None, aliases=None):
    """Import CURVE_FILE under `curve_id`. Returns the id actually assigned."""
    import json
    meta = {
        'name': name or curve_id,
        'description': 'A curve for testing',
        'reference': 'Nobody 2026',
        'type': 'terrestrial',
        'sample_type': 'terrestrial',
        'aliases': aliases or [],
    }
    response = client.post(
        '/api/curves',
        files={'file': (f'{curve_id}.14c', io.BytesIO(CURVE_FILE), 'text/plain')},
        data={'metadata': json.dumps(meta), 'calendar': 'cal_bp', 'curve_id': curve_id},
    )
    assert response.status_code == 201, response.text
    return response.json()['data']['id']


class TestListing:
    def test_a_curve_is_absent_from_another_account_s_list(self, alice, bob):
        _import(alice, 'alices-private-curve')

        ids = [c['id'] for c in bob.get('/api/curves').json()['data']['items']]
        assert 'alices-private-curve' not in ids

    def test_the_owner_still_sees_their_own(self, alice):
        _import(alice, 'alices-visible-curve')
        ids = [c['id'] for c in alice.get('/api/curves').json()['data']['items']]
        assert 'alices-visible-curve' in ids

    def test_the_builtins_are_visible_to_everybody(self, alice, bob):
        # The exception, and the one that must not break: hiding IntCal20 would
        # take every analysis in the app with it.
        for client in (alice, bob):
            ids = [c['id'] for c in client.get('/api/curves').json()['data']['items']]
            assert {'intcal20', 'shcal20', 'marine20'} <= set(ids)

    def test_the_type_listing_is_scoped_too(self, alice, bob):
        # A second listing endpoint, and a second place the filter has to be.
        _import(alice, 'alices-terrestrial-curve')
        ids = [c['id'] for c in
               bob.get('/api/curves/type/terrestrial').json()['data']]
        assert 'alices-terrestrial-curve' not in ids


class TestReading:
    """Every id-taking endpoint, because each is its own way in."""

    @pytest.fixture(scope='class')
    def alices_curve(self, alice):
        return _import(alice, 'alices-readable-curve')

    @pytest.mark.parametrize('path', [
        '/api/curves/{id}',
        '/api/curves/{id}/points',
        '/api/curves/{id}/points.csv',
        '/api/curves/{id}/resolution',
        '/api/curves/{id}/resolution.csv',
        '/api/curves/{id}/export',
        '/api/curves/{id}/usage',
    ])
    def test_another_account_gets_a_404(self, alice, bob, alices_curve, path):
        assert bob.get(path.format(id=alices_curve)).status_code == 404

    def test_the_owner_gets_a_200_from_the_same_paths(self, alice, alices_curve):
        # The other half of the parametrised test above: a 404 for everyone
        # would also pass it.
        for path in ('/api/curves/{id}', '/api/curves/{id}/points',
                     '/api/curves/{id}/export', '/api/curves/{id}/usage'):
            response = alice.get(path.format(id=alices_curve))
            assert response.status_code == 200, path

    def test_simulating_against_it_is_refused(self, alice, bob, alices_curve):
        # A VALID body, deliberately: an invalid one is rejected by the schema
        # before the route runs, so it would pass this test without the gate
        # ever being reached.
        response = bob.post(f'/api/curves/{alices_curve}/simulate',
                            json={'cal_age': 200, 'error': 25})
        assert response.status_code == 404
        # And the owner's identical request works, so the 404 is about who is
        # asking rather than about the request.
        assert alice.post(f'/api/curves/{alices_curve}/simulate',
                          json={'cal_age': 200, 'error': 25}).status_code == 200

    def test_comparing_against_it_is_refused(self, alice, bob, alices_curve):
        # Both operands are checked: a compare that gated only `a` would let
        # `b` be anything.
        assert bob.get(
            f'/api/curves/compare?a=intcal20&b={alices_curve}').status_code == 404
        assert bob.get(
            f'/api/curves/compare?a={alices_curve}&b=intcal20').status_code == 404

    def test_it_is_a_404_not_a_403(self, alice, bob, alices_curve):
        """
        The distinction the whole module maintains: 403 would confirm the
        curve exists, which turns every id-taking endpoint into a way to
        enumerate what other people have imported.
        """
        response = bob.get(f'/api/curves/{alices_curve}')
        assert response.status_code == 404
        # And the id that names nothing at all answers identically.
        ghost = bob.get('/api/curves/no-such-curve-anywhere')
        assert ghost.status_code == 404


class TestChanging:
    @pytest.fixture(scope='class')
    def alices_curve(self, alice):
        return _import(alice, 'alices-editable-curve')

    def test_another_account_cannot_edit_it(self, alice, bob, alices_curve):
        response = bob.patch(f'/api/curves/{alices_curve}',
                             json={'description': 'hijacked'})
        assert response.status_code == 404

    def test_another_account_cannot_delete_it(self, alice, bob, alices_curve):
        assert bob.delete(f'/api/curves/{alices_curve}').status_code == 404

    def test_another_account_cannot_archive_or_restore_it(self, alice, bob, alices_curve):
        assert bob.post(f'/api/curves/{alices_curve}/restore').status_code == 404

    def test_the_owner_still_can(self, alice, alices_curve):
        response = alice.patch(f'/api/curves/{alices_curve}',
                               json={'description': 'corrected'})
        assert response.status_code == 200
        assert response.json()['data']['description'] == 'corrected'

    def test_a_builtin_still_answers_403_with_its_reason(self, alice):
        # Not a 404: those three ids are public knowledge, so explaining is
        # more use than pretending they are not there.
        response = alice.patch('/api/curves/intcal20', json={'description': 'no'})
        assert response.status_code == 403
        assert 'shipped with this app' in response.json()['detail']


class TestCalibratingAgainstIt:
    """The one that matters most: a curve you cannot see, you cannot use."""

    @pytest.fixture(scope='class')
    def alices_curve(self, alice):
        return _import(alice, 'alices-calibration-curve')

    def test_another_account_cannot_name_it_in_an_analysis(self, alice, bob, alices_curve):
        response = bob.post('/api/analysis/calibrate', json={
            'sample_name': 'Borrowed', 'age_bp': 200, 'error_1sigma': 25,
            'curve_id': alices_curve, 'save': False,
        })
        assert response.status_code == 422

    def test_the_refusal_does_not_name_other_people_s_curves(self, alice, bob, alices_curve):
        # It lists what BOB can use. Listing everything would undo the whole
        # point by way of an error message.
        response = bob.post('/api/analysis/calibrate', json={
            'sample_name': 'Borrowed', 'age_bp': 200, 'error_1sigma': 25,
            'curve_id': alices_curve, 'save': False,
        })
        detail = str(response.json()['detail'])
        assert 'intcal20' in detail
        # The curve asked for is named -- they typed it -- but no OTHER curve
        # of Alice's appears in the list of what is available.
        assert detail.count(alices_curve) == 1

    def test_the_owner_can(self, alice, alices_curve):
        response = alice.post('/api/analysis/calibrate', json={
            'sample_name': 'Mine', 'age_bp': 200, 'error_1sigma': 25,
            'curve_id': alices_curve, 'save': False,
        })
        assert response.status_code == 200, response.text

    def test_a_bayesian_run_checks_every_measurement(self, alice, bob, alices_curve):
        # Not just the first: the check is over the whole list.
        response = bob.post('/api/analysis/bayesian?wait=true', json={
            'name': 'Borrowed curve',
            'measurements': [
                {'id': 'bc1', 'sample_name': 'A', 'age_bp': 300, 'error_1sigma': 25,
                 'curve_id': 'intcal20'},
                {'id': 'bc2', 'sample_name': 'B', 'age_bp': 200, 'error_1sigma': 25,
                 'curve_id': alices_curve},
            ],
            'save': False,
        })
        assert response.status_code == 422

    def test_a_project_date_cannot_name_it(self, alice, bob, alices_curve):
        project = bob.post('/api/projects', json={'name': 'Bobs project'})
        project_id = project.json()['data']['id']
        response = bob.post(f'/api/projects/{project_id}/samples', json={
            'samples': [{'sample_name': 'X', 'age_bp': 200, 'error_1sigma': 25,
                         'curve_id': alices_curve}],
        })
        assert response.status_code == 422


class TestImportingAFileThatNamesIt:
    """
    An uploaded file resolves curve names against the uploader's own curves.

    Without this, importing a file is a way to ask "does a curve by this name
    exist on this server", and would calibrate one user's dates against a
    curve they cannot see.
    """

    def test_a_csv_naming_another_account_s_curve_fails(self, alice, bob):
        _import(alice, 'alices-csv-curve', name='Alices CSV Curve')
        csv = b'name,age,error,curve\nOxA-1,3000,25,Alices CSV Curve\n'
        response = bob.post('/api/import/dates',
                            files={'file': ('dates.csv', io.BytesIO(csv), 'text/csv')})
        assert response.status_code == 200
        data = response.json()['data']
        assert data['ok'] is False
        assert data['errors']

    def test_the_owner_s_own_import_resolves_it(self, alice):
        _import(alice, 'alices-own-csv-curve', name='Alices Own CSV Curve')
        csv = b'name,age,error,curve\nOxA-1,3000,25,Alices Own CSV Curve\n'
        response = alice.post('/api/import/dates',
                              files={'file': ('dates.csv', io.BytesIO(csv), 'text/csv')})
        data = response.json()['data']
        assert data['ok'] is True, data['errors']
        assert data['dates'][0]['curve_id'] == 'alices-own-csv-curve'

    def test_the_error_does_not_list_another_account_s_curves(self, alice, bob):
        _import(alice, 'alices-secret-curve', name='Alices Secret Curve')
        csv = b'name,age,error,curve\nOxA-1,3000,25,Nonsense Curve\n'
        response = bob.post('/api/import/dates',
                            files={'file': ('dates.csv', io.BytesIO(csv), 'text/csv')})
        errors = ' '.join(response.json()['data']['errors'])
        assert 'Alices Secret Curve' not in errors

    def test_an_oxcal_file_naming_another_account_s_curve_fails(self, alice, bob):
        _import(alice, 'alices-oxcal-curve', name='AlicesOxcalCurve')
        script = b'Plot(){ Curve("AlicesOxcalCurve"); R_Date("A", 3000, 25); };'
        response = bob.post('/api/import/dates',
                            files={'file': ('m.oxcal', io.BytesIO(script), 'text/plain')})
        data = response.json()['data']
        assert data['ok'] is False
        assert any('AlicesOxcalCurve' in e for e in data['errors'])


class TestMixedCurves:
    def test_a_mixture_is_private_like_an_import(self, alice, bob):
        created = alice.post('/api/curves/mixed', json={
            'name': 'Alices Mixture', 'curve_id': 'alices-mixture',
            'a': 'intcal20', 'b': 'marine20',
            'percent_a': 70, 'percent_a_error': 10,
        })
        assert created.status_code == 201, created.text
        ids = [c['id'] for c in bob.get('/api/curves').json()['data']['items']]
        assert 'alices-mixture' not in ids
        assert bob.get('/api/curves/alices-mixture').status_code == 404

    def test_a_mixture_cannot_be_built_from_another_account_s_curve(self, alice, bob):
        alices = _import(alice, 'alices-mixable-curve')
        response = bob.post('/api/curves/mixed', json={
            'name': 'Bobs attempt', 'curve_id': 'bobs-attempt',
            'a': alices, 'b': 'marine20',
            'percent_a': 50, 'percent_a_error': 5,
        })
        assert response.status_code == 422

    def test_two_accounts_may_each_hold_the_same_recipe(self, alice, bob):
        """
        Duplicate detection is per user. The same numbers, but a curve Bob
        cannot see is not one he could have reused -- and refusing his would
        report that Alice's exists.
        """
        for client, slug in ((alice, 'alices-7030'), (bob, 'bobs-7030')):
            response = client.post('/api/curves/mixed', json={
                'name': f'Mixture {slug}', 'curve_id': slug,
                'a': 'intcal20', 'b': 'marine20',
                'percent_a': 65, 'percent_a_error': 12,
            })
            assert response.status_code == 201, response.text


class TestIdCollisions:
    def test_the_same_slug_from_two_accounts_both_succeed(self, alice, bob):
        """
        Curve ids are one global primary key while curves are per user, so the
        second import has to be given a free id rather than refused -- refusing
        would be both a dead end and a disclosure.
        """
        first = _import(alice, 'shared-slug-curve')
        second = _import(bob, 'shared-slug-curve')
        assert first == 'shared-slug-curve'
        assert second != first
        assert second.startswith('shared-slug-curve')

    def test_each_account_reaches_its_own(self, alice, bob):
        first = _import(alice, 'reachable-slug-curve')
        second = _import(bob, 'reachable-slug-curve')
        assert alice.get(f'/api/curves/{first}').status_code == 200
        assert bob.get(f'/api/curves/{second}').status_code == 200
        assert alice.get(f'/api/curves/{second}').status_code == 404
        assert bob.get(f'/api/curves/{first}').status_code == 404

    def test_the_owner_s_own_duplicate_is_still_refused(self, alice):
        # Their own clash they can see and act on, so it stays a 409.
        _import(alice, 'alices-duplicate-curve')
        import json
        meta = {'name': 'Again', 'description': '', 'reference': '',
                'type': 'terrestrial', 'sample_type': 'terrestrial', 'aliases': []}
        response = alice.post(
            '/api/curves',
            files={'file': ('c.14c', io.BytesIO(CURVE_FILE), 'text/plain')},
            data={'metadata': json.dumps(meta), 'calendar': 'cal_bp',
                  'curve_id': 'alices-duplicate-curve'},
        )
        assert response.status_code == 409

    def test_an_alias_may_be_reused_by_a_different_account(self, alice, bob):
        # Aliases resolve per user, so two accounts may each use "local" for
        # their own curve without either seeing the other's.
        _import(alice, 'alices-aliased-curve', aliases=['local'])
        _import(bob, 'bobs-aliased-curve', aliases=['local'])
        csv = b'name,age,error,curve\nOxA-1,3000,25,local\n'
        for client, expected in ((alice, 'alices-aliased-curve'),
                                 (bob, 'bobs-aliased-curve')):
            response = client.post(
                '/api/import/dates',
                files={'file': ('d.csv', io.BytesIO(csv), 'text/csv')})
            data = response.json()['data']
            assert data['ok'] is True, data['errors']
            assert data['dates'][0]['curve_id'] == expected


class TestTheRegistryItself:
    """
    The scoping below the API, tested directly.

    The registry is where a mistake would be silent: an endpoint that forgot
    its check fails a test above loudly, but a listing function that quietly
    stopped filtering would keep every one of those passing while the picker
    filled up again.
    """

    def test_list_specs_holds_only_the_owner_s_curves(self, alice, bob):
        _import(alice, 'registry-alice-curve')
        _import(bob, 'registry-bob-curve')
        curve_registry.invalidate()

        alice_id = alice.get('/api/auth/me').json()['data']['id']
        ids = {spec.id for spec in curve_registry.list_specs(alice_id)}
        assert 'registry-alice-curve' in ids
        assert 'registry-bob-curve' not in ids

    def test_scope_any_still_sees_everything(self, alice, bob):
        # The escape hatch the docstring documents, for `usage` and the like.
        _import(alice, 'scope-any-alice-curve')
        curve_registry.invalidate()
        ids = {spec.id for spec in
               curve_registry.list_specs(curve_registry.SCOPE_ANY)}
        assert 'scope-any-alice-curve' in ids

    def test_the_alias_index_is_cached_per_owner(self, alice, bob):
        """
        One shared cache would hand whichever user warmed it second the curves
        of whoever warmed it first -- a leak that appears only under a second
        request, which is exactly the kind a test suite otherwise misses.
        """
        _import(alice, 'cache-alice-curve', aliases=['cached'])
        _import(bob, 'cache-bob-curve', aliases=['cached'])
        curve_registry.invalidate()

        alice_id = alice.get('/api/auth/me').json()['data']['id']
        bob_id = bob.get('/api/auth/me').json()['data']['id']
        # Alice first, so hers is the entry a shared cache would hold.
        assert curve_registry.resolve_alias('cached', alice_id) == 'cache-alice-curve'
        assert curve_registry.resolve_alias('cached', bob_id) == 'cache-bob-curve'

    def test_resolve_is_deliberately_unscoped(self, alice):
        """
        The loader takes no owner, by design -- a solver re-running a stored
        analysis has no user in scope and must still load the curve that
        analysis was computed against. This pins that as intended rather than
        as an oversight: the check lives at the API boundary, and the tests
        above are what hold it there.
        """
        curve_id = _import(alice, 'unscoped-resolve-curve')
        curve_registry.invalidate()
        assert curve_registry.resolve(curve_id) is not None

    def test_builtins_resolve_for_anyone(self, alice, bob):
        alice_id = alice.get('/api/auth/me').json()['data']['id']
        bob_id = bob.get('/api/auth/me').json()['data']['id']
        for owner in (alice_id, bob_id):
            assert curve_registry.exists('intcal20', owner)
            assert curve_registry.visible_to('shcal20', owner)
