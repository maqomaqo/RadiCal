"""
Wiggle matching -- services/wiggle_match.py, and the endpoint that reaches it.

The solver is closed form, so most of what is checked here is checkable
exactly rather than statistically: a one-member series must reproduce that
date's own calibration, an all-zero-gap series must reproduce the pooled
product of its members, and a V_Sequence must converge on the D_Sequence as
its gap uncertainties go to zero. Those three pin the maths from three
directions without needing OxCal.

The two that are NOT about the maths are the important ones:

  * THE SIGN. A wiggle match with the sign inverted does not produce
    nonsense; it produces a tight range in the wrong place, which reads
    exactly like a good answer. TestTheSignConvention runs on a stretch of
    IntCal20 chosen so that a reversed series lands somewhere clearly wrong
    rather than merely shifted -- a symmetric region would pass with the sign
    inverted, which is the failure this fixture exists to catch.

  * THE GRID. The whole point of services/wiggle_match.py building its own
    grid rather than borrowing exact_chain's is resolution (see its module
    docstring). TestTheGrid halves the step and requires the reported range
    not to move, which is the check that would have failed on the borrowed
    one.
"""
import itertools

import numpy as np
import pytest

from app.services import wiggle_match
from app.services.calibration import SIGMA_2, get_curve, hpd_ranges_from_distribution
from app.services.model_tree import WiggleGroup

_IDS = itertools.count(1)


# --------------------------------------------------------------------------
# Building a series whose answer is known
# --------------------------------------------------------------------------

def simulated_series(true_reference, offsets, error=25.0, curve_id='intcal20',
                     noise=None, prefix='d'):
    """
    A series of measurements taken FROM the curve at known calendar ages, so
    the answer the match should recover is known independently of the model --
    the closest thing available to the dendrochronologically-dated series that
    makes this method's ground truth real.

    `offsets` are ring offsets: years BEFORE the reference, so member i sits
    at `true_reference + offsets[i]` cal BP.
    """
    curve = get_curve(curve_id)
    rng = None if noise is None else np.random.default_rng(noise)
    measurements = []
    for index, offset in enumerate(offsets):
        c14, _error = curve.interpolate(np.array([true_reference + offset]))
        age = float(c14[0])
        if rng is not None:
            age += float(rng.normal(0.0, error))
        measurements.append({
            'id': f'{prefix}{index}',
            'sample_name': f'Ring {offset:g}',
            'age_bp': round(age),
            'error_1sigma': error,
            'material_type': 'wood',
            'sample_type': 'terrestrial',
            'curve_id': curve_id,
            'element_id': 'w',
            'ring_offset': float(offset),
        })
    return measurements


def group_for(measurements, errors=None, **kwargs):
    members = [
        (m['id'], float(m['ring_offset']),
         float((errors or {}).get(m['id'], m.get('ring_offset_error') or 0.0)))
        for m in measurements
    ]
    return WiggleGroup('w', 'Timber', members, **kwargs)


def solve(measurements, **kwargs):
    return wiggle_match.solve(measurements, group_for(measurements, **kwargs))


def width_95(summary):
    return summary['hpd_95'][1] - summary['hpd_95'][0]


# --------------------------------------------------------------------------

