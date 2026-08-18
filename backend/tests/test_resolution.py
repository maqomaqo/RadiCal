"""
Expected temporal resolution: services/resolution.py and the endpoints over it.

What makes this testable at all is that the answer has known shape. The
Hallstatt plateau (roughly 2,750-2,400 cal BP) is the best known bad stretch
in the Holocene, and the drop at its start is one of the best; a wrong
implementation smooths that structure away while still returning plausible
numbers. So most of what follows asserts the shape rather than a value, and
the one test that does assert values holds them against the implementation
the method comes from (skipped unless you generate its output -- see
fixtures/res14c/README.md).
"""
import os

import numpy as np
import pytest

from app.services import curve_registry, resolution

FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'res14c',
    'intcal20_u15_reference.csv',
)


@pytest.fixture(scope='module')
def intcal():
    return curve_registry.resolve('intcal20')


@pytest.fixture(scope='module')
def small():
    """
    A short synthetic curve, for the tests about caching and slicing rather
    than about radiocarbon.

    Those need a WHOLE-curve profile, and computing one for IntCal20 takes
    about fifteen seconds because the calculation scans every radiocarbon age
    the curve reaches -- `stride` thins the output, not the work. Three of
    them made this file take 51 seconds to assert things that have nothing to
    do with which curve it is.
    """
    from app.services.calibration import CalibrationCurve
    cal = np.arange(0, 3000, 5.0)
    c14 = np.where(cal < 1500, cal * 0.95, 1425 + (cal - 1500) * 0.4)
    return CalibrationCurve(cal, c14, np.full_like(cal, 15.0))


def at(profile, cal):
    """The mean resolution reported nearest a calendar age."""
    return float(profile.mean[int(np.argmin(np.abs(profile.cal_ages - cal)))])


class TestTheShapeIsRight:

    def test_the_hallstatt_plateau_is_the_worst_of_its_neighbourhood(self, intcal):
        """
        800-400 BC is the stretch archaeologists complain about, and this is
        the number that says why: a date from inside it is worth roughly half
        as much as one from either side.
        """
        profile = resolution.profile(intcal, 2000, 3200, stride=5)
        plateau = at(profile, 2600)
        assert plateau > 250
        assert plateau > at(profile, 2200) * 1.4
        assert plateau > at(profile, 3000) * 1.4

    def test_the_drop_at_the_plateaus_start_is_the_best_of_its_neighbourhood(self, intcal):
        """
        The steep fall at ~2,750 cal BP is where resolution is exceptional --
        about 34 years, against ~300 a century and a half later. A smoothed or
        mis-weighted implementation loses this entirely, which is why it is
        asserted as sharply as it is.

        At u0=15 explicitly, because that is the uncertainty these figures
        were measured at and the one res14c publishes. The feature is there at
        any precision -- 103 years against 330 at u0=25 -- but the absolute
        numbers are not, so a test asserting them has to say which run it
        means.
        """
        profile = resolution.profile(intcal, 2600, 2900, u0=15, stride=1)
        trough = at(profile, 2750)
        assert trough < 45
        assert trough < at(profile, 2650) / 3

    def test_resolution_is_worse_deep_in_the_pleistocene(self, intcal):
        """Larger curve errors and a larger scaled laboratory error both push
        the same way."""
        recent = resolution.profile(intcal, 3000, 3400, stride=10)
        ancient = resolution.profile(intcal, 40000, 40400, stride=10)
        assert np.nanmean(ancient.mean) > np.nanmean(recent.mean) * 2

    def test_the_percentile_band_brackets_the_mean(self, intcal):
        profile = resolution.profile(intcal, 2000, 3000, stride=5)
        assert np.all(profile.p05 <= profile.mean + 1e-9)
        assert np.all(profile.p95 >= profile.mean - 1e-9)


class TestTheLaboratoryUncertainty:

    def test_a_worse_measurement_is_never_a_better_date(self, intcal):
        """
        Monotonicity in u0. Not obvious from the code -- the width comes out
        of an HPD construction over a multimodal distribution, not from a
        formula in u -- but it has to hold, and if it does not the profile is
        not measuring what it claims to.
        """
        window = (2000, 3000)
        tight = resolution.profile(intcal, *window, u0=15, stride=10)
        loose = resolution.profile(intcal, *window, u0=30, stride=10)
        assert np.all(loose.mean >= tight.mean - 1e-6)
        assert np.nanmean(loose.mean) > np.nanmean(tight.mean)

    def test_the_uncertainty_reported_is_the_one_used(self, intcal):
        """It scales with age, so a profile that reported u0 flat would be
        describing a calculation nobody ran."""
        profile = resolution.profile(intcal, 0, 40000, stride=1000)
        assert profile.uncertainty[0] == pytest.approx(resolution.DEFAULT_U0, abs=0.5)
        assert profile.uncertainty[-1] > 40
        assert np.all(np.diff(profile.uncertainty) > 0)

    def test_a_non_positive_uncertainty_is_refused(self, intcal):
        with pytest.raises(ValueError):
            resolution.profile(intcal, 2000, 2100, u0=0)


