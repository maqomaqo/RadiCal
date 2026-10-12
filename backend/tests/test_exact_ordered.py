"""
Dates ordered against each other inside one pair of Boundaries, or beyond an
end Boundary, as groups one after another with no Boundary between them:
Phases in a row (services/exact_chain.py, _OrderedGroup).

Checked as the other shapes are:

  * the layouts find the blocks, and decline, with a reason, an order that
    is not one block after another.
  * the solvers reproduce an explicit sum over every configuration of every
    Boundary AND every date on a coarse grid, the cross-check's Boundaries,
    and the sampler's dates.
  * every joint draw keeps each block older than the next, and the draws come
    from the joint the marginals describe.
  * the grid rule differs from the sampler's by a double coincidence only.
  * the API solves such a model exactly under Automatic.
"""
import itertools

import numpy as np
import pytest

from app.services import exact_chain as ec
from app.services import exact_parallel as ep
from app.services import gibbs
from app.services.model_tree import inherited_bounds
from tests.test_exact_parallel import _date, _element, _run, _site

OPTIONS = dict(num_samples=10, seed=1, uniform_span_prior=True, estimate_durations=False,
               dispersal=None, interval_draws=10, resolution=5.0)


def _phases(name, blocks, error, **extra):
    """A Phase per block, in a row with no Boundary between them."""
    phases, dates = [], []
    for k, ages in enumerate(blocks):
        phase = _element('phase', f'{name} {k + 1}')
        phases.append(phase)
        dates += [_date(phase, age, error, **extra) for age in ages]
    return phases, dates


def _model(groups, before=(), after=(), error=25, **extra):
    """
    A Sequence of groups between Boundaries, each group a list of blocks of
    ages -- one block a plain Phase, several the Phases in a row -- with
    `before` and `after`, lists of blocks, outside every Boundary.
    """
    children, dates = [], []
    found, tail = _phases('Before', before, error, **extra)
    children += found
    dates += tail
    children.append(_element('boundary', 'Start'))
    for index, blocks in enumerate(groups):
        found, inside = _phases(f'Group {index}', blocks, error, **extra)
        children += found + [_element('boundary', f'Boundary {index + 1}')]
        dates += inside
    found, tail = _phases('After', after, error, **extra)
    children += found
    dates += tail
    return _element('sequence', 'Site', children), dates


def _ids(dates, ages):
    return [d['id'] for d in dates if d['age_bp'] in ages]


def _layout(tree, dates):
    chain, reason = ec.layout_with_reason(tree, dates, allow_tails=True)
    assert chain is not None, reason
    return chain


