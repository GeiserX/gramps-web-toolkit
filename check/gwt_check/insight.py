"""Read-only reading aid: what the tree's citations and notes say, where they seem to disagree, and where to look next.

It proves nothing and decides nothing. The output quotes citations and notes of the tree, so it is as private as the tree.

Five readings of a dump, none of which writes anything or decides a fact:

- citations:     the act number, image, year and link hidden in each citation's free text, acts that several citations draw on,
                 and citations whose act year differs from the event they back (a possible mismatch to review)
- discrepancies: the conflicts a researcher wrote into notes, listed with the object they sit on and the
                 citations they name, so each one can be closed when the missing document arrives
- fill:          events with no place or no date, with the passage of their own citations that may hold it
                 (a proposal with the quote next to it, never a change)
- frontier:      people without recorded parents, ordered by how workable the search is
- witnesses:     names transcribed as witnesses or godparents, and the names that recur in separate acts
                 (a lead to check the transcription against, never a relationship)

Every finding quotes the text it came from. A reading that cannot be made safely is counted, not guessed.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata

from .checks import norm, person_name
from .dates import BIRTH, BAPTISM, DEATH, MARRIAGE, kind, span
from .tree import Tree

SECTIONS = ("citations", "discrepancies", "fill", "frontier", "witnesses")
BORN_AFTER = 1925  # outside searches skip anyone born after this year without a death date

_ACT = re.compile(r"\b(?:acte|acta|act|n[°º]|no\.?)\s*(?:n[°º]\s*)?(\d+)", re.I)
_IMAGE = re.compile(r"\b(?:media|imagen|image|vue|vista|view|scan|folio|p[aá]gina|page)\s*(\d+(?:\s*[-/]\s*\d+)?)", re.I)
_YEAR = re.compile(r"\b(1[4-9]\d\d)\b")
_URL = re.compile(r"https?://[^\s|)\]]+")
_CITE_ID = re.compile(r"\bC\d{4,6}\b")
_DISCREPANCY = re.compile(r"discrepan|diverg|difiere|differ|contradic|conflict|\bvs\.?\b|≠|no coincide|disagree", re.I)
_RESOLVED = re.compile(r"\b(?:resuelt[ao]|resolved|zanjad[ao]|settled)\b", re.I)
_KEPT = re.compile(r"se conserva|se mantiene|canónic|canonic|retained|we keep|kept as|preferred reading|se prefiere|se adopta", re.I)
_NEGATIVE = re.compile(r"negativ|no hallad|not found|sin resultado|aucun r[ée]sultat|ningún", re.I)
_WITNESS = re.compile(r"(?:t[ée]moins?|testigos?|witnesses|parrains?|padrinos?|godparents?)\s*:?\s*", re.I)
_PERSON = re.compile(r"([A-ZÁÉÍÓÚÑÀ-Ý][\wÀ-ÿ'’-]+(?:\s+[A-ZÁÉÍÓÚÑÀ-Ý][\wÀ-ÿ'’-]+){1,3})\s*\((\d{1,3})\s*(?:a[ñn]os?|ans?|years?|,|\))")
_ACT_YEAR = re.compile(r"\bn[°ºo]?\s*(\d+)\s*/\s*(1[4-9]\d\d)\b", re.I)
_KIND_WORDS = {"birth": re.compile(r"naissance|nacimiento|birth|naci[óo]", re.I), "death": re.compile(r"d[ée]c[èe]s|defunci|death", re.I),
               "marriage": re.compile(r"mariage|matrimonio|marriage", re.I)}
_MARGIN = re.compile(r"margen|marge|marginal|mention|menci[óo]n|nota", re.I)
_FULL_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(1[4-9]\d\d)\b")


def _text(note: dict) -> str:
    t = note.get("text")
    return (t.get("string") if isinstance(t, dict) else t) or ""


def _snip(text: str, at: int, width: int = 90) -> str:
    lo = max(0, at - width // 3)
    return re.sub(r"\s+", " ", text[lo:lo + width]).strip()


def _owners(tree: Tree) -> dict[str, list[tuple[str, str]]]:
    """note handle -> [(type, handle)] of every object (and family child link) that carries it."""
    out: dict[str, list[tuple[str, str]]] = {}
    for t, objs in tree.objs.items():
        for h, o in objs.items():
            notes = list(o.get("note_list") or [])
            for c in o.get("child_ref_list") or []:
                notes += c.get("note_list") or []
            for n in notes:
                out.setdefault(n, []).append((t, h))
    return out


def _cite_notes(tree: Tree, cit: dict) -> str:
    return "\n".join(_text(tree.objs["notes"][n]) for n in cit.get("note_list") or [] if n in tree.objs["notes"])


def parse_citation(page: str) -> dict:
    """The structured facts a citation's free text states; a missing key means the text does not say."""
    out: dict = {}
    if m := _ACT.search(page):
        out["act"] = int(m[1])
    if m := _IMAGE.search(page):
        out["image"] = re.sub(r"\s+", "", m[1])
    if m := _ACT_YEAR.search(page):
        out["act"], out["year"], out["act_year"] = int(m[1]), int(m[2]), True  # "n°5/1851": the act's own year
    elif m := _YEAR.search(page):
        out["year"] = int(m[1])
    if m := _URL.search(page):
        out["url"] = m[0].rstrip(".,;")
    return out


