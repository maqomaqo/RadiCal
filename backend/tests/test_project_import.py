"""
Tests for the project package round trip: GET /api/projects/{id}/export as
the package, and POST /api/import/project(/preview) reading it back in
(api/project_import.py).

What is pinned is what a second researcher relies on. The package rebuilds
every analysis and the collection as a new project; the curves an analysis
needs travel with it and are matched by their numbers; the author's run is
kept beside the importer's own, so re-running reports whether this
installation agrees; and a damaged or foreign file is refused whole.
"""
import io
import itertools
import json
import zipfile

import pytest

from tests.test_curve_import import RISING, curve_text, store_curve
from tests.test_curve_privacy import _client_for

_IDS = itertools.count()

pytestmark = pytest.mark.usefixtures('remove_stored_curves')


def _date(name, age, **extra):
    return {'sample_name': name, 'age_bp': age, 'error_1sigma': 20,
            'material_type': 'charcoal', 'sample_type': 'terrestrial',
            'curve_id': 'shcal20', **extra}


def _project(client, curve_id='shcal20', precision=None):
    """A project with a collection, a group calibration and a bounded model."""
    tag = f'pi{next(_IDS)}'
    project_id = client.post('/api/projects', json={
        'name': f'Import site {tag}', 'description': 'A described site'}).json()['data']['id']
    if precision:
        client.patch(f'/api/projects/{project_id}', json={'coordinate_precision': precision})
    added = client.post(f'/api/projects/{project_id}/samples', json={'samples': [
        _date('OxA-1', 614, default_context_name='Layer 11', curve_id=curve_id,
              latitude=-45.87421, longitude=170.50361),
        _date('OxA-2', 627, default_context_name='Layer 11', notes='eggshell'),
        _date('Unused-9', 700),
    ]})
    assert added.status_code == 200, added.text

    group = client.post('/api/analysis/calibrate-group', json={
        'name': 'Group', 'project_id': project_id,
        'measurements': [{**_date(f'G{i}', a), 'id': f'{tag}_g{i}'}
                         for i, a in enumerate((656, 660, 650))],
    })
    assert group.status_code == 200, group.text

    tree = {'id': f'{tag}_root', 'element_type': 'sequence', 'name': 'Site',
            'tpq_age_bp': 800, 'taq_age_bp': 450, 'children': [
        {'id': f'{tag}_b0', 'element_type': 'boundary', 'name': 'Start', 'children': []},
        {'id': f'{tag}_p', 'element_type': 'phase', 'name': 'Layer 11', 'children': []},
        {'id': f'{tag}_b1', 'element_type': 'boundary', 'name': 'End', 'children': []},
    ]}
    model = client.post('/api/analysis/bayesian?wait=true', json={
        'name': 'Model', 'project_id': project_id,
        'measurements': [
            {**_date('OxA-1', 614, curve_id=curve_id, latitude=-45.87421,
                     longitude=170.50361, outlier_prior=0.05),
             'id': f'{tag}_m0', 'element_id': f'{tag}_p'},
            {**_date('OxA-2', 627), 'id': f'{tag}_m1', 'element_id': f'{tag}_p'},
            {**_date('M3', 645), 'id': f'{tag}_m2', 'element_id': f'{tag}_p'},
        ],
        'model_tree': tree,
        'bayesian_config': {'estimate_durations': True},
        'save': True,
    })
    assert model.status_code == 200, model.text
    return project_id, group.json()['data']['id'], model.json()['data']['id']


def _package(client, project_id, query=''):
    response = client.get(f'/api/projects/{project_id}/export{query}')
    assert response.status_code == 200, response.text
    return response.content


def _preview(client, content, filename='package.zip'):
    response = client.post('/api/import/project/preview',
                           files={'file': (filename, io.BytesIO(content), 'application/zip')})
    assert response.status_code == 200, response.text
    return response.json()['data']


def _import(client, content, name=None):
    response = client.post('/api/import/project',
                           files={'file': ('package.zip', io.BytesIO(content), 'application/zip')},
                           data={'name': name} if name else {})
    return response


def _rewrite(content, change):
    """The package with `change(files)` applied to its members."""
    source = zipfile.ZipFile(io.BytesIO(content))
    files = {name: source.read(name) for name in source.namelist()}
    change(files)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return out.getvalue()