class TestLayout:
    def test_phases_in_a_row_are_blocks(self):
        tree, dates = _model([[[3100, 3090], [3000], [2900, 2890]]])
        chain = _layout(tree, dates)
        assert len(chain.groups) == 1
        assert [sorted(b) for b in chain.blocks[0]] == [
            sorted(_ids(dates, (3100, 3090))), _ids(dates, (3000,)),
            sorted(_ids(dates, (2900, 2890)))]
        assert chain.ordered(0)

    def test_a_plain_group_is_one_block(self):
        tree, dates = _model([[[3100, 3090]], [[3000]]])
        chain = _layout(tree, dates)
        assert [len(b) for b in chain.blocks] == [1, 1]
        assert not chain.ordered(0)

    def test_a_sequence_inside_a_group_adds_blocks(self):
        tree, dates = _model([[[3100]]])
        group = tree['children'][1]
        inner_a, inner_b = _element('phase', 'Inner A'), _element('phase', 'Inner B')
        tree['children'].insert(2, _element('sequence', 'Inner', [inner_a, inner_b]))
        dates += [_date(inner_a, 3050), _date(inner_b, 3000)]
        chain = _layout(tree, dates)
        assert [b for b in chain.blocks[0]] == [
            [d['id'] for d in dates if d['element_id'] == e['id']]
            for e in (group, inner_a, inner_b)]

    def test_tails_in_a_row_are_blocks(self):
        tree, dates = _model([[[3000]]], before=[[3200], [3100, 3090]],
                             after=[[2900], [2800]])
        chain = _layout(tree, dates)
        assert chain.older_tail_blocks == [_ids(dates, (3200,)),
                                           _ids(dates, (3100, 3090))]
        assert chain.younger_tail_blocks == [_ids(dates, (2900,)), _ids(dates, (2800,))]

    def test_an_area_takes_them(self):
        tree, dates = _site([('areas', [[[3010]], [[2980]]], [])])
        area = tree['children'][1]['children'][0]
        extra = _element('phase', 'Extra')
        area['children'].insert(2, extra)
        dates.append(_date(extra, 2995))
        stages, reason = ep.layout_with_reason(tree, dates)
        assert reason is None
        assert len(stages.stages[0].areas[0].blocks[0]) == 2

    def test_side_by_side_sequences_in_one_pair_are_declined(self):
        """Two Sequences of Phases with no Boundaries of their own, side by
        side in one Phase: each orders its own dates, but nothing orders one
        against the other, which is not one block after another."""
        tree, dates = _model([[[3100]]])
        lines = []
        for name, ages in (('North', (3050, 3000)), ('South', (3040, 2990))):
            phases, found = _phases(name, [[a] for a in ages], 25)
            lines.append(_element('sequence', name, phases))
            dates += found
        tree['children'][1]['children'] = lines
        chain, reason = ec.layout_with_reason(tree, dates, allow_tails=True)
        assert chain is None and reason == ec.NOT_ONE_AFTER_ANOTHER

    def test_an_outlier_model_beyond_an_end_is_still_declined(self):
        tree, dates = _model([[[3000]]], before=[[3200], [3100]], outlier_prior=0.05)
        chain, reason = ec.layout_with_reason(tree, dates, allow_tails=True)
        assert chain is None and 'carry an outlier model' in reason


# --------------------------------------------------------------------------
# The brute force
# --------------------------------------------------------------------------

def _configurations(M, K):
    return np.array(list(itertools.product(range(M), repeat=K)), dtype=np.int64)


def _block_rule(blocks, where, configs, older=None, younger=None):
    """
    The grid rule, per configuration of the dates in `blocks` (columns of
    `configs` in `where`): every block wholly older than the next, and

      * block 1 under each date's trapezoid over [the next block's oldest
        date, the older end]: a half at either end, nothing if they meet;
      * a later block with r dates on its oldest cell u, and l below it (the
        next block's oldest date, or the younger end): r 2^(1-r) -- one of
        them the oldest, the rest a half each at u -- a half for each date on
        l, and where u = l a half if the block is one date, else nothing.

    `older`/`younger` None for an end at the grid's edge.
    """
    M = configs.max() + 1
    weight = np.ones(len(configs))
    tops = [configs[:, [where[d] for d in block]].max(axis=1) for block in blocks]
    lows = [configs[:, [where[d] for d in block]].min(axis=1) for block in blocks]
    top_end = M - 1 if older is None else older
    low_end = 0 if younger is None else younger
    weight *= tops[0] <= top_end
    weight *= lows[-1] >= low_end
    for i in range(len(blocks) - 1):
        weight *= lows[i] >= tops[i + 1]
    for i, block in enumerate(blocks):
        l = tops[i + 1] if i + 1 < len(blocks) else np.full(len(configs), low_end)
        cells = configs[:, [where[d] for d in block]]
        if i == 0:
            weight *= l < top_end
            weight *= np.prod(np.where(cells == top_end, 0.5, 1.0), axis=1)
            weight *= np.prod(np.where(cells == l[:, None], 0.5, 1.0), axis=1)
            continue
        u = tops[i]
        r = (cells == u[:, None]).sum(axis=1)
        apart = r * 0.5 ** (r - 1.0) * np.prod(np.where(cells == l[:, None], 0.5, 1.0), axis=1)
        weight *= np.where(u > l, apart, 0.5 if len(block) == 1 else 0.0)
    return weight


