"""Cut one tree out of a GEDCOM file: the root's blood relatives, with their spouses as stubs.

    gwt-import-gedcom split family.ged --root @I1@ --root @I40@ --out-dir trees/

writes trees/I1.ged and trees/I40.ged. A root's tree is everyone who descends from one of the root's ancestors
(the root, their parents, grandparents and so on, and every descendant of each of them), plus every spouse of
those people. A spouse is a stub: their own record and the family they share with a blood relative go in, their
parents and other marriages do not. Sources, notes, repositories and media the kept records point at go in too.
Two roots whose trees share people both keep them; the summary says how many.
"""

from __future__ import annotations

import os
import re

from .parse import Gedcom, Line


def links(ged: Gedcom) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, tuple[list[str], list[str]]]]:
    """(person -> families as child, person -> families as spouse, family -> (parents, children)), from the FAM
    records and the INDI records both, so a link written on one side only still counts."""
    famc: dict[str, list[str]] = {}
    fams: dict[str, list[str]] = {}
    members: dict[str, tuple[list[str], list[str]]] = {}
    people = {r.xref for r in ged.individuals}

    def add(d, k, v):
        if v not in d.setdefault(k, []):
            d[k].append(v)

    for f in ged.families:
        parents = [p for p in (c.pointer for c in f.all("HUSB", "WIFE")) if p in people]
        kids = [p for p in (c.pointer for c in f.all("CHIL")) if p in people]
        members[f.xref] = (parents, kids)
        for p in parents:
            add(fams, p, f.xref)
        for k in kids:
            add(famc, k, f.xref)
    for r in ged.individuals:
        for c in r.all("FAMC"):
            if c.pointer in members:
                add(famc, r.xref, c.pointer)
                if r.xref not in members[c.pointer][1]:
                    members[c.pointer][1].append(r.xref)
        for c in r.all("FAMS"):
            if c.pointer in members:
                add(fams, r.xref, c.pointer)
                if r.xref not in members[c.pointer][0]:
                    members[c.pointer][0].append(r.xref)
    return famc, fams, members


def reach(ged: Gedcom, root: str) -> tuple[set[str], set[str], set[str]]:
    """(blood relatives, spouse stubs, families) of `root`'s tree."""
    if root not in ged.by_xref or ged.by_xref[root].tag != "INDI":
        raise ValueError(f"{root} is not a person in the file")
    famc, fams, members = links(ged)
    ancestors, todo = set(), [root]
    while todo:
        x = todo.pop()
        if x in ancestors:
            continue
        ancestors.add(x)
        for f in famc.get(x, []):
            todo += members[f][0]
    blood, todo = set(), list(ancestors)
    while todo:
        x = todo.pop()
        if x in blood:
            continue
        blood.add(x)
        for f in fams.get(x, []):
            todo += members[f][1]
    families = {f for x in blood for f in fams.get(x, []) + famc.get(x, [])}
    stubs = {p for f in families for p in members[f][0]} - blood
    return blood, stubs, families


def _refs(line: Line, out: set[str]) -> None:
    if line.pointer:
        out.add(line.pointer)
    for c in line.children:
        _refs(c, out)


def _prune(line: Line, keep: set[str]) -> Line | None:
    """The line without any sub-line that points at a record not kept."""
    if line.pointer and line.pointer not in keep and line.level > 0:
        return None
    kids = [k for k in (_prune(c, keep) for c in line.children) if k is not None]
    return Line(line.level, line.tag, line.value, line.xref, line.lineno, kids)


def _lines(line: Line, seven: bool) -> list[str]:
    head = f"{line.level}" + (f" {line.xref}" if line.xref else "") + f" {line.tag}"
    value = line.value
    if line.pointer is None and value:
        value = ("@" + value if value.startswith("@") else value) if seven else value.replace("@", "@@")
    parts = value.split("\n")
    out = [head + (f" {parts[0]}" if parts[0] else "")]
    out += [f"{line.level + 1} CONT" + (f" {p}" if p else "") for p in parts[1:]]
    for c in line.children:
        out += _lines(c, seven)
    return out


def write(ged: Gedcom, people: set[str], stubs: set[str], families: set[str]) -> str:
    """The GEDCOM text of the kept records, in UTF-8 terms: the header says CHAR UTF-8."""
    keep = people | stubs | families
    records = {x for x, r in ged.by_xref.items() if r.tag not in ("INDI", "FAM")}
    todo = [ged.by_xref[x] for x in keep] + ged.of("HEAD")
    while todo:  # sources, notes, repositories, media, submitters the kept records point at, and theirs
        seen: set[str] = set()
        for r in todo:
            _refs(_prune(r, keep | records), seen)
        new = (seen & records) - keep
        keep |= new
        todo = [ged.by_xref[x] for x in new]
    seven = ged.major >= 7
    out: list[str] = []
    for rec in ged.records:
        if rec.tag == "HEAD":
            h = _prune(rec, keep)
            h.children = [c for c in h.children if c.tag != "CHAR"]
            if not seven:
                h.children.append(Line(1, "CHAR", "UTF-8"))
            out += _lines(h, seven)
        elif rec.tag == "TRLR":
            continue
        elif rec.xref in keep:
            out += _lines(_prune(rec, keep), seven)  # a stub's parents and other marriages are not kept: pruned
    out.append("0 TRLR")
    return "\n".join(out) + "\n"


def name_of(xref: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "", xref) or "root"


def split(ged: Gedcom, roots: list[str], out_dir: str, *, force: bool = False) -> list[dict]:
    """Write one file per root; one summary row each."""
    trees = [(r, *reach(ged, r)) for r in roots]
    paths = [os.path.join(out_dir, name_of(r) + ".ged") for r in roots]
    for p in paths:
        if os.path.exists(p) and not force:
            raise FileExistsError(f"{p} exists; pass --force to overwrite it")
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    for (root, blood, stubs, families), path in zip(trees, paths):
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(write(ged, blood, stubs, families))
        others = set().union(*[b | s for r, b, s, _ in trees if r != root]) if len(trees) > 1 else set()
        rows.append({"root": root, "file": path, "people": len(blood), "spouse_stubs": len(stubs),
                     "families": len(families), "shared_with_other_roots": len((blood | stubs) & others)})
    return rows
