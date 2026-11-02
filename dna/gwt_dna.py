#!/usr/bin/env python3
"""gwt-import-dna: DNA match lists exported from a testing site, as a gwt-apply change list for Gramps Web's DNA view.

  gwt-import-dna --site SITE --tested I0001 --snapshot DUMP --out changes.jsonl FILE [FILE ...]
                 [--kit LABEL] [--ruling R] [--tag dna-match] [--id-map TSV] [--column FIELD=HEADER ...]
                 [--batch 1500] [--report report.json] [--online [--config FILE]] [--force]

FILE is a CSV the site lets you download: a match list (one row per match), a segment file (one row per shared
segment), or both. Which is which is read from the header: a file with a chromosome column is a segment file.
Columns are found by name; only the fields below are read, so a site's contact, e-mail, country, location and
tree columns never reach the change list.

For each match: an add_person (unless the match is already in the tree), an add_association from the tested
person with relation DNA, a Research note on the match person whose first line marks the site, the match and
its cM, and, when segments are known, a note on the association in the segment format the DNA view parses.

A match already in the tree is pointed at, not made again: by --id-map, by the mark an earlier import of the
same site left (same match id), or by another site's mark with the same name and a close cM (one person only).
Exit 2 on a bad file or option. Standard library only (--online needs gwt-apply).
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import re
import sys

try:
    from gwt_core.rules import fold
except ImportError:  # a clone without an install: the shared core sits beside this folder
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "core"))
    from gwt_core.rules import fold

SITES = {"myheritage": "MyHeritage", "ftdna": "FamilyTreeDNA", "gedmatch": "GEDmatch",
         "livingdna": "Living DNA", "ancestry": "AncestryDNA", "other": "a DNA testing site"}
BATCH = 1500
CROSS_CM_ABS, CROSS_CM_REL = 10.0, 0.30  # two sites' cM for one match differ by at most max(10 cM, 30 %)
MARK = "gwt-import-dna"
MARK_RE = re.compile(r"^%s site=(\S+) id=(\S+) cM=([0-9.]+)" % MARK)
SEGMENT_HEADER = "Chromosome,Start Location,End Location,Centimorgans,Matching SNPs"
RULING = "gwt-import-dna"
LONG_NUMBER = re.compile(r"\d{6,}")  # a phone or an identity number inside a display name
PLACEHOLDERS = {"private", "<private> <private>", "<private>", "anonymous", "unknown"}

# folded header -> field. Headers as the sites' own exports write them (MyHeritage, FamilyTreeDNA, GEDmatch),
# plus the usual third-party spellings. Anything else in the file is ignored and listed in the report.
MATCH_COLUMNS = {
    "id": ["dna match id", "match id", "kit", "kit nbr", "kit number", "matched kit", "matchedkit", "match guid",
           "matchguid", "barcode"],
    "name": ["name", "match name", "full name", "display name", "matched name", "matchedname", "username"],
    "first": ["first name"], "middle": ["middle name"], "last": ["last name"],
    "cm": ["shared dna cm", "total cm shared", "shared cm", "shared dna", "total cm", "shared centimorgans",
           "sharedcentimorgans", "dna shared", "dnashared"],
    "segments": ["shared segments", "number of shared segments", "segments", "segment count", "segmentcount"],
    "longest": ["largest segment cm", "largest segment", "longest block", "largest seg", "largest cm",
                "longest segment", "longestsegment", "sharedsegment"],
    "relationship": ["estimated relationship", "relationship range", "relationship", "predicted relationship"],
    "generations": ["gen", "generations"],
    "sex": ["sex", "gender", "matched sex"],
    "surnames": ["ancestral surnames", "ancestral names"],
}
SEGMENT_COLUMNS = {
    "id": ["dna match id", "match id", "matched kit", "matchedkit"],
    "name": ["match name", "matched name", "matchedname"],
    "chromosome": ["chromosome", "chr", "chrom"],
    "start": ["start location", "start position", "start", "b37 start", "b37start", "b37 start pos n"],
    "end": ["end location", "end position", "end", "stop", "b37 end", "b37end", "b37 end pos n"],
    "cm": ["centimorgans", "centimorgans cm", "segment cm", "cm"],
    "snps": ["matching snps", "snps", "snp", "number of snps"],
}


class BadInput(Exception):
    pass


def _hfold(text: str) -> str:
    return fold(text)


def number(text) -> float | None:
    """A cM or a count as a site writes it: «227.6», «227,6», «1,234.5», «3.2% (227.6 cM)». None if none."""
    t = str(text or "").strip()
    m = re.search(r"([\d.,]+)\s*cM", t, re.I) or re.search(r"[\d.,]*\d", t)
    if not m:
        return None
    v = m.group(1) if m.lastindex else m.group(0)
    if "," in v and "." in v:
        v = v.replace(",", "") if v.rfind(".") > v.rfind(",") else v.replace(".", "").replace(",", ".")
    elif v.count(",") == 1 and len(v.split(",")[1]) != 3:
        v = v.replace(",", ".")
    else:
        v = v.replace(",", "")
    try:
        return float(v)
    except ValueError:
        return None


def read_csv(path: str) -> list[list[str]]:
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    first = text.split("\n", 1)[0]
    delim = max(",;\t", key=first.count)
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise BadInput(f"{path}: no header and rows")
    return rows


def columns(header: list[str], table: dict, overrides: dict, path: str) -> tuple[dict, list[str]]:
    """field -> column index, and the headers left unread. --column wins; else the first alias found, in order."""
    folded = [_hfold(h) for h in header]
    found = {}
    for field, aliases in table.items():
        if field in overrides:
            want = _hfold(overrides[field])
            if want not in folded:
                raise BadInput(f"{path}: --column {field}={overrides[field]}: no such column; it has {header}")
            found[field] = folded.index(want)
            continue
        for a in aliases:
            if a in folded and folded.index(a) not in found.values():
                found[field] = folded.index(a)
                break
    ignored = [h for i, h in enumerate(header) if i not in found.values()]
    return found, ignored


def clean_name(name: str) -> str:
    """A display name fit for the tree: an e-mail keeps its local part, long digit runs go, placeholders go."""
    n = (name or "").strip()
    if "@" in n:
        n = n.split("@")[0]
    n = " ".join(LONG_NUMBER.sub(" ", n).split())
    return "" if fold(n) in {fold(p) for p in PLACEHOLDERS} or "<private>" in n.lower() else n


def match_id(raw_id: str, name: str) -> str:
    rid = "-".join((raw_id or "").split())
    return rid if rid else "name-" + ("-".join(fold(name).split()) or "unnamed")


def read_files(paths: list[str], overrides: dict) -> tuple[dict, list, dict]:
    """(matches by match id, segments as (id or None, folded name, row), report of columns)."""
    matches, segments, cols = {}, [], {}
    for path in paths:
        rows = read_csv(path)
        header, body = rows[0], rows[1:]
        folded = {_hfold(h) for h in header}
        is_seg = bool(folded & set(SEGMENT_COLUMNS["chromosome"])) or "chromosome" in overrides
        table = SEGMENT_COLUMNS if is_seg else MATCH_COLUMNS
        ov = {k: v for k, v in overrides.items() if k in table}
        found, ignored = columns(header, table, ov, path)
        cols[path] = {"kind": "segments" if is_seg else "matches",
                      "read": {f: header[i] for f, i in found.items()}, "ignored": ignored}
        cell = lambda r, f: r[found[f]].strip() if f in found and found[f] < len(r) else ""
        if is_seg:
            need = [f for f in ("chromosome", "start", "end", "cm") if f not in found]
            if need or not ({"id", "name"} & set(found)):
                raise BadInput(f"{path}: a segment file needs a match id or match name column and {need or 'more'}; "
                               f"name them with --column")
            for r in body:
                start, end, cm = number(cell(r, "start")), number(cell(r, "end")), number(cell(r, "cm"))
                chrom = cell(r, "chromosome").upper().removeprefix("CHR")
                if not chrom or start is None or end is None or cm is None:
                    continue
                snps = number(cell(r, "snps"))
                segments.append((cell(r, "id") or None, fold(clean_name(cell(r, "name"))),
                                 {"name": cell(r, "name"), "chromosome": chrom, "start": int(start), "end": int(end),
                                  "cm": cm, "snps": int(snps) if snps is not None else None}))
            continue
        if not ({"id", "name", "last"} & set(found)) or "cm" not in found:
            raise BadInput(f"{path}: a match list needs a name or id column and a shared cM column; "
                           f"name them with --column (it has {header})")
        for rn, r in enumerate(body, 2):
            if "first" in found or "last" in found:
                first = " ".join(x for x in (cell(r, "first"), cell(r, "middle")) if x)
                full = clean_name(" ".join(x for x in (first, cell(r, "last")) if x) or cell(r, "name"))
                parts = (clean_name(first), clean_name(cell(r, "last"))) if cell(r, "last") else None
            else:
                full, parts = clean_name(cell(r, "name")), None
            if not full and not cell(r, "id"):
                cols[path].setdefault("skipped_rows", []).append(rn)
                continue
            mid = base = match_id(cell(r, "id"), full)
            n = 1
            while mid in matches:  # two matches with one name and no id are two people
                n += 1
                mid = f"{base}-{n}"
            sex = cell(r, "sex")[:1].upper()
            matches[mid] = {"id": mid, "name": full, "parts": parts, "cm": number(cell(r, "cm")),
                            "segments": number(cell(r, "segments")), "longest": number(cell(r, "longest")),
                            "relationship": cell(r, "relationship"), "generations": cell(r, "generations"),
                            "surnames": " ".join(re.sub(r"\([^)]*\)", " ", cell(r, "surnames")).split()),
                            "gender": sex if sex in ("M", "F") else None, "segs": []}
    return matches, segments, cols


def attach_segments(matches: dict, segments: list) -> list[str]:
    """Put each segment on its match, by id or else by folded name; a match only in the segment file is made from
    its segments. Returns the names whose segments were ambiguous (two matches with that name, no id)."""
    by_name: dict[str, list[str]] = {}
    for mid, m in matches.items():
        by_name.setdefault(fold(m["name"]), []).append(mid)
    ambiguous = set()
    for sid, sname, seg in segments:
        named = by_name.get(sname, []) if sname else []
        if sid and sid in matches:
            mid = sid
        elif len(named) == 1:
            mid = named[0]
        elif len(named) > 1:
            ambiguous.add(seg["name"])
            continue
        else:
            mid = match_id(sid or "", clean_name(seg["name"]))
            if mid not in matches:
                matches[mid] = {"id": mid, "name": clean_name(seg["name"]), "parts": None, "cm": None,
                                "segments": None, "longest": None, "relationship": "", "generations": "",
                                "surnames": "",
                                "gender": None, "segs": []}
                by_name.setdefault(sname, []).append(mid)
        matches[mid]["segs"].append(seg)
    for m in matches.values():
        if m["segs"] and m["cm"] is None:
            m["cm"] = round(sum(s["cm"] for s in m["segs"]), 2)
            m["segments"] = m["segments"] or len(m["segs"])
            m["longest"] = m["longest"] or max(s["cm"] for s in m["segs"])
    return sorted(ambiguous)


CHROM_ORDER = {str(n): n for n in range(1, 23)} | {"X": 23, "Y": 24, "MT": 25}


def segment_note(segs: list[dict]) -> str:
    """The segment table as Gramps Web's DNA view parses it: a CSV header, then one segment per line."""
    rows = sorted(segs, key=lambda s: (CHROM_ORDER.get(s["chromosome"], 99), s["chromosome"], s["start"]))
    lines = [SEGMENT_HEADER]
    for s in rows:
        snps = "" if s["snps"] is None else str(s["snps"])
        lines.append(f"{s['chromosome']},{s['start']},{s['end']},{_fmt(s['cm'])},{snps}")
    return "\n".join(lines)


