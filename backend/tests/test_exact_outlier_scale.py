"""
The shared outlier scale in the exact solver (exact_chain._weigh_scales).

An outlier model's offset scale is one parameter for every date using it,
constrained by the data. The exact solver weighs it with cheap evidence passes
on a coarse grid and then mixes each flagged date's offset over that posterior,
instead of over the prior. Checked:

  * against OxCal: the dune fixture's outlier probabilities, the charcoal
    fixture's dates, span and scale;
  * against the exact answer, a mixture of one whole solve per scale, on a
    model small enough to afford it;
  * that the coarse weighing matches a finer one, and that two outlier models
    weighed in turn match their joint weighing;
  * that a model with no outlier prior is untouched, and that the weights
    reproduce the prior mixture when uniform.
"""
import itertools

import numpy as np
import pytest

import dune_section_fixture as dune
import hillside_charcoal_fixture as fx
from app.services import exact_chain as ec
from app.services import exact_parallel as ep
from app.services.model_tree import inherited_bounds
from tests.test_exact_parallel import TWO_AREAS, _run, _site

OPTIONS = dict(num_samples=10, seed=1, uniform_span_prior=True, estimate_durations=False,
               dispersal=None, interval_draws=10, resolution=5.0)


def _chain_model(groups, outlier_prior=0.05, wild=None):
    """A chain of Phases between Boundaries, every date flagged General;
    `wild` (group, index, shift) moves one date's radiocarbon age."""
    children = [{'id': 'os_b0', 'element_type': 'boundary', 'name': 'Start', 'children': []}]
    dates = []
    for g, ages in enumerate(groups):
        children += [{'id': f'os_p{g}', 'element_type': 'phase', 'name': f'P{g}',
                      'children': []},
                     {'id': f'os_b{g + 1}', 'element_type': 'boundary', 'name': f'B{g + 1}',
                      'children': []}]
        for j, age in enumerate(ages):
            if wild is not None and (g, j) == wild[:2]:
                age += wild[2]
            dates.append({'id': f'os_d{g}_{j}', 'sample_name': f'os_d{g}_{j}',
                          'age_bp': float(age), 'error_1sigma': 25.0,
                          'curve_id': 'intcal20', 'element_id': f'os_p{g}',
                          'outlier_prior': outlier_prior})
    return {'id': 'os_root', 'element_type': 'sequence', 'name': 'Site',
            'children': children}, dates


def _solve(tree, dates):
    return ec.solve(dates, tree, ec.layout(tree, dates), num_samples=200)


class TestAgainstOxCal:
    def test_the_dune_outlier_probabilities(self):
        """0.0016 from OxCal's at most, where mixing each date's offset over
        the scale's prior gave 0.0158 (and the sampler 0.0016)."""
        dates, tree = dune.measurements(), dune.model_tree()
        solved = _solve(tree, dates)
        table = dune.table()
        worst = max(abs(solved['measurements'][d['id']]['outlier_probability']
                        - table[d['id']]['outlier_probability']) for d in dates)
        assert worst < 0.003, worst

    def test_the_charcoal_scale(self):
        """OxCal's exported U(0,3) posterior for the Charcoal model: mean 1.79,
        68% 1.49-2.06, which the sampler matches; weighed here, 1.82 and
        1.46-2.05."""
        dates, tree = fx.measurements(), fx.model_tree()
        solved = _solve(tree, dates)
        scale = solved['outlier_scale']['charcoal']
        assert scale['log10_mean'] == pytest.approx(1.79, abs=0.15), scale
        low, high = scale['log10_hpd_68']
        assert low == pytest.approx(1.49, abs=0.2) and high == pytest.approx(2.06, abs=0.2)
        drawn = solved['joint_scales']['charcoal']
        assert len(drawn) == len(next(iter(solved['joint_draws'].values())))
        assert set(np.unique(drawn)) <= set(ec.scale_nodes('charcoal'))


class TestAgainstTheExactMixture:
    def test_one_wild_date(self):
        """
        The exact answer mixes one whole solve per scale node by its evidence.
        On a model small enough for that, with one date 400 radiocarbon years
        off, the weighed scale gives the same outlier probabilities to 0.002
        and Boundaries to a year.
        """
        tree, dates = _chain_model([[3300, 3290, 3280], [3150, 3140, 3130]],
                                   wild=(1, 0, 400))
        chain = ec.layout(tree, dates)
        solved = ec.solve(dates, tree, chain, num_samples=200)
        g = solved['grid']
        grid = np.arange(g['lower'], g['upper'] + g['step'] / 2, g['step'])
        bounds = inherited_bounds(tree)
        runs, log_evidence = [], []
        for node in range(ec.OUTLIER_SCALE_NODES):
            alone = np.zeros(ec.OUTLIER_SCALE_NODES)
            alone[node] = 1.0
            run, _d = ec._solve_on_grid(grid, dates, tree, chain, bounds,
                                        scale_weights={'general': alone}, **OPTIONS)
            runs.append(run)
            log_evidence.append(run['log_evidence'])
        weights = np.exp(np.array(log_evidence) - max(log_evidence))
        weights /= weights.sum()
        for d in dates:
            exact = sum(w * r['measurements'][d['id']]['outlier_probability']
                        for w, r in zip(weights, runs))
            assert solved['measurements'][d['id']]['outlier_probability'] == pytest.approx(
                exact, abs=0.002), d['id']
        for bid in chain.boundary_ids:
            exact = sum(w * r['boundaries'][bid]['posterior']['posterior_mean']
                        for w, r in zip(weights, runs))
            assert solved['boundaries'][bid]['posterior']['posterior_mean'] == pytest.approx(
                exact, abs=1.0), bid


