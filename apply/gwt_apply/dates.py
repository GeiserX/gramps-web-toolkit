"""Turn the change list's date strings into Gramps Date dicts.

Grammar (case-insensitive):

    DATE   := [QUALITY " "] [MOD " "] YMD
            | [QUALITY " "] ("bet" | "between") YMD "and" YMD
            | [QUALITY " "] "from" YMD "to" YMD
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


def _ymd(text: str) -> tuple[int, int, int]:
    m = _YMD.match(text)
    if not m:
        raise DateError(f"unreadable date part {text!r}")
    year = int(m.group(1))
    month = int(m.group(2) or 0)
    day = int(m.group(3) or 0)
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
    day, month, year = _ymd(first)
    dateval: list = [day, month, year, False]
    if second is not None:
        dateval += [*_ymd(second), False]
    date = empty_date()
    date.update(
        modifier=modifier,
        quality=quality,
        dateval=dateval,
        sortval=gregorian_sdn(max(year, 1), max(month, 1), max(day, 1)),
    )
    return date


def date_key(date: dict | None) -> tuple:
    """What two dates must share to count as the same date."""
    if not date:
        return (MOD_NONE, QUAL_NONE, (0, 0, 0))
    val = date.get("dateval") or [0, 0, 0, False]
    parts = tuple(v for i, v in enumerate(val) if i % 4 != 3)
    if all(v == 0 for v in parts):
        parts = (0, 0, 0)
    return (date.get("modifier", 0), date.get("quality", 0), parts)
