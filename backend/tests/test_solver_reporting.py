"""
Which solver ran, recorded on the analysis and returned to the client.

This exists because the fallback used to be invisible. A chain-shaped model is
integrated exactly; anything else is sampled by SMC, which has a known and
measured failure on wiggly calibration curves (see services/exact_chain.py's
module docstring). Both came back looking identical, so a result carrying that
error was indistinguishable from an exact one -- and the shapes that trigger it
are ordinary, not exotic: a sequence of phases with no Boundary anywhere, or two
parallel excavation areas.

The exact path is pure numpy, so most of this needs no sampler. The one test
that covers the fallback's bookkeeping stubs the sampler out rather than running
it: what is being checked is the record, not the sampling.
"""
import itertools

import pytest

_IDS = itertools.count(1)


def _chain_tree(tag):
    """Sequence(Start, Phase, End) -- the exactly-solvable shape."""
    return {
        'id': f'{tag}root', 'element_type': 'sequence', 'name': 'Site', 'children': [
            {'id': f'{tag}b0', 'element_type': 'boundary', 'name': 'Start', 'children': []},
            {'id': f'{tag}p1', 'element_type': 'phase', 'name': 'Only', 'children': []},
            {'id': f'{tag}b1', 'element_type': 'boundary', 'name': 'End', 'children': []},
        ],
    }


def _unbounded_tree(tag):
    """
    Sequence(Phase, Phase, Phase) with no Boundary anywhere -- an ordinary
    naive model, and one that reaches SMC. Implicit transitions between the
    three phases give two boundaries and so one bounded group (the middle
    phase), but the first and last phases' dates are still outside every
    boundary, which the chain cannot express.
    """
    return {
        'id': f'{tag}root', 'element_type': 'sequence', 'name': 'Site', 'children': [
            {'id': f'{tag}p1', 'element_type': 'phase', 'name': 'Early', 'children': []},
            {'id': f'{tag}p2', 'element_type': 'phase', 'name': 'Middle', 'children': []},
            {'id': f'{tag}p3', 'element_type': 'phase', 'name': 'Late', 'children': []},
        ],
    }


def _measurement(mid, element_id, age_bp=3000):
    return {
        'id': mid, 'sample_name': f'Sample {mid}', 'age_bp': age_bp,
        'error_1sigma': 30, 'material_type': 'charcoal',
        'sample_type': 'terrestrial', 'curve_id': 'shcal20',
        'element_id': element_id,
    }


def _run(client, tag, tree, measurements):
    return client.post('/api/analysis/bayesian', json={
        'name': f'Solver {tag}',
        'measurements': measurements,
        'model_tree': tree,
        'bayesian_config': {'mcmc_samples': 200, 'mcmc_tune': 200, 'mcmc_chains': 1},
        'save': True,
    })


class TestExactPath:
    def test_run_reports_and_persists_the_exact_solver(self, client):
        tag = f'sr{next(_IDS)}'
        created = _run(client, tag, _chain_tree(tag),
                       [_measurement(f'{tag}s1', f'{tag}p1')])
        assert created.status_code == 200, created.text
        assert created.json()['data']['solver']['method'] == 'exact'
        assert created.json()['data']['solver']['reason'] is None

        analysis_id = created.json()['data']['id']
        fetched = client.get(f'/api/analysis/{analysis_id}').json()['data']
        assert fetched['solver']['method'] == 'exact'

    def test_implicit_transitions_are_counted_not_hidden(self, client):
        """
        Two phases ordered with no Boundary between them are solved exactly by
        supplying the transition they imply -- a change to the model, so the
        record says how many were added rather than presenting the result as
        though the user's model were solvable as written.
        """
        tag = f'sr{next(_IDS)}'
        tree = _chain_tree(tag)
        tree['children'].insert(2, {'id': f'{tag}p2', 'element_type': 'phase',
                                   'name': 'Later', 'children': []})
        created = _run(client, tag, tree, [
            _measurement(f'{tag}s1', f'{tag}p1', age_bp=3200),
            _measurement(f'{tag}s2', f'{tag}p2', age_bp=3000),
        ])
        assert created.status_code == 200, created.text
        solver = created.json()['data']['solver']
        assert solver['method'] == 'exact'
        assert solver['implicit_boundaries'] == 1