class TestTheWeighing:
    def test_a_finer_weighing_changes_nothing_that_matters(self, monkeypatch):
        """Every node on 600 points against every second on 200."""
        tree, dates = _chain_model([[3300, 3290, 3280], [3150, 3140, 3130],
                                    [3000, 2990, 2985]], wild=(1, 0, 400))
        coarse = _solve(tree, dates)
        monkeypatch.setattr(ec, 'SCALE_WEIGHING_POINTS', 600)
        monkeypatch.setattr(ec, 'SCALE_WEIGHING_STRIDE', 1)
        fine = _solve(tree, dates)
        for d in dates:
            a, b = coarse['measurements'][d['id']], fine['measurements'][d['id']]
            assert a['posterior_mean'] == pytest.approx(b['posterior_mean'], abs=0.5)
            assert a['outlier_probability'] == pytest.approx(b['outlier_probability'],
                                                             abs=0.002)

    def test_two_models_weighed_in_turn_match_their_joint_weighing(self, monkeypatch):
        """The charcoal fixture's General and Charcoal scales, weighed one at a
        time, against all 24 x 24 pairs on the same grid -- every node solved,
        so this is the weighing in turn alone, not the interpolation."""
        monkeypatch.setattr(ec, 'SCALE_WEIGHING_STRIDE', 1)
        dates, tree = fx.measurements(), fx.model_tree()
        chain = ec.layout(tree, dates)
        bounds = inherited_bounds(tree)
        solved = _solve(tree, dates)
        lower, upper = solved['grid']['lower'], solved['grid']['upper']

        def evidence(grid, weights):
            return ec._solve_on_grid(grid, dates, tree, chain, bounds, scale_weights=weights,
                                     evidence_only=True, **OPTIONS)

        turns = ec._weigh_scales(evidence, ['charcoal', 'general'], lower, upper)
        grid = np.linspace(lower, upper, ec.SCALE_WEIGHING_POINTS)
        n = ec.OUTLIER_SCALE_NODES
        joint = np.empty((n, n))
        for i, j in itertools.product(range(n), range(n)):
            joint[i, j] = evidence(grid, {'charcoal': np.eye(n)[i], 'general': np.eye(n)[j]})
        joint = np.exp(joint - joint.max())
        joint /= joint.sum()
        assert 0.5 * np.abs(turns['charcoal'] - joint.sum(axis=1)).sum() < 0.02
        assert 0.5 * np.abs(turns['general'] - joint.sum(axis=0)).sum() < 0.02

    def test_uniform_weights_are_the_prior_mixture(self):
        dates = dune.measurements()
        grid = np.arange(300.0, 1100.0, 2.0)
        bounds = inherited_bounds(dune.model_tree())
        prior, _c, _p = ec.date_tables(grid, dates, bounds)
        uniform, _c2, _p2 = ec.date_tables(
            grid, dates, bounds,
            {'general': np.full(ec.OUTLIER_SCALE_NODES, 1.0 / ec.OUTLIER_SCALE_NODES)})
        for d in dates:
            np.testing.assert_allclose(uniform[d['id']], prior[d['id']], rtol=1e-10,
                                       atol=1e-15)

    def test_a_model_with_no_outlier_prior_is_not_weighed(self, monkeypatch):
        tree, dates = _chain_model([[3300, 3290], [3150, 3140]], outlier_prior=0.0)
        monkeypatch.setattr(ec, '_weigh_scales', lambda *a, **k: pytest.fail('weighed'))
        solved = _solve(tree, dates)
        assert 'outlier_scale' not in solved and 'joint_scales' not in solved


class TestEveryShape:
    def test_side_by_side_areas_weigh_it_too(self):
        tree, dates = _site(TWO_AREAS, outlier_prior=0.05)
        layout, reason = ep.layout_with_reason(tree, dates)
        assert reason is None
        solved = ep.solve(dates, tree, layout, num_samples=50, interval_draws=200)
        assert 'general' in solved['outlier_scale']
        for d in dates:
            assert 0.0 <= solved['measurements'][d['id']]['outlier_probability'] <= 1.0

    def test_the_analysis_records_it(self, client):
        tree, dates = _site(TWO_AREAS, outlier_prior=0.05)
        created = _run(client, tree, dates)
        assert created.status_code == 200, created.text
        solver = created.json()['data']['solver']
        assert solver['method'] == 'exact'
        assert 'general' in solver['outlier_scale']
