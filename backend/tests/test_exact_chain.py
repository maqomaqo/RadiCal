"""
The exact chain solver (services/exact_chain.py).

Three things are checked, in increasing order of what they'd catch:

  * `layout` recognises exactly the models the solver can express, and
    refuses the ones it cannot. Getting this wrong is the dangerous failure:
    a model silently solved under the wrong structure.
  * the solver reproduces an independent brute-force integration of the same
    maths, written out as an explicit double loop.
  * it reproduces a real OxCal 4.4 run's MODELLED ranges. This one is only
    possible because the solver takes about a second -- the MCMC path could
    never be asserted in the suite.

No pymc anywhere: the solver is pure numpy, so all of this runs regardless
of whether a sampler is installed.
"""
import numpy as np
import pytest

from app.services import exact_chain as ec
from app.services.calibration import get_curve

import oxcal_fixture as ox


def _phase(eid, name='Phase'):
    return {'id': eid, 'element_type': 'phase', 'name': name,
            'tpq_age_bp': None, 'taq_age_bp': None, 'children': []}


def _boundary(eid, name='Boundary'):
    return {'id': eid, 'element_type': 'boundary', 'name': name,
            'tpq_age_bp': None, 'taq_age_bp': None, 'children': []}


def _sequence(eid, children, name='Sequence'):
    return {'id': eid, 'element_type': 'sequence', 'name': name,
            'tpq_age_bp': None, 'taq_age_bp': None, 'children': children}


def _dates(*specs):
    return [{'id': i, 'sample_name': i, 'age_bp': a, 'error_1sigma': e,
             'curve_id': 'intcal20', 'element_id': p}
            for i, a, e, p in specs]


class TestLayout:
    """Which models the solver will claim."""

    def test_single_phase_between_boundaries(self):
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        chain = ec.layout(tree, _dates(('d1', 3000, 30, 'p'), ('d2', 3050, 30, 'p')))
        assert chain is not None
        assert chain.boundary_ids == ['b0', 'b1']
        assert chain.groups == [['d1', 'd2']]

    def test_several_phases_sharing_boundaries(self):
        tree = _sequence('s', [_boundary('b0'), _phase('p0'), _boundary('b1'),
                               _phase('p1'), _boundary('b2')])
        chain = ec.layout(tree, _dates(('d1', 3200, 30, 'p0'), ('d2', 3000, 30, 'p1')))
        assert chain.boundary_ids == ['b0', 'b1', 'b2']
        assert chain.groups == [['d1'], ['d2']]

    def test_nesting_that_adds_no_ordering_is_still_a_chain(self):
        """
        A Sequence wrapping a single Phase, and a Phase holding two parallel
        Phases, constrain nothing extra -- so the model is still a chain even
        though its tree is not flat. This is the Nenthorn shared_boundary
        shape, and rejecting it would send a solvable model to the sampler.
        """
        chain = ec.layout(ox.model_tree('shared_boundary'), ox.measurements())
        assert chain is not None
        assert chain.boundary_ids == ['b_start', 'b_mid', 'b_end']
        assert [sorted(g) for g in chain.groups] == [
            sorted(ox.FIRE_FEATURE_J), sorted(ox.ROCKSHELTER)]

    def test_groups_ordered_without_a_boundary_between_them_is_not_a_chain(self):
        """
        The Nenthorn original: two branches ordered against each other while
        sharing ONE enclosing boundary pair. Their dates are all in a single
        span group, and the extra ordering does not factorise along the
        chain, so the solver must decline it rather than quietly drop the
        constraint.
        """
        assert ec.layout(ox.model_tree('ordered'), ox.measurements()) is None

    def test_a_model_with_no_boundaries_is_not_a_chain(self):
        tree = _sequence('s', [_phase('p0'), _phase('p1')])
        assert ec.layout(tree, _dates(('d1', 3200, 30, 'p0'), ('d2', 3000, 30, 'p1'))) is None

    def test_parallel_branches_are_not_a_chain(self):
        """Two separately bracketed branches inside a Phase: two chains, not
        one, so there is no single line of boundaries to pass messages along."""
        left = _sequence('l', [_boundary('lb0'), _phase('lp'), _boundary('lb1')])
        right = _sequence('r', [_boundary('rb0'), _phase('rp'), _boundary('rb1')])
        tree = _sequence('s', [{'id': 'par', 'element_type': 'phase', 'name': 'Parallel',
                                'tpq_age_bp': None, 'taq_age_bp': None,
                                'children': [left, right]}])
        assert ec.layout(tree, _dates(('d1', 3200, 30, 'lp'), ('d2', 3000, 30, 'rp'))) is None

    def test_a_date_outside_every_bounded_group_is_not_a_chain(self):
        """An unbracketed phase alongside a bracketed one: the loose date has
        no uniform-scatter prior, which the chain has no way to express."""
        tree = _sequence('s', [_boundary('b0'), _phase('p0'), _boundary('b1'), _phase('p1')])
        assert ec.layout(tree, _dates(('d1', 3200, 30, 'p0'), ('d2', 3000, 30, 'p1'))) is None


