"""Rules several tools share: who may be living, how names fold, how dates read, and per-language word tables.

    fold(text, locale=None)            lowercase, no accents, punctuation to spaces; with a locale also its
                                       spelling pairs (es: b/v, y/i) and without its particles (de, van, von)
    possibly_living(person, snapshot)  (living, reason, bound_year): may this person still be alive, and why
    read_date(text, locale=None)       a Gramps Date dict from gwt-apply's date grammar, or with a locale
                                       also from a written date such as «12 de marzo de 1890»
    load_locale(code)                  one word table from gwt_core/locales/<code>.toml
    dedication(text, locale)           a church name with its abbreviations written out («N.ª S.ª» -> nuestra senora)
    find_ids(text, kinds=None)         identity numbers whose check digit is right (DNI, NIE, NIR, codice fiscale, SSN)

Standard library only (tomllib needs Python 3.11).
"""
from __future__ import annotations

import datetime
import functools
import re
import tomllib
import unicodedata
from importlib import resources

LIVING_YEARS = 110   # no death recorded and born less than this many years ago: possibly living
SIBLING_GAP = 20     # a sibling is born at most this many years before or after the person
PARENT_AGE = 14      # nobody has a child or marries younger than this
PARENT_MAX_AGE = 60  # nobody is born to a parent older than this

LOCALES = ("es", "en", "fr", "it", "pt", "de", "nl", "ca", "eu", "gl", "la")
SECTIONS = ("code", "name", "fold", "months", "dates", "roles", "events", "dedication")

_WORD = re.compile(r"[^\W_]+")


# --- name folding --------------------------------------------------------------------------------------------

def _plain(text: str) -> list[str]:
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    return _WORD.findall(t)


def fold(text: str | None, locale: str | None = None) -> str:
    """Lowercase, accents and punctuation gone, spaces collapsed. With a locale, its particles are dropped as
    whole words first and its spelling pairs applied after, so «Bermúdez de Castro» and «Vermudez Castro» compare."""
    if locale is None:
        return " ".join(_plain(text or ""))
    tab = load_locale(locale)
    return _fold(text, tab["_particles"], tab["_pairs"])


def _fold(text, particles, pairs) -> str:
    out = " ".join(w for w in _plain(text or "") if w not in particles)
    for a, b in pairs:
        out = out.replace(a, b)
    return out


# --- locale tables -------------------------------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def load_locale(code: str) -> dict:
    """The parsed table for `code`, with folded lookups added under keys that start with «_»."""
    if code not in LOCALES:
        raise ValueError(f"no locale {code!r}; there are: {', '.join(LOCALES)}")
    with resources.files("gwt_core").joinpath("locales", f"{code}.toml").open("rb") as fh:
        tab = tomllib.load(fh)
    tab["_particles"] = frozenset(" ".join(_plain(w)) for w in tab["fold"]["particles"])
    tab["_pairs"] = tuple((" ".join(_plain(a)), " ".join(_plain(b))) for a, b in tab["fold"]["pairs"])
    tab["_filler"] = frozenset(" ".join(_plain(w)) for w in tab["dates"]["filler"])
    tab["_months"] = {_fold(w, tab["_particles"], tab["_pairs"]): int(n) for n, words in tab["months"].items() for w in words}
    tab["_dedication"] = tuple((" ".join(_plain(a)), " ".join(_plain(b))) for a, b in tab["dedication"]["pairs"])
    return tab


def month(word: str, locale: str) -> int | None:
    """1-12 for a month name or abbreviation in that language, else None."""
    return load_locale(locale)["_months"].get(fold(word, locale))


def dedication(text: str, locale: str) -> str:
    """The folded church name with the locale's abbreviations written out, whole words only."""
    out = f" {fold(text)} "
    for short, full in load_locale(locale)["_dedication"]:
        out = out.replace(f" {short} ", f" {full} ")
    return " ".join(out.split())


# --- identity numbers ----------------------------------------------------------------------------------------

