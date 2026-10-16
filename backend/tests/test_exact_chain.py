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
from app.services.model_tree import inherited_bounds

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
        though its tree is not flat. This is the Hillside shared_boundary
        shape, and rejecting it would send a solvable model to the sampler.
        """
        chain = ec.layout(ox.model_tree('shared_boundary'), ox.measurements())
        assert chain is not None
        assert chain.boundary_ids == ['b_start', 'b_mid', 'b_end']
        assert [sorted(g) for g in chain.groups] == [
            sorted(ox.FIRE_FEATURE_J), sorted(ox.SOUTH)]

    def test_groups_ordered_without_a_boundary_between_them_are_blocks(self):
        """
        The Hillside original: two branches ordered against each other while
        sharing ONE enclosing boundary pair. Their dates are all in a single
        span group, ordered one block after the other inside it, which the
        solver integrates as such (_OrderedGroup) rather than dropping the
        order or supplying a Boundary the model does not have.
        """
        chain = ec.layout(ox.model_tree('ordered'), ox.measurements())
        assert len(chain.groups) == 1 and chain.ordered(0)
        assert [sorted(block) for block in chain.blocks[0]] == [sorted(ox.NORTH),
                                                                sorted(ox.SOUTH)]

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
        not_blocks = _side_by_side_lines()

        cases = [
            (no_boundaries, _dates(('d1', 3200, 30, 'p0'), ('d2', 3000, 30, 'p1'))),
            (parallel, _dates(('d1', 3200, 30, 'lp'), ('d2', 3000, 30, 'rp'))),
            (loose, _dates(('d1', 3200, 30, 'p0'), ('d2', 3000, 30, 'p1'))),
            not_blocks,
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

    def test_too_few_boundaries_says_what_to_add(self):
        """The app never adds them itself, so the reason has to say where."""
        dates = _dates(('d1', 3200, 30, 'p0'), ('d2', 3000, 30, 'p1'))
        none = _sequence('s', [_phase('p0'), _phase('p1')])
        one = _sequence('s', [_boundary('b0'), _phase('p0'), _phase('p1')])
        _c, reason = ec.layout_with_reason(none, dates)
        assert 'Add a Boundary at the start and end of the Sequence' in reason
        _c, reason = ec.layout_with_reason(one, dates)
        assert 'Add another Boundary' in reason

    def test_an_order_that_is_not_blocks_says_to_give_each_its_own_boundaries(self):
        _chain, reason = ec.layout_with_reason(*_side_by_side_lines())
        assert reason == ec.NOT_ONE_AFTER_ANOTHER
        assert 'Boundaries' in reason


def _side_by_side_lines():
    """Two Sequences of Phases with no Boundaries of their own, side by side
    in one pair of Boundaries: an order inside the pair that is not blocks."""
    lines = {'id': 'lines', 'element_type': 'phase', 'name': 'Lines',
             'tpq_age_bp': None, 'taq_age_bp': None,
             'children': [_sequence('na', [_phase('na1'), _phase('na2')]),
                          _sequence('nb', [_phase('nb1'), _phase('nb2')])]}
    tree = _sequence('s4', [_boundary('n0'), lines, _boundary('n1')])
    return tree, _dates(('e1', 3100, 30, 'na1'), ('e2', 3000, 30, 'na2'),
                        ('e3', 3090, 30, 'nb1'), ('e4', 2990, 30, 'nb2'))


def _brute_force(curve, grid, dates):
    """
    One bounded group's exact marginals, by explicit summation over every
    (younger, older) boundary pair. Deliberately the slowest, most obvious
    implementation of

        p(t_j) ∝ L_j(t_j) * sum over {tb <= t_j <= ta} of
                 h(t_j) (ta-tb)^(-K) * prod over i != j of F_i(tb, ta)

    with h = 1/2 when t_j sits on tb or ta and 1 otherwise -- and where tb and
    ta share a cell, a tie at half weight with every date in it, the term
    1/2 prod over i != j of L_i(tb), over the step: it is the whole weight of
    that one cell where the rest are densities -- so that agreement
    with the vectorised solver is evidence about the code rather than a
    restatement of it. The half is what makes these the marginals of the joint
    the trapezoidal F integrates: summed over t_j, L_j h gives F_j back.

    What it checks is the MESSAGE PASSING, not the quadrature: F below uses the
    same trapezoidal interval integral the solver does, because a different
    quadrature here would make the two disagree for a reason that has nothing to
    do with the algorithm being compared. The quadrature has its own test against
    a continuum reference -- see TestIntervalQuadrature.

    The limits factor is included for the same reason, written out from A.31 rather
    than imported: at one group there is no interior-boundary factor, so it used to
    be true that a single group carried no span correction at all, and it is not any
    more -- the dates bound the boundaries between them, which is a real part of the
    prior worth 5.8% of the overlap here.
    """
    dt = grid[1] - grid[0]
    K = len(dates)
    L = []
    for d in dates:
        # In F14C, as calibration forms it; written out here so the reference
        # does not lean on the code it checks.
        c14, err = curve.interpolate(grid)
        expected, measured = np.exp(-c14 / 8033.0), np.exp(-d['age_bp'] / 8033.0)
        sigma2 = (expected * err / 8033.0) ** 2 + (measured * d['error_1sigma'] / 8033.0) ** 2
        lik = np.exp(-0.5 * (expected - measured) ** 2 / sigma2) / np.sqrt(sigma2)
        L.append(lik / (lik.sum() * dt))
    L = np.array(L)
    cum = np.cumsum(L, axis=1) * dt

    def F(j, b, a):
        whole = cum[j, a] - (cum[j, b - 1] if b > 0 else 0.0)
        return max(whole - 0.5 * dt * (L[j, a] + L[j, b]), 0.0)

    M = len(grid)
    # A.31's limits, from the dates' own extents: the older boundary can be no
    # younger than the youngest end of the OLDEST date's support, and the younger no
    # older than the oldest end of the YOUNGEST date's.
    support = [np.where(l >= l.max() * ec.SUPPORT_THRESHOLD)[0] for l in L]
    young_bound = max(grid[sup[0]] for sup in support)
    old_bound = min(grid[sup[-1]] for sup in support)

    def limits(b, a):
        # only A.31's second term is finite with the outer sides unconstrained
        g = (grid[a] - grid[b]) - (young_bound - old_bound)
        return 1.0 / g if g > 0 else 1.0

    out = np.zeros((K, M))
    for b in range(M):
        # The two boundaries in one cell: a tie, at half weight, and every date
        # there with them -- the limit of span^(-K) prod F, which is prod L.
        for j in range(K):
            out[j, b] += 0.5 * limits(b, b) * np.prod([L[i, b] for i in range(K) if i != j]) / dt
        for a in range(b + 1, M):
            base = (grid[a] - grid[b]) ** (-K) * limits(b, a)
            fs = [F(j, b, a) for j in range(K)]
            for j in range(K):
                weight = base
                for i in range(K):
                    if i != j:
                        weight *= fs[i]
                if weight > 0:
                    out[j, b + 1:a] += weight
                    out[j, b] += 0.5 * weight
                    out[j, a] += 0.5 * weight
    out = L * out
    return out / (out.sum(axis=1, keepdims=True) * dt)


class TestIntervalQuadrature:
    """
    The interval integral, against a continuum reference with NO quadrature in it
    at all: Gaussian likelihoods, so the integral of each over [b, a] is an exact
    error function, and the only approximation left is the 2-D integration over
    the boundaries, which is refined until it stops moving.

    This is the check that was missing, and its absence cost a lot. Every other
    comparison in this suite either shares the solver's quadrature (the
    brute-force tests, and tests/manual/oxcal_reference.py, all of which use the
    same cumulative-sum trick) or is against OxCal, which cannot tell a
    discretisation error from a modelling difference. A first-order error in this
    one function therefore went unnoticed while being amplified to the K-th power
    by the (span)^(-K) factor it feeds -- see _interval_integral for the story.
    """

    MU = np.array([100.0, 130.0, 160.0])
    SD = np.array([30.0, 30.0, 30.0])
    WINDOW = (-400.0, 700.0)

    def _gaussians(self, grid):
        return (np.exp(-0.5 * ((grid[None, :] - self.MU[:, None]) / self.SD[:, None]) ** 2)
                / (self.SD[:, None] * np.sqrt(2 * np.pi)))

    def _continuum_span(self, points):
        """E[boundary span] with exact interval integrals."""
        from scipy.special import erf
        grid = np.linspace(*self.WINDOW, points)
        younger, older = np.meshgrid(grid, grid, indexing='ij')
        span = older - younger
        ok = span > 0

        def cdf(x):
            return erf((x[None, ...] - self.MU[:, None, None])
                       / (self.SD[:, None, None] * np.sqrt(2)))

        integrals = 0.5 * (cdf(np.where(ok, older, 1.0)) - cdf(np.where(ok, younger, 0.0)))
        with np.errstate(divide='ignore', invalid='ignore'):
            logw = np.where(ok, -len(self.MU) * np.log(np.where(ok, span, 1.0))
                            + np.log(np.clip(integrals, 1e-300, None)).sum(0), -np.inf)
        weight = np.exp(logw - logw[np.isfinite(logw)].max())
        weight = np.where(np.isfinite(weight), weight, 0.0)
        return float((span * weight).sum() / weight.sum())

    def _solver_span(self, step):
        """The same quantity via exact_chain's own interval integral."""
        grid = np.arange(self.WINDOW[0], self.WINDOW[1] + step, step)
        density = self._gaussians(grid)
        cumulative = np.cumsum(density, axis=1) * step
        span = grid[None, :] - grid[:, None]
        ok = span > 0
        with np.errstate(divide='ignore', invalid='ignore'):
            logw = np.where(ok, -len(self.MU) * np.log(np.where(ok, span, 1.0)), -np.inf)
        for i in range(len(self.MU)):
            logw = logw + ec._log_interval_integral(cumulative[i], density[i], step)
        weight = np.exp(logw - logw[np.isfinite(logw)].max())
        weight = np.where(np.isfinite(weight), weight, 0.0)
        return float((span * weight).sum() / weight.sum())

    def test_it_matches_the_continuum_answer(self):
        """
        Truth is 181.5 years. The trapezoidal form gives 186.3 at a ten-year step
        and 181.9 at one year; the plain cumulative difference this replaced gave
        126.4 and 167.8 -- 30% low at ten years, and converging only as the step
        vanishes.
        """
        truth = self._continuum_span(3000)
        assert truth == pytest.approx(181.5, abs=1.0), truth
        for step, tolerance in ((10.0, 0.05), (5.0, 0.03), (1.0, 0.01)):
            got = self._solver_span(step)
            assert got == pytest.approx(truth, rel=tolerance), (step, got, truth)

    def test_a_zero_width_interval_integrates_to_zero(self):
        """
        The defect in one line. A cumulative difference gives a zero-width
        interval one whole cell of probability mass, which is what handed the
        near-coincident configurations a factor of two per date.
        """
        grid = np.arange(0.0, 100.0, 5.0)
        density = self._gaussians(grid)[0]
        cumulative = np.cumsum(density) * 5.0
        F = ec._interval_integral(cumulative, density, 5.0)
        assert np.allclose(np.diagonal(F), 0.0)

    def test_it_is_the_true_width_that_is_integrated(self):
        """
        A flat density over k cells' span must integrate to k * step, not
        (k + 1) * step. Stated on a uniform density where the answer is obvious.
        """
        step = 2.0
        flat = np.ones(60) / (60 * step)
        F = ec._interval_integral(np.cumsum(flat) * step, flat, step)
        for k in (1, 5, 20):
            assert F[0, k] == pytest.approx(k * step * flat[0], rel=1e-12), k


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

    def test_a_date_marginal_is_the_marginal_of_the_joint_draws(self):
        """
        Where a group's two Boundaries share a cell, the date in that group is
        in that cell too, and its marginal takes that configuration's whole
        weight. That weight is a mass in one cell where every other term is a
        density, so it is divided by the step; counted whole, the answer
        depended on the grid's unit, correct only at a one-year step.

        The joint draws never had that problem -- they place the date in the
        cell its Boundaries share -- so on a coarse grid, where such ties carry
        real weight, the reported marginal must be their histogram. It was 0.07
        away on this model, and the sampler agreed with the draws.
        """
        dates = _dates(('jd_a', 3000, 30, 'p'), ('jd_b', 2950, 35, 'p'))
        tree = _sequence('jd', [_boundary('jd0'), _phase('p'), _boundary('jd1')])
        chain = ec.layout(tree, dates)
        grid = np.linspace(3000.0, 3350.0, 12)
        solved, _densities = ec._solve_on_grid(
            grid, dates, tree, chain, inherited_bounds(tree), num_samples=50, seed=1,
            uniform_span_prior=True, estimate_durations=False, dispersal=None,
            interval_draws=40000, resolution=5.0)
        for d in dates:
            reported = np.zeros(len(grid))
            for age, density in solved['measurements'][d['id']]['probability_distribution']:
                reported[np.argmin(np.abs(grid - age))] = density
            drawn = np.bincount(np.searchsorted(grid, solved['joint_draws'][d['id']]),
                                minlength=len(grid))
            assert np.abs(reported / reported.sum() - drawn / drawn.sum()).max() < 0.012, d['id']


