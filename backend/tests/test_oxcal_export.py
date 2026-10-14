"""
Tests for the OxCal export (services/oxcal_export.py and
GET /api/results/{id}/export/oxcal).

The export is held to the importer: each file written here is read back by
services/oxcal_script.parse_oxcal and must give the same dates, curves,
offsets, outlier settings and structure. What OxCal's language there cannot
carry must be listed, never approximated.
"""
import itertools

import pytest

from app.services.oxcal_export import write_oxcal
from app.services.oxcal_script import parse_oxcal

_IDS = itertools.count()
OWNER = 'nobody-in-particular'


def _date(name, age, element_id=None, **extra):
    return {'sample_name': name, 'age_bp': age, 'error_1sigma': 25,
            'curve_id': 'shcal20', 'delta_r': None, 'delta_r_error': None,
            'outlier_prior': None, 'outlier_model': None, 'depth_cm': None,
            'ring_offset': None, 'ring_offset_error': None, 'tpq_age_bp': None,
            'taq_age_bp': None, 'inbuilt_years': None, 'inbuilt_shape': None,
            'element_id': element_id, **extra}


def _el(eid, etype, name, children=(), **extra):
    return {'id': eid, 'element_type': etype, 'name': name, 'tpq_age_bp': None,
            'taq_age_bp': None, 'children': list(children), **extra}


def _read(text, owner=OWNER):
    result = parse_oxcal(text, owner)
    assert not result.errors, (result.errors, text)
    return result


def _names(element):
    return [element.get('name'), [_names(c) for c in element.get('children') or []]]


def _phase_of(result, name):
    by_id = {}

    def walk(e):
        by_id[e['id']] = e.get('name')
        for c in e.get('children') or []:
            walk(c)
    walk(result.model_tree)
    date = next(d for d in result.dates if d['sample_name'] == name)
    return by_id[date['element_id']]


