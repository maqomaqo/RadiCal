"""
"Match OxCal 4.4": reproducing OxCal rather than solving the model as stated.

Observed OxCal 4.4 behaviour that the default solvers deliberately do not
copy: partway through a run it narrows every parameter's limits to the range
sampled so far, once, and the span prior's limits factor then reads those
narrower limits; it interpolates the curve cubically; and it cuts a
likelihood to zero beyond 5.26 standard deviations. On one Phase of eight
dates between two Boundaries (IntCal20, 1480 to 1410 BP in steps of ten,
+/-25) OxCal's span is 26.26 years against 28.4 solved as stated.
"""
import itertools

import numpy as np
import pytest

from app.services import calibration, gibbs
from app.services.calibration import OXCAL_LIKELIHOOD_CUTOFF_Z, get_curve

_IDS = itertools.count(1)

OXCAL_SPAN_EIGHT_DATES = 26.26


def _phase(ages, tag='m'):
    tree = {'id': f'{tag}seq', 'element_type': 'sequence', 'name': 'Site', 'children': [
        {'id': f'{tag}b1', 'element_type': 'boundary', 'name': 'Start', 'children': []},
        {'id': f'{tag}p', 'element_type': 'phase', 'name': 'Phase', 'children': []},
        {'id': f'{tag}b2', 'element_type': 'boundary', 'name': 'End', 'children': []},
    ]}
    measurements = [{
        'id': f'{tag}u{i}', 'sample_name': f'U{i}', 'age_bp': age, 'error_1sigma': 25,
        'material_type': 'charcoal', 'sample_type': 'terrestrial',
        'curve_id': 'intcal20', 'element_id': f'{tag}p',
    } for i, age in enumerate(ages)]
    return tree, measurements


EIGHT = list(range(1480, 1405, -10))


class TestLikelihood:
    def test_off_by_default(self):
        assert not calibration.is_matching_oxcal()
        with calibration.matching_oxcal():
            assert calibration.is_matching_oxcal()
        assert not calibration.is_matching_oxcal()

    def test_cut_beyond_the_cutoff(self):
        curve = get_curve('intcal20')
        cal = np.arange(2000.0, 4500.0)
        plain = curve.likelihood(cal, 3000.0, 30.0)
        with calibration.matching_oxcal():
            matched = curve.likelihood(cal, 3000.0, 30.0)
        # Somewhere the plain likelihood is positive but far out in its tail.
        far = (plain > 0) & (plain < plain.max() * np.exp(-0.5 * 6.0 ** 2))
        assert far.any() and np.all(matched[far] == 0.0)
        near = plain > plain.max() * np.exp(-0.5 * 4.0 ** 2)
        assert np.all(matched[near] > 0.0)
        assert OXCAL_LIKELIHOOD_CUTOFF_Z == pytest.approx(5.2565, abs=1e-4)

    def test_cubic_agrees_at_the_points_and_differs_between(self):
        curve = get_curve('intcal20')
        nodes = curve.cal_ages[(curve.cal_ages > 3000) & (curve.cal_ages < 3200)]
        c14, error = curve.interpolate_cubic(nodes)
        assert np.allclose(c14, np.interp(nodes, curve.cal_ages, curve.c14_ages))
        assert np.allclose(error, np.interp(nodes, curve.cal_ages, curve.c14_errors))
        between = np.arange(10000.5, 10500.0, 1.0)     # 5-year spacing here
        assert not np.allclose(curve.interpolate_cubic(between)[0],
                               curve.interpolate(between)[0])


    def test_a_curve_still_pickles_after_cubic_use(self):
        """Curves go to the sampler's worker processes by pickle."""
        import pickle
        curve = get_curve('intcal20')
        curve.interpolate_cubic(np.array([3000.5]))
        copy = pickle.loads(pickle.dumps(curve))
        assert np.allclose(copy.interpolate_cubic(np.array([3000.5]))[0],
                           curve.interpolate_cubic(np.array([3000.5]))[0])