def _brute_force(tree, dates, grid):
    """
    Every marginal of a chain whose groups and tails may hold blocks, by
    explicit summation over every configuration of every Boundary and every
    date, written from the model statement:

        weight = C(B_0, B_n) * prod over groups of
                     span^(-K) * prod L(t) dt * the grid rule (_block_rule)
                     -- or, where the group's two Boundaries share a cell,
                     every date in it: a half, prod L, and a half more for
                     each pair of dates in neighbouring blocks --
                 * prod over tails of prod L(t) dt * the grid rule

    The span correction and the limits are the solver's own definitions, as
    in test_exact_chain's brute force: what this checks is how the pieces are
    combined, not the pieces. Dates are enumerated outright, so the blocks'
    cuts, the solver's whole device, appear nowhere here.
    """
    chain = _layout(tree, dates)
    bounds = inherited_bounds(tree)
    M = len(grid)
    dt = grid[1] - grid[0]
    likelihood, _cumulative, _parts = ec.date_tables(grid, dates, bounds)

    def group_tables(blocks, ends):
        """For each pair of ends: the group's weight and each date's mass by cell."""
        members = [d for block in blocks for d in block]
        where = {d: k for k, d in enumerate(members)}
        configs = _configurations(M, len(members))
        L = np.prod([likelihood[d][configs[:, where[d]]] * dt for d in members], axis=0)
        sizes = [len(b) for b in blocks]
        ties = sum(a * b for a, b in zip(sizes, sizes[1:]))
        Z, mass = {}, {}
        for older, younger in ends:
            if older is not None and younger is not None and older == younger:
                w = 0.5 * np.prod([likelihood[d][older] for d in members]) * 0.5 ** ties
                Z[older, younger] = w
                mass[older, younger] = {d: np.eye(M)[older] * w for d in members}
                continue
            w = L * _block_rule(blocks, where, configs, older, younger)
            if older is not None and younger is not None:
                w = w * ((older - younger) * dt) ** -len(members)
            Z[older, younger] = w.sum()
            mass[older, younger] = {d: np.bincount(configs[:, where[d]], weights=w,
                                                   minlength=M) for d in members}
        return Z, mass

    names = chain.boundary_ids
    cube = np.array(list(itertools.product(range(M), repeat=len(names))), dtype=np.int64)
    cube = cube[np.all(cube[:, :-1] >= cube[:, 1:], axis=1)]
    inside = [d for g in chain.groups for d in g]
    log_c = ec._span_prior_correction(
        grid, len(names), True,
        *ec.pair_limits(bounds, names[0], names[-1], grid, likelihood, inside))
    weight = np.exp(np.where(np.isfinite(log_c), log_c, -np.inf))[cube[:, -1], cube[:, 0]]
    pairs = []
    for k, blocks in enumerate(chain.blocks):
        ends = {(int(o), int(y)) for o, y in zip(cube[:, k], cube[:, k + 1])}
        Z, mass = (group_tables(blocks, ends) if blocks
                   else ({e: 0.5 if e[0] == e[1] else 1.0 for e in ends}, {}))
        weight = weight * np.array([Z[int(o), int(y)] for o, y in zip(cube[:, k], cube[:, k + 1])])
        pairs.append((k, Z, mass))
    tails = []
    for blocks, column, older in ((chain.older_tail_blocks, 0, True),
                                  (chain.younger_tail_blocks, -1, False)):
        if not blocks:
            continue
        ends = [(None, F) if older else (F, None) for F in range(M)]
        Z, mass = group_tables(blocks, ends)
        weight = weight * np.array([Z[(None, F) if older else (F, None)]
                                    for F in cube[:, column]])
        tails.append((column, older, Z, mass))
    weight = weight / weight.sum()

    out = {b: np.bincount(cube[:, i], weights=weight, minlength=M)
           for i, b in enumerate(names)}
    for k, Z, mass in pairs:
        for d in chain.groups[k]:
            total = np.zeros(M)
            for row, (o, y) in enumerate(zip(cube[:, k], cube[:, k + 1])):
                if weight[row] > 0:
                    total += weight[row] * mass[int(o), int(y)][d] / Z[int(o), int(y)]
            out[d] = total
    for column, older, Z, mass in tails:
        end = out[names[column]]
        for d in next(iter(mass.values())):
            out[d] = sum(end[F] * mass[key][d] / Z[key]
                         for F in range(M)
                         for key in [(None, F) if older else (F, None)] if Z[key] > 0)
    return out