def _brute_force(curve, grid, dates):
    """
    One bounded group's exact marginals, by explicit summation over every
    (younger, older) boundary pair. Deliberately the slowest, most obvious
    implementation of

        p(t_j) ∝ L_j(t_j) * sum over {tb <= t_j <= ta} of
                 (ta-tb)^(-K) * prod over i != j of F_i(tb, ta)

    so that agreement with the vectorised solver is evidence about the code
    rather than a restatement of it.
    """
    dt = grid[1] - grid[0]
    K = len(dates)
    L = []
    for d in dates:
        c14, err = curve.interpolate(grid)
        sigma2 = err ** 2 + d['error_1sigma'] ** 2
        lik = np.exp(-0.5 * (c14 - d['age_bp']) ** 2 / sigma2) / np.sqrt(sigma2)
        L.append(lik / (lik.sum() * dt))
    L = np.array(L)
    cum = np.cumsum(L, axis=1) * dt

    def F(j, b, a):
        return cum[j, a] - (cum[j, b - 1] if b > 0 else 0.0)

    M = len(grid)
    out = np.zeros((K, M))
    for b in range(M):
        for a in range(b + 1, M):
            base = (grid[a] - grid[b]) ** (-K)
            fs = [F(j, b, a) for j in range(K)]
            for j in range(K):
                weight = base
                for i in range(K):
                    if i != j:
                        weight *= fs[i]
                if weight > 0:
                    out[j, b:a + 1] += weight
    out = L * out
    return out / (out.sum(axis=1, keepdims=True) * dt)


class TestMatchesBruteForce:
    def test_single_group(self, monkeypatch):
        """
        A coarse grid on purpose: the brute force is O(M^2) in Python, and
        both sides use the same grid so the comparison is exact rather than
        approximate.
        """
        monkeypatch.setattr(ec, 'MAX_GRID_POINTS', 90)
        monkeypatch.setattr(ec, 'MIN_BOUNDARY_PAD_YEARS', 300.0)
        monkeypatch.setattr(ec, 'BOUNDARY_WINDOW_FACTOR', 1.5)

        dates = _dates(('e1', 3000, 30, 'p'), ('e2', 3040, 30, 'p'), ('e3', 3080, 30, 'p'))
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        chain = ec.layout(tree, dates)
        solved = ec.solve(dates, tree, chain, num_samples=200)

        grid = np.arange(solved['grid']['lower'],
                         solved['grid']['upper'] + solved['grid']['step'] / 2,
                         solved['grid']['step'])
        expected = _brute_force(get_curve('intcal20'), grid, dates)

        for j, d in enumerate(dates):
            got = np.array(solved['measurements'][d['id']]['probability_distribution'])[:, 1]
            assert len(got) == len(grid)
            # Overlapping coefficient; 1.0 is identical.
            overlap = float(np.minimum(got / got.sum(), expected[j] / expected[j].sum()).sum())
            assert overlap > 0.999, f'{d["id"]} overlap {overlap}'


# What the solver actually produces on the shared_boundary fixture, with
# OxCal's UniformSpanPrior on (the default) and off. Both are recorded
# because the correction is a measured trade-off rather than an improvement:
# it pulls the outermost boundaries in towards OxCal's, and moves the dates
# slightly away. For scale, pm.sample_smc gave 25.3 rms and a separation of
# 57 against OxCal's 13.2.
_SOLVED_CACHE = {}

