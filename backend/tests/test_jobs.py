"""
The background job queue: services/jobs.py and api/jobs.py.

A run is queued rather than held open in the request that asked for it, so
these cover the things that only exist because of that -- a job reaching
completed, a failure landing on the job as well as the analysis, cancellation
leaving previous results alone, work running in order rather than at once, and
a restart not leaving rows claiming to be running forever.

Timing. The tests that wait do so by polling with a timeout rather than by
sleeping a fixed amount: a Bayesian run takes as long as it takes, and a
sleep long enough to be safe on a slow machine is a sleep wasted on every
other run. Nothing here asserts on how long anything took.
"""
import threading
import time
from datetime import datetime

import pytest

from app.database import SessionLocal
from app.models import Analysis, Job
from app.services import jobs


def _payload(tag, name='Job test', count=3, solver='gibbs'):
    """
    A small two-phase model. Two phases rather than one because a single
    phase in a sequence is chain-shaped and would be integrated exactly --
    and the exact solver has no progress to report and nothing to cancel
    between, which is half of what this file is about.

    `tag` namespaces every id: the suite shares one database, and model
    element ids are a primary key (see conftest).

    Untempered (mcmc_temperatures 1): what is tested here is the queue, and a
    tempered run costs three times as much to say nothing more about it.
    """
    phases = [f'{tag}p{i}' for i in range(2)]
    return {
        'name': name,
        'save': True,
        'bayesian_config': {'solver': solver, 'mcmc_temperatures': 1},
        'measurements': [
            {
                'id': f'{tag}m{i}',
                'sample_name': f'S{i}',
                'age_bp': 3000 - 40 * i,
                'error_1sigma': 30,
                'curve_id': 'intcal20',
                'element_id': phases[i % 2],
            }
            for i in range(count * 2)
        ],
        'model_tree': {
            'id': f'{tag}seq', 'element_type': 'sequence', 'name': 'Site',
            'children': [
                {'id': f'{tag}b0', 'element_type': 'boundary', 'name': 'Start', 'children': []},
                {'id': phases[0], 'element_type': 'phase', 'name': 'A', 'children': []},
                {'id': f'{tag}b1', 'element_type': 'boundary', 'name': 'Mid', 'children': []},
                {'id': phases[1], 'element_type': 'phase', 'name': 'B', 'children': []},
                {'id': f'{tag}b2', 'element_type': 'boundary', 'name': 'End', 'children': []},
            ],
        },
    }


