"""
Tests for model templates (api/model_templates.py): a model's structure and
settings saved without its dates, private to its owner, and moved between
accounts as a JSON file.
"""
import io
import itertools
import json

from tests.test_curve_privacy import _client_for

_IDS = itertools.count()


def _tree(tag, wiggle=False):
    """A bounded two-phase sequence, as the editor builds one."""
    children = [
        {'id': f'{tag}-b0', 'element_type': 'boundary', 'name': 'Start', 'children': [],
         'posterior': {'posterior_median': 700}},
        {'id': f'{tag}-p1', 'element_type': 'phase', 'name': 'Layer 3', 'children': []},
        {'id': f'{tag}-b1', 'element_type': 'boundary', 'name': 'Transition', 'children': []},
        {'id': f'{tag}-p2', 'element_type': 'phase', 'name': 'Layer 2', 'children': []},
        {'id': f'{tag}-b2', 'element_type': 'boundary', 'name': 'End', 'children': []},
    ]
    if wiggle:
        children.insert(3, {'id': f'{tag}-w', 'element_type': 'wiggle_sequence',
                            'name': 'Post', 'children': [],
                            'params': {'gap_units': 'rings', 'reference_sample_id': 'date-1'}})
    return {'id': f'{tag}-root', 'element_type': 'sequence', 'name': 'Site',
            'tpq_age_bp': 900, 'taq_age_bp': 400, 'children': children}


def _save(client, name=None, **extra):
    tag = f'mt{next(_IDS)}'
    body = {'name': name or f'Two layers {tag}', 'model_tree': _tree(tag),
            'bayesian_config': {'solver': 'exact', 'resolution': 10,
                                'estimate_durations': True,
                                'dispersal_origin': {'lat': -45.9, 'lon': 170.5}},
            **extra}
    response = client.post('/api/model-templates', json=body)
    assert response.status_code == 200, response.text
    return response.json()['data']


def _file(client, query=''):
    response = client.get(f'/api/model-templates/export{query}')
    assert response.status_code == 200, response.text
    return response.content


def _post_file(client, path, content):
    return client.post(path, files={'file': ('model-templates.json', io.BytesIO(content),
                                             'application/json')})


class TestSaving:
    def test_keeps_structure_and_settings_and_drops_results(self, client):
        saved = _save(client)
        tree = saved['model_tree']
        assert [c['name'] for c in tree['children']] == [
            'Start', 'Layer 3', 'Transition', 'Layer 2', 'End']
        assert tree['tpq_age_bp'] == 900
        assert 'posterior' not in json.dumps(tree)
        assert saved['phases'] == 2 and saved['boundaries'] == 3
        assert saved['bayesian_config']['solver'] == 'exact'
        assert saved['bayesian_config']['resolution'] == 10
        assert 'dispersal_origin' not in saved['bayesian_config']

    def test_ids_are_its_own_and_nothing_names_a_date(self, client):
        tag = f'mt{next(_IDS)}'
        response = client.post('/api/model-templates', json={
            'name': f'Wiggle {tag}', 'model_tree': _tree(tag, wiggle=True)})
        assert response.status_code == 200, response.text
        tree = response.json()['data']['model_tree']
        assert not any(c['id'].startswith(tag) for c in tree['children'])
        wiggle = next(c for c in tree['children'] if c['element_type'] == 'wiggle_sequence')
        assert wiggle['params'] == {'gap_units': 'rings'}

    def test_an_impossible_structure_is_refused(self, client):
        tag = f'mt{next(_IDS)}'
        tree = _tree(tag)
        tree['children'][1]['children'] = [{'id': f'{tag}-x', 'element_type': 'boundary',
                                            'children': []}]
        response = client.post('/api/model-templates',
                               json={'name': 'Bad', 'model_tree': tree})
        assert response.status_code == 422
        assert 'boundary cannot sit directly inside a phase' in response.text

    def test_rename_and_delete(self, client):
        saved = _save(client)
        renamed = client.patch(f"/api/model-templates/{saved['id']}",
                               json={'name': 'Renamed', 'description': 'Two layers'})
        assert renamed.status_code == 200
        assert renamed.json()['data']['name'] == 'Renamed'
        assert renamed.json()['data']['description'] == 'Two layers'
        assert client.delete(f"/api/model-templates/{saved['id']}").status_code == 200
        ids = [t['id'] for t in client.get('/api/model-templates').json()['data']['items']]
        assert saved['id'] not in ids

    def test_another_account_cannot_see_or_change_them(self, client):
        saved = _save(client)
        other = _client_for(f'templates{next(_IDS)}@radical.example.com')
        assert other.get('/api/model-templates').json()['data']['items'] == []
        assert other.patch(f"/api/model-templates/{saved['id']}",
                           json={'name': 'Mine'}).status_code == 404
        assert other.delete(f"/api/model-templates/{saved['id']}").status_code == 404
        assert other.get(f"/api/model-templates/export?template={saved['id']}").status_code == 404


