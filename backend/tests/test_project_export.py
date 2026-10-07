"""
Tests for GET /api/projects/{id}/export (api/project_export.py, feature spec
W4): the whole project as one zip.

What is worth pinning is what the zip promises a script that reads it: each
analysis's JSON is exactly its own Export JSON, the inputs are enough to see
what was run, the collection goes straight back through the CSV importer, and
the summary has a row per Boundary and duration with a calendar age and a
length of time never in one column.
"""
import csv
import io
import itertools
import json
import zipfile

import pytest

_IDS = itertools.count()


def _date(name, age, **extra):
    return {'sample_name': name, 'age_bp': age, 'error_1sigma': 20,
            'material_type': 'charcoal', 'sample_type': 'terrestrial',
            'curve_id': 'shcal20', **extra}


def _project(client):
    """A project with a collection, a group calibration and a model with durations."""
    tag = f'pe{next(_IDS)}'
    project_id = client.post('/api/projects', json={'name': f'Export site {tag}'}).json()['data']['id']
    added = client.post(f'/api/projects/{project_id}/samples', json={'samples': [
        _date('OxA-1', 614, default_context_name='Layer 11'),
        _date('OxA-2', 627, default_context_name='Layer 11', notes='eggshell'),
    ]})
    assert added.status_code == 200, added.text

    group = client.post('/api/analysis/calibrate-group', json={
        'name': 'Group', 'project_id': project_id,
        'measurements': [{**_date(f'G{i}', a), 'id': f'{tag}_g{i}'}
                         for i, a in enumerate((656, 660, 650))],
    })
    assert group.status_code == 200, group.text

    tree = {'id': f'{tag}_root', 'element_type': 'sequence', 'name': 'Site', 'children': [
        {'id': f'{tag}_b0', 'element_type': 'boundary', 'name': 'Start', 'children': []},
        {'id': f'{tag}_p', 'element_type': 'phase', 'name': 'Layer 11', 'children': []},
        {'id': f'{tag}_b1', 'element_type': 'boundary', 'name': 'End', 'children': []},
    ]}
    model = client.post('/api/analysis/bayesian?wait=true', json={
        'name': 'Model / one', 'project_id': project_id,
        'measurements': [{**_date(f'M{i}', a), 'id': f'{tag}_m{i}', 'element_id': f'{tag}_p'}
                         for i, a in enumerate((614, 627, 645))],
        'model_tree': tree,
        'bayesian_config': {'estimate_durations': True},
        'save': True,
    })
    assert model.status_code == 200, model.text
    return project_id, group.json()['data']['id'], model.json()['data']['id']


def _zip(client, project_id, query=''):
    response = client.get(f'/api/projects/{project_id}/export{query}')
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'application/zip'
    return zipfile.ZipFile(io.BytesIO(response.content))


def _summary(archive):
    rows = list(csv.reader(io.StringIO(archive.read('summary.csv').decode())))
    return rows[0], [dict(zip(rows[0], r)) for r in rows[1:]]


