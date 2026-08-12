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

    def _ordered_without_a_boundary(self, tag):
        """Two phases ordered with no Boundary between them: solvable exactly only
        by supplying the transition they imply."""
        tree = _chain_tree(tag)
        tree['children'].insert(2, {'id': f'{tag}p2', 'element_type': 'phase',
                                   'name': 'Later', 'children': []})
        return tree, [
            _measurement(f'{tag}s1', f'{tag}p1', age_bp=3200),
            _measurement(f'{tag}s2', f'{tag}p2', age_bp=3000),
        ]

    def test_a_model_needing_a_transition_is_sampled_not_quietly_patched(self, client):
        """
        `auto` does not supply the transition. Doing so makes the model exactly
        solvable, but it splits one span term over K dates into two over K1 + K2 and
        is measurably the worse answer -- 2.51 years rms from OxCal against the
        sampler's 1.49 on the plain fixture, 10.63 against 4.53 on the charcoal one.
        So the user's model is taken as written and sampled.
        """
        tag = f'sr{next(_IDS)}'
        tree, measurements = self._ordered_without_a_boundary(tag)
        created = _run(client, tag, tree, measurements)
        assert created.status_code == 200, created.text
        solver = created.json()['data']['solver']
        assert solver['method'] == 'gibbs'
        # Nothing was supplied to the solver that ran...
        assert solver['implicit_boundaries'] == 0
        # ...but the record still says what an exact solve would have cost, which
        # is a different statement from "this cannot be solved exactly".
        assert solver['exact_needs_boundaries'] == 1

    def test_asking_for_exact_still_supplies_them_and_counts_them(self, client):
        """
        The override exists so an analyst can have the exact answer anyway. It
        gets it, transitions and all, and the record says how many -- rather than
        presenting the result as though their model were solvable as written.
        """
        tag = f'sr{next(_IDS)}'
        tree, measurements = self._ordered_without_a_boundary(tag)
        created = client.post('/api/analysis/bayesian', json={
            'name': f'Solver {tag}',
            'measurements': measurements,
            'model_tree': tree,
            'bayesian_config': {'mcmc_samples': 200, 'solver': 'exact'},
            'save': True,
        })
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