class TestGroundTruth:
    """
    A series taken off the curve at a known age has to come back at that age.
    Nothing else in this app has ground truth of this kind: a dendro-dated
    series has a felling date established without any model at all, and this
    is the same statement made against the curve directly.
    """

    def test_a_clean_series_recovers_the_age_it_was_built_at(self):
        measurements = simulated_series(2500.0, np.linspace(0, 250, 8))
        result = solve(measurements)
        assert abs(result['reference']['posterior_median'] - 2500.0) < 10.0

    def test_every_member_is_the_reference_plus_its_own_gap(self):
        """
        The members are ONE distribution translated, not n results. Their
        medians must therefore differ by exactly their gaps, to well under the
        grid step -- which is the arithmetic the results panel is claiming
        when it says so.
        """
        offsets = np.linspace(0, 250, 8)
        measurements = simulated_series(2500.0, offsets)
        result = solve(measurements)
        reference = result['reference']['posterior_median']
        for m in measurements:
            member = result['measurements'][m['id']]['posterior_median']
            assert member == pytest.approx(reference + m['ring_offset'], abs=1e-6)

    def test_a_series_the_gaps_cannot_fit_is_refused_rather_than_forced(self):
        """
        A gap of 250 rings between two dates 3,000 radiocarbon years apart is
        not a series. The model has no slack to absorb it -- that is what
        makes the gaps HARD -- so the honest answer is a refusal naming the
        problem, not a confident range somewhere in between.
        """
        measurements = simulated_series(2500.0, [0.0, 250.0])
        measurements[1]['age_bp'] = 200.0
        with pytest.raises(wiggle_match.WiggleMatchError) as raised:
            solve(measurements)
        assert 'gaps' in str(raised.value)


class TestTheOutlierModelIsRefused:
    """
    A wiggle match cannot apply one, and the honest response to being handed
    one is to say so.

    An outlier model gives a date room to sit apart from the others. A wiggle
    match has none to give: every member is the same calendar age plus a fixed
    number, so a flagged member would simply be matched as though it were not
    flagged -- the likelihoods here come from exact_chain._date_likelihood, and
    the outlier mixture lives a level up in date_tables. That silent drop is
    the failure these tests exist to prevent.
    """

    def test_a_flagged_member_is_refused_by_the_solver(self):
        measurements = simulated_series(2500.0, [0.0, 120.0])
        measurements[1]['outlier_prior'] = 0.05
        with pytest.raises(wiggle_match.WiggleMatchError) as raised:
            solve(measurements)
        assert 'outlier model' in str(raised.value)
        assert 'agreement index' in str(raised.value)

    def test_a_zero_prior_is_not_a_flag(self):
        # Every date carries the field; only a positive prior is a setting.
        measurements = simulated_series(2500.0, np.linspace(0, 250, 8))
        for m in measurements:
            m['outlier_prior'] = 0.0
            m['outlier_model'] = 'general'
        assert solve(measurements)['reference']['posterior_median'] > 0

    def test_the_edge_refuses_it_too_and_names_the_dates(self):
        from app.services.model_tree import validate_ordinates

        tree = _wiggle_tree('ov')
        measurements = simulated_series(2500.0, [0.0, 120.0], prefix='ov-')
        for m in measurements:
            m['element_id'] = tree['id']
        measurements[0]['outlier_prior'] = 1.0

        with pytest.raises(ValueError) as raised:
            validate_ordinates(tree, measurements)
        assert measurements[0]['sample_name'] in str(raised.value)
        assert 'outlier model' in str(raised.value)


class TestTheSignConvention:
    """
    ring_offset counts years BEFORE the reference: 0 at the reference,
    increasing into the past, so a member's calendar age is the reference's
    PLUS its offset.

    Run on an asymmetric stretch of IntCal20, deliberately. A reversed series
    on a symmetric one comes back merely shifted and every assertion still
    passes; here it has to land somewhere clearly wrong.
    """

    TRUE_AGE = 3200.0
    OFFSETS = np.linspace(0, 250, 8)

    def test_the_series_recovers_its_age_the_right_way_round(self):
        measurements = simulated_series(self.TRUE_AGE, self.OFFSETS)
        result = solve(measurements)
        assert abs(result['reference']['posterior_median'] - self.TRUE_AGE) < 15.0

    def test_reversing_the_gaps_does_not_quietly_still_work(self):
        """
        The same measurements with every gap negated: the series now claims
        the outermost ring is the oldest. If the sign were being applied the
        other way round this would be the reading that worked, so it has to
        fail visibly -- either no fit at all, or a fit far from the truth.
        """
        measurements = simulated_series(self.TRUE_AGE, self.OFFSETS)
        for m in measurements:
            m['ring_offset'] = -m['ring_offset']
        try:
            result = solve(measurements)
        except wiggle_match.WiggleMatchError:
            return                     # no fit at all is the clearest failure
        offset = abs(result['reference']['posterior_median'] - self.TRUE_AGE)
        assert offset > 100.0, (
            'a series read with the gaps reversed landed within 100 years of '
            f'the truth ({offset:.0f}), so this fixture cannot tell the sign '
            'convention apart from its inverse -- pick a more asymmetric '
            'stretch of curve')

    def test_the_older_member_is_the_one_with_the_larger_gap(self):
        measurements = simulated_series(self.TRUE_AGE, [0.0, 120.0])
        result = solve(measurements)
        younger = result['measurements'][measurements[0]['id']]
        older = result['measurements'][measurements[1]['id']]
        # cal BP counts backwards, so older is the LARGER number.
        assert older['posterior_median'] > younger['posterior_median']


