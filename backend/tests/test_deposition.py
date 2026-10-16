"""
Age-depth modelling -- OxCal's P_Sequence, in services/gibbs.py.

WHAT IS GROUND TRUTH HERE. Three dated levels make a three-dimensional
integral that can be computed directly on a grid, so the sampler can be pinned
against an exact answer without needing exact_chain to support the model at
all. TestAgainstBruteForce does that, and it is the load-bearing test in this
file: the brute force writes the deposition density out again from the paper's
formula rather than calling the sampler's own, so what is being checked is the
SAMPLER against the model.

That the MODEL is discretised correctly is a separate question, and the answer
to it is TestTheGrid: refine the step and the answer must not move. Arguing
about the quadrature is what that replaces.

The rest are the two limits (a rigidity so low it says nothing beyond the
ordering, one so high it forces a straight line in depth), the undated levels
that make "how old is 47 cm" answerable, and the one thing that would be
quietly wrong -- a deposition sequence's dates ALSO being treated as a uniform
scatter between the enclosing Boundaries, which would leave phase widths wrong
by a factor nobody could eyeball.

Every run here is untempered (temperatures=1, or mcmc_temperatures through
the API). Tempering is for crossing calibration lobes, which test_gibbs.py
tests, and it triples the cost of runs that ask nothing of it.
"""
import itertools

import numpy as np
import pytest

from app.services import gibbs
from app.services.calibration import get_curve
from app.services.model_tree import plan_model

_IDS = itertools.count(1)

# Ages are built at a constant 10 years per centimetre, so the true answer at
# any depth is known and a straight line in depth is the right answer at high
# rigidity.
YEARS_PER_CM = 10.0
SURFACE_AGE = 1000.0


def true_age(depth):
    return SURFACE_AGE + YEARS_PER_CM * depth


def core(depths, error=40.0, prefix='p'):
    """
    Measurements taken off IntCal20 at the calendar age each depth would have
    under constant accumulation -- so the answer is known independently of the
    model, as it is for the wiggle-match fixtures.
    """
    curve = get_curve('intcal20')
    measurements = []
    for index, depth in enumerate(depths):
        c14, _error = curve.interpolate(np.array([true_age(depth)]))
        measurements.append({
            'id': f'{prefix}{index}',
            'sample_name': f'{depth:g} cm',
            'age_bp': round(float(c14[0])),
            'error_1sigma': error,
            'material_type': 'peat',
            'sample_type': 'terrestrial',
            'curve_id': 'intcal20',
            'element_id': f'{prefix}seq',
            'depth_cm': float(depth),
        })
    return measurements


def deposition_tree(prefix='p', k=None, levels=(), log_k=(-2.0, 2.0)):
    """A model that IS one deposition sequence, with any undated levels."""
    return {
        'id': f'{prefix}seq', 'element_type': 'deposition_sequence',
        'name': 'Core', 'children': [
            {'id': f'{prefix}lv{index}', 'element_type': 'interpolated_depth',
             'name': f'{depth:g} cm', 'params': {'depth_cm': float(depth)},
             'children': []}
            for index, depth in enumerate(levels)
        ],
        'params': {
            'k': k, 'k_variable': k is None,
            'log_k_min': log_k[0], 'log_k_max': log_k[1],
            'depth_units': 'cm',
        },
    }


