"""Whole-tree checks. Each finding is (check, key, detail); the key is stable across runs."""

from __future__ import annotations

import datetime
import re
import unicodedata
from collections import defaultdict

from . import dates
from .tree import Tree

# list of handles -> type they point at
HANDLE_LISTS = {
    "citation_list": "citations",
    "note_list": "notes",
    "family_list": "families",
    "parent_family_list": "families",
    "tag_list": "tags",
}
# list of {"ref": handle, ...} -> type they point at
REF_LISTS = {
    "event_ref_list": "events",
    "child_ref_list": "people",
    "person_ref_list": "people",
    "media_list": "media",
    "placeref_list": "places",
    "reporef_list": "repositories",
}
# single handle -> type
SCALARS = {
    "father_handle": "people",
    "mother_handle": "people",
    "source_handle": "sources",
    "place": "places",
    "famc": "families",
}
# what makes two refs in one list "the same ref twice"
REF_IDENTITY = {
    "event_ref_list": ("ref", "role"),
    "media_list": ("ref", "rect"),
    "person_ref_list": ("ref", "rel"),
}
SKIP_KEYS = {"text", "extended", "profile", "backlinks", "date"}
VITAL = {"Birth", "Death", "Baptism", "Burial", "Marriage"}
# left out when one name is compared with another (after norm(), so «y» is already «i»)
PARTICLES = {"de", "del", "la", "las", "los", "i", "e"}

CHECKS = {
    "orphan-event": "event no person or family refers to",
    "orphan-citation": "citation nothing refers to",
    "orphan-note": "note nothing refers to",
    "orphan-media": "media object nothing refers to",
    "orphan-repository": "repository no source lists",
    "dangling-ref": "reference to an object that does not exist",
    "place-not-place": "a place field that holds something other than a place",
    "citation-no-source": "citation without a source",
    "birth-death-pointer": "birth or death pointer wrong, out of range or missing",
    "repeated-ref": "the same reference twice in one list",
    "family-backlink": "person and family disagree about who is in it",
    "empty-family": "family with no father, mother or child",
    "dup-couple": "two families with the same father and mother",
    "dup-couple-names": "two families whose couples have the same two names",
    "dup-person-birth": "two people with the same name and a birth or baptism in the same year",
    "dup-person-relative": "two people with the same name and a parent, spouse or child of the same name",
    "dup-person-fuzzy": "two people whose names differ by an extra given name or surname, born within 2 years "
                        "or with a parent, spouse or child of the same name",
    "dup-vital-event": "one person or family with two vital events of one type",
    "living-media-sensitive": "media of a possibly living person, not marked private, whose path, description or "
                              "attributes look like an ID document, passport or home address",
    "dup-repository": "two repositories with the same name, compared like people's names",
    "dup-media": "two media objects with the same file: the same checksum",
    "dup-citation": "two citations of one source with the same page text, ignoring case and spacing",
    "private": "object or sub-reference marked private",
    "place-no-coords": "place used by an event without coordinates",
    "surname-marker": "surname with a « [ » marker",
    "note-marker-no-tag": "note whose text says «hipótesis» or «homónimo» but lacks the tag of that name, in a "
                          "tree that has such a tag",
    "missing-object": "object of the earlier tree that is gone",
    "ahnentafel-lost": "Ahnentafel number of the earlier tree that is gone",
    "ahnentafel-changed": "Ahnentafel number that now holds another person",
    "ahnentafel-truncated": "pedigree too large to compare whole; only the generations named were compared",
    "unconnected": "family component with no family link to the root's component (or the largest one)",
    "event-order": "a person's own dates in an impossible order: death before birth or baptism, baptism before "
                   "birth, burial before death",
    "child-before-parent": "child born before a parent",
    "child-after-parent-death": "child born after the mother's death, or more than 300 days after the father's",
    "marriage-before-birth": "marriage before a spouse was born",
    "marriage-after-death": "marriage after a spouse's death",
    "own-ancestor": "a person who is their own ancestor",
    "parent-age": "mother under 14 or over 52, or father under 14 or over 85, at a child's birth",
    "marriage-age": "bride under 12 or groom under 14 at the marriage",
    "sibling-spacing": "two children of one mother born less than 240 days apart, not twins",
    "lifespan": "a life of more than 105 years",
}

# Checks reported without failing: a finding of these is never new, so it never sets the exit code.
INFO_CHECKS = {"unconnected", "parent-age", "marriage-age", "sibling-spacing", "lifespan", "dup-citation",
               "note-marker-no-tag"}

