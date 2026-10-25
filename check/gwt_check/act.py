"""Set one transcribed act beside the tree and say, field by field, what agrees and what does not.

The input is a small JSON file somebody wrote after reading an act: the kind of act, its date and place,
and the names it gives. Nothing here reads images, and nothing is written to the tree. For each person
whose name fits the act's subject the report says whether the tree's matching event has the same date,
the same place and the same parents, and whether the text of a citation of that event contains the act's reference. Agreeing names are not proof that two people are the same or related, and a string found in a citation does not show the record was checked against the act.
A person who is not in the tree is reported as absent: no person, family or link is proposed. A name
that fits two people lists both and picks neither.
"""

from __future__ import annotations

import datetime
import json
import re

from .dates import BAPTISM, BIRTH, BURIAL, DEATH, MARRIAGE, kind, span
from .fill import _fold, _name, _note_text
from .tree import BadInput, Tree

KINDS = {"birth": BIRTH, "baptism": BAPTISM, "death": DEATH, "burial": BURIAL, "marriage": MARRIAGE}
_ISO = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")


def _iso_ok(v) -> bool:
    m = _ISO.match(v) if isinstance(v, str) else None
    try:
        return bool(m) and bool(datetime.date(int(m[1]), int(m[2] or 1), int(m[3] or 1)))
    except ValueError:
        return False