class TestDegenerateCases:
    def test_one_member_is_that_date_calibrated_on_its_own(self):
        """
        With nothing to match against, a wiggle match must be an ordinary
        calibration and not a slightly different one.
        """
        measurements = simulated_series(2500.0, [0.0])
        result = solve(measurements)

        curve = get_curve('intcal20')
        cal, prob = curve.calibrate(
            measurements[0]['age_bp'], measurements[0]['error_1sigma'])
        expected = curve.get_point_estimate(cal, prob)

        assert result['reference']['posterior_median'] == pytest.approx(
            expected['median'], abs=3.0)
        assert result['reference']['posterior_mean'] == pytest.approx(
            expected['mean'], abs=3.0)

    def test_all_gaps_zero_is_the_pooled_product_of_the_members(self):
        """
        Every member at the same ring is four measurements of one event, so
        the answer is the product of their likelihoods -- computed here
        straight off the curve, with no part of the solver involved.
        """
        measurements = simulated_series(2500.0, [0.0, 0.0, 0.0, 0.0],
                                        noise=7, prefix='z')
        for index, m in enumerate(measurements):
            m['id'] = f'z{index}'
        group = WiggleGroup('w', 'Pooled',
                            [(m['id'], 0.0, 0.0) for m in measurements])
        result = wiggle_match.solve(measurements, group)

        curve = get_curve('intcal20')
        grid = np.arange(2300.0, 2700.0, 1.0)
        c14, curve_error = curve.interpolate(grid)
        product = np.ones(len(grid))
        for m in measurements:
            sigma2 = curve_error ** 2 + m['error_1sigma'] ** 2
            product *= np.exp(-0.5 * (c14 - m['age_bp']) ** 2 / sigma2) / np.sqrt(sigma2)
        mass = product / product.sum()
        # Each grid point stands for a one-year cell, so the cumulative mass
        # through a cell is reached at its far edge, not its centre.
        edges = np.concatenate([[grid[0] - 0.5], grid + 0.5])
        expected_median = float(np.interp(0.5, np.concatenate([[0.0], np.cumsum(mass)]), edges))

        assert result['reference']['posterior_median'] == pytest.approx(
            expected_median, abs=1.0)


