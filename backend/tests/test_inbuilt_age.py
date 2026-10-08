"""
Inbuilt age (feature spec M1): a date's allowance for its material being
older than its event, integrated out of its likelihood
(exact_chain.inbuilt_kernel, event_likelihood), and carried through the
request, the stored date, a re-run, the collection, the importer and the
same-event test.

The acceptance check is the spec's: The dune reference model of DS/C:Dune
Layers 11-4 must match the sampler-free reference (corpus_analogues.py, model
C0) to within 0.10 in P(Start 11 before AD 1360), with the published maxima
applied and without them.
"""
import io
import itertools

import numpy as np
import pytest

from app.services import exact_chain as ec
from app.services.statistics import same_event

_IDS = itertools.count()
AD_1360 = 590.0


class TestKernel:
    GRID = np.arange(400.0, 900.0, 1.0)
    DATE = {'age_bp': 614, 'error_1sigma': 20, 'curve_id': 'shcal20'}

    def _mean(self, likelihood):
        return float((self.GRID * likelihood).sum() / likelihood.sum())

    def test_an_allowance_makes_the_event_younger(self):
        """The material is older than the event, so the event moves to a
        smaller cal BP -- by half the maximum for a uniform allowance."""
        plain = ec.event_likelihood(self.GRID, self.DATE)
        uniform = ec.event_likelihood(self.GRID, {**self.DATE, 'inbuilt_years': 30})
        assert self._mean(plain) - self._mean(uniform) == pytest.approx(15.0, abs=0.2)

    def test_an_exponential_allowance_moves_it_by_its_mean(self):
        plain = ec.event_likelihood(self.GRID, self.DATE)
        exponential = ec.event_likelihood(
            self.GRID, {**self.DATE, 'inbuilt_years': 15, 'inbuilt_shape': 'exponential'})
        assert self._mean(plain) - self._mean(exponential) == pytest.approx(15.0, abs=0.2)

    def test_no_allowance_is_the_calibration_itself(self):
        plain = ec.event_likelihood(self.GRID, self.DATE)
        zero = ec.event_likelihood(self.GRID, {**self.DATE, 'inbuilt_years': 0})
        assert np.array_equal(plain, zero)

    @pytest.mark.parametrize('step, years', [(1.0, 30), (5.0, 30), (5.0, 12), (5.0, 2), (1.667, 25)])
    def test_the_kernel_holds_the_allowance_whatever_the_grid_step(self, step, years):
        """Each lag cell takes the allowance's probability over that cell, so
        the mean is right even off a whole number of steps."""
        kernel = ec.inbuilt_kernel(step, {'inbuilt_years': years})
        centre = (len(kernel) - 1) // 2
        weights = kernel[:centre + 1][::-1]          # lag 0, 1 step, 2 steps, ...
        lags = step * np.arange(len(weights))
        assert weights.sum() == pytest.approx(1.0)
        assert (weights * lags).sum() == pytest.approx(years / 2.0, abs=step / 4.0)
        assert not kernel[centre + 1:].any()         # no mass on the older side

    def test_an_unknown_shape_is_refused(self):
        with pytest.raises(ValueError, match='inbuilt-age shape'):
            ec.inbuilt_kernel(1.0, {'inbuilt_years': 10, 'inbuilt_shape': 'gamma'})


# --- The dune reference model of Layers 11-4 --------------------------------

# (layer, lab code, CRA, error, published maximum inbuilt age)
PUBLISHED = [
    ('11', 'Le-10002', 614, 20, 0), ('11', 'Le-10003', 627, 20, 0),
    ('9', 'Le-10008', 694, 16, 30), ('9', 'Le-10012', 709, 16, 0),
    ('7', 'Le-10007', 623, 16, 30), ('7', 'Le-10006', 645, 16, 30),
    ('6', 'Le-10005', 546, 15, 25), ('6', 'Le-10011', 565, 16, 30),
    ('5', 'Le-10013', 597, 18, 0), ('5', 'Le-10009', 590, 15, 0),
    ('5', 'Le-10004', 620, 15, 25), ('4', 'Le-10010', 543, 20, 5),
]
# Oldest first; 10, 8 and 6L are sterile sand with no dates -- a gap between
# two Boundaries, as the reference has an empty interval.
LAYERS = ['11', '10', '9', '8', '7', '6L', '6', '5', '4']


def _dune_layers(client, variant, apply_inbuilt):
    tag = f'pk{next(_IDS)}'
    children = [{'id': f'{tag}_b0', 'element_type': 'boundary', 'name': 'Start 11', 'children': []}]
    for i, layer in enumerate(LAYERS):
        if layer not in ('10', '8', '6L'):
            children.append({'id': f'{tag}_L{layer}', 'element_type': 'phase',
                             'name': f'Layer {layer}', 'children': []})
        children.append({'id': f'{tag}_b{i + 1}', 'element_type': 'boundary',
                         'name': f'After {layer}', 'children': []})
    measurements = []
    for layer, code, cra, sigma, inbuilt in PUBLISHED:
        if code == 'Le-10012':
            if variant == 'NOE':
                continue
            if variant == 'E11':
                layer = '11'
        measurements.append({
            'id': f'{tag}_{code}', 'sample_name': code, 'age_bp': cra, 'error_1sigma': sigma,
            'curve_id': 'shcal20', 'material_type': 'charcoal' if inbuilt else 'eggshell',
            'element_id': f'{tag}_L{layer}', 'outlier_prior': 0.05, 'outlier_model': 'general',
            'inbuilt_years': inbuilt or None,
        })
    created = client.post('/api/analysis/bayesian?wait=true', json={
        'name': f'Dune layers {variant} {apply_inbuilt}', 'measurements': measurements,
        'model_tree': {'id': f'{tag}_root', 'element_type': 'sequence', 'name': 'DS/C:Dune',
                       'children': children},
        'bayesian_config': {'apply_inbuilt': apply_inbuilt},
        'save': True,
    })
    assert created.status_code == 200, created.text
    analysis_id = created.json()['data']['id']
    p = client.get(f'/api/results/{analysis_id}/p-before?cal_bp={AD_1360}').json()['data']
    return p['elements'][f'{tag}_b0'], analysis_id


