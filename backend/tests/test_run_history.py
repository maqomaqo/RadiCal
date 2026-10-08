"""
Tests for run history (api/run_history.py, feature spec W6): the last
RUNS_KEPT completed runs of each analysis, one run read back in GET
/analysis/{id}'s shape for the comparison page, and the project diff after a
re-run.
"""
import itertools

import pytest

from app.api import run_history
from app.database import SessionLocal
from app.models import AnalysisRun, RadiocarbonSample

_IDS = itertools.count()


def _date(name, age, **extra):
    return {'sample_name': name, 'age_bp': age, 'error_1sigma': 20,
            'material_type': 'charcoal', 'sample_type': 'terrestrial',
            'curve_id': 'shcal20', **extra}


def _project(client):
    return client.post('/api/projects', json={'name': f'History {next(_IDS)}'}).json()['data']['id']


def _model(client, project_id=None, ages=(614, 627, 645)):
    """A three-date phase between two Boundaries: solved exactly, so a re-run
    of unchanged inputs reproduces it."""
    tag = f'rh{next(_IDS)}'
    tree = {'id': f'{tag}_root', 'element_type': 'sequence', 'name': 'Site', 'children': [
        {'id': f'{tag}_b0', 'element_type': 'boundary', 'name': 'Start', 'children': []},
        {'id': f'{tag}_p', 'element_type': 'phase', 'name': 'Layer', 'children': []},
        {'id': f'{tag}_b1', 'element_type': 'boundary', 'name': 'End', 'children': []},
    ]}
    created = client.post('/api/analysis/bayesian?wait=true', json={
        'name': f'Model {tag}', 'project_id': project_id,
        'measurements': [{**_date(f'D{i}', a), 'id': f'{tag}_d{i}', 'element_id': f'{tag}_p'}
                         for i, a in enumerate(ages)],
        'model_tree': tree, 'save': True,
    })
    assert created.status_code == 200, created.text
    return created.json()['data']['id']


def _rerun(client, analysis_id):
    response = client.post(f'/api/analysis/{analysis_id}/rerun?wait=true')
    assert response.status_code == 200, response.text


def _runs(client, analysis_id):
    response = client.get(f'/api/analysis/{analysis_id}/runs')
    assert response.status_code == 200, response.text
    return response.json()['data']['runs']


class TestRecording:
    def test_every_kind_of_run_is_recorded(self, client):
        calibration = client.post('/api/analysis/calibrate', json={
            **_date('Solo', 614), 'save': True}).json()['data']['id']
        group = client.post('/api/analysis/calibrate-group', json={
            'name': 'Group', 'measurements': [{**_date(f'G{i}', a), 'id': f'rhg{next(_IDS)}'}
                                              for i, a in enumerate((614, 627))]}).json()['data']['id']
        model = _model(client)
        for analysis_id in (calibration, group, model):
            [run] = _runs(client, analysis_id)
            assert run['current'] is True
        _rerun(client, model)
        assert len(_runs(client, model)) == 2

    def test_only_the_last_three_are_kept_newest_first(self, client):
        model = _model(client)
        for _ in range(4):
            _rerun(client, model)
        runs = _runs(client, model)
        assert len(runs) == run_history.RUNS_KEPT == 3
        assert [r['current'] for r in runs] == [True, False, False]
        stamps = [r['created_at'] for r in runs]
        assert stamps == sorted(stamps, reverse=True)
        with SessionLocal() as db:
            assert db.query(AnalysisRun).filter(AnalysisRun.analysis_id == model).count() == 3

    def test_a_run_reads_back_in_the_analysis_shape(self, client):
        model = _model(client)
        _rerun(client, model)
        older = _runs(client, model)[1]
        response = client.get(f"/api/analysis/{model}/runs/{older['id']}")
        assert response.status_code == 200, response.text
        payload = response.json()['data']
        assert payload['id'] == f"{model}@{older['id']}"
        assert payload['run']['id'] == older['id']
        assert payload['run']['created_at'] == older['created_at']
        current = client.get(f'/api/analysis/{model}').json()['data']
        assert {r['sample_name'] for r in payload['results']} == {
            r['sample_name'] for r in current['results']}
        assert payload['model_tree']['children'][0]['posterior']['posterior_median'] == pytest.approx(
            current['model_tree']['children'][0]['posterior']['posterior_median'])

    def test_a_failure_to_record_never_fails_the_run(self, client, monkeypatch):
        model = _model(client)
        before = len(_runs(client, model))

        def broken(*_args, **_kwargs):
            raise RuntimeError('history unavailable')

        monkeypatch.setattr('app.api.analysis.get_analysis', broken)
        _rerun(client, model)
        monkeypatch.undo()
        assert client.get(f'/api/analysis/{model}').json()['data']['status'] == 'completed'
        assert len(_runs(client, model)) == before

    def test_runs_go_with_their_analysis(self, client):
        model = _model(client)
        assert client.delete(f'/api/analysis/{model}').status_code == 200
        with SessionLocal() as db:
            assert db.query(AnalysisRun).filter(AnalysisRun.analysis_id == model).count() == 0

    def test_someone_elses_runs_are_not_found(self, client, anonymous_client):
        model = _model(client)
        assert anonymous_client.get(f'/api/analysis/{model}/runs').status_code == 401
        assert client.get(f'/api/analysis/{model}/runs/no-such-run').status_code == 404