# What the solver actually produces on the shared_boundary fixture, with OxCal's
# UniformSpanPrior on (the default) and off. Both are recorded because the two
# settings moved in OPPOSITE directions when the interval quadrature was corrected
# (see _interval_integral), which is worth being able to see:
#
#                        rms          max        separation (OxCal 13.2)
#   span prior on    5.45 -> 1.26  9.30 -> 2.03    5.7 -> 15.8
#   span prior off   4.17 -> 6.67  5.30 -> 8.61   19.4 -> 26.7
#
# On -- the default, and what OxCal does -- is now clearly better on every
# measure, where the two used to be a trade-off. Off moved further away, which is
# the expected direction: with the quadrature right, the correction OxCal applies
# is doing real work, so omitting it costs more than it did.
#
# `lobe_gap` bounds how differently the two ordered groups may be weighted between
# the calibration lobes. With the prior on, ours are 0.101 and 0.182 against
# OxCal's 0.12 and 0.17. For scale, pm.sample_smc gave 25.3 rms, a separation of
# 57, and lobe shares of 0.00 and 0.71.
_SOLVED_CACHE = {}

SPAN_PRIOR_EXPECTED = {
    True:  {'rms': 2.0, 'max': 3.0, 'separation': (12.0, 19.0), 'lobe_gap': 0.15},
    False: {'rms': 8.0, 'max': 10.0, 'separation': (22.0, 31.0), 'lobe_gap': 0.30},
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
        separation = mean_ad(ox.SOUTH) - mean_ad(ox.NORTH)
        low, high = SPAN_PRIOR_EXPECTED[span_prior]['separation']
        assert low < separation < high

    def test_no_spurious_lobe_split(self, solved, span_prior):
        """
        SMC satisfied the ordering by pushing North into the early
        calibration lobe and South into the late one -- 0.00 and 0.71
        of the mass around AD 1370-1420 against OxCal's 0.12 and 0.17. Both
        groups must keep a comparable, modest share.
        """
        lo, hi = 1950 - 1420, 1950 - 1370
        share = {}
        for group, ids in (('north', ox.NORTH), ('south', ox.SOUTH)):
            masses = []
            for i in ids:
                dist = np.array(solved['measurements'][i]['probability_distribution'])
                inside = (dist[:, 0] >= lo) & (dist[:, 0] <= hi)
                masses.append(dist[inside, 1].sum() / dist[:, 1].sum())
            share[group] = float(np.mean(masses))
        assert 0.02 < share['north'] < 0.35
        assert 0.02 < share['south'] < 0.35
        assert abs(share['north'] - share['south']) < \
            SPAN_PRIOR_EXPECTED[span_prior]['lobe_gap']

    def test_boundary_ranges_match_oxcal(self, solved, span_prior):
        """The shared transition is the one boundary the dates pin down well."""
        table = ox.table_ranges('shared_boundary')
        got = solved['boundaries']['b_mid']['posterior']
        envelope = (min(r['min'] for r in got['hpd_95_ranges']),
                    max(r['max'] for r in got['hpd_95_ranges']))
        want_from, want_to = table['NORTHTOSOUTH']['modelled'][95][0]
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


class TestOrderedGroupsAgreeWithOxCal:
    """
    Two ordered groups against OxCal, which was the largest open disagreement in
    this suite for a long stretch and is now closed to about a year.

        run               OxCal   exact   gibbs        was (exact/gibbs)
        unordered          -0.1     0.0    -0.1
        ordered            11.7    12.0    11.5-11.6   15.8 / 14.4
        shared_boundary    13.2    13.5    13.5-14.0   15.8 / 15.9

    Measured with the F14C likelihood; the sampler over three seeds. On
    `ordered` the exact solver's trapezoid is first order in the step and it
    stops at 0.45 years: 12.36, 12.07, 11.93, 11.86 at steps of 1, 0.5, 0.25
    and 0.125, heading for about 11.8, which the sampler gives at any step
    (gibbs.Model._cluster_weight).

    The exact column once moved from 12.9 to 14.0 when two things were
    corrected at once. A date sitting on one of its Boundaries now counts half, as it does in
    the interval integral the Boundaries come from, where it used to count whole
    and the modelled dates were not the marginals of the joint posterior. And a
    second pass refines the grid. The 12.9 was partly the first error flattering
    a grid that had not converged: on shared_boundary the separation runs 15.2,
    14.2, 13.8 and 13.6 years at steps of 2.5, 1.0, 0.45 and 0.23 with the half,
    and both weightings meet near OxCal's 13.2 only below half a year.

    `ordered` has no Boundary between its groups. This solver used to supply
    one (with_implicit_boundaries), which made it the SAME model as
    shared_boundary, where OxCal's two runs differ by 1.5 years; it now takes
    the groups as written, as blocks (_OrderedGroup), and lands +0.36 from
    OxCal where the supplied Boundary gave +2.37.

    The history is worth keeping, because the residual was blamed on several things
    it was not. Four separate causes, each found by measurement:

      * the interval quadrature, which gave an interval of span k*step one cell too
        much likelihood -- see _interval_integral. Worth most of it, and it was
        making every modelled phase several times too narrow.
      * the same in the sampler, which had to be corrected separately.
      * the interior-boundary factor, which divided by a continuum power where the
        discrete model needs a binomial count -- see _span_prior_correction. ~1.1
        years.
      * the LIMITS factor, absent entirely, which this suite asserted was negligible
        on the strength of a measurement that did not test it. Worth 1.5-2 years
        here and 18% of the span on a prior-dominated model. Identified by asking
        OxCal to run the same models with UniformSpanPrior off, at which point it
        agreed with us; see pair_limits.

    What is NOT the cause, also by measurement: the implicit-boundary rewrite (the
    gap was the same size with the middle Boundary written out), OxCal's own
    convergence (kIterations 30 -> 60 moves it 0.23 years).
    """

    def _separation(self, run, **kwargs):
        measurements = ox.measurements()
        tree = ox.model_tree(run)
        chain = ec.layout(tree, measurements)
        solved = ec.solve(measurements, tree, chain, num_samples=100, **kwargs)
        mean = lambda ids: np.mean(
            [solved['measurements'][i]['posterior_mean'] for i in ids])
        return mean(ox.NORTH) - mean(ox.SOUTH)

    def _oxcals(self, run):
        dists = ox.distributions(run)
        mean = lambda ids: np.mean([
            float((cal * prob / prob.sum()).sum())
            for i in ids for cal, prob in [dists[ox.key(i)]['posterior']]])
        return mean(ox.NORTH) - mean(ox.SOUTH)

    def test_with_nothing_ordering_them_we_agree_exactly(self):
        """The control. Same dates, same curve, same priors, only the ordering
        removed, and the separation matches to a tenth of a year."""
        assert self._separation('unordered') == pytest.approx(
            self._oxcals('unordered'), abs=0.5)

    @pytest.mark.parametrize('run,bound', [('ordered', 1.5), ('shared_boundary', 1.5)])
    def test_ordered_groups_agree_to_about_a_year(self, run, bound):
        """
        Measured at +0.36 on `ordered`, taken as written, and +0.26 on
        `shared_boundary`; earlier +2.37 on `ordered` with a Boundary supplied
        and +0.84 on `shared_boundary`, +1.19 and -0.34 before the half weight
        and the second pass (see the class note),
        +4.10 and +2.57 before the limits factor and +5.03/+3.50 before the
        binomial count.
        """
        excess = self._separation(run) - self._oxcals(run)
        assert abs(excess) < bound, (run, excess)

    def test_both_refinements_earn_their_place(self):
        """
        Switching the whole UniformSpanPrior off is much worse (26.7 years of
        separation against OxCal's 13.2), and OxCal agrees with US when ITS option is
        off -- 26.30 against our 26.7 -- which is what identified the missing factor
        in the first place. So this is not an artefact of applying the correction.
        """
        with_it = self._separation('shared_boundary', uniform_span_prior=True)
        without = self._separation('shared_boundary', uniform_span_prior=False)
        oxcal = self._oxcals('shared_boundary')
        assert abs(with_it - oxcal) < abs(without - oxcal) / 4.0, (
            with_it, without, oxcal)
        assert without == pytest.approx(26.5, abs=1.5), (
            "OxCal with its own option off reports 26.30 here", without)


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

    It divides out the freedom the n-2 interior boundaries have inside the span,
    which stops a sequence's span prior depending on how many sub-groups it is cut
    into. That couples the two ends of the chain, so it needs a different and more
    expensive computation than the plain forward/backward pass -- these check that
    the expensive one computes the same thing a brute-force sum does, and that the
    cheap one is still used when the correction cannot matter.

    TestTheCorrectionIsExact below checks the factor itself against a closed form,
    which is the part that does not depend on any fixture.
    """

    def _two_group_model(self):
        dates = _dates(('sp_a', 3300, 30, 'p0'), ('sp_b', 3270, 30, 'p0'),
                       ('sp_c', 3050, 30, 'p1'), ('sp_d', 3020, 30, 'p1'))
        tree = _sequence('s', [_boundary('b0'), _phase('p0'), _boundary('b1'),
                               _phase('p1'), _boundary('b2')])
        return dates, tree

    def test_no_interior_factor_with_two_boundaries_but_the_limits_still_apply(self):
        """
        The interior-boundary exponent is n - 2, so a single Phase between two
        Boundaries has no interior freedom to divide out. It is NOT untouched
        though: the limits factor applies to any pair with a span, and the dates
        between them supply the limits (see exact_chain.pair_limits). This used to
        assert that the whole correction was skipped here, which was wrong.

        Both still run on the cheap O(M^2) chain -- with one group the correction is
        a function of that group's own two boundaries, so it folds into the transfer
        matrix.
        """
        dates = _dates(('sp1_a', 3000, 30, 'p'), ('sp1_b', 3040, 30, 'p'))
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        chain = ec.layout(tree, dates)

        on = ec.solve(dates, tree, chain, num_samples=100, uniform_span_prior=True)
        off = ec.solve(dates, tree, chain, num_samples=100, uniform_span_prior=False)

        assert on['uniform_span_prior'] is True
        assert off['uniform_span_prior'] is False
        # It pulls the boundaries in, as it does with three of them.
        span = lambda r: (r['boundaries']['b0']['posterior']['posterior_mean']
                          - r['boundaries']['b1']['posterior']['posterior_mean'])
        assert span(on) < span(off), (span(on), span(off))

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

        def own(i):
            # A date's factor: its interval integral, and where the two
            # boundaries share a cell, its likelihood there -- the limit of
            # F / span as the span closes.
            f = ec._log_interval_integral(cum[i], lik[i], dt)
            with np.errstate(divide='ignore'):
                np.fill_diagonal(f, np.log(lik[i]))
            return f

        def log_transfer(ids):
            with np.errstate(divide='ignore', invalid='ignore'):
                t = np.where(valid, -len(ids) * np.log(np.where(valid, span, 1.0)), -np.inf)
            # Two boundaries in one cell: a tie, at half weight.
            np.fill_diagonal(t, np.log(0.5))
            for i in ids:
                t = t + own(i)
            return t

        logT = [log_transfer(g) for g in chain.groups]
        T = [np.where(np.isfinite(np.exp(ec._rescaled(t))), np.exp(ec._rescaled(t)), 0.0)
             for t in logT]
        # The correction the solver applies, written out independently: one over
        # the COUNT of places the single interior boundary can sit across the
        # outer pair, a place on either end counting a half as a tie does: for
        # a span of j cells, j - 1 inside and two halves, j. With all three in
        # one cell the count is the two ties' quarter, divided out but for the
        # outer pair's own half, so 2. Not 1/(span in years) -- see
        # exact_chain._span_prior_correction for why the continuum power is
        # wrong on a grid, and note that a brute force carrying the same
        # mistake as the solver is exactly how the earlier interval-quadrature
        # bug survived this suite.
        cells = np.arange(M)[None, :] - np.arange(M)[:, None]   # [b2, b0]
        with np.errstate(divide='ignore', invalid='ignore'):
            corr = np.where(cells > 0, 1.0 / np.where(cells > 0, cells, 1.0),
                            np.where(cells == 0, 2.0, 0.0))

        # ...times the limits factor, A.31's second term, which is the only finite
        # one when nothing constrains the outer sides. Written out here rather than
        # imported for the same reason as the count above it.
        support = [np.where(lik[d] >= lik[d].max() * ec.SUPPORT_THRESHOLD)[0]
                   for d in lik]
        young_bound = max(grid[sup[0]] for sup in support)
        old_bound = min(grid[sup[-1]] for sup in support)
        outer_span = grid[None, :] - grid[:, None]               # [b2, b0]
        g = outer_span - (young_bound - old_bound)
        corr = corr * np.where(g > 0, 1.0 / np.where(g > 0, g, 1.0), 1.0)

        # Explicit cube, axes (b2, b1, b0).
        for gi, ids in enumerate(chain.groups):
            for d in ids:
                with np.errstate(invalid='ignore'):
                    lg = np.where(np.isfinite(logT[gi]), logT[gi] - own(d), -np.inf)
                G = np.exp(ec._rescaled(lg))
                G = np.where(np.isfinite(G), G, 0.0)
                if gi == 0:
                    pair = (G[None, :, :] * T[1][:, :, None] * corr[:, None, :]).sum(axis=0)
                else:
                    pair = (T[0][None, :, :] * G[:, :, None] * corr[:, None, :]).sum(axis=2)
                q = np.cumsum(pair, axis=0)
                q = np.cumsum(q[:, ::-1], axis=1)[:, ::-1]
                # A date ON one of its boundaries counts half, as in the
                # trapezoidal interval integral the transfers are built from;
                # ON both, the two boundaries tied, it is the whole weight of
                # that cell -- a mass, so over the step to sit with densities.
                on_a_boundary = 0.5 * (pair.sum(axis=1) + pair.sum(axis=0))
                expected = lik[d] * (np.diagonal(q) - on_a_boundary + np.diagonal(pair) / dt)
                expected = expected / expected.sum()

                pairs = np.array(solved['measurements'][d]['probability_distribution'])
                where = np.searchsorted(grid, pairs[:, 0] - solved['grid']['step'] / 2)
                got = pairs[:, 1] / pairs[:, 1].sum()
                mine = expected[where] / expected[where].sum()
                overlap = float(np.minimum(got, mine).sum())
                assert overlap > 0.999, f'{d} overlap {overlap}'


class TestBoundaryTies:
    """
    Neighbouring Boundaries may share a grid cell at half weight
    (_boundary_pair_term), and the span correction counts arrangements the
    same way (log_interior_placements).
    """

    @pytest.mark.parametrize('k', [1, 2, 3, 4])
    def test_the_count_is_every_arrangement_with_each_tie_a_half(self, k):
        import itertools
        for j in range(0, 7):
            total = 0.0
            for inside in itertools.combinations_with_replacement(range(j + 1), k):
                places = (0,) + inside + (j,)
                ties = sum(a == b for a, b in zip(places, places[1:]))
                total += 0.5 ** ties
            if j == 0:
                total *= 2.0          # the outer pair's own half is not divided out
            assert np.exp(ec.log_interior_placements(np.array([j]), k)[0]) == \
                pytest.approx(total)

    def test_a_long_sequence_converges_with_the_square_of_the_step(self):
        """
        Nine phases, four of them empty, ten Boundaries: the shape where
        keeping Boundaries a cell apart cost the most. The worst gap in the
        probability of a Boundary being older than a given year, against a
        solve at a quarter of the step, falls fourfold as the step halves;
        kept apart it fell twofold, from 0.036 at 1.25 years.
        """
        layers = [[615, 630, 705], [], [690], [], [625, 645], [],
                  [550, 565], [595, 590, 620], [545]]
        children, specs = [_boundary('t_b0', 'Start')], []
        for i, ages in enumerate(layers):
            children.append(_phase(f't_p{i}', f'Layer {i}'))
            specs += [(f't_d{i}_{j}', age, 18, f't_p{i}') for j, age in enumerate(ages)]
            children.append(_boundary(f't_b{i + 1}', f'Top {i}'))
        tree = _sequence('t', children)
        dates = _dates(*specs)
        chain = ec.layout(tree, dates)
        bounds = inherited_bounds(tree)

        def cdfs(step):
            grid = np.arange(216.0, 846.0 + step / 2, step)
            _r, densities = ec._solve_on_grid(
                grid, dates, tree, chain, bounds, num_samples=50, seed=1,
                uniform_span_prior=True, estimate_durations=False, dispersal=None,
                interval_draws=50, resolution=5.0)
            years = np.arange(150.0, 1000.0, 0.05)
            out = []
            for mass in densities[-len(chain.boundary_ids):]:
                # Each point's mass spread over its own cell.
                edges = np.concatenate([[grid[0] - step / 2], grid + step / 2])
                cumulative = np.concatenate([[0.0], np.cumsum(mass / mass.sum())])
                out.append(np.interp(years, edges, cumulative))
            return np.array(out)

        fine = cdfs(0.3125)
        gaps = [float(np.abs(cdfs(step) - fine).max()) for step in (2.5, 1.25)]
        assert gaps[1] < 0.025, gaps
        assert gaps[0] / gaps[1] > 3.0, gaps


class TestEvidence:
    """
    The model's evidence (solve's log_evidence): the chain's total weight with
    every factor the message scaling and the transfer rescaling took out put
    back. Checked against explicit sums on grids small enough to enumerate,
    with nothing rescaled, so a factor dropped or counted twice would show.
    """

    @staticmethod
    def _small_grid():
        import unittest.mock as mock
        return (mock.patch.object(ec, 'MAX_GRID_POINTS', 70),
                mock.patch.object(ec, 'MIN_BOUNDARY_PAD_YEARS', 250.0),
                mock.patch.object(ec, 'BOUNDARY_WINDOW_FACTOR', 1.0))

    @staticmethod
    def _tables(solved, dates, tree):
        from app.services.model_tree import inherited_bounds
        grid = np.arange(solved['grid']['lower'],
                         solved['grid']['upper'] + solved['grid']['step'] / 2,
                         solved['grid']['step'])
        lik, cum, _parts = ec.date_tables(grid, dates, inherited_bounds(tree))
        return grid, lik, cum

    @staticmethod
    def _log_limits(grid, lik):
        support = [np.where(l >= l.max() * ec.SUPPORT_THRESHOLD)[0] for l in lik.values()]
        young_bound = max(grid[sup[0]] for sup in support)
        old_bound = min(grid[sup[-1]] for sup in support)
        g = (grid[None, :] - grid[:, None]) - (young_bound - old_bound)   # [b_last, b_first]
        return np.where(g > 0, -np.log(np.where(g > 0, g, 1.0)), 0.0)

    def test_one_group_matches_an_explicit_sum(self):
        dates = _dates(('ev1', 3000, 30, 'p'), ('ev2', 3040, 30, 'p'), ('ev3', 3080, 30, 'p'))
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        patches = self._small_grid()
        with patches[0], patches[1], patches[2]:
            solved = ec.solve(dates, tree, ec.layout(tree, dates), num_samples=50)
            grid, lik, cum = self._tables(solved, dates, tree)
        dt = grid[1] - grid[0]
        span = grid[None, :] - grid[:, None]                         # [younger, older]
        with np.errstate(divide='ignore', invalid='ignore'):
            log_t = np.where(span > 0, -len(dates) * np.log(np.where(span > 0, span, 1.0)), -np.inf)
        for d in dates:
            log_t = log_t + ec._log_interval_integral(cum[d['id']], lik[d['id']], dt)
        # Both boundaries in one cell: the tie's half, and the dates there.
        with np.errstate(divide='ignore'):
            np.fill_diagonal(log_t, np.log(0.5) + sum(np.log(lik[d['id']]) for d in dates))
        log_t = log_t + self._log_limits(grid, lik)
        finite = np.isfinite(log_t)
        peak = log_t[finite].max()
        expected = peak + np.log(np.exp(log_t[finite] - peak).sum())
        assert solved['log_evidence'] == pytest.approx(expected, abs=1e-8)

    def test_two_groups_with_the_span_correction_match_the_cube(self):
        dates = _dates(('ew_a', 3300, 30, 'p0'), ('ew_b', 3270, 30, 'p0'),
                       ('ew_c', 3050, 30, 'p1'), ('ew_d', 3020, 30, 'p1'))
        tree = _sequence('s', [_boundary('b0'), _phase('p0'), _boundary('b1'),
                               _phase('p1'), _boundary('b2')])
        chain = ec.layout(tree, dates)
        patches = self._small_grid()
        with patches[0], patches[1], patches[2]:
            solved = ec.solve(dates, tree, chain, num_samples=50)
            grid, lik, cum = self._tables(solved, dates, tree)
        dt = grid[1] - grid[0]
        M = len(grid)
        span = grid[None, :] - grid[:, None]
        log_t = []
        for ids in chain.groups:
            with np.errstate(divide='ignore', invalid='ignore'):
                t = np.where(span > 0, -len(ids) * np.log(np.where(span > 0, span, 1.0)), -np.inf)
            for i in ids:
                t = t + ec._log_interval_integral(cum[i], lik[i], dt)
            with np.errstate(divide='ignore'):
                np.fill_diagonal(t, np.log(0.5) + sum(np.log(lik[i]) for i in ids))
            log_t.append(t)
        cells = np.arange(M)[None, :] - np.arange(M)[:, None]          # [b2, b0]
        # One interior boundary across j cells, a place on either end a tie at
        # a half: j places. All three in one cell: 2 (see
        # TestBoundaryTies for the count in general).
        with np.errstate(divide='ignore', invalid='ignore'):
            log_corr = np.where(cells > 0, -np.log(np.where(cells > 0, cells, 1.0)),
                                np.where(cells == 0, np.log(2.0), -np.inf))
        log_corr = log_corr + self._log_limits(grid, lik)
        # The cube, axes (b2, b1, b0), in the log.
        cube = log_t[0][None, :, :] + log_t[1][:, :, None] + log_corr[:, None, :]
        finite = np.isfinite(cube)
        peak = cube[finite].max()
        expected = peak + np.log(np.exp(cube[finite] - peak).sum())
        assert solved['log_evidence'] == pytest.approx(expected, abs=1e-8)

    def test_a_long_chain_neither_underflows_nor_overflows(self):
        children, dates = [_boundary('l0')], []
        for i in range(40):
            children += [_phase(f'lp{i}'), _boundary(f'l{i + 1}')]
            dates += _dates((f'ld{i}', 4000 - 40 * i, 30, f'lp{i}'))
        tree = _sequence('s', children)
        solved = ec.solve(dates, tree, ec.layout(tree, dates), num_samples=20,
                          uniform_span_prior=False, interval_draws=50)
        assert np.isfinite(solved['log_evidence'])


class TestModelEvidence:
    """
    The evidence that compares between runs (solve's `evidence`): the data's
    weight over the prior's, with the prior bounded by the outer Boundaries.
    """

    @staticmethod
    def _bounded(dates, young, old, middle=()):
        first, last = _boundary('b0'), _boundary('b1')
        first['tpq_age_bp'], last['taq_age_bp'] = old, young
        tree = _sequence('s', [first, _phase('p'), last])
        return tree, ec.layout(tree, dates)

    @staticmethod
    def _by_integration(dates, young, old, step=0.25):
        """One Phase between two Boundaries uniform over [young, old], each date
        uniform between them, written from the model statement."""
        grid = np.arange(young, old + step / 2, step)
        cums = []
        for m in dates:
            lik = ec.event_likelihood(grid, m)
            cums.append(np.concatenate([[0], np.cumsum((lik[1:] + lik[:-1]) / 2 * step)]))
        total = 0.0
        for j in range(1, len(grid)):
            span = grid[j] - grid[:j]
            term = np.ones(j)
            for cum in cums:
                term = term * (cum[j] - cum[:j]) / span
            total += term.sum() * step * step
        return np.log(2.0 / (old - young) ** 2 * total)

    @pytest.mark.parametrize('young, old', [(3000, 3500), (3050, 3400), (3100, 3300)])
    def test_it_is_the_integral_over_the_bounded_prior(self, young, old):
        dates = _dates(('mv1', 3000, 30, 'p'), ('mv2', 3040, 30, 'p'), ('mv3', 3080, 30, 'p'))
        tree, chain = self._bounded(dates, young, old)
        solved = ec.solve(dates, tree, chain, num_samples=50, uniform_span_prior=False)
        evidence = solved['evidence']
        assert evidence['available']
        assert evidence['log'] == pytest.approx(self._by_integration(dates, young, old),
                                                abs=0.01)
        assert evidence['log10'] == pytest.approx(evidence['log'] / np.log(10.0))
        assert (evidence['lower'], evidence['upper']) == (young, old)

    @pytest.mark.parametrize('old, young, tolerance', [
        (3800, 2900, 0.01),     # limits well clear of the dates
        (3450, 3250, 0.05),     # limits cutting into the posterior
    ], ids=['wide', 'tight'])
    def test_it_does_not_depend_on_the_grid(self, old, young, tolerance):
        """The same model's evidence at a 1.25-yr step against a grid four
        times finer. Where the limits cut into the posterior the error is first
        order in the step, from the cells at the limits; elsewhere second."""
        import unittest.mock as mock
        dates = _dates(('mg1', 3300, 30, 'p0'), ('mg2', 3270, 30, 'p0'),
                       ('mg3', 3050, 30, 'p1'), ('mg4', 3020, 30, 'p1'))
        tree = _sequence('s', [_boundary('b0'), _phase('p0'), _boundary('b1'),
                               _phase('p1'), _boundary('b2')])
        tree['tpq_age_bp'], tree['taq_age_bp'] = old, young
        found = {}
        for points in ((old - young) / 1.25, 4 * (old - young) / 1.25):
            with mock.patch.object(ec, 'EVIDENCE_MAX_GRID_POINTS', points), \
                    mock.patch.object(ec, 'MAX_GRID_POINTS', max(points, 2000)):
                evidence = ec.solve(dates, tree, ec.layout(tree, dates),
                                    num_samples=20)['evidence']
            found[evidence['step']] = evidence['log']
        assert sorted(found) == [0.3125, 1.25]
        assert abs(found[1.25] - found[0.3125]) < tolerance, found

    def test_limits_on_the_sequence_bound_its_boundaries(self):
        """The editor puts limiters on Sequences and Phases, not Boundaries."""
        dates = _dates(('mq1', 3000, 30, 'p'), ('mq2', 3040, 30, 'p'))
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        tree['tpq_age_bp'], tree['taq_age_bp'] = 3400, 2900
        evidence = ec.solve(dates, tree, ec.layout(tree, dates), num_samples=50)['evidence']
        by_boundaries = ec.solve(dates, *self._bounded(dates, 2900, 3400),
                                 num_samples=50)['evidence']
        assert evidence['available']
        assert evidence['log'] == pytest.approx(by_boundaries['log'], abs=1e-9)

    def test_it_is_not_given_for_an_unbounded_model(self):
        dates = _dates(('mu1', 3000, 30, 'p'))
        tree = _sequence('s', [_boundary('b0', 'Start'), _phase('p'), _boundary('b1', 'End')])
        evidence = ec.solve(dates, tree, ec.layout(tree, dates), num_samples=50)['evidence']
        assert not evidence['available']
        assert 'Start' in evidence['reason'] and 'TPQ' in evidence['reason']

    def test_it_covers_a_model_with_an_outlier_date(self):
        dates = _dates(('ml1', 3000, 30, 'p'), ('ml2', 3040, 30, 'p'))
        flagged = [dict(d, outlier_prior=0.05) if d['id'] == 'ml1' else d for d in dates]
        tree, chain = self._bounded(flagged, 2900, 3300)
        evidence = ec.solve(flagged, tree, chain, num_samples=50)['evidence']
        plain = ec.solve(dates, *self._bounded(dates, 2900, 3300), num_samples=50)['evidence']
        assert evidence['available']
        # A small allowance for a date being wrong costs a little on data that agree.
        assert 0 < plain['log'] - evidence['log'] < 1.0

    def test_its_key_names_the_readings_and_nothing_else(self):
        a = _dates(('mk1', 3000, 30, 'p'), ('mk2', 3040, 25, 'p'))
        b = _dates(('other-2', 3040, 25, 'p'), ('other-1', 3000, 30, 'p'))
        c = _dates(('mk1', 3000, 30, 'p'), ('mk2', 3040, 30, 'p'))
        assert ec._readings_key(a) == ec._readings_key(b)
        assert ec._readings_key(a) != ec._readings_key(c)


class TestTheCorrectionIsExact:
    """
    The UniformSpanPrior factor against a closed form, with no fixture and no
    OxCal involved.

    Switch the likelihoods off and the reported posterior IS the prior, which is
    known exactly. Each group's dates then have volume exactly span^K -- the
    trapezoid rule integrates a constant exactly -- so every span factor cancels
    and what is left is the correction against the interior boundaries' freedom.
    With the correction right those cancel too, leaving the ordered pair
    (b_first, b_last) UNIFORM, so the oldest boundary's marginal simply counts its
    admissible partners:

        p(i) proportional to (i + 1/2)

    -- every cell below it, and its own at the half a tie between the two
    carries, whatever the number of boundaries between them: the k interior
    ones may share cells with them and with each other, at the same half, and
    the correction counts them so (log_interior_placements). With boundaries
    kept a cell apart, as they once were, it was i - k, the younger boundary
    leaving room for the k interior ones. A continuum reference would predict
    p(i) ∝ i, a mean step/6 away; the old (b_first - b_last)^-(n-2) form was
    quietly delivering k*step/3.

    This is the test that would have caught that form immediately, and it is
    cheap, so it is here rather than in a scratch script.
    """

    LO, HI = 2800.0, 3600.0

    def _flat(self, M):
        """
        Patch the likelihood, the grid, and the containment limits; undo afterwards.

        The limits go too because this class is about the INTERIOR-BOUNDARY factor
        alone, and a flat likelihood has no meaningful extent -- the support it
        implies is just the grid, which is precisely what exact_chain.pair_limits
        refuses to treat as a constraint. Left in, it would apply a second factor of
        1/(span + window) and the closed form below would stop being the closed form.
        """
        grid = np.linspace(self.LO, self.HI, M)
        flat = np.ones(M)
        saved = (ec._date_likelihood, ec._build_grid, ec.containment_limits,
                 ec._refined_grid)
        ec._date_likelihood = lambda g, m, b: flat.copy()
        ec._build_grid = lambda ms, eb, ng=1: grid.copy()
        ec.containment_limits = lambda g, inl, ids: (-np.inf, np.inf)
        # This grid and no other: the likelihood above is sized to it.
        ec._refined_grid = lambda *args, **kwargs: None
        return grid, saved

    def _restore(self, saved):
        (ec._date_likelihood, ec._build_grid, ec.containment_limits,
         ec._refined_grid) = saved

    def _model(self, n, dates_per_group):
        children, dates = [], []
        for i in range(n - 1):
            children.append(_boundary(f'b{i}'))
            children.append(_phase(f'p{i}'))
            for j in range(dates_per_group[i]):
                dates.append(_dates((f'd{i}_{j}', 3000, 25, f'p{i}'))[0])
        children.append(_boundary(f'b{n - 1}'))
        return _sequence('s', children), dates

    def _oldest_marginal(self, n, dates_per_group, M=301):
        grid, saved = self._flat(M)
        try:
            tree, dates = self._model(n, dates_per_group)
            chain = ec.layout(tree, dates)
            assert chain is not None
            solved = ec.solve(dates, tree, chain, num_samples=50,
                              uniform_span_prior=True)
            pairs = np.asarray(solved['boundaries'][chain.boundary_ids[0]][
                'posterior']['probability_distribution'], float)
            order = np.argsort(pairs[:, 0])
            density = np.interp(grid, pairs[order, 0], pairs[order, 1],
                                left=0.0, right=0.0)
            return grid, density / density.sum()
        finally:
            self._restore(saved)

    @pytest.mark.parametrize('n,dates_per_group', [
        (3, [2, 2]), (3, [1, 3]), (4, [2, 1, 1]), (5, [1, 1, 1, 1]),
    ])
    def test_the_induced_prior_is_the_closed_form(self, n, dates_per_group):
        grid, got = self._oldest_marginal(n, dates_per_group)
        want = np.arange(len(grid)) + 0.5
        want = want / want.sum()
        # Total variation, and the mean in years -- the second because a fraction
        # of a percent of the first says nothing about whether it can move an
        # answer, and it turned out it could: the old form was worth +2.04 years
        # at n = 3 and +7.99 at n = 5.
        assert 0.5 * np.abs(got - want).sum() < 1e-6
        shift = float((grid * got).sum() - (grid * want).sum())
        assert abs(shift) < 1e-6, shift

    def test_it_does_not_depend_on_how_the_dates_are_split(self):
        """Every K_i cancels against its own group's span factor, so the prior
        cannot see the split. Asserted separately because it holds exactly, and
        an implementation that fitted the exponent to a fixture would break it."""
        _g1, a = self._oldest_marginal(3, [2, 2])
        _g2, b = self._oldest_marginal(3, [1, 3])
        assert 0.5 * np.abs(a - b).sum() < 1e-9

    def test_a_continuum_reference_would_have_missed_it(self):
        """
        Guard on the test above. The closed form differs from its continuum limit
        by a mean of step/6, so if someone 'simplifies' the reference to p ∝ i
        the check silently weakens to a tolerance of a fraction of a year.
        """
        M = 301
        grid, saved = self._flat(M)
        self._restore(saved)
        step = grid[1] - grid[0]
        discrete = np.arange(M) + 0.5
        discrete /= discrete.sum()
        continuum = np.arange(M, dtype=float)
        continuum /= continuum.sum()
        gap = float((grid * discrete).sum() - (grid * continuum).sum())
        assert gap == pytest.approx(-step / 6.0, rel=0.05), gap


class TestTheLimitsFactor:
    """
    The other half of OxCal's UniformSpanPrior: the limits factor of Bronk Ramsey
    2008 A.31, equivalently 2009a eq 29 and 2001 eq 7.

    It exists because constraining where a phase's boundaries may sit biases the
    prior on its span, and it divides that bias out. The subtlety, and the reason
    the sign of it is easy to get backwards, is that A.31 is a MIN of four terms:
    two of them depend on the span in opposite directions, and which one is
    smallest decides which way the factor pushes.

    What it is NOT allowed to depend on is the solver's grid. The grid is padded by
    six times the dates' own width so the boundaries have room, so taking its edges
    for llim/ulim would make this factor an artefact of that padding -- measured, it
    behaved exactly so, contributing +1.20 years at a 600-year window and +0.13 at
    4,800, halving with each doubling. An unconstrained boundary is unbounded, g is
    then infinite, and 1/g is constant in the span and drops out. Hence the first
    test below.
    """

    LO, HI, M = 2800.0, 3600.0, 401

    def _flat(self):
        grid = np.linspace(self.LO, self.HI, self.M)
        flat = np.ones(self.M)
        saved = (ec._date_likelihood, ec._build_grid, ec._refined_grid)
        ec._date_likelihood = lambda g, m, b: flat.copy()
        ec._build_grid = lambda ms, eb, ng=1: grid.copy()
        ec._refined_grid = lambda *args, **kwargs: None
        return grid, saved

    def _model(self, tpq=None, taq=None):
        dates = _dates(*[(f'lf{i}', 3000, 25, 'p0') for i in range(4)])
        tree = _sequence('s', [_boundary('b0'), _phase('p0'), _boundary('b1')])
        tree['tpq_age_bp'], tree['taq_age_bp'] = tpq, taq
        return tree, dates

    def test_the_grid_never_supplies_a_limit(self):
        """
        An absent TPQ/TAQ is an absent constraint, so boundary_limits reports
        infinities and the solver's own grid edges are never stood in for them.

        The factor is not inert on an unconstrained model, though: the DATES bound
        the boundaries they sit between, which is how OxCal derives these as well
        (see pair_limits). This test asserted the opposite for one commit, reasoning
        that no constraint meant no limits -- the dates are the constraint. What
        survives is the property that actually matters: the answer must not depend on
        how much padding the grid carries, that being a numerical choice rather than
        a statement about the model.
        """
        tree, dates = self._model()
        bounds = inherited_bounds(tree)
        assert ec.boundary_limits(bounds, 'b0') == (np.inf, -np.inf)
        grid = np.linspace(self.LO, self.HI, self.M)
        assert ec._log_limits_factor(grid, (np.inf, -np.inf), (np.inf, -np.inf)) is None

        # The limits themselves, at two window widths: these are what the grid could
        # leak into, and they are the right thing to pin. The factor's effect on the
        # span cannot be asserted window-independently -- a boundary enclosing four
        # identical dates has a heavy tail, so the span moves with the window on its
        # own account and the factor moves proportionally with it.
        derived = []
        for width in (700.0, 2800.0):
            axis = np.arange(3200.0 - width / 2, 3200.0 + width / 2, 1.0)
            inliers = {}
            for d in dates:
                inliers[d['id']] = ec._date_likelihood(
                    axis, d, ec._effective_date_bounds(d, bounds))
            derived.append(ec.containment_limits(axis, inliers,
                                                 [d['id'] for d in dates]))
        assert derived[0] == pytest.approx(derived[1], abs=1.5), derived

    def test_with_real_constraints_it_matches_the_closed_form(self):
        """
        Where the factor does apply, against a reference built from the model
        statement rather than from this solver: flat likelihoods make each group's
        date volume exactly span^K, cancelling its span factor, so the prior over
        the boundary pair is 1/g and nothing else -- the pair in one cell at
        the half a tie carries.
        """
        tpq, taq = 3400.0, 3000.0
        tree, dates = self._model(tpq=tpq, taq=taq)
        grid, saved = self._flat()
        try:
            bounds = inherited_bounds(tree)
            assert ec.boundary_limits(bounds, 'b0') == (tpq, taq)
            solved = ec.solve(dates, tree, ec.layout(tree, dates), num_samples=50,
                              uniform_span_prior=True)
            pairs = np.asarray(
                solved['boundaries']['b0']['posterior']['probability_distribution'],
                float)
            order = np.argsort(pairs[:, 0])
            got = np.interp(grid, pairs[order, 0], pairs[order, 1], left=0.0, right=0.0)
            got = got / got.sum()

            factor = ec._log_limits_factor(grid, (tpq, taq), (tpq, taq))
            assert factor is not None
            weight = np.where(np.isfinite(factor), np.exp(factor), 0.0)
            span = grid[None, :] - grid[:, None]
            inside = (grid >= taq) & (grid <= tpq)
            # The pair in one cell is a tie, at half weight.
            weight = np.where(inside[None, :] & inside[:, None],
                              weight * np.where(span > 0, 1.0, np.where(span == 0, 0.5, 0.0)),
                              0.0)
            want = weight.sum(axis=0)
            want = want / want.sum()
            assert 0.5 * np.abs(got - want).sum() < 1e-6
        finally:
            ec._date_likelihood, ec._build_grid, ec._refined_grid = saved

    def test_which_term_of_the_min_wins_sets_the_direction(self):
        """
        The property that makes this factor confusing, pinned so the sign cannot be
        silently flipped. Shared limits leave the first term smallest, so 1/g grows
        with the span and favours a longer one; limits tight and disjoint leave the
        second smallest, and it favours a shorter one.
        """
        grid = np.linspace(0.0, 100.0, 11)
        shared = ec._log_limits_factor(grid, (100.0, 0.0), (100.0, 0.0))
        # g = window - span, so -log g RISES with the span
        assert shared[0, 9] > shared[0, 1]

        disjoint = ec._log_limits_factor(grid, (100.0, 60.0), (40.0, 0.0))
        # g = span - 20 over the low spans, so -log g FALLS with the span
        assert disjoint[0, 3] > disjoint[0, 5]

    def test_a_one_sided_constraint_contributes_nothing(self):
        """
        Each span-dependent term of A.31 needs BOTH of its limits to be real. A TPQ
        with no TAQ leaves neither term finite, so the factor is still constant --
        which is worth pinning, because using the grid for the missing half is
        precisely the mistake this class exists to prevent.
        """
        grid = np.linspace(self.LO, self.HI, self.M)
        assert ec._log_limits_factor(grid, (3400.0, -np.inf),
                                     (3400.0, -np.inf)) is None

    def test_a_gap_that_is_zero_up_to_rounding_counts_as_zero(self):
        """
        The span and the limits are both differences of grid ages, so where they
        are equal g is zero only in exact arithmetic. On steps binary cannot
        represent it came out at +/-1e-13, and the positive half weighted that
        span by up to e^31, where a real gap is never less than one step.
        """
        for step in (5.0 / 3.0, 5.0 / 6.0, 5.0 / 7.0, 5.0 / 9.0):
            grid = 236.41666666666669 + step * np.arange(600)
            span = grid[None, :] - grid[:, None]
            for young, old in ((50, 250), (120, 400), (33, 333)):
                # Limits as the dates set them: the older Boundary no younger
                # than grid[old], the younger no older than grid[young].
                factor = ec.log_limits_factor_of_span(
                    span, (np.inf, grid[old]), (grid[young], -np.inf))
                assert factor[span > 0].max() <= max(0.0, -np.log(step)) + 1e-9, \
                    (step, young, old)

    def test_a_phase_is_not_squeezed_onto_a_rounding_error(self):
        """
        What that did to an ordinary model: eleven dates in one phase, the
        oldest and youngest too far apart to overlap, on the 5/3-year step the
        solver picks for it. The spike held the two Boundaries 86 years apart
        rather than 445, and Start came back at 3882 cal BP instead of 4071.
        Checked against the same model on a step binary represents exactly,
        where no difference of grid ages rounds.
        """
        ages = [3391, 3460, 3470, 3533, 3566, 3566, 3589, 3639, 3645, 3650, 3732]
        dates = _dates(*[(f'rz{i}', age, 20, 'rz_p') for i, age in enumerate(ages)])
        tree = _sequence('rz', [_boundary('rz_b0', 'Start'), _phase('rz_p'),
                                _boundary('rz_b1', 'End')])
        chain = ec.layout(tree, dates)
        solved = ec.solve(dates, tree, chain, num_samples=50, interval_draws=50)

        step = 1.25
        lower, upper = solved['grid']['lower'], solved['grid']['upper']
        exact = np.round(lower / step) * step + step * np.arange(int((upper - lower) / step) + 1)
        reference, _densities = ec._solve_on_grid(
            exact, dates, tree, chain, inherited_bounds(tree), num_samples=50, seed=1,
            uniform_span_prior=True, estimate_durations=False, dispersal=None,
            interval_draws=50, resolution=5.0)
        for bid in chain.boundary_ids:
            got = solved['boundaries'][bid]['posterior']['posterior_mean']
            want = reference['boundaries'][bid]['posterior']['posterior_mean']
            assert got == pytest.approx(want, abs=2.0), bid


class TestTheShortcutsChangeNothing:
    """
    Two ways the solver avoids work it used to do, each held to giving the
    answer the longer way gave.
    """

    @staticmethod
    def _three_phases(outlier_prior=0.0):
        """Three phases of three dates: small, and refined to a finer second grid."""
        specs = []
        for k, ages in enumerate([(3010, 2995, 3020), (2950, 2965, 2940), (2900, 2890, 2915)]):
            specs += [(f'tp_{k}{j}', age, 25, f'tp_p{k}') for j, age in enumerate(ages)]
        dates = [dict(d, outlier_prior=outlier_prior) for d in _dates(*specs)]
        tree = _sequence('tp', [_boundary('tp_b0'), _phase('tp_p0'), _boundary('tp_b1'),
                                _phase('tp_p1'), _boundary('tp_b2'), _phase('tp_p2'),
                                _boundary('tp_b3')])
        return tree, dates

    def test_the_straddling_weight_is_the_double_cumulative_sum(self):
        """
        A date's marginal needs, for each cell, the weight of the boundary pairs
        either side of it. That was the diagonal of two M x M cumulative sums;
        it is now read from the row and column sums, which holds because no
        pair has its older boundary below its younger.
        """
        rng = np.random.default_rng(3)
        size = 300
        W = np.triu(rng.random((size, size)) * (rng.random((size, size)) > 0.5))
        W *= np.exp(-0.05 * np.arange(size))[None, :]     # falling six decades towards the old end
        want = np.cumsum(np.cumsum(W, axis=0)[:, ::-1], axis=1)[:, ::-1].diagonal()
        got = ec._straddling(W.sum(axis=1), W.sum(axis=0))
        np.testing.assert_allclose(got, want, rtol=1e-9, atol=1e-12 * want.max())

    @pytest.mark.parametrize('outlier_prior', [0.0, 0.02])
    def test_the_boundaries_alone_place_the_second_grid(self, outlier_prior):
        """
        solve()'s first pass reports only the Boundaries. Every date lies between
        the outermost two, so their tails reach at least as far as any date's,
        and the second grid comes out exactly where every density would put it.
        """
        tree, dates = self._three_phases(outlier_prior)
        chain = ec.layout(tree, dates)
        bounds = inherited_bounds(tree)
        grid = ec._build_grid(dates, bounds, len(chain.groups))
        options = dict(num_samples=50, seed=1, uniform_span_prior=True,
                       estimate_durations=False, dispersal=None, interval_draws=50,
                       resolution=5.0)
        _none, boundaries = ec._solve_on_grid(grid, dates, tree, chain, bounds,
                                              boundaries_only=True, **options)
        _full, everything = ec._solve_on_grid(grid, dates, tree, chain, bounds, **options)
        assert len(boundaries) == len(chain.boundary_ids) < len(everything)
        from_boundaries = ec._refined_grid(grid, boundaries, dates, bounds, len(chain.groups))
        from_everything = ec._refined_grid(grid, everything, dates, bounds, len(chain.groups))
        assert from_boundaries is not None
        assert np.array_equal(from_boundaries, from_everything)

    def test_a_rejected_second_grid_returns_the_whole_first_pass(self, monkeypatch):
        """The first pass is solved in full only when it is the answer."""
        tree, dates = self._three_phases()
        chain = ec.layout(tree, dates)
        refined = ec.solve(dates, tree, chain, num_samples=50, interval_draws=50)
        assert 'first_pass_step' in refined['grid']

        monkeypatch.setattr(ec, 'REFINED_EDGE_MASS', -1.0)
        solved = ec.solve(dates, tree, chain, num_samples=50, interval_draws=50)
        assert 'first_pass_step' not in solved['grid']
        assert solved['grid']['step'] == refined['grid']['first_pass_step']
        assert set(solved['measurements']) == {d['id'] for d in dates}
        assert set(solved['boundaries']) == set(chain.boundary_ids)
        assert all(len(draws) == 50 for draws in solved['joint_draws'].values())


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


class TestReportingResolution:
    """
    An analysis can ask for its ranges at a step other than the default five
    years -- OxCal's Options Resolution. Only the reporting follows it.
    """

    def _solve(self, **kwargs):
        dates = _dates(('rr_a', 3000, 30, 'p'), ('rr_b', 3040, 30, 'p'),
                       ('rr_c', 3080, 30, 'p'))
        tree = _sequence('s', [_boundary('b0'), _phase('p'), _boundary('b1')])
        chain = ec.layout(tree, dates)
        return dates, ec.solve(dates, tree, chain, num_samples=200, **kwargs)

    @pytest.mark.parametrize('step', [1, 2, 10])
    def test_ranges_land_on_the_requested_step(self, step):
        dates, solved = self._solve(resolution=step)
        posteriors = [solved['measurements'][d['id']] for d in dates]
        posteriors += [b['posterior'] for b in solved['boundaries'].values()]
        for posterior in posteriors:
            for key in ('hpd_68_ranges', 'hpd_95_ranges'):
                for r in posterior[key]:
                    assert r['min'] % step == pytest.approx(0.0, abs=1e-6)
                    assert r['max'] % step == pytest.approx(0.0, abs=1e-6)
                    assert r['max'] - r['min'] >= step

    def test_the_default_is_five_years(self):
        dates, default = self._solve()
        _again, explicit = self._solve(resolution=ec.REPORTING_STEP_YEARS)
        for d in dates:
            assert (default['measurements'][d['id']]['hpd_95_ranges']
                    == explicit['measurements'][d['id']]['hpd_95_ranges'])

    def test_only_the_reporting_moves(self):
        """The posteriors, and so the agreement indices and draws read off
        them, are the same whatever step the ranges are quoted at."""
        dates, five = self._solve()
        _again, one = self._solve(resolution=1)
        assert one['grid'] == five['grid']
        for d in dates:
            a, b = five['measurements'][d['id']], one['measurements'][d['id']]
            assert a['probability_distribution'] == b['probability_distribution']
            assert a['posterior_mean'] == b['posterior_mean']
        for date_id, draws in five['joint_draws'].items():
            np.testing.assert_array_equal(draws, one['joint_draws'][date_id])

    def test_a_finer_step_gives_tighter_ranges(self):
        """The point of asking for one: a range quoted to five years carries
        up to five years of rounding at each end."""
        dates, five = self._solve()
        _again, one = self._solve(resolution=1)
        for d in dates:
            w5 = sum(r['max'] - r['min'] for r in five['measurements'][d['id']]['hpd_95_ranges'])
            w1 = sum(r['max'] - r['min'] for r in one['measurements'][d['id']]['hpd_95_ranges'])
            assert w1 <= w5

    @pytest.mark.parametrize('step', [1, 10])
    def test_durations_follow_the_step(self, step):
        _dates_, solved = self._solve(resolution=step, estimate_durations=True)
        durations = solved['intervals']['durations']
        assert durations
        for record in durations:
            quoted = record['years']['step']
            assert quoted >= step
            assert quoted % step == pytest.approx(0.0, abs=1e-6)


class TestOutlierModel:
    """
    The outlier model, against the OxCal 4.4 run in
    tests/fixtures/oxcal_outlier -- Dune Section, 16 dates in one phase,
    LB10002 at 970 +/- 47 BP against the rest at 537-660, with
    Outlier_Model("General", T(5), U(0,4), "t") and a 5% prior on each date.

    A Bernoulli indicator per date, integrated out exactly, which is what
    makes it free: the indicator touches only that date's own likelihood, so
    the mixture substitutes straight in and the chain never changes.
    """

    @pytest.fixture(scope='class')
    def solved(self):
        import dune_section_fixture as sf
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

        The second pass (solve's _refined_grid) narrows the grid to where the
        posterior is, which is the same question again, so it is held to the
        same standard: the refined answer must match the first pass's.
        """
        sf, res = solved
        import unittest.mock as mock
        samples, tree = sf.measurements(), sf.model_tree()
        chain = ec.layout(tree, samples)
        with mock.patch.object(ec, '_refined_grid', lambda *args, **kwargs: None):
            narrow = ec.solve(samples, tree, chain, num_samples=500)
            with mock.patch.object(ec, 'BOUNDARY_WINDOW_FACTOR', 30.0):
                wide = ec.solve(samples, tree, chain, num_samples=500)
        assert wide['grid']['upper'] - wide['grid']['lower'] > \
            2 * (narrow['grid']['upper'] - narrow['grid']['lower'])
        assert 'first_pass_step' in res['grid']
        for m in samples:
            narrow_p = narrow['measurements'][m['id']]['outlier_probability']
            wide_p = wide['measurements'][m['id']]['outlier_probability']
            refined_p = res['measurements'][m['id']]['outlier_probability']
            assert wide_p == pytest.approx(narrow_p, abs=0.05), m['id']
            assert refined_p == pytest.approx(narrow_p, abs=0.05), m['id']

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


def _spelled_out(element):
    """`element` with every Sequence directly inside a Sequence replaced by its
    children, at any depth -- the same model written flat."""
    children = []
    for child in element.get('children') or []:
        child = _spelled_out(child)
        if (element.get('element_type') == 'sequence'
                and child.get('element_type') == 'sequence'):
            children.extend(child['children'])
        else:
            children.append(child)
    return dict(element, children=children)


class TestSequenceOfSequences:
    """
    Sequence['Hillside then DS': Sequence[B, P, B], Sequence[B, P, B, P, B]]
    -- each inner Sequence bracketed by its own Boundaries. It plans as two
    paths of groups with only an ordering across the gap between them, which
    is exactly an empty group there, so it is the same chain as the children
    written out in place. It used to be refused as branching and sent to the
    sampler.
    """

    def _nested(self):
        first = _sequence('nen', [_boundary('b0', 'Start N'), _phase('pn', 'Hillside'),
                                  _boundary('b1', 'End N')], name='Hillside')
        second = _sequence('ds', [_boundary('b2', 'Start S'), _phase('ps1', 'DS 1'),
                                   _boundary('b3', 'Mid S'), _phase('ps2', 'DS 2'),
                                   _boundary('b4', 'End S')], name='DS')
        tree = _sequence('root', [first, second], name='Hillside then DS')
        dates = _dates(('n1', 3230, 30, 'pn'), ('n2', 3190, 30, 'pn'),
                       ('s1', 3060, 30, 'ps1'), ('s2', 3090, 30, 'ps1'),
                       ('s3', 2960, 30, 'ps2'), ('s4', 2990, 30, 'ps2'))
        return tree, dates

    def test_it_is_a_chain(self):
        tree, dates = self._nested()
        chain, reason = ec.layout_with_reason(tree, dates)
        assert reason is None
        assert chain.boundary_ids == ['b0', 'b1', 'b2', 'b3', 'b4']
        assert chain.groups == [['n1', 'n2'], [], ['s1', 's2'], ['s3', 's4']]

    def test_it_is_the_same_chain_as_spelled_out_in_place(self):
        tree, dates = self._nested()
        nested = ec.layout(tree, dates)
        flat = ec.layout(_spelled_out(tree), dates)
        assert nested.boundary_ids == flat.boundary_ids
        assert nested.groups == flat.groups

    def test_deeper_nesting_is_still_one_line(self):
        tree, dates = self._nested()
        third = _sequence('late', [_boundary('b5'), _phase('pl'), _boundary('b6')])
        deeper = _sequence('top', [tree, third])
        dates = dates + _dates(('l1', 2850, 30, 'pl'))
        chain = ec.layout(deeper, dates)
        assert chain.boundary_ids == ['b0', 'b1', 'b2', 'b3', 'b4', 'b5', 'b6']
        assert chain.groups[-2:] == [[], ['l1']]

    def test_the_plan_is_the_same_model(self):
        """
        plan_model on the nested tree: the same dated groups, the same
        Boundaries, and every ordering the flat tree states also forced --
        the flat tree's extra empty group carries no factor of its own.
        """
        from app.services.model_tree import plan_model

        tree, dates = self._nested()
        nested, flat = plan_model(tree, dates), plan_model(_spelled_out(tree), dates)

        def groups(plan):
            return sorted((g.older_boundary_id, g.younger_boundary_id, tuple(g.date_ids))
                          for g in plan.span_groups if g.date_ids)

        assert groups(nested) == groups(flat)
        assert nested.boundary_names == flat.boundary_names
        assert [(g.older_boundary_id, g.younger_boundary_id)
                for g in flat.span_groups if not g.date_ids] == [('b1', 'b2')]

        def pairs(plan):
            return {(o, y) for olds, youngs in plan.ordering
                    for _ko, o in olds for _ky, y in youngs}

        assert pairs(flat) <= pairs(nested)

    def test_the_sampler_applies_the_same_span_prior(self):
        """
        The gibbs Model works from the unstitched plan. Its UniformSpanPrior
        components must still be the single one exact_chain applies: all five
        Boundaries, oldest b0, youngest b4, exponent n - 2 = 3.
        """
        from app.services import gibbs

        tree, dates = self._nested()
        exact = ec.solve(dates, tree, ec.layout(tree, dates), num_samples=50)
        grid = np.arange(exact['grid']['lower'], exact['grid']['upper'] + 1e-9,
                         exact['grid']['step'])
        nested = gibbs.Model(dates, tree, grid=grid)
        flat = gibbs.Model(dates, _spelled_out(tree), grid=grid)
        assert nested.span_priors == [('b0', 'b4', 3)]
        assert nested.span_priors == flat.span_priors
        for a, b in zip(nested.span_prior_rows, flat.span_prior_rows):
            np.testing.assert_array_equal(a, b)

    def test_it_solves_identically_to_spelled_out(self):
        tree, dates = self._nested()
        flat_tree = _spelled_out(tree)
        nested = ec.solve(dates, tree, ec.layout(tree, dates), num_samples=200)
        flat = ec.solve(dates, flat_tree, ec.layout(flat_tree, dates), num_samples=200)
        for m in dates:
            assert (nested['measurements'][m['id']]['posterior_mean']
                    == pytest.approx(flat['measurements'][m['id']]['posterior_mean'],
                                     abs=1e-9))
        for bid in ('b1', 'b2', 'b3'):
            assert (nested['boundaries'][bid]['posterior']['posterior_mean']
                    == pytest.approx(flat['boundaries'][bid]['posterior']['posterior_mean'],
                                     abs=1e-9))

    @pytest.mark.slow
    def test_it_matches_the_sampler(self):
        """
        The independent check: gibbs.solve on the NESTED tree, which it plans
        as written, lands on the exact answer. Same grid, so only inference can
        differ; 3 years rms is the bound test_gibbs uses on the chain fixtures.
        Interior Boundaries only -- an outermost one's tail depends on the
        window (see test_gibbs.test_interior_boundaries_match).
        """
        from app.services import gibbs

        tree, dates = self._nested()
        exact = ec.solve(dates, tree, ec.layout(tree, dates), num_samples=100)
        grid = np.arange(exact['grid']['lower'], exact['grid']['upper'] + 1e-9,
                         exact['grid']['step'])
        sampled = gibbs.solve(dates, tree, sweeps=20000, max_sweeps=20000,
                              num_samples=100, grid=grid, temperatures=1)
        errors = np.array([
            sampled['measurements'][m['id']]['posterior_mean']
            - exact['measurements'][m['id']]['posterior_mean'] for m in dates])
        assert np.sqrt((errors ** 2).mean()) < 3.0, errors.round(2)
        for bid in ('b1', 'b2', 'b3'):
            assert (sampled['boundaries'][bid]['posterior']['posterior_mean']
                    == pytest.approx(exact['boundaries'][bid]['posterior']['posterior_mean'],
                                     abs=5.0)), bid

    def test_the_analysis_api_integrates_it_exactly(self):
        from app.api import analysis as analysis_api

        tree, dates = self._nested()
        _r, _b, solver, _i, _s = analysis_api._run_bayesian(
            dates, tree, {'mcmc_samples': 200})
        assert solver['method'] == 'exact'
        assert solver['reason'] is None
        assert 'implicit_boundaries' not in solver

    def test_independent_branches_are_still_not_chained(self):
        """Stitching needs the tree to order the paths. Two bracketed branches
        inside a Phase are not ordered, so joining them would add a constraint."""
        tree, dates = self._nested()
        tree = _sequence('root', [{'id': 'par', 'element_type': 'phase',
                                   'name': 'Parallel', 'tpq_age_bp': None,
                                   'taq_age_bp': None, 'children': tree['children']}])
        chain, reason = ec.layout_with_reason(tree, dates)
        assert chain is None
        assert 'branches' in reason

    def test_an_unbracketed_inner_sequence_is_not_spelled_out(self):
        """
        Sequence[B, Sequence[P1, B], P2, B] is NOT the flat model: the outer
        Boundaries enclose P1 and P2 as one group with the inner Boundary
        ordered between them. Spelling it out would change the prior, so it
        stays with the sampler.
        """
        inner = _sequence('in', [_phase('p1'), _boundary('mid')])
        tree = _sequence('s', [_boundary('b0'), inner, _phase('p2'), _boundary('b1')])
        dates = _dates(('d1', 3200, 30, 'p1'), ('d2', 3000, 30, 'p2'))
        assert ec.layout(tree, dates) is None
        assert ec.layout(_spelled_out(tree), dates) is not None


class TestJointDraws:
    """
    The joint draws Amodel and the durations read, taken by drawing each
    boundary from its conditional given the ones already drawn.
    """

    def test_a_row_on_a_tiny_scale_still_draws_from_its_own_weights(self):
        """
        A conditional's rows are products of messages running from 1 down to
        1e-60, so one batch holds rows on wildly different scales. Batching
        them through one shared searchsorted rounded the small rows away: each
        drew column 0, a cell of zero weight, i.e. a boundary where it cannot
        be.
        """
        width = 400
        weights = np.zeros((6, width))
        weights[0::2, 150:] = 19.0 / 250          # rows totalling about 19
        weights[1::2, 300:320] = 1e-13 / 20       # ...and about 1e-13
        keys = np.tile(np.arange(6), 100)
        picked = ec._draw_from_rows(np.random.default_rng(0), lambda rows: weights[rows], keys)
        assert np.all(weights[keys, picked] > 0)
        small = picked.reshape(100, 6)[:, 1::2]
        assert small.min() >= 300 and small.max() < 320

    def test_draws_are_spread_as_the_weights_say(self):
        row = np.array([[0.0, 1e-30, 3e-30, 0.0, 6e-30, 0.0]])
        picked = ec._draw_from_rows(np.random.default_rng(1), lambda rows: row[rows],
                                    np.zeros(20000, dtype=np.int64))
        counts = np.bincount(picked, minlength=6) / len(picked)
        np.testing.assert_allclose(counts, [0, 0.1, 0.3, 0, 0.6, 0], atol=0.015)

    def test_draws_sharing_a_row_are_the_draws_they_would_be_alone(self):
        """
        Draws that condition on the same row share one cumulative sum. That is
        a saving and must be nothing else: with the same generator, every draw
        is the one a row of its own would have given, more distinct rows than
        a block holds included.
        """
        rng = np.random.default_rng(7)
        weights = rng.random((700, 50)) * (rng.random((700, 50)) > 0.3)
        weights[:, 0] += 1e-3                         # no row is empty
        weights *= 10.0 ** rng.uniform(-60, 0, (700, 1))
        keys = rng.integers(0, 700, 5000)

        grouped = ec._draw_from_rows(np.random.default_rng(8), lambda rows: weights[rows], keys)

        uniforms = np.random.default_rng(8).random(len(keys))
        alone = np.empty(len(keys), dtype=np.int64)
        for k, key in enumerate(keys):
            cumulative = np.cumsum(weights[key])
            found = np.searchsorted(cumulative, uniforms[k] * cumulative[-1], side='right')
            alone[k] = min(found, np.flatnonzero(weights[key])[-1])
        assert np.array_equal(grouped, alone)

    def test_a_long_sequence_with_empty_phases_and_outliers(self):
        """
        The shape that failed: a dune profile of eleven groups, three of them
        empty, inside an outer Start/End pair, every date under the general
        outlier model. The marginals were fine but the joint draws raised 'a
        conditional came out empty' -- an earlier draw had landed on a
        zero-weight cell, leaving nothing for the next boundary to take.
        Synthetic dates; the failure did not depend on their exact values.
        """
        groups = [[(700, 20), (680, 18), (665, 16)], [], [(670, 16)], [],
                  [(640, 18), (630, 16)], [], [(600, 15), (585, 16)],
                  [(590, 18), (580, 15), (575, 15)], [(550, 20)]]
        children = [_boundary('dn_start', 'Site start'), _boundary('dn_b0')]
        dates = []
        for k, group in enumerate(groups):
            children += [_phase(f'dn_p{k}'), _boundary(f'dn_b{k + 1}')]
            for j, (age, error) in enumerate(group):
                dates.append({'id': f'dn_{k}_{j}', 'sample_name': f'dn_{k}_{j}',
                              'age_bp': age, 'error_1sigma': error,
                              'curve_id': 'shcal20', 'element_id': f'dn_p{k}',
                              'outlier_prior': 0.05, 'outlier_model': 'general'})
        children.append(_boundary('dn_end', 'Site end'))
        tree = _sequence('dn', children)
        chain = ec.layout(tree, dates)
        assert chain is not None and len(chain.groups) == 11

        solved = ec.solve(dates, tree, chain, num_samples=200)

        # Every draw is a possible state: no boundary younger than the next.
        ages = np.array([solved['joint_boundaries'][b] for b in chain.boundary_ids])
        assert np.all(np.diff(ages, axis=0) <= 0)