def citations_report(tree: Tree) -> dict:
    cites = tree.objs["citations"]
    parsed = {h: parse_citation(c.get("page") or "") for h, c in cites.items()}
    by_event: dict[str, list[str]] = {}
    for h, e in tree.objs["events"].items():
        for c in e.get("citation_list") or []:
            by_event.setdefault(c, []).append(h)
    dated = sum(1 for c in cites.values() if span(c.get("date")))
    seen: dict[tuple, list[str]] = {}
    for h, p in parsed.items():
        if "act" in p and "year" in p:
            seen.setdefault((cites[h].get("source_handle"), p["act"], p["year"]), []).append(h)
    twice = [{"citations": [tree.gid("citations", h) for h in hs], "act": k[1], "year": k[2],
              "source": tree.gid("sources", k[0]),
              "quotes": {tree.gid("citations", h): _snip(cites[h].get("page") or "", 0, 100) for h in hs}}
             for k, hs in sorted(seen.items(), key=lambda kv: str(kv[0])) if len(hs) > 1]
    odd = []
    for h, p in parsed.items():
        if not p.get("act_year"):
            continue  # a bare year may be the register's, a fiche's or a range's: not compared
        for eh in by_event.get(h, []):
            ev = tree.objs["events"][eh]
            k = kind(ev.get("type"))
            sp = span(ev.get("date"))
            if not sp or k not in BIRTH | DEATH | MARRIAGE | BAPTISM:
                continue
            group = "birth" if k in BIRTH | BAPTISM else "death" if k in DEATH else "marriage"
            page = cites[h].get("page") or ""
            # only a citation that says it is this kind of act, and is not a margin note on an older act
            if not _KIND_WORDS[group].search(page) or _MARGIN.search(page) or any(
                    w.search(page) for g, w in _KIND_WORDS.items() if g != group):
                continue
            lo_year, hi_year = _year_of(sp.lo), _year_of(sp.hi)
            if p["year"] < lo_year or p["year"] > hi_year + 1:
                odd.append({"citation": tree.gid("citations", h), "event": tree.gid("events", eh), "event_kind": k,
                            "event_date": sp.text, "text_year": p["year"],
                            "quote": _snip(cites[h].get("page") or "", 0, 100)})
    return {
        "total": len(cites),
        "with_act_number": sum(1 for p in parsed.values() if "act" in p),
        "with_image": sum(1 for p in parsed.values() if "image" in p),
        "with_year": sum(1 for p in parsed.values() if "year" in p),
        "with_link": sum(1 for p in parsed.values() if "url" in p),
        "with_own_date": dated,
        "same_act_in_several_citations": twice,
        "year_outside_event": sorted(odd, key=lambda x: x["citation"]),
        "note": "possible mismatches to review, not errors: only citations that name the same kind of act as the event, in the form n°5/1851 and not as a margin note, are compared, and an act of a child or a marriage can back a parent's event with its own year",
    }


def _year_of(ordinal: int) -> int:
    import datetime
    try:
        return datetime.date.fromordinal(max(1, min(ordinal, 3652059))).year
    except ValueError:
        return 0


def discrepancies_report(tree: Tree) -> dict:
    owners = _owners(tree)
    found = []
    for h, n in tree.objs["notes"].items():
        text = _text(n)
        m = _DISCREPANCY.search(text)
        if not m:
            continue
        found.append({
            "note": tree.gid("notes", h),
            "on": sorted(tree.gid(t, oh) for t, oh in owners.get(h, [])),
            "status": "resolved" if _RESOLVED.search(text) else "one reading kept" if _KEPT.search(text) else "open",
            "citations_named": sorted(set(_CITE_ID.findall(text)))[:8],
            "quote": _snip(text, m.start(), 160),
        })
    count = {k: sum(1 for f in found if f["status"] == k) for k in ("open", "one reading kept", "resolved")}
    return {"notes_with_a_conflict_marker": len(found), **count, "items": sorted(found, key=lambda f: ({"open": 0, "one reading kept": 1}.get(f["status"], 2), f["note"])),
            "note": "HEURISTIC: the status is read from words in the note, not proven: resolved (the note says so), one reading kept (it names a reading it keeps, the other is not necessarily closed), open (neither). Read the quote"}


