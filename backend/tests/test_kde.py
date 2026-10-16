"""
The kernel density summary (services/kde.py, OxCal's KDE_Plot) and the group
calibration option that produces it.

The binned sums are checked against the plain O(n^2) formulas they replace,
since every figure the summary reports rests on them. The bandwidth choice is
checked on data whose answer is known: two well-separated clusters need a
kernel much narrower than Silverman's, one normal cluster does not.
"""
import numpy as np
import pytest

from app.services import kde


def _exact_log_likelihood(t, h_s, g):
    n = t.size
    h = g * h_s
    diff = t[:, None] - t[None, :]
    kernel = np.exp(-0.5 * (diff / h) ** 2) / (h * np.sqrt(2 * np.pi))
    np.fill_diagonal(kernel, 0.0)
    return (n - 2) / n * np.log(kernel.sum(axis=1) / (n - 1)).sum()


def _exact_density(t, h, ages):
    z = (ages[:, None] - t[None, :]) / h
    return (np.exp(-0.5 * z ** 2) / (h * np.sqrt(2 * np.pi))).mean(axis=1)


def _bimodal(seed=1, per_mode=100):
    rng = np.random.default_rng(seed)
    return np.concatenate([rng.normal(2990, 3, per_mode), rng.normal(3010, 3, per_mode)])


def _precise(ages, width=0.5):
    """Dates known almost exactly, in the (cal_ages, masses) form calibrate
    returns: a KDE of these is a KDE of the ages themselves."""
    grid = np.arange(min(ages) - 10, max(ages) + 10, 0.1)
    return [(grid, np.exp(-0.5 * ((grid - a) / width) ** 2)) for a in ages]


class TestTheKernelSums:
    def test_silverman_is_equation_two(self):
        t = np.array([1.0, 2.0, 4.0, 8.0, 16.0])
        assert kde.silverman_bandwidth(t) == pytest.approx(
            (4 / 3) ** 0.2 * np.std(t, ddof=1) * 5 ** -0.2)

    @pytest.mark.parametrize('g', [0.05, 0.2, 0.5, 1.0])
    def test_the_likelihood_matches_the_pairwise_sum(self, g):
        t = _bimodal()
        h_s = kde.silverman_bandwidth(t)
        assert kde.log_likelihood(t, h_s, g) == pytest.approx(
            _exact_log_likelihood(t, h_s, g), abs=0.5), (
            'eq. 5 summed over 200 dates: within half a nat of the exact sum')

    def test_an_isolated_date_is_very_unlikely_not_impossible(self):
        # A bandwidth that leaves one date out of every other date's reach
        # makes it improbable by a definite amount. Cancelling to zero in the
        # binned sum would call it impossible and lose that amount.
        t = np.array([0.0, 1.0, 2.0, 3.0, 60.0])
        h_s = kde.silverman_bandwidth(t)
        got = kde.log_likelihood(t, h_s, 0.1)
        assert np.isfinite(got)
        assert got == pytest.approx(_exact_log_likelihood(t, h_s, 0.1), abs=0.01)

    def test_two_dates_say_nothing_about_the_bandwidth(self):
        t = np.array([100.0, 200.0])
        h_s = kde.silverman_bandwidth(t)
        assert kde.log_likelihood(t, h_s, 0.1) == kde.log_likelihood(t, h_s, 0.9) == 0.0

    def test_the_density_matches_the_direct_sum(self):
        t = _bimodal()
        h = 0.5 * kde.silverman_bandwidth(t)
        ages = np.arange(2960.0, 3041.0)
        exact = _exact_density(t, h, ages)
        assert np.abs(kde.kde_density(t, h, ages) - exact).max() < 0.005 * exact.max()


