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


class TestLayoutExplainsItself:
    """
    Every refusal carries a reason, because the API shows it to the user: a
    fallback the user cannot act on is barely better than a silent one. The
    reason comes from layout_with_reason, which layout() itself delegates to,
    so the two can never disagree about whether a model is a chain.
    """

    def test_a_chain_has_no_reason(self):
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        chain, reason = ec.layout_with_reason(tree, _dates(('d1', 3000, 30, 'p')))
        assert chain is not None
        assert reason is None

    def test_every_refusal_carries_one(self):
        no_boundaries = _sequence('s', [_phase('p0'), _phase('p1')])
        left = _sequence('l', [_boundary('lb0'), _phase('lp'), _boundary('lb1')])
        right = _sequence('r', [_boundary('rb0'), _phase('rp'), _boundary('rb1')])
        parallel = _sequence('s2', [{'id': 'par', 'element_type': 'phase',
                                     'name': 'Parallel', 'tpq_age_bp': None,
                                     'taq_age_bp': None, 'children': [left, right]}])
        loose = _sequence('s3', [_boundary('b0'), _phase('p0'), _boundary('b1'), _phase('p1')])

        cases = [
            (no_boundaries, _dates(('d1', 3200, 30, 'p0'), ('d2', 3000, 30, 'p1'))),
            (parallel, _dates(('d1', 3200, 30, 'lp'), ('d2', 3000, 30, 'rp'))),
            (loose, _dates(('d1', 3200, 30, 'p0'), ('d2', 3000, 30, 'p1'))),
            (ox.model_tree('ordered'), ox.measurements()),
            (None, []),
        ]
        for tree, dates in cases:
            chain, reason = ec.layout_with_reason(tree, dates)
            assert chain is None
            assert reason, f'no reason given for {tree and tree.get("name")}'
            # Written for an archaeologist reading the results page, not for a
            # developer reading a traceback.
            assert 'None' not in reason and '_' not in reason, reason

    def test_the_reason_names_the_dates_left_outside(self):
        """
        The actionable case: which dates need bracketing. Naming them is the
        difference between a fixable message and a puzzle.
        """
        tree = _sequence('s', [_boundary('b0'), _phase('p0'), _boundary('b1'), _phase('p1')])
        dates = _dates(('d1', 3200, 30, 'p0'), ('loose', 3000, 30, 'p1'))
        _chain, reason = ec.layout_with_reason(tree, dates)
        assert 'loose' in reason
        assert 'd1' not in reason, 'only the unenclosed dates should be named'

    def test_groups_ordered_with_no_boundary_says_to_add_one(self):
        _chain, reason = ec.layout_with_reason(ox.model_tree('ordered'), ox.measurements())
        assert 'Boundary' in reason


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
            # The series is trimmed to where it has mass, so line it up with
            # the brute force by calendar age rather than by position.
            pairs = np.array(solved['measurements'][d['id']]['probability_distribution'])
            where = np.searchsorted(grid, pairs[:, 0] - solved['grid']['step'] / 2)
            got, mine = pairs[:, 1], expected[j][where]
            # Overlapping coefficient over the shared support; 1.0 is identical.
            overlap = float(np.minimum(got / got.sum(), mine / mine.sum()).sum())
            assert overlap > 0.999, f'{d["id"]} overlap {overlap}'


# What the solver actually produces on the shared_boundary fixture, with
# OxCal's UniformSpanPrior on (the default) and off. Both are recorded
# because the correction is a measured trade-off rather than an improvement:
# it pulls the outermost boundaries in towards OxCal's, and moves the dates
# slightly away. For scale, pm.sample_smc gave 25.3 rms and a separation of
# 57 against OxCal's 13.2.
_SOLVED_CACHE = {}

