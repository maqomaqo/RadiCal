"""
Deriving a ΔR from a position: services/reservoir.py's nearest, combine and
lookup.

The load-bearing tests are the three in TestCombineReproducesCalibOrg. They
are published results from calib.org's own regioncalc.php, with the map
numbers and the point values they were computed from, and they are the whole
basis of the feature: reproducing that arithmetic EXACTLY is what lets this
app derive a value a reviewer can check on calib.org's page without the app
ever calling calib.org with a site's coordinates. The third case exists only
to discriminate max() from adding in quadrature.

Everything else here guards the ways a derived value can be wrong while
looking right -- a mean built from one point, a habit breakdown describing a
set the number did not come from, a nearest-N that silently became a
nearest-N-minus-two.
"""
import math

import pytest

from app.services import reservoir
from app.services.reservoir import ReservoirPoint


def point(map_no, delta_r, delta_r_error, *, lat=54.5, lon=-5.6,
          feeding='suspension', raw=None):
    return ReservoirPoint(
        map_no=map_no, latitude=lat, longitude=lon,
        delta_r=delta_r, delta_r_error=delta_r_error,
        feeding=feeding, feeding_raw=raw or feeding,
        locality=f'Point {map_no}', reference='Reimer and Reimer 2001',
    )


class TestCombineReproducesCalibOrg:
    """
    Three results calib.org printed, and the points behind them. The point
    values are the database's own, read out of the 2026-09-14 capture.
    """

    # (map numbers, [(ΔR, σ)], what calib.org prints)
    CASE_TWO = ([995, 992], [(-239, 25), (-238, 35)], (-239, 20))
    CASE_FIVE = (
        [2368, 1470, 2350, 2024, 834],
        [(-257, 36), (-160, 84), (-99, 36), (2, 25), (327, 20)],
        (90, 247),
    )
    CASE_THREE = (
        [2590, 1006, 2147],
        [(-79, 66), (-165, 30), (-174, 22)],
        (-165, 29),
    )

    def _combine(self, case):
        map_nos, values, _expected = case
        return reservoir.combine([
            point(m, dr, err) for m, (dr, err) in zip(map_nos, values)
        ])

    @pytest.mark.parametrize('case_name', ['CASE_TWO', 'CASE_FIVE', 'CASE_THREE'])
    def test_it_matches_the_printed_integers(self, case_name):
        case = getattr(self, case_name)
        result = self._combine(case)
        expected_delta_r, expected_error = case[2]
        assert round(result.delta_r) == expected_delta_r
        assert round(result.delta_r_error) == expected_error

    def test_the_uncertainty_is_the_max_and_not_the_quadrature_sum(self):
        """
        The case that discriminates. For 2590, 1006 and 2147 the internal
        uncertainty is 17.13 and the scatter 28.65: max gives 29, which is
        what calib.org prints, and adding them in quadrature gives 33.
        Getting this wrong would produce values that look right and are not
        reproducible against the source.
        """
        result = self._combine(self.CASE_THREE)
        assert round(result.internal) == 17
        assert round(result.scatter) == 29
        assert round(result.delta_r_error) == 29

        quadrature = math.hypot(result.internal, result.scatter)
        assert round(quadrature) == 33
        assert round(result.delta_r_error) != round(quadrature)

    def test_the_scatter_dominates_a_disagreeing_set(self):
        """Five points spanning 584 years report their disagreement, not the
        precision of their mean: 247 rather than 13."""
        result = self._combine(self.CASE_FIVE)
        assert round(result.internal) == 13
        assert round(result.delta_r_error) == 247

    def test_the_internal_dominates_an_agreeing_set(self):
        """Two points 1 year apart: nothing to disagree about, so the reported
        uncertainty is the precision of the mean."""
        result = self._combine(self.CASE_TWO)
        assert round(result.scatter) == 1
        assert round(result.delta_r_error) == 20


