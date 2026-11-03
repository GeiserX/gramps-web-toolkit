"""Turn a parsed GEDCOM file into a gwt-apply change list, checked against a snapshot of the tree.

Every op's key is `ged-<batch>-<first 8 of the file's sha256>-<xref>[-<part>]`, so the same file under the same
batch gives the same keys and a rerun is a skip, while an edited file gives new keys and goes through gwt-apply's
duplicate preflight again instead of being merged by key.

A person who looks like someone already in the tree, by the rules of gwt-apply's preflight (same name, or same
first given name and first surname born within two years), is never added: the person, their events and every
link through them are held, and the report lists them under `namesakes` with the Gramps IDs they look like.
The reviewer then says, per person, `--same @I3@=I0042` (that is the tree person: link the families to them and
add nothing about them) or `--different "@I3@=born in another parish"` (another person: add them with
allow_namesake). Nothing is decided on a name alone.
"""

from __future__ import annotations

import datetime
import hashlib
import re
from dataclasses import dataclass, field

from gwt_apply.dates import DateError, parse_date
from gwt_apply.dupes import Couple, Who, _alike, _same_couple, preflight, tree_people
from gwt_apply.ops import _is_familysearch, fold, stable_handle, validate
from gwt_core.rules import Index, possibly_living

from .parse import Gedcom, Line, ged_date

PERSON_EVENTS = {
    "BIRT": "Birth", "CHR": "Christening", "BAPM": "Baptism", "DEAT": "Death", "BURI": "Burial",
    "CREM": "Cremation", "ADOP": "Adopted", "BARM": "Bar Mitzvah", "BASM": "Bas Mitzvah", "BLES": "Blessing",
    "CHRA": "Adult Christening", "CONF": "Confirmation", "FCOM": "First Communion", "ORDN": "Ordination",
    "NATU": "Naturalization", "EMIG": "Emigration", "IMMI": "Immigration", "CENS": "Census", "PROB": "Probate",
    "WILL": "Will", "GRAD": "Graduation", "RETI": "Retirement", "RESI": "Residence", "OCCU": "Occupation",
    "EDUC": "Education", "RELI": "Religion", "NATI": "Nationality", "TITL": "Nobility Title", "PROP": "Property",
    "DSCR": "Description", "CAST": "Caste", "NCHI": "Number of Children", "NMR": "Number of Marriages",
    "SSN": "Social Security Number", "IDNO": "Identification Number",
}
FAMILY_EVENTS = {
    "MARR": "Marriage", "DIV": "Divorce", "DIVF": "Divorce Filing", "ENGA": "Engagement", "MARB": "Marriage Banns",
    "MARC": "Marriage Contract", "MARL": "Marriage License", "MARS": "Marriage Settlement", "ANUL": "Annulment",
    "CENS": "Census", "RESI": "Residence",
}
CUSTOM = ("EVEN", "FACT")  # the type is in TYPE
SINGLE = {"Birth", "Death"}  # gwt-apply keeps one of each per person; a second goes in a note
GENDER = {"M": "M", "F": "F"}
PEDI = {"adopted": "Adopted", "foster": "Foster", "sealing": "Sponsored", "birth": "Birth"}
QUAY = {"0": 0, "1": 1, "2": 3, "3": 4}  # GEDCOM's evidence quality to Gramps' confidence (2 = normal)
PLACE_TYPES = {w: w.title() for w in ("country", "state", "county", "city", "parish", "locality", "street",
                                      "province", "region", "department", "neighborhood", "district", "borough",
                                      "municipality", "town", "village", "hamlet", "farm", "building", "number")}
EXTERNAL = {"_FSFTID": "FamilySearch", "_APID": "Ancestry", "_WT_USER": None}
EXID_SYSTEMS = (("familysearch.org", "FamilySearch"), ("ancestry.", "Ancestry"), ("wikitree.com", "WikiTree"),
                ("geni.com", "Geni"), ("myheritage.", "MyHeritage"), ("wikidata.org", "Wikidata"))
NAME_TYPES = {"married": "Married Name", "aka": "Also Known As", "birth": "Birth Name", "immigrant": "Also Known As",
              "maiden": "Birth Name"}
