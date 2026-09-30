"""
Agreement indices (services/agreement.py), against the OxCal 4.4 runs in
tests/fixtures/oxcal.

The formula was identified from this fixture rather than taken on trust:
it holds each date's likelihood, its posterior and the index OxCal reported,
across three models -- 24 known answers. These tests re-run that
identification, so if someone changes the definition the fixture will say so.

Two separate things are checked and it matters not to conflate them:

  * feeding OxCal's OWN posterior in must reproduce OxCal's OWN index. That
    tests the formula, and is tight (rms under 1).
  * feeding the app's posterior in gives a number that differs by however
    much the app's posterior differs from OxCal's. That is not a test of the
    formula and is asserted loosely.
"""
import csv
import re

import numpy as np
import pytest

from app.services import agreement

import oxcal_fixture as ox


def _reported():
    """OxCal's own A column, plus the Amodel/Aoverall header values."""
    out = {}
    for run in sorted(ox.RUNS):
        path = ox.paths(run)[2]
        per_date = {}
        with open(path, encoding='utf-8-sig') as fh:
            for row in csv.reader(fh):
                if len(row) > 10 and row[0].startswith('R_Date'):
                    per_date[ox.key(row[0].split(None, 1)[1])] = float(row[10])
        text = open(path, encoding='utf-8-sig').read()
        out[run] = {
            'dates': per_date,
            'overall': float(re.search(r'Aoverall ([\d.]+)', text).group(1)),
            'model': float(re.search(r'Amodel ([\d.]+)', text).group(1)),
        }
    return out


REPORTED = _reported()
RUNS = sorted(ox.RUNS)


def _as_pairs(calbp, prob):
    return np.column_stack([calbp, prob])


@pytest.mark.parametrize('run', RUNS)
class TestReproducesOxCal:
    """OxCal's posterior in, OxCal's index out."""

    def test_individual_indices(self, run):
        """
        The formula itself: OxCal's likelihood AND OxCal's posterior in, so
        nothing but the definition is under test.
        """
        dists = ox.distributions(run)
        errors = []
        for m in ox.measurements():
            got = agreement.agreement_index(
                m,
                _as_pairs(*dists[m['id']]['posterior']),
                unmodelled=_as_pairs(*dists[m['id']]['likelihood']),
            )
            assert got is not None
            errors.append(got - REPORTED[run]['dates'][m['id']])
        errors = np.array(errors)
        # OxCal reports to one decimal and exports at five years, so exact
        # equality is not available; this is inside its own precision.
        assert np.abs(errors).max() < 1.5, dict(zip(
            [m['sample_name'] for m in ox.measurements()], errors.round(2)))
        assert np.sqrt((errors ** 2).mean()) < 1.0

    def test_our_own_likelihood_gives_much_the_same_answer(self, run):
        """
        The realistic path, where the likelihood is ours rather than OxCal's.
        Our calibration overlaps theirs at 0.978-0.983 rather than exactly,
        and that difference propagates here -- so this is deliberately looser
        than the formula test above, and exists to pin how much it costs.
        """
        dists = ox.distributions(run)
        errors = []
        for m in ox.measurements():
            got = agreement.agreement_index(m, _as_pairs(*dists[m['id']]['posterior']))
            errors.append(got - REPORTED[run]['dates'][m['id']])
        errors = np.array(errors)
        assert np.abs(errors).max() < 4.0
        assert np.sqrt((errors ** 2).mean()) < 2.5

    def test_overall_index(self, run):
        """
        End to end, from our per-date indices. Looser than the two tests
        either side of it on purpose: the 1/sqrt(n) exponent multiplies a
        systematic per-date bias by sqrt(n), so the +0.7 average offset above
        arrives here as about +2 on eight dates. That amplification is a
        property of the combining rule, not slack in the implementation --
        test_overall_from_oxcals_own_indices_is_exact pins the rule itself.
        """
        dists = ox.distributions(run)
        indices = [
            agreement.agreement_index(
                m,
                _as_pairs(*dists[m['id']]['posterior']),
                unmodelled=_as_pairs(*dists[m['id']]['likelihood']),
            )
            for m in ox.measurements()
        ]
        got = agreement.overall_agreement(indices)
        assert got == pytest.approx(REPORTED[run]['overall'], abs=2.5)

    def test_overall_from_oxcals_own_indices_is_exact(self, run):
        """
        Isolating the combining rule from the per-date one: fed OxCal's own
        published indices, the combination must return OxCal's own overall.
        """
        got = agreement.overall_agreement(list(REPORTED[run]['dates'].values()))
        assert got == pytest.approx(REPORTED[run]['overall'], abs=0.15)


