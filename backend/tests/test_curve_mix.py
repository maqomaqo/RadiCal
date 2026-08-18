"""
Curves made by mixing two others: services/curve_mix.py, the endpoint over it,
and Mix_Curves in the OxCal importer.

The case this exists for is a sample whose carbon came from more than one
reservoir -- most often a human or animal with a partly marine diet. Their
bone is neither terrestrial nor marine carbon, and calibrating it against
either curve alone is wrong by centuries in one direction or the other,
with nothing in the result to say so.

The tests that matter are the ones about the uncertainty on the proportion.
A dietary fraction is an estimate, and a mixed curve that ignored its error
would state a confident age from an admittedly uncertain diet -- while
looking exactly as trustworthy as one that did not.
"""
import numpy as np
import pytest

from app.services import curve_mix, curve_registry
from app.services.calibration import CalibrationCurve

# The endpoint tests store curves, and a stored curve changes what the whole
# application will accept -- so they are removed again. See conftest.
pytestmark = pytest.mark.usefixtures('remove_stored_curves')


@pytest.fixture
def flat_pair():
    """Two curves 400 radiocarbon years apart, so mixing them has an
    arithmetic answer that can be checked by hand."""
    cal = np.arange(0, 2001, 10.0)
    a = CalibrationCurve(cal, cal * 0.9, np.full_like(cal, 10.0))
    b = CalibrationCurve(cal, cal * 0.9 + 400, np.full_like(cal, 20.0))
    return a, b


class TestTheMixingMath:

    def test_halfway_is_halfway(self, flat_pair):
        a, b = flat_pair
        mixed = curve_mix.mix(a, b, 50)
        expected = np.interp(mixed.cal_ages, a.cal_ages, a.c14_ages) + 200
        assert np.allclose(mixed.c14_ages, expected)

    def test_the_proportion_is_of_the_FIRST_curve_as_in_oxcal(self, flat_pair):
        """A recipe copied out of an .oxcal file has to mean the same thing
        here as it does there."""
        a, b = flat_pair
        mostly_a = curve_mix.mix(a, b, 75)
        at_1000 = float(np.interp(1000, mostly_a.cal_ages, mostly_a.c14_ages))
        assert at_1000 == pytest.approx(900 + 0.25 * 400)

    def test_all_of_one_curve_reproduces_that_curve(self, flat_pair):
        a, b = flat_pair
        for percent, source in ((100, a), (0, b)):
            mixed = curve_mix.mix(a, b, percent)
            expected = np.interp(mixed.cal_ages, source.cal_ages, source.c14_ages)
            assert np.allclose(mixed.c14_ages, expected)
            expected_sigma = np.interp(mixed.cal_ages, source.cal_ages, source.c14_errors)
            assert np.allclose(mixed.c14_errors, expected_sigma)

    def test_errors_combine_in_quadrature_with_their_weights(self, flat_pair):
        a, b = flat_pair
        mixed = curve_mix.mix(a, b, 50, percent_a_error=0)
        assert np.allclose(mixed.c14_errors, np.hypot(0.5 * 10, 0.5 * 20))


class TestTheUncertaintyOnTheProportion:

    def test_an_uncertain_diet_widens_the_curve(self, flat_pair):
        """The term that earns its keep: without it a mixed curve would be as
        confident as if the proportion were known exactly."""
        a, b = flat_pair
        certain = curve_mix.mix(a, b, 50, percent_a_error=0)
        uncertain = curve_mix.mix(a, b, 50, percent_a_error=10)
        assert np.all(uncertain.c14_errors > certain.c14_errors)
        # 10% of a 400-year separation is 40 years, in quadrature with the
        # weighted curve errors.
        assert np.allclose(
            uncertain.c14_errors,
            np.sqrt((0.5 * 10) ** 2 + (0.5 * 20) ** 2 + 40 ** 2),
        )

    def test_it_matters_most_where_the_curves_are_furthest_apart(self):
        """Being wrong about the proportion moves the answer furthest where
        the two curves disagree most, and the widening has to follow that."""
        cal = np.arange(0, 2001, 10.0)
        a = CalibrationCurve(cal, cal * 0.9, np.full_like(cal, 10.0))
        # A curve that starts alongside A and diverges steadily from it.
        b = CalibrationCurve(cal, cal * 0.9 + cal * 0.5, np.full_like(cal, 10.0))
        mixed = curve_mix.mix(a, b, 50, percent_a_error=10)
        near = float(np.interp(100, mixed.cal_ages, mixed.c14_errors))
        far = float(np.interp(1900, mixed.cal_ages, mixed.c14_errors))
        assert far > near * 3

    def test_a_proportion_outside_0_100_is_refused(self, flat_pair):
        a, b = flat_pair
        for bad in (-1, 101):
            with pytest.raises(ValueError):
                curve_mix.mix(a, b, bad)