class TestThePackage:
    def test_carries_a_format_version_the_runs_and_a_full_collection(self, client):
        project_id, _group_id, model_id = _project(client)
        archive = zipfile.ZipFile(io.BytesIO(_package(client, project_id)))
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['format'] == 'radical-project'
        assert manifest['format_version'] == 1
        assert manifest['selection'] is False
        assert [c['id'] for c in manifest['curves']] == ['shcal20']
        assert all(a['has_run'] for a in manifest['analyses'])
        runs = [n for n in archive.namelist() if n.startswith('runs/')]
        assert len(runs) == 2 and any(model_id[:8] in n for n in runs)
        samples = json.loads(archive.read('samples.json'))
        assert [s['sample_name'] for s in samples] == ['OxA-1', 'OxA-2', 'Unused-9']
        assert samples[0]['default_context_name'] == 'Layer 11'

    def test_an_edited_unrun_analysis_carries_no_run(self, client):
        project_id, _group_id, model_id = _project(client)
        current = client.get(f'/api/analysis/{model_id}').json()['data']
        edited = client.put(f'/api/analysis/{model_id}/inputs', json={
            'name': 'Model', 'measurements': [
                {**{k: m[k] for k in ('id', 'sample_name', 'age_bp', 'error_1sigma',
                                      'material_type', 'sample_type', 'curve_id')},
                 'element_id': m.get('element_id')}
                for m in current['measurements']],
            'model_tree': current['model_tree'],
            'bayesian_config': {'estimate_durations': False},
        })
        assert edited.status_code == 200, edited.text
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(
            _package(client, project_id))).read('manifest.json'))
        by_id = {a['id']: a for a in manifest['analyses']}
        assert by_id[model_id]['has_run'] is False

    def test_a_selection_holds_only_its_analyses_and_the_dates_they_use(self, client):
        project_id, group_id, model_id = _project(client)
        archive = zipfile.ZipFile(io.BytesIO(
            _package(client, project_id, f'?analysis={model_id}')))
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['selection'] is True
        assert [a['id'] for a in manifest['analyses']] == [model_id]
        assert len([n for n in archive.namelist() if n.startswith('inputs/')]) == 1
        names = [s['sample_name'] for s in json.loads(archive.read('samples.json'))]
        assert names == ['OxA-1', 'OxA-2']

    def test_a_selection_names_only_this_projects_analyses(self, client):
        project_id, _g, _m = _project(client)
        _other, _g2, other_model = _project(client)
        response = client.get(f'/api/projects/{project_id}/export?analysis={other_model}')
        assert response.status_code == 404

    def test_unassigned_analyses_export_as_a_selection(self, client):
        made = client.post('/api/analysis/calibrate', json={**_date('Loose-1', 600), 'save': True})
        assert made.status_code == 200, made.text
        loose = made.json()['data']['id']
        assert client.get('/api/projects/unassigned/export').status_code == 422
        archive = zipfile.ZipFile(io.BytesIO(
            _package(client, 'unassigned', f'?analysis={loose}')))
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['project']['name'] == 'Unassigned'
        assert [a['id'] for a in manifest['analyses']] == [loose]

    def test_positions_leave_at_the_projects_precision_everywhere(self, client):
        project_id, _g, model_id = _project(client, precision='0.1')
        archive = zipfile.ZipFile(io.BytesIO(_package(client, project_id)))
        text = '\n'.join(archive.read(n).decode() for n in archive.namelist()
                         if n.endswith('.json') or n.endswith('.csv'))
        assert '45.87421' not in text and '170.50361' not in text
        assert '-45.9' in text