def _fmt(x) -> str:
    return ("%.2f" % x).rstrip("0").rstrip(".") if isinstance(x, float) else str(x)


def parse_like_gramps_web(text: str) -> list[dict]:
    """The segments Gramps Web reads from a note (gramps_webapi/api/dna.py, parse_raw_dna_match_string, for a
    comma-separated table with a header row, which every note from here has): what the DNA view will show. Used to check every note before it ships.
    https://github.com/gramps-project/gramps-web-api/blob/77263b895c1d476dae9a655587956ec798536776/gramps_webapi/api/dna.py"""
    rows = text.strip().split("\n")
    if len(rows) < 2 or rows[0].count(",") < 3:
        return []
    head = [c.lower().strip() for c in rows[0].split(",")]
    taken: list[int] = []

    def find(cond, missing=False):
        for i, c in enumerate(head):
            if i not in taken and cond(c):
                taken.append(i)
                return i
        if missing:
            return None
        raise ValueError

    try:
        chrom = find(lambda c: c.startswith("chr"))
        start = find(lambda c: "start" in c)
        end = find(lambda c: "end" in c or "stop" in c or ("length" in c and "morgan" not in c))
        cm = find(lambda c: c.startswith("cm") or "centimorgan" in c or "length" in c)
    except ValueError:
        return []
    snp = find(lambda c: "snp" in c, missing=True)
    out = []
    for row in rows[1:]:
        f = row.split(",")
        if not row.strip() or len(f) < 4:
            continue
        try:
            out.append({"chromosome": f[chrom].strip(), "start": int(f[start]), "stop": int(f[end]),
                        "cM": float(f[cm]), "SNPs": int(f[snp]) if snp is not None and f[snp].strip() else 0})
        except (ValueError, IndexError):
            continue
    return out


