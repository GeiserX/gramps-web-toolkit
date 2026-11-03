"""Julian and French Republican dates, and text-only dates written on purpose."""

import pytest

from gwt_apply.dates import (CAL_FRENCH, CAL_JULIAN, MOD_TEXTONLY, DateError, date_key, gregorian_sdn, parse_date)


@pytest.mark.parametrize("text, gregorian", [
    ("julian 1582-10-04", (1582, 10, 14)),  # the day before the first Gregorian day, 1582-10-15
    ("julian 1752-09-02", (1752, 9, 13)),   # Britain: the next day was 14 September
    ("julian 1700-02-29", (1700, 3, 11)),   # 1700 is a leap year in the Julian calendar only
    ("julian 1918-01-31", (1918, 2, 13)),   # Russia: the next day was 14 February
])
def test_julian_dates_sort_on_the_gregorian_day(text, gregorian):
    d = parse_date(text)
    assert d["calendar"] == CAL_JULIAN
    assert d["sortval"] == gregorian_sdn(*gregorian)


@pytest.mark.parametrize("text, gregorian", [
    ("french 1-01-01", (1792, 9, 22)),        # an I, 1 Vendemiaire
    ("french an XII-01-01", (1803, 9, 24)),   # an XII, 1 Vendemiaire
    ("french an VIII-02-18", (1799, 11, 9)),  # 18 Brumaire an VIII
    ("french an III-13-06", (1795, 9, 22)),   # the sixth extra day of a leap year
    ("french an xiv-04-10", (1805, 12, 31)),  # the day before the calendar was dropped
])
def test_french_republican_dates_sort_on_the_gregorian_day(text, gregorian):
    d = parse_date(text)
    assert d["calendar"] == CAL_FRENCH
    assert d["sortval"] == gregorian_sdn(*gregorian)


def test_calendar_dates_keep_their_own_values_and_modifiers():
    d = parse_date("French Est Abt an XII-03")
    assert (d["calendar"], d["quality"], d["modifier"], d["dateval"]) == (CAL_FRENCH, 1, 3, [0, 3, 12, False])
    d = parse_date("julian bet 1700 and 1701-03")
    assert d["dateval"] == [0, 0, 1700, False, 0, 3, 1701, False]
    assert parse_date("gregorian 1890-03-12") == parse_date("1890-03-12")


@pytest.mark.parametrize("bad", [
    "julian 1701-02-29",   # not a leap year in either calendar
    "french 4-13-06",      # an IV has five extra days
    "french 1-14-01",      # no 14th month
    "french 0-01-01",      # the calendar starts in an I
    "french an XQ-01-01",  # not a Roman numeral
])
def test_impossible_calendar_dates_are_refused(bad):
    with pytest.raises(DateError):
        parse_date(bad)


def test_text_only_dates_are_kept_only_when_asked():
    d = parse_date("text: the winter of the great flood")
    assert (d["modifier"], d["text"], d["sortval"], d["dateval"]) == (MOD_TEXTONLY, "the winter of the great flood",
                                                                      0, [0, 0, 0, False])
    for bad in ("the winter of the great flood", "text:", "text:   "):
        with pytest.raises(DateError):
            parse_date(bad)


def test_date_key_tells_calendars_and_texts_apart():
    assert date_key(parse_date("julian 1700-02-11")) != date_key(parse_date("1700-02-11"))
    assert date_key(parse_date("text: spring")) != date_key(parse_date("text: autumn"))
    assert date_key(parse_date("text: spring")) == date_key(parse_date("text:  spring"))
    assert date_key(parse_date("1890")) == date_key({"dateval": [0, 0, 1890, False], "modifier": 0, "quality": 0})
    assert date_key(None) == date_key(parse_date(""))
