"""Places for events that have none, read from the event's own citations. Proposals only: nothing is written.

A candidate is a place some event of the tree already uses, named in the page text or the notes of a
citation of the event, ignoring case and accents. When the text names a place and one that encloses it
("Villaejemplo, Provincia Imaginaria"), the enclosing one is dropped. One place left is a proposal; more
than one is listed as ambiguous. A citation can name where the record was made rather than where the event
happened, so every proposal carries its quote and is read against the record before it is applied.
"""

from __future__ import annotations

import json
import re
import unicodedata

from .dates import kind
from .tree import Tree

MIN_NAME = 4  # shorter names match inside too many words


def _fold(text: str) -> str:
    """Lowercase without accents, one character for one, so a match's offsets still point into the text."""
    out = []
    for c in text:
        b = "".join(x for x in unicodedata.normalize("NFD", c) if not unicodedata.combining(x))
        b = b if len(b) == 1 else c  # Hangul and the like decompose into letters, not accents: kept whole
        lo = b.lower()
        out.append(lo if len(lo) == 1 else b)
    return "".join(out)


def _name(place: dict) -> str:
    n = place.get("name")
    return ((n.get("value") if isinstance(n, dict) else n) or place.get("title") or "").strip()


def _note_text(note: dict) -> str:
    t = note.get("text")
    return (t.get("string") if isinstance(t, dict) else t) or ""


def _enclosing(places: dict, h: str) -> set[str]:
    """Every place that encloses h, through placeref_list."""
    seen: set[str] = set()
    todo = [r.get("ref") for r in places.get(h, {}).get("placeref_list") or []]
    while todo:
        p = todo.pop()
        if p in seen or p not in places:
            continue
        seen.add(p)
        todo += [r.get("ref") for r in places[p].get("placeref_list") or []]
    return seen


def propose_places(tree: Tree) -> dict:
    T = tree.objs
    P, E, C, N = T["places"], T["events"], T["citations"], T["notes"]
    by_name: dict[str, set[str]] = {}
    for h in {e.get("place") for e in E.values()} & P.keys():
        nm = " ".join(_fold(unicodedata.normalize("NFC", _name(P[h]))).split())
        if len(nm) >= MIN_NAME:
            by_name.setdefault(nm, set()).add(h)
    # one pattern for every name, longest first, compiled once
    rx = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(n).replace(r"\ ", r"\s+")
                                             for n in sorted(by_name, key=len, reverse=True)) + r")(?!\w)") if by_name else None
    from .cites import _owners, citation_problem  # here: cites imports this module
    own = _owners(tree)
    from .cites import _owners, citation_problem  # here: cites imports this module
    own = _owners(tree)
    proposals, ambiguous, held, without = [], [], [], 0

    def places_in(text: str):
        if rx is None or not text.strip():
            return {}, []
        hits: dict[str, int] = {}
        for m in rx.finditer(_fold(text)):
            for ph in by_name[" ".join(m[0].split())]:
                hits.setdefault(ph, m.start())
        enclosing = set().union(*(_enclosing(P, ph) for ph in hits)) if hits else set()
        return hits, sorted((ph for ph in hits if ph not in enclosing), key=lambda ph: tree.gid("places", ph))

    for h, e in E.items():
        if e.get("place"):
            continue
        without += 1
        parts, flagged, why = [], [], ""
        for ch in e.get("citation_list") or []:
            c = C.get(ch)
            if not c:
                continue
            text = [c.get("page") or ""] + [_note_text(N[n]) for n in c.get("note_list") or [] if n in N]
            prob = citation_problem(tree, e, own.get(h), c)
            (flagged if prob else parts).extend(text)
            why = why or (prob[1] if prob else "")
        text = unicodedata.normalize("NFC", "\n".join(p for p in parts if p))
        hits, most = places_in(text)
        row = {"event": tree.gid("events", h), "kind": kind(e.get("type"))}
        if not most:
            # a place only a citation about someone else or another act names is held back, not proposed
            _, only = places_in(unicodedata.normalize("NFC", "\n".join(p for p in flagged if p)))
            if only:
                held.append({**row, "places": [f"{tree.gid('places', ph)} {_name(P[ph])}" for ph in only], "why": why})
            continue
        if len(most) == 1:
            at = hits[most[0]]
            lo = max(0, at - 40)
            proposals.append({**row, "place": tree.gid("places", most[0]), "name": _name(P[most[0]]),
                              "quote": " ".join(text[lo:lo + 120].split())})
        else:
            ambiguous.append({**row, "places": [f"{tree.gid('places', ph)} {_name(P[ph])}" for ph in most]})
    return {"events_without_place": without, "proposals": proposals, "ambiguous": ambiguous, "held_back": held}


def print_places(rep: dict, limit: int) -> None:
    print(f"{rep['events_without_place']} events without place: {len(rep['proposals'])} with one place named "
          f"in their citations, {len(rep['ambiguous'])} with several")
    for x in rep["proposals"][:limit]:
        print(f"  {x['event']} ({x['kind']}): {x['place']} {x['name']}  «{x['quote']}»")
    for x in rep["ambiguous"][:limit]:
        print(f"  {x['event']} ({x['kind']}): several places: {'; '.join(x['places'])}")
    if rep.get("held_back"):
        print(f"held back, not proposed: {len(rep['held_back'])} (the only citations are about someone else or "
              f"another act; gwt-check cites says which; --json lists them)")
    print("proposals only: read each record before applying one (the text may name where the record was made)")


def write_ops(rep: dict, path: str) -> int:
    """One gwt-apply update_event op per proposal, with an empty ruling: gwt-apply refuses the file until
    someone has read each record and written the ruling. Refuses to replace a file that exists."""
    with open(path, "x", encoding="utf-8") as fh:
        for x in rep["proposals"]:
            fh.write(json.dumps({"op": "update_event", "key": f"fill-place-{x['event']}", "ruling": "",
                                 "event": x["event"], "set": {"place": x["place"]}}, ensure_ascii=False) + "\n")
    return len(rep["proposals"])