def _solve_on(grid, tree, dates, **options):
    chain = _layout(tree, dates)
    return ec._solve_on_grid(grid, dates, tree, chain, inherited_bounds(tree),
                             **{**OPTIONS, **options})


CASES = {
    'two blocks': ([[[3020], [2990]]], (), ()),
    'three blocks': ([[[3030], [3000, 2995], [2970]]], (), ()),
    'with the span correction': ([[[3040]], [[3010], [2985]]], (), ()),
    'an older tail in a row': ([[[3000]]], [[3070], [3040]], ()),
    'a younger tail in a row': ([[[3030]]], (), [[2990], [2960, 2955]]),
}


class TestMatchesBruteForce:
    @pytest.mark.parametrize('case', list(CASES))
    def test_every_marginal(self, case):
        groups, before, after = CASES[case]
        tree, dates = _model(groups, before=before, after=after, error=30)
        M = 7 if case in ('three blocks', 'a younger tail in a row') else 8
        grid = np.linspace(3000.0, 3350.0, M)
        expected = _brute_force(tree, dates, grid)
        solved, densities = _solve_on(grid, tree, dates)
        ids = list(solved['measurements']) + list(solved['boundaries'])
        assert set(ids) == set(expected)
        for pid, density in zip(ids, densities):
            np.testing.assert_allclose(density / density.sum(),
                                       expected[pid] / expected[pid].sum(),
                                       atol=1e-12, err_msg=pid)


class TestJointDraws:
    def test_every_block_is_older_than_the_next_in_every_draw(self):
        tree, dates = _model([[[3150]], [[3060, 3050], [3010], [2980, 2975]]],
                             before=[[3250], [3200]], after=[[2900], [2880]])
        chain = _layout(tree, dates)
        solved = ec.solve(dates, tree, chain, num_samples=50, interval_draws=4000)
        joint = {**solved['joint_boundaries'], **solved['joint_draws']}
        b = chain.boundary_ids

        def wholly_older(older, younger):
            for o in older:
                for y in younger:
                    assert np.all(joint[o] >= joint[y]), (o, y)

        for k, blocks in enumerate(chain.blocks):
            for block in blocks:
                wholly_older([b[k]], block)
                wholly_older(block, [b[k + 1]])
            for older, younger in zip(blocks, blocks[1:]):
                wholly_older(older, younger)
        tail = chain.older_tail_blocks
        wholly_older(tail[-1], [b[0]])
        wholly_older(tail[0], tail[1])
        tail = chain.younger_tail_blocks
        wholly_older([b[-1]], tail[0])
        wholly_older(tail[0], tail[1])

    @pytest.mark.parametrize('case', ['three blocks', 'an older tail in a row',
                                      'a younger tail in a row'])
    def test_the_draws_come_from_the_joint_the_marginals_describe(self, case):
        groups, before, after = CASES[case]
        tree, dates = _model(groups, before=before, after=after, error=30)
        grid = np.linspace(3000.0, 3350.0, 7)
        expected = _brute_force(tree, dates, grid)
        solved, _densities = _solve_on(grid, tree, dates, interval_draws=200000)
        drawn = {**solved['joint_boundaries'], **solved['joint_draws']}
        for pid, values in drawn.items():
            counts = np.bincount(np.searchsorted(grid, values), minlength=len(grid))
            np.testing.assert_allclose(counts / counts.sum(),
                                       expected[pid] / expected[pid].sum(), atol=0.006,
                                       err_msg=pid)