class TestWindowingIsExactRatherThanApproximate:

    def test_a_window_matches_the_same_span_of_a_wider_run(self, intcal):
        """
        The correctness condition for computing a window on demand. A window
        that did not pad far enough would clip the calendar distributions that
        run past its edge, and report better resolution than the curve
        achieves -- worst exactly where resolution is worst, since those are
        the distributions that spread furthest.
        """
        wide = resolution.profile(intcal, 1000, 6000, stride=5)
        for lo, hi in [(2400, 2800), (4900, 5100), (1200, 1260)]:
            narrow = resolution.profile(intcal, lo, hi, stride=5)
            expected = np.interp(narrow.cal_ages, wide.cal_ages, wide.mean)
            # 1e-6, not exact equality: the two runs scan a slightly different
            # span of radiocarbon ages -- their padding is computed from their
            # own windows -- so identical sums arrive in a different order. It
            # shows only at a window's outermost point and only in the ninth
            # significant figure. A padding shortfall, which is what this test
            # is actually guarding against, would show at percent level and
            # across the whole edge.
            assert np.allclose(narrow.mean, expected, rtol=1e-6)

    def test_the_cached_whole_curve_and_a_direct_window_agree(self, small):
        whole = resolution.full_profile(small, 'small-curve', stride=10)
        piece = resolution.slice_profile(whole, 1000, 1500)
        direct = resolution.profile(small, 1000, 1500, stride=10)
        common = np.intersect1d(piece.cal_ages, direct.cal_ages)
        assert len(common) > 5
        for cal in common:
            # Not exact equality here, and it is worth being precise about
            # why. Each run scans the radiocarbon ages its own window implies,
            # so the 5-sigma cutoff past which a measurement is dropped falls
            # in a slightly different place for the two. On this curve that
            # reaches 1.7e-5 at the worst shared year (mean 5e-7), because it
            # is a deliberate stress case: 1,500 years of straight line at
            # slope 0.4, which spreads a calibrated distribution about two and
            # a half times as far in calendar years as the padding formula's
            # roughly one-to-one assumption allows for. On the real curves the
            # same comparison is exact to 1e-9 -- see the test above, which is
            # the one that would catch a padding shortfall that mattered.
            assert at(piece, cal) == pytest.approx(at(direct, cal), rel=1e-4)

    def test_the_cache_is_keyed_on_curve_content(self, small):
        """Keyed by content hash, not curve id: the builtin curve files are
        all overridable by environment variable, and a swapped file must not
        be served the previous file's answer."""
        first = resolution.full_profile(small, 'content-aaa', stride=20)
        again = resolution.full_profile(small, 'content-aaa', stride=20)
        assert again is first
        other = resolution.full_profile(small, 'content-bbb', stride=20)
        assert other is not first


class TestParityWithTheReferenceImplementation:
    """
    Held against res14c's own published output, which is not committed -- see
    fixtures/res14c/README.md for why, and how to generate it.
    """

    @pytest.mark.skipif(not os.path.exists(FIXTURE), reason="res14c reference output not present")
    def test_agrees_with_res14c_over_the_late_holocene(self, intcal):
        reference = np.genfromtxt(FIXTURE, delimiter=',', skip_header=1)
        ref_cal = 1950 - reference[:, 0] + 1
        selected = (ref_cal >= 1000) & (ref_cal <= 4000)
        ref_cal, ref_mean = ref_cal[selected], reference[selected, 1]

        # u0=15 explicitly: the fixture is res14c's published run at that
        # uncertainty, so this comparison is only meaningful against it. It
        # used to inherit DEFAULT_U0 and so quietly depended on that constant
        # never changing -- which it since has.
        profile = resolution.profile(intcal, 1000, 4000, u0=15, stride=1)
        mine = np.interp(ref_cal[::-1], profile.cal_ages, profile.mean)
        theirs = ref_mean[::-1]

        relative = np.abs(mine - theirs) / theirs
        assert relative.mean() < 0.01
        assert relative.max() < 0.05


class TestEndpoint:

    def test_a_window(self, client):
        response = client.get(
            '/api/curves/intcal20/resolution?cal_from=2400&cal_to=2800&stride=10'
        )
        assert response.status_code == 200
        data = response.json()['data']
        assert data['curve_id'] == 'intcal20'
        assert data['u0'] == resolution.DEFAULT_U0
        assert all(len(p) == 5 for p in data['points'])
        assert 2400 <= data['points'][0][0] <= 2800

    def test_the_plateau_reads_worse_than_its_surroundings_through_the_api(self, client):
        """The same claim as the service test, through the endpoint the chart
        actually draws from."""
        points = client.get(
            '/api/curves/intcal20/resolution?cal_from=2000&cal_to=3200&stride=10'
        ).json()['data']['points']
        by_age = {int(p[0]): p[1] for p in points}
        assert by_age[2600] > by_age[2200]
        assert by_age[2600] > by_age[3000]

    def test_uncertainty_is_a_parameter(self, client):
        base = client.get('/api/curves/intcal20/resolution?cal_from=2000&cal_to=2200&u0=15')
        worse = client.get('/api/curves/intcal20/resolution?cal_from=2000&cal_to=2200&u0=30')
        mean = lambda r: np.mean([p[1] for p in r.json()['data']['points']])
        assert mean(worse) > mean(base)

    def test_a_nonsense_uncertainty_is_rejected(self, client):
        assert client.get('/api/curves/intcal20/resolution?u0=0').status_code == 422
        assert client.get('/api/curves/intcal20/resolution?u0=-5').status_code == 422

    def test_unknown_curve_is_404(self, client):
        assert client.get('/api/curves/intcal13/resolution').status_code == 404

    def test_csv_export_carries_the_reference_columns(self, client):
        response = client.get('/api/curves/marine20/resolution.csv?stride=200')
        assert response.status_code == 200
        header, first = response.text.splitlines()[:2]
        assert header.startswith('Sample Age (cal BP),Mean Resolution')
        assert len(first.split(',')) == 5
        assert 'attachment' in response.headers['content-disposition']
