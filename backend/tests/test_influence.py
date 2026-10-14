"""
Influence analysis: services/influence.py (the variants and their summaries)
and api/influence.py (the job, the report and opening a variant).

What has to hold: each variant changes exactly one thing about one date; a
date moves only to a Phase that is really its neighbour; the report's
baseline is the model as it stands; and a variant opened as an analysis is
that variant, run.
"""
import itertools
import time

import numpy as np
import pytest

from app.services import influence, statistics

_IDS = itertools.count(1)


def _tree(tag):
    """Start, Lower, Mid, Middle, Upper2, Upper, End: three Phases in a Sequence,
    with a Boundary between the first two and none between the last two."""
    def el(suffix, kind, name):
        return {'id': f'{tag}{suffix}', 'element_type': kind, 'name': name, 'children': []}
    return {'id': f'{tag}root', 'element_type': 'sequence', 'name': 'Site', 'children': [
        el('b0', 'boundary', 'Start'), el('p0', 'phase', 'Lower'),
        el('b1', 'boundary', 'Mid'), el('p1', 'phase', 'Middle'),
        el('b2', 'boundary', 'Upper start'), el('p2', 'phase', 'Upper'),
        el('b3', 'boundary', 'End'),
    ]}


def _dates(tag, ages=((3300, 'p0'), (3270, 'p0'), (3150, 'p1'), (3120, 'p1'),
                      (3050, 'p2'), (3020, 'p2'))):
    return [{'id': f'{tag}m{i}', 'sample_name': f'LB-{i + 1}', 'age_bp': age,
             'error_1sigma': 30, 'curve_id': 'intcal20', 'element_id': f'{tag}{phase}'}
            for i, (age, phase) in enumerate(ages)]


class TestPlanning:
    def test_each_kind_gives_its_variants(self):
        tree, dates = _tree('a'), _dates('a')
        variants = influence.plan(dates, tree, ['leave_out', 'move', 'outlier'])
        kinds = [v['kind'] for v in variants]
        assert kinds.count('leave_out') == 6
        assert kinds.count('outlier') == 6
        # Two dates per Phase; the end Phases have one neighbour, the middle two.
        assert kinds.count('move') == 2 * 1 + 2 * 2 + 2 * 1

    def test_a_date_moves_to_the_phase_either_side_across_its_boundary(self):
        tree, dates = _tree('b'), _dates('b')
        moves = [v for v in influence.plan(dates, tree, ['move']) if v['date_id'] == 'bm2']
        assert {(v['direction'], v['to_phase_name']) for v in moves} == {
            ('older', 'Lower'), ('younger', 'Upper')}

    def test_the_oldest_phase_has_no_older_neighbour(self):
        tree, dates = _tree('c'), _dates('c')
        moves = [v for v in influence.plan(dates, tree, ['move']) if v['date_id'] == 'cm0']
        assert [(v['direction'], v['to_phase_name']) for v in moves] == [('younger', 'Middle')]

    def test_a_phase_in_a_phase_has_no_neighbours(self):
        """Phases grouped in a Phase are unordered: nothing is older or younger."""
        tree = {'id': 'groot', 'element_type': 'phase', 'name': 'Site', 'children': [
            {'id': 'gp0', 'element_type': 'phase', 'name': 'A', 'children': []},
            {'id': 'gp1', 'element_type': 'phase', 'name': 'B', 'children': []}]}
        dates = _dates('g', ((3000, 'p0'), (2950, 'p1')))
        assert influence.plan(dates, tree, ['move']) == []

    def test_a_date_already_an_outlier_gets_no_outlier_variant(self):
        tree, dates = _tree('d'), _dates('d')
        dates[0]['outlier_prior'] = 1.0
        assert 'dm0' not in {v['date_id'] for v in influence.plan(dates, tree, ['outlier'])}

    def test_applying_a_variant_changes_one_date_only(self):
        tree, dates = _tree('e'), _dates('e')
        by_id = {v['id']: v for v in influence.plan(dates, tree, list(influence.KINDS))}
        left = influence.apply(by_id['leave_out:em3'], dates)
        assert [m['id'] for m in left] == ['em0', 'em1', 'em2', 'em4', 'em5']
        moved = influence.apply(by_id['move_younger:em3'], dates)
        assert moved[3]['element_id'] == 'ep2' and moved[2] == dates[2]
        flagged = influence.apply(by_id['outlier:em3'], dates)
        assert (flagged[3]['outlier_prior'], flagged[3]['outlier_model']) == (1.0, 'general')
        assert dates[3].get('outlier_prior') is None        # the input is left alone