def test_amodel_is_not_derivable_from_the_date_indices():
    """
    Why Amodel needs the joint posterior rather than the A column. On the
    `ordered` run the per-date indices multiply to more than one, so every
    exponent of that product lands above 100 while OxCal reports 96.0: Amodel
    carries the correlation the model's structure puts between the dates, which
    their marginal indices cannot contain. That is what
    agreement.model_agreement computes and what TestModelIndexAgainstOxCal
    holds it to; this pins the reason it cannot be done more cheaply.
    """
    values = np.array(list(REPORTED['ordered']['dates'].values())) / 100.0
    assert values.prod() > 1.0
    assert REPORTED['ordered']['model'] < 100.0


class TestBehaviour:
    """What the index means, independent of OxCal."""

    def _measurement(self, age=3000, error=30):
        return {'id': 'ag_x', 'sample_name': 'X', 'age_bp': age,
                'error_1sigma': error, 'curve_id': 'intcal20'}

    def _likelihood_pairs(self, m, lo=2800, hi=3600, step=5.0):
        grid = np.arange(lo, hi, step)
        return np.column_stack([grid, agreement.unmodelled_likelihood(m, grid)])

    def test_untouched_date_scores_100(self):
        """A model that changes nothing must not be reported as disagreeing."""
        m = self._measurement()
        assert agreement.agreement_index(m, self._likelihood_pairs(m)) == pytest.approx(100.0, abs=0.5)

    def test_sharpening_onto_the_peak_scores_above_100(self):
        """Why values over 100 are normal: the model concentrated the date
        where its own likelihood was already highest."""
        m = self._measurement()
        pairs = self._likelihood_pairs(m)
        peak = pairs[np.argmax(pairs[:, 1]), 0]
        narrow = np.exp(-0.5 * ((pairs[:, 0] - peak) / 15.0) ** 2)
        assert agreement.agreement_index(m, np.column_stack([pairs[:, 0], narrow])) > 105.0

    def test_a_date_pushed_off_its_likelihood_scores_low(self):
        """The case the index exists for -- and the one a naive
        normalisation would hide."""
        m = self._measurement()
        grid = np.arange(2800.0, 3600.0, 5.0)
        displaced = np.exp(-0.5 * ((grid - 3450.0) / 25.0) ** 2)
        score = agreement.agreement_index(m, np.column_stack([grid, displaced]))
        assert score < agreement.POOR_AGREEMENT_BELOW

    def test_the_likelihood_is_not_renormalised_over_the_posterior_window(self):
        """
        The tempting shortcut, and why it is wrong.

        The solver emits a posterior TRIMMED to its own support. If the
        likelihood were then normalised over only that window, a date the
        model had shoved onto the flank of its likelihood would have that
        likelihood rescaled over precisely the sliver where the two still
        touch -- and would score well for it.

        Measured on a narrow posterior 300 years off: 37.8 the right way,
        which trips the warning threshold, against 84.2 the shortcut way,
        which does not. The mutation is invisible to a test whose modelled
        distribution already spans the likelihood, so this one is
        deliberately trimmed.
        """
        m = self._measurement()
        grid = np.arange(3260.0, 3340.0, 5.0)
        narrow = np.exp(-0.5 * ((grid - 3300.0) / 10.0) ** 2)

        correct = agreement.agreement_index(m, np.column_stack([grid, narrow]))
        assert correct < agreement.POOR_AGREEMENT_BELOW, correct

        observed = agreement.unmodelled_likelihood(m, grid)
        observed = observed / observed.sum()
        fitted = narrow / narrow.sum()
        shortcut = float(100 * (fitted * observed).sum() / (observed ** 2).sum())
        assert shortcut > 80.0, shortcut
        assert correct < shortcut / 2.0

    def test_worsens_monotonically_as_the_model_pushes_further(self):
        m = self._measurement()
        grid = np.arange(2600.0, 3800.0, 5.0)
        peak = grid[np.argmax(agreement.unmodelled_likelihood(m, grid))]
        scores = []
        for offset in (0, 40, 80, 160, 320):
            shifted = np.exp(-0.5 * ((grid - (peak + offset)) / 25.0) ** 2)
            scores.append(agreement.agreement_index(m, np.column_stack([grid, shifted])))
        assert scores == sorted(scores, reverse=True), scores

    def test_overall_is_dragged_down_by_one_bad_date(self):
        assert agreement.overall_agreement([100.0] * 8) == pytest.approx(100.0, abs=0.1)
        assert agreement.overall_agreement([100.0] * 7 + [20.0]) < 60.0

    def test_a_zero_scoring_date_is_not_dropped_from_the_overall(self):
        """
        A date pushed clean off its likelihood scores a true zero. Skipping
        those as unusable would drop the worst dates in the model and report
        the remainder's healthy average -- the exact opposite of the warning
        this is for.
        """
        assert agreement.overall_agreement([100.0] * 5 + [0.0]) < 10.0
        assert agreement.overall_agreement([0.0, 0.0]) < 1.0

    def test_returns_none_rather_than_inventing_a_score(self):
        m = self._measurement()
        assert agreement.agreement_index(m, []) is None
        assert agreement.agreement_index(m, [[3000.0, 1.0]]) is None
        assert agreement.overall_agreement([]) is None
        assert agreement.overall_agreement([None, None]) is None


