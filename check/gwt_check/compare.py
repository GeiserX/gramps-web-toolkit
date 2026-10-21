"""Two trees side by side: what went missing, what changed, what is new, and the Ahnentafel."""

from __future__ import annotations

from .tree import Tree

MAX_GENERATIONS = 60  # deeper than any real pedigree; stops a loop where someone is their own ancestor


def diff(old: Tree, new: Tree, scope: set[tuple[str, str]] | None = None) -> tuple[list[dict], dict]:
    """Objects of `old` gone from `new` are findings. Changed and new objects are counted.

    With `scope`, changed and new objects are counted only when in scope; missing ones always count.
    """
    findings, info = [], {"missing": {}, "changed": {}, "new": {}}
    for t in new.objs:
        if t == "tags" and not (old.has_tags and new.has_tags):
            continue
        o, n = old.objs[t], new.objs[t]
        keep = (lambda h: True) if scope is None else (lambda h: (t, h) in scope)
        missing = sorted(o[h].get("gramps_id") or h for h in o.keys() - n.keys())
        changed = sorted(n[h].get("gramps_id") or h for h in o.keys() & n.keys()
                         if o[h].get("change") != n[h].get("change") and keep(h))
        added = sorted(n[h].get("gramps_id") or h for h in n.keys() - o.keys() if keep(h))
        for gid in missing:
            findings.append({"check": "missing-object", "key": f"{t} {gid}", "detail": ""})
        info["missing"][t] = len(missing)
        info["changed"][t] = changed
        info["new"][t] = added
    last = max(((o.get("change") or 0, o.get("gramps_id")) for objs in new.objs.values() for o in objs.values()),
               default=None)
    info["last change"] = {"change": last[0], "gramps_id": last[1]} if last else None
    return findings, info


def ahnentafel(tree: Tree, root_gid: str) -> dict[int, str]:
    """Ahnentafel number -> handle, following the first parent family of each person."""
    P, F = tree.objs["people"], tree.objs["families"]
    root = next((h for h, p in P.items() if p.get("gramps_id") == root_gid), None)
    if root is None:
        return {}
    out: dict[int, str] = {}
    todo = [(1, root, 0)]
    while todo:
        n, h, depth = todo.pop()
        out[n] = h
        parents = P[h].get("parent_family_list") or []
        if depth >= MAX_GENERATIONS or not parents or parents[0] not in F:
            continue
        f = F[parents[0]]
        if f.get("father_handle") in P:
            todo.append((2 * n, f["father_handle"], depth + 1))
        if f.get("mother_handle") in P:
            todo.append((2 * n + 1, f["mother_handle"], depth + 1))
    return out


def compare_ahnentafel(old: Tree, new: Tree, root_gid: str) -> tuple[list[dict], dict]:
    a, b = ahnentafel(old, root_gid), ahnentafel(new, root_gid)
    if not b:
        return [{"check": "ahnentafel-lost", "key": f"root {root_gid}", "detail": "not in the tree"}], {}
    gid = lambda tree, h: tree.objs["people"][h].get("gramps_id") or h  # noqa: E731
    findings = []
    for n in sorted(a):
        if n not in b:
            findings.append({"check": "ahnentafel-lost", "key": f"{n} {gid(old, a[n])}", "detail": ""})
        elif a[n] != b[n]:
            findings.append({"check": "ahnentafel-changed", "key": f"{n} {gid(old, a[n])}",
                             "detail": f"now {gid(new, b[n])}"})
    info = {
        "before": {"numbers": len(a), "people": len(set(a.values()))},
        "after": {"numbers": len(b), "people": len(set(b.values()))},
        "new numbers": {n: gid(new, b[n]) for n in sorted(b.keys() - a.keys())},
    }
    return findings, info
