"""Two trees side by side: what went missing, what changed, what is new, and the Ahnentafel."""

from __future__ import annotations

from collections import deque

from .tree import Tree

MAX_GENERATIONS = 60  # deeper than any real pedigree; stops a loop where someone is their own ancestor
MAX_POSITIONS = 100_000  # pedigree collapse doubles positions per generation; past the cap, say so


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


class Ahnentafel(dict):
    """Ahnentafel number -> handle. `complete_depth` is None when every reachable position is
    present, else the last generation fully expanded before MAX_POSITIONS stopped the walk."""
    complete_depth: int | None = None


def ahnentafel(tree: Tree, root_gid: str) -> Ahnentafel:
    """Ahnentafel number -> handle, following the first parent family of each person.

    Breadth-first so a MAX_POSITIONS stop leaves a clean prefix of generations: every position
    up to complete_depth is present, deeper ones may be missing. Pedigree collapse keeps one
    entry per position (the same person legitimately holds many numbers), so a collapsed
    pedigree grows exponentially in generations and only the cap keeps the walk bounded.
    """
    P, F = tree.objs["people"], tree.objs["families"]
    root = next((h for h, p in P.items() if p.get("gramps_id") == root_gid), None)
    out: Ahnentafel = Ahnentafel()
    if root is None:
        return out
    todo = deque([(1, root, 0)])
    while todo:
        n, h, depth = todo.popleft()
        if len(out) >= MAX_POSITIONS:
            out.complete_depth = depth - 1
            break
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
    gen = lambda n: n.bit_length() - 1  # noqa: E731
    # a truncated side was only walked up to its complete_depth: nothing deeper may be
    # reported lost or changed, and the truncation itself must be a finding, never silent
    limit = min((d for d in (a.complete_depth, b.complete_depth) if d is not None), default=None)
    findings = []
    if limit is not None:
        findings.append({"check": "ahnentafel-truncated", "key": f"root {root_gid}",
                         "detail": f"over {MAX_POSITIONS} positions; compared up to generation {limit}"})
    for n in sorted(a):
        if limit is not None and gen(n) > limit:
            continue
        if n not in b:
            findings.append({"check": "ahnentafel-lost", "key": f"{n} {gid(old, a[n])}", "detail": ""})
        elif a[n] != b[n]:
            findings.append({"check": "ahnentafel-changed", "key": f"{n} {gid(old, a[n])}",
                             "detail": f"now {gid(new, b[n])}"})
    info = {
        "before": {"numbers": len(a), "people": len(set(a.values()))},
        "after": {"numbers": len(b), "people": len(set(b.values()))},
        "new numbers": {n: gid(new, b[n]) for n in sorted(b.keys() - a.keys())
                        if limit is None or gen(n) <= limit},
    }
    if limit is not None:
        info["compared up to generation"] = limit
    return findings, info