class TestGapUncertainty:
    """V_Sequence: each gap measured rather than counted."""

    def test_a_vanishing_gap_uncertainty_reproduces_the_fixed_gap_answer(self):
        measurements = simulated_series(2500.0, np.linspace(0, 250, 8))
        fixed = solve(measurements)
        errors = {m['id']: 1e-6 for m in measurements}
        vanishing = solve(measurements, errors=errors)
        assert (vanishing['reference']['posterior_median']
                == pytest.approx(fixed['reference']['posterior_median'], abs=0.5))

    def test_uncertain_gaps_widen_the_answer_rather_than_moving_it(self):
        """
        A gap that was measured to +/- 20 years says less than one that was
        counted, and the extra uncertainty belongs in the answer. It should
        not, however, move the answer: the gaps are unbiased.
        """
        measurements = simulated_series(2500.0, np.linspace(0, 250, 8))
        fixed = solve(measurements)
        errors = {m['id']: (0.0 if m['ring_offset'] == 0 else 20.0)
                  for m in measurements}
        loose = solve(measurements, errors=errors)
        assert width_95(loose['reference']) > width_95(fixed['reference'])
        assert (loose['reference']['posterior_median']
                == pytest.approx(fixed['reference']['posterior_median'], abs=20.0))

    def test_a_measured_gap_widens_its_own_member_beyond_the_reference(self):
        """
        t_i = t + G_i. Knowing the series' age does not place a ring whose
        position in the series is itself uncertain, so on this series that
        member comes back wider than the reference -- not as the reference
        moved bodily, which is only right for a counted gap. (Not a law: a
        member whose own measurement is sharper than the series can come back
        narrower. The brute-force test below pins the maths either way.)
        """
        measurements = simulated_series(2500.0, np.linspace(0, 250, 8))
        loose_id = measurements[4]['id']
        result = solve(measurements, errors={loose_id: 20.0})
        reference = result['reference']
        loose = result['measurements'][loose_id]
        counted = result['measurements'][measurements[3]['id']]
        assert loose['posterior_std'] > reference['posterior_std'] + 1.0
        assert counted['posterior_std'] == pytest.approx(
            reference['posterior_std'], abs=1e-6)
        assert loose['posterior_median'] == pytest.approx(
            reference['posterior_median'] + measurements[4]['ring_offset'], abs=10.0)

    def test_a_measured_member_matches_a_brute_force_integral(self):
        """
        The closed form in _member_density against the joint (t, G_i)
        integrated directly on a grid, with no convolution anywhere.
        """
        measurements = simulated_series(2500.0, np.linspace(0, 250, 8), noise=3)
        errors = {m['id']: 15.0 for m in measurements}
        result = solve(measurements, errors=errors)
        members = [(m, float(m['ring_offset']), errors[m['id']]) for m in measurements]
        grid = wiggle_match._build_grid(members, {})
        terms = wiggle_match._log_terms(grid, members, {})
        step = grid[1] - grid[0]
        for index in (0, 4, 7):
            measurement, gap, sigma = members[index]
            rest = wiggle_match._density(
                np.sum([t for k, t in enumerate(terms) if k != index], axis=0))
            ages, weights = [], []
            for shift in np.arange(gap - 5 * sigma, gap + 5 * sigma + step, step):
                prior = np.exp(-0.5 * ((shift - gap) / sigma) ** 2)
                likelihood = wiggle_match._date_likelihood(grid + shift, measurement, (None, None))
                ages.append(grid + shift)
                weights.append(rest * likelihood * prior)
            ages = np.concatenate(ages)
            weights = np.concatenate(weights)
            weights /= weights.sum()
            mean = float((ages * weights).sum())
            sd = float(np.sqrt(((ages - mean) ** 2 * weights).sum()))
            member = result['measurements'][measurement['id']]
            assert member['posterior_mean'] == pytest.approx(mean, abs=0.1)
            assert member['posterior_std'] == pytest.approx(sd, abs=0.1)

    def test_a_vanishing_gap_uncertainty_leaves_the_member_translated(self):
        measurements = simulated_series(2500.0, np.linspace(0, 250, 8))
        fixed = solve(measurements)
        vanishing = solve(measurements, errors={m['id']: 1e-6 for m in measurements})
        for m in measurements:
            assert (vanishing['measurements'][m['id']]['posterior_std']
                    == pytest.approx(fixed['measurements'][m['id']]['posterior_std'], abs=0.2))


