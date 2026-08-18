"""
The curve registry (services/curve_registry.py) and the endpoints over it
(api/curves.py).

Neither had a single test before this file, which is most of the reason the
thing being tested here was wrong for so long: api/curves.py served a
hand-written metadata table claiming 5,141 points for IntCal20 and SHCal20 and
1,341 for Marine20, when the curves actually loaded hold 9,501, 9,501 and
5,501. Nothing called those endpoints and nothing checked them, so the numbers
were free to drift from the files.

So the load-bearing tests here are the ones that hold the served metadata
against the loaded curve rather than against a second constant. A test that
asserted "IntCal20 has 9,501 points" and nothing else would be the same
mistake one number later: what makes the count right is that it is counted.
"""
import pytest

from app.services import curve_registry
from app.services.calibration import get_curve


class TestRegistryMeasuresRatherThanAsserts:

    def test_point_count_is_the_loaded_curve_s(self):
        for spec in curve_registry.list_specs():
            curve = get_curve(spec.id)
            assert spec.data_points == len(curve.cal_ages)

    def test_ranges_are_the_loaded_curve_s(self):
        for spec in curve_registry.list_specs():
            curve = get_curve(spec.id)
            assert spec.cal_min == pytest.approx(float(curve.cal_ages.min()))
            assert spec.cal_max == pytest.approx(float(curve.cal_ages.max()))
            assert spec.c14_min == pytest.approx(float(curve.c14_ages.min()))
            assert spec.c14_max == pytest.approx(float(curve.c14_ages.max()))

    def test_the_counts_the_api_used_to_claim_are_gone(self):
        """
        The specific regression: the old table's numbers were not merely
        stale, they were of the wrong order for Marine20. Naming them keeps
        this from quietly coming back if someone reintroduces a constant.
        """
        counts = {spec.id: spec.data_points for spec in curve_registry.list_specs()}
        assert counts['intcal20'] == 9501 and counts['intcal20'] != 5141
        assert counts['shcal20'] == 9501 and counts['shcal20'] != 5141
        assert counts['marine20'] == 5501 and counts['marine20'] != 1341

    def test_step_summary_describes_the_real_grid(self):
        """
        IntCal20 changes calendar step several times across its range; the
        summary should say so, since that spacing is the first thing the
        curve's own resolution depends on. Marine20 is a single 10-year step
        throughout.
        """
        intcal = curve_registry.get_spec('intcal20').resolution
        assert '1 yr' in intcal and '20 yr' in intcal
        assert curve_registry.get_spec('marine20').resolution == '10 yr (0-55,000)'

    def test_content_hash_identifies_the_curve(self):
        specs = {s.id: s.content_sha256 for s in curve_registry.list_specs()}
        assert len(set(specs.values())) == len(specs)
        # Stable across calls -- this is what a stored analysis will be
        # compared against on rerun.
        assert curve_registry.get_spec('intcal20').content_sha256 == specs['intcal20']


class TestEverythingListedCanActuallyBeUsed:

    def test_every_listed_curve_resolves(self):
        for spec in curve_registry.list_specs():
            curve = curve_registry.resolve(spec.id)
            assert len(curve.cal_ages) > 0

    def test_every_listed_curve_is_accepted_by_the_request_schema(self):
        """
        The list offered and the list validated have to be the same list --
        a curve you can select but not submit is exactly the drift this
        registry exists to prevent.
        """
        from app.schemas.sample import VALID_CURVE_IDS
        assert set(curve_registry.curve_ids()) <= set(VALID_CURVE_IDS)

    def test_unknown_curve_raises_rather_than_defaulting(self):
        with pytest.raises(ValueError) as exc:
            curve_registry.resolve('intcal2020')
        assert 'intcal2020' in str(exc.value)

    def test_calibration_still_reaches_the_same_curve(self):
        """get_curve() delegates here now; it must resolve identically."""
        assert get_curve('shcal20') is curve_registry.resolve('shcal20')

    def test_builtin_curves_do_not_need_a_database(self, monkeypatch):
        """
        The curves ship with the app, so calibrating against one must not
        start depending on the database just because imported curves also
        live there. A script, or a database that has not been initialised,
        still gets IntCal20.
        """
        from sqlalchemy.exc import OperationalError

        def unreachable(*args, **kwargs):
            raise OperationalError('SELECT 1', {}, Exception('no such table'))

        curve_registry.invalidate()
        monkeypatch.setattr(curve_registry, 'SessionLocal', unreachable)
        try:
            assert len(curve_registry.resolve('intcal20').cal_ages) == 9501
            assert [s.id for s in curve_registry.list_specs()] == list(
                curve_registry.BUILTIN_CURVE_IDS
            )
            assert curve_registry.resolve_alias('SHCal20') == 'shcal20'
        finally:
            monkeypatch.undo()
            curve_registry.invalidate()