# --- the tree ------------------------------------------------------------------------------------------------

def load_tree(path: str) -> dict:
    """People by Gramps ID with folded names, and the marks earlier imports left: (site, id) -> Gramps ID."""
    try:
        with open(path, "rb") as fh:
            gz = fh.read(2) == b"\x1f\x8b"
        with (gzip.open if gz else open)(path, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, EOFError) as exc:
        raise BadInput(f"{path}: not a gwt-check dump or gwt-apply snapshot ({exc})")
    objs = data.get("objects", data) if isinstance(data, dict) else None
    if not isinstance(objs, dict):
        raise BadInput(f"{path}: not a gwt-check dump or gwt-apply snapshot")
    people = objs.get("people") or objs.get("Person")
    notes = objs.get("notes") or objs.get("Note") or []
    if not isinstance(people, list) or not people:
        raise BadInput(f"{path}: no people in it")
    note_text = {}
    for n in notes:
        t = n.get("text")
        note_text[n.get("handle")] = (t.get("string") if isinstance(t, dict) else t) or ""
    tree = {"ids": {}, "names": {}, "marks": {}, "marks_by_name": {}}
    for p in people:
        gid = p.get("gramps_id")
        if not gid:
            continue
        n = p.get("primary_name") or {}
        sur = " ".join(s.get("surname") or "" for s in n.get("surname_list") or [] if s.get("surname"))
        name = " ".join(x for x in (n.get("first_name") or "", sur) if x)
        tree["ids"][gid] = name
        tree["names"].setdefault(fold(name), []).append(gid)
        for h in p.get("note_list") or []:
            m = MARK_RE.match(note_text.get(h, ""))
            if m:
                site, mid, cm = m.group(1), m.group(2), float(m.group(3))
                tree["marks"].setdefault((site, mid), gid)
                tree["marks_by_name"].setdefault(fold(name), []).append((gid, site, cm))
    return tree


