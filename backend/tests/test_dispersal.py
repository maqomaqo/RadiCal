"""
Wave of advance: the site derivation and the per-draw fit.

The fit is tested against fronts whose truth is known by construction, because
the failure this feature is most exposed to is a tidy answer to a question the
data cannot settle. A test that only checked "it returns a number" would pass
on exactly the code that ships that failure.
"""
import math

import numpy as np
import pytest

from app.services import dispersal
from app.services.reservoir import haversine_km


# --------------------------------------------------------------------------
# Building models to derive sites from
# --------------------------------------------------------------------------

def _boundary(bid):
    return {'id': bid, 'element_type': 'boundary', 'name': bid}


def _phase(pid, name=None):
    return {'id': pid, 'element_type': 'phase', 'name': name or pid,
            'children': []}


def _sequence(sid, children, name=None):
    return {'id': sid, 'element_type': 'sequence', 'name': name or sid,
            'children': children}


def _date(did, element_id, lat=None, lon=None, name=None):
    return {'id': did, 'element_id': element_id, 'sample_name': name or did,
            'latitude': lat, 'longitude': lon}


def _one_phase_per_site(places):
    """Sequence[B0, P1, B1, P2, B2, ...] -- the ordinary regional model."""
    children, measurements = [], []
    for index, (name, lat, lon) in enumerate(places):
        children.append(_boundary(f'b{index}'))
        children.append(_phase(f'p{index}', name))
        measurements.append(_date(f'd{index}', f'p{index}', lat, lon))
    children.append(_boundary(f'b{len(places)}'))
    return _sequence('root', children), measurements


