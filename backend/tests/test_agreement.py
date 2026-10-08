"""
Agreement indices (services/agreement.py), against OxCal 4.4 runs:
tests/fixtures/oxcal (Hillside, three models, OxCal's default five-year
resolution), tests/fixtures/oxcal_outlier (Dune Section, an outlier model,
five years) and tests/fixtures/oxcal_resolution (Hillside, Dune Section
and DS rerun at Resolution=1, which is what the definition is held to -- see
the module docstring for why).

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
import oxcal_resolution_fixture as rf


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


class TestReproducesOxCalAtOneYear:
    """
    OxCal's posterior in, OxCal's index out, on the Hillside `ordered` model
    rerun at Resolution=1 -- OxCal's index at the resolution where it has
    converged, which is the quantity the definition computes.
    """

    def _errors(self, own_likelihood):
        dists = rf.distributions(rf.HILLSIDE)
        table, _amodel, _aoverall = rf.table(rf.HILLSIDE)
        indices, errors = [], []
        for m in rf.measurements(rf.HILLSIDE):
            got = agreement.agreement_index(
                m, dists[m['id']]['posterior'],
                unmodelled=None if own_likelihood else dists[m['id']]['likelihood'])
            assert got is not None
            indices.append(got)
            errors.append(got - table[m['id']][0])
        return indices, np.array(errors)

    def test_individual_indices(self):
        """
        The formula itself: OxCal's likelihood AND OxCal's posterior in, so
        nothing but the definition is under test. Measured rms 0.23.
        """
        _indices, errors = self._errors(own_likelihood=False)
        assert np.abs(errors).max() < 1.0, errors.round(2)
        assert np.sqrt((errors ** 2).mean()) < 0.5

    def test_our_own_likelihood_gives_the_same_answer(self):
        """The realistic path, our calibration in place of OxCal's: rms 0.30."""
        _indices, errors = self._errors(own_likelihood=True)
        assert np.abs(errors).max() < 1.0, errors.round(2)
        assert np.sqrt((errors ** 2).mean()) < 0.5

    def test_overall_index(self):
        indices, _errors = self._errors(own_likelihood=True)
        _table, _amodel, aoverall = rf.table(rf.HILLSIDE)
        assert agreement.overall_agreement(indices) == pytest.approx(aoverall, abs=0.5)


@pytest.mark.parametrize('run', RUNS)
def test_oxcals_default_resolution_reads_lower(run):
    """
    OxCal at its default five years is a coarser approximation of the same
    integrals, and on these dates it reads about 0.7 below the converged
    value per date -- a consistent offset, not scatter. Pinned so the
    difference an analyst sees against a default OxCal run stays explained.
    """
    dists = ox.distributions(run)
    errors = np.array([
        agreement.agreement_index(m, _as_pairs(*dists[m['id']]['posterior']))
        - REPORTED[run]['dates'][m['id']]
        for m in ox.measurements()
    ])
    assert 0.3 < errors.mean() < 1.0, errors.mean()
    assert np.abs(errors - errors.mean()).max() < 0.6


@pytest.mark.parametrize('stem', [rf.HILLSIDE, rf.DUNE, rf.DS, rf.DS_ONE_YEAR])
def test_the_combination_reproduces_oxcals_aoverall(stem):
    """
    Isolating the combining rule from the per-date one: fed OxCal's own
    published indices, the combination must return OxCal's own overall -- at
    either resolution, with the outlier models' scale terms counted in n.
    """
    table, _amodel, aoverall = rf.table(stem)
    measurements = rf.measurements(stem)
    got = agreement.overall_agreement([table[m['id']][0] for m in measurements],
                                      agreement.outlier_scale_terms(measurements))
    assert got == pytest.approx(aoverall, abs=0.15)


