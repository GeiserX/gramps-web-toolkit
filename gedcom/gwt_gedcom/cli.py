"""gwt-import-gedcom: turn a GEDCOM file into a gwt-apply change list for review. It never writes to Gramps.

  gwt-import-gedcom FILE --tree SNAPSHOT --batch NAME --out changes.jsonl [--report report.json]
                    [--root @I1@] [--same @I3@=I0042 ...] [--different "@I7@=why another person" ...]
                    [--decisions FILE] [--write-decisions FILE] [--ruling TEXT] [--allow-living] [--force]
  gwt-import-gedcom audit FILE [--json]
  gwt-import-gedcom split FILE --root @I1@ [--root @I40@ ...] --out-dir DIR [--force]

SNAPSHOT is `gwt apply --save-snapshot` or a `gwt check` dump of the tree you import into: a person who looks
like someone already in it is held until you say --same or --different. Then run `gwt apply changes.jsonl`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from gwt_apply import snapshot as snapshots

from . import audit as audit_mod
from . import split as split_mod
from . import export as export_mod
from .mapping import ImportError_, build
from .parse import GedcomError, read

BATCH = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
GRAMPS_ID = re.compile(r"^[A-Za-z]+\d+(?:-[A-Za-z0-9]+)?$")
XREF = re.compile(r"^@[^@\s]+@$")


def _xref(x: str) -> str:
    """@I1@ as written, or I1 as a shell user types it."""
    x = x.strip()
    return x if x.startswith("@") else f"@{x}@"


def _decisions(path: str) -> tuple[list[str], list[str]]:
    """(--same values, --different values) from a decisions file: `@I3@=I0042` or `@I3@!=why` per line."""
    same, different = [], []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            x, sep, rest = line.partition("!=")
            if sep:
                different.append(f"{x}={rest}")
            elif "=" in line:
                same.append(line)
            else:
                raise ImportError_(f"{path} line {n}: write @I3@=I0042 (the same person) or @I3@!=why (another)")
    return same, different


def _template(rep: dict) -> str:
    """A decisions file to edit: every held look-alike, with each choice commented out."""
    out = ["# Decisions for gwt-import-gedcom --decisions. Remove the # of one line per person:",
           "#   @I3@=I0042   the file person is that tree person",
           "#   @I3@!=why    another person, and why", ""]
    for row in rep["namesakes"]:
        born = f", born {'/'.join(map(str, row['born']))}" if row["born"] else ""
        out.append(f"# {row['xref']} {row['name']}{born} looks like {', '.join(row['namesake_of'])} "
                   f"({'; '.join(row['why'])})")
        out += [f"#{row['xref']}={gid}" for gid in row["namesake_of"]]
        out += [f"#{row['xref']}!=", ""]
    return "\n".join(out)


def _pairs(values: list[str], flag: str, check_value) -> dict[str, str]:
    out = {}
    for v in values or []:
        x, sep, rest = v.partition("=")
        x, rest = _xref(x) if x.strip() else "", rest.strip()
        if not sep or not XREF.match(x) or not rest or not check_value(rest):
            raise ImportError_(f"{flag} {v!r}: write it as @I3@=" + ("I0042" if flag == "--same" else "<why>"))
        if x in out:
            raise ImportError_(f"{flag} names {x} twice")
        out[x] = rest
    return out


def run_import(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="gwt-import-gedcom", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--tree", required=True, help="snapshot or gwt-check dump of the tree you import into")
    ap.add_argument("--batch", required=True,
                    help="a name for this import (a-z, 0-9, -), part of every key; keep it the same on reruns")
    ap.add_argument("--out", required=True, help="the change list to write (JSON Lines)")
    ap.add_argument("--report", help="the report to write (JSON)")
    ap.add_argument("--root", help="import only this person's tree: blood relatives and their spouses")
    ap.add_argument("--same", action="append", default=[], metavar="@XREF@=ID",
                    help="this file person is that tree person: link to them, add nothing about them")
    ap.add_argument("--different", action="append", default=[], metavar="@XREF@=WHY",
                    help="this file person is not the tree person they look like, and why")
    ap.add_argument("--decisions", metavar="FILE",
                    help="--same and --different from a file: @I3@=I0042 or @I3@!=why per line, # for comments")
    ap.add_argument("--write-decisions", metavar="FILE",
                    help="write a decisions file to edit, with every held look-alike and each choice commented out")
    ap.add_argument("--ruling", help="the decision every op names (default: the file name and its sha256)")
    ap.add_argument("--allow-living", action="store_true", help="also import people who may be living")
    ap.add_argument("--force", action="store_true", help="overwrite --out and --report")
    a = ap.parse_args(argv)
    try:
        if not BATCH.match(a.batch):
            raise ImportError_(f"--batch {a.batch!r}: use 1-40 of a-z, 0-9 and -, starting with a letter or digit")
        outs = [p for p in (a.out, a.report, a.write_decisions) if p]
        if len({os.path.abspath(p) for p in outs}) < len(outs):
            raise ImportError_("--out, --report and --write-decisions must be different files")
        for p in outs:
            if os.path.exists(p) and not a.force:
                raise ImportError_(f"{p} exists; pass --force to overwrite it")
            if not os.path.isdir(os.path.dirname(os.path.abspath(p))):
                raise ImportError_(f"{p}: the folder {os.path.dirname(os.path.abspath(p))} does not exist")
        more_same, more_different = _decisions(a.decisions) if a.decisions else ([], [])
        same = _pairs(a.same + more_same, "--same", GRAMPS_ID.match)
        different = _pairs(a.different + more_different, "--different", lambda v: True)
        both = set(same) & set(different)
        if both:
            raise ImportError_(f"{', '.join(sorted(both))}: --same or --different, not both")
        ged = read(a.file)
        snap = snapshots.load(a.tree)
        only = None
        if a.root:
            blood, stubs, _ = split_mod.reach(ged, _xref(a.root))
            only = blood | stubs
        ruling = a.ruling or f"GEDCOM import of {os.path.basename(a.file)} (sha256 {ged.sha256[:12]})"
        ops, rep = build(ged, snap, batch=a.batch, ruling=ruling, same=same, different=different,
                         allow_living=a.allow_living, only=only)
    except (ImportError_, GedcomError, OSError, ValueError) as e:
        print(f"gwt-import-gedcom: {e}", file=sys.stderr)
        return 2
    with open(a.out, "w", encoding="utf-8") as fh:
        for op in ops:
            fh.write(json.dumps(op, ensure_ascii=False) + "\n")
    if a.report:
        with open(a.report, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=1)
    print(f"people {rep['people']} | families {rep['families']} | events {rep['events']} | places {rep['places']} "
          f"new, {rep['places_matched']} found in the tree | sources {rep['sources']} | citations {rep['citations']} "
          f"| notes {rep['notes']} | external ids {rep['external_ids']} -> {a.out}")
    if a.write_decisions:
        with open(a.write_decisions, "w", encoding="utf-8") as fh:
            fh.write(_template(rep))
    print(f"held: {len(rep['namesakes'])} look like tree people (decide with --same or --different), "
          f"{len(rep['living_held'])} may be living, {len(rep['held'])} other | date problems "
          f"{len(rep['date_problems'])} | lines read in another encoding {rep['file']['repaired_lines']} "
          f"| bare lines kept as text {rep['file']['joined_lines']} | lines not placed {len(rep['problems'])}")
    for row in rep["namesakes"][:20]:
        print(f"  {row['xref']} {row['name']} looks like {', '.join(row['namesake_of'])}: {row['decide']}")
    if rep["preflight"]:
        print(f"gwt-apply's preflight would still refuse {len(rep['preflight'])} op(s):")
        for msg in rep["preflight"][:20]:
            print("  " + msg)
        print("  A person or a couple that looks like one in the tree: decide each person with --same or "
              "--different (or --decisions FILE). A couple goes away once both parents are --same.")
        return 1
    return 0


def run_audit(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="gwt-import-gedcom audit", description=audit_mod.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--json", action="store_true", help="the whole report as JSON")
    a = ap.parse_args(argv)
    try:
        result = audit_mod.audit(read(a.file))
    except (GedcomError, OSError) as e:
        print(f"gwt-import-gedcom: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=1) if a.json else audit_mod.summary(result))
    return 0


def run_split(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="gwt-import-gedcom split", description=split_mod.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--root", action="append", required=True, help="a person whose tree to write, e.g. @I1@")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--force", action="store_true", help="overwrite files already there")
    a = ap.parse_args(argv)
    try:
        ged = read(a.file)
        roots = [_xref(r) for r in a.root]
        rows = split_mod.split(ged, roots, a.out_dir, force=a.force)
    except (GedcomError, OSError, ValueError) as e:
        print(f"gwt-import-gedcom: {e}", file=sys.stderr)
        return 2
    for r in rows:
        print(f"{r['root']}: {r['people']} people, {r['spouse_stubs']} spouse stubs, {r['families']} families, "
              f"{r['shared_with_other_roots']} also in another root's tree -> {r['file']}")
    left = split_mod.left_out(ged, roots)
    print(f"in no output: {len(left)} of {len(ged.individuals)} people"
          + (f" ({', '.join(left[:20])}{' ...' if len(left) > 20 else ''})" if left else ""))
    return 0


def run_export(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="gwt-import-gedcom export", description=export_mod.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="a gwt-check dump or a gwt-apply snapshot of the tree (.gz to read compressed)")
    ap.add_argument("--out", required=True, help="the GEDCOM file to write")
    ap.add_argument("--report", help="the privacy report to write (JSON): who was anonymized or left out")
    ap.add_argument("--submitter", default="Gramps Web Toolkit", help="the SUBM name the header records")
    ap.add_argument("--force", action="store_true", help="overwrite --out and --report")
    a = ap.parse_args(argv)
    try:
        outs = [p for p in (a.out, a.report) if p]
        if len({os.path.abspath(p) for p in outs}) < len(outs):
            raise export_mod.ExportError("--out and --report must be different files")
        for p in outs:
            if os.path.exists(p) and not a.force:
                raise export_mod.ExportError(f"{p} exists; pass --force to overwrite it")
            folder = os.path.dirname(os.path.abspath(p))
            if not os.path.isdir(folder):
                raise export_mod.ExportError(f"{p}: the folder {folder} does not exist")
        lines, rep = export_mod.build(export_mod.load(a.file), submitter=a.submitter)
    except (export_mod.ExportError, OSError, ValueError) as e:
        print(f"gwt-import-gedcom: {e}", file=sys.stderr)
        return 2
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    if a.report:
        with open(a.report, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=1)
    print(f"people {rep['people']} ({len(rep['living'])} anonymized as living) | families {rep['families']} "
          f"({rep['dropped_empty_families']} empty dropped) | sources {rep['sources']} | notes {rep['notes']} "
          f"| private left out: {sum(len(v) for v in rep['excluded_private'].values())} "
          f"| texts naming living: {sum(len(v) for v in rep['text_naming_living'].values())} -> {a.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "audit":
        return run_audit(argv[1:])
    if argv and argv[0] == "split":
        return run_split(argv[1:])
    if argv and argv[0] == "export":
        return run_export(argv[1:])
    if argv and argv[0] == "import":
        argv = argv[1:]
    return run_import(argv)