class TestAliases:
    """
    One name map, used by both file importers. Returning None for an unknown
    name is the point: date_import and oxcal_script both turn that into an
    error naming the curve, never a substitution.
    """

    @pytest.mark.parametrize('written,expected', [
        ('IntCal20', 'intcal20'),
        ('intcal', 'intcal20'),
        ('IntCal20.14c', 'intcal20'),
        ('=SHCal20', 'shcal20'),
        ('SHCal 20', 'shcal20'),
        ('marine', 'marine20'),
        ('MARINE20', 'marine20'),
    ])
    def test_known_spellings(self, written, expected):
        assert curve_registry.resolve_alias(written) == expected

    @pytest.mark.parametrize('written', ['intcal13', 'marine2020', '', 'Curve'])
    def test_unknown_names_are_not_guessed(self, written):
        assert curve_registry.resolve_alias(written) is None

    def test_oxcal_and_csv_importers_use_it(self):
        from app.services.date_import import _curve
        from app.services.oxcal_script import _curve_id

        assert _curve_id('=IntCal20.14c') == 'intcal20'
        assert _curve_id('IntCal13') is None

        errors = []
        assert _curve('SHCal20', 1, errors) == 'shcal20'
        assert errors == []
        assert _curve('IntCal13', 2, errors) is None
        assert 'IntCal13' in errors[0]


class TestEndpoints:

    def test_list_returns_every_curve(self, client):
        response = client.get('/api/curves')
        assert response.status_code == 200
        body = response.json()
        items = body['data']['items']
        assert body['data']['total'] == len(curve_registry.list_specs())
        assert [i['id'] for i in items] == list(curve_registry.curve_ids())

    def test_list_default_limit_does_not_truncate(self, client):
        """
        The previous default was 10 -- fine for three curves, silently wrong
        for a picker once imported curves exist.
        """
        items = client.get('/api/curves').json()['data']['items']
        assert len(items) == len(curve_registry.list_specs())

    def test_list_carries_the_fields_a_picker_needs(self, client):
        item = client.get('/api/curves').json()['data']['items'][0]
        for field in ('id', 'name', 'type', 'sample_type', 'description',
                      'reference', 'data_points', 'cal_min', 'cal_max',
                      'resolution', 'content_sha256'):
            assert field in item

    def test_marine_curve_is_marked_for_marine_samples(self, client):
        """
        The frontend switches curve on sample type from this field; getting
        it wrong would offer a terrestrial curve for marine carbon, which is
        wrong by centuries.
        """
        items = client.get('/api/curves').json()['data']['items']
        by_id = {i['id']: i for i in items}
        assert by_id['marine20']['sample_type'] == 'marine'
        assert by_id['intcal20']['sample_type'] == 'terrestrial'
        assert by_id['shcal20']['sample_type'] == 'terrestrial'
        # First terrestrial curve listed is what selecting "Terrestrial"
        # switches to -- previously hardcoded as IntCal20 in the frontend.
        terrestrial = [i['id'] for i in items if i['sample_type'] == 'terrestrial']
        assert terrestrial[0] == 'intcal20'

    def test_detail(self, client):
        body = client.get('/api/curves/marine20').json()
        assert body['success'] is True
        assert body['data']['name'] == 'Marine20'
        assert body['data']['data_points'] == 5501

    def test_detail_unknown_is_404(self, client):
        response = client.get('/api/curves/intcal13')
        assert response.status_code == 404
        assert 'intcal13' in response.json()['detail']

    def test_by_type(self, client):
        marine = client.get('/api/curves/type/marine').json()['data']
        assert [c['id'] for c in marine] == ['marine20']

    def test_by_unknown_type_is_an_empty_list_not_an_error(self, client):
        response = client.get('/api/curves/type/lacustrine')
        assert response.status_code == 200
        assert response.json()['data'] == []