class TestCombine:
    def test_a_point_with_no_uncertainty_is_excluded_and_named(self):
        """
        Thirty rows of 2060 are in this position. Giving one an invented error
        would let it into a weighted mean; dropping it silently would overstate
        how much evidence is behind the number. So it is excluded, by map
        number.
        """
        result = reservoir.combine([
            point(1, -200, 30), point(2, -210, 40), point(3, -500, None),
        ])
        assert result.excluded_no_error == [3]
        assert result.n == 2
        assert result.map_nos == [1, 2]

    def test_a_zero_uncertainty_is_treated_as_no_uncertainty(self):
        """1/0² is not a weight. Excluded rather than crashing, and named the
        same way."""
        result = reservoir.combine([
            point(1, -200, 30), point(2, -210, 40), point(3, -220, 0),
        ])
        assert result.excluded_no_error == [3]
        assert result.n == 2

    def test_one_point_reports_its_own_error(self):
        """There is nothing to disagree with, so the scatter is zero and the
        max is the point's own uncertainty -- which is the honest answer, and
        why lookup() refuses to build an answer from one point at all."""
        result = reservoir.combine([point(1, -200, 30)])
        assert result.delta_r == -200
        assert result.delta_r_error == 30
        assert result.scatter == 0

    def test_nothing_weighable_is_an_empty_result_rather_than_an_error(self):
        result = reservoir.combine([point(1, -200, None)])
        assert result.n == 0
        assert result.delta_r is None
        assert result.excluded_no_error == [1]

    def test_the_more_precise_point_pulls_harder(self):
        """Inverse-variance weighting, stated as a property rather than a
        recomputation of the formula under test."""
        result = reservoir.combine([point(1, -100, 10), point(2, -200, 100)])
        assert result.delta_r < -100
        assert result.delta_r > -150


class TestHaversine:
    def test_a_point_is_no_distance_from_itself(self):
        assert reservoir.haversine_km(54.5, -5.6, 54.5, -5.6) == 0

    def test_a_degree_of_latitude_is_about_111_km(self):
        assert reservoir.haversine_km(54, 0, 55, 0) == pytest.approx(111.2, abs=0.5)

    def test_a_degree_of_longitude_shrinks_towards_the_pole(self):
        """
        Why the distance is great-circle and not planar. At 70 degrees north a
        degree of longitude is a third of what it is at the equator, so a
        planar 'nearest' would reach three times too far east and west of a
        Norwegian site and rank the wrong points.
        """
        equator = reservoir.haversine_km(0, 0, 0, 1)
        northern = reservoir.haversine_km(70, 0, 70, 1)
        assert northern == pytest.approx(equator * math.cos(math.radians(70)), rel=0.01)

    def test_it_crosses_the_date_line_the_short_way(self):
        """Two points either side of the antimeridian are 2 degrees apart, not
        358 -- which matters because three rows of the database sit there."""
        assert reservoir.haversine_km(0, 179, 0, -179) == pytest.approx(222.4, abs=1)


