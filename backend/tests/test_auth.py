"""
Accounts, and the separation they buy: api/auth.py and services/auth.py.

The rest of the suite runs as one signed-in user (see conftest's `client`), so
it demonstrates that the authenticated path works and says nothing at all
about what happens to anyone else. That is what this file is for, and it is
the more important half: an ownership filter that is merely PRESENT is worth
nothing, and the way these usually fail is not "the check is missing" but "the
check is on the detail route and not on the list route", or "on the read and
not on the delete".

So the load-bearing part is TestOneUserCannotReachAnother, which builds two
real accounts, creates real work under the first, and then tries to reach it
every way the API allows -- read, list, edit, delete, duplicate, re-run,
export. Each has to answer 404, not 403: a 403 confirms the id exists, which
turns any endpoint taking an id into a way to enumerate other people's work.

The other thing pinned here is that the routes are protected AT ALL. That is
asserted structurally, over the app's own route table, rather than as a list
someone has to remember to extend -- a new endpoint added without a
`current_user` dependency fails the suite instead of quietly becoming a hole.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import auth


# --------------------------------------------------------------------------
# The arithmetic, with no HTTP in it
# --------------------------------------------------------------------------

class TestPasswordHashing:
    def test_a_hash_is_not_the_password(self):
        hashed = auth.hash_password('correct horse battery staple')
        assert 'correct horse' not in hashed
        assert auth.verify_password('correct horse battery staple', hashed)

    def test_a_wrong_password_does_not_verify(self):
        hashed = auth.hash_password('the right one')
        assert not auth.verify_password('the wrong one', hashed)

    def test_two_hashes_of_one_password_differ(self):
        # bcrypt salts. Identical hashes would mean two users with the same
        # password were visibly the same in the table.
        a = auth.hash_password('same password')
        b = auth.hash_password('same password')
        assert a != b
        assert auth.verify_password('same password', a)
        assert auth.verify_password('same password', b)

    def test_a_missing_or_broken_hash_is_not_a_match(self):
        # A caller comparing a password has one useful question, and a hash
        # that cannot be parsed is not a yes.
        assert not auth.verify_password('anything', None)
        assert not auth.verify_password('anything', '')
        assert not auth.verify_password('anything', 'not a bcrypt hash')

    def test_a_password_past_bcrypt_s_limit_is_refused_not_truncated(self):
        # bcrypt ignores everything past 72 bytes. Silently accepting a long
        # passphrase whose tail does not count is the exact failure a long
        # passphrase was chosen to avoid.
        with pytest.raises(ValueError) as exc:
            auth.hash_password('x' * 73)
        assert '72 bytes' in str(exc.value)

    def test_the_limit_is_bytes_not_characters(self):
        # 40 accented characters is 80 bytes. Counting characters would let
        # this through and truncate it.
        with pytest.raises(ValueError):
            auth.hash_password('é' * 40)


class TestTokens:
    def test_a_token_round_trips(self):
        token = auth.create_token('user-123')
        assert auth.read_token(token).user_id == 'user-123'

    def test_an_expired_token_is_refused(self):
        old = datetime.now(timezone.utc) - auth.TOKEN_TTL - timedelta(minutes=1)
        with pytest.raises(auth.AuthError):
            auth.read_token(auth.create_token('user-123', now=old))

    def test_a_tampered_token_is_refused(self):
        token = auth.create_token('user-123')
        # Flip a character in the signature.
        broken = token[:-2] + ('a' if token[-1] != 'a' else 'b')
        with pytest.raises(auth.AuthError):
            auth.read_token(broken)

    def test_an_unsigned_token_is_refused(self, monkeypatch):
        # `alg: none` is the classic way a JWT library is turned into an
        # unauthenticated one. read_token pins the algorithm for this reason.
        import jwt
        forged = jwt.encode({'sub': 'somebody-else', 'exp': 2 ** 31 - 1},
                            key='', algorithm='none')
        with pytest.raises(auth.AuthError):
            auth.read_token(forged)

    def test_a_token_signed_with_another_key_is_refused(self, monkeypatch):
        import jwt
        forged = jwt.encode(
            {'sub': 'somebody-else', 'exp': 2 ** 31 - 1},
            key='a different secret entirely', algorithm='HS256')
        with pytest.raises(auth.AuthError):
            auth.read_token(forged)


class TestTheSigningKey:
    def test_a_declared_deployment_must_declare_a_key(self, monkeypatch):
        # A hardcoded fallback secret is a published private key. The app
        # refuses to mint tokens rather than mint forgeable ones.
        monkeypatch.setenv('RADICAL_ENV', 'production')
        monkeypatch.delenv('RADICAL_SECRET_KEY', raising=False)
        with pytest.raises(RuntimeError) as exc:
            auth.secret_key()
        assert 'RADICAL_SECRET_KEY' in str(exc.value)

    def test_development_may_go_without_one(self, monkeypatch):
        monkeypatch.setenv('RADICAL_ENV', 'development')
        monkeypatch.delenv('RADICAL_SECRET_KEY', raising=False)
        assert auth.secret_key()
        assert auth.using_the_development_secret()

    def test_a_configured_key_is_used_and_is_not_the_default(self, monkeypatch):
        monkeypatch.setenv('RADICAL_SECRET_KEY', 'a real deployment key')
        assert auth.secret_key() == 'a real deployment key'
        assert not auth.using_the_development_secret()


class TestEmailNormalisation:
    def test_case_and_whitespace_are_folded(self):
        assert auth.normalise_email('  Ann@Example.ORG ') == 'ann@example.org'

    def test_it_survives_nothing(self):
        assert auth.normalise_email(None) == ''


# --------------------------------------------------------------------------
# Registering and signing in
# --------------------------------------------------------------------------

def _call(client, method, path, body=None):
    """
    One request, with a body only where the method takes one.

    httpx's `get` and `delete` refuse a `json=` argument outright, so a
    parametrised table covering every verb cannot pass one unconditionally.
    """
    if method in ('get', 'delete'):
        return getattr(client, method)(path)
    return getattr(client, method)(path, json=body or {})


def _register(client, email, password='a good enough password'):
    return client.post('/api/auth/register',
                       json={'email': email, 'password': password})


class TestRegistering:
    def test_it_returns_a_working_token(self, anonymous_client):
        response = _register(anonymous_client, 'newcomer@radical.example.com')
        assert response.status_code == 201
        token = response.json()['data']['token']

        me = anonymous_client.get('/api/auth/me',
                                  headers={'Authorization': f'Bearer {token}'})
        assert me.status_code == 200
        assert me.json()['data']['email'] == 'newcomer@radical.example.com'

    def test_the_hash_never_leaves_the_server(self, anonymous_client):
        response = _register(anonymous_client, 'discreet@radical.example.com')
        assert 'password' not in str(response.json()['data']['user'])

    def test_the_same_address_twice_is_refused(self, anonymous_client):
        _register(anonymous_client, 'twice@radical.example.com')
        again = _register(anonymous_client, 'twice@radical.example.com')
        assert again.status_code == 409

    def test_case_does_not_make_a_second_account(self, anonymous_client):
        _register(anonymous_client, 'case@radical.example.com')
        assert _register(anonymous_client, 'CASE@Radical.Example.COM').status_code == 409

    def test_a_short_password_is_refused(self, anonymous_client):
        assert _register(anonymous_client, 'short@radical.example.com', 'seven77').status_code == 422

    def test_something_that_is_not_an_address_is_refused(self, anonymous_client):
        assert _register(anonymous_client, 'not-an-address').status_code == 422


class TestSigningIn:
    def test_the_right_password_works(self, anonymous_client):
        _register(anonymous_client, 'signin@radical.example.com', 'the right password')
        response = anonymous_client.post('/api/auth/login', json={
            'email': 'signin@radical.example.com', 'password': 'the right password'})
        assert response.status_code == 200
        assert response.json()['data']['token']

    def test_the_wrong_password_does_not(self, anonymous_client):
        _register(anonymous_client, 'wrongpw@radical.example.com', 'the right password')
        response = anonymous_client.post('/api/auth/login', json={
            'email': 'wrongpw@radical.example.com', 'password': 'the wrong password'})
        assert response.status_code == 401

    def test_a_missing_account_and_a_wrong_password_are_indistinguishable(
            self, anonymous_client):
        # Otherwise this endpoint is a way to find out who has an account here.
        _register(anonymous_client, 'exists@radical.example.com', 'the right password')
        wrong_password = anonymous_client.post('/api/auth/login', json={
            'email': 'exists@radical.example.com', 'password': 'nope'})
        no_account = anonymous_client.post('/api/auth/login', json={
            'email': 'nobody-at-all@radical.example.com', 'password': 'nope'})
        assert wrong_password.status_code == no_account.status_code == 401
        assert wrong_password.json()['detail'] == no_account.json()['detail']


class TestChangingAPassword:
    def test_it_needs_the_current_one(self, anonymous_client):
        token = _register(anonymous_client, 'changer@radical.example.com',
                          'the first password').json()['data']['token']
        headers = {'Authorization': f'Bearer {token}'}

        refused = anonymous_client.post('/api/auth/password', headers=headers, json={
            'current_password': 'a guess', 'new_password': 'the second password'})
        # Not 401, which the browser reads as an expired session and signs out.
        assert refused.status_code == 403

        accepted = anonymous_client.post('/api/auth/password', headers=headers, json={
            'current_password': 'the first password',
            'new_password': 'the second password'})
        assert accepted.status_code == 200

        assert anonymous_client.post('/api/auth/login', json={
            'email': 'changer@radical.example.com',
            'password': 'the second password'}).status_code == 200


class TestResettingAForgottenPassword:
    """
    A reset link works once, for an hour, for one account, and is never a
    session; asking for one says nothing about who has an account.
    """

    @staticmethod
    def _user(client, email, password='the old password'):
        from app.database import SessionLocal
        from app.models import User
        _register(client, email, password)
        with SessionLocal() as db:
            user = db.query(User).filter(User.email == email).one()
            return user.id, user.password_hash

    @staticmethod
    def _login(client, email, password):
        return client.post('/api/auth/login', json={'email': email, 'password': password})

    def test_a_link_sets_the_password_and_signs_in(self, anonymous_client):
        user_id, old_hash = self._user(anonymous_client, 'forgot@radical.example.com')
        token = auth.create_reset_token(user_id, old_hash)
        response = anonymous_client.post('/api/auth/reset-password', json={
            'token': token, 'new_password': 'the new password'})
        assert response.status_code == 200, response.text
        session = response.json()['data']['token']
        assert anonymous_client.get('/api/auth/me', headers={
            'Authorization': f'Bearer {session}'}).status_code == 200
        assert self._login(anonymous_client, 'forgot@radical.example.com',
                           'the old password').status_code == 401
        assert self._login(anonymous_client, 'forgot@radical.example.com',
                           'the new password').status_code == 200

    def test_a_link_works_once(self, anonymous_client):
        user_id, old_hash = self._user(anonymous_client, 'once@radical.example.com')
        token = auth.create_reset_token(user_id, old_hash)
        body = {'token': token, 'new_password': 'the new password'}
        assert anonymous_client.post('/api/auth/reset-password', json=body).status_code == 200
        again = anonymous_client.post('/api/auth/reset-password',
                                      json={**body, 'new_password': 'an attacker s password'})
        assert again.status_code == 400
        assert 'expired or has already been used' in again.json()['detail']

    def test_an_expired_link_is_refused(self, anonymous_client):
        user_id, old_hash = self._user(anonymous_client, 'late@radical.example.com')
        token = auth.create_reset_token(
            user_id, old_hash, now=datetime.now(timezone.utc) - timedelta(hours=2))
        assert anonymous_client.post('/api/auth/reset-password', json={
            'token': token, 'new_password': 'the new password'}).status_code == 400

    def test_a_link_is_not_a_session_and_a_session_is_not_a_link(self, anonymous_client):
        user_id, old_hash = self._user(anonymous_client, 'purpose@radical.example.com')
        reset = auth.create_reset_token(user_id, old_hash)
        with pytest.raises(auth.AuthError):
            auth.read_token(reset)
        assert anonymous_client.get('/api/auth/me', headers={
            'Authorization': f'Bearer {reset}'}).status_code == 401
        assert anonymous_client.post('/api/auth/reset-password', json={
            'token': auth.create_token(user_id),
            'new_password': 'the new password'}).status_code == 400

    def test_a_short_new_password_is_refused(self, anonymous_client):
        user_id, old_hash = self._user(anonymous_client, 'shortreset@radical.example.com')
        assert anonymous_client.post('/api/auth/reset-password', json={
            'token': auth.create_reset_token(user_id, old_hash),
            'new_password': 'seven77'}).status_code == 422

    def test_without_email_there_is_no_email_option(self, anonymous_client, monkeypatch):
        monkeypatch.delenv('RADICAL_SMTP_HOST', raising=False)
        assert anonymous_client.get('/api/auth/options').json()['data'] == {
            'password_reset_email': False}
        assert anonymous_client.post('/api/auth/forgot-password', json={
            'email': 'anyone@radical.example.com'}).status_code == 503

    @pytest.fixture
    def outbox(self, monkeypatch):
        from app.services import mail
        monkeypatch.setenv('RADICAL_PUBLIC_URL', 'https://radical.example.com/')
        monkeypatch.setenv('RADICAL_SMTP_HOST', 'smtp.radical.example.com')
        monkeypatch.setenv('RADICAL_SMTP_FROM', 'radical@radical.example.com')
        sent = []
        monkeypatch.setattr(mail, 'send', lambda config, to, subject, body: sent.append((to, body)))
        return sent

    def test_an_email_carries_a_working_link(self, anonymous_client, outbox):
        self._user(anonymous_client, 'emailed@radical.example.com')
        assert anonymous_client.get('/api/auth/options').json()['data']['password_reset_email']
        response = anonymous_client.post('/api/auth/forgot-password', json={
            'email': 'Emailed@Radical.Example.com'})
        assert response.status_code == 200
        [(to, body)] = outbox
        assert to == 'emailed@radical.example.com'
        prefix = 'https://radical.example.com/reset-password#token='
        link = next(line for line in body.splitlines() if line.startswith(prefix))
        assert anonymous_client.post('/api/auth/reset-password', json={
            'token': link[len(prefix):], 'new_password': 'the new password'}).status_code == 200

    def test_asking_says_nothing_about_who_has_an_account(self, anonymous_client, outbox):
        self._user(anonymous_client, 'known@radical.example.com')
        known = anonymous_client.post('/api/auth/forgot-password', json={
            'email': 'known@radical.example.com'})
        unknown = anonymous_client.post('/api/auth/forgot-password', json={
            'email': 'unknown@radical.example.com'})
        assert known.status_code == unknown.status_code == 200
        assert known.json() == unknown.json()
        assert [to for to, _ in outbox] == ['known@radical.example.com']

    def test_asking_again_at_once_sends_nothing_more(self, anonymous_client, outbox):
        self._user(anonymous_client, 'impatient@radical.example.com')
        for _ in range(3):
            assert anonymous_client.post('/api/auth/forgot-password', json={
                'email': 'impatient@radical.example.com'}).status_code == 200
        assert len(outbox) == 1


class TestTheServerResetCommand:
    def test_it_sets_the_password(self, anonymous_client):
        from app.reset_password import reset
        _register(anonymous_client, 'cli@radical.example.com', 'the old password')
        reset('CLI@radical.example.com', 'the new password')
        assert anonymous_client.post('/api/auth/login', json={
            'email': 'cli@radical.example.com', 'password': 'the new password'}).status_code == 200

    def test_it_refuses_an_unknown_account_or_a_short_password(self):
        from app.reset_password import reset
        with pytest.raises(LookupError):
            reset('nobody-here@radical.example.com', 'a long enough password')
        with pytest.raises(ValueError):
            reset('cli@radical.example.com', 'short')


# --------------------------------------------------------------------------
# The dependency
# --------------------------------------------------------------------------

class TestReachingAnythingWithoutAnAccount:
    @pytest.mark.parametrize('method,path', [
        ('get', '/api/analysis'),
        ('get', '/api/projects'),
        ('get', '/api/curves'),
        ('get', '/api/jobs'),
        ('post', '/api/projects'),
        ('post', '/api/analysis/calibrate'),
    ])
    def test_it_is_a_401(self, anonymous_client, method, path):
        response = _call(anonymous_client, method, path)
        assert response.status_code == 401

    def test_health_is_still_open(self, anonymous_client):
        # The one thing a load balancer has to reach without credentials.
        assert anonymous_client.get('/api/health').status_code == 200

    @pytest.mark.parametrize('header', [
        'Bearer', 'Bearer   ', 'Basic abc123', 'Bearer not.a.token', 'nonsense',
    ])
    def test_a_malformed_header_is_a_401_and_says_nothing(
            self, anonymous_client, header):
        response = anonymous_client.get('/api/projects',
                                        headers={'Authorization': header})
        assert response.status_code == 401


class TestEveryRouteIsProtected:
    """
    Structural, not a list. A new endpoint that forgets `current_user` fails
    here rather than becoming a quiet hole -- the same argument
    test_event_loop.py makes for blocking handlers.
    """

    # The only endpoints that may be reached without an account, each for a
    # stated reason. Adding to this list is a deliberate act.
    OPEN = {
        '/api/auth/register',   # there is no account yet, by definition
        '/api/auth/login',      # ditto
        '/api/auth/options',            # what the sign-in page may offer
        '/api/auth/forgot-password',    # the password is what is missing
        '/api/auth/reset-password',     # ditto; the reset token is the credential
        '/api/health',          # a load balancer has no credentials
        '/',                    # the API's own index
        '/api/docs', '/api/redoc', '/api/openapi.json', '/api/docs/oauth2-redirect',
        '/docs', '/redoc', '/openapi.json',     # redirects to the three above
    }

    def test_no_route_is_unintentionally_open(self):
        from app.main import app
        from app.api.auth import current_user

        offenders = []
        for route in app.routes:
            path = getattr(route, 'path', None)
            if path is None or path in self.OPEN:
                continue
            dependencies = getattr(getattr(route, 'dependant', None), 'dependencies', [])
            protected = any(
                getattr(d, 'call', None) is current_user
                for d in _flatten_dependencies(dependencies)
            )
            if not protected:
                offenders.append(f'{sorted(getattr(route, "methods", []) or [])} {path}')

        assert not offenders, (
            'These routes can be reached without an account. Add '
            '`user: User = Depends(current_user)` to each, or add it to '
            'TestEveryRouteIsProtected.OPEN with a reason:\n  '
            + '\n  '.join(offenders)
        )


def _flatten_dependencies(dependencies):
    for dependency in dependencies:
        yield dependency
        yield from _flatten_dependencies(getattr(dependency, 'dependencies', []))


# --------------------------------------------------------------------------
# The point of all of it
# --------------------------------------------------------------------------

class TestOneUserCannotReachAnother:
    """
    Two real accounts, real work under the first, and every route the second
    could use to reach it.

    404 throughout, never 403: a 403 confirms the id exists.
    """

    # Class-scoped, and so registered once: the parametrised test below runs
    # fourteen times, and a function-scoped fixture would try to create the
    # same two accounts fourteen times and be refused after the first.
    @pytest.fixture(scope='class')
    def two_users(self):
        from fastapi.testclient import TestClient
        from app.main import app

        def sign_up(email):
            with TestClient(app) as bare:
                token = _register(bare, email).json()['data']['token']
            return TestClient(app, headers={'Authorization': f'Bearer {token}'})

        return sign_up('owner@radical.example.com'), sign_up('stranger@radical.example.com')

    @pytest.fixture(scope='class')
    def owned_work(self, two_users):
        """A project and a completed calibration belonging to the first user."""
        owner, _ = two_users
        project = owner.post('/api/projects', json={'name': 'Private dig'})
        assert project.status_code == 200, project.text
        project_id = project.json()['data']['id']

        analysis = owner.post('/api/analysis/calibrate', json={
            'sample_name': 'PRIVATE-1', 'age_bp': 3000, 'error_1sigma': 30,
            'curve_id': 'intcal20', 'save': True, 'project_id': project_id,
        })
        assert analysis.status_code == 200, analysis.text
        return project_id, analysis.json()['data']['id']

    def test_a_list_shows_only_your_own(self, two_users, owned_work):
        owner, stranger = two_users
        project_id, analysis_id = owned_work

        mine = owner.get('/api/projects').json()['data']['items']
        assert project_id in [p['id'] for p in mine]

        theirs = stranger.get('/api/projects').json()['data']['items']
        assert project_id not in [p['id'] for p in theirs]

        their_analyses = stranger.get('/api/analysis').json()['data']['items']
        assert analysis_id not in [a['id'] for a in their_analyses]

    @pytest.mark.parametrize('method,template', [
        ('get', '/api/projects/{project_id}'),
        ('patch', '/api/projects/{project_id}'),
        ('delete', '/api/projects/{project_id}'),
        ('post', '/api/projects/{project_id}/rerun'),
        ('get', '/api/projects/{project_id}/samples'),
        ('post', '/api/projects/{project_id}/samples'),
        ('get', '/api/analysis/{analysis_id}'),
        ('patch', '/api/analysis/{analysis_id}'),
        ('delete', '/api/analysis/{analysis_id}'),
        ('post', '/api/analysis/{analysis_id}/rerun'),
        ('post', '/api/analysis/{analysis_id}/duplicate'),
        ('get', '/api/results/{analysis_id}/export/csv'),
        ('get', '/api/results/{analysis_id}/export/json'),
        ('get', '/api/results/{analysis_id}/graphs'),
    ])
    def test_someone_else_s_row_is_a_404(self, two_users, owned_work, method, template):
        _, stranger = two_users
        project_id, analysis_id = owned_work
        path = template.format(project_id=project_id, analysis_id=analysis_id)

        # A body each route will actually accept. It has to pass validation,
        # because FastAPI validates before the handler runs -- a 422 here would
        # say nothing about ownership either way.
        body = {
            'name': 'An intruding name',
            'samples': [{'sample_name': 'X-1', 'age_bp': 3000, 'error_1sigma': 30}],
        }
        response = _call(stranger, method, path, body)
        assert response.status_code == 404, (
            f'{method.upper()} {template} answered {response.status_code}; '
            'someone else\'s row must be indistinguishable from a missing one.'
        )

    def test_the_owner_can_still_reach_all_of_it(self, two_users, owned_work):
        # The other half of the parametrised test above: a filter that returns
        # 404 for everybody would pass that one.
        owner, _ = two_users
        project_id, analysis_id = owned_work
        assert owner.get(f'/api/projects/{project_id}').status_code == 200
        assert owner.get(f'/api/analysis/{analysis_id}').status_code == 200
        assert owner.get(f'/api/results/{analysis_id}/export/csv').status_code == 200

    def test_an_analysis_cannot_be_filed_into_someone_else_s_project(
            self, two_users, owned_work):
        _, stranger = two_users
        project_id, _ = owned_work

        response = stranger.post('/api/analysis/calibrate', json={
            'sample_name': 'INTRUDER-1', 'age_bp': 3000, 'error_1sigma': 30,
            'curve_id': 'intcal20', 'save': True, 'project_id': project_id,
        })
        assert response.status_code == 404

    def test_a_job_belongs_to_whoever_started_it(self, two_users, owned_work):
        owner, stranger = two_users
        _, analysis_id = owned_work

        queued = owner.post(f'/api/analysis/{analysis_id}/rerun')
        assert queued.status_code == 202, queued.text
        job_id = queued.json()['data']['job_id']

        assert owner.get(f'/api/jobs/{job_id}').status_code == 200
        assert stranger.get(f'/api/jobs/{job_id}').status_code == 404
        # ...and cannot be stopped by them either, which is the one that would
        # let a stranger interfere rather than merely look.
        assert stranger.delete(f'/api/jobs/{job_id}').status_code == 404

    def test_a_bulk_delete_cannot_reach_someone_else_s_analysis(self, two_users, owned_work):
        owner, stranger = two_users
        _, analysis_id = owned_work

        response = stranger.request('DELETE', '/api/analysis',
                                    json={'analysis_ids': [analysis_id]})
        assert response.status_code == 404
        assert owner.get(f'/api/analysis/{analysis_id}').status_code == 200


class TestCurvesAreTheException:
    """
    Curves are shared reference data: readable by anyone signed in, writable
    only by whoever imported one. See models.StoredCalibrationCurve.
    """

    def test_everyone_can_read_the_curve_list(self, anonymous_client):
        from fastapi.testclient import TestClient
        from app.main import app

        token = _register(anonymous_client, 'curvereader@radical.example.com'
                          ).json()['data']['token']
        reader = TestClient(app, headers={'Authorization': f'Bearer {token}'})

        response = reader.get('/api/curves')
        assert response.status_code == 200
        ids = [c['id'] for c in response.json()['data']['items']]
        assert 'intcal20' in ids

    def test_a_builtin_curve_still_cannot_be_edited_by_anyone(self, client):
        # Pre-existing behaviour, restated here because the new owner check
        # sits in front of it and must not have replaced it.
        assert client.patch('/api/curves/intcal20',
                            json={'description': 'mine now'}).status_code == 403


class TestAdoptingTheWorkThatCameBefore:
    def test_exactly_one_account_is_the_first(self, client):
        # Asserted as a property rather than of any particular fixture: which
        # account registered first depends on what else ran, and the invariant
        # that matters is that the flag names exactly one of them.
        from app.database import SessionLocal
        from app.models import User

        with SessionLocal() as db:
            firsts = db.query(User).filter(User.is_first_account.is_(True)).count()
            total = db.query(User).count()
        assert total >= 1
        assert firsts == 1

    def test_a_later_account_is_not(self, anonymous_client):
        later = _register(anonymous_client, 'latecomer@radical.example.com').json()['data']
        assert later['user']['is_first_account'] is False
        assert later['adopted_existing_records'] == 0

    def test_adoption_leaves_nothing_ownerless(self, client):
        # The failure this guards against is an upgrade appearing to delete
        # every analysis in the database, because an ownerless row is visible
        # to nobody.
        from app.database import SessionLocal
        from app.models import Analysis, Project

        with SessionLocal() as db:
            assert db.query(Project).filter(Project.owner_id.is_(None)).count() == 0
            assert db.query(Analysis).filter(Analysis.owner_id.is_(None)).count() == 0


# --------------------------------------------------------------------------
# Personal access tokens
# --------------------------------------------------------------------------

class TestAccessTokens:
    """
    Long-lived tokens for scripts. What matters: the secret is shown once and
    never stored, a read-only token cannot write, expiry and revocation take
    effect at once, and a token cannot manage tokens or the password.
    """

    @pytest.fixture(scope='class')
    def owner(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as bare:
            token = _register(bare, 'scripts@radical.example.com').json()['data']['token']
        return TestClient(app, headers={'Authorization': f'Bearer {token}'})

    @staticmethod
    def _with(token):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app, headers={'Authorization': f'Bearer {token}'})

    @staticmethod
    def _make(owner, **body):
        response = owner.post('/api/auth/tokens', json={'name': 'script', **body})
        assert response.status_code == 201, response.text
        return response.json()['data']

    def test_the_secret_is_shown_once_and_stored_only_as_a_hash(self, owner):
        from app.database import SessionLocal
        from app.models import PersonalAccessToken
        made = self._make(owner, name='once')
        assert made['token'].startswith('rct_') and len(made['token']) > 40
        listed = owner.get('/api/auth/tokens').json()['data']
        row = next(t for t in listed if t['id'] == made['id'])
        assert 'token' not in row and made['token'].startswith(row['prefix'])
        with SessionLocal() as db:
            stored = db.query(PersonalAccessToken).filter_by(id=made['id']).one()
            assert made['token'] not in (stored.token_hash, stored.prefix)
            assert stored.token_hash == auth.access_token_hash(made['token'])

    def test_a_token_signs_its_owner_in_and_records_its_use(self, owner):
        made = self._make(owner, scope='full')
        me = self._with(made['token']).get('/api/auth/me')
        assert me.status_code == 200
        assert me.json()['data']['email'] == 'scripts@radical.example.com'
        listed = owner.get('/api/auth/tokens').json()['data']
        assert next(t for t in listed if t['id'] == made['id'])['last_used_at']

    def test_a_read_only_token_reads_and_cannot_write(self, owner):
        reader = self._with(self._make(owner, scope='read')['token'])
        assert reader.get('/api/projects').status_code == 200
        refused = reader.post('/api/projects', json={'name': 'Not allowed'})
        assert refused.status_code == 403
        assert 'read-only' in refused.json()['detail']

    def test_a_full_token_can_write(self, owner):
        writer = self._with(self._make(owner, scope='full')['token'])
        assert writer.post('/api/projects', json={'name': 'From a script'}).status_code == 200

    def test_the_default_is_read_only_for_ninety_days(self, owner):
        made = self._make(owner)
        assert made['scope'] == 'read'
        created = datetime.fromisoformat(made['created_at'])
        expires = datetime.fromisoformat(made['expires_at'])
        assert expires - created == timedelta(days=90)

    def test_only_the_offered_lifetimes_are_accepted(self, owner):
        for days in (0, 1000, 400):
            response = owner.post('/api/auth/tokens', json={'name': 'x', 'days': days})
            assert response.status_code == 422, days
        assert owner.post('/api/auth/tokens', json={'name': 'x', 'scope': 'admin'}).status_code == 422

    def test_an_expired_token_is_refused(self, owner):
        from app.database import SessionLocal
        from app.models import PersonalAccessToken
        made = self._make(owner, scope='full')
        with SessionLocal() as db:
            row = db.query(PersonalAccessToken).filter_by(id=made['id']).one()
            row.expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()
        assert self._with(made['token']).get('/api/auth/me').status_code == 401
        listed = owner.get('/api/auth/tokens').json()['data']
        assert next(t for t in listed if t['id'] == made['id'])['expired'] is True

    def test_a_revoked_token_stops_working_at_once(self, owner):
        made = self._make(owner, scope='full')
        script = self._with(made['token'])
        assert script.get('/api/auth/me').status_code == 200
        assert owner.delete(f"/api/auth/tokens/{made['id']}").status_code == 200
        assert script.get('/api/auth/me').status_code == 401

    def test_a_wrong_token_is_refused_like_any_other(self, anonymous_client):
        assert self._with('rct_' + 'x' * 43).get('/api/auth/me').status_code == 401

    def test_a_token_cannot_manage_tokens_or_the_password(self, owner):
        script = self._with(self._make(owner, scope='full')['token'])
        assert script.get('/api/auth/tokens').status_code == 403
        assert script.post('/api/auth/tokens', json={'name': 'successor'}).status_code == 403
        assert script.post('/api/auth/password', json={
            'current_password': 'whatever', 'new_password': 'something new'}).status_code == 403

    def test_someone_else_cannot_see_or_revoke_yours(self, owner):
        from fastapi.testclient import TestClient
        from app.main import app
        made = self._make(owner)
        with TestClient(app) as bare:
            token = _register(bare, 'token-stranger@radical.example.com').json()['data']['token']
        stranger = TestClient(app, headers={'Authorization': f'Bearer {token}'})
        assert all(t['id'] != made['id'] for t in stranger.get('/api/auth/tokens').json()['data'])
        assert stranger.delete(f"/api/auth/tokens/{made['id']}").status_code == 404
        assert owner.get('/api/auth/tokens').status_code == 200


class TestTheSchemaIsUnderApi:
    """The schema and docs sit under /api so a proxy forwarding /api passes them."""

    def test_the_schema_is_served_and_declares_the_bearer_scheme(self, anonymous_client):
        schema = anonymous_client.get('/api/openapi.json')
        assert schema.status_code == 200
        schemes = schema.json()['components']['securitySchemes']
        assert any(s.get('scheme') == 'bearer' for s in schemes.values())

    def test_the_old_paths_redirect(self, anonymous_client):
        for old, new in (('/docs', '/api/docs'), ('/openapi.json', '/api/openapi.json'),
                         ('/redoc', '/api/redoc')):
            response = anonymous_client.get(old, follow_redirects=False)
            assert response.status_code == 308 and response.headers['location'] == new
        assert anonymous_client.get('/api/docs').status_code == 200