class TestWhatItCanAndCannotSee:
    """
    The index measures conflict between the data and the model's STRUCTURE.
    Both halves of that are worth pinning, because the difference decides
    whether it is any use for the job you reached for it to do.
    """

    def _run(self, client, tag, groups, ordered):
        children = [{'id': f'{tag}_b0', 'element_type': 'boundary', 'name': 'Start',
                     'tpq_age_bp': None, 'taq_age_bp': None, 'children': []}]
        measurements = []
        for gi, ages in enumerate(groups):
            pid = f'{tag}_p{gi}'
            children.append({'id': pid, 'element_type': 'phase', 'name': f'Phase {gi}',
                             'tpq_age_bp': None, 'taq_age_bp': None, 'children': []})
            if ordered or gi == len(groups) - 1:
                children.append({'id': f'{tag}_b{gi + 1}', 'element_type': 'boundary',
                                 'name': f'B{gi + 1}', 'tpq_age_bp': None,
                                 'taq_age_bp': None, 'children': []})
            for j, age in enumerate(ages):
                measurements.append({
                    'id': f'{tag}_{gi}_{j}', 'sample_name': f'S{gi}{j}',
                    'age_bp': age, 'error_1sigma': 30, 'element_id': pid,
                })
        created = client.post('/api/analysis/bayesian?wait=true', json={
            'name': tag,
            'measurements': measurements,
            'model_tree': {'id': f'{tag}_seq', 'element_type': 'sequence', 'name': 'Site',
                           'tpq_age_bp': None, 'taq_age_bp': None, 'children': children},
            'bayesian_config': {'mcmc_samples': 300, 'mcmc_tune': 200, 'mcmc_chains': 1},
            'save': True,
        })
        assert created.status_code == 200, created.text
        return created.json()['data']['id']

    def _scores(self, client, analysis_id):
        import csv as _csv
        import io
        rows = list(_csv.reader(io.StringIO(
            client.get(f'/api/results/{analysis_id}/export/csv').text)))
        header, data = rows[0], rows[1:]
        col, name_col, kind = (header.index('Agreement %'), header.index('Name'),
                               header.index('Row Type'))
        return ({r[name_col]: r[col] for r in data if r[kind] == 'Date'},
                [r[col] for r in data if r[kind] == 'Boundary'])

    def test_flags_a_date_the_model_has_to_fight(self, client):
        """
        The outlier sits in the YOUNGER phase of an ordered sequence while
        being 600 radiocarbon years older than the phase above it, so the
        ordering cannot be satisfied without moving it a long way. It, and
        everything it contradicts, must say so.
        """
        analysis_id = self._run(client, 'agconf',
                                [[3000, 3020, 3040], [2900, 2920, 3640]], ordered=True)
        scores, _ = self._scores(client, analysis_id)
        assert len(scores) == 6 and all(v != '' for v in scores.values()), scores
        numeric = {k: float(v) for k, v in scores.items()}
        assert numeric['S12'] < 20.0, numeric          # the contradictory date
        assert min(numeric.values()) < 20.0, numeric

    def test_does_not_flag_an_outlier_a_lone_phase_can_simply_absorb(self, client):
        """
        The limitation, pinned so nobody reaches for this expecting an
        outlier detector. One unconstrained phase widens to fit anything, so
        nothing is moved and nothing scores badly -- measured at 100.5 for a
        date 600 years from its neighbours. Catching that needs an outlier
        model, not this.
        """
        analysis_id = self._run(client, 'agabsorb',
                                [[3000, 3020, 3040, 3060, 3640]], ordered=False)
        scores, _ = self._scores(client, analysis_id)
        numeric = {k: float(v) for k, v in scores.items()}
        assert min(numeric.values()) > 80.0, numeric

    def test_boundaries_report_no_agreement_rather_than_zero(self, client):
        """A boundary has no measurement behind it, so it has nothing to
        agree with -- which is not the same as agreeing badly."""
        analysis_id = self._run(client, 'agbnd', [[3000, 3030, 3060]], ordered=False)
        _scores, boundaries = self._scores(client, analysis_id)
        assert boundaries and all(v == '' for v in boundaries), boundaries