NAME_USED = {"GIVN", "SURN", "SOUR", "TYPE", "_MARNM"}
USED = {"INDI": {"NAME", "SEX", "FAMC", "FAMS", "SOUR", "NOTE", "SNOTE", "EXID", "_FSFTID", "_APID", "_UID", "CHAN",
                 *PERSON_EVENTS, *CUSTOM},
        "FAM": {"HUSB", "WIFE", "CHIL", "SOUR", "NOTE", "SNOTE", "CHAN", *FAMILY_EVENTS, *CUSTOM}}


class ImportError_(ValueError):
    """A run that cannot go on: an unknown --same/--different xref, a bad batch name."""


@dataclass
class Person:
    rec: Line
    key: str
    first: str
    surname: str
    gender: str
    held: str | None = None  # why it is not added: "living", "namesake", "no given name", "same"
    ref: str = ""  # what other ops call it: key:<key>, or the Gramps ID with --same
    alt_names: list[tuple[str, str, str | None]] = field(default_factory=list)  # (given, surname, name type)
    namesake: tuple[str, list[str]] | None = None  # --different: (reason, the Gramps IDs it looks like)


def _slug(xref: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", xref.strip("@").lower()).strip("-") or "x"


def _h(*parts) -> str:
    return hashlib.sha256("\x1f".join(map(str, parts)).encode("utf-8")).hexdigest()[:10]


def uids(rec: Line) -> list[str]:
    """The _UID values of a record, as compared: upper case, hex digits only (some exporters add dashes)."""
    return [u for u in (re.sub(r"[^0-9A-F]", "", line.value.upper()) for line in rec.all("_UID")) if u]


def _tree_uids(person: dict) -> list[str]:
    out = []
    for a in person.get("attribute_list") or []:
        t = a.get("type")
        t = t.get("string") if isinstance(t, dict) else t
        if str(t) == "_UID" and a.get("value"):
            out.append(re.sub(r"[^0-9A-F]", "", str(a["value"]).upper()))
    return out


def split_name(rec: Line) -> tuple[str, str]:
    """(given names, surname) of a NAME line: "Pablo Luis /García Moreno/ Jr" -> ("Pablo Luis", "García Moreno"),
    with GIVN and SURN when the line has them."""
    value = rec.value.strip()
    m = re.match(r"^(.*?)/(.*?)/(.*)$", value)
    given, surname = ((m.group(1) + " " + m.group(3)).strip(), m.group(2).strip()) if m else (value, "")
    given = rec.val("GIVN") or re.sub(r"\s+", " ", given)
    surname = rec.val("SURN").replace(",", " ").strip() or re.sub(r"\s+", " ", surname)
    return given, surname


class Snapshot:
    """A gwt-apply snapshot as the little client dupes.preflight reads: list_all only."""

    def __init__(self, snap: dict):
        self.objects = snap.get("objects") or {}

    def list_all(self, cls: str, keys: list[str]) -> list[dict]:
        return [{k: o.get(k) for k in ["handle", *keys]} for o in self.objects.get(cls) or []]


class Builder:
    def __init__(self, ged: Gedcom, snap: dict, *, batch: str, ruling: str, same: dict[str, str] | None = None,
                 different: dict[str, str] | None = None, allow_living: bool = False, this_year: int | None = None,
                 only: set[str] | None = None, check_tree: bool = True):
        self.ged, self.snap = ged, snap
        self.client = Snapshot(snap)
        self.prefix = f"ged-{batch}-{ged.sha256[:8]}"
        self.ruling = ruling
        self.same, self.different = dict(same or {}), dict(different or {})
        self.allow_living, self.this_year = allow_living, this_year or datetime.date.today().year
        self.only = only
        self.check_tree = check_tree
        self.ops: list[dict] = []
        self.payloads: dict[str, str] = {}
        self.report: dict = {"file": {"version": ged.version, "encoding": ged.encoding, "declared": ged.declared,
                                      "sha256": ged.sha256, "repaired_lines": len(ged.repairs),
                                      "joined_lines": len(ged.joined)},
                             "people": 0, "families": 0, "events": 0, "places": 0, "sources": 0, "citations": 0,
                             "notes": 0, "external_ids": 0, "namesakes": [], "file_namesakes": [], "file_couples": [],
                             "living_held": [], "held": [], "date_problems": [], "places_ambiguous": [],
                             "places_matched": 0, "second_vitals": [], "families_skipped": [], "duplicate_events": 0,
                             "external_ids_refused": [], "not_imported": {}, "preflight": [], "problems": list(ged.problems)}
        for x in list(self.same) + list(self.different):
            if x not in ged.by_xref or ged.by_xref[x].tag != "INDI":
                raise ImportError_(f"{x} is not a person in the file")
        if check_tree:
            ids = {o.get("gramps_id") for o in self.client.objects.get("Person") or []}
            for x, gid in self.same.items():
                if gid not in ids:
                    raise ImportError_(f"--same {x}={gid}: the tree has no person {gid}")
        self.people: dict[str, Person] = {}
        self.sources: dict[str, str] = {}  # source xref, or inline text hash -> key
        self.citations: dict[tuple, dict] = {}
        self.notes: dict[tuple, dict] = {}
        self.places: dict[tuple, tuple[str, str | None]] = {}  # folded path -> (ref, tree handle or None)
        self.ambiguous: set[str] = set()
        self.tree_places = self.client.list_all("Place", ["name", "placeref_list", "gramps_id"]) if check_tree else []
        self.place_ix: dict[tuple, list[dict]] = {}  # (folded name, enclosing handle) -> tree places
        self.place_names: dict[str, list[dict]] = {}  # folded name -> tree places, at any level
        for r in self.tree_places:
            name = fold((r.get("name") or {}).get("value"))
            up = next((x.get("ref") for x in r.get("placeref_list") or []), None)
            self.place_ix.setdefault((name, up), []).append(r)
            self.place_names.setdefault(name, []).append(r)
        self.place_form = self._form(ged.header.first("PLAC") if ged.header else None)

    # --- helpers -------------------------------------------------------------------------------------------
    def emit(self, op: dict) -> tuple[str, bool]:
        """Add an op unless the same change is already there (gwt-apply refuses a list that has it twice).
        Returns (the key that makes the change, whether this op is new)."""
        op = {"op": op["op"], "key": op["key"], "ruling": self.ruling,
              **{k: v for k, v in op.items() if k not in ("op", "key")}}
        payload = repr(sorted((k, repr(v)) for k, v in op.items() if k not in ("key", "ruling")))
        if payload in self.payloads:
            return self.payloads[payload], False
        self.payloads[payload] = op["key"]
        self.ops.append(op)
        return op["key"], True

    def unused(self, rec: Line, used: set[str]) -> None:
        for c in rec.children:
            if c.tag not in used:
                k = f"{rec.tag}.{c.tag}"
                self.report["not_imported"][k] = self.report["not_imported"].get(k, 0) + 1

    @staticmethod
    def _form(plac: Line | None) -> list[str]:
        form = plac.val("FORM") if plac else ""
        return [w.strip().lower() for w in form.split(",")] if form else []

    # --- people --------------------------------------------------------------------------------------------
    def read_people(self) -> None:
        for rec in self.ged.individuals:
            if self.only is not None and rec.xref not in self.only:
                continue
            names = rec.all("NAME")
            first, surname = split_name(names[0]) if names else ("", "")
            p = Person(rec, f"{self.prefix}-{_slug(rec.xref)}", first, surname, GENDER.get(rec.val("SEX").upper(), "U"))
            for i, n in enumerate(names):
                given, sur = split_name(n)
                if i:
                    p.alt_names.append((given, sur, NAME_TYPES.get(n.val("TYPE").lower())))
                for m in n.all("_MARNM"):  # a married surname, as Gramps Web's own importer keeps it
                    mg, ms = "", m.value.strip().strip("/").strip()  # «Gil» alone is the surname
                    if re.search(r"/.*/", m.value):  # «Ana /Gil/»: a whole name, as Gramps reads it
                        mg, ms = split_name(Line(m.level, "NAME", m.value, None, m.lineno))
                    if ms:
                        p.alt_names.append((mg or given, ms, "Married Name"))
                for c in n.children:
                    if c.tag not in NAME_USED:
                        k = f"INDI.NAME.{c.tag}"
                        self.report["not_imported"][k] = self.report["not_imported"].get(k, 0) + 1
            p.ref = "key:" + p.key
            if not first:
                p.held = "no given name"
            self.people[rec.xref] = p

    def gramps_view(self) -> dict:
        """The file's people, families and events as Gramps objects, enough for gwt_core's living rule."""
        people, families, events = {}, {}, {}
        for x, p in self.people.items():
            refs = []
            for i, ev in enumerate(p.rec.children):
                etype = PERSON_EVENTS.get(ev.tag) or (ev.val("TYPE") if ev.tag in CUSTOM else None)
                if not etype:
                    continue
                d, _ = ged_date(ev.val("DATE"))
                try:
                    date = parse_date(d) if d else None
                except DateError:
                    date = None
                h = f"{x}#{i}"
                events[h] = {"handle": h, "type": etype, "date": date}
                refs.append({"ref": h, "role": "Primary"})
            people[x] = {"handle": x, "event_ref_list": refs,
                         "family_list": [c.pointer for c in p.rec.all("FAMS") if c.pointer],
                         "parent_family_list": [c.pointer for c in p.rec.all("FAMC") if c.pointer]}
        for f in self.ged.families:
            refs = []
            for i, ev in enumerate(f.children):
                if ev.tag == "MARR":
                    d, _ = ged_date(ev.val("DATE"))
                    try:
                        date = parse_date(d) if d else None
                    except DateError:
                        date = None
                    h = f"{f.xref}#{i}"
                    events[h] = {"handle": h, "type": "Marriage", "date": date}
                    refs.append({"ref": h})
            families[f.xref] = {"handle": f.xref, "father_handle": f.first("HUSB") and f.first("HUSB").pointer,
                                "mother_handle": f.first("WIFE") and f.first("WIFE").pointer,
                                "child_ref_list": [{"ref": c.pointer} for c in f.all("CHIL") if c.pointer],
                                "event_ref_list": refs}
        return {"people": people, "families": families, "events": events}

    def hold_living(self) -> None:
        if self.allow_living:
            return
        view = self.gramps_view()
        ix = Index(view)
        for x, p in self.people.items():
            if p.held:
                continue
            living, why, _ = possibly_living(view["people"][x], ix, this_year=self.this_year)
            if living:
                p.held = "living"
                self.report["living_held"].append({"xref": x, "name": f"{p.first} {p.surname}".strip(), "why": why})

    def birth_years(self, p: Person) -> set[int]:
        years = set()
        for ev in p.rec.all("BIRT", "CHR", "BAPM"):
            d, _ = ged_date(ev.val("DATE"))
            try:
                y = (parse_date(d)["dateval"] or [0, 0, 0])[2] if d else 0
            except DateError:
                y = 0
            if y:
                years.add(y)
        return years

    def who(self, x: str, ref: str, through: dict[str, str]) -> Who:
        """The person as preflight compares them: name, birth years, and parents and spouses named through
        `through` (xref -> the reference preflight will see for them)."""
        p = self.people[x]
        w = Who(ref, p.first, p.surname, years=self.birth_years(p))
        for c in p.rec.all("FAMC"):
            fam = self.ged.by_xref.get(c.pointer or "")
            w.parents |= {through[q] for q in (fam and [l.pointer for l in fam.all("HUSB", "WIFE")]) or []
                          if q in through}
        for c in p.rec.all("FAMS"):
            fam = self.ged.by_xref.get(c.pointer or "")
            w.spouses |= {through[q] for q in (fam and [l.pointer for l in fam.all("HUSB", "WIFE")]) or []
                          if q in through and q != x}
        return w

    def match_tree(self) -> None:
        """Hold every person who looks like a tree person, unless --same or --different says who they are."""
        tree = tree_people(self.client)[0] if self.check_tree else []
        # a person an earlier run of this very list made is that op, not a namesake of it: a rerun is a skip
        mine = {stable_handle(p.key) for p in self.people.values()}
        tree = [w for w in tree if w.handle not in mine]
        by_loose: dict[tuple, list[Who]] = {}
        for w in tree:
            by_loose.setdefault(w.loose, []).append(w)
        # the _UID Gramps Web's own GEDCOM import keeps as an attribute: the same record, whatever its name is now
        by_uid: dict[str, list[Who]] = {}
        if self.check_tree:
            handles = {w.handle: w for w in tree}
            for o in self.client.objects.get("Person") or []:
                if o.get("handle") in handles:
                    for u in _tree_uids(o):
                        by_uid.setdefault(u, []).append(handles[o["handle"]])
        for x, p in self.people.items():
            if x in self.same:
                p.held, p.ref = "same", self.same[x]
                continue
            if p.held:
                continue
            who = self.who(x, x, self.same)
            # the same full name means the same first name and first surname: by_loose holds every namesake
            alike = [(w, why) for w in by_loose.get(who.loose, []) if (why := _alike(who, w))]
            alike += [(w, "same _UID") for u in uids(p.rec) for w in by_uid.get(u, [])]
            ids = sorted({w.ref for w, _ in alike})
            if not ids:
                continue
            if x in self.different:
                p.namesake = (self.different[x], ids)
                continue
            p.held = "namesake"
            self.report["namesakes"].append({
                "xref": x, "name": f"{p.first} {p.surname}", "born": sorted(who.years), "namesake_of": ids,
                "why": sorted({why for _, why in alike}),
                "decide": f"--same {x}=<one of {', '.join(ids)}>  or  --different \"{x}=<why another person>\""})

    def add_people(self) -> None:
        made: dict[tuple, list[Who]] = {}  # by loose name: only those can look alike
        refs = {x: p.ref for x, p in self.people.items() if p.held in (None, "same")}
        for x, p in self.people.items():
            if p.held:
                if p.held not in ("same", "living", "namesake"):
                    self.report["held"].append({"xref": x, "why": p.held})
                continue
            op = {"op": "add_person", "key": p.key, "first_name": p.first, "surname": p.surname, "gender": p.gender}
            waived = p.namesake
            # namesakes inside the file: separate records the file keeps apart; preflight wants that said
            who = self.who(x, p.ref, refs)
            twins = [w.ref for w in made.get(who.loose, []) if _alike(who, w)]
            if waived or twins:
                op["allow_namesake"] = True
                reasons = ([waived[0]] if waived else []) + (
                    [f"a separate record of the GEDCOM file ({x})"] if twins else [])
                op["namesake_reason"] = "; ".join(reasons)
                op["namesake_of"] = (waived[1] if waived else []) + twins
                if twins:
                    self.report["file_namesakes"].append({"xref": x, "name": f"{p.first} {p.surname}",
                                                          "of": [t[4:] for t in twins]})
            self.emit(op)
            self.report["people"] += 1
            made.setdefault(who.loose, []).append(who)
            for given, sur, ntype in p.alt_names:
                alt = {"op": "add_alternate_name", "key": f"{p.key}-name-{_h(given, sur, *([ntype] if ntype else []))}",
                       "target": {"person": p.ref}}
                if given:
                    alt["first_name"] = given
                if sur:
                    alt["surnames"] = [{"surname": sur}]
                if ntype:
                    alt["type"] = ntype
                if given or sur:
                    self.emit(alt)
            for u in uids(p.rec)[:1]:  # so the next import of a file from the same site finds this person
                self.emit({"op": "set_attribute", "key": f"{p.key}-uid", "target": {"person": p.ref},
                           "type": "_UID", "value": u})

    # --- places, sources, citations, notes -----------------------------------------------------------------
    def place(self, plac: Line | None, where: str) -> tuple[str | None, str | None]:
        """(place ref, text to keep in the description when no place is used)."""
        if plac is None or not plac.value.strip():
            return None, None
        parts = [p.strip() for p in plac.value.split(",")]
        if not any(parts):
            return None, None
        form = self._form(plac) or self.place_form
        types = [PLACE_TYPES.get(f, "Unknown") for f in form] if len(form) == len(parts) else ["Unknown"] * len(parts)
        levels = [(n, t) for n, t in zip(parts, types) if n][::-1]  # country first
        coords = None
        m = plac.first("MAP")
        if m and m.val("LATI") and m.val("LONG"):
            try:
                lat = float(m.val("LATI")[1:]) * (-1 if m.val("LATI")[0].upper() == "S" else 1)
                lon = float(m.val("LONG")[1:]) * (-1 if m.val("LONG")[0].upper() == "W" else 1)
                coords = (lat, lon)
            except (ValueError, IndexError):
                pass
        parent, handle, path, matching = None, None, (), self.check_tree
        for i, (name, ptype) in enumerate(levels):
            path = path + (fold(name),)
            if path in self.places:
                parent, handle = self.places[path]
                matching = handle is not None
                continue
            if matching:  # the same name under the same parent, top down
                found = self.place_ix.get((fold(name), handle), [])
                if not found and len(levels) == 1:  # one level only ("Villanueva"): that name anywhere in the tree
                    found = self.place_names.get(fold(name), [])
                if len(found) == 1 and found[0].get("gramps_id"):
                    parent, handle = self.places[path] = (found[0]["gramps_id"], found[0]["handle"])
                    self.report["places_matched"] += 1
                    continue
                if found:
                    if plac.value not in self.ambiguous:
                        self.ambiguous.add(plac.value)
                        self.report["places_ambiguous"].append({"where": where, "text": plac.value, "at": name,
                                                                "tree_ids": sorted(r.get("gramps_id") or r["handle"]
                                                                                   for r in found)})
                    return None, plac.value
                matching = False
            key = f"{self.prefix}-place-{_h(*path)}"
            op = {"op": "add_place", "key": key, "name": name, "type": ptype}
            if parent:
                op["enclosed_by"] = parent
            if coords and i == len(levels) - 1:
                op["lat"], op["long"] = coords
            self.emit(op)
            self.report["places"] += 1
            parent, handle = self.places[path] = ("key:" + key, None)
        return parent, None

    def source(self, line: Line) -> str | None:
        """key:<key> of the source a SOUR line cites, made once."""
        x = line.pointer
        if x:
            if x in self.sources:
                return self.sources[x]
            rec = self.ged.by_xref.get(x)
            if rec is None or rec.tag != "SOUR":
                self.report["problems"].append(f"line {line.lineno}: SOUR points at {x}, which is not a source")
                return None
            key = f"{self.prefix}-{_slug(x)}"
            title = rec.val("TITL") or rec.val("ABBR") or f"Source {x} of the GEDCOM file"
            op = {"op": "add_source", "key": key, "title": title.replace("\n", " ")}
            for tag, f in (("AUTH", "author"), ("PUBL", "pubinfo"), ("ABBR", "abbrev")):
                if rec.val(tag):
                    op[f] = rec.val(tag).replace("\n", " ")
            key, new = self.emit(op)  # two records with the same title make one source: cite the one made
            self.report["sources"] += new
            self.sources[x] = "key:" + key
            for r in rec.all("REPO"):
                self.repository(r, key)
            if rec.val("TEXT"):
                self.note_text(rec.val("TEXT"), {"source": "key:" + key}, f"{self.prefix}-{_slug(x)}-text")
            for n in rec.all("NOTE", "SNOTE"):
                self.note(n, {"source": "key:" + key})
            self.unused(rec, {"TITL", "ABBR", "AUTH", "PUBL", "REPO", "TEXT", "NOTE", "SNOTE", "CHAN"})
            return self.sources[x]
        text = line.value.strip()
        if not text:
            return None
        hid = "inline-" + _h(text)
        if hid not in self.sources:
            key = f"{self.prefix}-src-{_h(text)}"
            made, new = self.emit({"op": "add_source", "key": key, "title": text.split("\n")[0][:200]})
            self.report["sources"] += new
            self.sources[hid] = "key:" + made
            if "\n" in text or len(text) > 200:
                self.note_text(text, {"source": "key:" + made}, f"{key}-text")
        return self.sources[hid]

    def repository(self, line: Line, source_key: str) -> None:
        rec = self.ged.by_xref.get(line.pointer or "")
        if rec is None or not rec.val("NAME"):
            return
        rkey = f"{self.prefix}-{_slug(rec.xref)}"
        rkey, _ = self.emit({"op": "add_repository", "key": rkey, "name": rec.val("NAME"), "type": "Unknown"})
        link = {"op": "link_repository", "key": f"{source_key}-repo-{_slug(rec.xref)}", "source": "key:" + source_key,
                "repository": "key:" + rkey}
        if line.val("CALN"):
            link["call_number"] = line.val("CALN")
        self.emit(link)

    def cite(self, owner: Line, target: dict) -> None:
        for s in owner.all("SOUR"):
            src = self.source(s)
            if not src:
                continue
            page = s.val("PAGE")
            conf = QUAY.get(s.val("QUAY"), 2)
            data = s.first("DATA")
            date = None
            if data and data.val("DATE"):
                date, why = ged_date(data.val("DATE"))
                if why:
                    self.report["date_problems"].append({"line": data.lineno, "text": data.val("DATE"), "problem": why})
            ident = (src, page, conf, date)
            cit = self.citations.get(ident)
            if cit is None:
                used_pages = {(c["source"], c["page"]) for c in self.citations.values()}
                shown = page
                if (src, page) in used_pages:  # gwt-apply wants one confidence and date per source and page
                    shown = f"{page} [quality {s.val('QUAY') or 'none'}{', ' + date if date else ''}]".strip()
                cit = {"op": "add_citation", "key": f"{self.prefix}-cit-{_h(*ident)}", "source": src, "page": shown,
                       "attach": [], "confidence": conf}
                if date:
                    cit["date"] = date
                self.citations[ident] = cit
                self.report["citations"] += 1
            if target not in cit["attach"]:
                cit["attach"].append(target)
            text = data.val("TEXT") if data else ""
            if text:
                self.note_text(text, {"citation": "key:" + cit["key"]}, f"{cit['key']}-text")
            for n in s.all("NOTE", "SNOTE"):
                self.note(n, {"citation": "key:" + cit["key"]})

    def note(self, line: Line, target: dict) -> None:
        x = line.pointer
        if x:
            rec = self.ged.by_xref.get(x)
            if rec is None:
                self.report["problems"].append(f"line {line.lineno}: NOTE points at {x}, which is not in the file")
                return
            self.note_text(rec.value, target, f"{self.prefix}-{_slug(x)}")
        else:
            self.note_text(line.value, target, None)

    def note_text(self, text: str, target: dict, key: str | None) -> None:
        text = text.strip()
        if not text:
            return
        ident = key or _h(text)
        n = self.notes.get(ident)
        if n is None:
            n = self.notes[ident] = {"op": "add_note", "key": key or f"{self.prefix}-note-{_h(text, repr(target))}",
                                     "text": text, "attach": []}
            self.report["notes"] += 1
        if target not in n["attach"]:
            n["attach"].append(target)

    # --- events --------------------------------------------------------------------------------------------
    def events(self, rec: Line, owner: dict, kinds: dict[str, str], key: str, label: str) -> None:
        seen: dict[str, str] = {}
        for i, ev in enumerate(rec.children):
            etype = kinds.get(ev.tag) or (ev.val("TYPE") if ev.tag in CUSTOM else None)
            if not etype:
                continue
            raw = ev.val("DATE")
            date, why = ged_date(raw)
            if why:
                self.report["date_problems"].append({"line": ev.lineno, "who": label, "event": etype,
                                                     "text": raw, "problem": why, "kept_as": date})
            pref, as_written = self.place(ev.first("PLAC"), f"{label} {etype}")
            desc = []
            value = ev.value.strip()
            if value and value.upper() != "Y":
                desc.append(value)
            if ev.tag not in CUSTOM and ev.val("TYPE"):
                desc.append(ev.val("TYPE"))
            if as_written:
                desc.append(f"Place as written: {as_written}")
            if "person" in owner and etype in SINGLE and etype in seen:
                text = f"Another {etype.lower()} in the GEDCOM file: " + ", ".join(
                    x for x in (raw, ev.val("PLAC"), *desc) if x)
                self.note_text(text, owner, None)
                self.report["second_vitals"].append({"who": label, "event": etype, "line": ev.lineno})
                continue
            op = {"op": "add_event", "key": f"{key}-ev{i}", **owner, "type": etype}
            if date:
                op["date"] = date
            if pref:
                op["place"] = pref
            if desc:
                op["description"] = "; ".join(desc)
            made, new = self.emit(op)
            if new:
                seen[etype] = made
                self.report["events"] += 1
            else:  # the same event twice in the file: one event, with the sources of both
                self.report["duplicate_events"] += 1
            self.cite(ev, {"event": "key:" + made})
            for n in ev.all("NOTE", "SNOTE"):
                self.note(n, {"event": "key:" + made})

    def external_ids(self, p: Person) -> None:
        found = []
        for tag, system in EXTERNAL.items():
            for line in p.rec.all(tag):
                if system and line.value.strip():
                    found.append((system, line.value.strip()))
        for line in p.rec.all("EXID"):
            kind = line.val("TYPE")
            system = next((name for host, name in EXID_SYSTEMS if host in kind), None) or kind or "EXID"
            if line.value.strip():
                found.append((system, line.value.strip()))
        for system, value in found:
            if _is_familysearch(system) and not re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{2,4}", value.upper()):
                self.report["external_ids_refused"].append({"xref": p.rec.xref, "system": system, "id": value})
                continue
            self.emit({"op": "set_external_id", "key": f"{p.key}-id-{_h(system, value)}", "target": {"person": p.ref},
                       "system": system, "id": value})
            self.report["external_ids"] += 1

    # --- families ------------------------------------------------------------------------------------------
    def add_families(self) -> None:
        couples: list[tuple[Couple, tuple[str, str]]] = []  # families with both parents this list makes
        for f in self.ged.families:
            def usable(x):
                p = self.people.get(x or "")
                return p.ref if p and p.held in (None, "same") else None
            husb = [c.pointer for c in f.all("HUSB")]
            wife = [c.pointer for c in f.all("WIFE")]
            kids = [c.pointer for c in f.all("CHIL") if c.pointer]
            if self.only is not None and not ({*husb, *wife, *kids} & self.only):
                continue
            father = usable(husb[0]) if husb else None
            mother = usable(wife[0]) if wife else None
            if father and mother and father == mother:
                mother = None
            dropped = [x for x in [*husb[:1], *wife[:1], *kids] if x and not usable(x)]
            if not (father or mother):
                self.report["families_skipped"].append({"xref": f.xref, "why": "no parent that is added",
                                                        "held": dropped})
                continue
            key = f"{self.prefix}-{_slug(f.xref)}"
            births, others = [], []
            for c in kids:
                ref = usable(c)
                if not ref:
                    continue
                rel = self.pedigree(c, f.xref)
                (births if rel == "Birth" else others).append((ref, rel))
            op = {"op": "add_family", "key": key}
            if father:
                op["father"] = father
            if mother:
                op["mother"] = mother
            if births:
                op["children"] = [r for r, _ in births]
            if father and mother:
                # two look-alike couples the file keeps apart, as with look-alike people: preflight wants that said
                hw = [self.people[x] for x in (husb[0], wife[0])]
                mine = Couple("key:" + key, *(Who(q.ref, q.first, q.surname) for q in hw))
                twins = [c.ref for c, pair in couples if pair != (father, mother) and _same_couple(mine, c)]
                if twins:
                    op["allow_new_couple"] = True
                    op["new_couple_reason"] = f"a separate family record of the GEDCOM file ({f.xref})"
                    op["new_couple_of"] = twins
                    self.report["file_couples"].append({"xref": f.xref, "of": [t[4:] for t in twins]})
                couples.append((mine, (father, mother)))
            if not self.emit(op)[1]:
                continue
            self.report["families"] += 1
            if dropped:
                self.report["held"].append({"xref": f.xref, "why": "family made without held members",
                                            "members": dropped})
            for ref, rel in others:
                self.emit({"op": "link_child", "key": f"{key}-child-{_h(ref)}", "family": "key:" + key, "child": ref,
                           "frel": rel, "mrel": rel})
            self.events(f, {"family": "key:" + key}, FAMILY_EVENTS, key, f.xref)
            self.cite(f, {"family": "key:" + key})
            for n in f.all("NOTE", "SNOTE"):
                self.note(n, {"family": "key:" + key})
            self.unused(f, USED["FAM"])

    def pedigree(self, child: str, fam: str) -> str:
        p = self.people[child]
        for c in p.rec.all("FAMC"):
            if c.pointer == fam:
                return PEDI.get(c.val("PEDI").lower(), "Birth")
        return "Birth"

    # --- the whole run -------------------------------------------------------------------------------------
    def build(self) -> tuple[list[dict], dict]:
        self.read_people()
        self.hold_living()
        self.match_tree()
        self.add_people()
        for x, p in self.people.items():
            if p.held:
                continue
            self.events(p.rec, {"person": p.ref}, PERSON_EVENTS, p.key, x)
            self.cite(p.rec, {"person": p.ref})
            for n in p.rec.all("NOTE", "SNOTE"):
                self.note(n, {"person": p.ref})
            for name in p.rec.all("NAME"):
                self.cite(name, {"person": p.ref})
            self.external_ids(p)
            self.unused(p.rec, USED["INDI"])
        self.add_families()
        media = sum(1 for r in self.ged.records if r.tag == "OBJE")  # links to them are counted as INDI.OBJE...
        if media:
            self.report["not_imported"]["OBJE records (media: the files are not in a GEDCOM)"] = media
        for op in [*self.citations.values(), *self.notes.values()]:
            self.emit(op)
        bad = validate(self.ops)
        if bad:  # a bug of this importer, never the reviewer's to fix
            raise ValueError("the change list does not validate: " + "; ".join(bad[:5]))
        if self.check_tree:
            self.report["preflight"] = preflight(self.ops, self.client)
        return self.ops, self.report


def build(ged: Gedcom, snap: dict, **kw) -> tuple[list[dict], dict]:
    return Builder(ged, snap, **kw).build()
