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

    def test_a_phase_the_grouping_rule_can_build_is_never_refused(self):
        """
        Grouping joins a date to a site when it is within SITE_SCATTER_KM of
        the site's FIRST date, so two later members can sit almost twice
        that apart. Refusing such a phase would refuse what the app's own
        "Group dates into sites" builds -- which it did, at Anakena, 5.3 km.
        """
        root = _sequence('root', [
            _boundary('b0'), _phase('p0', 'Anakena'), _boundary('b1')])
        # ~4.4 km north and ~4.4 km south of the first date: each inside the
        # radius of it, and ~8.9 km from each other.
        measurements = [
            _date('d1', 'p0', 56.1500, 10.2000),
            _date('d2', 'p0', 56.1900, 10.2000),
            _date('d3', 'p0', 56.1100, 10.2000),
        ]

        site = dispersal.derive_sites(root, measurements)[0]
        assert site.usable, site.refusal
        assert dispersal.SITE_SCATTER_KM < site.scatter_km <= dispersal.SITE_SPREAD_LIMIT_KM

    def test_a_site_astride_the_antimeridian_is_one_site(self):
        """Longitudes 179.98 and -179.98 are a few kilometres apart, not the
        width of the world; the distance and the centroid both know it."""
        root = _sequence('root', [
            _boundary('b0'), _phase('p0', 'Dateline'), _boundary('b1')])
        measurements = [
            _date('d1', 'p0', -16.5000, 179.9800),
            _date('d2', 'p0', -16.5000, -179.9800),
        ]

        site = dispersal.derive_sites(root, measurements)[0]
        assert site.usable, site.refusal
        assert site.scatter_km < 5
        assert abs(abs(site.longitude) - 180) < 0.05

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


# --------------------------------------------------------------------------
# Model 2: the trend surface
# --------------------------------------------------------------------------

def _grid(bearing_deg, speed=1.0, t0=6500.0, draws=200, rows=3, cols=3,
          lat0=50.0, lon0=0.0, spacing_km=80.0, scatter=0.0, seed=5):
    """
    Sites on a grid, with arrival ages falling along `bearing_deg`.

    Positions are built by walking north and east in KILOMETRES from a corner,
    and the arrival age at each is set from its distance along the bearing --
    so the truth is a plane in ground distance, which is what the model claims
    to fit, rather than a plane in degrees, which nothing does.
    """
    rng = np.random.default_rng(seed)
    radians = math.radians(bearing_deg)
    # Unit vector of ADVANCE, in (east, north) kilometres.
    east_hat, north_hat = math.sin(radians), math.cos(radians)

    sites, draws_by_id = [], {}
    for i in range(rows):
        for j in range(cols):
            north_km, east_km = spacing_km * i, spacing_km * j
            lat = lat0 + north_km / 111.32
            lon = lon0 + east_km / (111.32 * math.cos(math.radians(lat)))
            along = east_km * east_hat + north_km * north_hat
            sid = f'g{i}{j}'
            sites.append(dispersal.SiteSpec(
                element_id=sid, name=sid, parameter_id=sid, basis='boundary',
                latitude=lat, longitude=lon))
            draws_by_id[sid] = (t0 - along / speed
                                + rng.normal(0.0, scatter, draws))
    return sites, draws_by_id