class TestTheBandwidth:
    def test_separated_clusters_get_a_narrower_kernel_than_silverman(self):
        summary = kde.summarise(_precise(_bimodal()), draws=100)
        assert summary['factor']['median'] < 0.6, (
            "Silverman's rule assumes one normal and over-smooths two; the "
            'likelihood should pull the bandwidth well below it')

    def test_one_normal_cluster_keeps_most_of_silverman(self):
        ages = np.random.default_rng(2).normal(3000, 5, 200)
        summary = kde.summarise(_precise(ages), draws=100)
        assert summary['factor']['median'] > 0.6

    def test_it_resolves_both_clusters(self):
        summary = kde.summarise(_precise(_bimodal()), draws=100)
        ages, density = np.array(summary['distribution'])[:, :2].T
        at = lambda age: density[np.argmin(np.abs(ages - age))]
        assert at(3000) < 0.5 * min(at(2990), at(3010)), (
            'the trough between the clusters must show')


class TestTheSummary:
    def test_it_is_a_density_with_a_band(self):
        summary = kde.summarise(_precise(_bimodal()), draws=100)
        table = np.array(summary['distribution'])
        ages, mean, sd = table.T
        assert np.all(np.diff(ages) > 0), 'ascending cal BP, as every stored distribution'
        assert np.trapezoid(mean, ages) == pytest.approx(1.0, abs=0.01)
        assert np.all(sd >= 0) and sd.max() > 0
        assert summary['dates'] == 200 and summary['draws'] == 100

    def test_it_is_reproducible(self):
        dates = _precise([2990.0, 3000.0, 3020.0])
        assert kde.summarise(dates, draws=50) == kde.summarise(dates, draws=50)

    def test_one_date_has_no_summary(self):
        assert kde.summarise(_precise([3000.0])) is None


def _group(client, name, **options):
    return client.post('/api/analysis/calibrate-group', json={
        'name': name, **options,
        'measurements': [
            {'id': f'{name}-{i}', 'sample_name': f'K{i}', 'age_bp': age, 'error_1sigma': 30}
            for i, age in enumerate([3000, 2950, 2900, 2600])
        ]})


class TestAGroupCalibration:
    def test_it_reports_a_kde_when_asked(self, client):
        created = _group(client, 'kde-on', estimate_kde=True)
        assert created.status_code == 200, created.text
        analysis_id = created.json()['data']['id']

        fetched = client.get(f'/api/analysis/{analysis_id}').json()['data']
        [block] = fetched['summaries']['kde']
        assert block['dates'] == 4
        assert block['element_id'] is None and block['reflected'] is False
        assert block['method'] == 'kde_plot'
        assert fetched['bayesian_config']['estimate_kde'] is True

        exported = client.get(f'/api/results/{analysis_id}/export/json')
        assert exported.json()['data']['summaries'] == fetched['summaries']

    def test_it_stays_off_by_default(self, client):
        analysis_id = _group(client, 'kde-off').json()['data']['id']
        assert client.get(f'/api/analysis/{analysis_id}').json()['data']['summaries'] is None

    def test_a_rerun_recomputes_it_and_an_edit_clears_it(self, client):
        analysis_id = _group(client, 'kde-rerun', estimate_kde=True).json()['data']['id']

        ran = client.post(f'/api/analysis/{analysis_id}/rerun?wait=true')
        assert ran.status_code == 200, ran.text
        assert client.get(f'/api/analysis/{analysis_id}').json()['data']['summaries']['kde']

        fetched = client.get(f'/api/analysis/{analysis_id}').json()['data']
        edited = client.put(f'/api/analysis/{analysis_id}/inputs', json={
            'name': 'kde-rerun', 'analysis_type': 'group_calibration',
            'measurements': [
                {'id': m['id'], 'sample_name': m['sample_name'], 'age_bp': m['age_bp'],
                 'error_1sigma': m['error_1sigma'], 'curve_id': m['curve_id']}
                for m in fetched['measurements']
            ],
            'groups': [], 'relationships': [],
            'bayesian_config': {'estimate_kde': True},
        })
        assert edited.status_code == 200, edited.text
        assert client.get(f'/api/analysis/{analysis_id}').json()['data']['summaries'] is None, (
            'a summary of the dates just replaced describes nothing')

        client.post(f'/api/analysis/{analysis_id}/rerun?wait=true')
        assert client.get(f'/api/analysis/{analysis_id}').json()['data']['summaries']['kde']