class TestProjectDiff:
    def test_an_unchanged_rerun_moves_nothing(self, client):
        project_id = _project(client)
        _model(client, project_id)
        client.post(f'/api/projects/{project_id}/rerun?wait=true')
        data = client.get(f'/api/projects/{project_id}/run-diff').json()['data']
        assert data['compared'] == 1
        assert data['changed'] == []

    def test_a_changed_date_is_listed_with_what_moved(self, client):
        project_id = _project(client)
        model = _model(client, project_id)
        with SessionLocal() as db:
            sample = db.query(RadiocarbonSample).filter(
                RadiocarbonSample.analysis_id == model).first()
            sample.age_bp += 300
            db.commit()
        client.post(f'/api/projects/{project_id}/rerun?wait=true')
        data = client.get(f'/api/projects/{project_id}/run-diff').json()['data']
        [entry] = data['changed']
        assert entry['analysis_id'] == model
        assert entry['same_inputs'] is False
        start = next(m for m in entry['moved'] if m['name'] == 'Start')
        assert abs(start['shift']) >= 10
        assert start['units'] == 'cal BP'


class TestDiffRuns:
    def _payload(self, median, ranges, converged=True):
        return {
            'solver': {'converged': converged},
            'model_tree': {'element_type': 'sequence', 'name': 'Site', 'children': [
                {'element_type': 'boundary', 'name': 'Start', 'children': [],
                 'posterior': {'posterior_median': median,
                               'hpd_95_ranges': [{}] * ranges}}]},
            'intervals': {'durations': [
                {'kind': 'phase_duration', 'name': 'Layer',
                 'years': {'median': 40.0, 'hpd_95_ranges': [{}]}}]},
        }

    def test_a_shift_under_the_threshold_is_not_listed(self):
        diff = run_history.diff_runs(self._payload(600, 1), self._payload(605, 1))
        assert diff['moved'] == []

    def test_a_lost_mode_is_listed_however_small_the_shift(self):
        """REF-A-NOE lost its short mode while its median moved under 10 yr."""
        diff = run_history.diff_runs(self._payload(600, 1), self._payload(603, 2))
        [moved] = diff['moved']
        assert (moved['modes_before'], moved['modes_after']) == (2, 1)

    def test_a_change_in_convergence_is_reported(self):
        diff = run_history.diff_runs(self._payload(600, 1, converged=True),
                                     self._payload(600, 1, converged=False))
        assert diff['convergence_changed'] is True