class TestNearest:
    def _spread(self):
        return [
            point(1, -200, 30, lat=54.5, lon=-5.6),     # 0 km
            point(2, -210, 30, lat=54.6, lon=-5.6),     # ~11 km
            point(3, -220, 30, lat=55.5, lon=-5.6),     # ~111 km
            point(4, -230, 30, lat=60.0, lon=-5.6),     # ~612 km
        ]

    def test_it_returns_the_n_closest_nearest_first(self):
        got = reservoir.nearest(54.5, -5.6, self._spread(), 2)
        assert [p.map_no for p in got] == [1, 2]
        assert got[0].distance_km < got[1].distance_km

    def test_each_point_carries_its_distance(self):
        got = reservoir.nearest(54.5, -5.6, self._spread(), 4)
        assert got[0].distance_km == 0
        assert got[2].distance_km == pytest.approx(111, abs=2)

    def test_max_km_drops_what_is_too_far(self):
        got = reservoir.nearest(54.5, -5.6, self._spread(), 10, max_km=50)
        assert [p.map_no for p in got] == [1, 2]

    def test_a_point_with_no_delta_r_is_not_a_candidate(self):
        """A row with a position and no offset has nothing to say about the
        reservoir. Distinct from a row with an offset and no uncertainty,
        which IS a candidate and is excluded later, by name."""
        points = self._spread() + [point(5, None, 30, lat=54.5, lon=-5.6)]
        assert 5 not in [p.map_no for p in reservoir.nearest(54.5, -5.6, points, 10)]

    def test_a_point_with_no_uncertainty_is_still_a_candidate(self):
        points = [point(9, -200, None, lat=54.5, lon=-5.6)]
        assert [p.map_no for p in reservoir.nearest(54.5, -5.6, points, 5)] == [9]

    def test_ties_break_on_map_number_so_a_lookup_is_reproducible(self):
        """Two points at the same distance must come back in the same order on
        every call, or a provenance record naming them cannot be checked."""
        tied = [point(7, -200, 30, lat=54.6, lon=-5.6),
                point(3, -210, 30, lat=54.4, lon=-5.6)]
        first = [p.map_no for p in reservoir.nearest(54.5, -5.6, tied, 2)]
        second = [p.map_no for p in reservoir.nearest(54.5, -5.6, list(reversed(tied)), 2)]
        assert first == second == [3, 7]


