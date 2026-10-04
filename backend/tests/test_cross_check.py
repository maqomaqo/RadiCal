"""
The direct calculation a sampled model is checked against (services/cross_check.py).

It is only worth anything if it is the SAME model as the sampler's, so it is
held to the exact solver on a chain, to the sampler on parallel branches, and
shown to flag a sampled answer that is in the wrong place.
"""
import copy
import itertools

import numpy as np
import pytest

from app.services import cross_check
from app.services import exact_chain as ec
from app.services import gibbs

_IDS = itertools.count()


def _element(kind, name, children=None):
    return {'id': f'x{next(_IDS)}', 'element_type': kind, 'name': name,
            'tpq_age_bp': None, 'taq_age_bp': None, 'children': children or []}


def _date(element_id, age_bp, **extra):
    tag = f'xd{next(_IDS)}'
    date = {'id': tag, 'sample_name': tag, 'age_bp': age_bp, 'error_1sigma': 25,
            'curve_id': 'shcal20', 'element_id': element_id}
    date.update(extra)
    return date


def _nested_chain():
    """Two Sequences in a Sequence: one line of Boundaries, written nested."""
    a, b = _element('phase', 'A'), _element('phase', 'B')
    tree = _element('sequence', 'A then B', [
        _element('sequence', 'First', [_element('boundary', 'sA'), a,
                                       _element('boundary', 'eA')]),
        _element('sequence', 'Second', [_element('boundary', 'sB'), b,
                                        _element('boundary', 'eB')]),
    ])
    dates = [_date(a['id'], 900), _date(a['id'], 930),
             _date(b['id'], 700), _date(b['id'], 720)]
    return dates, tree


def _parallel(outliers=False):
    """A site's Start and End around two areas' Sequences."""
    extra = {'outlier_model': 'general', 'outlier_prior': 0.05} if outliers else {}
    a1, a2, b1 = _element('phase', 'A1'), _element('phase', 'A2'), _element('phase', 'B1')
    tree = _element('sequence', 'Site', [
        _element('boundary', 'Site start'),
        _element('phase', 'Activity', [
            _element('sequence', 'Area A', [
                _element('boundary', 'sA'), a1, _element('boundary', 'mA'),
                a2, _element('boundary', 'eA')]),
            _element('sequence', 'Area B', [
                _element('boundary', 'sB'), b1, _element('boundary', 'eB')]),
        ]),
        _element('boundary', 'Site end'),
    ])
    dates = [_date(a1['id'], 660, **extra), _date(a1['id'], 680, **extra),
             _date(a2['id'], 600, **extra), _date(a2['id'], 615, **extra),
             _date(b1['id'], 640, **extra), _date(b1['id'], 650, **extra)]
    return dates, tree


class TestShape:
    def test_a_nested_chain_is_one_chain(self):
        dates, tree = _nested_chain()
        assert ec.layout(tree, dates) is None
        outer, branches = cross_check.shape(tree, dates)
        assert outer is None and len(branches) == 1
        assert len(branches[0].boundary_ids) == 4

    def test_parallel_areas_are_branches_inside_an_outer_pair(self):
        dates, tree = _parallel()
        outer, branches = cross_check.shape(tree, dates)
        assert outer[0] == tree['children'][0]['id']
        assert outer[1] == tree['children'][2]['id']
        assert outer[2] == []
        assert [len(b.boundary_ids) for b in branches] == [3, 2]

    def test_anything_else_is_not_checked(self):
        dates, tree = _parallel()
        tree['children'][1]['children'].append(_element('phase', 'Loose phase'))
        with pytest.raises(cross_check.NotChecked):
            cross_check.shape(tree, dates)
        with pytest.raises(cross_check.NotChecked):
            cross_check.shape(_element('phase', 'Just a phase'), dates)


class TestTheSameModel:
    def test_it_is_the_exact_solver_on_a_chain(self):
        """The nested chain, spelled out, is something exact_chain solves."""
        dates, tree = _nested_chain()
        flat = copy.deepcopy(tree)
        flat['children'] = [c for s in tree['children'] for c in s['children']]
        exact = ec.solve(dates, flat, ec.layout(flat, dates), interval_draws=200)
        direct = cross_check.reference(dates, tree)
        for boundary_id, density in direct['boundaries'].items():
            mean = float((direct['grid'] * density).sum())
            assert mean == pytest.approx(
                exact['boundaries'][boundary_id]['posterior']['posterior_mean'], abs=1.0)

    @pytest.mark.parametrize('outliers', [False, True])
    def test_it_is_the_sampler_on_parallel_branches(self, outliers):
        """
        With the outlier model on, the sampler shares one offset scale across
        every date, which the direct calculation can only match by integrating
        over that scale -- the case that would drift if it were done wrong.
        """
        dates, tree = _parallel(outliers)
        # Four chains: one chain of this length puts the median Boundary 0.5
        # to 3.4 years from the direct answer, either side, by noise alone.
        sampled = gibbs.solve(dates, tree, sweeps=20000, max_sweeps=20000,
                              num_samples=200, chains=4)
        record = cross_check.check(dates, tree, sampled)
        assert record['status'] == 'agrees', record
        assert record['checked'] == 7
        assert record['worst']['distance'] < 0.08
        # Typical rather than worst: an outer Boundary's mean rides on a long
        # tail and moves several years with Monte Carlo noise alone.
        rows = cross_check.compare(cross_check.reference(dates, tree), sampled['boundaries'])
        assert np.median([abs(r['sampled_mean'] - r['direct_mean']) for r in rows]) < 2.0


class TestItFlagsAWrongAnswer:
    def test_a_boundary_in_the_wrong_place_is_reported(self):
        dates, tree = _parallel()
        sampled = gibbs.solve(dates, tree, sweeps=8000, max_sweeps=8000,
                              num_samples=200, chains=1)
        moved = copy.deepcopy(sampled['boundaries'])
        site_start = tree['children'][0]['id']
        points = moved[site_start]['posterior']['probability_distribution']
        moved[site_start]['posterior']['probability_distribution'] = [
            [x - 60.0, p] for x, p in points]
        record = cross_check.check(dates, tree, {'boundaries': moved})
        assert record['status'] == 'differs'
        assert [row['id'] for row in record['differing']] == [site_start]

    def test_a_model_it_cannot_integrate_says_why(self):
        dates, tree = _parallel()
        tree['children'][1]['children'].append(_element('phase', 'Loose phase'))
        record = cross_check.check(dates, tree, {'boundaries': {}})
        assert record['status'] == 'not_checked'
        assert 'Loose phase' in record['reason']
