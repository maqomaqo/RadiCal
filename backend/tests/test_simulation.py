"""
Scenario simulation: services/simulation.py (truth windows and the draws) and
api/simulation.py (replicate analyses, the job, the truth key).

The spec's acceptance, pinned here: r0 dates read the curve at each window's
centre; the same seed regenerates identical replicates; laboratory scatter has
the stated SD to within 3%.
"""
import csv
import io
import itertools
import time

import numpy as np
import pytest

from app.services import simulation
from app.services.calibration import get_curve

_IDS = itertools.count(1)


def _date(i, element='p', error=20.0, **extra):
    return {'id': f's{i}', 'sample_name': f'LC-{i}', 'age_bp': 600, 'error_1sigma': error,
            'curve_id': 'shcal20', 'element_id': element, **extra}


def _window(element='p', old=700.0, young=600.0, kind='phase'):
    return {'element_id': element, 'name': element, 'kind': kind,
            'old_bp': old, 'young_bp': young}


def _curve(cal_bp, curve='shcal20'):
    return float(np.asarray(get_curve(curve).interpolate(np.array([cal_bp]))[0]).ravel()[0])


class TestTheDraws:
    def test_r0_reads_the_curve_at_each_window_centre(self):
        dates = [_date(1, 'a'), _date(2, 'b')]
        windows = [_window('a', 720, 680), _window('b', 600, 560)]
        rows = simulation.simulate(dates, windows, seed=7, replicate=0)
        assert [r['event_bp'] for r in rows] == [700.0, 580.0]
        assert [r['age_bp'] for r in rows] == [round(_curve(700)), round(_curve(580))]
        assert all(r['z'] == 0 for r in rows)

    def test_the_same_seed_regenerates_identical_replicates(self):
        dates = [_date(i) for i in range(10)]
        first = simulation.simulate(dates, [_window()], seed=42, replicate=3)
        again = simulation.simulate(dates, [_window()], seed=42, replicate=3)
        other = simulation.simulate(dates, [_window()], seed=43, replicate=3)
        assert first == again
        assert [r['age_bp'] for r in first] != [r['age_bp'] for r in other]

    def test_laboratory_scatter_has_the_stated_sd(self):
        dates = [_date(i, error=25.0) for i in range(6000)]
        rows = simulation.simulate(dates, [_window(old=650, young=650)], seed=1, replicate=1)
        residuals = np.array([r['age_bp'] - r['curve_bp'] for r in rows])
        assert np.std(residuals) == pytest.approx(25.0, rel=0.03)
        assert abs(np.mean(residuals)) < 1.0

    def test_events_fall_uniformly_inside_their_window(self):
        dates = [_date(i) for i in range(4000)]
        rows = simulation.simulate(dates, [_window(old=700, young=600)], seed=2, replicate=1)
        events = np.array([r['event_bp'] for r in rows])
        assert events.min() >= 600 and events.max() <= 700
        assert np.mean(events) == pytest.approx(650, abs=2)

    def test_inbuilt_age_makes_the_sample_older(self):
        uniform = [_date(i, inbuilt_years=30, inbuilt_shape='uniform') for i in range(3000)]
        rows = simulation.simulate(uniform, [_window()], seed=3, replicate=1)
        offsets = np.array([r['inbuilt_years'] for r in rows])
        assert offsets.min() >= 0 and offsets.max() <= 30
        assert np.mean(offsets) == pytest.approx(15, abs=0.7)
        assert all(r['sample_bp'] == pytest.approx(r['event_bp'] + r['inbuilt_years'], abs=0.02)
                   for r in rows)
        expo = [_date(i, inbuilt_years=40, inbuilt_shape='exponential') for i in range(3000)]
        offsets = np.array([r['inbuilt_years'] for r in
                            simulation.simulate(expo, [_window()], seed=3, replicate=1)])
        assert np.mean(offsets) == pytest.approx(40, rel=0.06)

    def test_r0_takes_the_expected_inbuilt_age(self):
        dates = [_date(1, inbuilt_years=30), _date(2, inbuilt_years=40, inbuilt_shape='exponential')]
        rows = simulation.simulate(dates, [_window()], seed=1, replicate=0)
        assert [r['inbuilt_years'] for r in rows] == [15.0, 40.0]

    def test_switching_off_scatter_or_inbuilt(self):
        dates = [_date(i, inbuilt_years=30) for i in range(20)]
        rows = simulation.simulate(dates, [_window()], seed=5, replicate=2,
                                   scatter=False, inbuilt=False)
        assert all(r['z'] == 0 and r['inbuilt_years'] == 0 for r in rows)
        assert all(r['age_bp'] == round(r['curve_bp']) for r in rows)

    def test_a_different_truth_keeps_the_same_draws(self):
        """Two truths with one seed differ in the truth alone: the scatter and
        the place in the window are the same draws."""
        dates = [_date(i) for i in range(10)]
        a = simulation.simulate(dates, [_window(old=700, young=600)], seed=9, replicate=1)
        b = simulation.simulate(dates, [_window(old=500, young=400)], seed=9, replicate=1)
        assert [r['z'] for r in a] == [r['z'] for r in b]
        assert [round(r['event_bp'] - 200, 6) for r in a] == [round(r['event_bp'], 6) for r in b]

    def test_the_reservoir_offset_is_the_truth(self):
        rows = simulation.simulate([_date(1, curve_id='marine20', delta_r=-50)],
                                   [_window(old=650, young=650)], seed=1, replicate=0)
        assert rows[0]['age_bp'] == round(_curve(650, 'marine20') - 50)

    def test_a_date_with_no_window_is_refused(self):
        with pytest.raises(ValueError, match='no truth window'):
            simulation.simulate([_date(1, 'elsewhere')], [_window('p')], seed=1, replicate=0)