class TestLookup:
    def _mixed(self):
        """Suspension feeders near, with a deposit feeder and a migratory
        animal among them -- the ordinary shape of a nearest-N set."""
        return [
            point(1, -200, 20, lat=54.50, lon=-5.6),
            point(2, -210, 25, lat=54.55, lon=-5.6),
            point(3, -120, 30, lat=54.60, lon=-5.6, feeding='deposit', raw='Deposit'),
            point(4, -190, 30, lat=54.65, lon=-5.6),
            point(5, -100, 40, lat=54.70, lon=-5.6, feeding='migratory'),
        ]

    def test_the_default_includes_everything(self):
        """
        The reference implementation includes every point, so this one does
        too. An app whose default disagreed with calib.org's published number
        for the same coordinates would throw away the reproducibility that
        reproducing their arithmetic exactly was for.
        """
        result = reservoir.lookup(54.5, -5.6, self._mixed(), n=5)
        assert result.n == 5
        assert result.delta_r == result.included_all.delta_r

    def test_the_counterfactual_is_computed_whether_or_not_it_was_asked_for(self):
        """§5.4.1's whole recommendation: never prompt, never exclude by
        default, compute both and show the difference."""
        result = reservoir.lookup(54.5, -5.6, self._mixed(), n=5)
        assert result.suspension_equivalent is not None
        assert result.suspension_equivalent.n == 3
        assert set(result.suspension_equivalent.map_nos) == {1, 2, 4}

    def test_the_counterfactual_is_hand_checkable(self):
        """
        Points 1, 2 and 4: (-200, 20), (-210, 25), (-190, 30). Computed here
        independently of combine() so the two would have to be wrong the same
        way to agree.
        """
        result = reservoir.lookup(54.5, -5.6, self._mixed(), n=5)
        weights = [1 / 20 ** 2, 1 / 25 ** 2, 1 / 30 ** 2]
        values = [-200, -210, -190]
        total = sum(weights)
        expected = sum(w * v for w, v in zip(weights, values)) / total
        assert result.suspension_equivalent.delta_r == pytest.approx(expected)

    def test_the_shift_is_reported_and_compared_to_the_threshold(self):
        result = reservoir.lookup(54.5, -5.6, self._mixed(), n=5)
        assert result.feeding_shift == pytest.approx(
            abs(result.included_all.delta_r - result.suspension_equivalent.delta_r))
        assert result.feeding_shift_matters == (
            result.feeding_shift >= reservoir.FEEDING_SHIFT_WARN_YEARS)

    def test_a_small_shift_does_not_raise_the_warning(self):
        """Below 35 yr the difference is a line of small type. Four points in
        close agreement, one of them a deposit feeder."""
        points = [
            point(1, -200, 20), point(2, -205, 20),
            point(3, -202, 20, feeding='deposit'), point(4, -203, 20),
        ]
        result = reservoir.lookup(54.5, -5.6, points, n=4)
        assert result.feeding_shift < reservoir.FEEDING_SHIFT_WARN_YEARS
        assert result.feeding_shift_matters is False

    def test_a_large_shift_does(self):
        points = [
            point(1, -200, 20), point(2, -205, 20),
            point(3, 400, 20, feeding='migratory'), point(4, -203, 20),
        ]
        result = reservoir.lookup(54.5, -5.6, points, n=4)
        assert result.feeding_shift >= reservoir.FEEDING_SHIFT_WARN_YEARS
        assert result.feeding_shift_matters is True

    def test_suspension_only_applies_the_counterfactual_when_asked(self):
        result = reservoir.lookup(54.5, -5.6, self._mixed(), n=5, suspension_only=True)
        assert result.delta_r == result.suspension_equivalent.delta_r
        assert result.n == 3

    def test_unknown_and_grazer_are_never_excluded_by_the_preset(self):
        """
        §2.7 measured both as indistinguishable from the suspension pool, and
        dropping `unknown` alone would discard 14% of the database to fix
        nothing.
        """
        points = [
            point(1, -200, 20, feeding='unknown'),
            point(2, -210, 20, feeding='grazer'),
            point(3, -190, 20, feeding='suspension'),
        ]
        result = reservoir.lookup(54.5, -5.6, points, n=3, suspension_only=True)
        assert result.n == 3
        assert set(result.suspension_equivalent.map_nos) == {1, 2, 3}

    def test_ambiguous_is_never_excluded_either(self):
        """Those five values are truncated compound judgements. Dropping them
        would decide the question their author declined to decide."""
        points = [
            point(1, -200, 20, feeding='ambiguous', raw='Deposit or suspe'),
            point(2, -210, 20), point(3, -190, 20),
        ]
        result = reservoir.lookup(54.5, -5.6, points, n=3, suspension_only=True)
        assert 1 in result.suspension_equivalent.map_nos

    def test_exclusion_that_would_leave_too_few_points_is_refused(self):
        """
        Forty query points in the jackknife fall below two once the biased
        classes go. Returning a "weighted mean" of one point -- whose sigma is
        just that point's own error -- would read as precision where there is
        no evidence of any.
        """
        points = [
            point(1, -200, 20, feeding='deposit'),
            point(2, -210, 20, feeding='migratory'),
            point(3, -190, 20),
        ]
        with pytest.raises(reservoir.LookupRefused) as raised:
            reservoir.lookup(54.5, -5.6, points, n=3, suspension_only=True)
        message = str(raised.value)
        assert 'deposit' in message and 'migratory' in message

    def test_the_refusal_names_the_classes_it_would_have_dropped(self):
        """"No answer" and "no answer because you excluded six of the eight
        points" are different things to the person reading it."""
        points = [point(1, -200, 20, feeding='carnivore'), point(2, -210, 20)]
        with pytest.raises(reservoir.LookupRefused) as raised:
            reservoir.lookup(54.5, -5.6, points, n=2, suspension_only=True)
        assert 'carnivore' in str(raised.value)

    def test_too_few_weighable_points_is_refused(self):
        points = [point(1, -200, None), point(2, -210, None), point(3, -190, 20)]
        with pytest.raises(reservoir.LookupRefused) as raised:
            reservoir.lookup(54.5, -5.6, points, n=3)
        assert 'at least 2' in str(raised.value)

    def test_nothing_within_range_is_refused_and_says_so(self):
        points = [point(1, -200, 20, lat=0, lon=0)]
        with pytest.raises(reservoir.LookupRefused) as raised:
            reservoir.lookup(54.5, -5.6, points, n=5, max_km=50)
        assert '50 km' in str(raised.value)

    def test_the_nearest_distance_is_reported(self):
        """
        §2.6: Tampa's nearest point is 163 km away and the p90 is 223 km.
        Distance to the nearest point is the single most informative thing to
        show beside a derived value, and it has to be shown before the value
        is accepted.
        """
        result = reservoir.lookup(54.5, -5.6, self._mixed(), n=5)
        assert result.nearest_km == 0
        assert result.farthest_km > result.nearest_km

    def test_the_nearest_distance_counts_a_point_that_could_not_be_weighted(self):
        """The warning is about how far the evidence is, which is true whether
        or not the nearest point carried an uncertainty."""
        points = [
            point(1, -200, None, lat=54.5, lon=-5.6),
            point(2, -210, 20, lat=55.5, lon=-5.6),
            point(3, -190, 20, lat=55.6, lon=-5.6),
        ]
        result = reservoir.lookup(54.5, -5.6, points, n=3)
        assert result.nearest_km == 0
        assert result.excluded_no_error == [1]

    def test_the_habit_breakdown_describes_the_points_actually_used(self):
        """A count including a point excluded for having no uncertainty would
        describe a set the number did not come from."""
        points = [
            point(1, -200, 20), point(2, -210, 20),
            point(3, -190, None, feeding='migratory'),
        ]
        result = reservoir.lookup(54.5, -5.6, points, n=3)
        assert result.feeding_counts == {'suspension': 2}
        assert result.excluded_no_error == [3]

    def test_a_user_exclusion_is_honoured_and_recorded(self):
        result = reservoir.lookup(
            54.5, -5.6, self._mixed(), n=5, exclude_map_nos=[3, 5])
        assert result.excluded_by_user == [3, 5]
        assert set(result.included_all.map_nos) == {1, 2, 4}

    def test_the_raw_feeding_string_travels_with_the_class(self):
        """So the normalisation table can be corrected later without any
        stored provenance becoming a lie about what the source said."""
        result = reservoir.lookup(54.5, -5.6, self._mixed(), n=5)
        assert result.feeding_raw[3] == 'Deposit'


