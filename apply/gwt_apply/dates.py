"""Turn the change list's date strings into Gramps Date dicts.

Grammar (case-insensitive):

    DATE   := [CAL " "] [QUALITY " "] [MOD " "] YMD
            | [CAL " "] [QUALITY " "] ("bet" | "between") YMD "and" YMD
            | [CAL " "] [QUALITY " "] "from" YMD "to" YMD
            | "text:" ANY TEXT
    CAL    := "gregorian" | "julian" | "french"
    QUALITY:= "est" | "estimated" | "calc" | "calculated"
    MOD    := "abt" | "about" | "bef" | "before" | "aft" | "after"
    YMD    := YYYY | YYYY-MM | YYYY-MM-DD

A Julian or French Republican date keeps its own day, month and year, as Gramps stores them; its sortval is the
Gregorian day it fell on. A French year may be written in Roman numerals after "an" ("french an XII-01-01"); its
months are 1-12 and 13 for the extra days. "text: ..." is a date Gramps keeps as text only, never sorted: the
writer says so on purpose. Anything else is refused: a date the tool cannot read is never stored as text by
accident.
"""

from __future__ import annotations

import re

MOD_NONE, MOD_BEFORE, MOD_AFTER, MOD_ABOUT, MOD_RANGE, MOD_SPAN, MOD_TEXTONLY = 0, 1, 2, 3, 4, 5, 6
QUAL_NONE, QUAL_ESTIMATED, QUAL_CALCULATED = 0, 1, 2
CAL_GREGORIAN, CAL_JULIAN, CAL_FRENCH = 0, 1, 3  # Gramps' own calendar numbers
_CALENDAR = {"gregorian": CAL_GREGORIAN, "julian": CAL_JULIAN, "french": CAL_FRENCH}
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
_AN = re.compile(r"\ban ([ivxlc]+)(?=-|$|\s)")

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


def _roman(word: str) -> int:
    values = [_ROMAN[c] for c in word]
    total = sum(-v if i + 1 < len(values) and v < values[i + 1] else v for i, v in enumerate(values))
    if total < 1:
        raise DateError(f"unreadable Roman year {word!r}")
    return total


def _ymd(text: str, calendar: int = CAL_GREGORIAN) -> tuple[int, int, int]:
    m = _YMD.match(text)
    if not m:
        raise DateError(f"unreadable date part {text!r}")
    year = int(m.group(1))
    month = int(m.group(2) or 0)
    day = int(m.group(3) or 0)
    months = 13 if calendar == CAL_FRENCH else 12
    if not 0 <= month <= months or not 0 <= day <= 31 or (day and not month):
        raise DateError(f"impossible date {text!r}")
    if calendar == CAL_FRENCH and month and not year:
        raise DateError(f"impossible date {text!r}: the French Republican calendar starts in year 1")
    if day and year:
        if calendar == CAL_FRENCH:
            # 30 days a month; the 13th has 5 extra days, 6 in years 3, 7, 11 (as Gramps counts them)
            last = 30 if month < 13 else (6 if (year + 1) % 4 == 0 else 5)
        else:
            leap = year % 4 == 0 and (calendar == CAL_JULIAN or year % 100 != 0 or year % 400 == 0)
            last = (31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month - 1]
        if day > last:
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


def julian_sdn(year: int, month: int, day: int) -> int:
    """Serial day number of a day in the Julian calendar."""
    year += 4801 if year < 0 else 4800
    if month > 2:
        month -= 3
    else:
        month += 9
        year -= 1
    return (year * 1461) // 4 + (month * 153 + 2) // 5 + day - 32083


def french_sdn(year: int, month: int, day: int) -> int:
    """Serial day number of a day in the French Republican calendar, as Gramps computes it: an I 1 Vendemiaire
    is 1792-09-22."""
    return (year * 1461) // 4 + (month - 1) * 30 + day + 2375474


_SDN = {CAL_GREGORIAN: gregorian_sdn, CAL_JULIAN: julian_sdn, CAL_FRENCH: french_sdn}


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
    raw = str(text).strip()
    if raw[:5].lower() == "text:":
        if not raw[5:].strip():
            raise DateError(f"a text-only date needs its text: {text!r}")
        date = empty_date()
        date.update(modifier=MOD_TEXTONLY, text=raw[5:].strip())
        return date
    words = raw.lower().split()
    calendar = CAL_GREGORIAN
    if words and words[0] in _CALENDAR:
        calendar = _CALENDAR[words.pop(0)]
        if calendar == CAL_FRENCH:  # "an XII-01-01": the year in Roman numerals
            words = _AN.sub(lambda m: str(_roman(m.group(1))), " ".join(words)).split()
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
    date.update(
        calendar=calendar,
        modifier=modifier,
        quality=quality,
        dateval=dateval,
        sortval=_SDN[calendar](max(year, 1), max(month, 1), max(day, 1)),
    )
    return date


def date_key(date: dict | None) -> tuple:
    """What two dates must share to count as the same date: modifier, quality, values, then the calendar, and the
    text of a text-only date. 1700-02-11 Julian and 1700-02-11 Gregorian are eleven days apart."""
    if not date:
        return (MOD_NONE, QUAL_NONE, (0, 0, 0), CAL_GREGORIAN, "")
    val = date.get("dateval") or [0, 0, 0, False]
    parts = tuple(v for i, v in enumerate(val) if i % 4 != 3)
    if all(v == 0 for v in parts):
        parts = (0, 0, 0)
    text = (date.get("text") or "") if date.get("modifier") == MOD_TEXTONLY else ""
    return (date.get("modifier", 0), date.get("quality", 0), parts, date.get("calendar") or CAL_GREGORIAN, text)