SPAN_PRIOR_EXPECTED = {
    True:  {'rms': 6.5, 'max': 11.0, 'separation': (2.0, 12.0)},
    False: {'rms': 5.5, 'max': 7.0, 'separation': (13.0, 26.0)},
}


@pytest.mark.parametrize('span_prior', [True, False], ids=['span-prior', 'no-span-prior'])
class TestAgainstOxCalModelled:
    """
    The modelled half of the OxCal comparison, on the shared_boundary run.
    pm.sample_smc puts these dates 25 years rms from OxCal and separates the
    two groups by 57 years against OxCal's 13; the solver is what closed that.
    """

    @pytest.fixture
    def solved(self, span_prior):
        # Cached per setting: the span-prior solve is O(M^3) and every test
        # in the class wants the same result.
        if span_prior not in _SOLVED_CACHE:
            tree = ox.model_tree('shared_boundary')
            samples = ox.measurements()
            chain = ec.layout(tree, samples)
            assert chain is not None
            _SOLVED_CACHE[span_prior] = ec.solve(
                samples, tree, chain, num_samples=2000,
                uniform_span_prior=span_prior)
        return _SOLVED_CACHE[span_prior]

    def test_grid_covers_the_posterior(self, solved, span_prior):
        """A heavy boundary tail running off the end of the grid would bias
        everything, so the solver reports how much mass sits at the edges."""
        assert solved['edge_mass'] < 0.01

    def test_date_means_match_oxcal(self, solved, span_prior):
        dists = ox.distributions('shared_boundary')
        errors = []
        for m in ox.measurements():
            cal, prob = dists[m['id']]['posterior']
            oxcal_mean = float((cal * prob / prob.sum()).sum())
            errors.append(solved['measurements'][m['id']]['posterior_mean'] - oxcal_mean)
        errors = np.array(errors)
        expected = SPAN_PRIOR_EXPECTED[span_prior]
        assert np.abs(errors).max() < expected['max']
        assert np.sqrt((errors ** 2).mean()) < expected['rms']

    def test_group_separation_matches_oxcal(self, solved, span_prior):
        """
        The number SMC got wrong. OxCal separates the two groups' means by
        13 years; SMC said 57. The span prior undershoots it and leaving the
        prior off overshoots it, so OxCal sits between the two settings.
        """
        def mean_ad(ids):
            return np.mean([1950 - solved['measurements'][i]['posterior_mean'] for i in ids])
        separation = mean_ad(ox.ROCKSHELTER) - mean_ad(ox.QUARRY)
        low, high = SPAN_PRIOR_EXPECTED[span_prior]['separation']
        assert low < separation < high

    def test_no_spurious_lobe_split(self, solved, span_prior):
        """
        SMC satisfied the ordering by pushing Quarry into the early
        calibration lobe and Rockshelter into the late one -- 0.00 and 0.71
        of the mass around AD 1370-1420 against OxCal's 0.12 and 0.17. Both
        groups must keep a comparable, modest share.
        """
        lo, hi = 1950 - 1420, 1950 - 1370
        share = {}
        for group, ids in (('quarry', ox.QUARRY), ('rockshelter', ox.ROCKSHELTER)):
            masses = []
            for i in ids:
                dist = np.array(solved['measurements'][i]['probability_distribution'])
                inside = (dist[:, 0] >= lo) & (dist[:, 0] <= hi)
                masses.append(dist[inside, 1].sum() / dist[:, 1].sum())
            share[group] = float(np.mean(masses))
        assert 0.02 < share['quarry'] < 0.35
        assert 0.02 < share['rockshelter'] < 0.35
        assert abs(share['quarry'] - share['rockshelter']) < 0.20

    def test_boundary_ranges_match_oxcal(self, solved, span_prior):
        """The shared transition is the one boundary the dates pin down well."""
        table = ox.table_ranges('shared_boundary')
        got = solved['boundaries']['b_mid']['posterior']
        envelope = (min(r['min'] for r in got['hpd_95_ranges']),
                    max(r['max'] for r in got['hpd_95_ranges']))
        want_from, want_to = table['QUARRYTOROCKSHELTER']['modelled'][95][0]
        assert 1950 - envelope[1] == pytest.approx(want_from, abs=10.0)
        assert 1950 - envelope[0] == pytest.approx(want_to, abs=10.0)

    def test_output_shape_matches_the_sampler_path(self, solved, span_prior):
        """
        Callers must not have to know which solver ran, so every key
        BayesianChronology.extract_results produces has to be present.
        """
        expected = {'posterior_mean', 'posterior_median', 'posterior_std',
                    'hpd_68', 'hpd_95', 'hpd_68_ranges', 'hpd_95_ranges',
                    'probability_distribution', 'posterior_samples'}
        for entry in solved['measurements'].values():
            assert expected <= set(entry)
            assert len(entry['posterior_samples']) == 2000
            assert not any(k.startswith('_') for k in entry), 'internals leaked'
        for entry in solved['boundaries'].values():
            assert set(entry) == {'name', 'posterior'}
            assert expected <= set(entry['posterior'])

    def test_draws_agree_with_the_density_they_came_from(self, solved, span_prior):
        """The synthesised samples exist for downstream code; they must not
        tell a different story from the exact density beside them."""
        for entry in solved['measurements'].values():
            draws = np.asarray(entry['posterior_samples'])
            assert draws.mean() == pytest.approx(entry['posterior_mean'], abs=3.0)
            assert draws.std() == pytest.approx(entry['posterior_std'], rel=0.15)