class TestFiles:
    def test_a_file_moves_templates_to_another_account(self, client):
        first, second = _save(client), _save(client)
        content = _file(client, f"?template={first['id']}&template={second['id']}")
        data = json.loads(content)
        assert data['format'] == 'radical-model-templates'
        assert [t['name'] for t in data['templates']] == sorted([first['name'], second['name']])

        other = _client_for(f'templates{next(_IDS)}@radical.example.com')
        preview = _post_file(other, '/api/import/model-templates/preview', content).json()['data']
        assert preview['ok'] is True
        assert [t['action'] for t in preview['templates']] == ['add', 'add']
        assert 'model_tree' not in preview['templates'][0]
        assert other.get('/api/model-templates').json()['data']['items'] == []

        imported = _post_file(other, '/api/import/model-templates', content)
        assert imported.status_code == 200, imported.text
        assert imported.json()['data'] == {'added': 2, 'skipped': 0}
        items = other.get('/api/model-templates').json()['data']['items']
        assert sorted(t['name'] for t in items) == sorted([first['name'], second['name']])
        mine = next(t for t in items if t['name'] == first['name'])
        assert mine['bayesian_config'] == first['bayesian_config']

    def test_the_same_template_is_skipped_and_a_name_clash_renamed(self, client):
        saved = _save(client)
        content = _file(client, f"?template={saved['id']}")
        preview = _post_file(client, '/api/import/model-templates/preview', content).json()['data']
        assert [t['action'] for t in preview['templates']] == ['same']
        assert _post_file(client, '/api/import/model-templates', content).json()['data'] == {
            'added': 0, 'skipped': 1}

        changed = json.loads(content)
        changed['templates'][0]['bayesian_config']['resolution'] = 20
        preview = _post_file(client, '/api/import/model-templates/preview',
                             json.dumps(changed).encode()).json()['data']
        assert preview['templates'][0]['action'] == 'rename'
        assert preview['templates'][0]['saved_as'] == f"{saved['name']} (2)"

    def test_a_bad_template_fails_the_whole_file(self, client):
        saved = _save(client)
        data = json.loads(_file(client, f"?template={saved['id']}"))
        broken = copy_of(data['templates'][0])
        broken['name'] = 'Broken'
        broken['model_tree']['element_type'] = 'boundary'
        data['templates'].append(broken)
        other = _client_for(f'templates{next(_IDS)}@radical.example.com')
        response = _post_file(other, '/api/import/model-templates', json.dumps(data).encode())
        assert response.status_code == 422
        assert 'Broken' in response.text
        assert other.get('/api/model-templates').json()['data']['items'] == []

    def test_a_file_that_is_not_a_templates_file(self, client):
        preview = _post_file(client, '/api/import/model-templates/preview',
                             b'sample_name,age_bp\n').json()['data']
        assert preview['ok'] is False
        newer = json.dumps({'format': 'radical-model-templates', 'format_version': 99,
                            'templates': []}).encode()
        preview = _post_file(client, '/api/import/model-templates/preview', newer).json()['data']
        assert 'newer version' in preview['errors'][0]


def copy_of(value):
    return json.loads(json.dumps(value))
