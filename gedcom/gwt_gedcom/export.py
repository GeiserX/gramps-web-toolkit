"""Write a GEDCOM 5.5.1 file from an offline tree file (a `gwt-check dump` or a `gwt-apply` snapshot, gzipped
or not). It never touches Gramps: the file is the only input.

    ged = build(data, submitter="...") -> (lines, report)

Privacy (the private marks `gwt-check public` reports on, and a living rule that is stricter than its own):

- a person who may be living (gwt_core.rules.possibly_living: no death of their own, and born less than 110
  years ago, or never dated with no dated relative to bound them, or in a DNA association) is only a
  placeholder: the name "Living" and their sex, never their real name, events, notes or citations. An undated
  person counts as living here, which `gwt-check public` does not report;
- an object marked private never leaves the tree: it is not written, and references to it are dropped (a
  private parent disappears from their children's families; a family that loses every member is not written);
- a part of an object marked private is left out too: a private name (a private primary name becomes
  "Private"), a private event reference, and a private child link, which drops both the family's CHIL and the
  child's FAMC;
- a family's events, notes and citations are written only when neither recorded spouse is living or private
  (judged before private members are dropped: a family that lost a spouse to the private filter keeps no
  events);
- a source is written only when an exported citation points at it: a source nobody can cite anymore (its
  citations were anonymized with their person) never leaves, whatever its title says.

Free text is not anonymized: a note, citation page, event description or source title written for a dead
person may still name a living one. The report's `text_naming_living` lists, by Gramps ID, every exported text
that holds a possibly living person's first name and surname, so a share can be read before it goes out.

The export never invents: a date Gramps keeps as text only is written as a GEDCOM date phrase, never guessed.
Media are not written (a GEDCOM points at files that stay on the server), and event roles other than Primary
(Family on a family's events) are not written either: they belong to the other person's own record. A date in
a calendar GEDCOM 5.5.1 cannot hold, with no text to write as a phrase, is left out and counted in the report.
XREFs are the Gramps IDs when they are well-formed, free and fit GEDCOM's 20 characters, else a short form of
the handle; they are unique case-insensitively and never @SUBM@. Text
doubles every `@` and wraps with CONC/CONT inside GEDCOM's line limit, never splitting an escape pair; values
that must stay on one line (names, places, date phrases) cannot inject a record through a newline. CHAR is
UTF-8, FORM LINEAGE-LINKED.
"""

from __future__ import annotations

import re

from gwt_apply import snapshot as snapshots
from gwt_apply.dates import (CAL_FRENCH, CAL_GREGORIAN, CAL_JULIAN, MOD_ABOUT, MOD_AFTER, MOD_BEFORE,
                             MOD_NONE, MOD_RANGE, MOD_SPAN, MOD_TEXTONLY, QUAL_CALCULATED, QUAL_ESTIMATED)
from gwt_core.rules import Index, _s, fold, possibly_living
from gwt_core.version import version

# Gramps event types with a GEDCOM tag; anything else is EVEN with a TYPE line
EVENT_TAG = {
    "Birth": "BIRT", "Baptism": "BAPT", "Christening": "CHR", "Death": "DEAT", "Burial": "BURI",
    "Cremation": "CREM", "Adoption": "ADOP", "Bar Mitzvah": "BARM", "Bat Mitzvah": "BASM",
    "Confirmation": "CONF", "First Communion": "FCOM", "Ordination": "ORDN", "Census": "CENS",
    "Residence": "RESI", "Occupation": "OCCU", "Retirement": "RETI", "Emigration": "EMIG",
    "Immigration": "IMMI", "Naturalization": "NATU", "Graduation": "GRAD", "Probate": "PROB",
    "Will": "WILL", "Marriage": "MARR", "Engagement": "ENGA", "Divorce": "DIV",
    "Divorce Filing": "DIVF", "Annulment": "ANUL", "Blessing": "BLES",
}
_MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
_FRENCH_MONTHS = "VEND BRUM FRIM NIVO PLUV VENT GERM FLOR PRAI MESS THER FRUC COMP".split()
_HEBREW_MONTHS = "TSH CSH KSL TVT SHV ADR ADS NSN IYR SVN TMZ AAV ELL".split()  # Gramps' order, Tishri first
CAL_HEBREW = 2
MOD_FROM, MOD_TO = 7, 8  # Gramps 6 open spans: "from 1850", "to 1850"
_ID = re.compile(r"[A-Za-z]+\d+")
_XREF_MAX = 20  # GEDCOM: an XREF is at most 22 characters with its two @
_BREAKS = re.compile(r"\r\n|[\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")  # every line end a reader may honour
_LIMIT = 240  # a GEDCOM line stays under 255 characters; 240 leaves room for level, tag and doubled @s