class TestSideBySide:
    """
    One area inside a stage is the same model as a chain with an empty group
    either side (test_exact_parallel's TestOneAreaIsAChain), so an area or a
    plain stage holding Phases in a row must solve as that chain does -- and
    the chain is held to the brute force above.
    """

    @staticmethod
    def _both(tree, chain_tree, dates):
        layout, reason = ep.layout_with_reason(tree, dates)
        assert reason is None, reason
        chain = _layout(chain_tree, dates)
        grid = np.arange(2700.0, 3600.0, 2.5)
        parallel, _d = ep._solve_on_grid(grid, dates, tree, layout,
                                         inherited_bounds(tree), **OPTIONS)
        line, _d2 = ec._solve_on_grid(grid, dates, chain_tree, chain,
                                      inherited_bounds(chain_tree), **OPTIONS)
        for kind in ('measurements', 'boundaries'):
            assert set(parallel[kind]) == set(line[kind])
            for pid, summary in line[kind].items():
                a = summary.get('posterior', summary)
                b = parallel[kind][pid].get('posterior', parallel[kind][pid])
                assert b['posterior_mean'] == pytest.approx(a['posterior_mean'], abs=1e-6), pid
                assert b['posterior_std'] == pytest.approx(a['posterior_std'], abs=1e-6), pid

    def test_phases_in_a_row_inside_an_area(self):
        tree, dates = _site([('areas', [[[3010, 2990], [2950]]], [])])
        area = tree['children'][1]['children'][0]
        extra = _element('phase', 'Extra')
        area['children'].insert(2, extra)          # after the area's first Phase
        dates.append(_date(extra, 2975))
        chain_tree = _element('sequence', 'Line', [tree['children'][0]]
                              + area['children'] + [tree['children'][2]])
        self._both(tree, chain_tree, dates)

    def test_phases_in_a_row_as_a_plain_stage(self):
        tree, dates = _site([('plain', [3150])] + [('areas', [[[3010], [2950]]], [])])
        extra = _element('phase', 'Extra')
        tree['children'].insert(2, extra)          # beside Stage 0, no Boundary between
        dates.append(_date(extra, 3100))
        area = tree['children'][4]['children'][0]
        chain_tree = _element('sequence', 'Line', tree['children'][:4]
                              + area['children'] + [tree['children'][5]])
        self._both(tree, chain_tree, dates)


class TestAgreesWithTheReferences:
    @pytest.mark.parametrize('groups, before, after', [
        ([[[3060, 3050], [3010, 3000], [2960, 2955]]], (), ()),
        ([[[3080, 3075, 3070]], [[3030, 3025], [2990, 2985]]], (), ()),
        ([[[3010, 3005, 3000]]], [[3110, 3105], [3070, 3065]], ()),
    ], ids=['three blocks', 'with the span correction', 'an older tail in a row'])
    def test_the_boundaries_are_the_cross_checks(self, groups, before, after):
        """services/cross_check.py integrates blocks with the solver's own
        _OrderedGroup, reading them off the sampler's ordering; on its grid
        the two must agree to rounding."""
        from app.services import cross_check
        tree, dates = _model(groups, before=before, after=after)
        reference = cross_check.reference(dates, tree)
        solved, densities = _solve_on(reference['grid'], tree, dates)
        mine = dict(zip(list(solved['measurements']) + list(solved['boundaries']),
                        densities))
        for bid, density in reference['boundaries'].items():
            np.testing.assert_allclose(mine[bid] / mine[bid].sum(), density / density.sum(),
                                       atol=1e-12, err_msg=bid)

    @pytest.mark.parametrize('parallel', [False, True], ids=['chain', 'areas'])
    def test_the_dates_are_the_samplers(self, parallel):
        if parallel:
            tree, dates = _site([('plain', [3150])] + [('areas', [[[3010], [2950]]], [])])
            extra = _element('phase', 'Extra')
            tree['children'].insert(2, extra)
            dates.append(_date(extra, 3100))
            layout, _reason = ep.layout_with_reason(tree, dates)
            exact = ep.solve(dates, tree, layout, num_samples=50, interval_draws=50)
        else:
            tree, dates = _model([[[3060, 3050], [3010, 3000], [2960]]],
                                 before=[[3200], [3150]])
            exact = ec.solve(dates, tree, _layout(tree, dates), num_samples=50,
                             interval_draws=50)
        sampled = gibbs.solve(dates, tree, sweeps=30000, max_sweeps=30000,
                              num_samples=200, seed=11, chains=1, temperatures=1)
        errors = [sampled['measurements'][d['id']]['posterior_mean']
                  - exact['measurements'][d['id']]['posterior_mean'] for d in dates]
        assert np.sqrt(np.mean(np.square(errors))) < 2.0, np.round(errors, 2)


    def test_side_by_side_with_tails_in_a_row_is_the_cross_checks(self):
        from app.services import cross_check
        from tests.test_exact_tails import _with_tails
        from tests.test_exact_parallel import TWO_AREAS
        tree, dates = _with_tails(*_site(TWO_AREAS), before=[3150, 3145])
        earlier = _element('phase', 'Earlier')
        tree['children'].insert(0, earlier)
        dates += [_date(earlier, 3250), _date(earlier, 3245)]
        layout, reason = ep.layout_with_reason(tree, dates)
        assert reason is None and len(layout.older_tail_blocks) == 2
        reference = cross_check.reference(dates, tree)
        solved, densities = ep._solve_on_grid(reference['grid'], dates, tree, layout,
                                              inherited_bounds(tree), **OPTIONS)
        mine = dict(zip(list(solved['measurements']) + list(solved['boundaries']),
                        densities))
        for bid, density in reference['boundaries'].items():
            np.testing.assert_allclose(mine[bid] / mine[bid].sum(), density / density.sum(),
                                       atol=1e-12, err_msg=bid)


