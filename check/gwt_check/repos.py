"""`gwt-check repos`: find repositories by name or type, and list what is not linked. Read-only."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from .checks import norm
from .tree import Tree


def repository_report(tree: Tree, *, name: str | None = None, rtype: str | None = None) -> dict:
    """Repositories matching `name` (a part of the name, compared like people's names) and `rtype`, each with the
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
        kind = o.get("type") if isinstance(o.get("type"), str) else (o.get("type") or {}).get("string", "")
        if name and norm(name) not in norm(o.get("name") or ""):
            continue
        if rtype and norm(rtype) != norm(kind):
            continue
        found.append({"id": o.get("gramps_id"), "name": o.get("name") or "", "type": kind,
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


# Words that open the name of an institution that keeps records, folded (no accents, lowercase). One table, so a
# language is added here and nowhere else.
INSTITUTION_WORDS = ("archives", "archive", "archivo", "archiv", "boletin oficial", "registro civil", "registre",
                     "mairie", "ayuntamiento", "musee", "museo", "biblioteca", "bibliotheque", "parroquia", "paroisse")


def _host(url: str) -> str:
    """The host of a URL or bare address: no scheme, credentials, port, path or `www.`; lowercase."""
    url = (url or "").strip().lower()
    if "://" not in url:
        url = "//" + url
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host.strip(".").removeprefix("www.")


_ADDRESS = re.compile(r"(?:[a-z][a-z0-9+.-]*://)?[^\s|<>\"'()\[\]{},;]+")


def _hosts_in(text: str) -> set[str]:
    """Hosts a free text mentions: each address-looking token (a scheme, or a dot between name parts) is parsed on
    its own, so `host.evil.invalid`, `host@evil.invalid` and `evil.invalid/host` are never the host."""
    out = set()
    for tok in _ADDRESS.findall((text or "").lower()):
        tok = tok.rstrip(".:!?")
        if "://" in tok or re.search(r"[a-z0-9]\.[a-z]{2,}", tok):
            h = _host(tok)
            if "." in h:
                out.add(h)
    return out


def _fold(s: str) -> str:
    return norm((s or "").replace("\u2019", "'").replace("\u2018", "'"))


def _whole(text: str, phrase: str) -> bool:
    """`phrase` occurs in `text` as whole words (both already folded)."""
    i = text.find(phrase)
    while i >= 0:
        before = text[i - 1] if i else " "
        after = text[i + len(phrase)] if i + len(phrase) < len(text) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        i = text.find(phrase, i + 1)
    return False


def classify_repositories(tree: Tree) -> dict:
    """Read-only. Sources with no repository, sorted by what their own text says:
    `names_existing`: title, author or publication info names a repository the tree has (by its name as whole words,
    or by its web host); `names_other`: it opens with an institution word but that institution is not a repository
    of the tree (grouped, so one new repository can serve many sources); `names_nothing`: no institution at all.
    Also repository hygiene: repositories with no URL and no address, two repositories on one web host, and linked
    sources with no call number."""
    repos, sources = tree.objs["repositories"], tree.objs["sources"]
    known = []
    for h, o in repos.items():
        name = _fold(o.get("name"))
        hosts = {_host(u.get("path") or "") for u in o.get("urls") or []} - {""}
        if name:
            known.append((o.get("gramps_id"), name, hosts))
    existing, other, nothing = [], {}, []
    for s in sources.values():
        if s.get("reporef_list"):
            continue
        fields = {k: _fold(s.get(k)) for k in ("title", "author", "pubinfo")}
        text = " | ".join(fields.values())
        hit = [(rid, f) for rid, name, hosts in known
               for f in fields if _whole(fields[f], name) or (hosts & _hosts_in(s.get(f)))]
        if hit:
            ids = sorted({r for r, _ in hit})
            existing.append({"source": s.get("gramps_id"), "title": s.get("title", ""), "repositories": ids,
                             "fields": sorted({f for _, f in hit}), "certain": len(ids) == 1})
            continue
        found = None
        for w in INSTITUTION_WORDS:
            i = text.find(w)
            if i >= 0 and _whole(text, w) and (found is None or i < found[0]):
                found = (i, text[i:].split(" | ")[0].split(" / ")[0].split(" - ")[0].split(" \u2014 ")[0].split(" (")[0][:60].strip(" ,;.-"))
        if found:
            other.setdefault(found[1], []).append(s.get("gramps_id"))
        else:
            nothing.append(s.get("gramps_id"))
    by_host: dict[str, list[str]] = {}
    for rid, _, hosts in known:
        for h in hosts:
            by_host.setdefault(h, []).append(rid)
    no_call = sum(1 for s in sources.values() for r in s.get("reporef_list") or [] if not (r.get("call_number") or "").strip())
    return {
        "names_existing": sorted(existing, key=lambda x: x["source"] or ""),
        "names_other": [{"institution": k, "sources": sorted(v, key=lambda x: x or "")}
                        for k, v in sorted(other.items(), key=lambda kv: (-len(kv[1]), kv[0]))],
        "names_nothing": sorted(nothing, key=lambda x: x or ""),
        "repositories_without_url_or_address": sorted(
            o.get("gramps_id") for o in repos.values() if not (o.get("urls") or o.get("address_list"))),
        "repositories_sharing_a_host": {h: ids for h, ids in sorted(by_host.items()) if len(ids) > 1},
        "linked_sources_without_call_number": no_call,
    }


def print_classification(rep: dict, limit: int) -> None:
    ex = rep["names_existing"]
    print(f"sources with no repository that name one the tree has: {len(ex)} "
          f"({sum(1 for x in ex if x['certain'])} name exactly one)")
    for x in ex[:limit]:
        print(f"  {x['source']} -> {','.join(x['repositories'])}  (in {', '.join(x['fields'])})  {x['title'][:60]}")
    print(f"institutions named but not a repository of the tree: {len(rep['names_other'])}")
    for x in rep["names_other"][:limit]:
        print(f"  {len(x['sources']):3d} sources  {x['institution']}")
    print(f"sources that name no institution (a family photo, an e-mail, a website): {len(rep['names_nothing'])}")
    print(f"repositories with no URL and no address: {len(rep['repositories_without_url_or_address'])}"
          + (": " + ", ".join(rep["repositories_without_url_or_address"]) if rep["repositories_without_url_or_address"] else ""))
    for h, ids in rep["repositories_sharing_a_host"].items():
        print(f"repositories on the same web host {h}: {', '.join(ids)}")
    print(f"linked sources with no call number: {rep['linked_sources_without_call_number']}")
    print("report only: nothing is changed; link with gwt-apply link_repository after reading each source")