class TestTheExports:
    def test_the_csv_carries_the_summary_and_the_curve(self, client):
        import csv
        import io
        analysis_id = _group(client, 'kde-csv', estimate_kde=True).json()['data']['id']
        [kde_block] = client.get(f'/api/analysis/{analysis_id}').json()['data']['summaries']['kde']

        text = client.get(f'/api/results/{analysis_id}/export/csv?date_format=bcad').text
        rows = list(csv.reader(io.StringIO(text)))
        summary = [r for r in rows if r and r[0] == 'KDE Summary']
        curve = [r for r in rows if r and r[0] == 'KDE']
        assert len(summary) == 1 and summary[0][2] == '4'
        assert len(curve) == len(kde_block['distribution'])
        assert 'BC' in curve[0][5] or 'AD' in curve[0][5], 'the age gains its BC/AD counterpart'

    def test_the_csv_has_no_kde_block_when_not_asked(self, client):
        analysis_id = _group(client, 'kde-csv-off').json()['data']['id']
        text = client.get(f'/api/results/{analysis_id}/export/csv').text
        assert 'KDE' not in text

    def test_the_pdf_has_a_kde_section(self, client):
        from tests.test_pdf_report import pdf_text
        analysis_id = _group(client, 'kde-pdf', estimate_kde=True).json()['data']['id']
        response = client.get(f'/api/results/{analysis_id}/pdf')
        assert response.status_code == 200
        assert 'Kernel Density Estimates' in pdf_text(response.content)

        plain = _group(client, 'kde-pdf-off').json()['data']['id']
        assert 'Kernel Density Estimates' not in pdf_text(
            client.get(f'/api/results/{plain}/pdf').content)


# --------------------------------------------------------------------------
# Inside a model: one KDE per phase, reflected at its Boundaries
# --------------------------------------------------------------------------

def _element(eid, kind, name, children=()):
    return {'id': eid, 'element_type': kind, 'name': name,
            'tpq_age_bp': None, 'taq_age_bp': None, 'children': list(children)}


def _two_phase_tree(prefix):
    return _element(f'{prefix}-root', 'sequence', 'Site', [
        _element(f'{prefix}-b0', 'boundary', 'Start'),
        _element(f'{prefix}-p1', 'phase', 'Lower'),
        _element(f'{prefix}-b1', 'boundary', 'Mid'),
        _element(f'{prefix}-p2', 'phase', 'Upper'),
        _element(f'{prefix}-b2', 'boundary', 'End'),
    ])


class TestReflection:
    def test_a_uniform_phase_stays_flat_up_to_its_boundaries(self):
        t = np.linspace(0.5, 99.5, 400)
        ages = np.arange(-20.0, 121.0)
        h = 5.0
        reflected = kde.kde_density(t, h, ages, (0.0, 100.0))
        plain = kde.kde_density(t, h, ages)
        at = lambda d, age: d[np.argmin(np.abs(ages - age))]
        assert at(reflected, 1) == pytest.approx(at(reflected, 50), rel=0.1)
        assert at(plain, 0) == pytest.approx(0.5 * at(plain, 50), rel=0.1), (
            'unreflected, the density halves at the edge -- the rounding-off '
            'reflection exists to remove')
        assert np.all(reflected[(ages < 0) | (ages > 100)] == 0)
        assert np.trapezoid(reflected, ages) == pytest.approx(1.0, abs=0.01)

    def test_each_phase_gets_its_own_reflected_kde(self):
        rng = np.random.default_rng(3)
        states = 300
        start = 3400 + rng.normal(0, 5, states)
        mid = 3200 + rng.normal(0, 5, states)
        end = 3000 + rng.normal(0, 5, states)
        draws = {'r-b0': start, 'r-b1': mid, 'r-b2': end}
        measurements = []
        for index in range(6):
            phase, (old, young) = (('r-p1', (start, mid)) if index < 3
                                   else ('r-p2', (mid, end)))
            draws[f'r-d{index}'] = young + rng.uniform(0, 1, states) * (old - young)
            measurements.append({'id': f'r-d{index}', 'element_id': phase})

        records = kde.report_phases(_two_phase_tree('r'), measurements, draws)
        assert [r['element_id'] for r in records] == ['r-p1', 'r-p2']
        assert all(r['reflected'] and r['dates'] == 3 for r in records)
        lower = np.array(records[0]['distribution'])
        assert lower[:, 0].max() <= start.max() + 1, (
            'no density older than the phase could have started')
        assert lower[:, 0].min() >= mid.min() - 1