class TestMixingRealCurves:

    def test_intcal20_with_marine20(self):
        intcal = curve_registry.resolve('intcal20')
        marine = curve_registry.resolve('marine20')
        mixed = curve_mix.mix(intcal, marine, 50, 10)

        # The union grid keeps IntCal20's finer sampling rather than dropping
        # to Marine20's 10-year steps.
        assert len(mixed.cal_ages) > len(marine.cal_ages)

        at = lambda curve, t: float(np.interp(t, curve.cal_ages, curve.c14_ages))
        for year in (1000, 3000, 5000):
            assert at(mixed, year) == pytest.approx(
                (at(intcal, year) + at(marine, year)) / 2, abs=1.0
            )

    def test_curves_that_do_not_overlap_are_refused(self):
        old = CalibrationCurve(np.arange(10000, 20000, 10.0),
                               np.arange(10000, 20000, 10.0), np.full(1000, 20.0))
        young = CalibrationCurve(np.arange(0, 1000, 10.0),
                                 np.arange(0, 1000, 10.0), np.full(100, 20.0))
        with pytest.raises(ValueError) as exc:
            curve_mix.mix(old, young, 50)
        assert 'do not overlap' in str(exc.value)


def make_mix(client, curve_id, percent=30, error=10, a='intcal20', b='marine20', **extra):
    body = {'curve_id': curve_id, 'a': a, 'b': b,
            'percent_a': percent, 'percent_a_error': error}
    body.update(extra)
    return client.post('/api/curves/mixed', json=body)


class TestTheEndpoint:

    def test_a_mixed_curve_can_be_calibrated_against(self, client):
        response = make_mix(client, 'mix-diet-30')
        assert response.status_code == 201, response.text
        spec = response.json()['data']
        assert spec['kind'] == 'mixed'
        assert spec['type'] == 'mixed'
        # It contains marine carbon, so a reservoir offset applies to it --
        # which is what sample_type decides in the date form.
        assert spec['sample_type'] == 'marine'
        assert '30%' in spec['description']

        result = client.post('/api/analysis/calibrate', json={
            'sample_name': 'Diet', 'age_bp': 3000, 'error_1sigma': 30,
            'material_type': 'bone', 'curve_id': 'mix-diet-30', 'save': False,
        })
        assert result.status_code == 200, result.text

    def test_it_dates_between_its_parents(self, client):
        """The whole point: a partly marine sample calibrates between what the
        two curves would each say on their own."""
        make_mix(client, 'mix-diet-50', percent=50, error=0)
        ranges = {}
        for curve in ('intcal20', 'marine20', 'mix-diet-50'):
            response = client.post('/api/analysis/calibrate', json={
                'sample_name': 'S', 'age_bp': 3000, 'error_1sigma': 30,
                'material_type': 'bone', 'curve_id': curve, 'save': False,
            })
            data = response.json()['data']
            result = data.get('result', data)
            ranges[curve] = result['posterior_mean'] or result['calibrated_age_bp']
        assert ranges['marine20'] < ranges['mix-diet-50'] < ranges['intcal20'] or \
               ranges['intcal20'] < ranges['mix-diet-50'] < ranges['marine20']

    def test_an_ingredient_cannot_be_deleted_while_the_mixture_exists(self, client):
        """A mixed curve is stored as its recipe, so its parents are in use --
        deleting one would leave a curve that cannot be rebuilt."""
        import json as _json
        upload = client.post(
            '/api/curves',
            files={'file': ('parent.14c', b'\n'.join(
                f'{c},{c * 0.95:g},20'.encode() for c in range(0, 2001, 10)
            ), 'text/plain')},
            data={'metadata': _json.dumps({'name': 'Mix Parent'}), 'curve_id': 'mix-parent'},
        )
        assert upload.status_code == 201, upload.text
        assert make_mix(client, 'mix-of-parent', a='mix-parent', b='intcal20').status_code == 201

        refusal = client.delete('/api/curves/mix-parent')
        assert refusal.status_code == 409
        assert refusal.json()['detail']['usage']['mixed_curve_ids'] == ['mix-of-parent']

    def test_the_same_mixture_under_a_second_name_is_refused(self, client):
        """Two curves with one recipe are the same numbers twice, and would
        make matching an .oxcal file's Mix_Curves to one of them arbitrary --
        which is exactly how this test's own duplicate recipes first went
        wrong."""
        assert make_mix(client, 'mix-first', percent=44, error=6).status_code == 201
        duplicate = make_mix(client, 'mix-second', percent=44, error=6)
        assert duplicate.status_code == 409
        assert 'mix-first' in duplicate.text

    def test_the_same_mixture_written_the_other_way_round_is_also_refused(self, client):
        assert make_mix(client, 'mix-fwd', percent=25, error=5).status_code == 201
        flipped = make_mix(client, 'mix-rev', a='marine20', b='intcal20',
                           percent=75, error=5)
        assert flipped.status_code == 409

    def test_a_curve_mixed_with_itself_is_refused(self, client):
        assert make_mix(client, 'mix-silly', a='intcal20', b='intcal20').status_code == 422

    def test_an_unknown_ingredient_is_refused(self, client):
        assert make_mix(client, 'mix-missing', a='intcal13').status_code == 422

    def test_the_percentage_is_bounded(self, client):
        assert make_mix(client, 'mix-toomuch', percent=140).status_code == 422


