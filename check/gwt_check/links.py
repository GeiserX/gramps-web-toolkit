"""Which parent links of the tree have a record whose text names both parents. A report, never a verdict.

For each child of each family the report first looks at the link itself: a citation on the child_ref is the
top level, and a [probable], [confirmed] or [wrong link] note on it is shown. Then it reads the text (page and
notes) of the citations of the child's own birth or baptism (role Primary), then of the child's other events
and citations and the family's, and says whether that text
contains the names of the father and the mother. This is words in text. It is not proof that the record says
they are the child's parents, and it never marks a link probable, proven or wrong. A name that a second
person in the tree also bears, a denial word in the same sentence as a name, and a relation the tree marks as
not by birth are listed beside the row so a person reads them first. Citations and notes marked private are
not read. Nothing is written.
"""

from __future__ import annotations

import re
from collections import Counter

from .checks import BIOLOGICAL
from .dates import BAPTISM, BIRTH, kind
from .fill import _fold, _note_text
from .tree import Tree

MIN_WORD = 3  # shorter words ("de", "la", "y") match too much
DENIAL = {"no", "not", "ni", "pas", "jamais", "sin", "sans", "never", "nor", "desmiente", "descarta", "descartado",
          "rechaza", "rechazado", "falso", "incorrecto", "wrong", "denies", "denied", "contradice", "niega"}
MARKS = ("[probable]", "[confirmed]", "[wrong link]")  # how gwt-apply marks a parent link: a note on its child_ref
FLAG_MARKS = ("[probable]", "[wrong link]")  # a doubt still open on the link
LEVELS = ("the link itself is cited", "birth record names both", "other record names both", "a record names one", "cited, names neither",
          "no record names a parent")


def _words(s: str) -> list[str]:
    return [w for w in re.split(r"[^\w]+", _fold(s or "")) if len(w) >= MIN_WORD]


def _parts(p: dict) -> tuple[list[str], list[str]]:
    n = p.get("primary_name") or {}
    return _words(n.get("first_name") or ""), _words(" ".join(x.get("surname") or "" for x in n.get("surname_list") or []))


def _named(p: dict, sentences: list[set[str]]) -> tuple[bool, bool]:
    """(the person's given words and first surname word are in one sentence, a denial word is in that sentence)"""
    g, s = _parts(p)
    if not g or not s:
        return False, False
    need = set(g) | {s[0]}
    hit = [w for w in sentences if need <= w]
    return bool(hit), any(w & DENIAL for w in hit)


def _sentences(text: str) -> list[set[str]]:
    return [set(_words(x)) | {w for w in re.split(r"[^\w]+", _fold(x)) if w in DENIAL}
            for x in re.split(r"[.;\n]+", text) if x.strip()]


def _public_cits(tree: Tree, handles: list[str]) -> list[dict]:
    C = tree.objs["citations"]
    return [C[h] for h in handles or [] if h in C and not C[h].get("private")]


def _cit_text(tree: Tree, handles: list[str]) -> str:
    N = tree.objs["notes"]
    parts = []
    for c in _public_cits(tree, handles):
        parts.append(c.get("page") or "")
        parts += [_note_text(N[n]) for n in c.get("note_list") or [] if n in N and not N[n].get("private")]
    return "\n".join(parts)


def _mark(tree: Tree, ref: dict) -> str | None:
    """The [probable], [confirmed] or [wrong link] mark of a link, read from the notes on its child_ref."""
    N = tree.objs["notes"]
    for n in ref.get("note_list") or []:
        t = _note_text(N[n]).lstrip() if n in N else ""
        for m in MARKS:
            if t.startswith(m):
                return m
    return None


def link_report(tree: Tree) -> dict:
    O = tree.objs
    P, E = O["people"], O["events"]
    bearers = Counter()
    for p in P.values():
        g, s = _parts(p)
        if g and s:
            bearers[(frozenset(g), s[0])] += 1
    rows = []
    for f in O["families"].values():
        fa, mo = P.get(f.get("father_handle")), P.get(f.get("mother_handle"))
        for r in f.get("child_ref_list") or []:
            ch = P.get(r.get("ref"))
            if not ch:
                continue
            evs = [E[x["ref"]] for x in ch.get("event_ref_list") or []
                   if x.get("ref") in E and kind(x.get("role")) == "primary"]  # a godparent's baptism is not the child's
            vital = [e for e in evs if kind(e.get("type")) in BIRTH | BAPTISM]
            t_vital = _cit_text(tree, [c for e in vital for c in e.get("citation_list") or []])
            t_other = _cit_text(tree, [c for e in evs if e not in vital for c in e.get("citation_list") or []]
                                + (ch.get("citation_list") or []) + (f.get("citation_list") or []))
            link_cited = bool(_public_cits(tree, r.get("citation_list")))
            t_link = _cit_text(tree, r.get("citation_list"))
            mark = _mark(tree, r)
            flags = [f"the link is marked {mark}"] if mark in FLAG_MARKS else []
            seen = {}
            for role, par in (("father", fa), ("mother", mo)):
                if not par:
                    continue
                g, s = _parts(par)
                if not g or not s:
                    flags.append(f"the {role}'s name has no word of {MIN_WORD} or more letters: it can never match")
                for where, text in (("link citation", t_link), ("birth record", t_vital), ("other record", t_other)):
                    ok, den = _named(par, _sentences(text))
                    if ok and role not in seen:
                        seen[role] = where
                    if ok and den:
                        flags.append(f"a denial word is in the same sentence as the {role}'s name ({where}): read it")
                if g and s and bearers[(frozenset(g), s[0])] > 1:
                    flags.append(f"another person in the tree bears the {role}'s name")
            rel = [kind(r.get(k)) for k in ("frel", "mrel")]
            if any(x not in BIOLOGICAL for x in rel):
                flags.append(f"the tree marks the relation as {'/'.join(x or 'birth' for x in rel)}, not by birth")
            if len([1 for h in ch.get("parent_family_list") or [] if h in O["families"]]) > 1:
                flags.append("the child has more than one parental family: each is its own row")
            if link_cited:
                lvl = LEVELS[0]
            elif len(seen) == 2 and set(seen.values()) == {"birth record"}:
                lvl = LEVELS[1]
            elif len(seen) == 2:
                lvl = LEVELS[2]
            elif seen:
                lvl = LEVELS[3]
            elif any(_public_cits(tree, e.get("citation_list")) for e in vital):
                lvl = LEVELS[4]
            else:
                lvl = LEVELS[5]
            rows.append({"family": tree.gid("families", f["handle"]), "child": tree.gid("people", ch["handle"]),
                         "level": lvl, "named": seen, "mark": mark, "flags": sorted(set(flags))})
    return {"links": len(rows), "levels": {k: sum(1 for r in rows if r["level"] == k) for k in LEVELS}, "rows": rows}


def print_links(rep: dict, limit: int, only: str | None) -> None:
    print(f"{rep['links']} parent links, by what the text of their records names:")
    for k, v in rep["levels"].items():
        print(f"  {v:5d}  {k}")
    flagged = sum(1 for r in rep["rows"] if r["flags"])
    print(f"  {flagged} rows carry a flag to read first")
    rows = [r for r in rep["rows"] if only is None or r["level"] == only or (only == "flagged" and r["flags"])]
    for r in rows[:limit]:
        print(f"  {r['family']} {r['child']}: {r['level']}" + (f"  [{'; '.join(r['flags'])}]" if r["flags"] else ""))
    print("words in text only: not proof of a parent, never a reason to change a link")
