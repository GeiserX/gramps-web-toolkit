"""Turn the change list's date strings into Gramps Date dicts.

Grammar (case-insensitive):

    DATE   := [CALENDAR " "] [QUALITY " "] [MOD " "] YMD
            | [QUALITY " "] ("bet" | "between") YMD "and" YMD
            | [QUALITY " "] "from" YMD "to" YMD
    CALENDAR := "fr" | "republican"   (French Republican calendar; YMD is then year-month-day
                of that calendar: "fr 0005-03-26" is 26 frimaire an V; year 1 to 14, month 1 to 12,
                13 for the complementary days)
    QUALITY:= "est" | "estimated" | "calc" | "calculated"
    MOD    := "abt" | "about" | "bef" | "before" | "aft" | "after"
    YMD    := YYYY | YYYY-MM | YYYY-MM-DD

Anything else is refused: a date the tool cannot read is never stored as text.
"""

from __future__ import annotations

import re

MOD_NONE, MOD_BEFORE, MOD_AFTER, MOD_ABOUT, MOD_RANGE, MOD_SPAN = 0, 1, 2, 3, 4, 5
QUAL_NONE, QUAL_ESTIMATED, QUAL_CALCULATED = 0, 1, 2

_QUALITY = {
    "est": QUAL_ESTIMATED,
    "estimated": QUAL_ESTIMATED,
    "calc": QUAL_CALCULATED,
    "calculated": QUAL_CALCULATED,
}
_MOD = {
    "abt": MOD_ABOUT,
    "about": MOD_ABOUT,
    "bef": MOD_BEFORE,
    "before": MOD_BEFORE,
    "aft": MOD_AFTER,
    "after": MOD_AFTER,
}
_YMD = re.compile(r"^(\d{1,4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?$")


class DateError(ValueError):
    pass


CAL_GREGORIAN, CAL_FRENCH = 0, 3
_CALENDAR = {"fr": CAL_FRENCH, "republican": CAL_FRENCH}
# Years of the Republican calendar with six complementary days (Gramps counts a leap year every fourth year).
_FR_LEAP = (3, 7, 11)


def french_sdn(year: int, month: int, day: int) -> int:
    """Serial day number of a Republican date, the same value Gramps stores as `sortval`."""
    return (year * 1461) // 4 + (month - 1) * 30 + day + 2375474


def _ymd(text: str, calendar: int = CAL_GREGORIAN) -> tuple[int, int, int]:
    m = _YMD.match(text)
    if not m:
        raise DateError(f"unreadable date part {text!r}")
    year = int(m.group(1))
    month = int(m.group(2) or 0)
    day = int(m.group(3) or 0)
    if calendar == CAL_FRENCH:
        if not 1 <= year <= 14:
            raise DateError(f"Republican year {year} is outside 1 to 14 in {text!r}")
        if not 0 <= month <= 13 or not 0 <= day <= 30 or (day and not month):
            raise DateError(f"impossible date {text!r}")
        if month == 13 and day > (6 if year in _FR_LEAP else 5):
            raise DateError(f"impossible date {text!r}: that year has no complementary day {day}")
        return day, month, year
    if not 0 <= month <= 12 or not 0 <= day <= 31 or (day and not month):
        raise DateError(f"impossible date {text!r}")
    if day and year:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        if day > (31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month - 1]:
            raise DateError(f"impossible date {text!r}: that month has no day {day}")
    return day, month, year


def gregorian_sdn(year: int, month: int, day: int) -> int:
    """Serial day number, the same value Gramps stores as `sortval`."""
    year += 4801 if year < 0 else 4800
    if month > 2:
        month -= 3
    else:
        month += 9
        year -= 1
    return (
        ((year // 100) * 146097) // 4
        + ((year % 100) * 1461) // 4
        + (month * 153 + 2) // 5
        + day
        - 32045
    )


def empty_date() -> dict:
    return {
        "_class": "Date",
        "calendar": 0,
        "modifier": MOD_NONE,
        "quality": QUAL_NONE,
        "dateval": [0, 0, 0, False],
        "text": "",
        "sortval": 0,
        "newyear": 0,
    }


def parse_date(text: str | None) -> dict:
    """Return a Gramps Date dict for `text`; None or "" gives the empty date."""
    if text is None or not str(text).strip():
        return empty_date()
    words = str(text).strip().lower().split()
    calendar = CAL_GREGORIAN
    if words and words[0] in _CALENDAR:
        calendar = _CALENDAR[words.pop(0)]
    quality = QUAL_NONE
    if words and words[0] in _QUALITY:
        quality = _QUALITY[words.pop(0)]
    modifier = MOD_NONE
    second = None
    if len(words) == 4 and words[0] in ("bet", "between") and words[2] == "and":
        modifier, first, second = MOD_RANGE, words[1], words[3]
    elif len(words) == 4 and words[0] == "from" and words[2] == "to":
        modifier, first, second = MOD_SPAN, words[1], words[3]
    elif len(words) == 2 and words[0] in _MOD:
        modifier, first = _MOD[words[0]], words[1]
    elif len(words) == 1:
        first = words[0]
    else:
        raise DateError(f"unreadable date {text!r}")
    day, month, year = _ymd(first, calendar)
    dateval: list = [day, month, year, False]
    if second is not None:
        dateval += [*_ymd(second, calendar), False]
    date = empty_date()
    to_sdn = french_sdn if calendar == CAL_FRENCH else gregorian_sdn
    date.update(
        calendar=calendar,
        modifier=modifier,
        quality=quality,
        dateval=dateval,
        sortval=to_sdn(max(year, 1), max(month, 1), max(day, 1)),
    )
    return date


def date_key(date: dict | None) -> tuple:
    """What two dates must share to count as the same date."""
    if not date:
        return (MOD_NONE, QUAL_NONE, (0, 0, 0))
    calendar = date.get("calendar", 0)
    val = date.get("dateval") or [0, 0, 0, False]
    parts = tuple(v for i, v in enumerate(val) if i % 4 != 3)
    if all(v == 0 for v in parts):
        parts = (0, 0, 0)
    if calendar:
        return (date.get("modifier", 0), date.get("quality", 0), parts, calendar)
    return (date.get("modifier", 0), date.get("quality", 0), parts)
