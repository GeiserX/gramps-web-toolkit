"""Read a GEDCOM file and say what is wrong with it before anything is imported. Writes nothing.

    gwt-import-gedcom audit family.ged [--json]

Reports the encoding (declared and as read, and every line read in another encoding), records that look like
the same person, dates that cannot be read or cannot be true, people linked to nobody, pointers to records that
are not in the file, and records nothing points at. The exit code is 0 whatever it finds.
"""

from __future__ import annotations

import datetime

from gwt_apply.dates import DateError, parse_date
from gwt_apply.dupes import Who, _alike
from gwt_core.rules import LIVING_YEARS, PARENT_AGE, PARENT_MAX_AGE

from .mapping import FAMILY_EVENTS, PERSON_EVENTS, split_name
from .parse import Gedcom, Line, ged_date
from .split import links

BIRTHS = ("BIRT", "CHR", "BAPM")
DEATHS = ("DEAT", "BURI", "CREM")


def _sortval(line: Line | None) -> tuple[int, int]:
    """(Gregorian serial day, year) of an event's date, (0, 0) when it has none that sorts."""
    if line is None:
        return 0, 0
    d, _ = ged_date(line.val("DATE"))
    if not d or d.startswith("text:"):
        return 0, 0
    try:
        date = parse_date(d)
    except DateError:
        return 0, 0
    sv = date["sortval"]
    return sv, (datetime.date.fromordinal(sv - 1721425).year if sv > 1721425 else 0)


def _first(rec: Line, tags) -> Line | None:
    return next((c for c in rec.children if c.tag in tags and c.val("DATE")), None)


def _refs(line: Line, out: list[tuple[int, str, str]]) -> None:
    if line.pointer:
        out.append((line.lineno, line.tag, line.pointer))
    for c in line.children:
        _refs(c, out)


def audit(ged: Gedcom) -> dict:
    famc, fams, members = links(ged)
    out: dict = {
        "file": {"version": ged.version, "declared": ged.declared, "read_as": ged.encoding, "sha256": ged.sha256},
        "counts": {t: len(ged.of(t)) for t in ("INDI", "FAM", "SOUR", "REPO", "NOTE", "OBJE", "SNOTE")},
        "repaired_lines": ged.repairs, "problems": list(ged.problems), "duplicates": [], "dates": [],
        "orphans": [], "dangling": [], "unreferenced": [],
    }
    # dates that cannot be read
    for rec in ged.individuals + ged.families:
        for ev in rec.children:
            if ev.tag in PERSON_EVENTS or ev.tag in FAMILY_EVENTS or ev.tag in ("EVEN", "FACT"):
                if ev.val("DATE"):
                    d, why = ged_date(ev.val("DATE"))
                    if why:
                        out["dates"].append({"line": ev.lineno, "who": rec.xref, "event": ev.tag,
                                             "text": ev.val("DATE"), "problem": why, "kept_as": d})
    # dates that cannot be true
    born = {r.xref: _sortval(_first(r, BIRTHS)) for r in ged.individuals}
    died = {r.xref: _sortval(_first(r, DEATHS)) for r in ged.individuals}
    for x in born:
        (b, by), (d, dy) = born[x], died[x]
        if b and d and d < b:
            out["dates"].append({"who": x, "problem": "dies before they are born"})
        if by and dy and dy - by > LIVING_YEARS:
            out["dates"].append({"who": x, "problem": f"dies at {dy - by}, older than {LIVING_YEARS}"})
    for fam, (parents, kids) in members.items():
        for p in parents:
            for k in kids:
                (pb, py), (kb, ky) = born.get(p, (0, 0)), born.get(k, (0, 0))
                if py and ky and ky - py < PARENT_AGE:
                    out["dates"].append({"who": k, "family": fam, "parent": p,
                                         "problem": f"born when the parent was {ky - py}"})
                if py and ky and ky - py > PARENT_MAX_AGE:
                    out["dates"].append({"who": k, "family": fam, "parent": p,
                                         "problem": f"born when the parent was {ky - py}"})
                pd = died.get(p, (0, 0))[0]
                rec = ged.by_xref.get(p)
                mother = rec is not None and rec.val("SEX").upper() == "F"
                # a father can die up to nine months before the birth; a mother cannot die before it
                if pd and kb and kb > pd + (0 if mother else 280):
                    out["dates"].append({"who": k, "family": fam, "parent": p,
                                         "problem": "born after the parent died"})
    # look-alikes inside the file, as gwt-apply's preflight compares people
    seen: list[Who] = []
    for r in ged.individuals:
        names = r.all("NAME")
        first, sur = split_name(names[0]) if names else ("", "")
        w = Who(r.xref, first, sur, years={born[r.xref][1]} - {0})
        w.parents = {p for f in famc.get(r.xref, []) for p in members[f][0]}
        w.spouses = {p for f in fams.get(r.xref, []) for p in members[f][0]} - {r.xref}
        for o in seen:
            why = _alike(w, o)
            if why and all(w.full) and not (w.parents & {o.ref} or o.parents & {w.ref}):
                out["duplicates"].append({"a": o.label(), "b": w.label(), "why": why})
        seen.append(w)
    # people linked to nobody, pointers to nothing, records nothing points at
    for r in ged.individuals:
        if not famc.get(r.xref) and not fams.get(r.xref):
            out["orphans"].append(r.xref)
    pointed: set[str] = set()
    for rec in ged.records:
        refs: list[tuple[int, str, str]] = []
        _refs(rec, refs)
        for lineno, tag, x in refs:
            if x == rec.xref:
                continue
            pointed.add(x)
            if x not in ged.by_xref:
                out["dangling"].append({"line": lineno, "tag": tag, "to": x})
    out["unreferenced"] = [r.xref for r in ged.records
                           if r.xref and r.tag in ("SOUR", "NOTE", "REPO", "OBJE", "SNOTE") and r.xref not in pointed]
    return out


def summary(a: dict) -> str:
    f = a["file"]
    lines = [f"GEDCOM {f['version']}, declared {f['declared']}, read as {f['read_as']}; "
             + ", ".join(f"{n} {t}" for t, n in a["counts"].items() if n)]
    lines.append(f"lines read in another encoding: {len(a['repaired_lines'])}"
                 + (f" (first at line {a['repaired_lines'][0]['line']}: read as {a['repaired_lines'][0]['read_as']})"
                    if a["repaired_lines"] else ""))
    for k, label in (("duplicates", "people who look like another record"), ("dates", "date problems"),
                     ("orphans", "people linked to nobody"), ("dangling", "pointers to records not in the file"),
                     ("unreferenced", "records nothing points at"), ("problems", "lines the reader could not place")):
        lines.append(f"{label}: {len(a[k])}")
        for row in a[k][:10]:
            lines.append(f"  {row}")
        if len(a[k]) > 10:
            lines.append(f"  ... {len(a[k]) - 10} more (--json for all)")
    return "\n".join(lines)