class TestShrink:
    def test_sampled_range_is_trimmed_at_the_peak_fraction(self):
        counts = np.array([0.0, 1.0, 5e5, 3.0, 0.0])
        assert gibbs.sampled_ranges({'p': counts}, trim=1e-6) == {'p': (1, 3)}
        assert gibbs.sampled_ranges({'p': counts}, trim=3e-6) == {'p': (2, 3)}
        assert gibbs.sampled_ranges({'q': np.zeros(4)}) == {}

    def test_narrows_the_windows_and_the_limits_factor(self):
        tree, measurements = _phase(EIGHT)
        model = gibbs.Model(measurements, tree)
        before = {key: row.copy() for key, row in model.limits_tables.items()}
        index = lambda year: int(np.searchsorted(model.grid, year))   # noqa: E731
        # As a run samples them: the Start older than the End, overlapping.
        start, end = model.boundary_ids
        ranges = {start: (index(1330), index(1450)), end: (index(1280), index(1400))}
        model.shrink_limits(ranges)
        for b, (lo_year, hi_year) in ((start, (1330, 1450)), (end, (1280, 1400))):
            lo, hi = model.window[b]
            assert model.grid[lo] >= lo_year - model.dt and model.grid[hi] <= hi_year + model.dt
        # With limits close to the dates the factor falls faster with span.
        (key, row), = model.limits_tables.items()
        short, longer = 2, 10
        assert (row[short] - row[longer]) > (before[key][short] - before[key][longer])

    def test_a_tempered_chain_refuses(self):
        tree, measurements = _phase(EIGHT)
        chain = gibbs._Chain(gibbs.Model(measurements, tree), 2000, 1, None, None, 0,
                             temperatures=2)
        with pytest.raises(ValueError):
            chain.shrink({})

    def test_a_run_not_converged_at_round_four_is_shrunk_later(self, monkeypatch):
        """
        OxCal checks at the end of every group from the fourth on and
        narrows at the first one whose convergence is above 0.5, once. So a
        run that is not converged at round four is asked again each round
        until it is, and never after.
        """
        tree, measurements = _phase(EIGHT)
        model = gibbs.Model(measurements, tree)
        real, asked = gibbs._shrink_due, []

        def not_yet_twice(rounds, params):
            asked.append(len(rounds))
            return None if len(asked) <= 2 else real(rounds, params)

        monkeypatch.setattr(gibbs, '_shrink_due', not_yet_twice)
        result = gibbs.run(model, sweeps=4000, seed=1, temperatures=1, match_oxcal=True)
        first = gibbs.shrink_round()
        assert result['limits_shrunk']
        assert asked == [first, first + 1, first + 2]

    def test_narrows_a_phase_toward_oxcal(self):
        """
        One chain, the default seed. Solved as stated the span is about 28.4;
        matched it lands within a couple of years of OxCal's 26.26, the rest
        being the run's own Monte Carlo variation (26.2-27.6 across seeds and
        chain counts).
        """
        tree, measurements = _phase(EIGHT)
        with calibration.matching_oxcal():
            matched = gibbs.solve(measurements, tree, chains=1, match_oxcal=True,
                                  estimate_durations=True, num_samples=500)
        plain = gibbs.solve(measurements, tree, chains=1, temperatures=1,
                            estimate_durations=True, num_samples=500)
        span = lambda r: r['intervals']['durations'][0]['years']['mean']   # noqa: E731
        assert matched['limits_shrunk'] and not plain['limits_shrunk']
        assert matched['lobe_probe'] is None
        assert span(matched) < span(plain) - 1.0
        assert span(matched) == pytest.approx(OXCAL_SPAN_EIGHT_DATES, abs=2.0)


class TestThroughTheApi:
    def _post(self, client, config):
        tag = f'mo{next(_IDS)}'
        tree, measurements = _phase(EIGHT, tag)
        return client.post('/api/analysis/bayesian?wait=true', json={
            'name': f'Match {tag}', 'measurements': measurements, 'model_tree': tree,
            'bayesian_config': {'mcmc_samples': 200, 'mcmc_chains': 1, **config},
            'save': True,
        })

    def test_runs_the_sampler_and_says_so(self, client):
        created = self._post(client, {'match_oxcal': True})
        assert created.status_code == 200, created.text
        analysis_id = created.json()['data']['id']
        solver = client.get(f'/api/analysis/{analysis_id}').json()['data']['solver']
        assert solver['method'] == 'gibbs'
        assert solver['match_oxcal'] is True
        assert solver['cross_check']['status'] == 'not_checked'

    def test_off_is_the_exact_solver_as_before(self, client):
        created = self._post(client, {})
        analysis_id = created.json()['data']['id']
        solver = client.get(f'/api/analysis/{analysis_id}').json()['data']['solver']
        assert solver['method'] == 'exact'
        assert 'match_oxcal' not in solver

    def test_with_the_exact_solver_is_refused(self, client):
        created = self._post(client, {'match_oxcal': True, 'solver': 'exact'})
        assert created.status_code == 400, created.text
        assert 'Match OxCal 4.4' in created.json()['detail']
