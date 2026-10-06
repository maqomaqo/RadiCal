"""
Tests for services/statistics.py: the Ward and Wilson same-event test, and
where it reaches the Results page and the JSON export.

The five groups are the acceptance table of the October 2026 feature spec
(Q2), from the Shag River Mouth corpus; the reference implementation is
`chi2` in corpus/corpus_checks.py of the AD1250-1450 test bundle. Tolerances
are the spec's: T within 0.1, critical values to two decimal places.
"""
import csv
import io

import pytest

from app.services.statistics import same_event, same_event_report
from tests.test_pdf_report import pdf_text

NENTHORN = [  # old-wood dates (Wk-50104, Wk-50098, ANU-55207) excluded
    (685, 25), (671, 21), (635, 21), (671, 20), (647, 21), (636, 21), (676, 26), (637, 24)]
LEGACY_AND_OTHER_AREAS = [
    (660, 46), (646, 47), (630, 35), (670, 47), (600, 50), (580, 47), (537, 44), (560, 40),
    (600, 50), (570, 45), (647, 47), (627, 40), (626, 95), (592, 48), (585, 93), (582, 47)]
LAYER_4_SHELLS = [
    (1070, 45), (1060, 45), (1050, 50), (1040, 45), (1040, 50), (1020, 50), (1010, 45),
    (1010, 50), (990, 45), (980, 40), (980, 45), (965, 26), (960, 45), (950, 45)]
NEW_LAYERS_11_TO_7 = [(614, 20), (627, 20), (694, 16), (709, 16), (623, 16), (645, 16)]
NEW_LAYERS_11_TO_4 = NEW_LAYERS_11_TO_7 + [
    (546, 15), (565, 16), (597, 18), (590, 15), (620, 15), (543, 20)]


def _dates(pairs, curve_id='shcal20', **extra):
    return [{'id': f'd{i}', 'age_bp': age, 'error_1sigma': error, 'curve_id': curve_id,
             'delta_r': None, 'delta_r_error': None, **extra}
            for i, (age, error) in enumerate(pairs)]


class TestSameEvent:
    @pytest.mark.parametrize('pairs, curve, mean, error, t, critical, consistent', [
        (NENTHORN, 'shcal20', 656, 8, 5.7, 14.07, True),
        (LEGACY_AND_OTHER_AREAS, 'shcal20', 607, 12, 10.7, 25.00, True),
        (LAYER_4_SHELLS, 'marine20', 1002, 11, 11.2, 22.36, True),
        (NEW_LAYERS_11_TO_7, 'shcal20', 656, 7, 27.9, 11.07, False),
        (NEW_LAYERS_11_TO_4, 'shcal20', None, None, 111, 19.68, False),
    ])
    def test_reproduces_the_spec_acceptance_table(
            self, pairs, curve, mean, error, t, critical, consistent):
        result = same_event(_dates(pairs, curve))
        assert result['n'] == len(pairs)
        assert result['df'] == len(pairs) - 1
        assert result['t'] == pytest.approx(t, abs=0.1 if t < 100 else 0.5)
        assert round(result['critical'], 2) == critical
        assert result['consistent'] is consistent
        if mean is not None:
            assert round(result['mean_bp']) == mean
            assert round(result['error_bp']) == error

    def test_needs_two_dates(self):
        assert same_event(_dates([(600, 20)])) is None
        assert same_event([]) is None

    def test_pooled_date_is_calibrated_only_when_consistent(self):
        passed = same_event(_dates(NENTHORN))
        levels = [entry['confidence'] for entry in passed['pooled_hpd_ranges']]
        assert len(levels) == 3 and levels == sorted(levels)
        assert all(entry['ranges'] for entry in passed['pooled_hpd_ranges'])

        failed = same_event(_dates(NEW_LAYERS_11_TO_7))
        assert failed['pooled_hpd_ranges'] is None

    def test_refuses_dates_on_different_curves(self):
        dates = _dates([(600, 20), (610, 20)])
        dates[1]['curve_id'] = 'intcal20'
        result = same_event(dates)
        assert result['refused']
        assert result['t'] is None and result['consistent'] is None

    def test_refuses_dates_with_different_reservoir_offsets(self):
        dates = _dates([(1000, 30), (1010, 30)], 'marine20')
        dates[0].update(delta_r=-190, delta_r_error=17)
        dates[1].update(delta_r=-172, delta_r_error=92)
        assert same_event(dates)['refused']

    def test_a_shared_reservoir_offset_leaves_t_unchanged(self):
        """A shared ΔR shifts every age alike. Adding its error to each date
        before pooling would shrink T, counting one uncertainty n times."""
        plain = same_event(_dates(LAYER_4_SHELLS, 'marine20'))
        shared = same_event(_dates(LAYER_4_SHELLS, 'marine20', delta_r=-172, delta_r_error=92))
        assert shared['refused'] is None
        assert shared['t'] == pytest.approx(plain['t'])
        assert shared['mean_bp'] == pytest.approx(plain['mean_bp'])

    def test_refuses_a_date_with_no_error(self):
        assert same_event(_dates([(600, 20), (610, 0)]))['refused']