class TestAgainstTheOutlierFixture:
    """
    services/agreement.py against the SECOND OxCal run
    (tests/fixtures/oxcal_outlier: Shag River Mouth, 16 dates, one phase,
    Outlier_Model active). An independent site, structure and curve region
    from the fixture the formula was identified on.

    It also pins something the first fixture could not show: with an outlier
    model running, OxCal judges each posterior against the OUTLIER-ADJUSTED
    likelihood rather than the raw one, so our raw-likelihood index reads
    systematically low against it.
    """

    def test_agreement_flags_the_outlier_and_nothing_else(self):
        import shag_fixture as sf
        dists, table = sf.distributions(), sf.table()
        scores = {
            m['id']: agreement.agreement_index(
                m, np.column_stack(dists[m['id']]['posterior']),
                unmodelled=np.column_stack(dists[m['id']]['likelihood']))
            for m in sf.measurements()
        }
        assert scores[sf.OUTLIER] < agreement.POOR_AGREEMENT_BELOW, scores
        others = [v for k, v in scores.items() if k != sf.OUTLIER]
        assert min(others) > agreement.POOR_AGREEMENT_BELOW, scores
        # OxCal's own ordering of the dates by agreement must be reproduced.
        assert min(scores, key=scores.get) == sf.OUTLIER

    def test_reads_low_against_oxcal_because_an_outlier_model_is_running(self):
        """
        The measured size of that difference, so a future change that makes
        the index outlier-aware shows up here as an improvement rather than
        passing unnoticed.

        On the fixture WITHOUT an outlier model we match OxCal to rms 0.81.
        Here the fifteen sound dates read about 5 low, because OxCal is
        comparing against a likelihood broadened by the 5% outlier component
        and we are not. Feeding a mixture in closes most of it -- measured
        mean +0.6 against -4.9 -- which is what identified the cause.
        """
        import shag_fixture as sf
        dists, table = sf.distributions(), sf.table()
        errors = []
        for m in sf.measurements():
            if m['id'] == sf.OUTLIER:
                continue
            got = agreement.agreement_index(
                m, np.column_stack(dists[m['id']]['posterior']),
                unmodelled=np.column_stack(dists[m['id']]['likelihood']))
            errors.append(got - table[m['id']]['agreement'])
        errors = np.array(errors)
        assert -8.0 < errors.mean() < -1.0, errors.mean()
        assert np.abs(errors).max() < 8.0

    def test_the_outlier_reads_zero_where_oxcal_reads_five(self):
        """
        A date the model has pushed entirely off its likelihood has no
        overlap left to measure, so the index bottoms out. OxCal reports 5.3
        because its comparison likelihood carries the outlier component's
        tail; ours reports ~0 against the raw likelihood. Both say the same
        thing for screening, but the overall index is built from a product
        and is therefore hypersensitive to the difference -- 82.9 against
        OxCal's own value, 15.6 from ours.
        """
        import shag_fixture as sf
        dists, table = sf.distributions(), sf.table()
        m = next(x for x in sf.measurements() if x['id'] == sf.OUTLIER)
        got = agreement.agreement_index(
            m, np.column_stack(dists[m['id']]['posterior']),
            unmodelled=np.column_stack(dists[m['id']]['likelihood']))
        assert got < 1.0
        assert table[sf.OUTLIER]['agreement'] < 10.0

        from_oxcal = agreement.overall_agreement(
            [table[x['id']]['agreement'] for x in sf.measurements()])
        assert from_oxcal == pytest.approx(table['model']['Aoverall'], abs=1.0)