@pytest.mark.parametrize('run', RUNS)
def test_the_combination_reproduces_oxcals_default_aoverall(run):
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
        import itertools
        # The results table only: blocks after the first blank line
        # (durations, KDE) have headers of their own.
        rows = list(itertools.takewhile(bool, _csv.reader(io.StringIO(
            client.get(f'/api/results/{analysis_id}/export/csv').text))))
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
    (tests/fixtures/oxcal_outlier: Dune Section, 16 dates, one phase,
    Outlier_Model active). An independent site, structure and curve region
    from the fixture the formula was identified on.

    It also pins something the first fixture could not show: with an outlier
    model running, OxCal scores each date against the likelihood term the
    outlier model puts in the model rather than the bare calibration, so an
    index fed the bare likelihood -- which is what OxCal's export holds, and
    what these tests feed in -- reads systematically low against it. The app's
    own path does not: see TestOutlierModels.
    """

    def test_agreement_flags_the_outlier_and_nothing_else(self):
        import dune_section_fixture as sf
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
        import dune_section_fixture as sf
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
        tail; against the raw likelihood it reads ~0. Both say the same
        thing for screening, but the overall index is built from a product
        and is therefore hypersensitive to the difference -- 82.9 against
        OxCal's own value, 15.6 from the raw one. That gap is what scoring the
        outlier model's own likelihood term closes (TestOutlierModels).
        """
        import dune_section_fixture as sf
        dists, table = sf.distributions(), sf.table()
        m = next(x for x in sf.measurements() if x['id'] == sf.OUTLIER)
        got = agreement.agreement_index(
            m, np.column_stack(dists[m['id']]['posterior']),
            unmodelled=np.column_stack(dists[m['id']]['likelihood']))
        assert got < 1.0
        assert table[sf.OUTLIER]['agreement'] < 10.0

        from_oxcal = agreement.overall_agreement(
            [table[x['id']]['agreement'] for x in sf.measurements()],
            agreement.outlier_scale_terms(sf.measurements()))
        assert from_oxcal == pytest.approx(table['model']['Aoverall'], abs=0.2)


class TestOutlierScaleTerms:
    """
    OxCal counts each outlier model's scale parameter -- A = 100 in its table
    -- in the n of Aoverall and Amodel. Without it, OxCal's own per-date
    indices give 82.3 on Dune Section against its reported 82.9, and 44.5
    on DS against 45.9; test_the_combination_reproduces_oxcals_aoverall holds
    the rule on every run with an outlier model.
    """

    def test_leaving_the_term_out_misses(self):
        table, _amodel, aoverall = rf.table(rf.DS)
        indices = [table[m['id']][0] for m in rf.measurements(rf.DS)]
        assert agreement.overall_agreement(indices) < aoverall - 1.0

    def test_one_term_per_outlier_model_in_use(self):
        def m(prior, model=None):
            return {'outlier_prior': prior, 'outlier_model': model}
        assert agreement.outlier_scale_terms([m(None), m(0.0)]) == 0
        assert agreement.outlier_scale_terms([m(0.05), m(0.05, 'general')]) == 1
        assert agreement.outlier_scale_terms([m(0.05), m(1.0, 'charcoal')]) == 2


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


def _amodel(solved, measurements):
    """A solved run's Amodel, scored as api/analysis._model_agreement does."""
    return agreement.model_agreement(
        measurements, solved['joint_draws'], solved.get('joint_scales'))