class TestProvenance:
    def test_it_records_what_is_needed_to_reproduce_the_value(self):
        points = [
            point(1, -200, 20), point(2, -210, 25),
            point(3, -120, 30, feeding='deposit', raw='Deposit'),
        ]
        result = reservoir.lookup(
            54.5, -5.6, points, n=3, snapshot_id='calib-marine20-2026-05-11')
        record = reservoir.provenance(result)

        assert record['snapshot_id'] == 'calib-marine20-2026-05-11'
        assert record['centre'] == {'lat': 54.5, 'lon': -5.6}
        assert sorted(record['map_nos']) == [1, 2, 3]
        assert record['nearest_km'] is not None
        assert record['computed_at']

    def test_it_carries_the_counterfactual_even_when_it_was_not_applied(self):
        """The value a reader needs to judge the one that WAS applied."""
        points = [
            point(1, -200, 20), point(2, -210, 25), point(3, -190, 25),
            point(4, -120, 30, feeding='deposit'),
        ]
        result = reservoir.lookup(54.5, -5.6, points, n=4)
        record = reservoir.provenance(result)
        assert record['counterfactual_suspension_only']['n'] == 3

    def test_it_records_the_preset_that_was_applied(self):
        points = [point(1, -200, 20), point(2, -210, 25), point(3, -190, 25)]
        result = reservoir.lookup(54.5, -5.6, points, n=3, suspension_only=True)
        record = reservoir.provenance(result, suspension_only=True, max_km=100)
        assert record['filters'] == {'feeding_preset': 'suspension_only', 'max_km': 100}

    def test_the_raw_strings_are_keyed_by_map_number_as_text(self):
        """JSON has no integer keys, and a provenance record that changed
        shape on the way through the database would be worse than one that was
        awkward from the start."""
        points = [
            point(1, -200, 20, feeding='deposit', raw='Deposit'),
            point(2, -210, 25), point(3, -190, 25),
        ]
        result = reservoir.lookup(54.5, -5.6, points, n=3)
        record = reservoir.provenance(result)
        assert record['feeding_raw']['1'] == 'Deposit'