def _draws_from(grid, density, rng, count):
    """
    `count` draws from a density on `grid`, as the solvers produce them: a
    calendar age is drawn AT a grid node, not interpolated between two, so a
    drawn state means the same thing here as it does in a run.
    """
    return rng.choice(grid, size=count, p=density / density.sum())


class TestModelIndex:
    """
    A_model (agreement.model_agreement) on its own terms: the properties that
    say it is the joint version of A_i rather than a second number that
    happens to land nearby.
    """

    def _measurement(self, tag='am_x', age=3000, error=30):
        return {'id': tag, 'sample_name': tag, 'age_bp': age,
                'error_1sigma': error, 'curve_id': 'intcal20'}

    def test_one_date_reduces_to_its_own_agreement_index(self):
        """
        The identity the definition rests on: with n = 1 the joint
        distribution IS the marginal and the 1/sqrt(n) exponent is 1, so
        A_model has to return that date's own A_i. Anything else means the two
        are measuring different things.
        """
        m = self._measurement()
        grid = np.arange(2800.0, 3600.0, 5.0)
        # Sharpened onto the peak of its own likelihood, so the expected
        # answer is well away from 100 and a coincidence is ruled out.
        peak = grid[np.argmax(agreement.unmodelled_likelihood(m, grid))]
        posterior = np.exp(-0.5 * ((grid - peak) / 15.0) ** 2)
        expected = agreement.agreement_index(m, np.column_stack([grid, posterior]))
        got = agreement.model_agreement(
            [m], {m['id']: _draws_from(grid, posterior, np.random.default_rng(1), 40000)})
        assert got == pytest.approx(expected, rel=0.02), (got, expected)

    def test_dates_the_model_left_independent_score_100(self):
        """
        No model at all: every date drawn from its own likelihood, nothing
        correlated with anything. A_model must report that as agreement, and
        must not accumulate a penalty for having several dates in it.
        """
        rng = np.random.default_rng(2)
        grid = np.arange(2600.0, 3800.0, 5.0)
        ms = [self._measurement(f'am_ind{i}', age=age)
              for i, age in enumerate((2900, 3000, 3100, 3200, 3300))]
        draws = {m['id']: _draws_from(
            grid, agreement.unmodelled_likelihood(m, grid), rng, 40000) for m in ms}
        assert agreement.model_agreement(ms, draws) == pytest.approx(100.0, abs=2.0)

    def test_dates_forced_together_score_low(self):
        """
        The case it exists for. Five dates spread over 700 radiocarbon years,
        all driven to one calendar age -- a model whose structure the data
        cannot satisfy.
        """
        rng = np.random.default_rng(3)
        ms = [self._measurement(f'am_sq{i}', age=age)
              for i, age in enumerate((2800, 2950, 3100, 3250, 3400))]
        grid = np.arange(2600.0, 3800.0, 5.0)
        squeezed = np.exp(-0.5 * ((grid - 3150.0) / 20.0) ** 2)
        draws = {m['id']: _draws_from(grid, squeezed, rng, 20000) for m in ms}
        assert agreement.model_agreement(ms, draws) < agreement.POOR_AGREEMENT_BELOW

    def test_sampling_error_is_small_at_the_draws_the_solvers_keep(self):
        """
        A_model is a Monte Carlo average where A_i and A_overall are not, so
        the size of that error is part of the number. Pinned here so a change
        to how many states a run keeps cannot quietly make it noise.
        """
        ms = [self._measurement(f'am_mc{i}', age=age)
              for i, age in enumerate((2950, 3000, 3050, 3100))]
        grid = np.arange(2700.0, 3500.0, 5.0)
        posterior = np.exp(-0.5 * ((grid - 3150.0) / 60.0) ** 2)
        scores = []
        for seed in (11, 12, 13, 14):
            rng = np.random.default_rng(seed)
            scores.append(agreement.model_agreement(
                ms, {m['id']: _draws_from(grid, posterior, rng, 20000) for m in ms}))
        assert max(scores) - min(scores) < 1.0, scores

    def test_returns_none_rather_than_inventing_a_score(self):
        m = self._measurement()
        assert agreement.model_agreement([m], {}) is None
        assert agreement.model_agreement([m], {m['id']: []}) is None
        assert agreement.model_agreement([], {}) is None

    def test_a_date_with_no_draws_is_left_out_of_n(self):
        """
        Not scored as a zero: a date the run did not report a state for is
        missing information, and counting it in n would drag the whole model
        down for it.
        """
        rng = np.random.default_rng(4)
        grid = np.arange(2600.0, 3800.0, 5.0)
        ms = [self._measurement(f'am_miss{i}', age=age) for i, age in enumerate((2950, 3050))]
        draws = {m['id']: _draws_from(
            grid, agreement.unmodelled_likelihood(m, grid), rng, 20000) for m in ms}
        both = agreement.model_agreement(ms, draws)
        one = agreement.model_agreement(
            ms + [self._measurement('am_missing_x')], draws)
        assert one == pytest.approx(both, abs=1e-9)