def brute_force(measurements, root, grid, alpha=None):
    """
    The exact posterior for THREE levels, by summing the joint over the whole
    grid.

    The deposition density is written out here from Bronk Ramsey 2008 rather
    than borrowed from the sampler, so this is a check of the sampler against
    the model and not of the sampler against itself. `alpha=None` drops the
    deposition term entirely, leaving nothing but the ordering -- which is
    what the low-rigidity limit is compared against.

    Returns [(id, mean, sd)] oldest first.
    """
    model = gibbs.Model(measurements, root, grid=grid)
    ids = model.depositions[0].ids
    assert len(ids) == 3, 'the brute force is written for three levels'
    likelihood = [model.likelihood[pid] for pid in ids]

    older, middle, younger = grid[:, None, None], grid[None, :, None], grid[None, None, :]
    first, second = older - middle, middle - younger
    ordered = (first > 0) & (second > 0)
    with np.errstate(divide='ignore', invalid='ignore'):
        if alpha is None:
            log_joint = np.where(ordered, 0.0, -np.inf)
        else:
            log_joint = np.where(ordered, (
                (alpha[0] - 1) * np.log(np.where(ordered, first, 1.0))
                + (alpha[1] - 1) * np.log(np.where(ordered, second, 1.0))
                - (alpha.sum() - 1) * np.log(np.where(ordered, first + second, 1.0))
            ), -np.inf)
        log_joint = (log_joint
                     + np.log(np.maximum(likelihood[0], 1e-300))[:, None, None]
                     + np.log(np.maximum(likelihood[1], 1e-300))[None, :, None]
                     + np.log(np.maximum(likelihood[2], 1e-300))[None, None, :])

    finite = np.isfinite(log_joint)
    weights = np.where(finite, np.exp(log_joint - log_joint[finite].max()), 0.0)
    out = []
    for index, axes in enumerate([(1, 2), (0, 2), (0, 1)]):
        marginal = weights.sum(axis=axes)
        marginal = marginal / marginal.sum()
        mean = float((grid * marginal).sum())
        sd = float(np.sqrt(((grid - mean) ** 2 * marginal).sum()))
        out.append((ids[index], mean, sd))
    return out


class TestAgainstBruteForce:
    """
    Three levels, a fixed rigidity, and the whole joint summed on a grid. The
    strongest check available: no sampler, no message passing, and no
    agreement with another implementation standing in for the truth.
    """

    DEPTHS = (60, 40, 20)
    K = 0.5

    def test_the_sampler_reproduces_the_exact_marginals(self):
        measurements = core(self.DEPTHS, prefix=f'bf{next(_IDS)}')
        root = deposition_tree(measurements[0]['element_id'][:-3], k=self.K)
        root['id'] = measurements[0]['element_id']
        grid = np.arange(1000.0, 2100.0 + 5.0, 5.0)

        alpha = np.array([self.K * 20.0, self.K * 20.0])
        exact = brute_force(measurements, root, grid, alpha)
        sampled = gibbs.solve(measurements, root, grid=grid, sweeps=8000,
                              num_samples=500, max_sweeps=60000, seed=7, temperatures=1)

        for parameter_id, mean, sd in exact:
            got = sampled['measurements'][parameter_id]
            assert got['posterior_mean'] == pytest.approx(mean, abs=4.0)
            assert got['posterior_std'] == pytest.approx(sd, abs=4.0)