class ExportError(ValueError):
    """An input that cannot be read as a tree file, or an output that would be overwritten: exit code 2."""


def load(path: str) -> dict:
    """The tree file (dump or snapshot, gzip found by its bytes) through gwt-apply's own reader, or ExportError."""
    try:
        return snapshots.load(path)
    except (OSError, ValueError) as e:
        raise ExportError(str(e)) from e


def _by_handle(objs: dict, *names: str) -> dict:
    rows = next((objs[n] for n in names if objs.get(n) is not None), [])
    return {o["handle"]: o for o in rows if o.get("handle")}


def _private(o: dict) -> bool:
    return bool(o.get("private"))


def _esc(text: str) -> str:
    """A text value with every @ doubled, so it can never read as a pointer."""
    return text.replace("@", "@@")


def _inline(text, limit: int = 120) -> str:
    """A one-line value (a name piece, a place, a date phrase): newlines cannot inject a record, and the
    result fits GEDCOM's name and place lengths without splitting an @@ pair."""
    flat = _esc(_BREAKS.sub(" ", str(text)).replace("\n", " ").strip())[:limit]
    run = len(flat) - len(flat.rstrip("@"))
    return flat[:-1] if run % 2 == 1 else flat


def _cap(line: str, limit: int = 255) -> str:
    """A whole physical line cut at GEDCOM's 255 characters, never inside an @@ escape pair."""
    if len(line) <= limit:
        return line
    cut = line[:limit]
    run = len(cut) - len(cut.rstrip("@"))
    return cut[:-1] if run % 2 == 1 else cut


def _wrap_escaped(text: str, limit: int = _LIMIT, first: int | None = None) -> list:
    """Chunks of an escaped value, each within the line limit (`first` for the first one) and never splitting
    an @@ pair."""
    chunks = []
    limit, rest = (first if first is not None else limit), limit
    while len(text) > limit:
        run = 0
        while run < limit and text[limit - run - 1] == "@":
            run += 1
        cut = limit - 1 if run % 2 == 1 else limit
        chunks.append(text[:cut])
        text = text[cut:]
        limit = rest
    chunks.append(text)
    return chunks


def _text(out: list, level: int, tag: str, value) -> None:
    """The lines of one text value: @ doubled first, then CONT at its newlines and CONC past the line limit."""
    first_line = True
    head = 250 - len(f"{level} {tag} ")  # a long tag (a NOTE record's XREF) leaves less room on its line
    for para in _BREAKS.sub("\n", str(value)).split("\n"):
        for k, chunk in enumerate(_wrap_escaped(_esc(para), first=min(head, _LIMIT) if first_line else None)):
            if first_line:
                out.append(f"{level} {tag}" + (f" {chunk}" if chunk else ""))
                first_line = False
            else:
                kind = "CONT" if k == 0 else "CONC"
                out.append(f"{level + 1} {kind}" + (f" {chunk}" if chunk else ""))


def _note_text(note: dict) -> str:
    t = note.get("text")
    return (t.get("string") if isinstance(t, dict) else t) or ""