class TestTheAnalysisSolvesThemExactly:
    def test_automatic_takes_phases_in_a_row_as_written(self, client):
        """Exact, and with nothing supplied: the model as written, which the
        sampler used to be given instead (analysis._run_bayesian)."""
        tree, dates = _model([[[3060, 3050], [3010, 3000]]], before=[[3200], [3150]])
        created = _run(client, tree, dates)
        assert created.status_code == 200, created.text
        solver = created.json()['data']['solver']
        assert solver['method'] == 'exact'
        assert solver['reason'] is None
        assert solver['implicit_boundaries'] == 0

    def test_an_order_that_is_not_blocks_is_sampled_and_says_why(self, client):
        tree, dates = _model([[[3100]]])
        lines = []
        for name, ages in (('North', (3050, 3000)), ('South', (3040, 2990))):
            phases, found = _phases(name, [[a] for a in ages], 25)
            lines.append(_element('sequence', name, phases))
            dates += found
        tree['children'][1]['children'] = lines
        created = _run(client, tree, dates)
        assert created.status_code == 200, created.text
        solver = created.json()['data']['solver']
        assert solver['method'] == 'gibbs'
        assert solver['reason']


class TestConvergence:
    def test_the_hillside_start_settles_as_the_step_shrinks(self):
        """
        The grid rule is the trapezoid for the continuum integral, so halving
        the step roughly halves the error: Start on the Hillside `ordered`
        model runs 624.53, 623.23, 622.67, 622.40 and 622.27 at steps of 2, 1,
        0.5, 0.25 and 0.125 years, towards about 622.1.
        """
        import oxcal_fixture as ox
        dates, tree = ox.measurements(), ox.model_tree('ordered')
        chain = ec.layout(tree, dates)
        starts = []
        for step in (1.0, 0.5, 0.25):
            grid = np.arange(450.0, 800.0, step)
            solved, _d = ec._solve_on_grid(grid, dates, tree, chain, inherited_bounds(tree),
                                           **OPTIONS)
            starts.append(solved['boundaries'][chain.boundary_ids[0]]['posterior']
                          ['posterior_mean'])
        changes = np.abs(np.diff(starts))
        assert changes[1] < 0.7 * changes[0], starts
        assert changes[1] < 0.5, starts
