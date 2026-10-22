"""`gwt-check repos`: find repositories by name or type, and list what is not linked. Read-only."""

from __future__ import annotations

import unicodedata

from .tree import Tree


def fold(text: str) -> str:
    bare = "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c))
    return " ".join(bare.casefold().split())


def repository_report(tree: Tree, *, name: str | None = None, rtype: str | None = None) -> dict:
    """Repositories matching `name` (a part of the name, ignoring case and accents) and `rtype`, each with the
    sources that list it; plus the sources that list no repository and the repositories no source lists."""
    repos, sources = tree.objs["repositories"], tree.objs["sources"]
    used: dict[str, list[dict]] = {h: [] for h in repos}
    bare = []
    for s in sources.values():
        refs = s.get("reporef_list") or []
        if not refs:
            bare.append({"id": s.get("gramps_id"), "title": s.get("title", "")})
        for r in refs:
            if r.get("ref") in used:
                used[r["ref"]].append({"id": s.get("gramps_id"), "title": s.get("title", ""),
                                       "call_number": r.get("call_number", ""), "media_type": r.get("media_type", "")})
    found = []
    for h, o in repos.items():
        if name and fold(name) not in fold(o.get("name", "")):
            continue
        if rtype and fold(rtype) != fold(o.get("type") if isinstance(o.get("type"), str) else (o.get("type") or {}).get("string", "")):
            continue
        found.append({"id": o.get("gramps_id"), "name": o.get("name", ""), "type": o.get("type"),
                      "urls": [u.get("path") for u in o.get("urls") or []], "sources": used[h]})
    found.sort(key=lambda r: r["id"] or "")
    return {
        "repositories": found,
        "sources_without_repository": sorted(bare, key=lambda r: r["id"] or ""),
        "repositories_without_sources": [{"id": o.get("gramps_id"), "name": o.get("name", "")}
                                         for h, o in sorted(repos.items(), key=lambda x: x[1].get("gramps_id") or "")
                                         if not used[h]],
    }


def print_repository_report(rep: dict, *, unlinked: bool, limit: int) -> None:
    for r in rep["repositories"]:
        print(f"{r['id']}  {r['name']}  [{r['type']}]  {len(r['sources'])} sources")
    if not rep["repositories"]:
        print("no repository matches")
    if unlinked:
        print(f"\nrepositories no source lists: {len(rep['repositories_without_sources'])}")
        for r in rep["repositories_without_sources"][:limit]:
            print(f"  {r['id']}  {r['name']}")
        bare = rep["sources_without_repository"]
        print(f"sources that list no repository: {len(bare)}")
        for s in bare[:limit]:
            print(f"  {s['id']}  {s['title']}")
        if len(bare) > limit:
            print(f"  ... {len(bare) - limit} more (--limit, or --json)")