def _date(date: dict | None) -> str | None:
    """A Gramps date dict as a GEDCOM date value, or None when there is nothing to say. A text-only date, or
    one in a calendar GEDCOM 5.5.1 has no escape for, is a phrase in parentheses, never guessed."""
    if not date:
        return None
    mod = date.get("modifier", MOD_NONE)
    text = _inline(date.get("text") or "")
    if mod == MOD_TEXTONLY:
        return f"({text})" if text else None
    val = date.get("dateval") or []
    cal = date.get("calendar", CAL_GREGORIAN)
    phrase = f"({text})" if text else None
    if len(val) < 3 or val[2] <= 0 or cal not in (CAL_GREGORIAN, CAL_JULIAN, CAL_FRENCH, CAL_HEBREW):
        return phrase
    if mod not in (MOD_NONE, MOD_BEFORE, MOD_AFTER, MOD_ABOUT, MOD_RANGE, MOD_SPAN, MOD_FROM, MOD_TO):
        return phrase  # a modifier this writer does not know is never written as an exact date
    months = {CAL_FRENCH: _FRENCH_MONTHS, CAL_HEBREW: _HEBREW_MONTHS}.get(cal, _MONTHS)
    esc = {CAL_GREGORIAN: "", CAL_JULIAN: "@#DJULIAN@ ", CAL_FRENCH: "@#DFRENCH R@ ", CAL_HEBREW: "@#DHEBREW@ "}[cal]

    def ymd(v) -> str:
        d, m, y = v
        mon = months[m - 1] if 1 <= m <= len(months) else ""
        day = str(d) if d and mon else ""  # a day with no month is no GEDCOM date: keep the year
        return " ".join(w for w in (day, mon, f"{y:04d}" if y else "") if w)

    first = ymd(val[0:3])
    if mod == MOD_RANGE:
        if len(val) < 7 or val[6] <= 0:
            return phrase  # a range with no end is not a date GEDCOM can say
        return f"{esc}BET {first} AND {ymd(val[4:7])}"
    if mod == MOD_SPAN:
        second = ymd(val[4:7]) if len(val) >= 7 and val[6] > 0 else ""
        return f"{esc}FROM {first}" + (f" TO {second}" if second else "")
    if mod in (MOD_FROM, MOD_TO):
        return f"{esc}{'FROM' if mod == MOD_FROM else 'TO'} {first}"
    word = {MOD_BEFORE: "BEF", MOD_AFTER: "AFT", MOD_ABOUT: "ABT"}.get(mod, "")
    if not word:
        word = {QUAL_ESTIMATED: "EST", QUAL_CALCULATED: "CAL"}.get(date.get("quality", 0), "")
    return f"{esc}{word + ' ' if word else ''}{first}"


def _place(places: dict, h: str | None) -> str:
    """'Town, Province, Country' following enclosure, stopping at a cycle or a private place."""
    out, seen = [], set()
    while h and h not in seen and h in places:
        seen.add(h)
        p = places[h]
        if _private(p):
            break
        n = p.get("name")
        name = (n.get("value") if isinstance(n, dict) else n) or p.get("title") or ""
        if name:
            out.append(_inline(name))
        refs = p.get("placeref_list") or []
        h = refs[0].get("ref") if refs else None
    return ", ".join(out)


def _refs(lst) -> list:
    """Handles out of a *_list: plain handles in a dump, {"ref": handle} dicts in some snapshots."""
    return [(r.get("ref") if isinstance(r, dict) else r) for r in lst or []]


def _xrefs(kind_rows: dict[str, dict], used: set) -> dict:
    """handle -> @XREF@: the Gramps ID when it is well-formed and still free, else the handle sanitized.
    XREFs are unique case-insensitively and never @SUBM@, whatever the ids or handles hold."""
    out = {}
    for h, o in kind_rows.items():
        gid = o.get("gramps_id") or ""
        x = f"@{gid}@" if _ID.fullmatch(gid) and len(gid) <= _XREF_MAX - 4 else \
            f"@H{re.sub(r'[^A-Za-z0-9_]', '', h)[:_XREF_MAX - 5]}@"  # room for a "-NNN" suffix
        base, n = x, 1
        while x.upper() in used or x.upper() == "@SUBM@":
            n += 1
            x = f"{base[:-1]}-{n}@"
        used.add(x.upper())
        out[h] = x
    return out


def _naming_living(texts: list, living_people: list) -> dict:
    """{kind: [gramps ids]} of the texts that hold a living person's first given name and every word of a
    surname as whole words, accents and case folded. A namesake who died matches too: it is a list to read,
    not a verdict."""
    by_word: dict = {}
    for p in living_people:
        n = p.get("primary_name") or {}
        given = fold(n.get("first_name") or "").split()
        if not given:
            continue
        for sur in n.get("surname_list") or []:
            words = fold(sur.get("surname") or "").split()
            if words:
                by_word.setdefault(words[0], []).append((given[0], words))
    hits: dict = {}
    for kind, gid, text in texts:
        words = set(fold(str(text)).split())
        if any(g in words and all(w in words for w in sw)
               for w0 in words & by_word.keys() for g, sw in by_word[w0]):
            if gid not in hits.setdefault(kind, []):
                hits[kind].append(gid)
    return {k: sorted(v) for k, v in hits.items()}