class TestTheRigidityLimits:
    """
    Both limits, cheap and needing no OxCal: what the model does as k goes to
    each end of its range is the clearest statement of what k means.
    """

    def test_a_very_low_rigidity_says_little_beyond_the_ordering(self):
        """
        With k*dz far below one the deposition term is nearly scale-free and
        the depths stop informing the ages: what is left is close to the
        constraint that a deeper level is older, and nothing else. Compared
        against exactly that model, computed with the deposition term switched
        off.

        CLOSE, not identical, and the tolerance says which. At k*dz = 0.02 the
        three means move 11.4, 4.2 and 5.3 years and their standard deviations
        2.0, 4.0 and 0.9. A vanishing rigidity does not leave the plain
        Sequence's UNIFORM prior on the intervals -- that is the k*dz = 1 case
        -- it leaves a scale-free one. The assertion is what was measured
        rather than what the plan expected.
        """
        measurements = core((60, 40, 20), prefix=f'lo{next(_IDS)}')
        root = deposition_tree('lo', k=0.001)
        root['id'] = measurements[0]['element_id']
        grid = np.arange(1000.0, 2100.0 + 5.0, 5.0)

        ordering_only = brute_force(measurements, root, grid, None)
        nearly_flat = brute_force(measurements, root, grid,
                                  np.array([0.001 * 20.0, 0.001 * 20.0]))

        for (_id, a_mean, a_sd), (_other, b_mean, b_sd) in zip(
                ordering_only, nearly_flat):
            assert b_mean == pytest.approx(a_mean, abs=15.0)
            assert b_sd == pytest.approx(a_sd, abs=15.0)

    def test_a_high_rigidity_puts_the_ages_on_a_straight_line_in_depth(self):
        """
        Asserted on the residual scale rather than by eye: fit a line through
        the modelled ages against depth and require the scatter about it to be
        small compared with a single date's own uncertainty.
        """
        depths = (100, 80, 60, 40, 20)
        measurements = core(depths, prefix=f'hi{next(_IDS)}')
        root = deposition_tree('hi', k=20.0)
        root['id'] = measurements[0]['element_id']

        solved = gibbs.solve(measurements, root, sweeps=6000, num_samples=500,
                             max_sweeps=40000, seed=11, temperatures=1)
        ages = np.array([solved['measurements'][m['id']]['posterior_median']
                         for m in measurements])
        z = np.array([m['depth_cm'] for m in measurements])
        slope, intercept = np.polyfit(z, ages, 1)
        residual = float(np.std(ages - (slope * z + intercept)))

        assert residual < 10.0, f'ages are not on a line in depth: rms {residual:.1f}'
        # ...and the line it settles on is the accumulation rate the core was
        # built at, rather than any straight line at all.
        assert slope == pytest.approx(YEARS_PER_CM, abs=1.5)


class TestUndatedLevels:
    """
    An `interpolated_depth` is a level with no measurement -- the thing that
    makes "how old is the level at 47 cm" answerable. Its likelihood table is
    flat, which is precisely the right answer, so the sampler needs no branch
    for it.
    """

    def test_an_undated_level_lands_where_the_accumulation_rate_puts_it(self):
        depths = (100, 80, 60, 40, 20)
        measurements = core(depths, prefix=f'un{next(_IDS)}')
        root = deposition_tree('un', levels=(47.0,))
        root['id'] = measurements[0]['element_id']

        solved = gibbs.solve(measurements, root, sweeps=6000, num_samples=500,
                             max_sweeps=40000, seed=3, temperatures=1)
        level = solved['boundaries'][root['children'][0]['id']]
        assert level['posterior']['posterior_median'] == pytest.approx(
            true_age(47.0), abs=30.0)
        assert level['name'] == '47 cm'

    def test_a_level_beside_a_dated_one_reproduces_that_date(self):
        """
        A centimetre from a date, an undated level has almost the same
        neighbours and almost the same deposition terms, so it must come back
        at almost the same age.

        A centimetre rather than nought: a level at EXACTLY a dated depth is a
        zero interval, which has no deposition rate and is refused (see
        below). That is the honest reading -- two things at one depth are one
        event, and this model has no way to say two parameters are the same
        parameter.
        """
        depths = (100, 80, 60, 40, 20)
        measurements = core(depths, prefix=f'co{next(_IDS)}')
        root = deposition_tree('co', levels=(59.0,))
        root['id'] = measurements[0]['element_id']

        solved = gibbs.solve(measurements, root, sweeps=8000, num_samples=500,
                             max_sweeps=60000, seed=5, temperatures=1)
        dated = next(m for m in measurements if m['depth_cm'] == 60.0)
        modelled = solved['measurements'][dated['id']]['posterior_median']
        interpolated = solved['boundaries'][
            root['children'][0]['id']]['posterior']['posterior_median']
        assert interpolated == pytest.approx(modelled, abs=30.0)

    def test_a_level_at_exactly_a_dated_depth_is_refused(self):
        from app.services.model_tree import validate_ordinates

        measurements = core((60, 40, 20), prefix=f'cx{next(_IDS)}')
        root = deposition_tree('cx', levels=(60.0,))
        root['id'] = measurements[0]['element_id']

        with pytest.raises(ValueError) as raised:
            validate_ordinates(root, measurements)
        assert 'same depth' in str(raised.value)