def _tree():
    def el(eid, kind, name, posterior=None):
        return {'id': eid, 'element_type': kind, 'name': name, 'children': [],
                'posterior': posterior}
    median = lambda bp: {'posterior_median': bp}  # noqa: E731
    return {'id': 'root', 'element_type': 'sequence', 'name': 'Site', 'children': [
        el('b0', 'boundary', 'Start', median(700)), el('p0', 'phase', 'Lower'),
        el('b1', 'boundary', 'Sand starts', median(660)), el('sand', 'phase', 'Sand'),
        el('b2', 'boundary', 'Sand ends', median(640)), el('p1', 'phase', 'Upper'),
        el('b3', 'boundary', 'End', median(600)),
    ]}


class TestTheTruthWindows:
    def test_each_phase_takes_its_boundaries_medians(self):
        dates = [_date(1, 'p0'), _date(2, 'p1')]
        windows = simulation.default_windows(_tree(), dates, {'s1': 680, 's2': 620})
        assert [(w['element_id'], w['kind'], w['old_bp'], w['young_bp']) for w in windows] == [
            ('p0', 'phase', 700, 660), ('sand', 'empty', 660, 640), ('p1', 'phase', 640, 600)]

    def test_closing_the_gaps_removes_the_sand_and_moves_the_upper_phase_older(self):
        dates = [_date(1, 'p0'), _date(2, 'p1')]
        windows = simulation.default_windows(_tree(), dates, {'s1': 680, 's2': 620})
        closed = {w['element_id']: (w['old_bp'], w['young_bp'])
                  for w in simulation.close_gaps(_tree(), windows)}
        assert closed == {'p0': (700, 660), 'sand': (660, 660), 'p1': (660, 620)}

    def test_a_group_calibration_has_one_window_over_its_dates(self):
        dates = [_date(1, None), _date(2, None)]
        windows = simulation.default_windows(None, dates, {'s1': 690, 's2': 610})
        assert windows == [{'element_id': None, 'name': 'All dates', 'kind': 'phase',
                            'old_bp': 690, 'young_bp': 610}]


# --------------------------------------------------------------------------
# Through the API
# --------------------------------------------------------------------------

def _await_job(client, job_id, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f'/api/jobs/{job_id}').json()['data']
        if body['state'] in ('completed', 'failed', 'cancelled'):
            return body
        time.sleep(0.2)
    pytest.fail(f'job {job_id} never finished')


def _saved_model(client, tag):
    tree = {'id': f'{tag}root', 'element_type': 'sequence', 'name': 'Site', 'children': [
        {'id': f'{tag}b0', 'element_type': 'boundary', 'name': 'Start', 'children': []},
        {'id': f'{tag}p0', 'element_type': 'phase', 'name': 'Lower', 'children': []},
        {'id': f'{tag}b1', 'element_type': 'boundary', 'name': 'Mid', 'children': []},
        {'id': f'{tag}p1', 'element_type': 'phase', 'name': 'Upper', 'children': []},
        {'id': f'{tag}b2', 'element_type': 'boundary', 'name': 'End', 'children': []}]}
    dates = [{'id': f'{tag}m{i}', 'sample_name': f'LC-{i}', 'age_bp': age, 'error_1sigma': 20,
              'material_type': 'charcoal', 'sample_type': 'terrestrial', 'curve_id': 'shcal20',
              'element_id': f'{tag}{phase}', **({'inbuilt_years': 30} if i == 0 else {})}
             for i, (age, phase) in enumerate(((700, 'p0'), (690, 'p0'), (600, 'p1'), (590, 'p1')))]
    project = client.post('/api/projects', json={'name': f'Simulation {tag}'}).json()['data']['id']
    response = client.post('/api/analysis/bayesian?wait=true', json={
        'name': f'Template {tag}', 'measurements': dates, 'model_tree': tree,
        'project_id': project, 'save': True})
    assert response.status_code == 200, response.text
    return response.json()['data']['id'], project