class TestReport:
    TREE = {
        'id': 'r', 'element_type': 'sequence', 'name': 'Site', 'children': [
            {'id': 'b0', 'element_type': 'boundary', 'name': 'Start', 'children': []},
            {'id': 'p1', 'element_type': 'phase', 'name': 'Lower', 'children': []},
            {'id': 'b1', 'element_type': 'boundary', 'name': 'Mid', 'children': []},
            {'id': 'p2', 'element_type': 'phase', 'name': 'Upper', 'children': []},
            {'id': 'b2', 'element_type': 'boundary', 'name': 'End', 'children': []},
        ],
    }

    def test_one_test_per_phase_with_two_or_more_dates(self):
        dates = _dates([(700, 20), (690, 20), (600, 20)])
        dates[0]['element_id'] = dates[1]['element_id'] = 'p1'
        dates[2]['element_id'] = 'p2'
        report = same_event_report('bayesian', self.TREE, dates)
        assert [(r['element_id'], r['name'], r['n']) for r in report] == [('p1', 'Lower', 2)]

    def test_group_calibration_is_tested_as_one_set(self):
        report = same_event_report('group_calibration', None, _dates(NENTHORN))
        assert len(report) == 1
        assert report[0]['element_id'] is None
        assert report[0]['n'] == len(NENTHORN)

    def test_single_calibration_reports_nothing(self):
        assert same_event_report('calibration', None, _dates([(600, 20)])) == []


def _measurement(name, age, error, **extra):
    return {'sample_name': name, 'age_bp': age, 'error_1sigma': error,
            'material_type': 'charcoal', 'sample_type': 'terrestrial',
            'curve_id': 'shcal20', **extra}


def _block(text: str, first_cell: str):
    """The rows of one CSV block, found by its header's first cells."""
    rows = list(csv.reader(io.StringIO(text)))
    start = next(i for i, r in enumerate(rows) if r[:2] == ['Row Type', first_cell])
    block = []
    for row in rows[start:]:
        if not row:
            break
        block.append(row)
    return block[0], block[1:]


_GROUP: dict = {}


def _group(client) -> str:
    """One Nenthorn group calibration, shared: sample ids are global keys."""
    if not _GROUP:
        created = client.post('/api/analysis/calibrate-group', json={
            'name': 'Nenthorn',
            'measurements': [{**_measurement(f'N{i}', a, e), 'id': f'sg_d{i}'}
                             for i, (a, e) in enumerate(NENTHORN)],
        })
        assert created.status_code == 200, created.text
        _GROUP['id'] = created.json()['data']['id']
    return _GROUP['id']


class TestRoutes:
    def test_group_calibration_reports_the_test_on_read_and_in_export(self, client):
        analysis_id = _group(client)

        shown = client.get(f'/api/analysis/{analysis_id}').json()['data']['same_event']
        exported = client.get(f'/api/results/{analysis_id}/export/json').json()['data']
        assert shown == exported['same_event']
        assert len(shown) == 1
        assert shown[0]['t'] == pytest.approx(5.7, abs=0.1)
        assert shown[0]['consistent'] is True

    def test_bayesian_phases_report_the_test_and_export_the_solver(self, client):
        tree = {
            'id': 'st_root', 'element_type': 'sequence', 'name': 'Site', 'children': [
                {'id': 'st_b0', 'element_type': 'boundary', 'name': 'Start', 'children': []},
                {'id': 'st_p1', 'element_type': 'phase', 'name': 'Layer 11', 'children': []},
                {'id': 'st_b1', 'element_type': 'boundary', 'name': 'End', 'children': []},
            ],
        }
        created = client.post('/api/analysis/bayesian?wait=true', json={
            'name': 'Same-event phase',
            'measurements': [
                {**_measurement(f'L{i}', a, e), 'id': f'st_d{i}', 'element_id': 'st_p1'}
                for i, (a, e) in enumerate(NEW_LAYERS_11_TO_7)],
            'model_tree': tree,
            'bayesian_config': {'mcmc_samples': 200, 'mcmc_tune': 200, 'mcmc_chains': 1},
            'save': True,
        })
        assert created.status_code == 200, created.text
        analysis_id = created.json()['data']['id']

        shown = client.get(f'/api/analysis/{analysis_id}').json()['data']
        exported = client.get(f'/api/results/{analysis_id}/export/json').json()['data']
        assert exported['same_event'] == shown['same_event']
        [phase] = shown['same_event']
        assert phase['name'] == 'Layer 11'
        assert phase['t'] == pytest.approx(27.9, abs=0.1)
        assert phase['consistent'] is False

        assert exported['solver'] == shown['solver']
        assert exported['solver']['method'] in ('exact', 'gibbs')

    def test_csv_carries_a_same_event_block(self, client):
        analysis_id = _group(client)
        text = client.get(f'/api/results/{analysis_id}/export/csv?date_format=bcad').text
        header, rows = _block(text, 'Phase')
        assert header[:11] == [
            'Row Type', 'Phase', 'Dates', 'Pooled Age BP', 'Pooled Error', 'T', 'df',
            '5% Critical', 'One Event', 'Pooled 95.4% HPD', 'Note']
        assert header[-1].startswith('Pooled 95.4% HPD (')
        [row] = rows
        assert row[:9] == ['Same-Event Test', '', '8', '656', '8', '5.73', '7', '14.07', 'yes']
        assert row[9] and row[11]          # pooled range, in cal BP and in AD

    def test_pdf_has_a_same_event_section(self, client):
        text = pdf_text(client.get(f'/api/results/{_group(client)}/pdf').content)
        assert 'Same-Event Tests' in text
        assert 'Consistent with one event' in text

    def test_single_calibration_exports_no_block(self, client):
        created = client.post('/api/analysis/calibrate', json={
            **_measurement('Solo', 600, 20), 'save': True})
        analysis_id = created.json()['data']['id']
        assert 'Same-Event Test' not in client.get(
            f'/api/results/{analysis_id}/export/csv').text
