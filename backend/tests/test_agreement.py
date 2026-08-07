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
    Guards the decision not to report Amodel. On the `ordered` run the
    per-date indices multiply to more than one, so every exponent of that
    product lands above 100 while OxCal reports 96.0 -- Amodel must carry a
    term for the model structure that these indices do not contain. If a
    future change claims to compute Amodel, this documents what it has to
    account for.
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
        created = client.post('/api/analysis/bayesian', json={
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