class TestRoundTrip:
    def test_a_layered_model_comes_back_as_it_went(self):
        tree = _el('root', 'sequence', 'Hillside', [
            _el('b0', 'boundary', 'Start'),
            _el('p3', 'phase', 'Layer 3'),
            _el('b1', 'boundary', 'Transition'),
            _el('p2', 'phase', 'Layer 2'),
            _el('b2', 'boundary', 'End'),
        ])
        dates = [
            _date('LA10003', 702, 'p3', outlier_prior=0.05, outlier_model='general'),
            _date('LB-10002', 970, 'p3', curve_id='marine20', delta_r=-7, delta_r_error=45),
            _date('LA10001', 614, 'p2', outlier_prior=1.0, outlier_model='charcoal'),
            _date('LA10002', 640, 'p2'),
        ]
        text, omitted = write_oxcal('Hillside', 'bayesian', dates, tree, {'resolution': 10})
        assert omitted == []
        result = _read(text)
        assert result.resolution == 10
        assert _names(result.model_tree) == _names(tree)
        back = {d['sample_name']: d for d in result.dates}
        for d in dates:
            got = back[d['sample_name']]
            assert (got['age_bp'], got['error_1sigma'], got['curve_id']) == (
                d['age_bp'], d['error_1sigma'], d['curve_id'])
            assert (got['delta_r'] or 0) == (d['delta_r'] or 0)
            assert (got['delta_r_error'] or 0) == (d['delta_r_error'] or 0)
            assert got['outlier_prior'] == (d['outlier_prior'] or 0)
            if d['outlier_prior']:
                assert got['outlier_model'] == d['outlier_model']
        assert _phase_of(result, 'LB-10002') == 'Layer 3'
        assert _phase_of(result, 'LA10002') == 'Layer 2'
        assert not result.warnings

    def test_a_group_calibration_is_its_dates(self):
        dates = [_date('G1', 656), _date('G2', 660, curve_id='intcal20')]
        result = _read(write_oxcal('Group', 'group_calibration', dates, None, {})[0])
        assert result.model_tree is None
        assert [(d['sample_name'], d['curve_id']) for d in result.dates] == [
            ('G1', 'shcal20'), ('G2', 'intcal20')]

    def test_a_wiggle_match_keeps_its_gaps(self):
        tree = _el('w', 'wiggle_sequence', 'Timber 1', params={'gap_units': 'rings'})
        dates = [_date(f'R{n}', 2500 - 10 * n, 'w', ring_offset=float(offset))
                 for n, offset in enumerate((100, 60, 20, 0))]
        result = _read(write_oxcal('Timber', 'bayesian', dates, tree, {})[0])
        assert result.model_tree['element_type'] == 'wiggle_sequence'
        assert {d['sample_name']: d['ring_offset'] for d in result.dates} == {
            'R0': 100, 'R1': 60, 'R2': 20, 'R3': 0}

    def test_a_measured_series_keeps_each_members_error(self):
        tree = _el('w', 'wiggle_sequence', 'Varves')
        dates = [_date(f'V{n}', 3000 - 20 * n, 'w', ring_offset=float(offset),
                       ring_offset_error=err)
                 for n, (offset, err) in enumerate(((200, 5.0), (100, 3.0), (0, None)))]
        text, omitted = write_oxcal('Varves', 'bayesian', dates, tree, {})
        assert 'V_Sequence' in text and omitted == []
        back = {d['sample_name']: d for d in parse_oxcal(text, OWNER).dates}
        assert back['V0']['ring_offset_error'] == pytest.approx(5.0)
        assert back['V1']['ring_offset_error'] == pytest.approx(3.0)
        assert back['V2']['ring_offset_error'] is None

    def test_an_age_depth_model_keeps_depths_levels_and_rate(self):
        tree = _el('d', 'deposition_sequence', 'Core', [
            _el('lv', 'interpolated_depth', '47 cm', params={'depth_cm': 47.0}),
        ], params={'k': 0.5, 'k_variable': False, 'log_k_min': -2, 'log_k_max': 2,
                   'depth_units': 'cm'})
        dates = [_date(f'{z} cm', 3000 - z * 10, 'd', depth_cm=float(z)) for z in (20, 60, 100)]
        result = _read(write_oxcal('Core', 'bayesian', dates, tree, {})[0])
        element = result.model_tree
        assert element['element_type'] == 'deposition_sequence'
        assert element['params']['k'] == pytest.approx(0.5)
        assert [c['params']['depth_cm'] for c in element['children']] == [47.0]
        assert sorted(d['depth_cm'] for d in result.dates) == [20, 60, 100]

    def test_a_variable_rate_comes_back_variable(self):
        tree = _el('d', 'deposition_sequence', 'Core', params={
            'k': None, 'k_variable': True, 'log_k_min': -1.5, 'log_k_max': 1.0})
        dates = [_date(f'{z} cm', 3000 - z * 10, 'd', depth_cm=float(z)) for z in (20, 60)]
        params = _read(write_oxcal('Core', 'bayesian', dates, tree, {})[0]).model_tree['params']
        assert params['k_variable'] is True
        assert (params['log_k_min'], params['log_k_max']) == pytest.approx((-1.5, 1.0))

    def test_a_hiatus_is_a_comment_and_the_rest_still_reads(self):
        tree = _el('root', 'sequence', 'S', [
            _el('b0', 'boundary', 'End of A'), _el('h', 'hiatus', 'Abandonment'),
            _el('b1', 'boundary', 'Start of B'), _el('p', 'phase', 'B'),
            _el('b2', 'boundary', 'End')])
        text, _ = write_oxcal('S', 'bayesian', [_date('X', 600, 'p')], tree, {})
        assert '/* Hiatus Abandonment' in text
        result = _read(text)
        assert [c['name'] for c in result.model_tree['children']] == [
            'End of A', 'Start of B', 'B', 'End']