class TestTheSpanPriorIsNotAppliedTwice:
    """
    The thing most likely to be QUIETLY wrong.

    A deposition sequence supplies its own prior on the intervals between its
    members. If those members were also treated as a uniform scatter between
    the enclosing Boundaries, two priors would apply to the same intervals --
    which would not crash, would not look wrong, and would give phase widths
    out by a factor nobody could eyeball. So it is asserted structurally,
    where the separation is actually made, as well as in the answer.
    """

    def _bounded(self, prefix):
        measurements = core((60, 40, 20), prefix=prefix)
        inner = deposition_tree(prefix, k=0.5)
        inner['id'] = measurements[0]['element_id']
        root = {
            'id': f'{prefix}root', 'element_type': 'sequence', 'name': 'Site',
            'children': [
                {'id': f'{prefix}b0', 'element_type': 'boundary',
                 'name': 'Start', 'children': []},
                inner,
                {'id': f'{prefix}b1', 'element_type': 'boundary',
                 'name': 'End', 'children': []},
            ],
        }
        return measurements, root, inner

    def test_the_levels_are_contained_by_the_boundaries_but_not_scattered(self):
        measurements, root, _inner = self._bounded(f'sp{next(_IDS)}')
        plan = plan_model(root, measurements)

        assert len(plan.span_groups) == 1
        group = plan.span_groups[0]
        # Nothing in the uniform-scatter term...
        assert group.date_ids == []
        # ...and every level penned in by the same pair of boundaries.
        assert set(group.contained_ids) == {m['id'] for m in measurements}

    def test_the_sampler_charges_no_span_term_for_them(self):
        measurements, root, _inner = self._bounded(f'sp{next(_IDS)}')
        model = gibbs.Model(measurements, root)

        for _older, _younger, dates in model.group_dates:
            assert dates == [], (
                'a deposition sequence contributed dates to a span term, so '
                'the uniform-scatter prior is being applied on top of the '
                'deposition prior')
        # Contained all the same: each level must still sit between them.
        for measurement in measurements:
            assert model.below[measurement['id']]
            assert model.above[measurement['id']]

    @pytest.mark.slow
    def test_enclosing_boundaries_barely_move_the_levels(self):
        """
        The boundaries add containment and nothing else, and containment is
        slack here, so the ages must come back where they were. A second
        interval prior would show up as a systematic shift.
        """
        prefix = f'sp{next(_IDS)}'
        measurements, bounded_root, inner = self._bounded(prefix)
        bare = gibbs.solve(measurements, inner, sweeps=6000, num_samples=500,
                           max_sweeps=40000, seed=13, temperatures=1)
        bounded = gibbs.solve(measurements, bounded_root, sweeps=6000,
                              num_samples=500, max_sweeps=40000, seed=13, temperatures=1)

        for measurement in measurements:
            assert (bounded['measurements'][measurement['id']]['posterior_median']
                    == pytest.approx(
                        bare['measurements'][measurement['id']]['posterior_median'],
                        abs=25.0))