class TestImporting:
    def test_preview_lists_what_the_import_would_do_and_stores_nothing(self, client):
        project_id, _g, _m = _project(client)
        before = len(client.get('/api/projects').json()['data']['items'])
        data = _preview(client, _package(client, project_id))
        assert data['ok'] is True, data
        assert data['project_name'].startswith('Import site')
        assert data['samples'] == 3
        assert [(a['name'], a['analysis_type'], a['measurements'], a['has_run'])
                for a in data['analyses']] == [
            ('Group', 'group_calibration', 3, True), ('Model', 'bayesian', 3, True)]
        assert [(c['id'], c['action']) for c in data['curves']] == [('shcal20', 'shipped')]
        assert len(client.get('/api/projects').json()['data']['items']) == before

    def test_import_rebuilds_the_project_as_a_new_one(self, client):
        project_id, _g, model_id = _project(client)
        response = _import(client, _package(client, project_id), name='Replication')
        assert response.status_code == 200, response.text
        result = response.json()['data']
        assert result['analyses'] == 2 and result['samples'] == 3
        new_id = result['project_id']
        assert new_id != project_id

        project = client.get(f'/api/projects/{new_id}').json()['data']
        assert project['name'] == 'Replication'
        assert project['description'] == 'A described site'
        assert sorted(a['name'] for a in project['analyses']) == ['Group', 'Model']
        assert all(a['status'] == 'pending' for a in project['analyses'])

        samples = client.get(f'/api/projects/{new_id}/samples').json()['data']['items']
        assert [s['sample_name'] for s in samples] == ['OxA-1', 'OxA-2', 'Unused-9']
        assert samples[0]['default_context_name'] == 'Layer 11'

        imported = next(a for a in project['analyses'] if a['name'] == 'Model')
        original = client.get(f'/api/analysis/{model_id}').json()['data']
        copy = client.get(f"/api/analysis/{imported['id']}").json()['data']
        assert [c['name'] for c in copy['model_tree']['children']] == ['Start', 'Layer 11', 'End']
        assert copy['model_tree']['tpq_age_bp'] == 800
        assert copy['bayesian_config']['estimate_durations'] is True
        assert ([(m['sample_name'], m['age_bp'], m['outlier_prior']) for m in copy['measurements']]
                == [(m['sample_name'], m['age_bp'], m['outlier_prior'])
                    for m in original['measurements']])
        # New rows throughout: nothing of the original's is shared.
        assert not ({m['id'] for m in copy['measurements']}
                    & {m['id'] for m in original['measurements']})
        assert copy['model_tree']['id'] != original['model_tree']['id']
        phase = copy['model_tree']['children'][1]['id']
        assert all(m['element_id'] == phase for m in copy['measurements'])

    def test_the_same_package_imports_twice(self, client):
        project_id, _g, _m = _project(client)
        content = _package(client, project_id)
        assert _import(client, content).status_code == 200
        assert _import(client, content).status_code == 200

    def test_rerunning_compares_against_the_authors_run(self, client):
        project_id, _g, _m = _project(client)
        new_id = _import(client, _package(client, project_id)).json()['data']['project_id']
        for analysis in client.get(f'/api/projects/{new_id}').json()['data']['analyses']:
            runs = client.get(f"/api/analysis/{analysis['id']}/runs").json()['data']['runs']
            assert len(runs) == 1
            rerun = client.post(f"/api/analysis/{analysis['id']}/rerun?wait=true")
            assert rerun.status_code == 200, rerun.text
        diff = client.get(f'/api/projects/{new_id}/run-diff').json()['data']
        # Same inputs, same code, same seed: the author's answer, reproduced.
        assert diff['compared'] == 2
        assert diff['changed'] == []

    def test_a_changed_answer_is_reported_against_the_authors(self, client):
        project_id, _g, model_id = _project(client)
        content = _package(client, project_id)

        def move_the_authors_start(files):
            for name in [n for n in files if n.startswith('runs/') and model_id[:8] in n]:
                run = json.loads(files[name])
                start = run['payload']['model_tree']['children'][0]
                start['posterior']['posterior_median'] += 50
                files[name] = json.dumps(run).encode()

        new_id = _import(client, _rewrite(content, move_the_authors_start)).json()['data']['project_id']
        model = next(a for a in client.get(f'/api/projects/{new_id}').json()['data']['analyses']
                     if a['name'] == 'Model')
        client.post(f"/api/analysis/{model['id']}/rerun?wait=true")
        diff = client.get(f'/api/projects/{new_id}/run-diff').json()['data']
        [changed] = diff['changed']
        assert changed['same_inputs'] is True
        assert [m['name'] for m in changed['moved']] == ['Start']