class TestModelIndexAgainstOxCal:
    """
    A_model end to end, against the Amodel OxCal reports for the same three
    models -- the number an analyst actually screens a model on, so the claim
    that we compute the same quantity has to be made against its own value and
    not only against the per-date column.

    Solved rather than fed in: Amodel is a function of the JOINT posterior, and
    the fixture's export holds only the marginals, so there is nothing to
    substitute for running the model. That makes this looser than the formula
    tests above by exactly the amount our posterior differs from OxCal's -- the
    same cost test_our_own_likelihood_gives_much_the_same_answer pins per date.
    """

    SOLVED = {}

    def _solved(self, run):
        """One run per model, cached: each is a few seconds of sampling."""
        if run not in self.SOLVED:
            from app.services import gibbs
            self.SOLVED[run] = gibbs.solve(
                ox.measurements(), ox.model_tree(run), num_samples=200)
        return self.SOLVED[run]

    def _our_indices(self, solved):
        return [
            agreement.agreement_index(
                m, solved['measurements'][m['id']]['probability_distribution'])
            for m in ox.measurements()
        ]

    @pytest.mark.parametrize('run', RUNS)
    def test_reproduces_oxcals_amodel(self, run):
        got = self._solved(run)['model_agreement']
        assert got is not None
        # Reads high by 1.5-3.3 across the three models, which is our
        # posterior's own offset carried through rather than slack: the same
        # dates' per-date indices read high by about 0.7 each.
        assert got == pytest.approx(REPORTED[run]['model'], abs=3.5), got

    @pytest.mark.parametrize('run', RUNS)
    def test_amodel_is_closer_to_oxcal_than_aoverall_is(self, run):
        """
        Why the joint index is worth the joint draws. A_overall's independence
        assumption costs it about 5 units on these models -- more than the
        difference it is being read for -- and A_model does not pay it.
        """
        solved = self._solved(run)
        joint_error = abs(solved['model_agreement'] - REPORTED[run]['model'])
        overall = agreement.overall_agreement(self._our_indices(solved))
        overall_error = abs(overall - REPORTED[run]['overall'])
        assert joint_error < overall_error, (joint_error, overall_error)

    def test_ordering_pulls_amodel_below_aoverall(self):
        """
        The difference between the two indices, and the direction OxCal reports
        it in: on the ordered model the constraints correlate the dates and
        Amodel falls below Aoverall (96.0 against 100.4 in OxCal's own run),
        while on the unordered one -- same dates, branches free -- the two all
        but coincide (106.6 against 107.0).
        """
        assert REPORTED['ordered']['model'] < REPORTED['ordered']['overall']
        assert abs(REPORTED['unordered']['model']
                   - REPORTED['unordered']['overall']) < 1.0

        gaps = {}
        for run in ('ordered', 'unordered'):
            solved = self._solved(run)
            gaps[run] = (agreement.overall_agreement(self._our_indices(solved))
                         - solved['model_agreement'])
        assert gaps['ordered'] > gaps['unordered'], gaps
        assert gaps['ordered'] > 0.0, gaps

    def test_both_solvers_report_the_same_amodel(self):
        """
        Exact integration and the sampler must agree about it, or the number
        means something different depending on which solver a model happened to
        qualify for. The exact solver needs the implied transition supplied,
        which is a real change to the model -- hence a wider tolerance than the
        two solvers' marginals themselves agree to.
        """
        from app.services import exact_chain
        from app.services.model_tree import with_implicit_boundaries
        measurements = ox.measurements()
        tree, inserted = with_implicit_boundaries(ox.model_tree('ordered'))
        assert inserted
        layout, _reason = exact_chain.layout_with_reason(tree, measurements)
        assert layout is not None
        exact = exact_chain.solve(measurements, tree, layout, num_samples=200)
        assert exact['model_agreement'] == pytest.approx(
            self._solved('ordered')['model_agreement'], abs=1.5)