def fill_report(tree: Tree) -> dict:
    places = {h: p for h, p in tree.objs["places"].items()}

    def pname(p: dict) -> str:
        n = p.get("name")
        return ((n.get("value") if isinstance(n, dict) else n) or p.get("title") or "").strip()

    names = {h: pname(p) for h, p in places.items() if len(pname(p)) >= 4}
    out_place, out_date = [], []
    no_place = no_date = 0
    for h, e in tree.objs["events"].items():
        text = ""
        for ch in e.get("citation_list") or []:
            c = tree.objs["citations"].get(ch)
            if c:
                text += "\n" + (c.get("page") or "") + "\n" + _cite_notes(tree, c)
        gid = tree.gid("events", h)
        if not e.get("place"):
            no_place += 1
            hits = {ph: m for ph, nm in names.items() if (m := re.search(rf"\b{re.escape(nm)}\b", text, re.I))}
            if len(hits) == 1:
                ph, m = next(iter(hits.items()))
                out_place.append({"event": gid, "kind": kind(e.get("type")), "place": tree.gid("places", ph), "name": names[ph],
                                  "quote": _snip(text, m.start(), 110)})
            elif hits:
                out_place.append({"event": gid, "kind": kind(e.get("type")), "ambiguous": sorted(names[p] for p in hits)})
        if not span(e.get("date")):
            no_date += 1
            dates = {m[0]: m for m in _FULL_DATE.finditer(text)}
            if len(dates) == 1:
                m = next(iter(dates.values()))
                out_date.append({"event": gid, "kind": kind(e.get("type")), "date": f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}",
                                 "quote": _snip(text, m.start(), 110)})
    return {"events_without_place": no_place, "events_without_readable_date": no_date,
            "place_proposals": out_place, "date_proposals": out_date,
            "note": "a proposal quotes the citation text it comes from; a citation that names several people or dates can point at the wrong one"}


def _birth(tree: Tree, person: dict):
    idx = person.get("birth_ref_index", -1)
    refs = person.get("event_ref_list") or []
    ev = None
    if isinstance(idx, int) and 0 <= idx < len(refs):
        ev = tree.objs["events"].get(refs[idx].get("ref"))
    if ev is None:
        for r in refs:
            e = tree.objs["events"].get(r.get("ref"))
            if e and kind(e.get("type")) in BIRTH:
                ev = e
                break
    return ev


def frontier_report(tree: Tree, archives: dict | None = None, searched: dict | None = None) -> dict:
    """People with no parents on record, most workable first. `archives` maps a place name to
    {"from": year, "to": year, "online": bool}: what is known to be readable there."""
    owners = _owners(tree)
    rows, skipped = [], 0
    for h, p in tree.objs["people"].items():
        if p.get("parent_family_list"):
            continue
        ev = _birth(tree, p)
        sp = span(ev.get("date")) if ev else None
        year = _year_of(sp.lo) if sp else None
        died = any(kind((tree.objs["events"].get(r.get("ref")) or {}).get("type")) in DEATH for r in p.get("event_ref_list") or [])
        if year and year > BORN_AFTER and not died:
            skipped += 1
            continue
        place = tree.objs["places"].get(ev.get("place")) if ev and ev.get("place") else None
        pn = ""
        if place:
            n = place.get("name")
            pn = ((n.get("value") if isinstance(n, dict) else n) or place.get("title") or "").strip()
        texts = "\n".join(_text(tree.objs["notes"][n]) for n in p.get("note_list") or [] if n in tree.objs["notes"])
        negative = bool(_NEGATIVE.search(texts))
        score, why = 0, []
        if year:
            score += 2
            why.append(f"birth year {year}")
        if pn:
            score += 2
            why.append(f"birth place {pn}")
        cites = len(p.get("citation_list") or [])
        if cites:
            score += min(cites, 3)
            why.append(f"{cites} citation(s)")
        arch = (archives or {}).get(pn) if pn else None
        if arch and year and arch.get("from", 0) <= year <= arch.get("to", 9999):
            score += 3 if arch.get("online") else -1
            why.append("records online there" if arch.get("online") else "records exist but are not online")
        if negative:
            score -= 3
            why.append("a note records a negative search")
        if not year and not pn:
            score -= 2
            why.append("no birth date or place")
        row = {"person": tree.gid("people", h), "score": score, "why": why}
        if searched is not None:
            last = _last_search(searched, tree.gid("people", h), p)
            row["last_searched"] = last
        rows.append(row)
    rows.sort(key=lambda r: (-r["score"], r["person"]))
    given = bool(archives)
    return {"without_parents": len(rows) + skipped, "listed": len(rows), "skipped_possibly_living": skipped, "ordered": rows,
            "archives_given": given, "searched_given": searched is not None,
            "note": f"people with a birth year after {BORN_AFTER} and no death event are left out (people with no birth year are kept). "
                    + ("The order uses the archives file given." if given else
                       "PARTIAL: no --archives file, so the order ignores which records are readable and online; it uses birth year, birth place, citations and recorded negative searches only.")
                    + " The score only orders work, it proves nothing"}