class TestModelIndexAgainstOxCal:
    """
    A_model end to end, against the Amodel OxCal reports -- the number an
    analyst actually screens a model on, so the claim
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

    def test_reproduces_oxcals_amodel(self):
        """Against OxCal at one year, 100.6: measured 100.9."""
        got = _amodel(self._solved('ordered'), ox.measurements())
        _table, amodel, _aoverall = rf.table(rf.HILLSIDE)
        assert got == pytest.approx(amodel, abs=1.5), got

    @pytest.mark.parametrize('run', RUNS)
    def test_oxcals_default_resolution_reads_lower(self, run):
        """
        OxCal's default five-year runs read 4.6 lower than its one-year run on
        the `ordered` model, and ours read about that far above each of the
        three default runs: measured 4.9, 5.1 and 5.4.
        """
        got = _amodel(self._solved(run), ox.measurements())
        assert 3.0 < got - REPORTED[run]['model'] < 7.0, got

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
                         - _amodel(solved, ox.measurements()))
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
        assert _amodel(exact, measurements) == pytest.approx(
            _amodel(self._solved('ordered'), measurements), abs=1.5)


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
    amodel = _amodel(solved, measurements)
    assert amodel == pytest.approx(overall, abs=2.0), (amodel, overall)


def _scale_posterior(stem):
    """
    OxCal's exported outlier-scale posterior, binned onto the solvers' scale
    nodes: (log10 scales, weights).
    """
    from app.services.exact_chain import scale_nodes
    u, w = rf.distributions(stem)['U']
    nodes = scale_nodes('general')
    index = np.clip(np.rint((u - nodes[0]) / (nodes[1] - nodes[0])).astype(int),
                    0, len(nodes) - 1)
    weights = np.bincount(index, weights=w, minlength=len(nodes))
    keep = weights > 1e-4
    return nodes[keep], weights[keep] / weights[keep].sum()


class TestOutlierModels:
    """
    A date under an outlier model is scored against the likelihood term the
    model puts in Bayes' theorem as it is specified -- the calibration, or the
    calibration moved by a sampled shift with the outlier PRIOR -- with the
    expectation taken over the shift and the scale as well as the date (see
    services/agreement.py).
    """

    def _measurement(self, tag='oc_x', age=3000, prior=None, model=None):
        return {'id': tag, 'sample_name': tag, 'age_bp': age, 'error_1sigma': 30,
                'curve_id': 'intcal20', 'outlier_prior': prior, 'outlier_model': model}

    def _pushed(self, m, offset, width=15.0, lo=2600.0, hi=4200.0):
        grid = np.arange(lo, hi, 1.0)
        peak = grid[np.argmax(agreement.unmodelled_likelihood(m, grid))]
        return np.column_stack([grid, np.exp(-0.5 * ((grid - (peak + offset)) / width) ** 2)])

    def test_a_date_with_no_outlier_model_ignores_the_scale(self):
        m = self._measurement()
        posterior = self._pushed(m, 40.0)
        assert agreement.agreement_index(m, posterior, log_scale=2.5) == \
            agreement.agreement_index(m, posterior)

    def test_a_date_explained_only_as_an_outlier_scores_about_100p(self):
        """
        Pushed 400 years off its likelihood, the date fits only through the
        outlier branch -- which always fits, the shift going wherever it must,
        but carries the prior's weight and no more: about 100 p / ((1 - p)^2 +
        p^2), 5.5 at p = 0.05. Not the near-zero a bare comparison gives, and
        not rescued to anything like agreement either.
        """
        m = self._measurement(prior=0.05, model='general')
        score = agreement.agreement_index(m, self._pushed(m, -400.0), log_scale=2.5)
        assert score == pytest.approx(100 * 0.05 / (0.95 ** 2 + 0.05 ** 2), abs=0.5), score
        bare = self._measurement()
        assert agreement.agreement_index(bare, self._pushed(bare, -400.0)) < 0.1

    def test_a_date_the_data_cleared_reads_a_little_above_its_bare_index(self):
        """
        Left where its likelihood puts it, a date under a 5% outlier model
        scores its bare index lifted by the share of the likelihood term the
        inlier branch holds -- about 4%, as OxCal's own run shows (Dune Section
        Mouth's fifteen sound dates, 1.0426 +/- 0.002).
        """
        flagged = self._measurement(prior=0.05, model='general')
        bare = self._measurement()
        posterior = self._pushed(bare, 0.0, width=25.0)
        ratio = (agreement.agreement_index(flagged, posterior, log_scale=2.5)
                 / agreement.agreement_index(bare, posterior))
        assert 1.02 < ratio < 1.06, ratio

    def test_a_charcoal_date_moved_as_instructed_is_not_scored_a_failure(self):
        """
        Old wood, charcoal model at prior 1, and the model has put the context
        250 years younger than the wood -- exactly the move the model exists to
        make. Against the bare calibration that is a date pushed off its
        likelihood; against the model's own likelihood term it is not.
        """
        m = self._measurement(prior=1.0, model='charcoal')
        bare = self._measurement()
        assert agreement.agreement_index(bare, self._pushed(bare, -250.0, 20.0)) < 1.0
        assert agreement.agreement_index(m, self._pushed(m, -250.0, 20.0)) > 60.0

    def test_charcoal_does_not_excuse_a_date_moved_the_wrong_way(self):
        """
        The shift is one-sided: the context can be younger than old wood,
        never older. A charcoal date the model pushed OLDER is still a date the
        model had to fight, and must still read that way.
        """
        m = self._measurement(prior=1.0, model='charcoal')
        younger = agreement.agreement_index(m, self._pushed(m, -150.0, 20.0))
        older = agreement.agreement_index(m, self._pushed(m, 150.0, 20.0))
        assert older < younger / 5.0, (older, younger)

    @pytest.mark.parametrize('stem', rf.ONE_YEAR_OUTLIER_RUNS)
    def test_reproduces_oxcal_on_its_one_year_outlier_runs(self, stem):
        """
        OxCal's posteriors and its scale posterior in, OxCal's indices out --
        the flagged dates included. Measured per-date rms 0.47 on Dune Section
        Mouth (LB10002 5.2 against 5.4) and 0.42 on DS. The scale is taken as
        independent of each date here, which the exports force; a run pairs
        them draw by draw.
        """
        dists = rf.distributions(stem)
        table, _amodel, aoverall = rf.table(stem)
        measurements = rf.measurements(stem)
        scales, weights = _scale_posterior(stem)
        indices, errors = [], []
        for m in measurements:
            got = sum(w * agreement.agreement_index(m, dists[m['id']]['posterior'], log_scale=u)
                      for u, w in zip(scales, weights))
            indices.append(got)
            errors.append(got - table[m['id']][0])
        errors = np.array(errors)
        assert np.abs(errors).max() < 1.5, errors.round(2)
        assert np.sqrt((errors ** 2).mean()) < 0.8
        assert agreement.overall_agreement(
            indices, agreement.outlier_scale_terms(measurements)) == pytest.approx(aoverall, abs=1.5)

    def test_a_run_scores_the_flagged_date_as_oxcal_does(self):
        """
        End to end on Dune Section, RadiCal's own run: LB10002 is scored off
        the joint draws under the scale each state held, and the model's
        indices land on OxCal's one-year run (Amodel 83.9, Aoverall 84.2).
        Integrating the shift out instead gave LB10002 0.5 and Aoverall 48.9.
        """
        import dune_section_fixture as sf
        from app.services import gibbs
        measurements = sf.measurements()
        solved = gibbs.solve(measurements, sf.model_tree(), num_samples=200)
        overall = agreement.attach(solved['measurements'], measurements,
                                   solved['joint_draws'], solved['joint_scales'])
        flagged = solved['measurements'][sf.OUTLIER]['agreement_index']
        _table, amodel, aoverall = rf.table(rf.DUNE)
        assert 3.5 < flagged < 7.5, flagged
        assert overall == pytest.approx(aoverall, abs=4.0)
        assert _amodel(solved, measurements) == pytest.approx(amodel, abs=4.0)

    def test_amodel_uses_the_same_comparison(self):
        """
        Amodel and A_i are one definition read two ways, and that has to stay
        true for a date under an outlier model: on one date, at one scale, they
        must agree once both count the outlier model's scale term in n.
        """
        m = self._measurement(prior=1.0, model='charcoal')
        posterior = self._pushed(m, -150.0, 20.0)
        expected = agreement.overall_agreement(
            [agreement.agreement_index(m, posterior, log_scale=2.0)],
            agreement.outlier_scale_terms([m]))
        draws = _draws_from(posterior[:, 0], posterior[:, 1], np.random.default_rng(5), 40000)
        got = agreement.model_agreement(
            [m], {m['id']: draws}, {'charcoal': np.full(len(draws), 2.0)})
        assert got == pytest.approx(expected, rel=0.02), (got, expected)