class TestLimiters:
    def test_tpq_truncates_the_posterior(self):
        """A TPQ on the phase must be respected as a hard bound."""
        dates = _dates(('t1', 3000, 30, 'p'), ('t2', 3020, 30, 'p'))
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        tree['children'][1]['tpq_age_bp'] = 3100.0

        chain = ec.layout(tree, dates)
        solved = ec.solve(dates, tree, chain, num_samples=200)
        for d in dates:
            dist = np.array(solved['measurements'][d['id']]['probability_distribution'])
            beyond = dist[dist[:, 0] > 3100.0, 1].sum()
            assert beyond == pytest.approx(0.0, abs=1e-12)

    def test_contradictory_limiters_raise(self):
        dates = _dates(('c1', 3000, 30, 'p'))
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        tree['children'][1]['taq_age_bp'] = 20000.0
        tree['children'][1]['tpq_age_bp'] = 21000.0
        chain = ec.layout(tree, dates)
        with pytest.raises(ValueError):
            ec.solve(dates, tree, chain, num_samples=100)


class TestUniformSpanPrior:
    """
    OxCal's UniformSpanPrior, on by default here as there.

    It divides the prior by (b_first - b_last)^(n-2), which stops a
    sequence's span prior depending on how many sub-groups it is cut into.
    That couples the two ends of the chain, so it needs a different and more
    expensive computation than the plain forward/backward pass -- these check
    that the expensive one computes the same thing a brute-force sum does,
    and that the cheap one is still used when the correction cannot matter.
    """

    def _two_group_model(self):
        dates = _dates(('sp_a', 3300, 30, 'p0'), ('sp_b', 3270, 30, 'p0'),
                       ('sp_c', 3050, 30, 'p1'), ('sp_d', 3020, 30, 'p1'))
        tree = _sequence('s', [_boundary('b0'), _phase('p0'), _boundary('b1'),
                               _phase('p1'), _boundary('b2')])
        return dates, tree

    def test_no_effect_with_two_boundaries(self):
        """
        The exponent is n - 2, so a single Phase between two Boundaries --
        the app's default shape -- is untouched, and the cheap chain is kept.
        """
        dates = _dates(('sp1_a', 3000, 30, 'p'), ('sp1_b', 3040, 30, 'p'))
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        chain = ec.layout(tree, dates)

        on = ec.solve(dates, tree, chain, num_samples=100, uniform_span_prior=True)
        off = ec.solve(dates, tree, chain, num_samples=100, uniform_span_prior=False)

        assert on['uniform_span_prior'] is False, 'nothing to apply at n = 2'
        for d in dates:
            a = np.array(on['measurements'][d['id']]['probability_distribution'])
            b = np.array(off['measurements'][d['id']]['probability_distribution'])
            assert np.allclose(a, b)

    def test_applies_with_three_boundaries_and_can_be_switched_off(self):
        dates, tree = self._two_group_model()
        chain = ec.layout(tree, dates)

        on = ec.solve(dates, tree, chain, num_samples=100, uniform_span_prior=True)
        off = ec.solve(dates, tree, chain, num_samples=100, uniform_span_prior=False)

        assert on['uniform_span_prior'] is True
        assert off['uniform_span_prior'] is False
        # It pulls the outermost boundaries in -- that is the point of it.
        def width(res, bid):
            r = res['boundaries'][bid]['posterior']['hpd_95_ranges']
            return max(x['max'] for x in r) - min(x['min'] for x in r)
        assert width(on, 'b0') < width(off, 'b0')

    def test_matches_a_brute_force_sum_over_the_boundary_cube(self):
        """
        The correction turns the chain into a coupled sum over every
        (b0, b1, b2) triple. The solver does that with matrix products; this
        does it by explicit summation, on a grid coarse enough to afford the
        cube.
        """
        dates, tree = self._two_group_model()
        chain = ec.layout(tree, dates)

        # Small grid: the cube below is M^3.
        import unittest.mock as mock
        with mock.patch.object(ec, 'MAX_GRID_POINTS', 70), \
             mock.patch.object(ec, 'MIN_BOUNDARY_PAD_YEARS', 250.0), \
             mock.patch.object(ec, 'BOUNDARY_WINDOW_FACTOR', 1.0):
            solved = ec.solve(dates, tree, chain, num_samples=100,
                              uniform_span_prior=True)
            grid = np.arange(solved['grid']['lower'],
                             solved['grid']['upper'] + solved['grid']['step'] / 2,
                             solved['grid']['step'])
            element_bounds = {}
            from app.services.model_tree import inherited_bounds
            element_bounds = inherited_bounds(tree)
            dt = grid[1] - grid[0]
            lik, cum = {}, {}
            for d in dates:
                l = ec._date_likelihood(grid, d, ec._effective_date_bounds(d, element_bounds))
                l = l / (l.sum() * dt)
                lik[d['id']], cum[d['id']] = l, np.cumsum(l) * dt

        M = len(grid)
        span = grid[None, :] - grid[:, None]
        valid = span > 0

        def log_transfer(ids):
            with np.errstate(divide='ignore', invalid='ignore'):
                t = np.where(valid, -len(ids) * np.log(np.where(valid, span, 1.0)), -np.inf)
            for i in ids:
                t = t + ec._log_interval_integral(cum[i])
            return t

        logT = [log_transfer(g) for g in chain.groups]
        T = [np.where(np.isfinite(np.exp(ec._rescaled(t))), np.exp(ec._rescaled(t)), 0.0)
             for t in logT]
        outer = grid[None, :] - grid[:, None]                  # [b2, b0]
        with np.errstate(divide='ignore', invalid='ignore'):
            corr = np.where(outer > 0, 1.0 / np.where(outer > 0, outer, 1.0), 0.0)

        # Explicit cube, axes (b2, b1, b0).
        for gi, ids in enumerate(chain.groups):
            for d in ids:
                with np.errstate(invalid='ignore'):
                    lg = np.where(np.isfinite(logT[gi]),
                                  logT[gi] - ec._log_interval_integral(cum[d]), -np.inf)
                G = np.exp(ec._rescaled(lg))
                G = np.where(np.isfinite(G), G, 0.0)
                if gi == 0:
                    pair = (G[None, :, :] * T[1][:, :, None] * corr[:, None, :]).sum(axis=0)
                else:
                    pair = (T[0][None, :, :] * G[:, :, None] * corr[:, None, :]).sum(axis=2)
                q = np.cumsum(pair, axis=0)
                q = np.cumsum(q[:, ::-1], axis=1)[:, ::-1]
                expected = lik[d] * np.diagonal(q)
                expected = expected / expected.sum()

                got = np.array(solved['measurements'][d]['probability_distribution'])[:, 1]
                got = got / got.sum()
                overlap = float(np.minimum(got, expected).sum())
                assert overlap > 0.999, f'{d} overlap {overlap}'