def _dni(s: str) -> bool:
    s = s.upper()
    return "TRWAGMYFPDXBNJZSQVHLCKE"[int(s[:8]) % 23] == s[8]


def _nie(s: str) -> bool:
    s = s.upper()
    return _dni(str("XYZ".index(s[0])) + s[1:])


def _nir(s: str) -> bool:
    s = s.replace(" ", "").upper()
    body = s[:13].replace("2A", "19").replace("2B", "18")
    return body.isdigit() and 97 - int(body) % 97 == int(s[13:])


_CF_ODD = dict(zip("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                   [1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 2, 4, 18, 20, 11, 3, 6, 8,
                    12, 14, 16, 10, 22, 25, 24, 23]))


def _cf(s: str) -> bool:
    s = s.upper()
    total = sum(_CF_ODD[c] if i % 2 == 0 else (int(c) if c.isdigit() else ord(c) - 65) for i, c in enumerate(s[:15]))
    return chr(65 + total % 26) == s[15]


def _ssn(s: str) -> bool:
    area, group, serial = s.split("-")
    return area not in ("000", "666") and not area.startswith("9") and group != "00" and serial != "0000"


# An identity number belongs to a country, not to the language of the text around it, so every kind is looked
# for in every text; the check digit keeps a dated file name such as «20230115B» from passing for one.
# Patterns match ASCII digits only.
_EDGE, _END = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"
ID_KINDS = {
    # Spain: DNI, 8 digits and a check letter; NIE, X, Y or Z, 7 digits and a check letter (mod 23)
    "dni": (_EDGE + r"\d{8}[A-Za-z]" + _END, _dni),
    "nie": (_EDGE + r"[XYZxyz]\d{7}[A-Za-z]" + _END, _nie),
    # France: NIR, 13 characters and a 2-digit key, 97 - (number mod 97); Corsica 2A/2B
    "nir": (_EDGE + r"[12] ?\d{2} ?\d{2} ?(?:\d{2}|2[ABab]) ?\d{3} ?\d{3} ?\d{2}" + _END, _nir),
    # Italy: codice fiscale, 6 letters, 2 digits, a letter, 2 digits, a letter, 3 digits and a check letter
    "cf": (_EDGE + r"[A-Za-z]{6}\d{2}[A-Za-z]\d{2}[A-Za-z]\d{3}[A-Za-z]" + _END, _cf),
    # United States: SSN; area 000, 666 and 9xx, group 00 and serial 0000 are never issued
    "us_ssn": (r"(?<![0-9])\d{3}-\d{2}-\d{4}(?![0-9])", _ssn),
}
_IDS = tuple((kind, re.compile(rx, re.ASCII), check) for kind, (rx, check) in ID_KINDS.items())


def find_ids(text: str, kinds=None) -> list[tuple[str, str]]:
    """(kind, number) for each identity number in `text` whose check digit or rules hold; `kinds` limits the
    search to those kinds."""
    flat = "".join(c for c in unicodedata.normalize("NFD", text or "") if not unicodedata.combining(c))
    return [(kind, m.group(0)) for kind, rx, check in _IDS if kinds is None or kind in kinds
            for m in rx.finditer(flat) if check(m.group(0))]


# --- dates ---------------------------------------------------------------------------------------------------
# The grammar is gwt-apply's (apply/gwt_apply/dates.py), so a date reads the same in every tool:
#   [est|estimated|calc|calculated] [abt|about|bef|before|aft|after] YYYY[-MM[-DD]]
#   [quality] (bet|between) YMD and YMD,   [quality] from YMD to YMD

MOD_NONE, MOD_BEFORE, MOD_AFTER, MOD_ABOUT, MOD_RANGE, MOD_SPAN = 0, 1, 2, 3, 4, 5
QUAL_NONE, QUAL_ESTIMATED, QUAL_CALCULATED = 0, 1, 2
_QUALITY = {"est": QUAL_ESTIMATED, "estimated": QUAL_ESTIMATED, "calc": QUAL_CALCULATED, "calculated": QUAL_CALCULATED}
_MOD = {"abt": MOD_ABOUT, "about": MOD_ABOUT, "bef": MOD_BEFORE, "before": MOD_BEFORE, "aft": MOD_AFTER,
        "after": MOD_AFTER}