class TestGriddedDraws:
    def test_dates_sharing_a_grid_point_still_have_a_kde(self):
        """
        A solver's draws are grid points, so in some states every date of a
        short phase lands on one of them. Spread across their cells, those
        states still have a bandwidth.
        """
        rng = np.random.default_rng(4)
        states = 200
        draws = {'g-b0': np.full(states, 3410.0), 'g-b1': np.full(states, 3200.0),
                 'g-b2': np.full(states, 3000.0)}
        measurements = []
        for index in range(3):
            # One in five states puts all three on 3300.
            values = rng.choice([3290.0, 3300.0, 3310.0], states)
            values[::5] = 3300.0
            draws[f'g-d{index}'] = values
            measurements.append({'id': f'g-d{index}', 'element_id': 'g-p1'})

        [record] = kde.report_phases(_two_phase_tree('g'), measurements, draws, grid_step=10.0)
        assert record['draws'] == states

    def test_without_a_grid_a_state_with_no_spread_is_left_out(self):
        sampled = np.array([[1.0, 5.0, 2.0], [1.0, 6.0, 4.0], [1.0, 9.0, 3.0]])
        assert kde.summarise_draws(sampled)['draws'] == 2


class TestAModel:
    def _run(self, client, prefix, solver='auto', estimate_kde=True):
        measurements = [
            {'id': f'{prefix}-{i}', 'sample_name': f'M{i}', 'age_bp': age,
             'error_1sigma': 30, 'element_id': f'{prefix}-{phase}'}
            for i, (age, phase) in enumerate(
                [(3300, 'p1'), (3280, 'p1'), (3260, 'p1'),
                 (3000, 'p2'), (3020, 'p2'), (2990, 'p2')])
        ]
        created = client.post('/api/analysis/bayesian?wait=true', json={
            'name': prefix, 'measurements': measurements,
            'model_tree': _two_phase_tree(prefix), 'save': True,
            # Untempered where the sampler runs: what is tested is the record,
            # not the sampling.
            'bayesian_config': {'mcmc_samples': 200, 'solver': solver,
                                'estimate_kde': estimate_kde, 'mcmc_temperatures': 1},
        })
        assert created.status_code == 200, created.text
        return created.json()['data']['id']

    @pytest.mark.slow
    @pytest.mark.parametrize('solver', ['exact', 'gibbs'])
    def test_it_reports_one_kde_per_phase(self, client, solver):
        prefix = f'km-{solver}'
        analysis_id = self._run(client, prefix, solver)
        fetched = client.get(f'/api/analysis/{analysis_id}').json()['data']
        records = fetched['summaries']['kde']
        assert [r['name'] for r in records] == ['Lower', 'Upper']
        assert all(r['reflected'] for r in records)
        phase_ids = {c['id'] for c in fetched['model_tree']['children']
                     if c['element_type'] == 'phase'}
        assert {r['element_id'] for r in records} == phase_ids, (
            'each KDE names a phase that exists in the stored model')

        ran = client.post(f'/api/analysis/{analysis_id}/rerun?wait=true')
        assert ran.status_code == 200, ran.text
        again = client.get(f'/api/analysis/{analysis_id}').json()['data']['summaries']['kde']
        assert [r['element_id'] for r in again] == [r['element_id'] for r in records]

    def test_it_stays_off_by_default(self, client):
        analysis_id = self._run(client, 'km-off', estimate_kde=False)
        assert client.get(f'/api/analysis/{analysis_id}').json()['data']['summaries'] is None