class TestProjectExport:
    def test_holds_every_analysis_its_inputs_the_collection_and_a_summary(self, client):
        project_id, group_id, model_id = _project(client)
        archive = _zip(client, project_id)
        names = set(archive.namelist())
        assert {'manifest.json', 'summary.csv', 'samples.csv'} <= names
        analyses = sorted(n for n in names if n.startswith('analyses/'))
        inputs = sorted(n for n in names if n.startswith('inputs/'))
        assert len(analyses) == len(inputs) == 2
        assert [n.split('/')[1] for n in analyses] == [n.split('/')[1] for n in inputs]

        manifest = json.loads(archive.read('manifest.json'))
        assert [a['id'] for a in manifest['analyses']] == [group_id, model_id]
        assert manifest['samples'] == 2

    def test_each_analysis_is_its_own_export_json_unchanged(self, client):
        project_id, _group_id, model_id = _project(client)
        archive = _zip(client, project_id)
        [name] = [n for n in archive.namelist()
                  if n.startswith('analyses/') and model_id[:8] in n]
        # A file name built from the analysis name, with the slash made safe.
        assert 'Model-one' in name
        in_zip = json.loads(archive.read(name))
        alone = client.get(f'/api/results/{model_id}/export/json').json()
        assert in_zip == json.loads(json.dumps(alone, default=str))

    def test_inputs_carry_settings_dates_and_the_tree_without_results(self, client):
        project_id, _group_id, model_id = _project(client)
        archive = _zip(client, project_id)
        [name] = [n for n in archive.namelist()
                  if n.startswith('inputs/') and model_id[:8] in n]
        inputs = json.loads(archive.read(name))
        assert inputs['bayesian_config']['estimate_durations'] is True
        assert sorted(m['age_bp'] for m in inputs['measurements']) == [614, 627, 645]
        assert 'posterior' not in json.dumps(inputs['model_tree'])
        assert [c['name'] for c in inputs['model_tree']['children']] == ['Start', 'Layer 11', 'End']

    def test_the_collection_goes_back_through_the_importer(self, client):
        project_id, _g, _m = _project(client)
        archive = _zip(client, project_id)
        content = archive.read('samples.csv')
        response = client.post('/api/import/dates',
                               files={'file': ('samples.csv', io.BytesIO(content), 'text/plain')})
        assert response.status_code == 200, response.text
        data = response.json()['data']
        assert data['ok'] is True, data
        assert [d['sample_name'] for d in data['dates']] == ['OxA-1', 'OxA-2']

    def test_summary_has_a_row_per_boundary_and_duration(self, client):
        project_id, group_id, model_id = _project(client)
        header, rows = _summary(_zip(client, project_id))
        model_rows = [r for r in rows if r['Analysis ID'] == model_id]
        boundaries = [r for r in model_rows if r['Quantity'] == 'Boundary']
        durations = [r for r in model_rows if 'duration' in r['Quantity'].lower()]
        assert [r['Name'] for r in boundaries] == ['Start', 'End']
        assert durations
        # A calendar age and a length of time never share a column.
        assert all(r['Median BP'] and not r['Median (yr)'] for r in boundaries)
        assert all(r['Median (yr)'] and not r['Median BP'] for r in durations)
        assert model_rows[0]['Seed'] in ('', '42')
        # An analysis with nothing modelled is still listed.
        assert [r['Quantity'] for r in rows if r['Analysis ID'] == group_id] == ['']

    def test_date_format_and_threshold_add_their_columns(self, client):
        project_id, _group_id, model_id = _project(client)
        archive = _zip(client, project_id, '?date_format=bcad&p_before_bp=590')
        header, rows = _summary(archive)
        assert header[-3:] == ['Median (BC/AD)', '95.4% HPD (BC/AD)', 'P Before AD 1360']
        start = next(r for r in rows if r['Analysis ID'] == model_id and r['Name'] == 'Start')
        assert start['Median (BC/AD)'].startswith('AD ')
        assert 0.0 <= float(start['P Before AD 1360']) <= 1.0
        [name] = [n for n in archive.namelist()
                  if n.startswith('analyses/') and model_id[:8] in n]
        assert json.loads(archive.read(name))['data']['p_before']['cal_bp'] == 590

    def test_only_the_owner_can_export(self, client, anonymous_client):
        project_id, _g, _m = _project(client)
        assert anonymous_client.get(f'/api/projects/{project_id}/export').status_code == 401
        assert client.get('/api/projects/no-such-project/export').status_code == 404

    @pytest.mark.parametrize('query', ['?p_before_bp=99999'])
    def test_refuses_a_threshold_off_every_curve(self, client, query):
        project_id = client.post('/api/projects', json={'name': 'Empty'}).json()['data']['id']
        assert client.get(f'/api/projects/{project_id}/export{query}').status_code == 422