class TestTheRigidityItself:
    """
    k is a parameter, not a setting: a fixed one chosen badly dominates the
    answer, so the run infers it and REPORTS what it inferred. A high-k model
    interpolates confidently straight through a plateau, so the rigidity is
    part of reading the result rather than an implementation detail.
    """

    def test_the_inferred_rigidity_is_reported(self):
        measurements = core((100, 80, 60, 40, 20), prefix=f'kk{next(_IDS)}')
        root = deposition_tree('kk')
        root['id'] = measurements[0]['element_id']

        solved = gibbs.solve(measurements, root, sweeps=6000, num_samples=500,
                             max_sweeps=40000, seed=17, temperatures=1)
        report = solved['deposition'][0]
        assert report['variable'] is True
        assert report['k_mean'] > 0
        assert report['log10_k_hpd_68'][0] < report['log10_k_hpd_68'][1]
        assert report['pinned'] is False

    def test_a_rigidity_pinned_against_its_prior_bound_is_flagged(self):
        """
        When the posterior piles up at the end of the range it was allowed,
        the PRIOR is doing the work rather than the data, and the reader has
        to be told that rather than shown a confident number.
        """
        measurements = core((100, 80, 60, 40, 20), prefix=f'kp{next(_IDS)}')
        # A core accumulating at ten years a centimetre wants a rigidity
        # around 10^0.8; allowed nothing above 10^0, the posterior can only
        # pile up against that bound.
        root = deposition_tree('kp', log_k=(-4.0, 0.0))
        root['id'] = measurements[0]['element_id']

        solved = gibbs.solve(measurements, root, sweeps=6000, num_samples=500,
                             max_sweeps=40000, seed=19, temperatures=1)
        assert solved['deposition'][0]['pinned'] is True

    def test_a_fixed_rigidity_is_reported_as_fixed(self):
        measurements = core((60, 40, 20), prefix=f'kf{next(_IDS)}')
        root = deposition_tree('kf', k=0.5)
        root['id'] = measurements[0]['element_id']

        solved = gibbs.solve(measurements, root, sweeps=4000, num_samples=200,
                             max_sweeps=20000, seed=23, temperatures=1)
        report = solved['deposition'][0]
        assert report['variable'] is False
        assert report['k_mean'] == pytest.approx(0.5)


class TestTheGrid:
    def test_halving_the_step_does_not_move_the_answer(self):
        """
        The deposition density is evaluated at grid points with coincident
        levels forbidden, which is a discretisation of the continuum model.
        Whether that discretisation is fine enough is not something to argue
        about -- refine it and see.
        """
        measurements = core((60, 40, 20), prefix=f'gr{next(_IDS)}')
        root = deposition_tree('gr', k=0.5)
        root['id'] = measurements[0]['element_id']

        coarse = np.arange(1000.0, 2100.0 + 8.0, 8.0)
        fine = np.arange(1000.0, 2100.0 + 4.0, 4.0)
        at_coarse = brute_force(measurements, root, coarse,
                                np.array([10.0, 10.0]))
        at_fine = brute_force(measurements, root, fine, np.array([10.0, 10.0]))

        for (_id, a_mean, a_sd), (_other, b_mean, b_sd) in zip(at_coarse, at_fine):
            assert b_mean == pytest.approx(a_mean, abs=3.0)
            assert b_sd == pytest.approx(a_sd, abs=3.0)


# --------------------------------------------------------------------------
# Through the API
# --------------------------------------------------------------------------