def read_id_map(path: str | None, site: str) -> dict:
    """site TAB match id or name TAB Gramps ID; rows of other sites are skipped, so one file serves every site."""
    out = {}
    if not path:
        return out
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip() or line.startswith("#"):
                continue
            f = [c.strip() for c in line.rstrip("\n").split("\t")]
            if len(f) != 3 or not re.fullmatch(r"[A-Za-z]+\d+", f[2]):
                raise BadInput(f"{path}:{n}: want site<TAB>match id or name<TAB>Gramps ID")
            if f[0] == site:
                out[f[1]] = f[2]
                out.setdefault("name:" + fold(f[1]), f[2])
    return out


def close_cm(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= max(CROSS_CM_ABS, CROSS_CM_REL * max(a, b))


# --- the change list -----------------------------------------------------------------------------------------

def build(matches: dict, tree: dict, *, site: str, tested: str, kit: str, ruling: str, tag: str | None,
          id_map: dict) -> tuple[list[list[dict]], dict]:
    """One group of ops per match (a group never splits across files), and the report."""
    if tested not in tree["ids"]:
        raise BadInput(f"--tested {tested}: not in the snapshot")
    label = SITES[site]
    groups, made_names = [], {}
    rep = {"matches": len(matches), "new_people": 0, "in_tree": [], "cross_site": [], "cross_ambiguous": [],
           "self": [], "namesakes": [], "with_segments": 0}
    for m in sorted(matches.values(), key=lambda m: (-(m["cm"] or 0), m["id"])):
        mid, name, cm = m["id"], m["name"], m["cm"]
        pkey = f"dna-{site}-{mid}"
        target, how = id_map.get(mid) or id_map.get("name:" + fold(name)), "id-map"
        if not target and (site, mid) in tree["marks"]:
            target, how = tree["marks"][(site, mid)], "same-site"
        if not target and name:
            cands = {gid: (s, c) for gid, s, c in tree["marks_by_name"].get(fold(name), []) if s != site and close_cm(cm, c)}
            if len(cands) == 1:
                (target, (s, c)), = cands.items()
                how = "cross-site"
                rep["cross_site"].append({"match": mid, "person": target, "cm": cm, "other_site": s, "other_cm": c})
            elif len(cands) > 1:
                rep["cross_ambiguous"].append({"match": mid, "people": sorted(cands)})
        ops = []
        if target:
            if how != "cross-site":
                rep["in_tree"].append({"match": mid, "person": target, "by": how})
            ref = target
        else:
            first, surname = m["parts"] or ((name.split(" ", 1) + [""])[:2] if name else (f"(no name, {label} {mid})", ""))
            person = {"op": "add_person", "key": pkey, "ruling": ruling, "first_name": first, "surname": surname}
            if m["gender"]:
                person["gender"] = m["gender"]
            if tag:
                person["tag"] = [tag]
            same = tree["names"].get(fold(f"{first} {surname}"), []) + made_names.get(fold(f"{first} {surname}"), [])
            if same:
                person.update(allow_namesake=True, namesake_reason="a DNA match: another person unless placed",
                              namesake_of=same)
                rep["namesakes"].append({"match": mid, "namesake_of": same})
            made_names.setdefault(fold(f"{first} {surname}"), []).append(f"key:{pkey}")
            ops.append(person)
            ref = f"key:{pkey}"
            rep["new_people"] += 1
        if ref == tested:
            rep["self"].append(mid)
            continue
        akey = f"dna-{site}-{mid}-{kit}"
        ops.append({"op": "add_association", "key": f"{akey}-assoc", "ruling": ruling, "person": tested,
                    "target": ref, "relation": "DNA"})
        lines = [f"{MARK} site={site} id={mid} cM={_fmt(cm) if cm is not None else 0}",
                 f"{label} DNA match of {tested} (kit {kit}).",
                 "Shared: " + (f"{_fmt(cm)} cM" if cm is not None else "cM not given")
                 + (f" in {int(m['segments'])} segment{'s' if m['segments'] != 1 else ''}" if m["segments"] else "")
                 + (f"; longest {_fmt(m['longest'])} cM" if m["longest"] else "") + "."]
        if m["relationship"]:
            lines.append(f"Relationship estimated by the site: {m['relationship']}.")
        if m["generations"]:
            lines.append(f"Generations to the common ancestor, estimated by the site: {m['generations']}.")
        if m["surnames"]:
            lines.append(f"Ancestral surnames listed by the match: {m['surnames']}.")
        if how == "cross-site":
            lines.append("Same name and a close cM as a match from another site already in the tree: taken as the "
                         "same person.")
        if not target:
            lines.append("Not placed in the tree: no family links until the relationship is proven.")
        ops.append({"op": "add_note", "key": f"{akey}-summary", "ruling": ruling, "type": "Research",
                    "text": "\n".join(lines), "attach": [{"person": ref}]})
        if m["segs"]:
            text = segment_note(m["segs"])
            if len(parse_like_gramps_web(text)) != len(m["segs"]):
                raise AssertionError(f"segment note of {mid} does not parse as Gramps Web reads it")
            ops.append({"op": "add_note", "key": f"{akey}-segments", "ruling": ruling, "text": text,
                        "attach": [{"association": {"person": tested, "target": ref, "relation": "DNA"}}]})
            rep["with_segments"] += 1
        groups.append(ops)
    return groups, rep


def blocks(groups: list[list[dict]], size: int) -> list[list[dict]]:
    """Files of at most `size` ops, a match's ops always together, in order (a namesake_of only names an earlier
    match, so applying the files in order always finds it)."""
    out, cur = [], []
    for g in groups:
        if cur and len(cur) + len(g) > size:
            out.append(cur)
            cur = []
        cur += g
    return out + [cur] if cur else out


def check_online(texts: list[str], conf_path: str | None) -> list[str]:
    """Each segment note through the server's own parser, POST /api/parsers/dna-match; the problems."""
    from gwt_apply.cli import _client, read_config, settings_problem
    conf = read_config(conf_path)
    if (p := settings_problem(conf)):
        raise BadInput(f"--online: {p}")
    client = _client(conf, dry_run=True)
    problems = []
    for t in texts:
        r = client._request("POST", "/parsers/dna-match", json={"string": t})
        want = parse_like_gramps_web(t)
        got = r.json() if r.status_code == 200 else None
        if got is None or len(got) != len(want):
            problems.append(f"the server read {len(got) if got is not None else 'HTTP %s' % r.status_code} segments, "
                            f"want {len(want)}: {t.splitlines()[1] if len(t.splitlines()) > 1 else t!r}")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gwt-import-dna", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("files", nargs="+", metavar="FILE", help="match list and/or segment CSV from the site")
    ap.add_argument("--site", required=True, choices=sorted(SITES))
    ap.add_argument("--tested", required=True, metavar="ID", help="Gramps ID of the person who took the test")
    ap.add_argument("--snapshot", required=True, metavar="DUMP", help="a gwt-check dump or gwt-apply snapshot")
    ap.add_argument("--out", required=True, help="the change list (JSON Lines); -01, -02... when it splits")
    ap.add_argument("--kit", help="a short label for this test in keys and notes (default: the tested ID)")
    ap.add_argument("--ruling", default=RULING, help="the ruling every op names (default: %(default)s)")
    ap.add_argument("--tag", default="dna-match", help="tag on each new match person; '' for none")
    ap.add_argument("--id-map", metavar="TSV", help="site<TAB>match id or name<TAB>Gramps ID: a match already in the tree")
    ap.add_argument("--column", action="append", default=[], metavar="FIELD=HEADER",
                    help="read FIELD from the column HEADER (fields: %s)" % ", ".join(
                        sorted(set(MATCH_COLUMNS) | set(SEGMENT_COLUMNS))))
    ap.add_argument("--batch", type=int, default=BATCH, help="ops per file at most (default: %(default)s)")
    ap.add_argument("--report", help="write the report (JSON) here")
    ap.add_argument("--online", action="store_true", help="also check every segment note with the server's parser")
    ap.add_argument("--config", help="with --online: gwt-apply's KEY=VALUE settings file")
    ap.add_argument("--force", action="store_true", help="overwrite --out and --report")
    a = ap.parse_args(argv)
    try:
        overrides = {}
        for c in a.column:
            field, _, header = c.partition("=")
            if field not in MATCH_COLUMNS and field not in SEGMENT_COLUMNS or not header:
                raise BadInput(f"--column {c}: want FIELD=HEADER, FIELD one of "
                               f"{sorted(set(MATCH_COLUMNS) | set(SEGMENT_COLUMNS))}")
            overrides[field] = header
        if a.batch < 4:
            raise BadInput("--batch is at least 4, one match's ops")
        matches, segments, cols = read_files(a.files, overrides)
        ambiguous = attach_segments(matches, segments)
        groups, rep = build(matches, load_tree(a.snapshot), site=a.site, tested=a.tested, kit=a.kit or a.tested,
                            ruling=a.ruling, tag=a.tag or None, id_map=read_id_map(a.id_map, a.site))
        rep["columns"] = cols
        rep["segments_ambiguous_name"] = ambiguous
        files = blocks(groups, a.batch)
        stem, ext = os.path.splitext(a.out)
        names = [a.out] if len(files) <= 1 else [f"{stem}-{i:02d}{ext}" for i in range(1, len(files) + 1)]
        for n in names + ([a.report] if a.report else []):
            if os.path.exists(n) and not a.force:
                raise BadInput(f"{n} exists; --force to overwrite")
        if a.online:
            problems = check_online([o["text"] for g in groups for o in g if o["key"].endswith("-segments")], a.config)
            if problems:
                print("\n".join(problems), file=sys.stderr)
                return 1
        for n, ops in zip(names, files or [[]]):
            with open(n, "w", encoding="utf-8") as fh:
                fh.writelines(json.dumps(op, ensure_ascii=False) + "\n" for op in ops)
        rep["files"] = names
        if a.report:
            with open(a.report, "w", encoding="utf-8") as fh:
                json.dump(rep, fh, indent=1, ensure_ascii=False)
    except BadInput as exc:
        print(f"gwt-import-dna: {exc}", file=sys.stderr)
        return 2
    ignored = sorted({h for c in cols.values() for h in c["ignored"]})
    print(f"{rep['matches']} matches: {rep['new_people']} new people, {len(rep['in_tree'])} already in the tree, "
          f"{len(rep['cross_site'])} found from another site, {len(rep['cross_ambiguous'])} ambiguous, "
          f"{rep['with_segments']} with segments; {sum(map(len, files))} ops in {len(names)} file(s)", file=sys.stderr)
    if ignored:
        print(f"columns not read: {', '.join(ignored)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