# words in a note that a tag of the same stem is meant to mark; matched without accents, whole word
NOTE_MARKERS = ("hipotesis", "homonimo")
# a marker with one of these words within three words after it («hipótesis descartada») or before it
# («queda descartada la hipótesis») was discarded or replaced and no longer needs its tag. A «no», «ni»,
# «sin» or «nada» between them denies the discard: «hipótesis no descartada» is still open. All in one sentence
_DISCARD = r"(?:descartad|refutad|superad|sustituid|desmentid)[oa]s?"
_SEP = r"[^\w.;:!?]+"                      # the words must sit in one sentence
_GAP = rf"(?:(?!(?:no|ni|sin|nada)\b)\w+{_SEP}){{0,3}}"
NOTE_DISCARDED_AFTER = re.compile(rf"{_SEP}{_GAP}{_DISCARD}\b")
NOTE_DISCARDED_BEFORE = re.compile(rf"\b{_DISCARD}{_SEP}{_GAP}$")

# limits of the dated checks
POSTHUMOUS_DAYS = 300                       # a child can be born this long after the father's death
PARENT_AGE = {"mother": (14, 52), "father": (14, 85)}
MARRIAGE_AGE = {0: 12, 1: 14}               # by gender; canon law allowed girls 12 and boys 14 until 1917
TWIN_DAYS = 1
MIN_SPACING = 240
MAX_LIFESPAN = 105
# child_ref relations whose parent is taken as the biological one
BIOLOGICAL = {"", "birth", "unknown"}


# What gives away an identity document or a home address. Metadata only: the check cannot read pixels.
# Spanish first. Another country adds its ID number as a function to ID_NUMBERS (with its check digit, so
# a dated file name does not match) and its words to ID_WORDS.
def spanish_id(text: str) -> bool:
    """A DNI (8 digits) or NIE (X, Y or Z and 7 digits) whose check letter is right."""
    for m in re.finditer(r"(?<![A-Za-z0-9])([XYZ]\d{7}|\d{8})([A-Z])(?![A-Za-z0-9])", text, re.I):
        n = m.group(1).upper()
        n = str("XYZ".index(n[0])) + n[1:] if n[0] in "XYZ" else n
        if "TRWAGMYFPDXBNJZSQVHLCKE"[int(n) % 23] == m.group(2).upper():
            return True
    return False


ID_NUMBERS = (spanish_id,)
ID_WORDS = re.compile(r"\b(?:pasaporte|passport|domicilio|empadronamiento|carnet de identidad|identity card|"
                      r"id card|carte d.identite|home address)\b|(?<![\w/])c/ ?[A-Za-z]", re.I)
SENSITIVE_PATTERNS = (
    ("id number", lambda s: any(f(s) for f in ID_NUMBERS)),
    ("id or address word", ID_WORDS.search),
)
# a person with no death event is taken as living if born less than this many years ago, or never dated
LIVING_YEARS = 110


def sensitive_kind(*texts: str) -> str:
    """Name the first kind of sensitive text found, never the text itself."""
    for text in texts:
        flat = "".join(c for c in unicodedata.normalize("NFD", text or "") if unicodedata.category(c) != "Mn")
        for kind, found in SENSITIVE_PATTERNS:
            if found(flat):
                return kind
    return ""


def norm(s: str) -> str:
    """Lowercase, no accents, b/v and i/y folded, the particle «de» dropped."""
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    s = f" {' '.join(s.split())} ".replace(" de ", " ").replace("y", "i").replace("b", "v")
    return " ".join(s.split())


def person_name(p: dict) -> str:
    n = p.get("primary_name") or {}
    return norm(" ".join([n.get("first_name", "")] + [s.get("surname", "") for s in n.get("surname_list", [])]))


class Findings:
    def __init__(self):
        self.items: list[dict] = []
        self._seen: set[tuple[str, str]] = set()

    def add(self, check: str, key: str, detail: str = "") -> None:
        if (check, key) not in self._seen:
            self._seen.add((check, key))
            self.items.append({"check": check, "key": key, "detail": detail})