# Measured against OxCal's shared_boundary run. These moved when the minimum
# span was introduced (exact_chain.MIN_SPAN_ERROR_FRACTION), and the two settings
# moved in OPPOSITE directions, which is the point of keeping both pinned:
#
#                        rms          max        separation (OxCal 13.2)
#   span prior on    5.45 -> 1.73  9.30 -> 3.19    5.7 -> 16.0
#   span prior off   4.17 -> 5.86  5.30 -> 7.99   19.4 -> 25.1
#
# On -- the default, and what OxCal does -- improves substantially on every
# measure, and its separation goes from badly undershooting OxCal to close. Off
# degrades, which is acceptable: it is a deliberate departure from OxCal, and the
# floor was calibrated on the setting that matches it.
SPAN_PRIOR_EXPECTED = {
    True:  {'rms': 2.5, 'max': 5.0, 'separation': (12.0, 20.0)},
    False: {'rms': 7.0, 'max': 9.5, 'separation': (20.0, 30.0)},
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
        services/gibbs.py's solve produces has to be present.
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

                pairs = np.array(solved['measurements'][d]['probability_distribution'])
                where = np.searchsorted(grid, pairs[:, 0] - solved['grid']['step'] / 2)
                got = pairs[:, 1] / pairs[:, 1].sum()
                mine = expected[where] / expected[where].sum()
                overlap = float(np.minimum(got, mine).sum())
                assert overlap > 0.999, f'{d} overlap {overlap}'


class TestReportingIsIndependentOfTheInternalGrid:
    """
    Two defects found by running the recommended shared-Boundary model
    through the app: every modelled date plotted as a narrow spike, and 68.3%
    and 95.4% ranges that looked nearly the same.
    """

    def _solve(self, **overrides):
        dates = _dates(('rp_a', 3000, 30, 'p'), ('rp_b', 3040, 30, 'p'),
                       ('rp_c', 3080, 30, 'p'))
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        chain = ec.layout(tree, dates)
        if not overrides:
            return dates, ec.solve(dates, tree, chain, num_samples=200)
        import unittest.mock as mock
        with mock.patch.multiple(ec, **overrides):
            return dates, ec.solve(dates, tree, chain, num_samples=200)

    def test_plotted_series_is_trimmed_to_where_the_mass_is(self):
        """
        The solver's grid runs far wider than the dates because the
        boundaries need room -- on the OxCal fixture it spanned 2031 years
        for a posterior about 100 years wide, so every date drew as a spike
        in a mostly empty axis. The series must cover the posterior, not the
        grid.
        """
        dates, solved = self._solve()
        span = solved['grid']['upper'] - solved['grid']['lower']
        for d in dates:
            pairs = np.array(solved['measurements'][d['id']]['probability_distribution'])
            plotted = pairs[-1, 0] - pairs[0, 0]
            assert plotted < span / 2, 'series still spans most of the solver grid'
            # ...and nothing meaningful was cut off either side.
            edge = pairs[:, 1][[0, -1]].max() / pairs[:, 1].max()
            assert edge < 1e-3

    def test_reported_ranges_do_not_move_with_the_internal_step(self):
        """
        The internal step is derived from the date range and the group count,
        so a range computed at it would change when neither the dates nor the
        model did. Reporting happens at REPORTING_STEP_YEARS instead.

        Asserted on the envelope and the total width rather than range by
        range: on a wiggly stretch of curve the posterior has shallow dips
        right at the HPD threshold, so how many fragments a region breaks
        into is genuinely borderline and shifts by one either way. Where the
        region sits and how much of the calendar it covers are not.
        """
        dates, fine = self._solve(MAX_GRID_POINTS=2000)
        _again, coarse = self._solve(MAX_GRID_POINTS=1000)
        assert coarse['grid']['step'] > fine['grid']['step'] * 1.5, 'steps not different'

        tolerance = 2 * ec.REPORTING_STEP_YEARS
        for d in dates:
            a = fine['measurements'][d['id']]['hpd_95_ranges']
            b = coarse['measurements'][d['id']]['hpd_95_ranges']
            assert min(r['min'] for r in a) == pytest.approx(
                min(r['min'] for r in b), abs=tolerance)
            assert max(r['max'] for r in a) == pytest.approx(
                max(r['max'] for r in b), abs=tolerance)
            wa = sum(r['max'] - r['min'] for r in a)
            wb = sum(r['max'] - r['min'] for r in b)
            assert wa == pytest.approx(wb, rel=0.15)

    def test_ranges_land_on_the_reporting_grid(self):
        dates, solved = self._solve()
        step = ec.REPORTING_STEP_YEARS
        for d in dates:
            for key in ('hpd_68_ranges', 'hpd_95_ranges'):
                for r in solved['measurements'][d['id']][key]:
                    assert r['min'] % step == pytest.approx(0.0, abs=1e-6)
                    assert r['max'] % step == pytest.approx(0.0, abs=1e-6)
                    assert r['max'] - r['min'] >= step, 'a zero-width range'

    def test_95_is_wider_than_68(self):
        """The symptom that prompted this: the two looked nearly the same."""
        dates, solved = self._solve()
        for d in dates:
            e = solved['measurements'][d['id']]
            w68 = sum(r['max'] - r['min'] for r in e['hpd_68_ranges'])
            w95 = sum(r['max'] - r['min'] for r in e['hpd_95_ranges'])
            assert w95 > w68 * 1.3, f'{d["id"]}: 68% {w68} vs 95% {w95}'


class TestOutlierModel:
    """
    The outlier model, against the OxCal 4.4 run in
    tests/fixtures/oxcal_outlier -- Shag River Mouth, 16 dates in one phase,
    NZ7756 at 970 +/- 47 BP against the rest at 537-660, with
    Outlier_Model("General", T(5), U(0,4), "t") and a 5% prior on each date.

    A Bernoulli indicator per date, integrated out exactly, which is what
    makes it free: the indicator touches only that date's own likelihood, so
    the mixture substitutes straight in and the chain never changes.
    """

    @pytest.fixture(scope='class')
    def solved(self):
        import shag_fixture as sf
        samples, tree = sf.measurements(), sf.model_tree()
        chain = ec.layout(tree, samples)
        assert chain is not None
        return sf, ec.solve(samples, tree, chain, num_samples=500)

    def test_finds_the_outlier_oxcal_found(self, solved):
        sf, res = solved
        probabilities = {m['id']: res['measurements'][m['id']]['outlier_probability']
                         for m in sf.measurements()}
        assert probabilities[sf.OUTLIER] > 0.95, probabilities
        others = [v for k, v in probabilities.items() if k != sf.OUTLIER]
        # Every sound date must land BELOW its own prior -- the evidence
        # should argue them down, not merely leave them where they started.
        assert max(others) < sf.OUTLIER_PRIOR, probabilities

    def test_probabilities_track_oxcals(self, solved):
        sf, res = solved
        table = sf.table()
        errors = np.array([
            res['measurements'][m['id']]['outlier_probability']
            - table[m['id']]['outlier_probability']
            for m in sf.measurements()
        ])
        assert np.abs(errors).max() < 0.05, dict(zip(
            [m['id'] for m in sf.measurements()], errors.round(4)))

    def test_the_outlier_component_keeps_its_own_scale(self, solved):
        """
        The trap this walked into once. The outlier component is a proper
        density whose mass mostly lies off the solver's grid -- that IS the
        statement that a flagged date could be anywhere. Renormalising what
        remains inflates it by the reciprocal of the retained mass and makes
        the answer depend on the grid width: it put these fifteen sound
        dates at 9-14%, above their own 5% prior, against OxCal's 0.6-1.5%.

        So the same dates solved on a much wider grid must give much the
        same probabilities.
        """
        sf, res = solved
        import unittest.mock as mock
        samples, tree = sf.measurements(), sf.model_tree()
        chain = ec.layout(tree, samples)
        with mock.patch.object(ec, 'BOUNDARY_WINDOW_FACTOR', 30.0):
            wide = ec.solve(samples, tree, chain, num_samples=500)
        assert wide['grid']['upper'] - wide['grid']['lower'] > \
            2 * (res['grid']['upper'] - res['grid']['lower'])
        for m in samples:
            narrow_p = res['measurements'][m['id']]['outlier_probability']
            wide_p = wide['measurements'][m['id']]['outlier_probability']
            assert wide_p == pytest.approx(narrow_p, abs=0.05), m['id']

    def test_off_by_default(self):
        """It changes reported dates, so it is the analyst's call."""
        dates = _dates(('ol_a', 3000, 30, 'p'), ('ol_b', 3030, 30, 'p'))
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        solved = ec.solve(dates, tree, ec.layout(tree, dates), num_samples=100)
        for d in dates:
            assert 'outlier_probability' not in solved['measurements'][d['id']]

    def test_costs_nothing_when_there_is_no_outlier(self):
        """
        Turning it on must not move a clean model, or nobody can afford to
        leave it on.
        """
        plain = _dates(('oc_a', 3000, 30, 'p'), ('oc_b', 3020, 30, 'p'),
                       ('oc_c', 3040, 30, 'p'), ('oc_d', 3060, 30, 'p'))
        flagged = [dict(d, outlier_prior=0.05) for d in plain]
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        chain = ec.layout(tree, plain)

        off = ec.solve(plain, tree, chain, num_samples=200)
        on = ec.solve(flagged, tree, chain, num_samples=200)
        for d in plain:
            a = off['measurements'][d['id']]['posterior_mean']
            b = on['measurements'][d['id']]['posterior_mean']
            assert b == pytest.approx(a, abs=5.0), d['id']
            assert on['measurements'][d['id']]['outlier_probability'] < 0.05


class TestCharcoalOutlierModel:
    """
    The one-sided model, for residual and old-wood material.

    The general model is symmetric: it lets a flagged date be wrong in
    either direction. Charcoal knows the direction -- the material formed
    BEFORE the context it was found in, never after -- and saying so is
    worth real precision.
    """

    def _residual_case(self, model):
        """Four contemporary dates plus one 400 radiocarbon years too old."""
        dates = [dict(d, outlier_prior=0.05, outlier_model=model) for d in _dates(
            ('ch_a', 3000, 30, 'p'), ('ch_b', 3020, 30, 'p'), ('ch_c', 3040, 30, 'p'),
            ('ch_d', 3060, 30, 'p'), ('ch_e', 3460, 30, 'p'))]
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        return dates, ec.solve(dates, tree, ec.layout(tree, dates), num_samples=300)

    def test_kernel_only_shifts_the_context_younger(self):
        """
        Direction is the whole point and is easy to get backwards. The
        material is older than its context, so the CONTEXT age the kernel
        produces must be younger -- lower cal BP, negative lag. Pointed the
        other way it says the context predates the wood in it, and a
        residual date comes out looking MORE consistent than it is.
        """
        step = 5.0
        kernel = ec._outlier_kernel(step, 5000.0, 'charcoal')
        centre = (len(kernel) - 1) // 2
        assert kernel[:centre + 1].sum() == pytest.approx(1.0)
        assert kernel[centre + 1:].sum() == pytest.approx(0.0)
        # ...and the general model is symmetric, which is the difference.
        general = ec._outlier_kernel(step, 5000.0, 'general')
        mid = (len(general) - 1) // 2
        assert general[:mid].sum() == pytest.approx(general[mid + 1:].sum(), rel=1e-6)

    def test_detects_a_residual_date_more_confidently_than_the_general_model(self):
        _dg, general = self._residual_case('general')
        _dc, charcoal = self._residual_case('charcoal')
        assert (charcoal['measurements']['ch_e']['outlier_probability']
                > general['measurements']['ch_e']['outlier_probability'])

    def test_leaves_the_sound_dates_alone(self):
        dates, solved = self._residual_case('charcoal')
        for d in dates:
            if d['id'] == 'ch_e':
                continue
            assert solved['measurements'][d['id']]['outlier_probability'] < 0.2, d['id']

    def test_an_unknown_model_is_refused_rather_than_guessed(self):
        dates = [dict(d, outlier_prior=0.05, outlier_model='wishful')
                 for d in _dates(('ch_x', 3000, 30, 'p'))]
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        with pytest.raises(ValueError, match='outlier model'):
            ec.solve(dates, tree, ec.layout(tree, dates), num_samples=50)

    def test_defaults_to_general_when_unspecified(self):
        dates = [dict(d, outlier_prior=0.05) for d in _dates(
            ('cd_a', 3000, 30, 'p'), ('cd_b', 3030, 30, 'p'))]
        explicit = [dict(d, outlier_model='general') for d in dates]
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        chain = ec.layout(tree, dates)
        a = ec.solve(dates, tree, chain, num_samples=200)
        b = ec.solve(explicit, tree, chain, num_samples=200)
        for d in dates:
            assert (a['measurements'][d['id']]['outlier_probability']
                    == pytest.approx(b['measurements'][d['id']]['outlier_probability']))


class TestImplicitBoundaries:
    """
    A Sequence that orders two groups with no Boundary between them is not a
    chain, so it used to fall through to MCMC -- where it is measurably
    wrong, and where the outlier model does not work at all. Supplying the
    transition it implies makes it exact.

    This is a change to the model, not a rewriting of it: the uniform-scatter
    prior becomes one term per group rather than one across both. The
    justification is empirical and lives in the fixture -- see
    test_matches_oxcals_own_answer_for_that_model below.
    """

    def test_inserts_only_where_a_boundary_is_missing(self):
        from app.services.model_tree import with_implicit_boundaries

        already = ox.model_tree('shared_boundary')
        _tree, inserted = with_implicit_boundaries(already)
        assert inserted == [], 'nothing to add when the transition is explicit'

        ordered = ox.model_tree('ordered')
        patched, inserted = with_implicit_boundaries(ordered)
        assert len(inserted) == 1
        assert ec.layout(patched, ox.measurements()) is not None

    def test_leaves_a_single_group_alone(self):
        from app.services.model_tree import with_implicit_boundaries

        _tree, inserted = with_implicit_boundaries(ox.model_tree('unordered'))
        assert inserted == []

    def test_matches_oxcals_own_answer_for_that_model(self):
        """
        The whole justification. Solved this way, the fixture's `ordered`
        model lands close to what OxCal reports FOR THAT MODEL -- much closer
        than MCMC managed, which put the two groups 37 years apart against
        OxCal's 11.7 and was 12.6 years rms out.
        """
        from app.services.model_tree import with_implicit_boundaries

        samples = ox.measurements()
        patched, _inserted = with_implicit_boundaries(ox.model_tree('ordered'))
        solved = ec.solve(samples, patched, ec.layout(patched, samples), num_samples=300)

        dists = ox.distributions('ordered')
        errors = []
        for m in samples:
            cal, prob = dists[m['id']]['posterior']
            errors.append(solved['measurements'][m['id']]['posterior_mean']
                          - float((cal * prob / prob.sum()).sum()))
        errors = np.array(errors)
        assert np.sqrt((errors ** 2).mean()) < 9.0, errors.round(1)

        def mean_ad(ids):
            return np.mean([1950 - solved['measurements'][i]['posterior_mean'] for i in ids])
        separation = mean_ad(ox.ROCKSHELTER) - mean_ad(ox.QUARRY)
        assert 0.0 < separation < 20.0, separation

    def test_the_outlier_model_now_works_on_such_a_model(self):
        """
        The reason this matters beyond accuracy: the MCMC path this replaces
        never applied the outlier model at all, so these analyses came back
        with an empty Outlier column however it was configured.
        """
        from app.services.model_tree import with_implicit_boundaries

        samples = [dict(m, outlier_prior=0.05, outlier_model='general')
                   for m in ox.measurements()]
        patched, inserted = with_implicit_boundaries(ox.model_tree('ordered'))
        assert inserted
        solved = ec.solve(samples, patched, ec.layout(patched, samples), num_samples=300)
        for m in samples:
            assert 'outlier_probability' in solved['measurements'][m['id']]