class TestTheTrendSurface:
    """
    §6.5. The model that assumes NO origin, which is what makes it the right
    first look at a dataset -- before a hypothesis has been imposed on it.
    """

    def test_it_takes_no_origin_at_all(self):
        """Not a default origin, not an optional one: a trend surface has no
        place to measure from, and offering one would invite the reader to
        think it did."""
        sites, draws = _grid(90.0)
        fit = dispersal.fit_trend(sites, draws)
        assert 'origin' not in fit

    def test_it_recovers_the_speed(self):
        sites, draws = _grid(90.0, speed=1.0)
        assert dispersal.fit_trend(sites, draws)['speed']['median'] == pytest.approx(
            1.0, rel=0.02)

    def test_it_recovers_a_speed_that_is_not_one(self):
        sites, draws = _grid(90.0, speed=0.4)
        assert dispersal.fit_trend(sites, draws)['speed']['median'] == pytest.approx(
            0.4, rel=0.02)

    @pytest.mark.parametrize('bearing', [0.0, 90.0, 180.0, 270.0, 45.0, 225.0])
    def test_it_recovers_the_bearing(self, bearing):
        """
        Compass bearing, clockwise from north. Every quadrant, because a sign
        error in either component passes on the axis it does not affect: a
        front tested only due east would not notice `-b` written as `b`.
        """
        sites, draws = _grid(bearing)
        azimuth = dispersal.fit_trend(sites, draws)['azimuth']
        offset = (azimuth['mean_deg'] - bearing + 180.0) % 360.0 - 180.0
        assert abs(offset) < 2.0, f'{azimuth["mean_deg"]} vs {bearing}'

    def test_the_direction_is_where_the_front_WENT(self):
        """
        Not where it came from. Arrival age DECREASES the way the front
        travels, so the reported azimuth is that of -grad(t); reporting the
        gradient itself would name the origin's direction and look entirely
        plausible while being exactly backwards.
        """
        sites, draws = _grid(90.0)
        fit = dispersal.fit_trend(sites, draws)
        # The eastern edge is younger than the western one, so the front went
        # east. (Ties within a column: every site in one column has the same
        # arrival, which is what a due-east front means.)
        def age(site):
            return float(np.median(draws[site.parameter_id]))
        west = min(sites, key=lambda s: s.longitude)
        east = max(sites, key=lambda s: s.longitude)
        assert age(east) < age(west)
        assert abs(fit['azimuth']['mean_deg'] - 90.0) < 2.0

    def test_collinear_sites_are_refused(self):
        """
        A plane through a line is not determined: the gradient along the line
        is fixed and the gradient across it is free. lstsq would return one of
        infinitely many planes without saying which.
        """
        sites, draws = _grid(90.0, rows=7, cols=1)
        fit = dispersal.fit_trend(sites, draws)
        assert fit['speed'] is None
        assert 'line' in fit['refused']

    def test_a_transect_along_a_PARALLEL_is_refused_too(self):
        """
        The case a rank check misses. A parallel is a curve in this projection,
        so the design matrix has full rank and a plane IS determined -- by the
        curvature of the map rather than by anything anybody dug. It looks like
        an answer and is an artefact of the projection.
        """
        sites, draws = _grid(90.0, rows=1, cols=7)
        fit = dispersal.fit_trend(sites, draws)
        assert fit['speed'] is None
        assert 'line' in fit['refused']

    def test_five_sites_are_refused(self):
        sites, draws = _grid(90.0, rows=1, cols=5)
        sites[0].latitude += 0.5   # not collinear, just too few
        fit = dispersal.fit_trend(sites, draws)
        assert fit['refused'] is not None
        assert str(dispersal.MIN_DISPERSAL_SITES) in fit['refused']

    def test_chronological_uncertainty_lands_inside_the_speed(self):
        tight = dispersal.fit_trend(*_grid(90.0, scatter=10.0, seed=3))
        loose = dispersal.fit_trend(*_grid(90.0, scatter=150.0, seed=3))

        def width(fit):
            low, high = fit['speed']['hpd_95']
            return high - low

        assert width(loose) > width(tight)

    def test_residuals_are_reported_and_sum_to_nothing(self):
        sites, draws = _grid(90.0, scatter=40.0, seed=9)
        residuals = dispersal.fit_trend(sites, draws)['residuals']
        assert len(residuals) == 9
        years = [r['residual_years'] for r in residuals]
        assert years == sorted(years, reverse=True)
        assert sum(years) == pytest.approx(0.0, abs=1e-6)

    def test_a_site_in_two_places_refuses_the_surface_too(self):
        sites, draws = _grid(90.0)
        sites.append(dispersal.SiteSpec(
            element_id='bad', name='Mixed', parameter_id='bbad',
            basis='boundary', refusal='two places in one phase'))
        assert dispersal.fit_trend(sites, draws)['refused'] == 'two places in one phase'


class TestACircularMean:
    """
    A direction near due north cannot be averaged as a number. This is the one
    piece of arithmetic in the module where the plausible-looking wrong answer
    is exactly 180 degrees out.
    """

    def test_draws_straddling_north_average_to_north(self):
        angles = np.array([359.0, 358.0, 1.0, 2.0, 0.0])
        summary = dispersal._circular_summary(angles)
        # The arithmetic mean of these is 144, which is south-east.
        assert float(np.mean(angles)) > 100.0
        offset = (summary['mean_deg'] + 180.0) % 360.0 - 180.0
        assert abs(offset) < 1.0

    def test_the_interval_wraps_with_it(self):
        summary = dispersal._circular_summary(
            np.array([355.0, 358.0, 0.0, 2.0, 5.0]))
        low, high = summary['hpd_95_deg']
        # Reported as bearings, so the pair reads 355-ish to 5-ish rather than
        # as a 350-degree span the wrong way round.
        assert low > 300.0 and high < 60.0
        assert summary['width_deg'] < 30.0

    def test_scattered_draws_report_a_low_concentration(self):
        """
        R near zero means the draws point every which way and the mean
        direction is an artefact of the arithmetic. Reported so a reader can
        tell "north, confidently" from "north, meaninglessly".
        """
        rng = np.random.default_rng(1)
        assert dispersal._circular_summary(
            rng.uniform(0, 360, 2000))['concentration'] < 0.1
        assert dispersal._circular_summary(
            np.full(200, 42.0))['concentration'] == pytest.approx(1.0)