def _await_job(client, job_id, timeout=180):
    """Poll until the job stops, and return its final body."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f'/api/jobs/{job_id}').json()['data']
        if body['state'] in ('completed', 'failed', 'cancelled'):
            return body
        time.sleep(0.1)
    pytest.fail(f"job {job_id} never finished (last state {body['state']})")


class TestQueueing:
    def test_a_queued_run_answers_202_and_completes(self, client):
        """
        The whole point: the request returns straight away with something to
        poll, and the analysis has its results by the time the job says so.
        """
        posted = client.post('/api/analysis/bayesian', json=_payload('jqa'))
        assert posted.status_code == 202, posted.text
        data = posted.json()['data']
        assert data['status'] == 'queued'
        assert data['job_id']

        finished = _await_job(client, data['job_id'])
        assert finished['state'] == 'completed', finished
        assert finished['progress'] == 1.0

        analysis = client.get(f"/api/analysis/{data['id']}").json()['data']
        assert analysis['status'] == 'completed'
        assert len(analysis['results']) == 6

    def test_wait_true_still_answers_with_the_results(self, client):
        """
        The synchronous path is not a migration aid -- it is how anything
        without an event loop to poll from uses this API. It answers 200 with
        the finished run, exactly as it did before there was a queue.
        """
        posted = client.post('/api/analysis/bayesian?wait=true',
                             json=_payload('jqb'))
        assert posted.status_code == 200, posted.text
        data = posted.json()['data']
        assert data['status'] == 'completed'
        assert len(data['results']) == 6
        assert 'job_id' not in data

    def test_an_unsaved_run_is_never_queued(self, client):
        """
        save=false persists nothing, so there is no analysis a later poll
        could read results from and no row to hang a job on. It has to answer
        in full, and does.
        """
        posted = client.post('/api/analysis/bayesian',
                             json=dict(_payload('jqc'), save=False))
        assert posted.status_code == 200, posted.text
        data = posted.json()['data']
        assert data['saved'] is False and data['id'] is None
        assert len(data['results']) == 6

    def test_a_queued_rerun_recomputes_the_analysis(self, client):
        created = client.post('/api/analysis/bayesian?wait=true',
                              json=_payload('jqd')).json()['data']
        posted = client.post(f"/api/analysis/{created['id']}/rerun")
        assert posted.status_code == 202, posted.text
        finished = _await_job(client, posted.json()['data']['job_id'])
        assert finished['state'] == 'completed', finished
        assert client.get(f"/api/analysis/{created['id']}").json(
        )['data']['status'] == 'completed'


class TestProgress:
    def test_the_sampler_reports_its_way_up(self, client):
        """
        Progress has to actually arrive while the run is going, which is the
        one thing a finished job cannot tell you. Collected by polling
        alongside the run.

        Asserts that something was reported and that sweeps never went
        backwards -- not that any particular fraction was seen, since a run
        that converges quickly may only report a few times.
        """
        posted = client.post('/api/analysis/bayesian', json=_payload('jqe', count=5))
        job_id = posted.json()['data']['job_id']

        seen = []
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            body = client.get(f'/api/jobs/{job_id}').json()['data']
            if body.get('detail') and 'sweeps' in (body['detail'] or {}):
                seen.append(body['detail'])
            if body['state'] in ('completed', 'failed', 'cancelled'):
                break
            time.sleep(0.05)

        assert body['state'] == 'completed', body
        assert seen, "the run finished without ever reporting progress"
        sweeps = [d['sweeps'] for d in seen]
        assert sweeps == sorted(sweeps), sweeps
        assert all(d['phase'] in ('burn_in', 'sampling') for d in seen)

    def test_progress_is_a_fraction_of_the_cap_not_a_prediction(self, client):
        """
        A run stops as soon as it converges, so a job can and usually does
        finish having reported only a small fraction. That is a success, and
        the reported convergence is what says so -- this pins the meaning,
        because a bar that reached 0.15 and then said 'done' looks like a bug
        to anyone who assumes it was a prediction.
        """
        posted = client.post('/api/analysis/bayesian', json=_payload('jqf'))
        job_id = posted.json()['data']['job_id']

        last = None
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            body = client.get(f'/api/jobs/{job_id}').json()['data']
            if body.get('detail') and 'convergence' in (body['detail'] or {}):
                last = body['detail']
            if body['state'] in ('completed', 'failed', 'cancelled'):
                break
            time.sleep(0.05)

        assert body['state'] == 'completed'
        if last and last['phase'] == 'sampling':
            assert last['sweeps'] < last['max_sweeps'], (
                "this model ran to the sweep cap; it is meant to converge first")
            assert 0.0 <= last['convergence'] <= 1.0


class TestFailure:
    def test_a_failed_run_fails_the_job_and_the_analysis(self, client):
        """
        Both, with the message on both. The job is what the page is polling,
        and the analysis is what it shows afterwards; a failure recorded on
        only one of them leaves the other lying.
        """
        created = client.post('/api/analysis/bayesian?wait=true',
                              json=_payload('jqg')).json()['data']

        # Break the analysis behind the endpoint's back -- a curve that no
        # longer resolves is exactly the failure a rerun hits in the wild,
        # and there is no API for producing one on purpose.
        with SessionLocal() as db:
            analysis = db.query(Analysis).filter(Analysis.id == created['id']).first()
            for sample in analysis.measurements:
                sample.curve_id = 'no-such-curve'
            db.commit()

        posted = client.post(f"/api/analysis/{created['id']}/rerun")
        finished = _await_job(client, posted.json()['data']['job_id'])
        assert finished['state'] == 'failed', finished
        assert finished['error_message']

        analysis = client.get(f"/api/analysis/{created['id']}").json()['data']
        assert analysis['status'] == 'failed'
        assert analysis['error_message']

    def test_a_job_for_a_deleted_analysis_fails_rather_than_crashing(self, client):
        created = client.post('/api/analysis/bayesian?wait=true',
                              json=_payload('jqh')).json()['data']
        posted = client.post(f"/api/analysis/{created['id']}/rerun")
        job_id = posted.json()['data']['job_id']
        client.delete(f"/api/analysis/{created['id']}")

        finished = _await_job(client, job_id)
        # Either it got there first or it did not; both are fine, and neither
        # may be an unhandled exception that leaves the job stuck running.
        assert finished['state'] in ('completed', 'failed'), finished


class TestCancellation:
    def test_cancelling_stops_the_run_and_keeps_the_old_results(self, client):
        """
        A cancel is a stop, not an undo. The analysis keeps whatever it had,
        because a cancelled run commits nothing.
        """
        created = client.post('/api/analysis/bayesian?wait=true',
                              json=_payload('jqi', count=6)).json()['data']
        before = client.get(f"/api/analysis/{created['id']}").json()['data']
        assert before['status'] == 'completed'
        before_results = len(before['results'])

        posted = client.post(f"/api/analysis/{created['id']}/rerun")
        job_id = posted.json()['data']['job_id']

        # Wait until it is genuinely running, so this tests the running-job
        # path rather than the drop-before-start one.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if client.get(f'/api/jobs/{job_id}').json()['data']['state'] == 'running':
                break
            time.sleep(0.02)

        assert client.delete(f'/api/jobs/{job_id}').status_code == 200
        finished = _await_job(client, job_id)
        assert finished['state'] == 'cancelled', finished

        after = client.get(f"/api/analysis/{created['id']}").json()['data']
        assert after['status'] == 'completed'
        assert len(after['results']) == before_results

    def test_cancelling_a_finished_job_is_a_409(self, client):
        created = client.post('/api/analysis/bayesian', json=_payload('jqj'))
        job_id = created.json()['data']['job_id']
        _await_job(client, job_id)
        response = client.delete(f'/api/jobs/{job_id}')
        assert response.status_code == 409
        assert 'nothing to cancel' in response.json()['detail']

    def test_the_sampler_stops_when_the_callback_says_to(self):
        """
        The library-level contract, without a job around it: on_progress
        returning False raises Cancelled out of run(). Asserted here because
        it is the mechanism every cancellation above depends on, and it is
        cheaper and more direct to check it at this level than through HTTP.
        """
        from app.services import gibbs

        payload = _payload('jqk')
        measurements = [dict(m, error_1sigma=30) for m in payload['measurements']]
        model = gibbs.Model(measurements, payload['model_tree'])

        calls = []

        def stop_after_two(info):
            calls.append(info)
            return len(calls) < 2

        with pytest.raises(gibbs.Cancelled):
            gibbs.run(model, on_progress=stop_after_two, temperatures=1)
        assert len(calls) == 2


class TestOrdering:
    def test_two_submissions_run_one_after_the_other(self, client):
        """
        One worker, on purpose: the sampler's loop is pure Python and holds
        the GIL, so two concurrent runs would each take about twice as long
        and neither would finish sooner. This asserts they do not overlap --
        the second cannot start before the first has finished.
        """
        first = client.post('/api/analysis/bayesian', json=_payload('jql', count=5))
        second = client.post('/api/analysis/bayesian', json=_payload('jqm', count=5))
        first_id = first.json()['data']['job_id']
        second_id = second.json()['data']['job_id']

        a = _await_job(client, first_id)
        b = _await_job(client, second_id)
        assert a['state'] == 'completed'
        assert b['state'] == 'completed'

        # Judged on the worker's own timestamps, not on polling both states:
        # two separate GETs can straddle the handover and see both 'running'.
        first_finished = datetime.fromisoformat(a['finished_at'])
        second_started = datetime.fromisoformat(b['started_at'])
        assert second_started >= first_finished, "both jobs were running at once"


class TestReconciliation:
    def test_a_restart_fails_whatever_was_mid_flight(self, client):
        """
        Nothing survives the process that was running it, so a row still
        saying 'running' at startup describes work that will never finish.
        Left alone the results page polls it forever.
        """
        with SessionLocal() as db:
            stranded_job = Job(kind='analysis_run', target_id='gone', state='running')
            queued_job = Job(kind='analysis_run', target_id='gone', state='queued')
            stranded_analysis = Analysis(
                name='Interrupted', analysis_type='bayesian', status='running')
            db.add_all([stranded_job, queued_job, stranded_analysis])
            db.commit()
            job_ids = [stranded_job.id, queued_job.id]
            analysis_id = stranded_analysis.id

        jobs.reconcile_orphans()

        with SessionLocal() as db:
            for job_id in job_ids:
                row = db.query(Job).filter(Job.id == job_id).first()
                assert row.state == 'failed'
                assert 'restart' in row.error_message
                assert row.finished_at is not None
            analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
            assert analysis.status == 'failed'
            assert 'restart' in analysis.error_message
            db.delete(analysis)
            db.commit()

    def test_it_leaves_finished_jobs_alone(self, client):
        with SessionLocal() as db:
            done = Job(kind='analysis_run', target_id='gone', state='completed')
            db.add(done)
            db.commit()
            done_id = done.id

        jobs.reconcile_orphans()

        with SessionLocal() as db:
            assert db.query(Job).filter(Job.id == done_id).first().state == 'completed'


class TestJobEndpoints:
    def test_an_unknown_job_is_a_404(self, client):
        assert client.get('/api/jobs/does-not-exist').status_code == 404
        assert client.delete('/api/jobs/does-not-exist').status_code == 404

    def test_jobs_can_be_found_by_what_they_are_about(self, client):
        """
        What a page loaded from a bookmark needs: it arrives holding an
        analysis id and no job id, and still has to know whether something is
        running before it decides what to show.
        """
        posted = client.post('/api/analysis/bayesian', json=_payload('jqn'))
        analysis_id = posted.json()['data']['id']
        job_id = posted.json()['data']['job_id']
        _await_job(client, job_id)

        listed = client.get(f'/api/jobs?target_id={analysis_id}').json()['data']['jobs']
        assert [j['job_id'] for j in listed] == [job_id]
        assert listed[0]['target_id'] == analysis_id