class TestTheGrid:
    """
    services/wiggle_match.py builds its own grid instead of borrowing
    exact_chain's, because that one pads for boundaries a wiggle match does
    not have and pays for the padding in resolution. The check that the step
    is fine enough is that halving it does not move the answer.
    """

    def test_halving_the_step_does_not_move_the_reported_range(self, monkeypatch):
        measurements = simulated_series(2500.0, np.linspace(0, 250, 8))
        at_one_year = solve(measurements)
        monkeypatch.setattr(wiggle_match, 'MIN_GRID_STEP_YEARS', 0.5)
        at_half_year = solve(measurements)

        assert at_half_year['grid']['step'] == pytest.approx(0.5)
        assert (at_half_year['reference']['hpd_95'][0]
                == pytest.approx(at_one_year['reference']['hpd_95'][0], abs=5.0))
        assert (at_half_year['reference']['hpd_95'][1]
                == pytest.approx(at_one_year['reference']['hpd_95'][1], abs=5.0))
        assert (at_half_year['reference']['posterior_median']
                == pytest.approx(at_one_year['reference']['posterior_median'], abs=1.0))

    def test_the_window_covers_every_member_rather_than_clipping_the_oldest(self):
        """
        Each member is read at the reference age PLUS its own gap, so the
        calendar range the CURVE is read over is wider than the reference's
        own by the series length. Getting that wrong truncates the oldest
        member silently, because the curve clamps rather than raising.
        """
        offsets = np.linspace(0, 600, 6)
        measurements = simulated_series(2500.0, offsets)
        result = solve(measurements)
        oldest = result['measurements'][measurements[-1]['id']]
        distribution = np.array(oldest['probability_distribution'])
        peak = distribution[:, 1].max()
        # The oldest member's own posterior must fall away at both ends
        # rather than being cut off at the window edge.
        assert distribution[0, 1] < peak * 1e-3
        assert distribution[-1, 1] < peak * 1e-3


class TestWhatTheMatchBuys:
    """
    The reason the feature exists: at a plateau a single date is hopeless and
    a long enough series is not. Asserted as a RATIO rather than against a
    remembered figure, so the test says "much better" without pinning a number
    that a curve update would move.
    """

    PLATEAU = 2500.0

    def test_a_long_series_collapses_a_plateau_a_single_date_cannot(self):
        one = simulated_series(self.PLATEAU, [0.0])
        alone = solve(one)
        series = simulated_series(self.PLATEAU, np.linspace(0, 250, 8))
        matched = solve(series)

        assert width_95(matched['reference']) < width_95(alone['reference']) / 5.0
        # ...and it resolves to a single range, where the lone date does not.
        assert len(alone['reference']['hpd_95_ranges']) > 1
        assert len(matched['reference']['hpd_95_ranges']) == 1

    def test_length_matters_more_than_count(self):
        """
        Twelve samples packed into 60 rings do not break a plateau; eight
        spread over 250 do. It is the statement the pre-run warning in the
        entry table is built on, so it is asserted rather than assumed.
        """
        packed = solve(simulated_series(self.PLATEAU, np.linspace(0, 60, 12)))
        spread = solve(simulated_series(self.PLATEAU, np.linspace(0, 250, 8)))
        assert width_95(spread['reference']) < width_95(packed['reference'])


class TestAgreement:
    """
    A D_Sequence asserts the ring counts are right and cannot widen to absorb
    a member that does not fit, so the agreement index is not a diagnostic
    beside the answer -- it is the check that the match is real.
    """

    def test_a_clean_series_agrees_with_itself(self):
        result = solve(simulated_series(2500.0, np.linspace(0, 250, 8)))
        assert result['combination_agreement'] > 60.0
        assert all(index > 60.0 for index in result['agreement'].values())

    def test_a_member_at_the_wrong_ring_shows_up_in_the_index(self):
        measurements = simulated_series(2500.0, np.linspace(0, 250, 8))
        clean = solve(measurements)
        # One sample misidentified by 120 rings: the gaps are hard, so the
        # model cannot move to accommodate it and everything fits worse.
        measurements[3]['ring_offset'] += 120.0
        spoiled = solve(measurements)
        assert spoiled['combination_agreement'] < clean['combination_agreement']


