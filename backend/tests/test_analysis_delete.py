"""
Deleting analyses: one at a time (DELETE /analysis/{id}) and the project
page's "delete selected" (DELETE /analysis with a body of ids).
"""


def _calibration(client, name, project_id=None):
    response = client.post('/api/analysis/calibrate', json={
        'sample_name': name, 'age_bp': 3000, 'error_1sigma': 30,
        'curve_id': 'intcal20', 'save': True, 'project_id': project_id,
    })
    assert response.status_code == 200, response.text
    return response.json()['data']['id']


def _project(client, name):
    return client.post('/api/projects', json={'name': name}).json()['data']['id']


def _project_analysis_ids(client, project_id):
    return {a['id'] for a in client.get(f'/api/projects/{project_id}').json()['data']['analyses']}


class TestBulkDelete:
    def test_the_selected_analyses_go_and_the_rest_stay(self, client):
        pid = _project(client, 'Bulk delete analyses')
        ids = [_calibration(client, f'BDA-{i}', pid) for i in range(3)]

        response = client.request('DELETE', '/api/analysis', json={'analysis_ids': ids[:2]})
        assert response.status_code == 200, response.text
        assert response.json()['data']['deleted_count'] == 2

        assert _project_analysis_ids(client, pid) == {ids[2]}
        for gone in ids[:2]:
            assert client.get(f'/api/analysis/{gone}').status_code == 404

    def test_one_bad_id_deletes_nothing(self, client):
        """All-or-nothing, as for the date collection's bulk delete."""
        pid = _project(client, 'Bulk delete analyses partial')
        ids = [_calibration(client, f'BDP-{i}', pid) for i in range(2)]

        response = client.request('DELETE', '/api/analysis',
                                  json={'analysis_ids': ids + ['ghost']})
        assert response.status_code == 404
        assert _project_analysis_ids(client, pid) == set(ids)

    def test_unassigned_analyses_can_be_deleted_too(self, client):
        # The Unassigned list has no project id, so the endpoint must not need one.
        aid = _calibration(client, 'BDU-1')
        response = client.request('DELETE', '/api/analysis', json={'analysis_ids': [aid]})
        assert response.status_code == 200, response.text
        assert client.get(f'/api/analysis/{aid}').status_code == 404

    def test_a_duplicate_id_is_rejected(self, client):
        aid = _calibration(client, 'BDD-1')
        response = client.request('DELETE', '/api/analysis', json={'analysis_ids': [aid, aid]})
        assert response.status_code == 422
        assert client.get(f'/api/analysis/{aid}').status_code == 200

    def test_an_empty_selection_is_rejected(self, client):
        assert client.request('DELETE', '/api/analysis',
                              json={'analysis_ids': []}).status_code == 422


class TestSingleDelete:
    def test_still_deletes_one(self, client):
        # The single-row path now shares its cascade with the bulk one.
        aid = _calibration(client, 'SD-1')
        assert client.delete(f'/api/analysis/{aid}').status_code == 200
        assert client.get(f'/api/analysis/{aid}').status_code == 404