class TestIsochrons:

    def test_they_are_drawn_at_round_ages(self):
        """An isochron every 137 years is arithmetically fine and reads as
        noise."""
        sites, draws = _grid(90.0)
        ages = [line['age'] for line in dispersal.fit_trend(sites, draws)['isochrons']]
        assert ages, 'a fitted surface should carry isochrons'
        steps = {round(b - a, 6) for a, b in zip(ages, ages[1:])}
        assert len(steps) == 1
        step = steps.pop()
        assert step in (10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0)
        assert all(age % step == 0 for age in ages)

    def test_there_are_few_of_them(self):
        """They are parallel straight lines on a PLANE, so a sixth says
        nothing a fifth did not."""
        sites, draws = _grid(90.0)
        lines = dispersal.fit_trend(sites, draws)['isochrons']
        assert len(lines) <= dispersal.ISOCHRON_COUNT + 1

    def test_each_is_a_pair_of_coordinates_the_map_can_draw(self):
        sites, draws = _grid(90.0)
        for line in dispersal.fit_trend(sites, draws)['isochrons']:
            assert len(line['points']) == 2
            for point in line['points']:
                assert -90 <= point['latitude'] <= 90
                assert -180 <= point['longitude'] <= 180

    def test_they_run_across_the_front_not_along_it(self):
        """
        An isochron is a line of EQUAL arrival, so it is perpendicular to the
        direction of advance. Drawn parallel to it instead -- an easy sign slip
        -- it would look like a plausible figure showing the opposite thing.
        """
        sites, draws = _grid(90.0)   # front heads east
        line = dispersal.fit_trend(sites, draws)['isochrons'][0]
        first, second = line['points']
        # So the line should run north-south: much more change in latitude
        # than in longitude.
        assert abs(first['latitude'] - second['latitude']) > 1.0
        assert abs(first['longitude'] - second['longitude']) < 0.5

    def test_a_surface_with_no_gradient_has_none(self):
        sites, draws = _grid(90.0)
        for key in draws:
            draws[key] = np.full(200, 6000.0)
        assert dispersal.fit_trend(sites, draws)['isochrons'] == []


class TestUnprojecting:

    def test_it_inverts_the_projection(self):
        """The isochrons are computed in the plane and drawn in degrees, so a
        wrong inverse would put a correct line in the wrong place."""
        sites, _draws = _grid(45.0)
        x, y, centre = dispersal._lambert_azimuthal(sites)
        for i, site in enumerate(sites):
            lat, lon = dispersal._unproject(float(x[i]), float(y[i]), centre)
            assert lat == pytest.approx(site.latitude, abs=1e-6)
            assert lon == pytest.approx(site.longitude, abs=1e-6)

    def test_the_centre_projects_to_the_origin(self):
        sites, _draws = _grid(0.0)
        x, y, centre = dispersal._lambert_azimuthal(sites)
        assert dispersal._unproject(0.0, 0.0, centre) == centre


# --------------------------------------------------------------------------
# Profiling the origin
# --------------------------------------------------------------------------

