"""
Which solver ran, recorded on the analysis and returned to the client.

This exists because the fallback used to be invisible. A chain-shaped model is
integrated exactly; anything else is sampled by SMC, which has a known and
measured failure on wiggly calibration curves (see services/exact_chain.py's
module docstring). Both came back looking identical, so a result carrying that
error was indistinguishable from an exact one -- and the shapes that trigger it
are ordinary, not exotic: two excavation areas with no Boundary they share, say.

The exact path is pure numpy, so most of this needs no sampler. The one test
that covers the fallback's bookkeeping stubs the sampler out rather than running
it: what is being checked is the record, not the sampling.

Where the sampler does run it runs untempered (mcmc_temperatures 1), for the
same reason: a tempered run costs three times as much and records nothing a
plain one does not.
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
    naive model. Implicit transitions between the three phases give two
    boundaries and so one bounded group (the middle phase), with the first and
    last phases' dates outside every boundary, which the exact solver takes
    as the model's two tails -- unless one carries an outlier model.
    """
    return {
        'id': f'{tag}root', 'element_type': 'sequence', 'name': 'Site', 'children': [
            {'id': f'{tag}p1', 'element_type': 'phase', 'name': 'Early', 'children': []},
            {'id': f'{tag}p2', 'element_type': 'phase', 'name': 'Middle', 'children': []},
            {'id': f'{tag}p3', 'element_type': 'phase', 'name': 'Late', 'children': []},
        ],
    }


def _separate_areas_tree(tag):
    """
    Phase(Sequence(Phase p1, Phase p2), Sequence(Phase p3, Phase p4)) with no
    Boundary anywhere: two areas with nothing they share around them, so
    separate models rather than areas of one site. One the exact solver
    cannot integrate, and so sampled.
    """
    def area(name, phases):
        return {'id': f'{tag}{name}', 'element_type': 'sequence', 'name': name,
                'children': [{'id': f'{tag}{p}', 'element_type': 'phase', 'name': p,
                              'children': []} for p in phases]}
    return {'id': f'{tag}root', 'element_type': 'phase', 'name': 'Site',
            'children': [area('North', ['p1', 'p2']), area('South', ['p3', 'p4'])]}


SEPARATE_AREAS = 'no Boundary before and after them'


def _measurement(mid, element_id, age_bp=3000):
    return {
        'id': mid, 'sample_name': f'Sample {mid}', 'age_bp': age_bp,
        'error_1sigma': 30, 'material_type': 'charcoal',
        'sample_type': 'terrestrial', 'curve_id': 'shcal20',
        'element_id': element_id,
    }