class TestDerivingSites:

    def test_one_site_per_phase_with_its_opening_boundary(self):
        root, measurements = _one_phase_per_site([
            ('Aarhus', 56.15, 10.20),
            ('Bergen', 60.39, 5.32),
        ])
        sites = dispersal.derive_sites(root, measurements)

        assert [s.name for s in sites] == ['Aarhus', 'Bergen']
        # The OLDER boundary of the bracketing pair, which is the one that
        # opens the phase. Getting this wrong would date every site by when
        # the thing LEFT rather than when it arrived.
        assert [s.parameter_id for s in sites] == ['b0', 'b1']
        assert all(s.basis == 'boundary' for s in sites)

    def test_a_sequence_of_phases_at_one_place_is_one_site(self):
        """
        Not three. A site excavated in three stratified phases is still one
        place and one arrival, and counting it three times would triple its
        weight in the regression while adding no information.
        """
        inner = _sequence('siteA', [
            _boundary('a0'), _phase('a1'), _boundary('a1b'),
            _phase('a2'), _boundary('a2b'),
        ], name='Skara Brae')
        root = _sequence('root', [_boundary('r0'), inner, _boundary('r1')])
        measurements = [
            _date('d1', 'a1', 59.05, -3.34),
            _date('d2', 'a2', 59.05, -3.34),
        ]

        sites = dispersal.derive_sites(root, measurements)
        assert len(sites) == 1
        assert sites[0].name == 'Skara Brae'
        # The sequence's FIRST boundary child, not the inner phase's.
        assert sites[0].parameter_id == 'a0'

    def test_the_model_root_is_not_mistaken_for_the_site(self):
        """
        A root Sequence wrapping one site Sequence is coherent at the root,
        but its opening Boundary is the start of the MODEL -- older than the
        site's own, and a different parameter. Dating the site by it would
        report an arrival earlier than the model says it was.
        """
        inner = _sequence('siteA', [
            _boundary('a0'), _phase('a1'), _boundary('a2')], name='Aarhus')
        root = _sequence('root', [_boundary('r0'), inner, _boundary('r1')])
        measurements = [_date('d1', 'a1', 56.15, 10.20)]

        site = dispersal.derive_sites(root, measurements)[0]
        assert site.name == 'Aarhus'
        assert site.parameter_id == 'a0'

    def test_a_named_phase_inside_a_named_sequence_does_not_lose_its_name(self):
        """
        The counterpart to the test above, and the reason the descent needs
        two conditions rather than one. A Sequence holding a single Phase
        opens on the SAME Boundary as that phase, so they are the same
        arrival -- and the element the analyst named is the one to report.
        """
        inner = _sequence('siteA', [
            _boundary('a0'), _phase('a1'), _boundary('a2')], name='Aarhus')
        root = _sequence('root', [
            _boundary('r0'), inner, _boundary('rm'),
            _sequence('siteB', [_boundary('b0'), _phase('b1'), _boundary('b2')],
                      name='Bergen'),
            _boundary('r1')])
        measurements = [_date('d1', 'a1', 56.15, 10.20),
                        _date('d2', 'b1', 60.39, 5.32)]

        sites = dispersal.derive_sites(root, measurements)
        assert [s.name for s in sites] == ['Aarhus', 'Bergen']

    def test_a_region_of_site_sequences_splits_into_its_sites(self):
        """
        The top-level candidate covers the whole region, fails the scatter
        test, and the walk descends. This is the same threshold doing a
        different job from the refusal below -- here it SEPARATES places.
        """
        first = _sequence('siteA', [
            _boundary('a0'), _phase('a1'), _boundary('a2')], name='Aarhus')
        second = _sequence('siteB', [
            _boundary('b0'), _phase('b1'), _boundary('b2')], name='Bergen')
        root = _sequence('root', [
            _boundary('r0'), first, _boundary('rm'), second, _boundary('r1')])
        measurements = [
            _date('d1', 'a1', 56.15, 10.20),
            _date('d2', 'b1', 60.39, 5.32),
        ]

        sites = dispersal.derive_sites(root, measurements)
        assert [s.name for s in sites] == ['Aarhus', 'Bergen']
        assert [s.parameter_id for s in sites] == ['a0', 'b0']

    def test_one_phase_holding_two_sites_is_refused(self):
        """
        §11.6, and the case the threshold exists for. There is nothing left to
        descend into, so the scatter is not a structure to split -- it is a
        modelling error, and the app says so instead of averaging.
        """
        root = _sequence('root', [
            _boundary('b0'), _phase('p0', 'Mixed'), _boundary('b1')])
        measurements = [
            _date('d1', 'p0', 56.1500, 10.2000),
            _date('d2', 'p0', 56.4000, 10.2000),
        ]

        sites = dispersal.derive_sites(root, measurements)
        assert len(sites) == 1
        assert not sites[0].usable
        assert sites[0].latitude is None

    def test_the_refusal_names_both_coordinates_and_the_distance(self):
        """A refusal that said only "too scattered" would leave the user
        hunting through the collection for which two dates it meant."""
        root = _sequence('root', [
            _boundary('b0'), _phase('p0', 'Mixed'), _boundary('b1')])
        measurements = [
            _date('d1', 'p0', 56.1500, 10.2000),
            _date('d2', 'p0', 56.4000, 10.2000),
        ]

        refusal = dispersal.derive_sites(root, measurements)[0].refusal
        assert '56.1500' in refusal and '56.4000' in refusal
        assert '10.2000' in refusal
        # The distance itself, to one decimal -- roughly 27.8 km here.
        assert '27.' in refusal

    def test_dates_just_inside_the_threshold_are_one_site(self):
        root = _sequence('root', [
            _boundary('b0'), _phase('p0', 'Close enough'), _boundary('b1')])
        # ~4.4 km apart, inside SITE_SCATTER_KM.
        measurements = [
            _date('d1', 'p0', 56.1500, 10.2000),
            _date('d2', 'p0', 56.1900, 10.2000),
        ]

        site = dispersal.derive_sites(root, measurements)[0]
        assert site.usable
        assert site.scatter_km < dispersal.SITE_SCATTER_KM
        # Centroid, so between the two.
        assert 56.15 < site.latitude < 56.19

    def test_a_site_with_no_coordinates_is_not_a_refusal(self):
        """A model that never had coordinates is not an error. It is a model
        this feature has nothing to say about, and it must not manufacture a
        complaint about one."""
        root, measurements = _one_phase_per_site([('Aarhus', 56.15, 10.20)])
        measurements.append(_date('d9', 'p0'))
        root['children'].insert(2, _phase('p9', 'Unplaced'))

        sites = dispersal.derive_sites(root, measurements)
        assert all(s.refusal is None for s in sites)
        assert all(s.name != 'Unplaced' for s in sites)

    def test_a_centroid_survives_the_antimeridian(self):
        """
        179 and -179 are two degrees apart, and their arithmetic mean is the
        Gulf of Guinea. Sites inside the scatter threshold can still straddle
        the line, so the mean is taken on the sphere.
        """
        root = _sequence('root', [
            _boundary('b0'), _phase('p0', 'Taputapuatea'), _boundary('b1')])
        measurements = [
            _date('d1', 'p0', -16.8, 179.99),
            _date('d2', 'p0', -16.8, -179.99),
        ]

        site = dispersal.derive_sites(root, measurements)[0]
        assert site.usable
        assert abs(site.longitude) > 179.0
        assert abs(site.latitude + 16.8) < 0.01

    def test_no_tree_derives_nothing(self):
        assert dispersal.derive_sites(None, []) == []