class TestSummaries:
    def test_a_compact_distribution_reads_the_same_probability(self):
        xs = np.arange(3000.0, 3600.0, 1.0)
        ys = np.exp(-0.5 * ((xs - 3300) / 40) ** 2)
        full = [[x, y] for x, y in zip(xs, ys)]
        compact = influence.compact_distribution(full)
        assert len(compact) == influence.DISTRIBUTION_POINTS
        for year in (3250, 3300, 3360):
            assert statistics.p_before(compact, year) == pytest.approx(
                statistics.p_before(full, year), abs=0.002)


def _await_job(client, job_id, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f'/api/jobs/{job_id}').json()['data']
        if body['state'] in ('completed', 'failed', 'cancelled'):
            return body
        time.sleep(0.2)
    pytest.fail(f'job {job_id} never finished')


def _variant(body, date_name, kind, direction=None):
    """A report row by what it does: saved dates take new ids."""
    return next(v for v in body['data']['variants']
                if v['date_name'] == date_name and v['kind'] == kind
                and v.get('direction') == direction)


def _saved_model(client, tag):
    dates = [{**d, 'material_type': 'charcoal', 'sample_type': 'terrestrial'}
             for d in _dates(tag)]
    response = client.post('/api/analysis/bayesian?wait=true', json={
        'name': f'Influence {tag}', 'measurements': dates, 'model_tree': _tree(tag),
        'save': True})
    assert response.status_code == 200, response.text
    return response.json()['data']['id']