def _run(client, tag, tree, measurements):
    return client.post('/api/analysis/bayesian?wait=true', json={
        'name': f'Solver {tag}',
        'measurements': measurements,
        'model_tree': tree,
        'bayesian_config': {'mcmc_samples': 200, 'mcmc_tune': 200, 'mcmc_chains': 1,
                            'mcmc_temperatures': 1},
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

    def _needing_transitions(self, tag):
        """Three phases in a Sequence with no Boundary at all: solvable exactly
        only with Boundaries the model does not have. (Phases in a row between
        Boundaries need none: test_exact_ordered.py.)"""
        return _unbounded_tree(tag), [
            _measurement(f'{tag}s1', f'{tag}p1', age_bp=3200),
            _measurement(f'{tag}s2', f'{tag}p2', age_bp=3100),
            _measurement(f'{tag}s3', f'{tag}p3', age_bp=3000),
        ]

    def test_a_model_needing_boundaries_is_sampled_and_says_what_to_add(self, client):
        """
        The app never adds a Boundary to a model. Doing so would make this one
        exactly solvable, but it answers a different model -- one span term
        over K dates split into several -- and measurably a worse one: on the
        ordered fixture, 2.33 years rms from OxCal with the transition supplied
        against 0.56 for the model as written. So it is sampled as written, and
        the reason says what to add.
        """
        tag = f'sr{next(_IDS)}'
        tree, measurements = self._needing_transitions(tag)
        created = _run(client, tag, tree, measurements)
        assert created.status_code == 200, created.text
        solver = created.json()['data']['solver']
        assert solver['method'] == 'gibbs'
        assert 'Add a Boundary at the start and end of the Sequence' in solver['reason']
        assert 'implicit_boundaries' not in solver
        assert 'exact_needs_boundaries' not in solver

    def test_asking_for_exact_is_refused_rather_than_adding_them(self, client):
        """
        Choosing the exact solver does not license changing the model: someone
        who chose it cannot be assumed to know Boundaries would be added. The
        run is refused, with what to add.
        """
        tag = f'sr{next(_IDS)}'
        tree, measurements = self._needing_transitions(tag)
        created = client.post('/api/analysis/bayesian?wait=true', json={
            'name': f'Solver {tag}',
            'measurements': measurements,
            'model_tree': tree,
            'bayesian_config': {'mcmc_samples': 200, 'solver': 'exact'},
            'save': True,
        })
        assert created.status_code == 400, created.text
        assert 'Add a Boundary at the start and end' in created.json()['detail']


class TestFallbackIsRecorded:
    """
    The sampled path. It runs for real here -- services/gibbs.py is pure numpy
    and fast, where the SMC sampler it replaced took minutes and had to be
    stubbed out to test any of this.
    """

    def _sampled(self, sweeps=2000):
        from app.api import analysis as analysis_api

        tag = f'sr{next(_IDS)}'
        tree = _separate_areas_tree(tag)
        measurements = [
            _measurement(f'{tag}s1', f'{tag}p1', age_bp=3200),
            _measurement(f'{tag}s2', f'{tag}p2', age_bp=3100),
            _measurement(f'{tag}s3', f'{tag}p3', age_bp=3000),
        ]
        results, boundaries, solver, _intervals, _summaries = analysis_api._run_bayesian(
            measurements, tree, {'mcmc_samples': 200, 'mcmc_temperatures': 1},
        )
        return measurements, tree, results, boundaries, solver

    def test_it_says_it_sampled_and_why(self):
        measurements, _tree, results, _boundaries, solver = self._sampled()
        assert solver['method'] == 'gibbs'
        assert solver['reason'], 'a fallback with no reason is barely better than a silent one'
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

    def test_the_reason_describes_the_model_as_written(self):
        """
        Nothing is added to the model before the reason is given -- the app
        used to supply transitions first and report what was wrong with the
        PATCHED model -- so it names what the user's own model lacks.
        """
        from app.api import analysis as analysis_api
        from app.services import exact_chain

        tag = f'sr{next(_IDS)}'
        tree = _unbounded_tree(tag)
        measurements = [
            {**_measurement(f'{tag}s1', f'{tag}p1', age_bp=3200), 'outlier_prior': 0.05},
            _measurement(f'{tag}s2', f'{tag}p2', age_bp=3100),
            _measurement(f'{tag}s3', f'{tag}p3', age_bp=3000),
        ]
        _results, _boundaries, solver, _intervals, _summaries = analysis_api._run_bayesian(
            measurements, tree, {'mcmc_samples': 200, 'mcmc_temperatures': 1},
        )
        _chain, original = exact_chain.layout_with_reason(tree, measurements)
        assert solver['method'] == 'gibbs'
        assert solver['reason'] == original
        assert 'no pair of Boundaries encloses any date' in original


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
        config = {'mcmc_samples': 200, 'mcmc_temperatures': 1}
        if solver is not None:
            config['solver'] = solver
        measurements = [
            _measurement(f'{tag}s1', f'{tag}p1', age_bp=3000),
            _measurement(f'{tag}s2', f'{tag}p1', age_bp=3040),
        ]
        return analysis_api._run_bayesian(measurements, _chain_tree(tag), config)

    def test_auto_still_integrates_a_chain_exactly(self):
        _r, _b, solver, _i, _s = self._chain()
        assert solver['method'] == 'exact'
        assert solver['requested'] == 'auto'

    def test_gibbs_can_be_forced_on_an_exactly_solvable_model(self):
        """
        The reproduce-OxCal and cross-check case. It must sample, and it must NOT
        report a reason -- there is nothing wrong with this model, so claiming it
        "cannot be solved exactly" would be false and would make the results page
        warn about a deliberate, valid decision.
        """
        _r, _b, solver, _i, _s = self._chain(solver='gibbs')
        assert solver['method'] == 'gibbs'
        assert solver['requested'] == 'gibbs'
        assert solver['reason'] is None
        assert solver['sweeps'] > 0

    def test_exact_can_be_required(self):
        _r, _b, solver, _i, _s = self._chain(solver='exact')
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
                measurements, _separate_areas_tree(tag),
                {'mcmc_samples': 200, 'solver': 'exact'},
            )
        message = str(raised.value)
        assert SEPARATE_AREAS in message, message
        assert 'MH/Gibbs' in message, 'the message should name the way forward'

    def test_the_two_solvers_agree_on_the_same_model(self):
        """
        What makes the cross-check worth offering: run the same model both ways
        and the answers match. Sub-year on this model, against calibrated ranges
        reported to 5 years.
        """
        tag = f'so{next(_IDS)}'
        exact_results, _eb, exact_solver, _ei, _es = self._chain(solver='exact', tag=tag)
        sampled_results, _sb, sampled_solver, _si, _ss = self._chain(solver='gibbs', tag=tag)
        assert exact_solver['method'] == 'exact'
        assert sampled_solver['method'] == 'gibbs'

        for sample_id, exact in exact_results.items():
            got = sampled_results[sample_id]['posterior_mean']
            assert got == pytest.approx(exact['posterior_mean'], abs=2.0), sample_id

    def test_it_round_trips_through_a_saved_analysis(self, client):
        """The override has to survive being saved, or a rerun would silently
        revert to the automatic choice."""
        tag = f'so{next(_IDS)}'
        created = client.post('/api/analysis/bayesian?wait=true', json={
            'name': f'Override {tag}',
            'measurements': [_measurement(f'{tag}s1', f'{tag}p1')],
            'model_tree': _chain_tree(tag),
            'bayesian_config': {'solver': 'gibbs', 'mcmc_samples': 200,
                                'mcmc_temperatures': 1},
            'save': True,
        })
        assert created.status_code == 200, created.text
        assert created.json()['data']['solver']['method'] == 'gibbs'

        analysis_id = created.json()['data']['id']
        fetched = client.get(f'/api/analysis/{analysis_id}').json()['data']
        assert fetched['bayesian_config']['solver'] == 'gibbs'

        rerun = client.post(f'/api/analysis/{analysis_id}/rerun?wait=true')
        assert rerun.status_code == 200, rerun.text
        after = client.get(f'/api/analysis/{analysis_id}').json()['data']
        assert after['solver']['method'] == 'gibbs', 'the rerun reverted to automatic'
        assert after['solver']['requested'] == 'gibbs'

    def test_an_impossible_override_is_refused_when_creating(self, client):
        """The refusal reaches the client as an error carrying the reason, rather
        than a result quietly computed some other way."""
        tag = f'so{next(_IDS)}'
        created = client.post('/api/analysis/bayesian?wait=true', json={
            'name': f'Impossible {tag}',
            'measurements': [
                _measurement(f'{tag}s1', f'{tag}p1', age_bp=3200),
                _measurement(f'{tag}s2', f'{tag}p2', age_bp=3100),
                _measurement(f'{tag}s3', f'{tag}p3', age_bp=3000),
            ],
            'model_tree': _separate_areas_tree(tag),
            'bayesian_config': {'solver': 'exact', 'mcmc_samples': 200},
            'save': True,
        })
        assert created.status_code == 400, created.text
        assert SEPARATE_AREAS in created.json()['detail']

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
        created = client.post('/api/analysis/bayesian?wait=true', json={
            'name': f'Then broken {tag}',
            'measurements': measurements,
            'model_tree': _separate_areas_tree(tag),
            'bayesian_config': {'mcmc_samples': 200, 'mcmc_temperatures': 1},
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

        rerun = client.post(f'/api/analysis/{analysis_id}/rerun?wait=true')
        assert rerun.status_code == 400, rerun.text

        after = client.get(f'/api/analysis/{analysis_id}').json()['data']
        assert after['status'] == 'failed'
        assert SEPARATE_AREAS in (after.get('error_message') or ''), after
        # No stale record claiming a method it never managed to use.
        assert after['solver'] is None

    def test_the_reporting_resolution_is_recorded_and_applied(self, client):
        tag = f'rs{next(_IDS)}'
        created = client.post('/api/analysis/bayesian?wait=true', json={
            'name': f'Resolution {tag}',
            'measurements': [_measurement(f'{tag}s1', f'{tag}p1')],
            'model_tree': _chain_tree(tag),
            'bayesian_config': {'mcmc_samples': 200, 'resolution': 2},
            'save': True,
        })
        assert created.status_code == 200, created.text
        data = created.json()['data']
        assert data['solver']['resolution'] == 2
        for result in data['results'].values():
            for r in result['hpd_95_ranges']:
                assert r['min'] % 2 == 0 and r['max'] % 2 == 0

        fetched = client.get(f"/api/analysis/{data['id']}").json()['data']
        assert fetched['solver']['resolution'] == 2
        assert fetched['bayesian_config']['resolution'] == 2

    def test_the_reporting_resolution_defaults_to_five_years(self, client):
        tag = f'rd{next(_IDS)}'
        created = _run(client, tag, _chain_tree(tag),
                       [_measurement(f'{tag}s1', f'{tag}p1')])
        assert created.status_code == 200, created.text
        assert created.json()['data']['solver']['resolution'] == 5

    @pytest.mark.parametrize('resolution', [0, 101, 2.5])
    def test_an_unusable_resolution_is_refused(self, client, resolution):
        tag = f'rb{next(_IDS)}'
        response = client.post('/api/analysis/bayesian?wait=true', json={
            'name': f'Bad {tag}',
            'measurements': [_measurement(f'{tag}s1', f'{tag}p1')],
            'model_tree': _chain_tree(tag),
            'bayesian_config': {'resolution': resolution},
            'save': False,
        })
        assert response.status_code == 422

    def test_an_unknown_solver_is_refused_rather_than_guessed(self, client):
        tag = f'so{next(_IDS)}'
        response = client.post('/api/analysis/bayesian?wait=true', json={
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

        rerun = client.post(f'/api/analysis/{analysis_id}/rerun?wait=true')
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


class TestModelAgreementIsReported:
    """
    The two model-level agreement indices travel with the run that produced
    them: they are properties of the whole fit, so they ride on the solver
    block rather than on any date's row (see services/agreement.py), and the
    results page reads them from there.
    """

    def _both(self, solver):
        return solver.get('model_agreement'), solver.get('overall_agreement')

    def test_the_exact_path_reports_and_persists_both_indices(self, client):
        tag = f'sr{next(_IDS)}'
        created = _run(client, tag, _chain_tree(tag), [
            _measurement(f'{tag}s1', f'{tag}p1', age_bp=3000),
            _measurement(f'{tag}s2', f'{tag}p1', age_bp=3040),
        ])
        assert created.status_code == 200, created.text
        solver = created.json()['data']['solver']
        assert solver['method'] == 'exact'
        model, overall = self._both(solver)
        # A lone phase constrains nothing between its dates, so a healthy model
        # -- which this is -- reports both near 100 rather than merely present.
        assert model is not None and overall is not None
        assert 80.0 < model < 130.0, model
        assert 80.0 < overall < 130.0, overall

        analysis_id = created.json()['data']['id']
        fetched = client.get(f'/api/analysis/{analysis_id}').json()['data']
        assert self._both(fetched['solver']) == (model, overall)

    def test_the_sampled_path_reports_both_indices_too(self, client):
        """
        Or the number would mean something different depending on which solver
        a model happened to qualify for.
        """
        tag = f'sr{next(_IDS)}'
        created = _run(client, tag, _separate_areas_tree(tag), [
            _measurement(f'{tag}s1', f'{tag}p1', age_bp=3200),
            _measurement(f'{tag}s2', f'{tag}p2', age_bp=3100),
            _measurement(f'{tag}s3', f'{tag}p3', age_bp=3000),
        ])
        assert created.status_code == 200, created.text
        solver = created.json()['data']['solver']
        assert solver['method'] == 'gibbs'
        model, overall = self._both(solver)
        assert model is not None and overall is not None
        assert 0.0 <= model <= 200.0 and 0.0 <= overall <= 200.0

    def test_a_model_its_dates_contradict_reports_a_low_amodel(self, client):
        """
        What the number is for. The younger phase holds a date 600 radiocarbon
        years older than the phase above it, so the ordering cannot be
        satisfied -- and the whole-model index has to say so, not just the one
        date's own.
        """
        tag = f'sr{next(_IDS)}'
        tree = _chain_tree(tag)
        tree['children'].insert(2, {'id': f'{tag}p2', 'element_type': 'phase',
                                    'name': 'Later', 'children': []})
        created = _run(client, tag, tree, [
            _measurement(f'{tag}s1', f'{tag}p1', age_bp=3000),
            _measurement(f'{tag}s2', f'{tag}p1', age_bp=3020),
            _measurement(f'{tag}s3', f'{tag}p2', age_bp=2900),
            _measurement(f'{tag}s4', f'{tag}p2', age_bp=3640),
        ])
        assert created.status_code == 200, created.text
        model, _overall = self._both(created.json()['data']['solver'])
        assert model is not None and model < 60.0, model