_YMD = re.compile(r"^(\d{1,4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?$")


class DateError(ValueError):
    pass


def _ymd(text: str) -> tuple[int, int, int]:
    m = _YMD.match(text)
    if not m:
        raise DateError(f"unreadable date part {text!r}")
    year, mon, day = int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)
    if not 0 <= mon <= 12 or not 0 <= day <= 31 or (day and not mon):
        raise DateError(f"impossible date {text!r}")
    if day and year:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        if day > (31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[mon - 1]:
            raise DateError(f"impossible date {text!r}: that month has no day {day}")
    return day, mon, year


def _sdn(year: int, mon: int, day: int) -> int:
    """Serial day number, the value Gramps stores as `sortval`."""
    year += 4801 if year < 0 else 4800
    if mon > 2:
        mon -= 3
    else:
        mon += 9
        year -= 1
    return ((year // 100) * 146097) // 4 + ((year % 100) * 1461) // 4 + (mon * 153 + 2) // 5 + day - 32045


def _written(text: str, locale: str) -> str:
    """A written date («12 de marzo de 1890», «March 12th, 1890», «marzo 1890») as YYYY-MM[-DD]."""
    tab = load_locale(locale)
    toks = [re.sub(r"^(\d{1,2})(?:st|nd|rd|th|er|e|o)$", r"\1", t) for t in _plain(text)]
    toks = [t for t in toks if t not in tab["_filler"]]
    nums = [t for t in toks if t.isdigit()]
    months = [month(t, locale) for t in toks if not t.isdigit()]
    if len(months) != 1 or months[0] is None or len(toks) - len(nums) != 1 or not nums or len(nums) > 2:
        raise DateError(f"unreadable date {text!r}")
    year = nums[-1]
    if len(year) < 3 or (len(nums) == 2 and (len(nums[0]) > 2 or not int(nums[0]))):
        raise DateError(f"unreadable date {text!r}")
    return f"{year}-{months[0]:02d}" + (f"-{int(nums[0]):02d}" if len(nums) == 2 else "")


def read_date(text: str | None, locale: str | None = None) -> dict:
    """A Gramps Date dict for `text` (None or "" gives the empty date). Refuses what it cannot read, so a date is
    never stored as text. With a locale, the part after an optional quality and modifier may be a written date."""
    date = {"_class": "Date", "calendar": 0, "modifier": MOD_NONE, "quality": QUAL_NONE,
            "dateval": [0, 0, 0, False], "text": "", "sortval": 0, "newyear": 0}
    if text is None or not str(text).strip():
        return date
    words = str(text).strip().lower().split()
    quality = _QUALITY[words.pop(0)] if words and words[0] in _QUALITY else QUAL_NONE
    second = None
    if len(words) == 4 and words[0] in ("bet", "between") and words[2] == "and":
        modifier, first, second = MOD_RANGE, words[1], words[3]
    elif len(words) == 4 and words[0] == "from" and words[2] == "to":
        modifier, first, second = MOD_SPAN, words[1], words[3]
    elif len(words) == 2 and words[0] in _MOD and _YMD.match(words[1]):
        modifier, first = _MOD[words[0]], words[1]
    elif len(words) == 1 and _YMD.match(words[0]):
        modifier, first = MOD_NONE, words[0]
    elif locale is not None and words:
        modifier = _MOD.get(words[0], MOD_NONE)
        first = _written(" ".join(words[1:] if modifier else words), locale)
    else:
        raise DateError(f"unreadable date {text!r}")
    day, mon, year = _ymd(first)
    dateval: list = [day, mon, year, False]
    if second is not None:
        dateval += [*_ymd(second), False]
    date.update(modifier=modifier, quality=quality, dateval=dateval,
                sortval=_sdn(max(year, 1), max(mon, 1), max(day, 1)))
    return date


# --- who may be living ---------------------------------------------------------------------------------------

def _s(v) -> str:
    """A Gramps type or role as text: the API gives a string, some exports a {"string": ...} object."""
    return (v.get("string") or "") if isinstance(v, dict) else (v or "")


def _slug(t: str) -> str:
    return "-".join(_plain(t)) or "event"


def _first_year(date: dict | None) -> int:
    """The Gregorian year a date starts in; 0 when undated."""
    d = date or {}
    dv = d.get("dateval") or []
    if len(dv) < 3 or not dv[2]:
        return 0
    if not d.get("calendar"):
        return dv[2]
    # Julian, Hebrew, French Republican, Persian, Islamic, Swedish: the year from the serial day number
    sv = d.get("sortval") or 0
    return datetime.date.fromordinal(sv - 1721425).year if sv > 1721425 else 0


def latest_year(date: dict | None) -> int | None:
    """The latest Gregorian year a Gramps date can fall in: the end of a range or span, the year itself for
    the rest. 0 when undated, None for «after Y», which has no end. Every living bound is read this way, so an
    uncertain date can only make a person more likely living, never less."""
    d = date or {}
    first = _first_year(d)
    if not first:
        return 0
    if d.get("modifier") == MOD_AFTER:
        return None
    dv = d.get("dateval") or []
    if d.get("modifier") in (MOD_RANGE, MOD_SPAN) and len(dv) >= 7 and dv[6]:
        return first + max(dv[6] - dv[2], 0)
    return first


class Index:
    """A snapshot's people, families and events by handle, and who is in a DNA association. Build it once when
    asking about many people; possibly_living() builds one per call when given a raw snapshot.

    Takes a `gwt-check dump` ({"objects": {"people": [...], "families": [...], "events": [...]}}), a gwt-apply
    snapshot ({"objects": {"Person": [...], ...}}), the inner maps alone, or the same maps keyed by handle."""

    def __init__(self, snapshot: dict):
        objs = snapshot.get("objects") or snapshot

        def by_handle(*names):
            rows = next((objs[n] for n in names if objs.get(n) is not None), [])
            return dict(rows) if isinstance(rows, dict) else {o["handle"]: o for o in rows if o.get("handle")}

        self.people = by_handle("people", "Person")
        self.families = by_handle("families", "Family")
        self.events = by_handle("events", "Event")
        self.dna: set[str] = set()
        for h, p in self.people.items():
            for r in p.get("person_ref_list") or []:
                if _s(r.get("rel")).strip().casefold() == "dna" and r.get("ref"):
                    self.dna |= {h, r.get("ref")}

    def own_events(self, p: dict, types: set[str] | None) -> list[tuple[str, int | None, int]]:
        """(type, latest year, first year) of the person's own events of these types (None: every type)."""
        out = []
        for r in p.get("event_ref_list") or []:
            e = self.events.get(r.get("ref"))
            if e and _s(r.get("role")) in ("", "Primary") and (types is None or _s(e.get("type")) in types):
                out.append((_s(e.get("type")), latest_year(e.get("date")), _first_year(e.get("date"))))
        return out

    def earliest(self, p: dict | None) -> tuple[int, str]:
        """(year, word) of the person's own event that bounds their birth tightest: they were born by that year.
        The word is «born» for a birth or baptism, else the event type («death», «census»). (0, "") if none."""
        dated = [(y, "born" if t in BIRTH_TYPES else _slug(t)) for t, y, _ in self.own_events(p, None) if y] if p else []
        return min(dated) if dated else (0, "")

    def handles(self, family_handles, key: str) -> list[str]:
        """The `key` handles (child refs, or father and mother) of these families."""
        out = []
        for fh in family_handles or []:
            f = self.families.get(fh) or {}
            out += [c.get("ref") for c in f.get("child_ref_list") or []] if key == "child" else \
                [f.get("father_handle"), f.get("mother_handle")]
        return [h for h in out if h in self.people]



BIRTH_TYPES = {"Birth", "Baptism", "Christening"}
END_TYPES = {"Death", "Burial", "Cremation"}


def possibly_living(person: dict, snapshot: dict | Index, *, years: int = LIVING_YEARS, gap: int = SIBLING_GAP,
                    this_year: int | None = None, undated: bool = True) -> tuple[bool, str, int | None]:
    """(living, reason, bound_year). `bound_year` is the latest year the person can have been born, from the
    evidence the reason names, or None. In order:

    1. a death, burial or cremation of their own: not living («death-recorded»);
    2. a dated birth, baptism or christening of their own: living if less than `years` ago («born-1950»), read
       by its latest year (the end of a range or span); «after Y» with Y less than `years` ago is living;
    3. in a DNA association, either end: living («dna-match»), since a match is a person who took a test;
    4. other dated evidence, each giving the latest year the person can have been born: any other event of
       their own in Y gives Y («census-1851»); a child's earliest event in Y gives Y-14, a grandchild's Y-28, a
       marriage Y-14, a spouse's or sibling's Y+`gap`, a parent's Y+60; the tightest bound decides, living if
       it is less than `years` ago («child-born-1990», «child-death-1750»);
    5. nothing dated: `undated` («no-dates»).

    Only the person's own events count in 1 and 2: a godparent at a 2001 baptism was not born in 2001."""
    ix = snapshot if isinstance(snapshot, Index) else Index(snapshot)
    cutoff = (this_year or datetime.date.today().year) - years
    end = ix.own_events(person, END_TYPES)
    if end:
        return False, f"{end[0][0].lower()}-recorded", None
    births = ix.own_events(person, BIRTH_TYPES)
    own = sorted((y, t) for t, y, _ in births if y)
    if own:
        y, t = own[0]
        return y >= cutoff, f"{'born' if t == 'Birth' else 'baptised'}-{y}", y
    after = [first for _, y, first in births if y is None]
    if after and max(after) >= cutoff:
        return True, f"born-after-{max(after)}", None
    if person.get("handle") in ix.dna:
        return True, "dna-match", None
    me = person.get("handle")
    bounds = []
    if (e := ix.earliest(person))[0]:
        bounds.append((e[0], f"{e[1]}-{e[0]}"))
    for ch in ix.handles(person.get("family_list"), "child"):
        child = ix.people[ch]
        if (e := ix.earliest(child))[0]:
            bounds.append((e[0] - PARENT_AGE, f"child-{e[1]}-{e[0]}"))
        for gh in ix.handles(child.get("family_list"), "child"):
            if (e := ix.earliest(ix.people[gh]))[0]:
                bounds.append((e[0] - 2 * PARENT_AGE, f"grandchild-{e[1]}-{e[0]}"))
    for fh in person.get("family_list") or []:
        for r in (ix.families.get(fh) or {}).get("event_ref_list") or []:
            e = ix.events.get(r.get("ref")) or {}
            if _s(e.get("type")) == "Marriage" and (y := latest_year(e.get("date"))):
                bounds.append((y - PARENT_AGE, f"married-{y}"))
    for sh in ix.handles(person.get("family_list"), "parents"):
        if sh != me and (e := ix.earliest(ix.people[sh]))[0]:
            bounds.append((e[0] + gap, f"spouse-{e[1]}-{e[0]}"))
    for ph in ix.handles(person.get("parent_family_list"), "parents"):
        if (e := ix.earliest(ix.people[ph]))[0]:
            bounds.append((e[0] + PARENT_MAX_AGE, f"parent-{e[1]}-{e[0]}"))
    for sh in ix.handles(person.get("parent_family_list"), "child"):
        if sh != me and (e := ix.earliest(ix.people[sh]))[0]:
            bounds.append((e[0] + gap, f"sibling-{e[1]}-{e[0]}"))
    if bounds:
        bound, reason = min(bounds)
        return bound >= cutoff, reason, bound
    return undated, "no-dates", None