# --------------------------------------------------------------------------
# Through the API
# --------------------------------------------------------------------------

def _wiggle_tree(tag, params=None):
    return {
        'id': f'{tag}-w', 'element_type': 'wiggle_sequence', 'name': 'Timber 1',
        'params': params or {'gap_units': 'rings'}, 'children': [],
    }


def _payload(tag, tree, measurements, name=None):
    for m in measurements:
        m['element_id'] = tree['id'] if tree['element_type'] == 'wiggle_sequence' \
            else m['element_id']
    return {
        'name': name or f'Wiggle {tag}',
        'measurements': measurements,
        'model_tree': tree,
        'save': True,
    }


class TestThroughTheApi:
    def test_a_standalone_series_runs_and_reports_the_reference_age(self, client):
        tag = f'wm{next(_IDS)}'
        tree = _wiggle_tree(tag)
        measurements = simulated_series(2500.0, np.linspace(0, 250, 8),
                                        prefix=f'{tag}-')
        response = client.post('/api/analysis/bayesian?wait=true',
                               json=_payload(tag, tree, measurements))
        assert response.status_code == 200, response.text

        analysis_id = response.json()['data']['id']
        data = client.get(f'/api/analysis/{analysis_id}').json()['data']
        assert data['solver']['method'] == 'wiggle_match'
        assert data['solver']['combination_agreement'] is not None
        assert data['solver']['series_span'] == pytest.approx(250.0)

        element = data['model_tree']
        assert element['element_type'] == 'wiggle_sequence'
        assert element['posterior'] is not None
        assert abs(element['posterior']['posterior_median'] - 2500.0) < 10.0
        # Every member got a result, and the ring offsets survived the round
        # trip -- a wiggle match IS its gaps, so an edit that lost them would
        # silently rerun a different model.
        assert len(data['results']) == len(measurements)
        assert {m['ring_offset'] for m in data['measurements']} == {
            float(m['ring_offset']) for m in measurements}

    def test_the_reference_member_survives_being_saved_and_rerun(self, client):
        """
        The reference names a SAMPLE, and saving mints new sample ids -- so a
        reference left at the request's own id would point at nothing, and the
        next run of the analysis would be refused for naming a member it does
        not have.
        """
        tag = f'wm{next(_IDS)}'
        measurements = simulated_series(2500.0, np.linspace(0, 250, 6),
                                        prefix=f'{tag}-')
        tree = _wiggle_tree(tag, params={
            'gap_units': 'rings',
            'reference_sample_id': measurements[0]['id'],
        })
        for m in measurements:
            m['element_id'] = tree['id']

        created = client.post('/api/analysis/bayesian?wait=true', json={
            'name': f'Reference {tag}', 'measurements': measurements,
            'model_tree': tree, 'save': True})
        assert created.status_code == 200, created.text
        analysis_id = created.json()['data']['id']

        data = client.get(f'/api/analysis/{analysis_id}').json()['data']
        stored = {m['sample_name']: m['id'] for m in data['measurements']}
        assert data['model_tree']['params']['reference_sample_id'] == stored['Ring 0']
        assert data['model_tree']['params']['reference_sample_id'] not in {
            m['id'] for m in measurements}, (
            'the reference still names the id the request used, not the one '
            'the sample was saved under')
        assert data['solver']['reference_sample_id'] is not None

        rerun = client.post(f'/api/analysis/{analysis_id}/rerun?wait=true')
        assert rerun.status_code == 200, rerun.text
        again = client.get(f'/api/analysis/{analysis_id}').json()['data']
        assert again['status'] == 'completed'
        assert again['solver']['method'] == 'wiggle_match'
        assert abs(again['model_tree']['posterior']['posterior_median'] - 2500.0) < 10.0

    def test_a_wiggle_match_inside_a_larger_model_is_refused_by_name(self, client):
        """
        Refused, not approximated. Reading the series as n free dates in a
        phase is the only thing the existing solvers could do with it, and it
        would report a match that was never made.
        """
        tag = f'wm{next(_IDS)}'
        wiggle = _wiggle_tree(tag)
        tree = {
            'id': f'{tag}-root', 'element_type': 'sequence', 'name': 'Site',
            'children': [
                {'id': f'{tag}-b0', 'element_type': 'boundary',
                 'name': 'Start', 'children': []},
                wiggle,
                {'id': f'{tag}-b1', 'element_type': 'boundary',
                 'name': 'End', 'children': []},
            ],
        }
        measurements = simulated_series(2500.0, [0.0, 120.0], prefix=f'{tag}-')
        for m in measurements:
            m['element_id'] = wiggle['id']
        response = client.post(
            '/api/analysis/bayesian?wait=true',
            json={'name': f'Nested {tag}', 'measurements': measurements,
                  'model_tree': tree, 'save': True})
        assert response.status_code == 400, response.text
        assert 'wiggle match inside a larger structure' in response.text

    def test_a_member_with_no_gap_is_refused_at_the_edge(self, client):
        tag = f'wm{next(_IDS)}'
        tree = _wiggle_tree(tag)
        measurements = simulated_series(2500.0, [0.0, 120.0], prefix=f'{tag}-')
        for m in measurements:
            m['element_id'] = tree['id']
        measurements[1].pop('ring_offset')
        response = client.post(
            '/api/analysis/bayesian?wait=true',
            json={'name': f'Gapless {tag}', 'measurements': measurements,
                  'model_tree': tree, 'save': True})
        assert response.status_code == 422, response.text
        assert 'gap' in response.text

    def test_two_members_at_the_same_gap_are_refused(self, client):
        tag = f'wm{next(_IDS)}'
        tree = _wiggle_tree(tag)
        measurements = simulated_series(2500.0, [40.0, 40.0], prefix=f'{tag}-')
        for m in measurements:
            m['element_id'] = tree['id']
        response = client.post(
            '/api/analysis/bayesian?wait=true',
            json={'name': f'Doubled {tag}', 'measurements': measurements,
                  'model_tree': tree, 'save': True})
        assert response.status_code == 422, response.text
        assert 'same gap' in response.text

    def test_a_flagged_member_is_refused_at_the_request(self, client):
        """
        Refused at the edge, with a 422, rather than accepted and dropped. The
        form offers the outlier switch on any Bayesian analysis, so a date can
        arrive carrying one; what must not happen is that it is stored,
        submitted, and quietly not applied.
        """
        tag = f'wm{next(_IDS)}'
        tree = _wiggle_tree(tag)
        measurements = simulated_series(2500.0, [0.0, 120.0], prefix=f'{tag}-')
        for m in measurements:
            m['element_id'] = tree['id']
        measurements[1]['outlier_prior'] = 0.05

        response = client.post(
            '/api/analysis/bayesian?wait=true',
            json={'name': f'Flagged {tag}', 'measurements': measurements,
                  'model_tree': tree, 'save': True})
        assert response.status_code == 422, response.text
        assert 'outlier model' in response.text

    def test_asking_for_the_sampler_on_a_wiggle_match_says_why_not(self, client):
        tag = f'wm{next(_IDS)}'
        tree = _wiggle_tree(tag)
        measurements = simulated_series(2500.0, [0.0, 120.0], prefix=f'{tag}-')
        for m in measurements:
            m['element_id'] = tree['id']
        response = client.post(
            '/api/analysis/bayesian?wait=true',
            json={'name': f'Sampled {tag}', 'measurements': measurements,
                  'model_tree': tree, 'save': True,
                  'bayesian_config': {'solver': 'gibbs'}})
        assert 'nothing to sample' in response.text
