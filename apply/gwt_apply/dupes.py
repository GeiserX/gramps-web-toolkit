"""Pre-flight duplicate pass: every new person who looks like someone already there, before any write.

A new person is flagged when another person (in the tree, or made by another op of the list) has:

- the same first name and surname (a namesake), or
- the same first given name and first surname, and a birth or baptism year within two years,
  a parent in common, or a spouse in common.

unless the op says `allow_namesake` with a `namesake_reason` and names that person in `namesake_of`.
Names are compared without case, accents or extra spaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .dates import DateError, parse_date
from .ops import _ref_list, fold, stable_handle

BIRTHLIKE = {"birth", "baptism", "christening"}


@dataclass
class Who:
    ref: str  # Gramps ID, or key:<key> for a person an op makes
    first: str
    surname: str
    handle: str = ""
    years: set[int] = field(default_factory=set)
    parents: set[str] = field(default_factory=set)
    spouses: set[str] = field(default_factory=set)

    @property
    def full(self) -> tuple[str, str]:
        return (fold(self.first), fold(self.surname))

    @property
    def loose(self) -> tuple[str, str]:
        first, sur = fold(self.first).split(), fold(self.surname).split()
        return (first[0] if first else "", sur[0] if sur else "")

    def label(self) -> str:
        return f"{self.ref} ({self.first} {self.surname}{', ' + '/'.join(map(str, sorted(self.years))) if self.years else ''})"


def _year(date) -> int | None:
    if isinstance(date, str):
        try:
            date = parse_date(date)
        except DateError:
            return None
    if not isinstance(date, dict):
        return None
    val = date.get("dateval") or []
    return val[2] if len(val) > 2 and val[2] else None


def tree_people(client) -> tuple[list[Who], dict[str, set[str]]]:
    """Everyone in the tree, with birth years, parents and spouses, and each family's parents, from three list reads."""
    people = client.list_all("Person", ["primary_name", "gramps_id", "event_ref_list", "parent_family_list", "family_list"])
    events = {e["handle"]: e for e in client.list_all("Event", ["type", "date"])}
    families = {f["handle"]: f for f in client.list_all("Family", ["father_handle", "mother_handle", "gramps_id"])}
    ids = {p["handle"]: p.get("gramps_id") or p["handle"] for p in people}
    family_parents = {
        f.get("gramps_id") or h: {ids[x] for x in (f.get("father_handle"), f.get("mother_handle")) if x in ids}
        for h, f in families.items()
    }
    out = []
    for p in people:
        n = p.get("primary_name") or {}
        surname = " ".join(s.get("surname", "") for s in n.get("surname_list") or [])
        who = Who(ids[p["handle"]], n.get("first_name", ""), surname, handle=p["handle"])
        for ref in p.get("event_ref_list") or []:
            ev = events.get(ref.get("ref"))
            if ev and str(ev.get("type")).lower() in BIRTHLIKE and ref.get("role", "Primary") == "Primary":
                y = _year(ev.get("date"))
                if y:
                    who.years.add(y)
        for fh in p.get("parent_family_list") or []:
            fam = families.get(fh) or {}
            who.parents |= {ids[h] for h in (fam.get("father_handle"), fam.get("mother_handle")) if h in ids}
        for fh in p.get("family_list") or []:
            fam = families.get(fh) or {}
            who.spouses |= {ids[h] for h in (fam.get("father_handle"), fam.get("mother_handle")) if h in ids} - {who.ref}
        out.append(who)
    return out, family_parents


def list_people(ops: list[dict], family_parents: dict[str, set[str]]) -> dict[str, Who]:
    """The people the list makes, keyed by key:<key>, with what the list says about them.

    `family_parents` maps a family reference (Gramps ID) to its parents' Gramps IDs, for link_child.
    """
    new = {f"key:{op['key']}": Who(f"key:{op['key']}", op["first_name"], op["surname"])
           for op in ops if op["op"] == "add_person"}
    fams = dict(family_parents)
    for op in ops:
        if op["op"] == "add_family":
            parents = {op[r] for r in ("father", "mother") if op.get(r)}
            fams[f"key:{op['key']}"] = parents
            for kid in op.get("children") or []:
                if kid in new:
                    new[kid].parents |= parents
            for r in parents:
                if r in new:
                    new[r].spouses |= parents - {r}
    for op in ops:
        if op["op"] == "add_event" and op.get("person") in new and str(op["type"]).lower() in BIRTHLIKE:
            if op.get("role", "Primary") == "Primary":
                y = _year(op.get("date"))
                if y:
                    new[op["person"]].years.add(y)
        if op["op"] == "link_child" and op["child"] in new:
            new[op["child"]].parents |= fams.get(op["family"], set())
    return new


def _alike(a: Who, b: Who) -> str | None:
    if a.full == b.full:
        return "same name"
    if a.loose != b.loose or not all(a.loose):
        return None
    if any(abs(x - y) <= 2 for x in a.years for y in b.years):
        return "same first name and first surname, born within two years"
    if a.parents & b.parents:
        return "same first name and first surname, a parent in common"
    if a.spouses & b.spouses:
        return "same first name and first surname, a spouse in common"
    return None


def preflight(ops: list[dict], client) -> list[str]:
    """Every likely duplicate the list would create, as one message each. Reads only."""
    if not any(op["op"] == "add_person" for op in ops):
        return []
    tree, family_parents = tree_people(client)
    # people an earlier run of this list already made are the ops themselves, not duplicates of them
    made = {stable_handle(op["key"]) for op in ops if op["op"] == "add_person"}
    tree = [w for w in tree if w.handle not in made]
    ids = {w.ref for w in tree}
    new = list_people(ops, family_parents)
    by_loose: dict[tuple[str, str], list[Who]] = {}
    for w in tree:
        by_loose.setdefault(w.loose, []).append(w)
    problems = []
    by_key = {f"key:{op['key']}": op for op in ops if op["op"] == "add_person"}
    seen: list[Who] = []
    for ref, who in new.items():
        op = by_key[ref]
        compared = set(_ref_list(op.get("namesake_of") or [])) if op.get("allow_namesake") else set()
        # key:<key> of a person an earlier list made: that person is in the tree under its own Gramps ID
        compared |= {stable_handle(r[4:]) for r in compared if r.startswith("key:")}
        candidates = by_loose.get(who.loose, []) + [w for w in seen if w.loose == who.loose or w.full == who.full]
        for other in candidates:
            why = _alike(who, other)
            if why is None or other.ref in compared or other.handle in compared:
                continue
            if other.ref in new and ref in set(_ref_list(by_key[other.ref].get("namesake_of") or [])):
                continue  # the earlier op already says these two are different people
            where = "in the tree" if other.ref in ids else "made by another op"
            problems.append(f"op {op['key']!r}: {who.label()} looks like {other.label()} {where} ({why}); "
                            "use that person, or set allow_namesake with namesake_reason and namesake_of naming them")
        seen.append(who)
    return problems
