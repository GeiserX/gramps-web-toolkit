"""What a guest can see of a tree, compared with the whole tree: a report that changes nothing.

Two dumps of the same tree, one read with an owner login and one with a guest login (the role that cannot see
private records), say what the guest still gets: an object the owner marked private, a visible object that points
at a private one, a private person's name written in a visible text, a possibly living person. Findings carry
Gramps IDs and kinds, never the text that leaked."""

from __future__ import annotations

import datetime
import re

from .checks import id_order, norm, person_name, possibly_living
from .tree import TYPES, BadInput, Tree

# text fields a name can leak into, by type
TEXT_FIELDS = {
    "notes": lambda o: [(o.get("text") or {}).get("string", "")],
    "citations": lambda o: [o.get("page", "")],
    "events": lambda o: [o.get("description", "")],
    "media": lambda o: [o.get("desc", ""), o.get("path", "")],
    "sources": lambda o: [o.get("title", ""), o.get("author", ""), o.get("pubinfo", "")],
}
# the kinds that mean the server shows a guest what the owner hid; living-visible is the owner's own call (Gramps Web
# never hides a person for being alive), so it is reported but does not set the exit code
LEAKS = ("private-visible", "private-reference", "private-name")
MIN_NAME_CHARS = 8  # a one-word or very short name is too common to say anything


def _words(s: str) -> str:
    """norm() on the words alone: punctuation is a word break, so «Name.» and «(Name)» match like «Name»."""
    return norm(re.sub(r"[^\w]+", " ", s))


def _private_parts(x, path: str = ""):
    """Paths of the sub-objects marked private inside one object (a name, an event or child reference, an
    attribute), the way the `private` rule of `gwt-check check` walks them."""
    if isinstance(x, list):
        for y in x:
            yield from _private_parts(y, path)
    elif isinstance(x, dict):
        if path and x.get("private") is True:
            yield path
        for k, v in x.items():
            yield from _private_parts(v, f"{path}.{k}" if path else k)


def _strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            if k != "handle":
                yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)


def public_report(owner: Tree, guest: Tree, *, this_year: int | None = None) -> dict:
    """Findings, in a fixed order: private-visible, private-reference, private-name, living-visible. `leaks` counts
    the rows of the LEAKS kinds."""
    this_year = this_year or datetime.date.today().year
    if owner.tree_id and guest.tree_id and owner.tree_id != guest.tree_id:
        raise BadInput(f"{guest.source} was read from another tree than {owner.source}")
    if not any(guest.objs[t] for t in TYPES):
        raise BadInput(f"{guest.source}: the guest dump is empty; a guest that sees nothing compares nothing")
    for t in TYPES:  # a guest dump that is not the same tree would compare nothing
        extra = set(guest.objs[t]) - set(owner.objs[t])
        if extra:
            raise BadInput(f"{guest.source}: {len(extra)} {t} not in {owner.source}; are they the same tree?")
    private = {t: {h for h, o in owner.objs[t].items() if o.get("private") is True} for t in TYPES}
    private_handles = {h: t for t, hs in private.items() for h in hs}
    rows: list[dict] = []

    def gid(t, h):
        return id_order(owner.gid(t, h))

    for t in TYPES:
        for h in sorted(private[t], key=lambda x: gid(t, x)):
            if h in guest.objs[t]:
                rows.append({"check": "private-visible", "object": owner.gid(t, h), "type": t})
        # a private sub-object the guest still gets: the guest copy carries its mark
        for h, o in sorted(guest.objs[t].items(), key=lambda kv: gid(t, kv[0])):
            for path in sorted(set(_private_parts(o))):
                rows.append({"check": "private-visible", "object": owner.gid(t, h), "type": t, "path": path})
    for t in TYPES:
        for h, o in sorted(guest.objs[t].items(), key=lambda kv: gid(t, kv[0])):
            hit = {s for s in _strings(o) if s in private_handles}
            for s in sorted(hit, key=lambda x: gid(private_handles[x], x)):
                rows.append({"check": "private-reference", "object": owner.gid(t, h), "type": t,
                             "points_at": owner.gid(private_handles[s], s)})
    visible_names = {_words(person_name(p)) for p in guest.objs["people"].values()}
    by_first: dict[str, dict[str, str]] = {}  # first word -> name -> handle: each text is read once
    for h in private["people"]:
        n = _words(person_name(owner.objs["people"][h]))
        if len(n) >= MIN_NAME_CHARS and " " in n and n not in visible_names:
            by_first.setdefault(n.split()[0], {})[n] = h
    for t, fields in TEXT_FIELDS.items():
        for h, o in sorted(guest.objs[t].items(), key=lambda kv: gid(t, kv[0])):
            text = _words(" ".join(fields(o)))
            padded = f" {text} "
            hits = {n: ph for w in set(text.split()) & by_first.keys() for n, ph in by_first[w].items()
                    if f" {n} " in padded}
            for n, ph in sorted(hits.items(), key=lambda kv: gid("people", kv[1])):
                rows.append({"check": "private-name", "object": owner.gid(t, h), "type": t,
                             "person": owner.gid("people", ph)})
    # judged from the owner's copy: a death or burial the guest cannot see still means dead. A person with no dated
    # birth or baptism is not counted: on a real tree that is most of the ancestors
    for h in sorted(guest.objs["people"], key=lambda x: gid("people", x)):
        if possibly_living(owner.objs["people"][h], owner.objs["events"], this_year, undated=False):
            rows.append({"check": "living-visible", "object": owner.gid("people", h), "type": "people"})
    counts: dict[str, int] = {}
    leaks = sum(r["check"] in LEAKS for r in rows)
    for r in rows:
        counts[r["check"]] = counts.get(r["check"], 0) + 1
    return {"owner": owner.source, "guest": guest.source, "rows": rows, "counts": counts, "leaks": leaks,
            "visible": {t: len(guest.objs[t]) for t in TYPES},
            "private": {t: len(private[t]) for t in TYPES}}


def print_public(rep: dict, limit: int = 30) -> None:
    print(f"owner {rep['owner']}  guest {rep['guest']}")
    print("guest sees " + ", ".join(f"{n} {t}" for t, n in rep["visible"].items() if n))
    print("marked private " + (", ".join(f"{n} {t}" for t, n in rep["private"].items() if n) or "nothing"))
    if not rep["rows"]:
        print("nothing found")
        return
    for k, n in rep["counts"].items():
        print(f"{k}: {n}")
    for r in rep["rows"][:limit]:
        extra = r.get("points_at") or r.get("person") or r.get("path") or ""
        print(f"  {r['check']}  {r['object']}" + (f"  -> {extra}" if extra else ""))
    if len(rep["rows"]) > limit:
        print(f"  ... {len(rep['rows']) - limit} more (--limit 0 for all)")
