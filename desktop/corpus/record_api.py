"""
A pytest plugin that records every HTTP exchange the backend's own tests
make, in the order they make them, for radical-server's replay test.

    cd backend && RECORD_OUT=/tmp/exchanges.json \\
        python -m pytest -p no:cacheprovider -p record_api tests/
    (with desktop/corpus on PYTHONPATH; generate.py --exchanges does this)

The suite shares one database across its tests, so the replay runs every
exchange in order against one Rust database too. Each exchange keeps which
credentials it was sent with (a short hash), so the replay can skip the
ones a second account or an anonymous client made: the desktop app has one
user and no sign-in.
"""
import base64
import copy
import hashlib
import json
import os

EXCHANGES = []
CURRENT = {'test': None}


def pytest_runtest_setup(item):
    CURRENT['test'] = item.nodeid


def _response_body(response):
    ctype = response.headers.get('content-type', '')
    if 'json' in ctype:
        try:
            return {'response_json': response.json()}
        except ValueError:
            pass
    raw = response.content
    if ctype.startswith('text/') or 'csv' in ctype:
        return {'response_text': raw.decode('utf-8', 'replace')}
    return {'response_sha256': hashlib.sha256(raw).hexdigest(), 'response_len': len(raw)}


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
        response = original(self, method, url, **kwargs)
        auth = (kwargs.get('headers') or {}).get('Authorization') or self.headers.get('Authorization')
        data = kwargs.get('data')
        content = kwargs.get('content')
        EXCHANGES.append({
            'test': CURRENT['test'],
            'auth': hashlib.sha256(auth.encode()).hexdigest()[:8] if auth else None,
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