def load_act(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            act = json.load(fh)
    except (OSError, ValueError) as exc:
        raise BadInput(f"{path}: {exc}") from exc
    if not isinstance(act, dict) or not isinstance(act.get("kind"), str) or act["kind"] not in KINDS:
        raise BadInput(f"{path}: 'kind' must be one of {', '.join(KINDS)}")
    for key in ("date", "registered"):
        if (key == "date" or act.get(key) is not None) and not _iso_ok(act.get(key)):
            raise BadInput(f"{path}: '{key}' must be text: YYYY, YYYY-MM or YYYY-MM-DD")
    for key in ("place", "reference"):
        if act.get(key) is not None and not isinstance(act[key], str):
            raise BadInput(f"{path}: '{key}' must be text")
    for key in ("subject", "father", "mother", "spouse"):
        who = act.get(key)
        if who is not None and not (isinstance(who, dict)
                                    and all(isinstance(who.get(k) or "", str) for k in ("given", "surname"))):
            raise BadInput(f"{path}: '{key}' must be an object whose given and surname are text")
    if not act.get("subject") or not (act["subject"].get("given") or act["subject"].get("surname")):
        raise BadInput(f"{path}: 'subject' needs a given name or a surname")
    if act["kind"] == "marriage" and not act.get("spouse"):
        raise BadInput(f"{path}: a marriage act needs a 'spouse'")
    return act


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[\s\-']+", _fold(s or "")) if t]


def _person_names(p: dict) -> tuple[list[str], list[str]]:
    n = p.get("primary_name") or {}
    sur = " ".join(x.get("surname") or "" for x in n.get("surname_list") or [])
    return _tokens(n.get("first_name") or ""), _tokens(sur)


def _fits(p: dict, who: dict) -> bool:
    """Every word of the act's name is in the person's name: the act may give fewer names than the tree."""
    g, s = _person_names(p)
    return bool(who) and set(_tokens(who.get("given"))) <= set(g) and set(_tokens(who.get("surname"))) <= set(s) \
        and bool(_tokens(who.get("given")) or _tokens(who.get("surname")))


def _fits_fewer(p: dict, who: dict) -> bool:
    g, s = _person_names(p)
    wg, ws = set(_tokens(who.get("given"))), set(_tokens(who.get("surname")))
    return bool(g or s) and set(g) <= wg and set(s) <= ws and bool(g) and bool(s) and (set(g) != wg or set(s) != ws)


def _date_status(tree_date: dict | None, iso: str, registered: str | None = None) -> str:
    m = _ISO.match(iso)
    y, mo, d = int(m[1]), int(m[2] or 0), int(m[3] or 0)
    want = span({"dateval": [d, mo, y, False], "modifier": 0, "quality": 0})
    have = span(tree_date)
    if want is None:
        return "unreadable act date"
    if not tree_date or not (tree_date.get("dateval") and any(tree_date["dateval"][:3])):
        return "tree has no date"
    if have is None:
        return "tree date cannot be read"
    if have.lo == want.lo and have.hi == want.hi:
        return "same"
    if have.hi < want.lo or have.lo > want.hi:
        if registered:
            r = _ISO.match(registered)
            reg = span({"dateval": [int(r[3] or 0), int(r[2] or 0), int(r[1]), False], "modifier": 0, "quality": 0})
            if reg and have.lo == reg.lo and have.hi == reg.hi:
                return f"differs (tree {have.text} matches the registered day, which is not the day of the event)"
        return f"differs (tree {have.text})"
    return f"compatible, not equal (tree {have.text})"


def _place_names(tree: Tree, h: str) -> list[str]:
    P = tree.objs["places"]
    out, todo, seen = [], [h], set()
    while todo:
        x = todo.pop()
        if x in seen or x not in P:
            continue
        seen.add(x)
        out.append(" ".join(_fold(_name(P[x])).split()))
        todo += [r.get("ref") for r in P[x].get("placeref_list") or []]
    return out


def _place_status(tree: Tree, ev: dict, place: str) -> str:
    if not place:
        return "act gives no place"
    if not ev.get("place") or ev["place"] not in tree.objs["places"]:
        return "tree has no place"
    names = _place_names(tree, ev["place"])
    if " ".join(_fold(place).split()) in names:
        return "same"
    return f"differs (tree {_name(tree.objs['places'][ev['place']])})"


def _cited(tree: Tree, ev: dict, ref: str) -> str:
    if not ref:
        return "act gives no reference"
    want = " ".join(_fold(ref).split())
    for ch in ev.get("citation_list") or []:
        c = tree.objs["citations"].get(ch)
        if not c or c.get("private"):
            continue
        parts = [c.get("page") or ""] + [_note_text(tree.objs["notes"][n]) for n in c.get("note_list") or []
                                         if n in tree.objs["notes"] and not tree.objs["notes"][n].get("private")]
        if re.search(r"(?<!\w)" + re.escape(want).replace(r"\ ", r"\s+") + r"(?!\w)", " ".join(_fold(" ".join(parts)).split())):
            return f"text found in {tree.gid('citations', ch)} (words only, the record itself is not checked)"
    return "no citation text contains it" if ev.get("citation_list") else "event has no citation"


def _parents(tree: Tree, p: dict, act: dict) -> dict:
    """Each named parent against every parental family of the person; one family that agrees is reported with the
    others that differ, never a global contradiction from the first family alone."""
    fams = [tree.objs["families"][h] for h in p.get("parent_family_list") or [] if h in tree.objs["families"]]
    out = {}
    for role, key in (("father", "father_handle"), ("mother", "mother_handle")):
        who = act.get(role)
        if not who:
            continue
        if not fams:
            out[role] = "tree has no parental family"
            continue
        per = []
        for f in fams:
            ph = f.get(key)
            gid = tree.gid("families", f["handle"])
            if not ph or ph not in tree.objs["people"]:
                per.append((gid, "none"))
            elif _fits(tree.objs["people"][ph], who):
                per.append((gid, "agree"))
            else:
                per.append((gid, "differ"))
        agree = [g for g, s in per if s == "agree"]
        other = [g for g, s in per if s != "agree"]
        if agree:
            out[role] = "names agree in " + ", ".join(agree) + (f"; not in {', '.join(other)}" if other else "")
        elif all(s == "none" for _, s in per):
            out[role] = "tree has no " + role + " in " + ", ".join(g for g, _ in per)
        else:
            out[role] = "names differ in " + ", ".join(g for g, s in per if s == "differ") + \
                ("; none recorded in " + ", ".join(g for g, s in per if s == "none") if any(s == "none" for _, s in per) else "")
    return out


def _events(tree: Tree, p: dict, types: set[str]) -> list[dict]:
    E = tree.objs["events"]
    return [E[r["ref"]] for r in p.get("event_ref_list") or []
            if r.get("role") == "Primary" and r.get("ref") in E and kind(E[r["ref"]].get("type")) in types]


def _spouse_families(tree: Tree, p: dict, spouse: dict) -> list[dict]:
    P, F = tree.objs["people"], tree.objs["families"]
    fams = [F[h] for h in p.get("family_list") or [] if h in F]
    return [f for f in fams if any(x in P and x != p["handle"] and _fits(P[x], spouse)
                                   for x in (f.get("father_handle"), f.get("mother_handle")))]


def compare_act(tree: Tree, act: dict, person: str | None = None) -> dict:
    """`person` is a Gramps ID: whoever transcribed the act names the person, and no name search is made."""
    types = KINDS[act["kind"]]
    P = tree.objs["people"]
    fewer = narrowed = False
    if person:
        cands = [p for p in P.values() if p.get("gramps_id") == person]
        if not cands:
            raise BadInput(f"--person {person}: no such person")
    else:
        cands = [p for p in P.values() if _fits(p, act["subject"])]
        if not cands:  # the tree may carry fewer names than the act: listed apart, never taken as a match
            cands = [p for p in P.values() if _fits_fewer(p, act["subject"])]
            fewer = bool(cands)
        if act["kind"] == "marriage" and len(cands) > 1:  # the spouse is a second name that picks the subject out
            wed = [p for p in cands if _spouse_families(tree, p, act["spouse"])]
            narrowed = bool(wed) and len(wed) < len(cands)
            cands = wed or cands
    rows = []
    for p in cands:
        if p.get("private"):  # counted, so a private namesake still blocks "one fits", but nothing of it is shown
            rows.append({"person": tree.gid("people", p["handle"]), "private": True})
            continue
        row = {"person": tree.gid("people", p["handle"]),
               "name": " ".join(_person_names(p)[0] + _person_names(p)[1])}
        if fewer:
            row["name_note"] = "the tree gives fewer names than the act"
        elif person and not _fits(p, act["subject"]):
            row["name_note"] = "the act's name does not fit this person"
        if act["kind"] == "marriage":
            fams = _spouse_families(tree, p, act["spouse"])
            evs = [e for f in fams for r in f.get("event_ref_list") or []
                   if (e := tree.objs["events"].get(r.get("ref"))) and kind(e.get("type")) in types]
            row["family"] = [tree.gid("families", f["handle"]) for f in fams]
        else:
            evs = _events(tree, p, types)
            row["parents"] = _parents(tree, p, act)
        if not evs:
            row["event"] = "tree has no such event" + (" (no family with that spouse)" if act["kind"] == "marriage" and not fams else "")
        else:
            row["events"] = [{"event": tree.gid("events", e["handle"]), "date": _date_status(e.get("date"), act["date"], act.get("registered")),
                              "place": _place_status(tree, e, act.get("place") or ""),
                              "cited": _cited(tree, e, act.get("reference") or "")} for e in evs]
        rows.append(row)
    if person:
        verdict = "the person was named, not searched for"
    elif not rows:
        verdict = "subject not in the tree"
    elif fewer:
        verdict = "the tree has people with fewer names than the act: none is taken as the subject, read each"
    elif len(rows) > 1:
        verdict = "several people's names fit the subject: none is chosen"
    else:
        verdict = "one person's name fits" + (" and has a family with that spouse" if narrowed else "")
    return {"act": {k: act.get(k) for k in ("kind", "date", "registered", "place", "reference")},
            "candidates": rows, "verdict": verdict}


def print_act(rep: dict) -> None:
    a = rep["act"]
    print(f"act: {a['kind']} {a['date']} {a['place'] or ''} {a['reference'] or ''}".rstrip()
          + (f" (registered {a['registered']}, not compared)" if a.get("registered") else ""))
    print(rep["verdict"])
    for r in rep["candidates"]:
        if r.get("private"):
            print(f"  {r['person']} (marked private: held back)")
            continue
        print(f"  {r['person']} {r['name']}" + (f"  ({r['name_note']})" if r.get("name_note") else ""))
        if "event" in r:
            print(f"    {r['event']}")
        for e in r.get("events", []):
            print(f"    {e['event']}: date {e['date']}; place {e['place']}; cited {e['cited']}")
        for role, s in (r.get("parents") or {}).items():
            print(f"    {role}: {s}")
    print("report only: agreeing names are not proof of identity or kinship, and a citation that contains the words is not a checked record; nothing was changed")
