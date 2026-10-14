"""
Tests for GET /api/projects/{id}/samples/export: a project's date collection
as a CSV that the date importer reads straight back in.

What is pinned is the round trip. Every field a collected date is stored
with comes back through POST /api/import/dates unchanged, so exporting a
collection, editing it in a spreadsheet and importing it loses nothing.
"""
import csv
import io
import itertools

_IDS = itertools.count()

SOURCE = {
    'method': 'nearest_n',
    'snapshot_id': 'calib-marine20-2026-05-11',
    'centre': {'lat': -45.9, 'lon': 170.5},
    'n': 2,
    'map_nos': [995, 992],
    'nearest_km': 0.0,
}


def _project(client, precision=None):
    pid = client.post('/api/projects', json={'name': f'CSV site {next(_IDS)}'}).json()['data']['id']
    if precision:
        client.patch(f'/api/projects/{pid}', json={'coordinate_precision': precision})
    response = client.post(f'/api/projects/{pid}/samples', json={'samples': [
        {'sample_name': 'LB-10002', 'age_bp': 970, 'error_1sigma': 47,
         'material_type': 'marine shell', 'sample_type': 'marine', 'curve_id': 'marine20',
         'default_context_name': 'Midden 1', 'tpq_age_bp': 1200, 'taq_age_bp': 300,
         'latitude': -45.87421, 'longitude': 170.50361, 'location': 'Hillside',
         'notes': 'shell, with "quotes", and commas', 'depth_cm': 42.5,
         'ring_offset': 10, 'ring_offset_error': 2,
         'inbuilt_years': 50, 'inbuilt_shape': 'exponential'},
        {'sample_name': 'LA10001', 'age_bp': 614, 'error_1sigma': 25,
         'material_type': 'charcoal', 'curve_id': 'shcal20'},
    ]})
    assert response.status_code == 200, response.text
    items = response.json()['data']['items']
    lookup = client.patch(f'/api/projects/{pid}/samples', json={
        'sample_ids': [items[0]['id']], 'delta_r': -7, 'delta_r_error': 45,
        'delta_r_source': SOURCE})
    assert lookup.status_code == 200, lookup.text
    return pid, items


def _export(client, pid, query=''):
    response = client.get(f'/api/projects/{pid}/samples/export{query}')
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('text/csv')
    return response.content


def _reimport(client, content):
    response = client.post('/api/import/dates',
                           files={'file': ('dates.csv', io.BytesIO(content), 'text/csv')})
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['ok'] is True, data
    return data['dates']


class TestCollectionCsv:
    def test_every_field_survives_a_round_trip(self, client):
        pid, _items = _project(client)
        dates = _reimport(client, _export(client, pid))
        assert [d['sample_name'] for d in dates] == ['LB-10002', 'LA10001']
        expected = {
            'age_bp': 970, 'error_1sigma': 47, 'material_type': 'marine shell',
            'sample_type': 'marine', 'curve_id': 'marine20', 'phase_name': 'Midden 1',
            'delta_r': -7, 'delta_r_error': 45, 'delta_r_source': SOURCE,
            'tpq_age_bp': 1200, 'taq_age_bp': 300,
            'latitude': -45.87421, 'longitude': 170.50361, 'location': 'Hillside',
            'notes': 'shell, with "quotes", and commas', 'depth_cm': 42.5,
            'ring_offset': 10, 'ring_offset_error': 2,
            'inbuilt_years': 50, 'inbuilt_shape': 'exponential',
        }
        assert {k: dates[0][k] for k in expected} == expected

    def test_reimported_into_a_collection_it_is_the_same_collection(self, client):
        pid, _items = _project(client)
        dates = _reimport(client, _export(client, pid))
        other = client.post('/api/projects', json={'name': f'Copy {next(_IDS)}'}).json()['data']['id']
        response = client.post(f'/api/projects/{other}/samples', json={'samples': [
            {**{k: v for k, v in d.items() if k not in ('id', 'phase_name', 'outlier_model',
                                                       'outlier_prior', 'element_id')},
             'default_context_name': d['phase_name']}
            for d in dates]})
        assert response.status_code == 200, response.text
        copied = response.json()['data']['items']
        stored = client.get(f'/api/projects/{pid}/samples').json()['data']['items']
        skip = {'id', 'project_id', 'created_at', 'updated_at'}
        assert ([{k: v for k, v in s.items() if k not in skip} for s in copied]
                == [{k: v for k, v in s.items() if k not in skip} for s in stored])

    def test_a_selection_exports_only_its_dates(self, client):
        pid, items = _project(client)
        rows = list(csv.DictReader(io.StringIO(
            _export(client, pid, f"?sample={items[1]['id']}").decode())))
        assert [r['sample_name'] for r in rows] == ['LA10001']

    def test_a_date_from_another_collection_is_refused(self, client):
        pid, _items = _project(client)
        _other, other_items = _project(client)
        response = client.get(f"/api/projects/{pid}/samples/export?sample={other_items[0]['id']}")
        assert response.status_code == 404

    def test_positions_leave_at_the_projects_precision(self, client):
        pid, _items = _project(client, precision='0.1')
        text = _export(client, pid).decode()
        assert '45.87421' not in text and '-45.9' in text

    def test_an_unreadable_provenance_cell_fails_the_row(self, client):
        content = ('sample_name,age_bp,error_1sigma,curve_id,delta_r,delta_r_error,delta_r_source\n'
                   'LB-1,970,47,marine20,-7,45,not json\n').encode()
        response = client.post('/api/import/dates',
                               files={'file': ('dates.csv', io.BytesIO(content), 'text/csv')})
        data = response.json()['data']
        assert data['ok'] is False
        assert any('delta_r_source' in e for e in data['errors'])

    def test_a_provenance_without_its_offset_is_not_stored(self, client):
        pid = client.post('/api/projects', json={'name': f'Bare {next(_IDS)}'}).json()['data']['id']
        response = client.post(f'/api/projects/{pid}/samples', json={'samples': [
            {'sample_name': 'X', 'age_bp': 900, 'error_1sigma': 30, 'curve_id': 'marine20',
             'delta_r_source': SOURCE}]})
        assert response.status_code == 200, response.text
        assert response.json()['data']['items'][0]['delta_r_source'] is None
