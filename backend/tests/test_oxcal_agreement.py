"""
Hold the app's calibration against a real OxCal 4.4 run (tests/fixtures/oxcal:
Nenthorn, SHCal20, default settings, no outlier model).

Only the UNMODELLED path is tested here, and deliberately so: it needs no
MCMC, so it runs in milliseconds and can be asserted tightly. It is also the
foundation everything else stands on -- curve loading, interpolation, the
1/sigma normalisation, trapezoidal grid weighting and the multi-range HPD
search all have to be right before a modelled range can mean anything.

The modelled comparison needs a real MCMC run and lives in
tests/manual/verify_model.py (`nenthorn`), together with what it currently
shows.

Every one of these eight dates is bimodal on SHCal20 -- the wiggle around
AD 1350 splits each likelihood into roughly AD 1300-1360 and AD 1380-1410 --
so this fixture exercises the multi-range machinery rather than just a
single-peak case.
"""
import numpy as np
import pytest

from app.services.calibration import get_curve

import oxcal_fixture as ox


@pytest.fixture(scope='module')
def curve():
    c = get_curve('shcal20')
    assert len(c.cal_ages) > 0, 'SHCal20 curve data not found'
    return c


@pytest.fixture(scope='module')
def oxcal():
    return ox.distributions(), ox.table_ranges()


def _calibrated(curve, m):
    return curve.calibrate(m['age_bp'], m['error_1sigma'])


@pytest.mark.parametrize('sample', ox.measurements(), ids=lambda m: m['sample_name'])
class TestAgainstOxCalLikelihoods:
    """Per-date, against OxCal's own exported likelihood distribution."""

    def test_distribution_matches(self, curve, oxcal, sample):
        dists, _table = oxcal
        ox_cal, ox_prob = dists[sample['id']]['likelihood']
        cal, prob = _calibrated(curve, sample)
        # 5-year export bins and a different interpolation of the same curve
        # keep this short of 1.0; well below ~0.95 would mean a real
        # disagreement about the calibration itself.
        assert ox.overlap(cal, prob, ox_cal, ox_prob) > 0.95

    @pytest.mark.parametrize('conf,level', [(68, 0.683), (95, 0.954)])
    def test_reported_range_matches(self, curve, oxcal, sample, conf, level):
        """
        Against OxCal's summary table, which reports the ENVELOPE of the HPD
        region rather than one row per lobe -- so compare envelope to
        envelope. 5 years is a little over OxCal's own export resolution.
        """
        _dists, table = oxcal
        expected = table[sample['id']]['unmodelled'][conf]
        assert expected, f'fixture has no unmodelled {conf}% range'
        want_from, want_to = expected[0]

        cal, prob = _calibrated(curve, sample)
        ranges = [(r['min'], r['max']) for r in curve.get_hpd_ranges(cal, prob, level)]
        got_from, got_to = ox.as_ad([ox.envelope(ranges)])[0]

        assert got_from == pytest.approx(want_from, abs=5.0)
        assert got_to == pytest.approx(want_to, abs=5.0)

    def test_finds_the_same_number_of_lobes(self, curve, oxcal, sample):
        """
        The bimodality is the point: the app and OxCal must agree on how many
        separate ranges a date resolves into, or one is merging lobes the
        other splits.

        Counted over ranges carrying at least 1% of the probability. The app
        works on the calibration curve's own grid, which is finer than
        OxCal's 5-year export bins near AD 1300, so it can resolve a narrow
        dip OxCal smooths over and split off a sliver -- see
        test_sub_resolution_slivers_are_reported below, which pins that
        behaviour rather than hiding it.
        """
        dists, _table = oxcal
        ox_cal, ox_prob = dists[sample['id']]['likelihood']
        cal, prob = _calibrated(curve, sample)

        theirs = ox.hpd_from_distribution(ox_cal, ox_prob, 0.954)
        mine = [r for r in curve.get_hpd_ranges(cal, prob, 0.954)
                if r['probability_percent'] >= 1.0]
        assert len(mine) == len(theirs)


def test_sub_resolution_slivers_are_reported():
    """
    Two of these dates come back with a THIRD HPD range carrying under 1% of
    the probability, one of them zero years wide -- Wk-50100's 95.4% region is
    reported as AD 1304-1304, 1306-1360, 1381-1402.

    This is not a calibration error: the app searches the curve's own grid,
    which is finer than OxCal's 5-year bins, so it resolves a one-point dip
    that OxCal smooths across. But a zero-width range is not meaningful
    output, and OxCal reports neither of them. Pinned here so the behaviour
    is a known, deliberate state rather than a surprise, and so that
    suppressing slivers below some probability or width shows up as a
    deliberate change to this test.
    """
    curve = get_curve('shcal20')
    found = {}
    for m in ox.measurements():
        cal, prob = curve.calibrate(m['age_bp'], m['error_1sigma'])
        slivers = [r for r in curve.get_hpd_ranges(cal, prob, 0.954)
                   if r['probability_percent'] < 1.0]
        if slivers:
            found[m['sample_name']] = [(r['max'] - r['min'], r['probability_percent'])
                                       for r in slivers]

    assert sorted(found) == ['ANU-55206', 'Wk-50100']
    assert found['Wk-50100'][0][0] == 0.0, 'the zero-width range is the point'
    for slivers in found.values():
        for width, share in slivers:
            assert width <= 5.0 and share < 1.0