def witnesses_report(tree: Tree) -> dict:
    appearances: dict[str, set[str]] = {}
    acts = with_names = 0
    assoc = sum(len(p.get("person_ref_list") or []) for p in tree.objs["people"].values())
    for ch, c in tree.objs["citations"].items():
        text = (c.get("page") or "") + "\n" + _cite_notes(tree, c)
        acts += 1
        names = set()
        for m in _WITNESS.finditer(text):
            seg = re.split(r"[.;\n]\s*(?=[A-ZÁÉÍÓÚ])|oficial|officier|officer", text[m.end(): m.end() + 260])[0]
            for pm in _PERSON.finditer(seg):
                names.add(re.sub(r"\s+", " ", pm[1]).strip())
        if names:
            with_names += 1
            for n in names:
                appearances.setdefault(n.lower(), set()).add(tree.gid("citations", ch))
    linked: set[frozenset] = set()  # people (by name words) that carry or are the target of a recorded association
    for p in tree.objs["people"].values():
        refs = p.get("person_ref_list") or []
        targets = [tree.objs["people"].get(r.get("ref")) for r in refs]
        for q in ([p] if refs else []) + [t for t in targets if t]:
            linked.add(frozenset(person_name(q).split()))
    recurring = [{"name": k, "citations": sorted(v), "association_recorded": frozenset(norm(k).split()) in linked}
                 for k, v in sorted(appearances.items()) if len(v) >= 2]
    return {"citations": acts, "citations_with_witness_names": with_names, "distinct_names": len(appearances),
            "names_in_two_or_more_citations": recurring, "recorded_associations": assoc,
            "recurring_without_recorded_association": sum(1 for r in recurring if not r["association_recorded"]),
            "note": "a name that recurs may be a namesake; a witness is not a relative. Check the transcription in the image before using one as a lead. association_recorded only says that a person with the same name words carries or is the target of an association in the tree: it does not say they are the same person"}


def _slug(text: str) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    return re.sub(r"[^0-9a-z]+", "-", plain).strip("-")


def read_searched(path: str) -> dict:
    """A gwt-ledger TSV (person_key, person_label, source, query, date, result, ...) -> {slug of key or label: [(date, result, source)]}."""
    out: dict[str, list[tuple[str, str, str]]] = {}
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh):
            f = line.rstrip("\n").split("\t") + [""] * 9
            if n == 0 and f[0] == "person_key":
                continue
            for k in {_slug(f[0]), _slug(f[1])} - {""}:
                out.setdefault(k, []).append((f[4], f[5], f[2]))
    return out


def _last_search(searched: dict, gid: str, person: dict):
    n = person.get("primary_name") or {}
    name = " ".join([n.get("first_name", "")] + [s.get("surname", "") for s in n.get("surname_list", [])])
    rows = sorted(set(searched.get(_slug(gid), []) + searched.get(_slug(name), [])))  # a row found by key and by label counts once
    if not rows:
        return None
    d, r, src = max(rows)
    return {"date": d, "result": r, "source": src, "searches": len(rows)}


def insight(tree: Tree, only=None, archives: dict | None = None, searched: dict | None = None) -> dict:
    todo = [s for s in SECTIONS if not only or s in only]
    fn = {"citations": citations_report, "discrepancies": discrepancies_report, "fill": fill_report,
          "witnesses": witnesses_report, "frontier": lambda t: frontier_report(t, archives, searched)}
    return {s: fn[s](tree) for s in todo}