# --------------------------------------------------------------------------
# The fit
# --------------------------------------------------------------------------

def _front(n_sites=8, speed=1.0, t0=7500.0, draws=400, scatter=0.0, seed=7):
    """
    A synthetic front due east of an origin at a known speed.

    Sites are laid out along a parallel so their distances are exact and
    spread evenly; each gets `draws` joint draws centred on the age the front
    implies. With `scatter` at zero the truth is recoverable to the arithmetic.
    """
    rng = np.random.default_rng(seed)
    origin = (0.0, 0.0)
    sites, draws_by_id = [], {}
    for i in range(n_sites):
        lon = 1.5 * (i + 1)
        distance = haversine_km(0.0, 0.0, 0.0, lon)
        age = t0 - distance / speed
        sites.append(dispersal.SiteSpec(
            element_id=f'e{i}', name=f'Site {i}', parameter_id=f'b{i}',
            basis='boundary', latitude=0.0, longitude=lon))
        draws_by_id[f'b{i}'] = age + rng.normal(0.0, scatter, draws)
    return sites, draws_by_id, origin


class TestFittingAWave:

    def test_a_synthetic_front_recovers_its_speed_and_origin_age(self):
        sites, draws, origin = _front(speed=1.0, t0=7500.0)
        fit = dispersal.fit_wave(sites, draws, origin)

        assert fit['refused'] is None
        assert fit['speed']['median'] == pytest.approx(1.0, rel=1e-6)
        assert fit['t0']['median'] == pytest.approx(7500.0, rel=1e-6)

    def test_it_recovers_a_speed_that_is_not_one(self):
        """1.0 km/yr is the one value where v and 1/v are the same number, so
        a sign or reciprocal error would pass the test above."""
        sites, draws, origin = _front(speed=0.25, t0=6000.0)
        fit = dispersal.fit_wave(sites, draws, origin)
        assert fit['speed']['median'] == pytest.approx(0.25, rel=1e-6)

    def test_a_front_running_the_other_way_reports_a_NEGATIVE_speed(self):
        """
        No absolute value, no sign flipped to make the answer look like a
        dispersal. A front that gets OLDER with distance from the assumed
        origin is a finding about the origin, and hiding it would turn this
        feature into a machine for confirming whatever was assumed.
        """
        sites, draws, origin = _front(speed=-1.0, t0=7500.0)
        fit = dispersal.fit_wave(sites, draws, origin)
        assert fit['speed']['median'] < 0

    def test_chronological_uncertainty_lands_inside_the_speed(self):
        """
        The whole argument for computing this during the run. A front whose
        arrival ages are uncertain has an uncertain speed, and the interval
        has to widen with the dates rather than being a standard error that
        pretends calibration was exact.
        """
        tight = dispersal.fit_wave(*_front(scatter=10.0, seed=3)[:2],
                                   origin=(0.0, 0.0))
        loose = dispersal.fit_wave(*_front(scatter=150.0, seed=3)[:2],
                                   origin=(0.0, 0.0))

        def width(fit):
            low, high = fit['speed']['hpd_95']
            return high - low

        assert width(loose) > width(tight)

    def test_each_draw_is_fitted_whole(self):
        """
        A per-draw fit reads every site's age off the SAME draw. Shuffling one
        site's draws independently destroys the joint correlation and must
        change the answer -- if it does not, the fit is secretly working off
        marginals, which is the failure §3.2 exists to prevent.
        """
        sites, draws, origin = _front(scatter=120.0, seed=11)
        straight = dispersal.fit_wave(sites, draws, origin)

        shuffled = {k: v.copy() for k, v in draws.items()}
        rng = np.random.default_rng(0)
        for key in shuffled:
            rng.shuffle(shuffled[key])
        scrambled = dispersal.fit_wave(sites, shuffled, origin)

        assert (straight['speed']['hpd_95']
                != pytest.approx(scrambled['speed']['hpd_95'], rel=1e-9))

    def test_five_sites_are_refused(self):
        sites, draws, origin = _front(n_sites=5)
        fit = dispersal.fit_wave(sites, draws, origin)

        assert fit['speed'] is None
        assert fit['refused'] is not None
        assert str(dispersal.MIN_DISPERSAL_SITES) in fit['refused']
        assert fit['n_sites'] == 5

    def test_six_sites_are_fitted(self):
        """The boundary is a refusal BELOW the minimum, not at it."""
        sites, draws, origin = _front(n_sites=6)
        assert dispersal.fit_wave(sites, draws, origin)['refused'] is None

    def test_a_two_site_phase_refuses_the_whole_fit(self):
        """
        A site the app could not place is not quietly dropped to get the count
        up. The fit that remains would be a fit of a different model from the
        one the analyst wrote down.
        """
        sites, draws, origin = _front(n_sites=8)
        sites.append(dispersal.SiteSpec(
            element_id='bad', name='Mixed', parameter_id='bbad',
            basis='boundary', refusal='two places in one phase'))

        fit = dispersal.fit_wave(sites, draws, origin)
        assert fit['refused'] == 'two places in one phase'
        assert fit['speed'] is None

    def test_sites_on_one_circle_are_refused(self):
        """
        Equidistant sites constrain the intercept and say nothing about the
        gradient. OLS would divide by a zero spread; the app declines instead
        and says what is wrong with the ORIGIN, which is the thing the user
        can move.
        """
        sites, draws, origin = _front(n_sites=8)
        for site in sites:
            site.latitude, site.longitude = 0.0, 3.0

        fit = dispersal.fit_wave(sites, draws, origin)
        assert fit['refused'] is not None
        assert 'origin' in fit['refused']

    def test_a_site_with_no_draws_is_not_counted(self):
        """A parameter the solver did not report cannot contribute an arrival
        age, and counting it towards the minimum would let a six-site refusal
        pass as a seven-site fit."""
        sites, draws, origin = _front(n_sites=6)
        draws.pop('b5')
        assert dispersal.fit_wave(sites, draws, origin)['refused'] is not None

    def test_residuals_are_signed_and_sorted(self):
        sites, draws, origin = _front(n_sites=8, scatter=0.0)
        # Push one site 300 years older than the front predicts.
        draws['b3'] = draws['b3'] + 300.0

        fit = dispersal.fit_wave(sites, draws, origin)
        residuals = fit['residuals']

        assert [r['name'] for r in residuals][0] == 'Site 3'
        assert residuals[0]['residual_years'] > 0
        # Sorted most-positive first, so the table opens on the site that
        # least fits the front. That IS the finding, per §6.6.
        years = [r['residual_years'] for r in residuals]
        assert years == sorted(years, reverse=True)
        assert sum(years) == pytest.approx(0.0, abs=1e-6)

    def test_a_speed_is_quoted_at_a_resolution_that_means_something(self):
        """
        intervals' own step floors at five YEARS. Applied to km/yr it would
        put a whole front posterior in one bin and return an HPD of nonsense,
        so a speed carries its own resolution.
        """
        sites, draws, origin = _front(speed=1.0, scatter=40.0)
        fit = dispersal.fit_wave(sites, draws, origin)

        low, high = fit['speed']['hpd_95']
        assert fit['speed']['step'] < (high - low)
        assert low < 1.0 < high

    def test_a_neolithic_scale_front_lands_near_one_km_per_year(self):
        """
        §10's landmark. The published LBK figure is about 1 km/yr, and a
        feature that could not reproduce it on data built to contain it would
        not be worth shipping.
        """
        sites, draws, origin = _front(n_sites=10, speed=1.0, t0=7500.0,
                                      scatter=60.0, seed=19)
        fit = dispersal.fit_wave(sites, draws, origin)

        low, high = fit['speed']['hpd_95']
        assert low < 1.0 < high
        assert fit['speed']['median'] == pytest.approx(1.0, abs=0.15)

    def test_the_origin_travels_with_the_answer(self):
        """A speed is meaningless without the origin it was measured from, so
        the two are never separated."""
        sites, draws, origin = _front()
        fit = dispersal.fit_wave(sites, draws, (12.0, -3.0))
        assert fit['origin'] == {'lat': 12.0, 'lon': -3.0}

    def test_a_different_origin_gives_a_different_speed(self):
        """The origin is an assumption, not a formality. §6.8's caveat is only
        honest if moving it actually moves the answer."""
        sites, draws, _origin = _front(n_sites=8)
        near = dispersal.fit_wave(sites, draws, (0.0, 0.0))
        far = dispersal.fit_wave(sites, draws, (40.0, -30.0))
        assert near['speed']['median'] != pytest.approx(far['speed']['median'])