class TestFixtureIntegrity:
    """
    Guards against the fixture being read wrongly rather than against the
    app -- if these fail, the comparisons above are measuring nothing.
    """

    def test_all_dates_present(self, oxcal):
        dists, table = oxcal
        for m in ox.measurements():
            assert 'likelihood' in dists[m['id']]
            assert 'posterior' in dists[m['id']]
            assert table[m['id']]['op'] == 'R_Date'
        assert len(ox.measurements()) == 8

    def test_boundaries_present_and_posterior_only(self, oxcal):
        """A Boundary has no likelihood of its own -- it is pure prior."""
        dists, table = oxcal
        for name in ('STARTNENTHORN', 'ENDNENTHORN'):
            assert 'posterior' in dists[name]
            assert 'likelihood' not in dists[name]
            assert table[name]['op'] == 'Boundary'
            assert not table[name]['unmodelled'][95]

    # WK50099 and ANU55209 sit right on the 68.3% cut: their late lobe (around
    # AD 1380-1395) is just barely inside it by our HPD search and just
    # outside it by OxCal's, so our envelope runs to 1393 where the table
    # stops at 1356. The older edge still agrees (1323 vs 1322), and both
    # agree at 95.4%, so this is a tie at the threshold rather than a
    # disagreement about the distribution. Listed rather than tolerated
    # generally, so a third case appearing would fail.
    BORDERLINE_68 = {('WK50099', 'modelled'), ('ANU55209', 'modelled')}

    def test_table_ranges_reproduce_the_raw_export(self, oxcal):
        """
        OxCal's summary table and its raw distributions have to agree with
        each other through our own HPD code, or our reading of one of them is
        wrong. This is what established that the table reports envelopes.
        """
        dists, table = oxcal
        checked = 0
        for name, kinds in dists.items():
            for kind, which in (('likelihood', 'unmodelled'), ('posterior', 'modelled')):
                if kind not in kinds:
                    continue
                calbp, prob = kinds[kind]
                for conf, level in ((68, 0.683), (95, 0.954)):
                    expected = table[name][which][conf]
                    if not expected:
                        continue
                    if conf == 68 and (name, which) in self.BORDERLINE_68:
                        continue
                    want_from, want_to = expected[0]
                    got = ox.as_ad([ox.envelope(ox.hpd_from_distribution(calbp, prob, level))])[0]
                    assert got[0] == pytest.approx(want_from, abs=3.0), (name, kind, conf)
                    assert got[1] == pytest.approx(want_to, abs=3.0), (name, kind, conf)
                    checked += 1
        assert checked == 34, 'fewer comparisons than expected -- fixture changed?'

    @pytest.mark.parametrize('name', sorted(n for n, _ in BORDERLINE_68))
    def test_borderline_68_cases_still_agree_at_95(self, oxcal, name):
        """The threshold tie above must not be hiding a real disagreement."""
        dists, table = oxcal
        calbp, prob = dists[name]['posterior']
        want_from, want_to = table[name]['modelled'][95][0]
        got = ox.as_ad([ox.envelope(ox.hpd_from_distribution(calbp, prob, 0.954))])[0]
        assert got[0] == pytest.approx(want_from, abs=3.0)
        assert got[1] == pytest.approx(want_to, abs=3.0)
        # ...and the older edge of the 68.3% range agrees even there.
        assert ox.as_ad([ox.envelope(ox.hpd_from_distribution(calbp, prob, 0.683))])[0][0] == \
            pytest.approx(table[name]['modelled'][68][0][0], abs=3.0)

    def test_model_tree_matches_the_oxcal_file(self):
        """
        The tree in oxcal_fixture mirrors Nenthorn_sites_3.oxcal by hand, so
        check it against the file: same element names, same nesting order,
        and every date assigned to the phase the file puts it in.
        """
        from app.services.model_tree import iter_elements, validate_tree

        text = open(ox.MODEL, encoding='utf-8').read()
        root = ox.model_tree()
        validate_tree(root, ox.measurements())

        names = [e['name'] for e in iter_elements(root)]
        assert names == ['Nenthorn', 'Start Nenthorn', 'Quarry', 'Fire_Feature_J',
                         'Rockshelter', 'Fires', 'Fire_Feature_K', 'Fire_Feature_A',
                         'End Nenthorn']
        for name in names:
            assert f'"{name}"' in text, f'{name} is not in the .oxcal file'
        # Order in the file, which is what makes Quarry older than Rockshelter.
        assert text.index('"Quarry"') < text.index('"Rockshelter"')
        for date_id in ox.FIRE_FEATURE_J:
            assert text.index('"Fire_Feature_J"') < text.index(f'"{date_id}"')
