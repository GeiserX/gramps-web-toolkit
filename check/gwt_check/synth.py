"""A large invented tree for scale tests: `python -m gwt_check.synth 20000 out.json.gz`.

Everything is generated from a seed: invented names, places and sources, no real data. The tree is clean
(every `gwt-check check` finding is a real finding about the generator) unless `--plant` is given.
"""

from __future__ import annotations

import argparse
import gzip
import json
import random

from .selftest import _base, _date, _ref

FIRST = ["Ana", "Luis", "Marta", "Pedro", "Elena", "Jorge", "Sara", "Pablo", "Rosa", "Hugo", "Clara", "Diego"]
LAST = ["Aldea", "Brezo", "Cantera", "Duero", "Encina", "Fresno", "Garza", "Hiedra", "Isla", "Junco", "Lomas", "Mora"]


def big_tree(n_people: int, seed: int = 1) -> dict:
    rnd = random.Random(seed)
    d: dict[str, list] = {t: [] for t in ("people", "families", "events", "places", "citations", "sources",
                                          "media", "notes", "repositories", "tags")}
    d["tags"].append(_base("", handle="htag1", name="run-1", color="#000000", priority=0))
    for i in range(1, 21):
        d["repositories"].append(_base(f"R{i:04d}", name=f"Archivo inventado {i}", type="Archive",
                                       address_list=[], urls=[], note_list=[]))
    n_places = max(10, n_people // 40)
    for i in range(1, n_places + 1):
        parent = [{"ref": f"hP{rnd.randint(1, i - 1):04d}", "date": _date()}] if i > 1 and i % 7 else []
        d["places"].append(_base(f"P{i:04d}", name={"value": f"Lugar {i}"}, place_type="Village",
                                 lat=f"{40 + rnd.random():.4f}", long=f"{-3 + rnd.random():.4f}",
                                 placeref_list=parent, citation_list=[], media_list=[], note_list=[],
                                 alt_names=[], urls=[]))
    n_src = max(10, n_people // 60)
    for i in range(1, n_src + 1):
        d["sources"].append(_base(f"S{i:04d}", title=f"Libro inventado {i}", author="", pubinfo="", abbrev="",
                                  attribute_list=[], media_list=[], note_list=[],
                                  reporef_list=[{"ref": f"hR{(i % 20) + 1:04d}", "call_number": "",
                                                 "media_type": "Book", "note_list": [], "private": False}]))
    counters = {"E": 0, "C": 0, "N": 0, "F": 0}

    def event(kind, year, person_place=True):
        counters["E"] += 1
        counters["C"] += 1
        eid, cid = f"E{counters['E']:06d}", f"C{counters['C']:06d}"
        d["citations"].append(_base(cid, source_handle=f"hS{rnd.randint(1, n_src):04d}", page=f"folio {counters['C']}",
                                    confidence=2, date=_date(), attribute_list=[], media_list=[], note_list=[]))
        d["events"].append(_base(eid, type=kind, date=_date(year, rnd.randint(1, 12), rnd.randint(1, 28)),
                                 place=f"hP{rnd.randint(1, n_places):04d}", description="", attribute_list=[],
                                 media_list=[], note_list=[], citation_list=["h" + cid]))
        return "h" + eid

    people: list[dict] = []

    def person(year, gender):
        i = len(people) + 1
        gid = f"I{i:06d}"
        b = event("Birth", year)
        refs = [_ref(b, role="Primary")]
        death = -1
        if year < 1930:
            refs.append(_ref(event("Death", year + rnd.randint(62, 90)), role="Primary"))
            death = 1
        p = _base(gid, gender=gender, birth_ref_index=0, death_ref_index=death,
                  primary_name={"first_name": rnd.choice(FIRST), "type": "Birth Name", "private": False,
                                "citation_list": [], "note_list": [], "date": _date(),
                                "surname_list": [{"surname": rnd.choice(LAST) + str(rnd.randint(0, 250)), "primary": True, "prefix": "",
                                                  "connector": "", "origintype": ""}]},
                  alternate_names=[], event_ref_list=refs, family_list=[], parent_family_list=[],
                  citation_list=[], note_list=[], media_list=[], attribute_list=[], address_list=[],
                  lds_ord_list=[], person_ref_list=[], urls=[])
        people.append(p)
        return p

    queue = [(person(1700, 0), 1700)]
    qi = 0
    while len(people) < n_people:
        if qi >= len(queue):
            queue.append((person(1700 + rnd.randint(0, 10), rnd.randint(0, 1)), 1700))
        parent, year = queue[qi]
        qi += 1
        spouse = person(year + rnd.randint(-4, 4), 1 - parent["gender"] if parent["gender"] in (0, 1) else 1)
        counters["F"] += 1
        fid = f"F{counters['F']:06d}"
        kids = []
        for _ in range(rnd.randint(1, 4)):
            if len(people) >= n_people:
                break
            c = person(year + rnd.randint(22, 34), rnd.randint(0, 1))
            c["parent_family_list"].append("h" + fid)
            kids.append(c)
            queue.append((c, year + 28))
        father, mother = (parent, spouse) if parent["gender"] == 1 else (spouse, parent)
        parent["family_list"].append("h" + fid)
        spouse["family_list"].append("h" + fid)
        d["families"].append(_base(fid, father_handle=father["handle"], mother_handle=mother["handle"],
                                   type="Married", complete=0,
                                   child_ref_list=[_ref(c["handle"], frel="Birth", mrel="Birth") for c in kids],
                                   event_ref_list=[_ref(event("Marriage", year + 22), role="Family")],
                                   attribute_list=[], citation_list=[], lds_ord_list=[], media_list=[],
                                   note_list=[], tag_list=[]))
    d["people"] = people
    for i in range(1, n_people // 10 + 1):
        d["notes"].append(_base(f"N{i:06d}", type="General", format=0,
                                text={"_class": "StyledText", "string": f"Nota inventada {i}", "tags": []}))
        people[(i - 1) * 7 % n_people]["note_list"].append(f"hN{i:06d}")
    return d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("people", type=int)
    ap.add_argument("out")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    data = big_tree(a.people, a.seed)
    op = gzip.open if a.out.endswith(".gz") else open
    with op(a.out, "wt") as fh:
        json.dump({"tree": "synthetic", "objects": data}, fh)
    print({k: len(v) for k, v in data.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
