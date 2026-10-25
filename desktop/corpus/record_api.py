"""
A pytest plugin that records every HTTP exchange the backend's own tests
make, in the order they make them, for radical-server's replay test.

    cd backend && RECORD_OUT=/tmp/exchanges.json \\
        python -m pytest -p no:cacheprovider -p record_api tests/
    (with desktop/corpus on PYTHONPATH; generate.py --exchanges does this)

The suite shares one database across its tests, so the replay runs every
exchange in order against one Rust database too. Each exchange keeps which
credentials it was sent with -- a short hash, and what kind of token it was
-- so the replay can send each request as the account that made it, or, in
single-user mode, skip the ones a second account or nobody made.
"""
import base64
import copy
import hashlib
import json
import os

EXCHANGES = []
CURRENT = {'test': None, 'cleanup': []}

# Fixtures that undo, behind the API, what their test stored -- which the
# replay has to undo too, or the next test sees rows the backend's did not.
CLEANUP_FIXTURES = ('remove_stored_curves',)


def pytest_runtest_setup(item):
    CURRENT['test'] = item.nodeid
    CURRENT['cleanup'] = [f for f in CLEANUP_FIXTURES if f in item.fixturenames]


def _auth_kind(header):
    """As api/auth.current_user reads the header."""
    if not header:
        return None
    scheme, _, token = header.partition(' ')
    if scheme.lower() != 'bearer' or not token.strip():
        return 'other'
    return 'rct' if token.strip().startswith('rct_') else 'jwt'


def _zip_members(raw):
    """A zip's members by name: text as text, anything else as its hash."""
    import io
    import zipfile
    members = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for name in archive.namelist():
            data = archive.read(name)
            try:
                members[name] = data.decode('utf-8')
            except UnicodeDecodeError:
                members[name] = {'sha256': hashlib.sha256(data).hexdigest()}
    return members


def _response_body(response):
    ctype = response.headers.get('content-type', '')
    if 'zip' in ctype:
        return {'response_zip': _zip_members(response.content)}
    if 'json' in ctype:
        try:
            return {'response_json': response.json()}
        except ValueError:
            pass
    raw = response.content
    if ctype.startswith('text/') or 'csv' in ctype:
        return {'response_text': raw.decode('utf-8', 'replace')}
    return {'response_sha256': hashlib.sha256(raw).hexdigest(), 'response_len': len(raw)}


def _readable(files):
    out = {}
    for key, value in (files.items() if isinstance(files, dict) else files):
        if isinstance(value, (tuple, list)):
            content = value[1]
            if hasattr(content, 'read'):
                content = content.read()
            out[key] = (value[0], content, *value[2:])
        else:
            out[key] = value.read() if hasattr(value, 'read') else value
    return out


def _files(files):
    if not files:
        return None
    out = {}
    for key, value in (files.items() if isinstance(files, dict) else files):
        name, content = value[0], value[1]
        if hasattr(content, 'read'):
            content = content.read()
        if isinstance(content, str):
            content = content.encode()
        out[key] = {'filename': name, 'b64': base64.b64encode(content).decode()}
    return out


def pytest_configure(config):
    from starlette.testclient import TestClient
    original = TestClient.request

    def recording(self, method, url, **kwargs):
        # Copied before sending: a test that edits its body and posts it
        # again would otherwise rewrite the earlier exchange's request too.
        sent = copy.deepcopy({k: kwargs.get(k) for k in ('json', 'params')})
        # A file object is read by the request, so it is read here first and
        # sent as the bytes it held: recorded afterwards, it would be empty.
        if kwargs.get('files'):
            kwargs['files'] = _readable(kwargs['files'])
        # RECORD_DUMP_AT=N writes the analyses filed under no project, as the
        # database holds them just before exchange N: for finding where a
        # replay's count parts from the backend's.
        if os.environ.get('RECORD_DUMP_AT') == str(len(EXCHANGES)):
            from app.database import SessionLocal
            from app.models import Analysis
            with SessionLocal() as db:
                rows = db.query(Analysis).filter(Analysis.project_id.is_(None)).all()
                with open(os.environ['RECORD_OUT'] + '.dump', 'w') as fh:
                    for a in rows:
                        fh.write(f'{a.id} {a.owner_id} {a.status} {a.name}\n')
        response = original(self, method, url, **kwargs)
        auth = (kwargs.get('headers') or {}).get('Authorization') or self.headers.get('Authorization')
        data = kwargs.get('data')
        content = kwargs.get('content')
        EXCHANGES.append({
            'test': CURRENT['test'],
            'cleanup': CURRENT['cleanup'],
            'auth': hashlib.sha256(auth.encode()).hexdigest()[:8] if auth else None,
            # What the backend takes the header for -- a personal access
            # token, a sign-in token, or no bearer token at all -- so a
            # replay can send a bad one of the same kind in place of a token
            # no response here issued.
            'auth_kind': _auth_kind(auth),
            'method': method.upper(),
            'url': str(url).replace('http://testserver', ''),
            'params': sent['params'],
            'request_json': sent['json'],
            'request_data': data if isinstance(data, (dict, str)) else None,
            'request_content': (content.decode('utf-8', 'replace') if isinstance(content, bytes)
                                else content if isinstance(content, str) else None),
            'files': _files(kwargs.get('files')),
            'status': response.status_code,
            'content_type': response.headers.get('content-type'),
            **_response_body(response),
        })
        return response

    TestClient.request = recording


def pytest_unconfigure(config):
    with open(os.environ['RECORD_OUT'], 'w', encoding='utf-8') as fh:
        json.dump(EXCHANGES, fh, default=str)
