"""
Tests for services/date_format.py -- the cal BP to BC/AD conversion the CSV
export uses to report in the format the results page is showing.

This module is a mirror of frontend/src/utils/dateFormat.ts, so the values
below are the contract BETWEEN the two. They were checked against the
frontend implementation directly (importing dateFormat.ts in the browser
and comparing output for the same inputs); if either side changes, these
should be re-checked against the other rather than simply updated to match
whichever moved.

Only the range separator differs by design: a plain hyphen here against the
en-dash used on screen, because the CSV's other range columns already use a
hyphen and ASCII survives a spreadsheet opening the file in the wrong
encoding.
"""
import pytest

from app.services.date_format import (
    CAL_BP,
    BC_AD,
    BCE_CE,
    DEFAULT_DATE_FORMAT,
    format_age,
    format_hpd_ranges,
    format_range,
    normalise,
)


# (cal BP, BC/AD, BCE/CE, cal BP rendering) -- identical to what
# dateFormat.ts produces for the same inputs.
AGES = [
    (5000, "3051 BC", "3051 BCE", "5000 cal BP"),
    (3197.1, "1248 BC", "1248 BCE", "3197 cal BP"),
    (2000, "51 BC", "51 BCE", "2000 cal BP"),
    # Either side of the era boundary: there is no year zero, so 1 BC is
    # immediately followed by AD 1.
    (1951, "2 BC", "2 BCE", "1951 cal BP"),
    (1950, "1 BC", "1 BCE", "1950 cal BP"),
    (1949, "AD 1", "1 CE", "1949 cal BP"),
    (1000, "AD 950", "950 CE", "1000 cal BP"),
    (0, "AD 1950", "1950 CE", "0 cal BP"),
    # Negative cal BP is a date after 1950 -- possible for a very young
    # sample, and it must not fall over.
    (-50, "AD 2000", "2000 CE", "-50 cal BP"),
]


class TestFormatAge:
    @pytest.mark.parametrize("cal_bp,bcad,bcece,calbp", AGES)
    def test_matches_the_frontend(self, cal_bp, bcad, bcece, calbp):
        assert format_age(cal_bp, BC_AD) == bcad
        assert format_age(cal_bp, BCE_CE) == bcece
        assert format_age(cal_bp, CAL_BP) == calbp

    def test_there_is_no_year_zero(self):
        """The one rule that is easy to get quietly wrong."""
        assert format_age(1950, BC_AD) == "1 BC"
        assert format_age(1949, BC_AD) == "AD 1"
        assert "AD 0" not in {format_age(v, BC_AD) for v in range(1940, 1961)}
        assert "0 BC" not in {format_age(v, BC_AD) for v in range(1940, 1961)}

    def test_no_age_is_blank_not_an_error(self):
        # Structural rows (a phase, a sequence) have no age of their own.
        assert format_age(None, BC_AD) == ""


class TestFormatRange:
    def test_reads_oldest_first_whichever_way_it_is_given(self):
        """
        A larger cal BP is further in the past, so oldest-first is
        descending cal BP -- and the caller shouldn't have to care which
        order it passes them in.
        """
        assert format_range(2000, 1900, BC_AD) == "51 BC - AD 50"
        assert format_range(1900, 2000, BC_AD) == "51 BC - AD 50"

    def test_a_range_may_span_the_era_boundary(self):
        assert format_range(1980, 1900, BC_AD) == "31 BC - AD 50"

    def test_cal_bp_keeps_the_bare_numbers(self):
        assert format_range(2000, 1900, CAL_BP) == "2000-1900 cal BP"

    def test_a_missing_bound_is_blank(self):
        assert format_range(None, 1900, BC_AD) == ""
        assert format_range(2000, None, BC_AD) == ""


class TestFormatHpdRanges:
    def test_each_range_keeps_its_own_probability_share(self):
        """
        A multimodal HPD region is several disjoint ranges; collapsing them
        would claim probability in the gap between the peaks.
        """
        ranges = [
            {"min": 1900, "max": 2000, "probability_percent": 77.6},
            {"min": 1850, "max": 1870, "probability_percent": 17.8},
        ]
        assert format_hpd_ranges(ranges, BC_AD) == (
            "51 BC - AD 50 (77.6%); AD 80 - AD 100 (17.8%)"
        )

    def test_no_ranges_is_blank(self):
        assert format_hpd_ranges(None, BC_AD) == ""
        assert format_hpd_ranges([], BC_AD) == ""


class TestNormalise:
    def test_an_unknown_format_falls_back_rather_than_failing(self):
        """
        The reporting format is a presentation choice; refusing to produce
        the download over a bad query string would be a poor trade.
        """
        for bad in ("nonsense", "", None, "CALBP"):
            assert normalise(bad) == DEFAULT_DATE_FORMAT
        for good in (CAL_BP, BC_AD, BCE_CE):
            assert normalise(good) == good