class TestTheGroupCalibrationFallback:

    def test_it_takes_the_earliest_dated_event_and_says_so(self):
        measurements = [
            _date('d1', None, 56.15, 10.20, name='Young'),
            _date('d2', None, 56.15, 10.20, name='Old'),
        ]
        draws = {'d1': np.full(50, 5000.0), 'd2': np.full(50, 6000.0)}

        sites = dispersal.derive_sites_from_dates(measurements, draws)
        assert len(sites) == 1
        # Older is a LARGER cal BP.
        assert sites[0].name == 'Old'
        # The label that stops any panel showing this as an arrival time.
        assert sites[0].basis == 'earliest_dated_event'

    def test_dates_at_different_places_are_different_sites(self):
        measurements = [
            _date('d1', None, 56.15, 10.20),
            _date('d2', None, 60.39, 5.32),
        ]
        draws = {'d1': np.full(50, 5000.0), 'd2': np.full(50, 6000.0)}
        assert len(dispersal.derive_sites_from_dates(measurements, draws)) == 2

    def test_an_unplaced_date_is_skipped(self):
        measurements = [_date('d1', None), _date('d2', None, 56.15, 10.20)]
        draws = {'d1': np.full(50, 5000.0), 'd2': np.full(50, 6000.0)}
        sites = dispersal.derive_sites_from_dates(measurements, draws)
        assert [s.parameter_id for s in sites] == ['d2']