class TestWhatIsNotWritten:
    def test_limits_and_inbuilt_age_are_listed_not_approximated(self):
        tree = _el('root', 'sequence', 'S', [
            _el('b0', 'boundary', 'Start'),
            {**_el('p', 'phase', 'Layer'), 'tpq_age_bp': 900, 'taq_age_bp': 400},
            _el('b1', 'boundary', 'End')])
        dates = [_date('X', 600, 'p', tpq_age_bp=800, inbuilt_years=50,
                       inbuilt_shape='exponential')]
        text, omitted = write_oxcal('S', 'bayesian', dates, tree, {})
        assert omitted == ['Phase Layer: TPQ 900 cal BP, TAQ 400 cal BP',
                           'Date X: TPQ 800 cal BP',
                           'Date X: inbuilt age up to 50 years (exponential)']
        assert "Not written, because OxCal's language here cannot say it" in text
        assert 'After' not in text and 'C_Date' not in text
        _read(text)

    def test_a_date_outside_the_model_is_listed(self):
        tree = _el('root', 'phase', 'P')
        text, omitted = write_oxcal('P', 'bayesian',
                                    [_date('In', 600, 'root'), _date('Out', 610)], tree, {})
        assert omitted == ['1 date(s) not placed in the model: Out']

    def test_a_quote_in_a_name_does_not_break_the_file(self):
        tree = _el('root', 'phase', 'Pit "A"')
        result = _read(write_oxcal('P', 'bayesian', [_date('X "1"', 600, 'root')], tree, {})[0])
        assert result.dates[0]['sample_name'] == "X '1'"


class TestThroughTheApi:
    def test_the_endpoint_writes_the_analysis_and_counts_what_it_left_out(self, client):
        tag = f'ox{next(_IDS)}'
        tree = {'id': f'{tag}-root', 'element_type': 'sequence', 'name': 'Site',
                'tpq_age_bp': 900, 'taq_age_bp': 400, 'children': [
                    {'id': f'{tag}-b0', 'element_type': 'boundary', 'name': 'Start', 'children': []},
                    {'id': f'{tag}-p', 'element_type': 'phase', 'name': 'Layer', 'children': []},
                    {'id': f'{tag}-b1', 'element_type': 'boundary', 'name': 'End', 'children': []}]}
        made = client.post('/api/analysis/bayesian?wait=true', json={
            'name': 'Site / model', 'save': True, 'model_tree': tree,
            'bayesian_config': {'resolution': 10},
            'measurements': [
                {'id': f'{tag}-m{i}', 'sample_name': f'LA{i}', 'age_bp': age,
                 'error_1sigma': 25, 'curve_id': 'shcal20', 'element_id': f'{tag}-p'}
                for i, age in enumerate((614, 640, 655))]})
        assert made.status_code == 200, made.text
        response = client.get(f"/api/results/{made.json()['data']['id']}/export/oxcal")
        assert response.status_code == 200
        assert 'Site-model.oxcal' in response.headers['content-disposition']
        assert response.headers['x-radical-not-written'] == '1'
        result = _read(response.text)
        assert sorted(d['sample_name'] for d in result.dates) == ['LA0', 'LA1', 'LA2']
        assert result.resolution == 10

    def test_a_mixed_curve_is_declared_and_matched_back(self, client, suite_user,
                                                       remove_stored_curves):
        from tests.test_curve_import import store_curve
        parent = store_curve(client, f'oxparent{next(_IDS)}', f'Ox parent {next(_IDS)}')
        assert parent.status_code == 201, parent.text
        mixed = client.post('/api/curves/mixed', json={
            'a': parent.json()['data']['id'], 'b': 'intcal20', 'percent_a': 30,
            'percent_a_error': 5, 'name': f'Ox mix {next(_IDS)}'})
        assert mixed.status_code == 201, mixed.text
        mix_id = mixed.json()['data']['id']
        text, _ = write_oxcal('M', 'group_calibration',
                              [_date('Bone', 500, curve_id=mix_id, delta_r=10, delta_r_error=5)],
                              None, {})
        assert 'Mix_Curves(' in text
        result = _read(text, suite_user['user']['id'])
        assert result.dates[0]['curve_id'] == mix_id
        assert result.dates[0]['delta_r'] == 10
