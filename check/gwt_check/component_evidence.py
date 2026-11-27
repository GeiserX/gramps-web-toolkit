"""Citations, notes and media that two family components share: a documentary bridge between groups of people the
tree holds with no family link between them. A report; it changes nothing and never sets the exit code."""
from __future__ import annotations

from collections import defaultdict

from .checks import family_components, id_order
from .dates import kind
from .tree import BadInput, Tree

KINDS = ("citations", "notes", "media")


def _refs(T: dict, obj: dict):
    """(type, handle) of each citation, note and media object `obj` lists itself."""
    for h in obj.get("citation_list") or []:
        if h in T["citations"]:
            yield "citations", h
    for h in obj.get("note_list") or []:
        if h in T["notes"]:
            yield "notes", h
    for r in obj.get("media_list") or []:
        if isinstance(r, dict) and r.get("ref") in T["media"]:
            yield "media", r["ref"]


def component_evidence(tree: Tree, root: str | None = None) -> dict:
    """Evidence held by more than one of the components `unconnected` reports, one row per set of components.

    A component holds what its people and families list, what their own events list (role Primary for a person,
    Family for a family, so a godparent or a witness holds nothing of the act) and the notes and media of those
    citations. Sources, repositories and places do not count."""
    if tree.partial:
        raise BadInput(f"{tree.source}: a .gramps backup has no citations, notes or media; use a dump")
    T = tree.objs
    if root and tree.by_gid("people", root) is None:
        raise BadInput(f"--root {root}: no such person")
    comp, comps, lowest, main, fam_member = family_components(T, root)
    held: dict[tuple[str, str], set[str]] = defaultdict(set)

    def hold(obj: dict, c: str, role: str) -> None:
        events = [T["events"][r["ref"]] for r in obj.get("event_ref_list") or []
                  if isinstance(r, dict) and r.get("ref") in T["events"] and kind(r.get("role")) == role]
        for o in [obj, *events]:
            for t, h in _refs(T, o):
                held[t, h].add(c)
                if t == "citations":
                    for t2, h2 in _refs(T, T["citations"][h]):
                        if t2 != "citations":
                            held[t2, h2].add(c)

    for h, c in comp.items():
        hold(T["people"][h], c, "primary")
    for h, m in fam_member.items():
        hold(T["families"][h], comp[m], "family")

    def group(c: str) -> dict:
        return {"key": lowest[c], "people": len(comps[c]), "main": c == main}

    bridges: dict[frozenset, dict[str, list[str]]] = defaultdict(lambda: {k: [] for k in KINDS})
    for (t, h), cs in held.items():
        if len(cs) > 1:
            bridges[frozenset(cs)][t].append(tree.gid(t, h))
    rows = []
    for cs, ev in bridges.items():
        gs = sorted(cs, key=lambda c: (c != main, id_order(lowest[c])))
        rows.append({"groups": [group(c) for c in gs], **{k: sorted(v, key=id_order) for k, v in ev.items()}})
    rows.sort(key=lambda r: (not r["groups"][0]["main"], [id_order(g["key"]) for g in r["groups"]]))
    return {"main": group(main) if main else None, "components": len(comps), "rows": rows}


def print_component_evidence(rep: dict, limit: int) -> None:
    rows, m = rep["rows"], rep["main"]
    print(f"{len(rows)} sets of family groups share a citation, note or media object (a report; nothing is changed)")
    if m:
        print(f"  {rep['components']} groups; main group {m['key']} has {m['people']} people")
    for r in rows[:limit]:
        gs = " + ".join(f"{g['key']} ({g['people']}{', main' if g['main'] else ''})" for g in r["groups"])
        print(f"  {gs}: {', '.join(i for k in KINDS for i in r[k])}")
    if len(rows) > limit:
        print(f"  ... {len(rows) - limit} more (--limit, or --json for all)")
    print("shared evidence is a lead to read, not proof that two groups are one family")