class TestSolverOverride:
    """
    bayesian_config.solver lets an analyst override the automatic choice. OxCal
    is the reference implementation, so there are two real reasons to want its
    method even where exact integration is available: reproducing a published
    analysis, and cross-checking the exact solver against an independent route
    to the same posterior.
    """

    def _chain(self, solver=None, tag=None):
        from app.api import analysis as analysis_api

        tag = tag or f'so{next(_IDS)}'
        config = {'mcmc_samples': 200}
        if solver is not None:
            config['solver'] = solver
        measurements = [
            _measurement(f'{tag}s1', f'{tag}p1', age_bp=3000),
            _measurement(f'{tag}s2', f'{tag}p1', age_bp=3040),
        ]
        return analysis_api._run_bayesian(measurements, _chain_tree(tag), config)

    def test_auto_still_integrates_a_chain_exactly(self):
        _r, _b, solver = self._chain()
        assert solver['method'] == 'exact'
        assert solver['requested'] == 'auto'

    def test_gibbs_can_be_forced_on_an_exactly_solvable_model(self):
        """
        The reproduce-OxCal and cross-check case. It must sample, and it must NOT
        report a reason -- there is nothing wrong with this model, so claiming it
        "cannot be solved exactly" would be false and would make the results page
        warn about a deliberate, valid decision.
        """
        _r, _b, solver = self._chain(solver='gibbs')
        assert solver['method'] == 'gibbs'
        assert solver['requested'] == 'gibbs'
        assert solver['reason'] is None
        assert solver['sweeps'] > 0

    def test_exact_can_be_required(self):
        _r, _b, solver = self._chain(solver='exact')
        assert solver['method'] == 'exact'
        assert solver['requested'] == 'exact'

    def test_requiring_exact_on_an_unsolvable_model_fails_loudly(self):
        """
        Refusing is the point. Quietly sampling when the analyst asked for an
        exact answer is the silent-fallback problem this whole area exists to
        remove, so it raises -- and the message says why the model cannot be
        integrated and what the alternative is.
        """
        from app.api import analysis as analysis_api

        tag = f'so{next(_IDS)}'
        measurements = [
            _measurement(f'{tag}s1', f'{tag}p1', age_bp=3200),
            _measurement(f'{tag}s2', f'{tag}p2', age_bp=3100),
            _measurement(f'{tag}s3', f'{tag}p3', age_bp=3000),
        ]
        with pytest.raises(ValueError) as raised:
            analysis_api._run_bayesian(
                measurements, _unbounded_tree(tag),
                {'mcmc_samples': 200, 'solver': 'exact'},
            )
        message = str(raised.value)
        assert 'outside every Boundary' in message, message
        assert 'MH/Gibbs' in message, 'the message should name the way forward'

    def test_the_two_solvers_agree_on_the_same_model(self):
        """
        What makes the cross-check worth offering: run the same model both ways
        and the answers match. Sub-year on this model, against calibrated ranges
        reported to 5 years.
        """
        tag = f'so{next(_IDS)}'
        exact_results, _eb, exact_solver = self._chain(solver='exact', tag=tag)
        sampled_results, _sb, sampled_solver = self._chain(solver='gibbs', tag=tag)
        assert exact_solver['method'] == 'exact'
        assert sampled_solver['method'] == 'gibbs'

        for sample_id, exact in exact_results.items():
            got = sampled_results[sample_id]['posterior_mean']
            assert got == pytest.approx(exact['posterior_mean'], abs=2.0), sample_id

    def test_each_solver_is_given_the_tree_it_can_take(self):
        """
        On a model that is not a chain as authored the two solvers see DIFFERENT
        trees -- the sampler the model as written, the exact solver a copy with the
        implied transition supplied, because it cannot integrate anything else.

        This reverses an earlier decision to hand both the patched tree so that the
        override was a pure method-vs-method cross-check. That property is real but
        costs accuracy on every such model, since the insertion weakens the (span)^
        (-K) exponent, and the record keeps the difference visible instead: the
        patch count is 1 for the exact solve and 0 for the sampled one.
        """
        tag = f'so{next(_IDS)}'
        tree = _chain_tree(tag)
        tree['children'].insert(2, {'id': f'{tag}p2', 'element_type': 'phase',
                                    'name': 'Later', 'children': []})
        measurements = [
            _measurement(f'{tag}s1', f'{tag}p1', age_bp=3200),
            _measurement(f'{tag}s2', f'{tag}p2', age_bp=3000),
        ]

        from app.api import analysis as analysis_api
        records = {}
        for choice in ('exact', 'gibbs'):
            _r, _b, records[choice] = analysis_api._run_bayesian(
                measurements, tree, {'mcmc_samples': 200, 'solver': choice})
        assert records['exact']['implicit_boundaries'] == 1
        assert records['gibbs']['implicit_boundaries'] == 0
        assert records['gibbs']['exact_needs_boundaries'] == 1

    def test_it_round_trips_through_a_saved_analysis(self, client):
        """The override has to survive being saved, or a rerun would silently
        revert to the automatic choice."""
        tag = f'so{next(_IDS)}'
        created = client.post('/api/analysis/bayesian', json={
            'name': f'Override {tag}',
            'measurements': [_measurement(f'{tag}s1', f'{tag}p1')],
            'model_tree': _chain_tree(tag),
            'bayesian_config': {'solver': 'gibbs', 'mcmc_samples': 200},
            'save': True,
        })
        assert created.status_code == 200, created.text
        assert created.json()['data']['solver']['method'] == 'gibbs'

        analysis_id = created.json()['data']['id']
        fetched = client.get(f'/api/analysis/{analysis_id}').json()['data']
        assert fetched['bayesian_config']['solver'] == 'gibbs'

        rerun = client.post(f'/api/analysis/{analysis_id}/rerun')
        assert rerun.status_code == 200, rerun.text
        after = client.get(f'/api/analysis/{analysis_id}').json()['data']
        assert after['solver']['method'] == 'gibbs', 'the rerun reverted to automatic'
        assert after['solver']['requested'] == 'gibbs'

    def test_an_impossible_override_is_refused_when_creating(self, client):
        """The refusal reaches the client as an error carrying the reason, rather
        than a result quietly computed some other way."""
        tag = f'so{next(_IDS)}'
        created = client.post('/api/analysis/bayesian', json={
            'name': f'Impossible {tag}',
            'measurements': [
                _measurement(f'{tag}s1', f'{tag}p1', age_bp=3200),
                _measurement(f'{tag}s2', f'{tag}p2', age_bp=3100),
                _measurement(f'{tag}s3', f'{tag}p3', age_bp=3000),
            ],
            'model_tree': _unbounded_tree(tag),
            'bayesian_config': {'solver': 'exact', 'mcmc_samples': 200},
            'save': True,
        })
        assert created.status_code == 400, created.text
        assert 'outside every Boundary' in created.json()['detail']

    def test_an_impossible_override_marks_a_saved_analysis_failed(self, client):
        """
        The other way in: an analysis that solved fine, then edited to require
        the exact solver on a structure that cannot support it. The rerun must
        fail visibly with the reason stored, not fall back to sampling.
        """
        tag = f'so{next(_IDS)}'
        measurements = [
            _measurement(f'{tag}s1', f'{tag}p1', age_bp=3200),
            _measurement(f'{tag}s2', f'{tag}p2', age_bp=3100),
            _measurement(f'{tag}s3', f'{tag}p3', age_bp=3000),
        ]
        created = client.post('/api/analysis/bayesian', json={
            'name': f'Then broken {tag}',
            'measurements': measurements,
            'model_tree': _unbounded_tree(tag),
            'bayesian_config': {'mcmc_samples': 200},
            'save': True,
        })
        assert created.status_code == 200, created.text
        assert created.json()['data']['solver']['method'] == 'gibbs'
        analysis_id = created.json()['data']['id']
        saved = client.get(f'/api/analysis/{analysis_id}').json()['data']

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
            'bayesian_config': {'solver': 'exact', 'mcmc_samples': 200},
        })
        assert replaced.status_code == 200, replaced.text

        rerun = client.post(f'/api/analysis/{analysis_id}/rerun')
        assert rerun.status_code == 400, rerun.text

        after = client.get(f'/api/analysis/{analysis_id}').json()['data']
        assert after['status'] == 'failed'
        assert 'outside every Boundary' in (after.get('error_message') or ''), after
        # No stale record claiming a method it never managed to use.
        assert after['solver'] is None

    def test_an_unknown_solver_is_refused_rather_than_guessed(self, client):
        tag = f'so{next(_IDS)}'
        response = client.post('/api/analysis/bayesian', json={
            'name': f'Bad {tag}',
            'measurements': [_measurement(f'{tag}s1', f'{tag}p1')],
            'model_tree': _chain_tree(tag),
            'bayesian_config': {'solver': 'whatever'},
            'save': False,
        })
        assert response.status_code == 422


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