def build(data: dict, *, submitter: str = "Gramps Web Toolkit", this_year: int | None = None):
    """(lines, report) of the GEDCOM file for a tree file's data (see load())."""
    objs = data["objects"]
    people = _by_handle(objs, "people", "Person")
    families = _by_handle(objs, "families", "Family")
    events = _by_handle(objs, "events", "Event")
    places = _by_handle(objs, "places", "Place")
    cites = _by_handle(objs, "citations", "Citation")
    sources = _by_handle(objs, "sources", "Source")
    notes = _by_handle(objs, "notes", "Note")

    ix = Index(data)
    living = {h for h, p in people.items() if possibly_living(p, ix, this_year=this_year)[0]}
    report = {"living": [], "excluded_private": {}, "dropped_empty_families": 0, "dates_left_out": 0,
              "text_naming_living": {}}

    def kept_person(h):
        p = people.get(h)
        if p is None:
            return None
        if _private(p):
            report["excluded_private"].setdefault("people", []).append(p.get("gramps_id") or h)
            return None
        return p

    kept_people = {}
    for h, p in people.items():
        if kept_person(h) is not None:
            kept_people[h] = p
    # counted over the people the file holds: a private person left out is not "anonymized"
    report["living"] = sorted(people[h].get("gramps_id") or h for h in living if h in kept_people)
    kept_sources = {}
    for h, s in sources.items():
        if _private(s):
            report["excluded_private"].setdefault("sources", []).append(s.get("gramps_id") or h)
        else:
            kept_sources[h] = s
    kept_notes = {h: n for h, n in notes.items() if not _private(n)}
    for h, n in notes.items():
        if _private(n):
            report["excluded_private"].setdefault("notes", []).append(n.get("gramps_id") or h)

    # families: private ones leave; private members leave their families; a family with nobody left is dropped
    kept_fams = {}
    fam_members = {}
    for h, f in families.items():
        if _private(f):
            report["excluded_private"].setdefault("families", []).append(f.get("gramps_id") or h)
            continue
        husb = f.get("father_handle") if f.get("father_handle") in kept_people else None
        wife = f.get("mother_handle") if f.get("mother_handle") in kept_people else None
        # a child link marked private is left out, like a private child: no CHIL here and no FAMC on the child
        children = [r.get("ref") for r in f.get("child_ref_list") or []
                    if r.get("ref") in kept_people and not _private(r)]
        if not husb and not wife and not children:
            report["dropped_empty_families"] += 1
            continue
        # the family's events stay private when EITHER recorded spouse is living, private or dropped:
        # judge the original spouses, before private members are removed above
        spouse_sensitive = any(s and (s not in kept_people or s in living)
                               for s in (f.get("father_handle"), f.get("mother_handle")))
        kept_fams[h] = f
        fam_members[h] = (husb, wife, children, spouse_sensitive)

    used: set = set()
    xref = {}
    for kind, rows in (("F", kept_fams), ("I", kept_people), ("S", kept_sources), ("N", kept_notes)):
        for h, x in _xrefs(rows, used).items():
            xref[(kind, h)] = x

    out = ["0 HEAD", "1 SOUR gwt-import-gedcom", f"2 VERS {version()}", "2 NAME Gramps Web Toolkit",
           "1 GEDC", "2 VERS 5.5.1", "2 FORM LINEAGE-LINKED", "1 CHAR UTF-8", "1 SUBM @SUBM@"]
    used_notes: list = []
    used_sources: list = []
    texts: list = []  # (kind, gramps id, text) of every free text written, for text_naming_living

    def add_notes(level, note_list):
        for nh in _refs(note_list):
            if nh in kept_notes:
                out.append(f"{level} NOTE {xref[('N', nh)]}")
                if nh not in used_notes:
                    used_notes.append(nh)

    def add_citations(level, citation_list):
        for rh in _refs(citation_list):
            c = cites.get(rh)
            if not c or _private(c):
                continue
            sh = c.get("source_handle")
            if sh not in kept_sources:
                continue
            out.append(f"{level} SOUR {xref[('S', sh)]}")
            if sh not in used_sources:
                used_sources.append(sh)
            if c.get("page"):
                _text(out, level + 1, "PAGE", c["page"])
                texts.append(("citations", c.get("gramps_id") or rh, c["page"]))
            if c.get("confidence"):
                out.append(f"{level + 1} QUAY {c['confidence']}")
            add_notes(level + 1, c.get("note_list"))

    def add_events(owner, skip: bool):
        for r in owner.get("event_ref_list") or []:
            # read role and type the way possibly_living does; a family's own events carry the role Family
            if _s(r.get("role")) not in ("", "Primary", "Family") or _private(r):
                continue
            e = events.get(r.get("ref"))
            if not e or _private(e) or skip:
                continue
            etype = _s(e.get("type"))
            tag = EVENT_TAG.get(etype)
            if tag:
                out.append(f"1 {tag}")
            else:
                out.append("1 EVEN")
                if etype:
                    _text(out, 2, "TYPE", etype)
            d = _date(e.get("date"))
            if d:
                out.append(f"2 DATE {d}")
            elif (e.get("date") or {}).get("dateval") and any((e.get("date") or {}).get("dateval")[:3]):
                report["dates_left_out"] += 1
            if e.get("place"):
                name = _place(places, e["place"])
                if name:
                    out.append(f"2 PLAC {name}")  # _place escaped each piece already
            if e.get("description"):
                _text(out, 2, "NOTE", e["description"])
                texts.append(("events", e.get("gramps_id") or r.get("ref"), e["description"]))
            add_notes(2, e.get("note_list"))
            add_citations(2, e.get("citation_list"))

    def gid(h):
        return people[h].get("gramps_id") or h

    for h in sorted(kept_people, key=gid):
        p = kept_people[h]
        out.append(f"0 {xref[('I', h)]} INDI")
        if h in living:
            out.append("1 NAME Living")
        else:
            if _private(p.get("primary_name") or {}):
                out.append("1 NAME Private")  # the record keeps a NAME, never the hidden one
            names = [p.get("primary_name")] + list(p.get("alternate_names") or [])
            for n in names:
                if not n or _private(n):
                    continue
                surs = " ".join(" ".join(w for w in (s.get("prefix") or "", s.get("surname") or "") if w).strip()
                                for s in n.get("surname_list") or [])
                surs = surs.replace("/", " ")  # a slash ends the surname for every reader
                first_name = _inline((n.get("first_name") or "").replace("/", " "))
                line = f"1 NAME {first_name} /{_inline(surs)}/" if first_name else f"1 NAME /{_inline(surs)}/"
                if n.get("suffix"):
                    line += f" {_inline(n['suffix'].replace('/', ' '))}"
                out.append(line)
        sex = {0: "F", 1: "M"}.get(p.get("gender"))
        if sex:
            out.append(f"1 SEX {sex}")
        if h not in living:
            add_events(p, skip=False)
            add_notes(1, p.get("note_list"))
            add_citations(1, p.get("citation_list"))
        for fh in p.get("family_list") or []:
            if fh in kept_fams:
                out.append(f"1 FAMS {xref[('F', fh)]}")
        for fh in p.get("parent_family_list") or []:
            if fh in kept_fams and h in fam_members[fh][2]:  # never a child link the family keeps private
                out.append(f"1 FAMC {xref[('F', fh)]}")

    for h in sorted(kept_fams, key=lambda k: families[k].get("gramps_id") or k):
        f = families[h]
        husb, wife, children, spouse_sensitive = fam_members[h]
        out.append(f"0 {xref[('F', h)]} FAM")
        if husb:
            out.append(f"1 HUSB {xref[('I', husb)]}")
        if wife:
            out.append(f"1 WIFE {xref[('I', wife)]}")
        for ch in children:
            out.append(f"1 CHIL {xref[('I', ch)]}")
        if not spouse_sensitive:
            add_events(f, skip=False)
            add_notes(1, f.get("note_list"))
            add_citations(1, f.get("citation_list"))

    for h in sorted(used_sources, key=lambda k: kept_sources[k].get("gramps_id") or k):
        s = kept_sources[h]
        out.append(f"0 {xref[('S', h)]} SOUR")
        for tag, value in (("TITL", s.get("title")), ("ABBR", s.get("abbrev")), ("AUTH", s.get("author")),
                           ("PUBL", s.get("pubinfo"))):
            if value:
                _text(out, 1, tag, value)
                texts.append(("sources", s.get("gramps_id") or h, value))
        add_notes(1, s.get("note_list"))

    for nh in used_notes:
        _text(out, 0, f"{xref[('N', nh)]} NOTE", _note_text(kept_notes[nh]))
        texts.append(("notes", kept_notes[nh].get("gramps_id") or nh, _note_text(kept_notes[nh])))
    report["text_naming_living"] = _naming_living(texts, [people[h] for h in living])
    out.append(f"0 @SUBM@ SUBM")
    out.append(f"1 NAME {_inline(submitter)}")
    out.append("0 TRLR")

    report.update(people=len(kept_people), families=len(kept_fams), sources=len(used_sources),
                  notes=len(used_notes))
    return [_cap(l) for l in out], report