def test_a_lone_phase_leaves_amodel_and_aoverall_together():
    """
    The structural claim, on a model with no ordering in it: one Phase
    constrains nothing between its dates, so the joint index and the product of
    the per-date ones have nothing to disagree about. OxCal's second fixture is
    exactly this shape (tests/fixtures/oxcal_outlier, sixteen dates in one
    phase) and reports Amodel 82.9 and Aoverall 82.9 -- the same number twice.
    """
    from app.services import gibbs
    measurements = [
        {'id': f'lone_{i}', 'sample_name': f'S{i}', 'age_bp': age,
         'error_1sigma': 30, 'curve_id': 'intcal20', 'element_id': 'lone_phase'}
        for i, age in enumerate((2900, 2960, 3010, 3050, 3120))
    ]
    tree = {'id': 'lone_seq', 'element_type': 'sequence', 'name': 'Site',
            'tpq_age_bp': None, 'taq_age_bp': None,
            'children': [{'id': 'lone_phase', 'element_type': 'phase', 'name': 'Phase',
                          'tpq_age_bp': None, 'taq_age_bp': None, 'children': []}]}
    solved = gibbs.solve(measurements, tree, num_samples=200)
    overall = agreement.overall_agreement([
        agreement.agreement_index(
            m, solved['measurements'][m['id']]['probability_distribution'])
        for m in measurements
    ])
    assert solved['model_agreement'] == pytest.approx(overall, abs=2.0), (
        solved['model_agreement'], overall)