class TestAgainstTheReference:
    # corpus_analogues_all.csv, model C0: P(Start 11 before AD 1360).
    @pytest.mark.parametrize('variant, off, on', [
        ('NOE', 0.968, 0.144),
        ('E11', 0.953, 0.363),
        ('orig', 0.998, 0.881),
    ])
    def test_matches_c0_with_and_without_the_published_maxima(self, client, variant, off, on):
        without, _ = _dune_layers(client, variant, apply_inbuilt=False)
        with_allowance, _ = _dune_layers(client, variant, apply_inbuilt=True)
        assert without == pytest.approx(off, abs=0.10)
        assert with_allowance == pytest.approx(on, abs=0.10)


# --- carried through the app ------------------------------------------------

def _measurement(name, age, **extra):
    return {'sample_name': name, 'age_bp': age, 'error_1sigma': 20, 'curve_id': 'shcal20',
            'material_type': 'charcoal', 'sample_type': 'terrestrial', **extra}


class TestCarriedThrough:
    def test_stored_with_the_date_and_kept_by_a_rerun(self, client):
        _p, analysis_id = _dune_layers(client, 'NOE', apply_inbuilt=True)
        dates = client.get(f'/api/analysis/{analysis_id}').json()['data']['measurements']
        assert {d['sample_name']: d['inbuilt_years'] for d in dates}['Le-10008'] == 30
        before = client.get(f'/api/results/{analysis_id}/p-before?cal_bp=590').json()['data']
        assert client.post(f'/api/analysis/{analysis_id}/rerun?wait=true').status_code == 200
        after = client.get(f'/api/results/{analysis_id}/p-before?cal_bp=590').json()['data']
        assert sorted(after['elements'].values()) == pytest.approx(sorted(before['elements'].values()))

    def test_the_collection_carries_it_to_a_deployed_analysis(self, client):
        project_id = client.post('/api/projects', json={'name': 'Inbuilt'}).json()['data']['id']
        added = client.post(f'/api/projects/{project_id}/samples', json={'samples': [
            _measurement('Heartwood', 700, inbuilt_years=40, inbuilt_shape='exponential')]})
        assert added.status_code == 200, added.text
        [item] = client.get(f'/api/projects/{project_id}/samples').json()['data']['items']
        assert (item['inbuilt_years'], item['inbuilt_shape']) == (40, 'exponential')

    def test_the_importer_reads_it(self, client):
        content = (b'sample_name,age_bp,error_1sigma,curve_id,inbuilt_max,inbuilt_shape\n'
                   b'Le-10008,694,16,shcal20,30,uniform\n')
        response = client.post('/api/import/dates',
                               files={'file': ('d.csv', io.BytesIO(content), 'text/plain')})
        assert response.status_code == 200, response.text
        [date] = response.json()['data']['dates']
        assert (date['inbuilt_years'], date['inbuilt_shape']) == (30, 'uniform')

    def test_a_wiggle_match_refuses_it(self, client):
        tag = f'wm{next(_IDS)}'
        response = client.post('/api/analysis/bayesian', json={
            'name': 'Wiggle', 'save': False,
            'measurements': [
                {**_measurement(f'R{i}', 700 - 10 * i, ring_offset=10 * i,
                                inbuilt_years=20 if i == 0 else None),
                 'id': f'{tag}_{i}', 'element_id': f'{tag}_w'}
                for i in range(3)],
            'model_tree': {'id': f'{tag}_w', 'element_type': 'wiggle_sequence',
                           'name': 'Timber', 'children': []},
        })
        assert response.status_code in (400, 422)
        assert 'inbuilt' in response.text

    def test_dates_with_different_allowances_are_not_pooled(self):
        dates = [{'age_bp': 650, 'error_1sigma': 20, 'curve_id': 'shcal20', 'inbuilt_years': 30},
                 {'age_bp': 660, 'error_1sigma': 20, 'curve_id': 'shcal20'}]
        assert 'inbuilt' in same_event(dates)['refused']
        shared = [{**d, 'inbuilt_years': 30} for d in dates]
        assert same_event(shared)['refused'] is None

    def test_with_and_without_duplicates_name_the_switch(self, client):
        _p, analysis_id = _dune_layers(client, 'NOE', apply_inbuilt=True)
        response = client.post(f'/api/analysis/{analysis_id}/duplicate?apply_inbuilt=false')
        assert response.status_code == 200, response.text
        copy = client.get(f"/api/analysis/{response.json()['data']['id']}").json()['data']
        assert copy['bayesian_config']['apply_inbuilt'] is False
        assert copy['name'].endswith('(inbuilt age off)')