def print_insight(rep: dict, limit: int = 15) -> None:
    def head(t):
        print(f"\n== {t}")

    if "citations" in rep:
        c = rep["citations"]
        head("citations")
        print(f"  ({c['note']})")
        print(f"{c['total']} citations: act number in {c['with_act_number']}, image in {c['with_image']}, year in {c['with_year']}, "
              f"link in {c['with_link']}, own date in {c['with_own_date']}")
        for x in c["year_outside_event"][:limit]:
            print(f"  possible mismatch: {x['citation']} says {x['text_year']} but {x['event']} ({x['event_kind']}) is {x['event_date']}: {x['quote']}")
        for x in c["same_act_in_several_citations"][:limit]:
            print(f"  same act {x['act']}/{x['year']} ({x['source']}) cited by {', '.join(x['citations'])} (possible duplicates to review, nothing is merged)")
            for gid, q in x["quotes"].items():
                print(f"      {gid}: {q}")
    if "discrepancies" in rep:
        d = rep["discrepancies"]
        head("discrepancies")
        print(f"  ({d['note']})")
        print(f"{d['notes_with_a_conflict_marker']} notes with a conflict marker: {d['open']} open, "
              f"{d['one reading kept']} keep one reading, {d['resolved']} say resolved")
        for x in d["items"][:limit]:
            print(f"  [{x['status']}] {x['note']} on {', '.join(x['on']) or '-'}: {x['quote']}")
    if "fill" in rep:
        f = rep["fill"]
        head("fill")
        print(f"  ({f['note']})")
        print(f"{f['events_without_place']} events without place, {f['events_without_readable_date']} without a readable date")
        for x in f["place_proposals"][:limit]:
            print("  " + (f"{x['event']} ({x['kind']}) place may be {x['name']} {x['place']}: {x['quote']}" if "place" in x
                          else f"{x['event']} ({x['kind']}) several places named: {', '.join(x['ambiguous'])}"))
        for x in f["date_proposals"][:limit]:
            print(f"  {x['event']} ({x['kind']}) date may be {x['date']}: {x['quote']}")
    if "frontier" in rep:
        f = rep["frontier"]
        head("frontier")
        print(f"  ({f['note']})")
        print(f"{f['without_parents']} people without parents ({f['skipped_possibly_living']} left out as possibly living)")
        for x in f["ordered"][:limit]:
            ls = x.get("last_searched")
            tail = "" if "last_searched" not in x else (f"; last searched {ls['date']} ({ls['result']}, {ls['source']}, {ls['searches']} search(es))" if ls else "; not in the search ledger")
            print(f"  {x['score']:>3} {x['person']}: {'; '.join(x['why'])}{tail}")
    if "witnesses" in rep:
        w = rep["witnesses"]
        head("witnesses")
        print(f"  ({w['note']})")
        print(f"{w['citations_with_witness_names']} of {w['citations']} citations carry transcribed witness or godparent names "
              f"({w['distinct_names']} names, {w['recorded_associations']} associations recorded)")
        print(f"{w['recurring_without_recorded_association']} of the {len(w['names_in_two_or_more_citations'])} names that recur have no association recorded")
        for x in sorted(w["names_in_two_or_more_citations"], key=lambda r: r["association_recorded"])[:limit]:
            print(f"  {x['name']}: {', '.join(x['citations'])}" + ("" if x["association_recorded"] else "  [no association recorded]"))


def dumps(rep: dict) -> str:
    return json.dumps(rep, ensure_ascii=False, indent=1)


CSV_COLUMNS = ("event", "kind", "proposal_type", "proposal", "other_places_named", "citation_text", "decision")


def _cell(v: str) -> str:
    """A spreadsheet reads a leading = + - @ as a formula: keep quoted text text."""
    return "'" + v if v[:1] in ("=", "+", "-", "@") else v


def proposals_rows(rep: dict) -> list[list[str]]:
    f = rep["fill"]
    rows = []
    for x in f["place_proposals"]:
        if "place" in x:
            rows.append([x["event"], x["kind"], "place", f"{x['name']} ({x['place']})", "", x["quote"], ""])
        else:
            rows.append([x["event"], x["kind"], "place", "", "; ".join(x["ambiguous"]), "", ""])
    for x in f["date_proposals"]:
        rows.append([x["event"], x["kind"], "date", x["date"], "", x["quote"], ""])
    return rows


def write_proposals_csv(rep: dict, path: str) -> int:
    """One row per fill proposal with an empty `decision` column to mark accept or reject. Writes only this file
    (never the tree) and refuses to replace a file that exists. The BOM lets Excel read the accents."""
    rows = proposals_rows(rep)
    with open(path, "x", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        for r in rows:
            w.writerow([_cell(c) for c in r])
    return len(rows)