class TestProfilingAnOrigin:
    """
    §6.4 mode 2: what the data say about where the front started, when nobody
    has told them. The answer is a SURFACE, and most of these tests are about
    the app being honest about how broad it is.
    """

    def test_the_true_origin_is_inside_the_region(self):
        """The one thing the profile has to get right. A region that excluded
        the origin the data were built from would be worse than no region."""
        sites, draws, origin = _front(n_sites=8, scatter=60.0, seed=4)
        profile = dispersal.profile_origin(sites, draws)

        near = [p for p in profile['region']
                if abs(p['lat'] - origin[0]) <= 1.0 and abs(p['lon'] - origin[1]) <= 1.0]
        assert near, 'the true origin fell outside the supported region'

    def test_the_region_is_long_along_the_front_and_narrow_across_it(self):
        """
        The degeneracy that makes this a region rather than a point: sliding
        the origin BACKWARDS along the line of travel and adjusting t0 to match
        fits almost exactly as well. That is a real property of the model, so
        the surface has to show it rather than hide it behind a best guess.
        """
        sites, draws, _origin = _front(n_sites=8, scatter=60.0, seed=4)
        region = dispersal.profile_origin(sites, draws)['region']

        # The front runs due east along latitude zero.
        along = max(p['lon'] for p in region) - min(p['lon'] for p in region)
        across = max(p['lat'] for p in region) - min(p['lat'] for p in region)
        assert along > across

    def test_it_says_how_much_of_the_grid_survived(self):
        """
        Near 1 the data do not locate an origin at all -- which is a finding,
        and exactly what a single best-guess marker would have hidden.
        """
        sites, draws, _origin = _front(n_sites=8, scatter=60.0, seed=4)
        profile = dispersal.profile_origin(sites, draws)
        assert 0.0 < profile['share_inside'] <= 1.0
        assert profile['candidates'] > len(profile['region'])

    def test_support_is_a_ratio_against_the_best_candidate(self):
        sites, draws, _origin = _front(n_sites=8, scatter=60.0, seed=4)
        region = dispersal.profile_origin(sites, draws)['region']
        assert all(0.0 < p['support'] <= 1.0 for p in region)
        assert max(p['support'] for p in region) == pytest.approx(1.0)
        assert all(p['support'] >= dispersal.ORIGIN_SUPPORT_CUTOFF for p in region)

    def test_the_peak_sits_inside_the_region_it_defines(self):
        sites, draws, _origin = _front(n_sites=8, scatter=60.0, seed=4)
        profile = dispersal.profile_origin(sites, draws)
        peak = profile['peak']
        assert any(p['lat'] == peak['lat'] and p['lon'] == peak['lon']
                   for p in profile['region'])

    def test_a_tighter_front_gives_a_tighter_region(self):
        """If chronological precision did not narrow the surface, the profile
        would not be using the draws for anything."""
        loose = dispersal.profile_origin(*_front(n_sites=8, scatter=400.0, seed=8)[:2])
        tight = dispersal.profile_origin(*_front(n_sites=8, scatter=5.0, seed=8)[:2])
        assert tight['share_inside'] < loose['share_inside']

    def test_five_sites_are_refused(self):
        sites, draws, _origin = _front(n_sites=5)
        profile = dispersal.profile_origin(sites, draws)
        assert profile['region'] == []
        assert str(dispersal.MIN_DISPERSAL_SITES) in profile['refused']

    def test_a_site_in_two_places_refuses_the_profile(self):
        sites, draws, _origin = _front(n_sites=8)
        sites.append(dispersal.SiteSpec(
            element_id='bad', name='Mixed', parameter_id='bbad',
            basis='boundary', refusal='two places in one phase'))
        assert dispersal.profile_origin(
            sites, draws)['refused'] == 'two places in one phase'

    def test_the_grid_widens_rather_than_exploding(self):
        """
        A continental spread at one degree would be tens of thousands of
        candidates, to locate the maximum of a surface that is flat where it
        matters. The step widens instead, and says that it did.
        """
        sites, draws, _origin = _front(n_sites=8)
        for index, site in enumerate(sites):
            site.latitude = -40.0 + 12.0 * index
            site.longitude = -60.0 + 15.0 * index
        profile = dispersal.profile_origin(sites, draws)

        assert profile['candidates'] <= dispersal.MAX_ORIGIN_CANDIDATES
        assert profile['step_deg'] > dispersal.ORIGIN_GRID_DEG

    def test_it_looks_outside_the_sites_own_box(self):
        """
        A front's origin is very often outside the excavated area -- that is
        usually the whole question -- so a grid clipped to the sites could not
        find it.
        """
        sites, draws, _origin = _front(n_sites=8)
        profile = dispersal.profile_origin(sites, draws)
        west = min(s.longitude for s in sites)
        assert any(p['lon'] < west for p in profile['region'])


# --------------------------------------------------------------------------
# Model 3: a spatial order, proposed
# --------------------------------------------------------------------------

def _placed(*coords):
    return [dispersal.SiteSpec(
        element_id=f'e{i}', name=f'Site {i}', parameter_id=f'b{i}',
        basis='boundary', latitude=lat, longitude=lon)
        for i, (lat, lon) in enumerate(coords)]