class TestThroughTheApi:
    def test_a_core_runs_and_reports_its_levels_and_its_rigidity(self, client):
        tag = f'dep{next(_IDS)}'
        measurements = core((100, 80, 60, 40, 20), prefix=tag)
        root = deposition_tree(tag, levels=(47.0,))
        root['id'] = measurements[0]['element_id']

        response = client.post('/api/analysis/bayesian?wait=true', json={
            'name': f'Core {tag}', 'measurements': measurements,
            'model_tree': root, 'save': True,
            'bayesian_config': {'mcmc_samples': 500, 'mcmc_temperatures': 1},
        })
        assert response.status_code == 200, response.text

        data = client.get(
            f"/api/analysis/{response.json()['data']['id']}").json()['data']
        assert data['solver']['method'] == 'gibbs'
        # The exact solver said WHY it could not take this, and the record
        # keeps it: the ages are coupled by the deposition rate rather than
        # only through their Boundaries.
        assert 'deposition sequence' in data['solver']['reason']
        assert data['solver']['deposition'][0]['levels'] == 6

        undated = next(c for c in data['model_tree']['children'])
        assert undated['element_type'] == 'interpolated_depth'
        assert undated['posterior'] is not None
        assert undated['posterior']['posterior_median'] == pytest.approx(
            true_age(47.0), abs=40.0)

    def test_requiring_the_exact_solver_fails_naming_the_reason(self, client):
        tag = f'dep{next(_IDS)}'
        measurements = core((60, 40, 20), prefix=tag)
        root = deposition_tree(tag, k=0.5)
        root['id'] = measurements[0]['element_id']

        response = client.post('/api/analysis/bayesian?wait=true', json={
            'name': f'Exact {tag}', 'measurements': measurements,
            'model_tree': root, 'save': True,
            'bayesian_config': {'solver': 'exact'},
        })
        assert response.status_code >= 400, response.text
        assert 'deposition sequence' in response.text

    def test_a_level_with_no_depth_is_refused_at_the_edge(self, client):
        tag = f'dep{next(_IDS)}'
        measurements = core((60, 40, 20), prefix=tag)
        root = deposition_tree(tag)
        root['id'] = measurements[0]['element_id']
        measurements[1].pop('depth_cm')

        response = client.post('/api/analysis/bayesian?wait=true', json={
            'name': f'Depthless {tag}', 'measurements': measurements,
            'model_tree': root, 'save': True,
        })
        assert response.status_code == 422, response.text
        assert 'no depth' in response.text

    def test_two_levels_at_one_depth_are_refused(self, client):
        tag = f'dep{next(_IDS)}'
        measurements = core((60, 60, 20), prefix=tag)
        root = deposition_tree(tag)
        root['id'] = measurements[0]['element_id']

        response = client.post('/api/analysis/bayesian?wait=true', json={
            'name': f'Doubled {tag}', 'measurements': measurements,
            'model_tree': root, 'save': True,
        })
        assert response.status_code == 422, response.text
        assert 'same depth' in response.text


