"""What a guest can see of a tree, compared with the whole tree: a report that changes nothing.

Two dumps of the same tree, one read with an owner login and one with a guest login (the role that cannot see
private records), say what the guest still gets: an object the owner marked private, a visible object that points
at a private one, a private person's name written in a visible text, a possibly living person. Findings carry
Gramps IDs and kinds, never the text that leaked."""

from __future__ import annotations

import datetime

from .checks import LIVING_YEARS, norm, person_name
from .tree import TYPES, BadInput, Tree

# text fields a name can leak into, by type
TEXT_FIELDS = {
    "notes": lambda o: [(o.get("text") or {}).get("string", "")],
    "citations": lambda o: [o.get("page", "")],
    "events": lambda o: [o.get("description", "")],
    "media": lambda o: [o.get("desc", ""), o.get("path", "")],
    "sources": lambda o: [o.get("title", ""), o.get("author", ""), o.get("pubinfo", "")],
}
MIN_NAME_CHARS = 8  # a one-word or very short name is too common to say anything


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


def possibly_living(p: dict, events: dict, this_year: int) -> bool:
    """The rule of the media check: no death or burial of their own, and a birth or baptism less than
    LIVING_YEARS ago, or none dated."""
    own = [events.get(r.get("ref")) for r in p.get("event_ref_list", []) if r.get("role") == "Primary"]
    if any(e and e.get("type") in ("Death", "Burial") for e in own):
        return False
    years = [((e.get("date") or {}).get("dateval") or [0, 0, 0])[2] for e in own
             if e and e.get("type") in ("Birth", "Baptism")]
    years = [y for y in years if y]
    return not (years and min(years) < this_year - LIVING_YEARS)


def public_report(owner: Tree, guest: Tree, *, this_year: int | None = None) -> dict:
    """Findings, in a fixed order: private-visible, private-reference, private-name, living-visible."""
    this_year = this_year or datetime.date.today().year
    for t in TYPES:  # a guest dump that is not the same tree would compare nothing
        extra = set(guest.objs[t]) - set(owner.objs[t])
        if extra:
            raise BadInput(f"{guest.source}: {len(extra)} {t} not in {owner.source}; are they the same tree?")
    private = {t: {h for h, o in owner.objs[t].items() if o.get("private") is True} for t in TYPES}
    private_handles = {h: t for t, hs in private.items() for h in hs}
    rows: list[dict] = []

    def gid(t, h):
        return owner.gid(t, h)

    for t in TYPES:
        for h in sorted(private[t], key=lambda x: gid(t, x)):
            if h in guest.objs[t]:
                rows.append({"check": "private-visible", "object": gid(t, h), "type": t})
    for t in TYPES:
        for h, o in sorted(guest.objs[t].items(), key=lambda kv: gid(t, kv[0])):
            hit = {s for s in _strings(o) if s in private_handles and s != h}
            for s in sorted(hit, key=lambda x: gid(private_handles[x], x)):
                rows.append({"check": "private-reference", "object": gid(t, h), "type": t,
                             "points_at": gid(private_handles[s], s)})
    visible_names = {person_name(p) for p in guest.objs["people"].values()}
    names = {}
    for h in private["people"]:
        n = person_name(owner.objs["people"][h])
        if len(n) >= MIN_NAME_CHARS and " " in n and n not in visible_names:
            names[n] = h
    for t, fields in TEXT_FIELDS.items():
        for h, o in sorted(guest.objs[t].items(), key=lambda kv: gid(t, kv[0])):
            text = " " + norm(" ".join(fields(o))) + " "
            for n, ph in sorted(names.items()):
                if f" {n} " in text:
                    rows.append({"check": "private-name", "object": gid(t, h), "type": t,
                                 "person": gid("people", ph)})
    events = guest.objs["events"]
    for h, p in sorted(guest.objs["people"].items(), key=lambda kv: gid("people", kv[0])):
        if possibly_living(p, events, this_year):
            rows.append({"check": "living-visible", "object": gid("people", h), "type": "people"})
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["check"]] = counts.get(r["check"], 0) + 1
    return {"owner": owner.source, "guest": guest.source, "rows": rows, "counts": counts,
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
        extra = r.get("points_at") or r.get("person") or ""
        print(f"  {r['check']}  {r['object']}" + (f"  -> {extra}" if extra else ""))
    if len(rep["rows"]) > limit:
        print(f"  ... {len(rep['rows']) - limit} more (--limit 0 for all)")
