"""
Run control for the sampler, through the API: the seed a run took and the
minimum length it was held to, recorded in the solver record, and a
duplicate with a fresh seed for an independent replicate (feature spec S1).

The sampler's own behaviour -- that the floor holds and the seed reproduces --
is tested in test_gibbs.py's TestRunLength.
"""
import itertools

_IDS = itertools.count()


def _sampled(client, **config):
    """A three-date phase, sampled on one chain: the cheapest run that takes a
    seed at all (the exact solver draws nothing)."""
    tag = f'rc{next(_IDS)}'
    tree = {'id': f'{tag}_root', 'element_type': 'sequence', 'name': 'Site', 'children': [
        {'id': f'{tag}_b0', 'element_type': 'boundary', 'name': 'Start', 'children': []},
        {'id': f'{tag}_p', 'element_type': 'phase', 'name': 'Phase', 'children': []},
        {'id': f'{tag}_b1', 'element_type': 'boundary', 'name': 'End', 'children': []},
    ]}
    created = client.post('/api/analysis/bayesian?wait=true', json={
        'name': f'Run control {tag}',
        'measurements': [
            {'id': f'{tag}_d{i}', 'sample_name': f'D{i}', 'age_bp': age, 'error_1sigma': 25,
             'curve_id': 'intcal20', 'element_id': f'{tag}_p'}
            for i, age in enumerate((3000, 3020, 3040))],
        'model_tree': tree,
        'bayesian_config': {'solver': 'gibbs', 'mcmc_chains': 1, **config},
        'save': True,
    })
    assert created.status_code == 200, created.text
    return created.json()['data']['id']


def _solver(client, analysis_id):
    return client.get(f'/api/analysis/{analysis_id}').json()['data']['solver']


class TestSolverRecord:
    def test_a_run_records_the_default_seed_and_no_floor(self, client):
        solver = _solver(client, _sampled(client))
        assert solver['method'] == 'gibbs'
        assert solver['seed'] == 42
        assert solver['min_sweeps'] == 0

    def test_a_chosen_seed_and_floor_are_used_and_recorded(self, client):
        solver = _solver(client, _sampled(client, seed=7, mcmc_min_sweeps=30000))
        assert solver['seed'] == 7
        assert solver['min_sweeps'] == 30000
        assert solver['sweeps'] >= 30000

    def test_the_record_reaches_the_json_export(self, client):
        analysis_id = _sampled(client, seed=9)
        exported = client.get(f'/api/results/{analysis_id}/export/json').json()['data']
        assert exported['solver']['seed'] == 9

    def test_a_seed_off_the_range_is_refused(self, client):
        response = client.post('/api/analysis/bayesian', json={
            'name': 'Bad seed', 'measurements': [], 'bayesian_config': {'seed': -1}})
        assert response.status_code == 422


class TestDuplicateWithNewSeed:
    def test_the_copy_takes_a_fresh_seed_and_says_so(self, client):
        source = _sampled(client, seed=5, mcmc_min_sweeps=1000)
        response = client.post(f'/api/analysis/{source}/duplicate?new_seed=true')
        assert response.status_code == 200, response.text
        copy = client.get(f"/api/analysis/{response.json()['data']['id']}").json()['data']
        seed = copy['bayesian_config']['seed']
        assert seed != 5
        assert f'(seed {seed})' in copy['name']
        # Everything else about the run is the source's.
        assert copy['bayesian_config']['mcmc_min_sweeps'] == 1000
        assert copy['bayesian_config']['solver'] == 'gibbs'

    def test_a_plain_duplicate_keeps_the_seed(self, client):
        source = _sampled(client, seed=5)
        response = client.post(f'/api/analysis/{source}/duplicate')
        copy = client.get(f"/api/analysis/{response.json()['data']['id']}").json()['data']
        assert copy['bayesian_config']['seed'] == 5

    def test_only_a_bayesian_analysis_has_a_seed(self, client):
        created = client.post('/api/analysis/calibrate', json={
            'sample_name': 'Solo', 'age_bp': 3000, 'error_1sigma': 30,
            'curve_id': 'intcal20', 'save': True})
        analysis_id = created.json()['data']['id']
        response = client.post(f'/api/analysis/{analysis_id}/duplicate?new_seed=true')
        assert response.status_code == 400
