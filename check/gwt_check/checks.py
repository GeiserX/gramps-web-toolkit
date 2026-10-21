"""Whole-tree checks. Each finding is (check, key, detail); the key is stable across runs."""

from __future__ import annotations

import datetime
import re
import unicodedata
from collections import defaultdict

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
    "living-media-sensitive": "media of a possibly living person whose title, path, description, attributes or "
                              "notes look like an ID document, passport or home address",
    "private": "object or sub-reference marked private",
    "place-no-coords": "place used by an event without coordinates",
    "surname-marker": "surname with a « [ » marker",
    "missing-object": "object of the earlier tree that is gone",
    "ahnentafel-lost": "Ahnentafel number of the earlier tree that is gone",
    "ahnentafel-changed": "Ahnentafel number that now holds another person",
}


# What gives away an identity document or a home address. Metadata only: the check cannot read pixels.
SENSITIVE_PATTERNS = (
    ("id number", re.compile(r"\b(?:\d{8}[A-Za-z]|[XYZxyz]\d{7}[A-Za-z])\b")),  # Spanish DNI / NIE
    ("id or address word", re.compile(
        r"\b(?:dni|nie|pasaporte|passport|domicilio|empadronamiento|carnet de identidad|"
        r"identity card|id card|carte d.identite|home address|direccion|calle|c/)\b", re.I)),
)
# a person with no death event is taken as living if born less than this many years ago, or never dated
LIVING_YEARS = 110


def sensitive_kind(*texts: str) -> str:
    """Name the first kind of sensitive text found, never the text itself."""
    for text in texts:
        flat = "".join(c for c in unicodedata.normalize("NFD", text or "") if unicodedata.category(c) != "Mn")
        for kind, rx in SENSITIVE_PATTERNS:
            if rx.search(flat):
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


def run_checks(tree: Tree, scope: set[tuple[str, str]] | None = None) -> tuple[list[dict], dict]:
    """Structural checks over the whole tree; duplicate checks need one object of each pair in scope.

    `scope` is the set of (type, handle) a run created or changed; None means every object.
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
                     ("notes", "orphan-note"), ("media", "orphan-media")):
        owners = event_owner if t == "events" else referenced[t]
        for h, o in T[t].items():
            if h not in owners:
                out.add(check, o.get("gramps_id") or h)

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
                if (pp & pq or sp & sq or cp & cq) and not years_differ:
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
            kin_shared = pp & pq or sp & sq or cp & cq
            if near or (kin_shared and not (yp and yq)):
                why = "birth within 2 years" if near else (
                    "same parent" if pp & pq else "same spouse" if sp & sq else "same child")
                pair = ",".join(sorted((P[h]["gramps_id"], P[c]["gramps_id"])))
                out.add("dup-person-fuzzy", pair, f"{person_name(P[h])} / {person_name(P[c])}, {why}")

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
        if not p.get("media_list"):
            continue
        events = [E.get(r.get("ref")) for r in p.get("event_ref_list", [])]
        if any(e and e.get("type") in ("Death", "Burial") for e in events):
            continue
        years = [(e.get("date") or {}).get("dateval", [0, 0, 0])[2] for e in events
                 if e and e.get("type") in ("Birth", "Baptism")]
        years = [y for y in years if y]
        if years and min(years) < this_year - LIVING_YEARS:
            continue
        for r in p["media_list"]:
            m = T["media"].get(r.get("ref"))
            if not m or not (in_scope("people", p["handle"]) or in_scope("media", m["handle"])):
                continue
            notes = [(T["notes"].get(h) or {}).get("text", {}).get("string", "") for h in m.get("note_list", [])]
            attrs = [str(a.get("value", "")) for a in m.get("attribute_list", [])]
            kind = sensitive_kind(m.get("path", ""), m.get("desc", ""), *attrs, *notes)
            if kind:
                out.add("living-media-sensitive", f"{p['gramps_id']} {m['gramps_id']}", kind)

    # placeholder markers in surnames
    for p in P.values():
        for n in [p.get("primary_name") or {}] + (p.get("alternate_names") or []):
            for s in n.get("surname_list", []):
                if "[" in (s.get("surname") or ""):
                    out.add("surname-marker", f"{p['gramps_id']} {s['surname']}")

    return out.items, info