class TestTheReplicates:
    @pytest.fixture(scope='class')
    def made(self, suite_user):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app, headers={'Authorization': f"Bearer {suite_user['token']}"})
        tag = f'sim{next(_IDS)}x'
        analysis_id, project_id = _saved_model(client, tag)
        defaults = client.get(f'/api/analysis/{analysis_id}/simulate/defaults')
        assert defaults.status_code == 200, defaults.text
        windows = defaults.json()['data']['windows']
        response = client.post(f'/api/analysis/{analysis_id}/simulate', json={
            'name': 'Null', 'windows': windows, 'replicates': 2, 'seed': 1234})
        assert response.status_code == 202, response.text
        data = response.json()['data']
        assert _await_job(client, data['job_id'])['state'] == 'completed'
        return client, analysis_id, project_id, windows, data

    def test_the_defaults_cover_both_phases(self, made):
        _client, _id, _project, windows, _data = made
        assert [w['name'] for w in windows] == ['Lower', 'Upper']
        assert all(w['old_bp'] >= w['young_bp'] for w in windows)

    def test_each_replicate_is_a_run_analysis_in_the_project(self, made):
        client, _id, project_id, _windows, data = made
        assert len(data['analysis_ids']) == 3
        for k, analysis_id in enumerate(data['analysis_ids']):
            analysis = client.get(f'/api/analysis/{analysis_id}').json()['data']
            assert analysis['status'] == 'completed'
            assert analysis['name'] == f'Null r{k}'
            assert analysis['project_id'] == project_id

    def test_the_replicates_read_what_the_truth_key_says(self, made):
        client, _id, _project, _windows, data = made
        truth = list(csv.DictReader(io.StringIO(
            client.get(f"/api/simulations/{data['id']}/truth.csv").text)))
        assert len(truth) == 3 * 4
        r0 = client.get(f"/api/analysis/{data['analysis_ids'][0]}").json()['data']
        ages = {m['sample_name']: m['age_bp'] for m in r0['measurements']}
        for row in (r for r in truth if r['replicate'] == '0'):
            assert ages[row['sample_name']] == int(row['age_bp'])
        # r0's charcoal date carries its expected inbuilt age, half of 30.
        charcoal = next(r for r in truth if r['replicate'] == '0' and r['sample_name'] == 'LC-0')
        assert float(charcoal['inbuilt_years']) == 15.0

    def test_the_simulation_is_listed_on_its_template(self, made):
        client, analysis_id, _project, _windows, data = made
        listed = client.get(f'/api/analysis/{analysis_id}/simulations').json()['data']
        record = next(s for s in listed if s['id'] == data['id'])
        assert (record['seed'], record['replicates']) == (1234, 2)
        assert [a['replicate'] for a in record['analyses']] == [0, 1, 2]

    def test_the_seed_regenerates_the_same_dates(self, made):
        client, analysis_id, _project, windows, data = made
        again = client.post(f'/api/analysis/{analysis_id}/simulate', json={
            'name': 'Null again', 'windows': windows, 'replicates': 2, 'seed': 1234}).json()['data']
        _await_job(client, again['job_id'])
        first = client.get(f"/api/simulations/{data['id']}/truth.csv").text.splitlines()
        second = client.get(f"/api/simulations/{again['id']}/truth.csv").text.splitlines()
        strip = lambda lines: [l.rsplit(',', 2)[0] for l in lines]  # noqa: E731
        assert strip(first) == strip(second)        # all but the copied ids

    def test_someone_else_cannot_see_it(self, made, anonymous_client):
        from fastapi.testclient import TestClient
        from app.main import app
        _client, analysis_id, _project, _windows, data = made
        token = anonymous_client.post('/api/auth/register', json={
            'email': 'simulation-stranger@radical.example.com',
            'password': 'a good enough password'}).json()['data']['token']
        stranger = TestClient(app, headers={'Authorization': f'Bearer {token}'})
        assert stranger.get(f"/api/simulations/{data['id']}").status_code == 404
        assert stranger.get(f"/api/simulations/{data['id']}/truth.csv").status_code == 404
        assert stranger.get(f'/api/analysis/{analysis_id}/simulate/defaults').status_code == 404


class TestWhatIsRefused:
    def test_a_window_that_ends_before_it_starts(self, client):
        analysis_id, _project = _saved_model(client, f'sim{next(_IDS)}y')
        windows = client.get(f'/api/analysis/{analysis_id}/simulate/defaults').json()['data']['windows']
        windows[0]['old_bp'], windows[0]['young_bp'] = 500, 700
        response = client.post(f'/api/analysis/{analysis_id}/simulate',
                               json={'windows': windows, 'replicates': 0})
        assert response.status_code == 422

    def test_too_many_replicates(self, client):
        analysis_id, _project = _saved_model(client, f'sim{next(_IDS)}z')
        windows = client.get(f'/api/analysis/{analysis_id}/simulate/defaults').json()['data']['windows']
        response = client.post(f'/api/analysis/{analysis_id}/simulate',
                               json={'windows': windows, 'replicates': 500})
        assert response.status_code == 422

    def test_a_single_calibration(self, client):
        made = client.post('/api/analysis/calibrate', json={
            'sample_name': 'LC-solo', 'age_bp': 650, 'error_1sigma': 20,
            'curve_id': 'shcal20', 'save': True}).json()['data']['id']
        assert client.get(f'/api/analysis/{made}/simulate/defaults').status_code == 400