def id_order(gid: str) -> tuple:
    """Gramps IDs in numeric order: I9001 before I10000. The ID itself breaks a tie (O01 and O001), so a finding key
    does not depend on the order the dump lists the objects in."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", gid)], gid


def run_checks(tree: Tree, scope: set[tuple[str, str]] | None = None,
               root: str | None = None) -> tuple[list[dict], dict]:
    """Structural checks over the whole tree; duplicate checks need one object of each pair in scope.

    `scope` is the set of (type, handle) a run created or changed; None means every object.
    `root` is the Gramps ID whose family component is the main one; None means the largest.
    """
    T = tree.objs
    out = Findings()
    info: dict = {}
    in_scope = (lambda t, h: True) if scope is None else (lambda t, h: (t, h) in scope)
    referenced: dict[str, set] = defaultdict(set)

    def label(t: str, h: str) -> str:
        return T[t][h].get("gramps_id") or h if h in T[t] else h

    def walk(x, owner: str, path: str):
        if isinstance(x, list):
            for y in x:
                walk(y, owner, path)
            return
        if not isinstance(x, dict):
            return
        if x.get("private") is True:
            out.add("private", f"{owner} {path or '(object)'}")
        for k, v in x.items():
            p = f"{path}.{k}" if path else k
            if k in HANDLE_LISTS and isinstance(v, list):
                t = HANDLE_LISTS[k]
                if t == "tags" and not tree.has_tags:
                    continue
                seen = set()
                for h in v:
                    if h in seen:
                        out.add("repeated-ref", f"{owner} {p} {label(t, h)}")
                    seen.add(h)
                    if h in T[t]:
                        referenced[t].add(h)
                    else:
                        out.add("dangling-ref", f"{owner} {p} -> {t} {h}")
            elif k in REF_LISTS and isinstance(v, list):
                t = REF_LISTS[k]
                ident = REF_IDENTITY.get(k, ("ref",))
                seen = set()
                for r in v:
                    if not isinstance(r, dict):
                        continue
                    h = r.get("ref")
                    sig = tuple(repr(r.get(f)) for f in ident)
                    if sig in seen:
                        out.add("repeated-ref", f"{owner} {p} {label(t, h)}")
                    seen.add(sig)
                    if h in T[t]:
                        referenced[t].add(h)
                    else:
                        out.add("dangling-ref", f"{owner} {p} -> {t} {h}")
                    walk(r, owner, f"{p}[{label(t, h)}]")
            elif k in SCALARS:
                t = SCALARS[k]
                if not v:
                    if k == "source_handle":
                        out.add("citation-no-source", owner)
                elif v in T[t]:
                    referenced[t].add(v)
                elif k == "place":
                    other = next((tt for tt in T if v in T[tt]), None)
                    out.add("place-not-place", f"{owner} {p}", f"holds {other or 'nothing'} {v}")
                else:
                    out.add("dangling-ref", f"{owner} {p} -> {t} {v}")
            elif k not in SKIP_KEYS and isinstance(v, (dict, list)):
                walk(v, owner, p)

    for t, objs in T.items():
        for o in objs.values():
            walk(o, o.get("gramps_id") or o["handle"], "")

    # orphans: an event must hang from a person or a family, not only from a citation's backlink
    event_owner: set[str] = set()
    for t in ("people", "families"):
        for o in T[t].values():
            event_owner.update(r.get("ref") for r in o.get("event_ref_list", []))
    for t, check in (("events", "orphan-event"), ("citations", "orphan-citation"),
                     ("notes", "orphan-note"), ("media", "orphan-media"), ("repositories", "orphan-repository")):
        owners = event_owner if t == "events" else referenced[t]
        for h, o in T[t].items():
            if h not in owners:
                out.add(check, o.get("gramps_id") or h)

    # repositories: where each source is kept. A source is linked only when its repository is certain, so a
    # source without one is counted, not a finding; a tree with no repository at all does not use them.
    R = T["repositories"]
    if R:
        info["sources without repository"] = sum(1 for o in T["sources"].values() if not o.get("reporef_list"))
    # the same file entered twice: Gramps keeps an md5 checksum per media object, the same identity gwt-apply's
    # add_media uses to skip a file already in the tree. Empty means the file was never read: nothing to compare
    by_checksum: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for h, o in T["media"].items():
        if o.get("checksum"):
            by_checksum[o["checksum"]].append((h, o.get("gramps_id") or h))
    for group in by_checksum.values():
        if len(group) > 1 and any(in_scope("media", h) for h, _ in group):
            out.add("dup-media", ",".join(sorted((i for _, i in group), key=id_order)), "same checksum")
    by_name: dict[str, list[str]] = defaultdict(list)
    for h, o in R.items():
        if norm(o.get("name") or ""):
            by_name[norm(o["name"])].append(o.get("gramps_id") or h)
    for ids in by_name.values():
        if len(ids) > 1:
            out.add("dup-repository", ",".join(sorted(ids)))

    # citations of one record entered twice: same source, same page. Not folded further (no b/v, no accents),
    # since "folio 12v" and "folio 12" are different pages
    by_page: dict[tuple, list[dict]] = defaultdict(list)
    for c in T["citations"].values():
        page = " ".join((c.get("page") or "").casefold().split())
        if page and c.get("source_handle"):
            by_page[(c["source_handle"], page)].append(c)
    for cs in by_page.values():
        if len(cs) > 1 and any(in_scope("citations", c["handle"]) for c in cs):
            out.add("dup-citation", ",".join(sorted((c.get("gramps_id") or c["handle"] for c in cs), key=id_order)))

    # birth and death pointers
    E = T["events"]
    for p in T["people"].values():
        erl = p.get("event_ref_list", [])
        for field, typ in (("birth_ref_index", "Birth"), ("death_ref_index", "Death")):
            i = p.get(field, -1)
            primaries = [r for r in erl if r.get("role") == "Primary" and E.get(r.get("ref"), {}).get("type") == typ]
            gid = p.get("gramps_id")
            if i == -1:
                if primaries:
                    out.add("birth-death-pointer", f"{gid} {field}", f"Primary {typ} but the pointer is -1")
            elif not 0 <= i < len(erl):
                out.add("birth-death-pointer", f"{gid} {field}", f"{i} is out of range")
            else:
                r = erl[i]
                e = E.get(r.get("ref"))
                if not e or e.get("type") != typ or r.get("role") != "Primary":
                    got = f"{e.get('gramps_id')} {e.get('type')} {r.get('role')}" if e else "a missing event"
                    out.add("birth-death-pointer", f"{gid} {field}", f"points at {got}")

    # person <-> family, both ways
    P, F = T["people"], T["families"]
    for f in F.values():
        fid = f.get("gramps_id")
        for k in ("father_handle", "mother_handle"):
            h = f.get(k)
            if h in P and f["handle"] not in P[h].get("family_list", []):
                out.add("family-backlink", f"{fid} {k} {label('people', h)}", "missing from the person's family_list")
        for c in f.get("child_ref_list", []):
            h = c.get("ref")
            if h in P and f["handle"] not in P[h].get("parent_family_list", []):
                out.add("family-backlink", f"{fid} child {label('people', h)}", "missing from the person's parent_family_list")
        if not f.get("father_handle") and not f.get("mother_handle") and not f.get("child_ref_list"):
            out.add("empty-family", fid)
    for p in P.values():
        pid = p.get("gramps_id")
        for fh in p.get("family_list", []):
            f = F.get(fh)
            if f and p["handle"] not in (f.get("father_handle"), f.get("mother_handle")):
                out.add("family-backlink", f"{pid} family_list {f.get('gramps_id')}", "not the father or mother there")
        for fh in p.get("parent_family_list", []):
            f = F.get(fh)
            if f and p["handle"] not in [c.get("ref") for c in f.get("child_ref_list", [])]:
                out.add("family-backlink", f"{pid} parent_family_list {f.get('gramps_id')}", "no child_ref there")

    # couples
    by_handles: dict[tuple, list] = defaultdict(list)
    by_names: dict[tuple, list] = defaultdict(list)
    for f in F.values():
        a, b = f.get("father_handle"), f.get("mother_handle")
        if a in P and b in P:
            by_handles[(a, b)].append(f)
            by_names[(person_name(P[a]), person_name(P[b]))].append(f)
    for fs in by_handles.values():
        if len(fs) > 1:  # exact, so whole tree like the structural checks
            out.add("dup-couple", ",".join(sorted(f["gramps_id"] for f in fs)))
    for (na, nb), fs in by_names.items():
        if not na.strip() or not nb.strip():
            continue
        distinct = {(f["father_handle"], f["mother_handle"]) for f in fs}
        if len(distinct) > 1 and any(in_scope("families", f["handle"]) for f in fs):
            out.add("dup-couple-names", ",".join(sorted(f["gramps_id"] for f in fs)), f"{na} × {nb}")

    # people with the same name
    def births(p: dict) -> set[tuple[int, int, int]]:
        got = set()
        for r in p.get("event_ref_list", []):
            e = E.get(r.get("ref"))
            if not e or r.get("role") != "Primary" or e.get("type") not in ("Birth", "Baptism"):
                continue
            d = e.get("date") or {}
            dv = d.get("dateval") or [0, 0, 0]
            # plain and "about" dates only: "before 1800" says nothing about 1800
            if d.get("modifier", 0) in (0, 3) and dv[2]:
                got.add((dv[2], dv[1], dv[0]))
        return got

    def compatible(xs, ys) -> bool:
        return any(x[0] == y[0] and (not x[1] or not y[1] or x[1] == y[1]) and (not x[2] or not y[2] or x[2] == y[2])
                   for x in xs for y in ys)

    def kin(p: dict) -> tuple[set, set, set]:
        parents, spouses, children = set(), set(), set()
        for fh in p.get("parent_family_list", []):
            f = F.get(fh) or {}
            parents |= {person_name(P[h]) for h in (f.get("father_handle"), f.get("mother_handle")) if h in P}
        for fh in p.get("family_list", []):
            f = F.get(fh) or {}
            spouses |= {person_name(P[h]) for h in (f.get("father_handle"), f.get("mother_handle"))
                        if h in P and h != p["handle"]}
            children |= {person_name(P[c.get("ref")]) for c in f.get("child_ref_list", []) if c.get("ref") in P}
        return parents - {""}, spouses - {""}, children - {""}

    def parent_handles(p: dict) -> set:
        out_: set = set()
        for fh in p.get("parent_family_list", []):
            f = F.get(fh) or {}
            out_ |= {h for h in (f.get("father_handle"), f.get("mother_handle")) if h in P}
        return out_

    def parent_and_child(a: str, b: str) -> bool:
        """A person is never their own parent: a father and a son with one name are two people."""
        return a in parent_handles(P[b]) or b in parent_handles(P[a])

    names: dict[str, list] = defaultdict(list)
    for p in P.values():
        n = person_name(p)
        if len(n.split()) >= 2:  # a lone first name matches half the tree
            names[n].append(p)
    for n, group in names.items():
        if len(group) < 2 or not any(in_scope("people", p["handle"]) for p in group):
            continue
        facts = {p["handle"]: (births(p), *kin(p)) for p in group}
        for i, p in enumerate(group):
            for q in group[i + 1:]:
                if not (in_scope("people", p["handle"]) or in_scope("people", q["handle"])):
                    continue
                bp, pp, sp, cp = facts[p["handle"]]
                bq, pq, sq, cq = facts[q["handle"]]
                pair = ",".join(sorted((p["gramps_id"], q["gramps_id"])))
                if bp and bq and compatible(bp, bq):
                    out.add("dup-person-birth", pair, n)
                years_differ = bp and bq and not ({x[0] for x in bp} & {x[0] for x in bq})
                # a shared relative proves nothing when one is the other's parent; a shared birth date still does
                if (pp & pq or sp & sq or cp & cq) and not years_differ \
                        and not parent_and_child(p["handle"], q["handle"]):
                    why = "same parent" if pp & pq else "same spouse" if sp & sq else "same child"
                    out.add("dup-person-relative", pair, f"{n}, {why}")

    # the same person written with an extra given name or surname: «Vicente» and «Miguel Vicente»
    def tokens(s: str) -> frozenset:
        return frozenset(t for t in norm(s).split() if t not in PARTICLES)

    def name_parts(p: dict) -> tuple[frozenset, frozenset]:
        n = p.get("primary_name") or {}
        surnames = " ".join(s.get("surname", "") for s in n.get("surname_list", []))
        return tokens(n.get("first_name", "")), tokens(surnames)

    parts = {h: name_parts(p) for h, p in P.items()}
    by_surname: dict[str, list] = defaultdict(list)
    for h, (g, s) in parts.items():
        if g and s:
            for t in s:
                by_surname[t].append(h)
    facts: dict[str, tuple] = {}

    def fact(h: str) -> tuple:
        if h not in facts:
            facts[h] = ({x[0] for x in births(P[h])}, *kin(P[h]))
        return facts[h]

    def parents_by_name(h: str) -> dict[str, set]:
        got: dict[str, set] = defaultdict(set)
        for fh in P[h].get("parent_family_list", []):
            f = F.get(fh) or {}
            for x in (f.get("father_handle"), f.get("mother_handle")):
                if x in P:
                    got[person_name(P[x])].add(x)
        return got

    def birth_parents(h: str) -> tuple[str, str] | None:
        """Names of the father and mother of the first family the person is a birth child of, both named."""
        for fh in P[h].get("parent_family_list", []):
            f = F.get(fh) or {}
            for c in f.get("child_ref_list", []):
                if c.get("ref") == h and dates.kind(c.get("frel")) in BIOLOGICAL \
                        and dates.kind(c.get("mrel")) in BIOLOGICAL:
                    fa, mo = (person_name(P[x]) if x in P else ""
                              for x in (f.get("father_handle"), f.get("mother_handle")))
                    return (fa, mo) if fa and mo else None
        return None

    skipped: set[str] = set()

    def touched(h: str) -> bool:
        """In scope, or in a family that is: an old person the run gave a new spouse or parents."""
        p = P[h]
        return in_scope("people", h) or any(
            in_scope("families", fh) for fh in p.get("family_list", []) + p.get("parent_family_list", []))

    for h, (g, s) in parts.items():
        if not (g and s and touched(h)):
            continue
        candidates = {c for t in s for c in by_surname[t]} - {h}
        for c in candidates:
            gc, sc = parts[c]
            if (g, s) == (gc, sc) or not ((g <= gc or gc <= g) and (s <= sc or sc <= s)):
                continue
            if touched(c) and c < h:  # both in scope: look at the pair once
                continue
            yp, pp, sp, cp = fact(h)
            yq, pq, sq, cq = fact(c)
            near = any(abs(a - b) <= 2 for a in yp for b in yq)
            # siblings share their parents: a parent counts only when each side has its own copy of them
            if pp & pq:
                ph, qh = parents_by_name(h), parents_by_name(c)
                pp = {n for n in pp & pq if ph[n] != qh[n]}
            kin_shared = (pp & pq or sp & sq or cp & cq) and not parent_and_child(h, c)
            if near or (kin_shared and not (yp and yq)):
                why = "birth within 2 years" if near else (
                    "same parent" if pp & pq else "same spouse" if sp & sq else "same child")
                pair = ",".join(sorted((P[h]["gramps_id"], P[c]["gramps_id"])))
                # namesakes: each has a father and a mother, and both differ by name, so two people
                bh, bc = birth_parents(h), birth_parents(c)
                if bh and bc and bh[0] != bc[0] and bh[1] != bc[1]:
                    skipped.add(pair)
                    continue
                out.add("dup-person-fuzzy", pair, f"{person_name(P[h])} / {person_name(P[c])}, {why}")

    info["dup-person-fuzzy skipped, parents differ by name"] = sorted(skipped, key=id_order)

    # two vital events of one type on one person or family
    for t, role in (("people", "Primary"), ("families", "Family")):
        for o in T[t].values():
            by_type: dict[str, list] = defaultdict(list)
            for r in o.get("event_ref_list", []):
                e = E.get(r.get("ref"))
                if e and e.get("type") in VITAL and r.get("role") == role:
                    by_type[e["type"]].append(e)
            for typ, es in by_type.items():
                if len(es) > 1 and (in_scope(t, o["handle"]) or any(in_scope("events", e["handle"]) for e in es)):
                    ids = ",".join(sorted(e["gramps_id"] for e in es))
                    out.add("dup-vital-event", f"{o['gramps_id']} {typ} {ids}")

    # places and events
    used = {e.get("place") for e in E.values() if e.get("place") in T["places"]}
    for h in used:
        pl = T["places"][h]
        if not (str(pl.get("lat") or "").strip() and str(pl.get("long") or "").strip()):
            out.add("place-no-coords", pl.get("gramps_id") or h)
    no_place = [e for e in E.values() if not e.get("place")]
    info["events without place"] = len(no_place)
    if scope is not None:
        info["events without place, in scope"] = sorted(
            e["gramps_id"] for e in no_place if in_scope("events", e["handle"]))

    # media of a possibly living person that looks like an ID document or an address
    this_year = datetime.date.today().year
    for p in P.values():
        if not p.get("media_list") or p.get("private"):
            continue
        # their own events only: a godparent at a 2001 baptism was not born in 2001
        events = [E.get(r.get("ref")) for r in p.get("event_ref_list", []) if r.get("role") == "Primary"]
        if any(e and e.get("type") in ("Death", "Burial") for e in events):
            continue
        years = [((e.get("date") or {}).get("dateval") or [0, 0, 0])[2] for e in events
                 if e and e.get("type") in ("Birth", "Baptism")]
        years = [y for y in years if y]
        if years and min(years) < this_year - LIVING_YEARS:
            continue
        for r in p["media_list"]:
            m = T["media"].get(r.get("ref"))
            if not m or r.get("private") or m.get("private"):  # already private: nothing left to fix
                continue
            if not (in_scope("people", p["handle"]) or in_scope("media", m["handle"])):
                continue
            attrs = [str(a.get("value", "")) for a in m.get("attribute_list", [])]
            kind = sensitive_kind(m.get("path", ""), m.get("desc", ""), *attrs)
            if kind:
                out.add("living-media-sensitive", f"{p['gramps_id']} {m['gramps_id']}", kind)

    # placeholder markers in surnames
    for p in P.values():
        for n in [p.get("primary_name") or {}] + (p.get("alternate_names") or []):
            for s in n.get("surname_list", []):
                if "[" in (s.get("surname") or ""):
                    out.add("surname-marker", f"{p['gramps_id']} {s['surname']}")

    # a note that announces a hypothesis or a namesake but carries no such tag. Only where the tree already
    # has a tag of that name: a tree that does not use the convention is not blamed for it
    def fold(t: str) -> str:
        return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")

    tag_names = {h: fold(t.get("name") or "") for h, t in T["tags"].items()}
    for stem in NOTE_MARKERS:
        word = re.compile(rf"\b{stem}s?\b")
        wanted = {h for h, name in tag_names.items() if word.search(name)}
        if not wanted:
            continue
        for n in T["notes"].values():
            if not in_scope("notes", n["handle"]) or wanted & set(n.get("tag_list") or []):
                continue
            text = n.get("text")
            text = text.get("string", "") if isinstance(text, dict) else str(text or "")
            text = fold(text)
            # each mention counts on its own: one discarded hypothesis does not hide another still open
            if any(not NOTE_DISCARDED_AFTER.match(text, m.end())
                   and not NOTE_DISCARDED_BEFORE.search(text[:m.start()]) for m in word.finditer(text)):
                out.add("note-marker-no-tag", f"{n['gramps_id']} {stem}")

    # family components: people joined by a family as father, mother or child. Godparents, witnesses and
    # associations do not join anyone, and people in no family are left out: they are not a batch
    parent: dict[str, str] = {}

    def find(h: str) -> str:
        while parent[h] != h:
            parent[h] = parent[parent[h]]
            h = parent[h]
        return h

    fam_member: dict[str, str] = {}
    for f in F.values():
        members = [f.get("father_handle"), f.get("mother_handle")]
        members += [r.get("ref") for r in f.get("child_ref_list", []) if isinstance(r, dict)]
        members = [m for m in members if m in P]
        for m in members:
            parent.setdefault(m, m)
        for m in members[1:]:
            parent[find(m)] = find(members[0])
        if members:
            fam_member[f["handle"]] = members[0]
    comps: dict[str, list[str]] = defaultdict(list)
    for h in parent:
        comps[find(h)].append(h)
    if len(comps) > 1:
        lowest = {c: min((label("people", h) for h in hs), key=id_order) for c, hs in comps.items()}
        root_h = next((h for h, p in P.items() if p.get("gramps_id") == root), None) if root else None
        main = find(root_h) if root_h in parent else min(comps, key=lambda c: (-len(comps[c]), id_order(lowest[c])))
        hit = set(comps) if scope is None else (
            {find(h) for t, h in scope if t == "people" and h in parent}
            | {find(fam_member[h]) for t, h in scope if t == "families" and h in fam_member})
        for c in sorted(hit - {main}, key=lambda c: id_order(lowest[c])):
            ids = sorted((label("people", h) for h in comps[c]), key=id_order)
            out.add("unconnected", lowest[c],
                    f"{len(ids)} people: {', '.join(ids[:5])}{', ...' if len(ids) > 5 else ''}; "
                    f"main component ({lowest[main]}) has {len(comps[main])}")

    dated_checks(T, out, info)
    return out.items, info


def dated_checks(T: dict, out: Findings, info: dict) -> None:
    """Dates that contradict each other. Whole tree, like the structural checks.

    Every date is an interval (dates.span); a finding needs the intervals to be disjoint, so a vague date only
    widens what is allowed. The detail says `firm` when every date behind it is a plain day, month or year.
    A person with no Birth event is taken as born up to a year before their baptism (an infant baptism).
    """
    P, F, E = T["people"], T["families"], T["events"]
    unread = 0

    def gid(t: str, h: str) -> str:
        return (T[t].get(h) or {}).get("gramps_id") or h

    def pick(p: dict, types: set, pointer: str | None):
        """(event, span) of the person's Primary event of these types: the pointed-at one, else the first readable."""
        nonlocal unread
        refs = [r for r in p.get("event_ref_list", []) if r.get("role") == "Primary"]
        events = [E.get(r.get("ref")) for r in refs]
        events = [e for e in events if e and dates.kind(e.get("type")) in types]
        i = p.get(pointer, -1) if pointer else -1
        erl = p.get("event_ref_list", [])
        pointed = E.get(erl[i].get("ref")) if 0 <= i < len(erl) else None
        if pointed in events:
            events.remove(pointed)
            events.insert(0, pointed)
        for e in events:
            s = dates.span(e.get("date"))
            if s:
                return e, s
            d = e.get("date") or {}
            if any((d.get("dateval") or [])[:3]) or d.get("text"):
                unread += 1
        return None

    vit: dict[str, dict] = {}
    for h, p in P.items():
        v = {"birth": pick(p, dates.BIRTH, "birth_ref_index"), "baptism": pick(p, dates.BAPTISM, None),
             "death": pick(p, dates.DEATH, "death_ref_index"), "burial": pick(p, dates.BURIAL, None)}
        vit[h] = {k: x for k, x in v.items() if x}

    def ev(kind_: str, pair) -> str:
        e, s = pair
        return f"{kind_} {e.get('gramps_id')} {s.text}"

    def firm(*spans) -> str:
        return "firm" if all(s.firm for s in spans) else "soft"

    def born(h: str):
        """(label, span) of a birth: the Birth event, else the baptism as its upper bound."""
        v = vit.get(h, {})
        if "birth" in v:
            return ev("Birth", v["birth"]), v["birth"][1]
        if "baptism" in v:
            return ev("Baptism", v["baptism"]), dates.birth_from_baptism(v["baptism"][1])
        return None

    def died(h: str):
        """(label, span) of a death: the Death event, else the burial, which bounds it from above."""
        v = vit.get(h, {})
        if "death" in v:
            return ev("Death", v["death"]), v["death"][1]
        if "burial" in v:
            return ev("Burial", v["burial"]), v["burial"][1]
        return None

    # a person's own events
    order = (("birth", "baptism"), ("birth", "death"), ("baptism", "death"), ("death", "burial"))
    for h, v in vit.items():
        pid = gid("people", h)
        for first, then in order:
            if first in v and then in v and v[then][1].hi < v[first][1].lo:
                a, b = v[first], v[then]
                out.add("event-order", f"{pid} {a[0].get('gramps_id')} {b[0].get('gramps_id')}",
                        f"{ev(then.capitalize(), b)} is before {ev(first.capitalize(), a)} ({firm(a[1], b[1])})")
        start = v.get("birth") or v.get("baptism")
        end = v.get("death") or v.get("burial")
        if start and end:
            least, _ = dates.years_between(start[1], end[1])
            if least > MAX_LIFESPAN:
                out.add("lifespan", pid, f"at least {least:.0f} years: {ev('from', start)} to {ev('to', end)} "
                                         f"({firm(start[1], end[1])})")

    # parents and children
    for fh, f in F.items():
        fid = f.get("gramps_id") or fh
        for role, ph in (("father", f.get("father_handle")), ("mother", f.get("mother_handle"))):
            if ph not in P:
                continue
            pb, pd = born(ph), died(ph)
            pg = gid("people", ph)
            for c in f.get("child_ref_list", []):
                ch = c.get("ref") if isinstance(c, dict) else None
                cb = born(ch) if ch in P else None
                if not cb or dates.kind(c.get("frel" if role == "father" else "mrel")) not in BIOLOGICAL:
                    continue
                key = f"{fid} {pg} {gid('people', ch)}"
                if pb and cb[1].hi < pb[1].lo:
                    out.add("child-before-parent", key, f"child {cb[0]} is before {role} {pb[0]} ({firm(cb[1], pb[1])})")
                elif pb:
                    young, old = PARENT_AGE[role]
                    least, most = dates.years_between(pb[1], cb[1])
                    if most < young:
                        out.add("parent-age", key, f"{role} at most {most:.1f} at the birth: {role} {pb[0]}, "
                                                   f"child {cb[0]} ({firm(cb[1], pb[1])})")
                    elif least > old:
                        out.add("parent-age", key, f"{role} at least {least:.1f} at the birth: {role} {pb[0]}, "
                                                   f"child {cb[0]} ({firm(cb[1], pb[1])})")
                if pd and cb[1].lo > pd[1].hi + (POSTHUMOUS_DAYS if role == "father" else 0):
                    out.add("child-after-parent-death", key,
                            f"child {cb[0]} is after {role} {pd[0]} ({firm(cb[1], pd[1])})")

    # marriages
    for fh, f in F.items():
        fid = f.get("gramps_id") or fh
        for r in f.get("event_ref_list", []):
            e = E.get(r.get("ref"))
            mar = e and dates.kind(e.get("type")) in dates.MARRIAGE and dates.span(e.get("date"))
            if not mar:
                continue
            ml = ev("Marriage", (e, mar))
            for role, ph in (("father", f.get("father_handle")), ("mother", f.get("mother_handle"))):
                if ph not in P:
                    continue
                key = f"{fid} {e.get('gramps_id')} {gid('people', ph)}"
                pb, pd = born(ph), died(ph)
                if pd and mar.lo > pd[1].hi:
                    out.add("marriage-after-death", key, f"{ml} is after {role} {pd[0]} ({firm(mar, pd[1])})")
                if pb and mar.hi < pb[1].lo:
                    out.add("marriage-before-birth", key, f"{ml} is before {role} {pb[0]} ({firm(mar, pb[1])})")
                elif pb:
                    least_age = MARRIAGE_AGE.get(P[ph].get("gender"), min(MARRIAGE_AGE.values()))
                    _, most = dates.years_between(pb[1], mar)
                    if most < least_age:
                        out.add("marriage-age", key, f"{role} at most {most:.1f} at {ml}, {pb[0]} "
                                                     f"({firm(mar, pb[1])})")

    # children of one mother too close together: Birth events with an exact day only
    kids: dict[str, dict] = defaultdict(dict)
    for f in F.values():
        mh = f.get("mother_handle")
        if mh not in P:
            continue
        for c in f.get("child_ref_list", []):
            ch = c.get("ref") if isinstance(c, dict) else None
            b = vit.get(ch, {}).get("birth")
            if b and b[1].firm and b[1].lo == b[1].hi and dates.kind(c.get("mrel")) in BIOLOGICAL:
                kids[mh][ch] = b
    for mh, got in kids.items():
        rows = sorted(got.items(), key=lambda x: x[1][1].lo)
        for i, (a, ba) in enumerate(rows):
            for b, bb in rows[i + 1:]:
                gap = bb[1].lo - ba[1].lo
                if gap >= MIN_SPACING:
                    break
                if gap > TWIN_DAYS:
                    pair = sorted((gid("people", a), gid("people", b)), key=id_order)
                    out.add("sibling-spacing", f"{gid('people', mh)} {','.join(pair)}",
                            f"{gap} days apart: {ev('Birth', ba)}, {ev('Birth', bb)}")

    # a person who is their own ancestor: a depth-first walk up the parents, without recursion
    parents: dict[str, list] = defaultdict(list)
    for f in F.values():
        for c in f.get("child_ref_list", []):
            ch = c.get("ref") if isinstance(c, dict) else None
            for par in (f.get("father_handle"), f.get("mother_handle")):
                if ch in P and par in P and par not in parents[ch]:
                    parents[ch].append(par)
    state: dict[str, int] = {}  # 1 on the current path, 2 done
    for start in sorted(parents):
        if start in state:
            continue
        path, pos, stack = [start], {start: 0}, [iter(sorted(parents[start]))]
        state[start] = 1
        while stack:
            nxt = next(stack[-1], None)
            if nxt is None:
                state[path[-1]] = 2
                del pos[path.pop()]
                stack.pop()
            elif state.get(nxt) == 1:
                cyc = [gid("people", x) for x in path[pos[nxt]:]]
                out.add("own-ancestor", ",".join(sorted(set(cyc), key=id_order)),
                        " -> ".join(cyc + [cyc[0]]) + " (each the child of the next)")
            elif nxt not in state:
                state[nxt] = 1
                pos[nxt] = len(path)
                path.append(nxt)
                stack.append(iter(sorted(parents.get(nxt, ()))))

    info["dates not read"] = unread