class TestTheAgeDepthBand:
    """
    The deliverable of an age-depth model is not a stack of per-date
    posteriors: it is age against depth with a band round it, and how fast the
    deposit accumulated between one level and the next.

    Every one of those is a function of SEVERAL ages at once, so they come off
    JOINT draws rather than from the marginals -- the identical argument
    services/intervals.py opens with for durations, and the identical trap:
    pairing marginals gives an answer wrong in shape rather than scale, which
    no eyeball check catches.
    """

    def _solved(self, seed=29):
        depths = (100, 80, 60, 40, 20)
        measurements = core(depths, prefix=f'ad{next(_IDS)}')
        root = deposition_tree('ad', levels=(47.0,))
        root['id'] = measurements[0]['element_id']
        return measurements, root, gibbs.solve(
            measurements, root, sweeps=6000, num_samples=500,
            max_sweeps=40000, seed=seed, temperatures=1)

    def test_the_band_is_produced_without_being_asked_for(self):
        """
        Phase durations are opt-in, because a duration asserts the dates are a
        fair sample of a bounded episode. An age-depth band is not an extra:
        it is what the model was built to produce, so a run that had to be
        asked twice for it would be answering a different question.
        """
        _measurements, _root, solved = self._solved()
        assert solved['intervals'] is not None
        assert solved['intervals']['durations'] == []
        assert len(solved['intervals']['age_depth']) == 1

    def test_every_level_appears_at_its_own_depth_with_nested_bands(self):
        measurements, root, solved = self._solved()
        record = solved['intervals']['age_depth'][0]
        assert record['depth_units'] == 'cm'

        depths = [level['depth'] for level in record['levels']]
        assert depths == sorted(depths, reverse=True), 'levels are not oldest first'
        assert 47.0 in depths
        assert sum(1 for level in record['levels'] if not level['dated']) == 1

        for level in record['levels']:
            wide, narrow = level['band_95'], level['band_68']
            assert wide[0] <= narrow[0] <= level['median'] <= narrow[1] <= wide[1], (
                'the 68.3% band is not inside the 95.4% one; an envelope drawn '
                'from these would cross itself')

    def test_the_accumulation_rate_is_the_rate_the_core_was_built_at(self):
        _measurements, _root, solved = self._solved()
        steps = solved['intervals']['age_depth'][0]['accumulation']
        assert len(steps) == 5          # six levels, five intervals

        for step in steps:
            assert step['depth_gap'] > 0
            assert step['rate_median'] == pytest.approx(YEARS_PER_CM, abs=6.0)
            low, high = step['rate_band_68']
            assert low <= step['rate_median'] <= high

    def test_the_rates_come_off_joint_draws_and_so_are_never_negative(self):
        """
        Deeper is older, so an elapsed time between adjacent levels cannot be
        negative -- in the JOINT posterior. Pairing two marginals would give
        negative rates wherever the two levels' marginals overlap, which on a
        tightly-dated core is most of them, and the mistake would show up as a
        band rather than as an error.
        """
        _measurements, _root, solved = self._solved()
        for step in solved['intervals']['age_depth'][0]['accumulation']:
            assert step['years']['hpd_95'][0] > 0

    def test_the_band_reaches_the_csv_export(self, client):
        tag = f'adx{next(_IDS)}'
        measurements = core((100, 80, 60, 40, 20), prefix=tag)
        root = deposition_tree(tag, levels=(47.0,))
        root['id'] = measurements[0]['element_id']

        created = client.post('/api/analysis/bayesian?wait=true', json={
            'name': f'Core {tag}', 'measurements': measurements,
            'model_tree': root, 'save': True,
            'bayesian_config': {'mcmc_samples': 500, 'mcmc_temperatures': 1},
        })
        assert created.status_code == 200, created.text
        analysis_id = created.json()['data']['id']

        csv_text = client.get(f'/api/results/{analysis_id}/export/csv').text
        assert 'Age-Depth Level' in csv_text
        assert 'Accumulation Rate' in csv_text
        assert 'Rate (yr per unit depth)' in csv_text

        # ...and through the same assembly, so the three downloads cannot
        # disagree about the same run.
        block = client.get(
            f'/api/results/{analysis_id}/export/json').json()['data']['intervals']
        assert len(block['age_depth'][0]['accumulation']) == 5

    def test_the_band_reaches_the_pdf_report(self, client):
        tag = f'adp{next(_IDS)}'
        measurements = core((100, 80, 60, 40, 20), prefix=tag)
        root = deposition_tree(tag, levels=(47.0,))
        root['id'] = measurements[0]['element_id']

        created = client.post('/api/analysis/bayesian?wait=true', json={
            'name': f'Core {tag}', 'measurements': measurements,
            'model_tree': root, 'save': True,
            'bayesian_config': {'mcmc_samples': 500, 'mcmc_temperatures': 1},
        })
        assert created.status_code == 200, created.text

        report = client.get(
            f"/api/results/{created.json()['data']['id']}/pdf")
        assert report.status_code == 200, report.text
        # A PDF is not readable as text, so what is asserted is that it
        # rendered at all with the section in it -- the section's CONTENT is
        # held by the CSV and JSON tests, which come off the same assembly.
        assert report.content[:4] == b'%PDF'
        assert len(report.content) > 1000

    def test_a_plain_model_grows_no_empty_accumulation_section(self, client):
        """
        README's rule: a column appears only when the run has something to put
        in it. Applied to a whole table -- a calibration must not export an
        age-depth heading with nothing under it.
        """
        response = client.post('/api/analysis/calibrate', json={
            'sample_name': f'Plain {next(_IDS)}', 'age_bp': 3000,
            'error_1sigma': 30, 'material_type': 'charcoal',
            'curve_id': 'intcal20', 'save': True,
        })
        analysis_id = response.json()['data']['id']
        csv_text = client.get(f'/api/results/{analysis_id}/export/csv').text
        assert 'Age-Depth Level' not in csv_text
        assert 'Accumulation Rate' not in csv_text