class TestTheReport:
    @pytest.fixture(scope='class')
    def tested(self, suite_user):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app, headers={'Authorization': f"Bearer {suite_user['token']}"})
        tag = f'inf{next(_IDS)}x'
        analysis_id = _saved_model(client, tag)
        queued = client.post(f'/api/analysis/{analysis_id}/influence',
                             json={'kinds': ['leave_out', 'move']})
        assert queued.status_code == 202, queued.text
        assert queued.json()['data']['variants'] == 6 + 8
        job = _await_job(client, queued.json()['data']['job_id'])
        assert job['state'] == 'completed', job
        report = client.get(f'/api/analysis/{analysis_id}/influence').json()
        return client, analysis_id, tag, report

    def test_every_variant_is_reported(self, tested):
        _client, _id, _tag, body = tested
        report = body['data']
        assert body['stale'] is False and body['job_id'] is None
        assert len(report['variants']) == 14
        assert all(row['error'] is None and row['summary'] for row in report['variants'])
        assert report['solver'] == 'exact'

    def test_the_baseline_is_the_model_as_it_stands(self, tested):
        client, analysis_id, tag, body = tested
        stored = client.get(f'/api/analysis/{analysis_id}').json()['data']
        start = next(e for e in stored['model_tree']['children'] if e['name'] == 'Start')
        baseline = body['data']['baseline']['boundaries'][f'{tag}b0']
        assert baseline['median'] == pytest.approx(start['posterior']['posterior_median'], abs=0.5)
        assert any(key.startswith('sequence_duration') for key in body['data']['baseline']['durations'])

    def test_moving_a_date_older_moves_the_boundary_it_crosses(self, tested):
        """LB-3 (3150 BP) moved from Middle into Lower: the Mid Boundary between
        them can no longer sit above it, so its median comes out younger."""
        _client, _id, tag, body = tested
        row = _variant(body, 'LB-3', 'move', 'older')
        before = body['data']['baseline']['boundaries'][f'{tag}b1']['median']
        after = row['summary']['boundaries'][f'{tag}b1']['median']
        assert after < before - 10

    def test_a_variant_opens_as_an_analysis(self, tested):
        client, analysis_id, tag, body = tested
        opened = client.post(f'/api/analysis/{analysis_id}/influence/open',
                             json={'variant_id': _variant(body, 'LB-5', 'leave_out')['id']})
        assert opened.status_code == 202, opened.text
        data = opened.json()['data']
        assert _await_job(client, data['job_id'])['state'] == 'completed'
        made = client.get(f"/api/analysis/{data['id']}").json()['data']
        assert made['name'].endswith('(LB-5 left out)')
        assert sorted(m['sample_name'] for m in made['measurements']) == [
            'LB-1', 'LB-2', 'LB-3', 'LB-4', 'LB-6']

    def test_a_moved_variant_opens_in_the_copy_s_own_phase(self, tested):
        client, analysis_id, tag, body = tested
        variant = _variant(body, 'LB-1', 'move', 'younger')
        opened = client.post(f'/api/analysis/{analysis_id}/influence/open',
                             json={'variant_id': variant['id']}).json()['data']
        _await_job(client, opened['job_id'])
        made = client.get(f"/api/analysis/{opened['id']}").json()['data']
        middle = next(e for e in made['model_tree']['children'] if e['name'] == 'Middle')
        moved = next(m for m in made['measurements'] if m['sample_name'] == 'LB-1')
        assert moved['element_id'] == middle['id'] != f'{tag}p1'

    def test_the_job_is_not_mistaken_for_a_run_of_the_analysis(self, tested):
        """The Results page watches the latest job about the analysis for its
        own runs; an influence job must not be it."""
        client, analysis_id, _tag, _body = tested
        jobs = client.get(f'/api/jobs?target_id={analysis_id}').json()['data']['jobs']
        assert all(job['kind'] != 'influence' for job in jobs)

    def test_an_unknown_variant_is_404(self, tested):
        client, analysis_id, _tag, _body = tested
        response = client.post(f'/api/analysis/{analysis_id}/influence/open',
                               json={'variant_id': 'leave_out:nobody'})
        assert response.status_code == 404

    def test_editing_the_analysis_marks_the_report_stale(self, tested):
        client, analysis_id, tag, _body = tested
        from app.database import SessionLocal
        from app.models import RadiocarbonSample
        with SessionLocal() as db:
            row = db.query(RadiocarbonSample).filter(RadiocarbonSample.analysis_id == analysis_id,
                                                     RadiocarbonSample.sample_name == 'LB-6').one()
            row.age_bp += 10
            db.commit()
        assert client.get(f'/api/analysis/{analysis_id}/influence').json()['stale'] is True


class TestWhatIsRefused:
    def test_a_calibration_has_no_influence_to_test(self, client):
        response = client.post('/api/analysis/calibrate', json={
            'sample_name': 'LB-solo', 'age_bp': 3000, 'error_1sigma': 30,
            'curve_id': 'intcal20', 'save': True})
        analysis_id = response.json()['data']['id']
        refused = client.post(f'/api/analysis/{analysis_id}/influence',
                              json={'kinds': ['leave_out']})
        assert refused.status_code == 400

    def test_no_kinds_is_refused(self, client):
        analysis_id = _saved_model(client, f'inf{next(_IDS)}y')
        assert client.post(f'/api/analysis/{analysis_id}/influence',
                           json={'kinds': []}).status_code == 422

    def test_someone_else_s_analysis_is_404(self, client, anonymous_client):
        from fastapi.testclient import TestClient
        from app.main import app
        analysis_id = _saved_model(client, f'inf{next(_IDS)}z')
        token = anonymous_client.post('/api/auth/register', json={
            'email': 'influence-stranger@radical.example.com',
            'password': 'a good enough password'}).json()['data']['token']
        stranger = TestClient(app, headers={'Authorization': f'Bearer {token}'})
        assert stranger.get(f'/api/analysis/{analysis_id}/influence').status_code == 404
        assert stranger.post(f'/api/analysis/{analysis_id}/influence',
                             json={'kinds': ['leave_out']}).status_code == 404