class TestOxcalMixCurves:
    """
    An .oxcal file that mixes curves used to fail the import outright. It now
    imports -- but only against a mixture this app already has, because
    parsing a file must not create curves. See _define_mixed_curve.
    """

    def test_a_matching_mixture_is_found_and_used(self, client):
        from app.services.oxcal_script import parse_oxcal
        assert make_mix(client, 'mix-oxcal-31', percent=31, error=11).status_code == 201

        script = (
            'Plot(){ Mix_Curves("diet","IntCal20","Marine20",31,11); '
            'Curve("=diet"); Phase("A"){ R_Date("a",3000,25); }; };'
        )
        result = parse_oxcal(script, 'diet.oxcal')
        assert result.ok, result.errors
        assert result.dates[0]['curve_id'] == 'mix-oxcal-31'
        assert any('Mix_Curves' in note for note in result.notes)

    def test_the_same_recipe_written_the_other_way_round_matches(self, client):
        """70% Marine20 is 30% IntCal20. A file writing it the other way is
        describing the same mixture, and refusing it would be pedantry."""
        from app.services.oxcal_script import parse_oxcal
        assert make_mix(
            client, 'mix-oxcal-flip', a='marine20', b='intcal20', percent=68, error=12
        ).status_code == 201

        script = (
            'Plot(){ Mix_Curves("d","IntCal20","Marine20",32,12); Curve("=d"); '
            'Phase("A"){ R_Date("a",3000,25); }; };'
        )
        result = parse_oxcal(script, 'flip.oxcal')
        assert result.ok, result.errors
        assert result.dates[0]['curve_id'] == 'mix-oxcal-flip'

    def test_a_mixture_this_app_does_not_have_fails_saying_what_to_create(self, client):
        from app.services.oxcal_script import parse_oxcal
        script = (
            'Plot(){ Mix_Curves("d","IntCal20","Marine20",42,7); Curve("=d"); '
            'Phase("A"){ R_Date("a",3000,25); }; };'
        )
        result = parse_oxcal(script, 'unknown-mix.oxcal')
        assert not result.ok
        message = ' '.join(result.errors)
        assert '42' in message and 'Curves page' in message

    def test_a_different_proportion_is_not_treated_as_the_same_mixture(self, client):
        """55% and 30% marine diet calibrate differently; matching one to the
        other would be the silent substitution this importer exists to
        avoid."""
        from app.services.oxcal_script import parse_oxcal
        assert make_mix(client, 'mix-oxcal-55', percent=55, error=10).status_code == 201
        result = parse_oxcal(
            'Plot(){ Mix_Curves("d","IntCal20","Marine20",30,10); Curve("=d"); '
            'Phase("A"){ R_Date("a",3000,25); }; };',
            'other.oxcal',
        )
        matched = [d['curve_id'] for d in result.dates] if result.ok else []
        assert 'mix-oxcal-55' not in matched
