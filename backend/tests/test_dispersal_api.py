"""
POST /api/dispersal/propose-sequence -- the one spatial model with no solver
run behind it.

Every other figure in services/dispersal.py is a function of many modelled
ages at once and so exists only inside a run. A spatial ORDER is a question
about positions, so it can be asked of a DRAFT -- which is the whole point,
since the proposal is offered in the tree editor before there is anything to
run.
"""


def _boundary(bid):
    return {'id': bid, 'element_type': 'boundary', 'name': bid, 'children': []}


def _phase(pid, name):
    return {'id': pid, 'element_type': 'phase', 'name': name, 'children': []}


def _model(places):
    """Sequence[B0, P0, B1, P1, ...] with one placed date per phase."""
    children, measurements = [], []
    for index, (name, lat, lon) in enumerate(places):
        children.append(_boundary(f'b{index}'))
        children.append(_phase(f'p{index}', name))
        measurements.append({
            'id': f'd{index}', 'sample_name': name, 'element_id': f'p{index}',
            'latitude': lat, 'longitude': lon,
        })
    children.append(_boundary(f'b{len(places)}'))
    return {
        'model_tree': {'id': 'root', 'element_type': 'sequence', 'name': 'Region',
                       'children': children},
        'measurements': measurements,
    }


WEST_TO_EAST = [('Aarhus', 50.0, 0.0), ('Bruges', 50.0, 1.0),
                ('Cologne', 50.0, 2.0), ('Dresden', 50.0, 3.0)]


class TestProposingASequence:

    def test_it_returns_an_order_and_the_tree_behind_it(self, client):
        body = _model(WEST_TO_EAST)
        body['origin'] = {'lat': 50.0, 'lon': -2.0}

        answer = client.post('/api/dispersal/propose-sequence', json=body)
        assert answer.status_code == 200, answer.text
        data = answer.json()['data']

        assert [o['name'] for o in data['order']] == [
            'Aarhus', 'Bruges', 'Cologne', 'Dresden']
        # The tree it was read off, so the map can draw the derivation rather
        # than only the conclusion.
        assert len(data['edges']) == 3
        assert data['total_km'] > 0

    def test_it_needs_no_saved_analysis(self, client):
        """A draft is the point: there is nothing to run yet."""
        answer = client.post('/api/dispersal/propose-sequence', json=_model(WEST_TO_EAST))
        assert answer.status_code == 200
        assert answer.json()['data']['order']

    def test_the_origin_decides_which_end_it_starts_from(self, client):
        body = _model(WEST_TO_EAST)
        body['origin'] = {'lat': 50.0, 'lon': 9.0}
        data = client.post('/api/dispersal/propose-sequence', json=body).json()['data']
        assert [o['name'] for o in data['order']][0] == 'Dresden'

    def test_a_model_with_one_placed_site_is_refused_in_words(self, client):
        data = client.post('/api/dispersal/propose-sequence',
                           json=_model(WEST_TO_EAST[:1])).json()['data']
        assert data['order'] == []
        assert 'two placed sites' in data['refused']

    def test_a_phase_holding_two_places_is_refused_rather_than_averaged(self, client):
        body = _model(WEST_TO_EAST)
        # A second date on the first phase, forty kilometres away.
        body['measurements'].append({
            'id': 'stray', 'sample_name': 'Stray', 'element_id': 'p0',
            'latitude': 50.4, 'longitude': 0.0,
        })
        data = client.post('/api/dispersal/propose-sequence', json=body).json()['data']
        assert data['order'] == []
        assert 'km apart' in data['refused']

    def test_an_anonymous_request_is_refused(self, anonymous_client):
        assert anonymous_client.post(
            '/api/dispersal/propose-sequence',
            json=_model(WEST_TO_EAST)).status_code == 401

    def test_a_model_with_no_coordinates_says_so_rather_than_erroring(self, client):
        body = _model(WEST_TO_EAST)
        for measurement in body['measurements']:
            measurement['latitude'] = measurement['longitude'] = None
        data = client.post('/api/dispersal/propose-sequence', json=body).json()['data']
        assert data['order'] == []
        assert data['refused'] is not None