class TestFallbackIsRecorded:
    """
    The sampled path. It runs for real here -- services/gibbs.py is pure numpy
    and fast, where the SMC sampler it replaced took minutes and had to be
    stubbed out to test any of this.
    """

    def _sampled(self, sweeps=2000):
        from app.api import analysis as analysis_api

        tag = f'sr{next(_IDS)}'
        tree = _unbounded_tree(tag)
        measurements = [
            _measurement(f'{tag}s1', f'{tag}p1', age_bp=3200),
            _measurement(f'{tag}s2', f'{tag}p2', age_bp=3100),
            _measurement(f'{tag}s3', f'{tag}p3', age_bp=3000),
        ]
        results, boundaries, solver = analysis_api._run_bayesian(
            measurements, tree, {'mcmc_samples': 200},
        )
        return measurements, tree, results, boundaries, solver

    def test_it_says_it_sampled_and_why(self):
        measurements, _tree, results, _boundaries, solver = self._sampled()
        assert solver['method'] == 'gibbs'
        assert solver['reason'], 'a fallback with no reason is barely better than a silent one'
        assert solver['implicit_boundaries'] == 0
        # The record has to carry enough to judge the answer, not just name the
        # method -- a sampled result with no convergence figure is a result the
        # user cannot weigh.
        assert solver['sweeps'] > 0
        assert 0.0 <= solver['convergence'] <= 1.0
        assert isinstance(solver['converged'], bool)
        # And it really did produce results, which the stubbed version could not
        # establish.
        assert set(results) == {m['id'] for m in measurements}
        for summary in results.values():
            assert summary['posterior_mean'] > 0

    def test_the_reason_describes_the_model_as_it_stands_after_patching(self):
        """
        Implicit transitions are supplied BEFORE giving up, so the reason must
        describe what is wrong with the PATCHED model, not the original
        complaint the patching has already dealt with.

        Here that makes the message strictly more useful: before patching the
        model has no boundaries at all, so all that can be said is "add some";
        after, the interior transitions exist and what remains is precisely
        which dates are still left outside them.
        """
        from app.services import exact_chain

        measurements, tree, _results, _boundaries, solver = self._sampled()
        _chain, original = exact_chain.layout_with_reason(tree, measurements)
        assert 'no Boundary encloses any date' in original
        assert solver['reason'] != original
        assert 'outside every Boundary' in solver['reason']


class TestStaleRecords:
    def test_editing_the_inputs_clears_it(self, client):
        """
        Editing the structure is exactly what changes which solver applies, so
        a record kept across the edit would label a pending analysis "exact"
        on the strength of a model that no longer exists.
        """
        tag = f'sr{next(_IDS)}'
        created = _run(client, tag, _chain_tree(tag),
                       [_measurement(f'{tag}s1', f'{tag}p1')])
        assert created.status_code == 200, created.text
        analysis_id = created.json()['data']['id']
        saved = client.get(f'/api/analysis/{analysis_id}').json()['data']
        assert saved['solver']['method'] == 'exact'

        replaced = client.put(f'/api/analysis/{analysis_id}/inputs', json={
            'name': saved['name'],
            'measurements': [
                {
                    'id': m['id'], 'sample_name': m['sample_name'],
                    'age_bp': m['age_bp'], 'error_1sigma': m['error_1sigma'],
                    'material_type': m.get('material_type') or 'unknown',
                    'sample_type': m.get('sample_type') or 'terrestrial',
                    'curve_id': m.get('curve_id') or 'intcal20',
                    'element_id': m.get('element_id'),
                }
                for m in saved['measurements']
            ],
            'groups': [], 'relationships': [],
            'model_tree': saved['model_tree'],
            'bayesian_config': {'mcmc_samples': 200, 'mcmc_tune': 200, 'mcmc_chains': 1},
        })
        assert replaced.status_code == 200, replaced.text
        assert client.get(f'/api/analysis/{analysis_id}').json()['data']['solver'] is None

    def test_a_rerun_records_it_again(self, client):
        tag = f'sr{next(_IDS)}'
        created = _run(client, tag, _chain_tree(tag),
                       [_measurement(f'{tag}s1', f'{tag}p1')])
        assert created.status_code == 200, created.text
        analysis_id = created.json()['data']['id']

        rerun = client.post(f'/api/analysis/{analysis_id}/rerun')
        assert rerun.status_code == 200, rerun.text
        assert client.get(f'/api/analysis/{analysis_id}').json()['data']['solver']['method'] == 'exact'

    def test_a_duplicate_has_not_been_run_so_carries_no_record(self, client):
        tag = f'sr{next(_IDS)}'
        created = _run(client, tag, _chain_tree(tag),
                       [_measurement(f'{tag}s1', f'{tag}p1')])
        assert created.status_code == 200, created.text
        analysis_id = created.json()['data']['id']

        copy = client.post(f'/api/analysis/{analysis_id}/duplicate')
        assert copy.status_code == 200, copy.text
        copy_id = copy.json()['data']['id']
        assert client.get(f'/api/analysis/{copy_id}').json()['data']['solver'] is None