class TestProposingASpatialOrder:
    """
    §6.7. The cheapest of the three models and the one that must be most
    careful about what it claims: a spanning tree is spatial adjacency, not
    chronology.
    """

    def test_it_needs_no_draws_at_all(self):
        """A question about positions, not about ages -- which is why it can
        be offered while a model is still being written, before there is
        anything to run."""
        sites = _placed((50.0, 0.0), (50.0, 1.0), (50.0, 2.0))
        assert dispersal.propose_sequence(sites)['order']

    def test_a_line_of_sites_comes_back_in_order(self):
        sites = _placed((50.0, 0.0), (50.0, 1.0), (50.0, 2.0), (50.0, 3.0))
        proposal = dispersal.propose_sequence(sites, origin=(50.0, -1.0))
        assert [o['name'] for o in proposal['order']] == [
            'Site 0', 'Site 1', 'Site 2', 'Site 3']

    def test_the_origin_decides_which_end_it_starts_from(self):
        sites = _placed((50.0, 0.0), (50.0, 1.0), (50.0, 2.0), (50.0, 3.0))
        from_east = dispersal.propose_sequence(sites, origin=(50.0, 9.0))
        assert [o['name'] for o in from_east['order']] == [
            'Site 3', 'Site 2', 'Site 1', 'Site 0']

    def test_with_no_origin_it_starts_at_an_end_of_the_longest_path(self):
        """
        Found rather than assumed. Starting at whichever site happened to be
        listed first would put the walk in the middle of the arrangement and
        produce an order that doubles back.
        """
        sites = _placed((50.0, 2.0), (50.0, 0.0), (50.0, 1.0), (50.0, 3.0))
        names = [o['name'] for o in dispersal.propose_sequence(sites)['order']]
        # Site 1 is the westernmost and Site 3 the easternmost; the walk has
        # to begin at one of them, not at Site 0 in the middle.
        assert names[0] in ('Site 1', 'Site 3')
        assert names[-1] in ('Site 1', 'Site 3')

    def test_the_tree_is_minimum_not_merely_connected(self):
        """
        Four sites on a square: the MST takes three sides and never the
        diagonal, which is longer than any of them.
        """
        sites = _placed((50.0, 0.0), (50.0, 1.0), (51.0, 0.0), (51.0, 1.0))
        proposal = dispersal.propose_sequence(sites)
        assert len(proposal['edges']) == 3
        longest_side = max(e['km'] for e in proposal['edges'])
        diagonal = haversine_km(50.0, 0.0, 51.0, 1.0)
        assert longest_side < diagonal

    def test_every_site_appears_exactly_once(self):
        sites = _placed((50.0, 0.0), (51.0, 1.0), (49.0, 2.0),
                        (52.0, 0.5), (50.5, 3.0), (48.0, 1.5))
        order = dispersal.propose_sequence(sites)['order']
        ids = [o['element_id'] for o in order]
        assert sorted(ids) == sorted(s.element_id for s in sites)
        assert len(ids) == len(set(ids))

    def test_it_carries_the_tree_it_read_the_order_off(self):
        """So the map can draw what the proposal was derived from, rather than
        only the conclusion."""
        sites = _placed((50.0, 0.0), (50.0, 1.0), (50.0, 2.0))
        proposal = dispersal.propose_sequence(sites)
        assert len(proposal['edges']) == len(sites) - 1
        assert proposal['total_km'] == pytest.approx(
            sum(e['km'] for e in proposal['edges']))
        for edge in proposal['edges']:
            assert edge['from'] != edge['to']

    def test_one_site_is_refused(self):
        proposal = dispersal.propose_sequence(_placed((50.0, 0.0)))
        assert proposal['order'] == []
        assert 'two placed sites' in proposal['refused']

    def test_a_site_in_two_places_refuses_the_proposal(self):
        sites = _placed((50.0, 0.0), (50.0, 1.0))
        sites.append(dispersal.SiteSpec(
            element_id='bad', name='Mixed', parameter_id='bbad',
            basis='boundary', refusal='two places in one phase'))
        assert dispersal.propose_sequence(
            sites)['refused'] == 'two places in one phase'

    def test_unplaced_sites_are_left_out_rather_than_guessed_at(self):
        sites = _placed((50.0, 0.0), (50.0, 1.0))
        sites.append(dispersal.SiteSpec(
            element_id='nowhere', name='Unplaced', parameter_id='bn',
            basis='boundary'))
        proposal = dispersal.propose_sequence(sites)
        assert [o['name'] for o in proposal['order']] == ['Site 0', 'Site 1']
        assert proposal['n_sites'] == 2
