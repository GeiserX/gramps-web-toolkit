"""Gramps dates as intervals of days, and the vital events a person's dates come from.

A date becomes the interval it may stand for: an exact day, a month, a year, a range, or a span widened by
the slack of an "about", estimated or calculated date. Two facts contradict each other only when their
intervals are disjoint, so a vague date can never produce a contradiction a plain reading would not.
Dates this module cannot read safely (another calendar, a non-January new year, a dual-dated year,
"from" and "to", free text other than a plain ISO date) give None: they are counted, never guessed.
"""

from __future__ import annotations

import datetime
import re

ABOUT_YEARS = 2       # "about" and estimated dates: this many years either side
CALCULATED_YEARS = 1  # calculated dates (usually worked out from a stated age): this many years either side
FAR = 10 ** 9         # the open end of "before" and "after"

# event types, compared lowercased; a tree with custom types in another language adds them here
BIRTH = {"birth", "nacimiento", "naissance"}
BAPTISM = {"baptism", "christening", "bautismo", "bautizo", "baptême"}
DEATH = {"death", "defunción", "defuncion", "décès"}
BURIAL = {"burial", "cremation", "entierro", "inhumation"}
MARRIAGE = {"marriage", "matrimonio", "mariage"}

_TEXT = re.compile(r"^\s*(?P<c>c\.?|ca\.?|about|circa)?\s*(?P<y>\d{4})(?:-(?P<m>\d{1,2})(?:-(?P<d>\d{1,2}))?)?\s*$", re.I)


class Span:
    """A date as an interval of proleptic Gregorian ordinals; `firm` when no guess or slack went into it."""
    __slots__ = ("lo", "hi", "firm", "text")

    def __init__(self, lo: int, hi: int, firm: bool, text: str):
        self.lo, self.hi, self.firm, self.text = lo, hi, firm, text

    def __repr__(self):
        return f"Span({self.text})"


def kind(t) -> str:
    """An event type or child relation, as a string or as {"string": ...}, lowercased."""
    return str(t if isinstance(t, str) else (t or {}).get("string", "")).strip().lower()


def _ord(y: int, m: int, d: int) -> int | None:
    try:
        return datetime.date(y, m, d).toordinal()
    except ValueError:
        return None


def _part(y: int, m: int, d: int) -> tuple[int, int] | None:
    """(lo, hi) of a possibly partial date."""
    if not y or not 1 <= y <= 9998:
        return None
    if m and d:
        o = _ord(y, m, d)
        return (o, o) if o else None
    if m:
        if not 1 <= m <= 12:
            return None
        return _ord(y, m, 1), _ord(y + (m == 12), m % 12 + 1, 1) - 1
    return _ord(y, 1, 1), _ord(y, 12, 31)


def _label(y: int, m: int, d: int) -> str:
    return f"{y:04d}" + (f"-{m:02d}" if m else "") + (f"-{d:02d}" if m and d else "")


def span(date: dict | None) -> Span | None:
    """The interval of a Gramps date, or None when it cannot be read safely."""
    if not date:
        return None
    dv = list(date.get("dateval") or [])
    dv += [0, 0, 0, False, 0, 0, 0, False][len(dv):]
    mod, qual = date.get("modifier") or 0, date.get("quality") or 0
    if date.get("calendar") or date.get("newyear") or dv[3] or mod in (7, 8):
        return None
    if mod == 6:  # text only: the plain ISO shapes, nothing else
        m = _TEXT.match(date.get("text") or "")
        got = m and _part(int(m["y"]), int(m["m"] or 0), int(m["d"] or 0))
        if not got:
            return None
        slack = ABOUT_YEARS * 366 if m["c"] else 0
        return Span(got[0] - slack, got[1] + slack, False, (date.get("text") or "").strip())
    d, mo, y = dv[0], dv[1], dv[2]
    first = _part(y, mo, d)
    if not first:
        return None
    lo, hi = first
    text = _label(y, mo, d)
    slack = 366 * max(ABOUT_YEARS if mod == 3 or qual == 1 else 0, CALCULATED_YEARS if qual == 2 else 0)
    prefix = {1: "est. ", 2: "calc. "}.get(qual, "")
    if mod in (4, 5):
        if dv[7]:
            return None
        second = _part(dv[6], dv[5], dv[4])
        if not second or second[1] < lo:
            return None
        return Span(lo - slack, second[1] + slack, False, prefix + text + ".." + _label(dv[6], dv[5], dv[4]))
    if mod == 1:
        return Span(-FAR, hi + slack, False, prefix + "before " + text)
    if mod == 2:
        return Span(lo - slack, FAR, False, prefix + "after " + text)
    if mod == 3:
        prefix += "about "
    return Span(lo - slack, hi + slack, mod == 0 and qual == 0, prefix + text)


def years_between(a: Span, b: Span) -> tuple[float, float]:
    """(fewest, most) years from a to b."""
    return (b.lo - a.hi) / 365.25, (b.hi - a.lo) / 365.25


def birth_from_baptism(bapt: Span) -> Span:
    """A baptism bounds a birth from above only: the child was born on that day or up to a year before."""
    return Span(bapt.lo - 366, bapt.hi, False, f"up to a year before baptism {bapt.text}")