class TestCurvesTravel:
    def test_a_stored_curve_is_reused_by_its_owner_and_added_for_someone_else(self, client):
        tag = next(_IDS)
        stored = store_curve(client, f'pkgcurve{tag}', f'Package curve {tag}')
        assert stored.status_code == 201, stored.text
        curve_id = stored.json()['data']['id']
        project_id, _g, _m = _project(client, curve_id=curve_id)
        content = _package(client, project_id)
        assert f'curves/{curve_id}.json' in zipfile.ZipFile(io.BytesIO(content)).namelist()

        mine = {c['id']: c['action'] for c in _preview(client, content)['curves']}
        assert mine[curve_id] == 'reuse'

        other = _client_for(f'importer{tag}@radical.example.com')
        theirs = {c['id']: c['action'] for c in _preview(other, content)['curves']}
        assert theirs[curve_id] == 'add'
        response = _import(other, content)
        assert response.status_code == 200, response.text
        assert response.json()['data']['curves_added'] == 1
        new_id = response.json()['data']['project_id']
        model = next(a for a in other.get(f'/api/projects/{new_id}').json()['data']['analyses']
                     if a['name'] == 'Model')
        dates = other.get(f"/api/analysis/{model['id']}").json()['data']['measurements']
        local = dates[0]['curve_id']
        spec = other.get(f'/api/curves/{local}').json()['data']
        assert spec['content_sha256'] == client.get(f'/api/curves/{curve_id}').json()['data']['content_sha256']
        assert other.post(f"/api/analysis/{model['id']}/rerun?wait=true").status_code == 200
        # A second import finds the curve it stored the first time.
        assert {c['id']: c['action'] for c in _preview(other, content)['curves']}[curve_id] == 'reuse'

    def test_a_mixed_curve_brings_its_parents(self, client):
        tag = next(_IDS)
        parent = store_curve(client, f'pkgparent{tag}', f'Parent {tag}').json()['data']['id']
        mixed = client.post('/api/curves/mixed', json={
            'a': parent, 'b': 'intcal20', 'percent_a': 30, 'percent_a_error': 5,
            'name': f'Mix {tag}', 'curve_id': f'pkgmix{tag}'})
        assert mixed.status_code == 201, mixed.text
        mix_id = mixed.json()['data']['id']
        project_id, _g, _m = _project(client, curve_id=mix_id)
        content = _package(client, project_id)

        other = _client_for(f'mixer{tag}@radical.example.com')
        actions = {c['id']: c['action'] for c in _preview(other, content)['curves']}
        assert actions[parent] == 'add' and actions[mix_id] == 'add'
        response = _import(other, content)
        assert response.status_code == 200, response.text
        assert response.json()['data']['curves_added'] == 2

    def test_an_altered_curve_is_refused(self, client):
        tag = next(_IDS)
        curve_id = store_curve(client, f'pkgbad{tag}', f'Altered {tag}').json()['data']['id']
        project_id, _g, _m = _project(client, curve_id=curve_id)

        def alter(files):
            record = json.loads(files[f'curves/{curve_id}.json'])
            record['source_text'] = curve_text([(c, a + 3, e) for c, a, e in RISING])
            files[f'curves/{curve_id}.json'] = json.dumps(record).encode()

        content = _rewrite(_package(client, project_id), alter)
        other = _client_for(f'altered{tag}@radical.example.com')
        data = _preview(other, content)
        assert data['ok'] is False
        assert any('checksum' in e for e in data['errors'])
        response = _import(other, content)
        assert response.status_code == 422
        assert other.get('/api/projects').json()['data']['items'] == []


class TestRefusals:
    def test_not_a_zip(self, client):
        data = _preview(client, b'sample_name,age_bp\n', 'dates.csv')
        assert data['ok'] is False
        assert 'not a project package' in data['errors'][0]

    def test_a_zip_that_is_not_a_package(self, client):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as archive:
            archive.writestr('notes.txt', 'hello')
        data = _preview(client, out.getvalue())
        assert data['ok'] is False
        assert 'not a RadiCal project package' in data['errors'][0]

    def test_a_package_from_a_newer_version(self, client):
        project_id, _g, _m = _project(client)

        def bump(files):
            manifest = json.loads(files['manifest.json'])
            manifest['format_version'] = 99
            files['manifest.json'] = json.dumps(manifest).encode()

        data = _preview(client, _rewrite(_package(client, project_id), bump))
        assert data['ok'] is False
        assert 'newer version' in data['errors'][0]

    def test_a_bad_date_fails_the_whole_package(self, client):
        project_id, _g, model_id = _project(client)
        before = len(client.get('/api/projects').json()['data']['items'])

        def break_a_date(files):
            name = next(n for n in files if n.startswith('inputs/') and model_id[:8] in n)
            inputs = json.loads(files[name])
            inputs['measurements'][1]['error_1sigma'] = -5
            files[name] = json.dumps(inputs).encode()

        content = _rewrite(_package(client, project_id), break_a_date)
        data = _preview(client, content)
        assert data['ok'] is False
        assert any("Analysis 'Model'" in e for e in data['errors'])
        assert _import(client, content).status_code == 422
        assert len(client.get('/api/projects').json()['data']['items']) == before

    def test_a_first_version_package_still_imports(self, client):
        project_id, _g, _m = _project(client)

        def make_it_version_zero(files):
            manifest = json.loads(files['manifest.json'])
            for key in ('format', 'format_version', 'curves', 'coordinate_precision'):
                manifest.pop(key, None)
            files['manifest.json'] = json.dumps(manifest).encode()
            for name in [n for n in files if n.startswith('runs/') or n == 'samples.json']:
                del files[name]

        content = _rewrite(_package(client, project_id), make_it_version_zero)
        data = _preview(client, content)
        assert data['ok'] is True, data
        assert data['samples'] == 3
        assert not any(a['has_run'] for a in data['analyses'])
        assert _import(client, content).status_code == 200
