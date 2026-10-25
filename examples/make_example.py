#!/usr/bin/env python3
"""Write tree.json.gz: an invented 30-person tree as a gwt-apply snapshot, for the offline demo.

It starts from the eight-person tree `gwt check selftest` plants its errors in and adds 22 more invented
people around it: spouses, siblings and two more generations. Every name, place and date is made up.

    python3 make_example.py            # from examples/, with the toolkit installed (or PYTHONPATH=../check)

The output is the same on every run (no timestamps), so regenerating it gives a file with the same content.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

from gwt_check.selftest import _base, _date, _ref, clean_tree

# (gramps id, first name, surname, gender 1=male 0=female, birth year)
PEOPLE = [
    ("I0009", "Julián", "Herrero Gil", 1, 1897),
    ("I0010", "Pilar", "Herrero Ortega", 0, 1925),
    ("I0011", "Ramón", "Herrero Ortega", 1, 1927),
    ("I0012", "Teresa", "Herrero Ortega", 0, 1930),
    ("I0013", "Andrea", "Ortega Ruiz", 0, 1873),
    ("I0014", "Felipe", "Ortega Ruiz", 1, 1876),
    ("I0015", "Jacinto", "Sanz Vidal", 1, 1875),
    ("I0016", "Óscar", "Molina Rey", 1, 1922),
    ("I0017", "Marta", "Molina Herrero", 0, 1950),
    ("I0018", "Diego", "Molina Herrero", 1, 1953),
    ("I0019", "Sofía", "Cano Ruiz", 0, 1929),
    ("I0020", "Pablo", "Herrero Cano", 1, 1955),
    ("I0021", "Laura", "Herrero Cano", 0, 1958),
    ("I0022", "Bruno", "Lago Mena", 1, 1870),
    ("I0023", "Elvira", "Lago Ortega", 0, 1898),
    ("I0024", "Simón", "Lago Ortega", 1, 1901),
    ("I0025", "Nuria", "Pardo Gil", 0, 1878),
    ("I0026", "Hugo", "Ortega Pardo", 1, 1905),
    ("I0027", "Alba", "Serra Font", 0, 1877),
    ("I0028", "Irene", "Sanz Serra", 0, 1904),
    ("I0029", "Luis", "Navarro Rey", 1, 1928),
    ("I0030", "Carmen", "Navarro Herrero", 0, 1956),
]

# (gramps id, father, mother, children, marriage year); children of a family the selftest tree has go in KIDS
FAMILIES = [
    ("F0004", "I0009", "I0001", ["I0010", "I0011", "I0012"], 1924),
    ("F0005", "I0016", "I0010", ["I0017", "I0018"], 1949),
    ("F0006", "I0011", "I0019", ["I0020", "I0021"], 1954),
    ("F0007", "I0022", "I0013", ["I0023", "I0024"], 1897),
    ("F0008", "I0014", "I0025", ["I0026"], 1904),
    ("F0009", "I0015", "I0027", ["I0028"], 1903),
    ("F0010", "I0029", "I0012", ["I0030"], 1955),
]
KIDS = {"F0002": ["I0013", "I0014"], "F0003": ["I0015"]}  # more children of Gil and Inés, of Blas and Rosa


def _event(gid: str, type_: str, year: int, day: int) -> dict:
    return _base(gid, type=type_, date=_date(year, 5, day), place="hP0001", description="",
                 attribute_list=[], media_list=[], note_list=[], citation_list=[])


def build() -> dict:
    d = clean_tree()
    kids = {gid: list(v) for gid, v in KIDS.items()}
    people = {p["gramps_id"]: p for p in d["people"]}
    for n, (gid, first, surname, gender, year) in enumerate(PEOPLE, 1):
        d["events"].append(_event(f"E03{n:02d}", "Birth", year, n))
        person = json.loads(json.dumps(people["I0008"]))  # same shape as a selftest person
        person.update(handle="h" + gid, gramps_id=gid, gender=gender, birth_ref_index=0, death_ref_index=-1,
                      event_ref_list=[_ref(f"hE03{n:02d}", role="Primary")], family_list=[],
                      parent_family_list=[], note_list=[], tag_list=[])
        person["primary_name"]["first_name"] = first
        person["primary_name"]["surname_list"][0]["surname"] = surname
        d["people"].append(person)
        people[gid] = person
    for n, (gid, fa, mo, children, year) in enumerate(FAMILIES, 1):
        d["events"].append(_event(f"E04{n:02d}", "Marriage", year, n))
        family = json.loads(json.dumps(d["families"][0]))
        family.update(handle="h" + gid, gramps_id=gid, father_handle="h" + fa, mother_handle="h" + mo,
                      child_ref_list=[], event_ref_list=[_ref(f"hE04{n:02d}", role="Family")], tag_list=[])
        d["families"].append(family)
        people[fa]["family_list"].append(family["handle"])
        people[mo]["family_list"].append(family["handle"])
        kids[gid] = list(children)
    for family in d["families"]:
        for kid in kids.get(family["gramps_id"], []):
            family["child_ref_list"].append(_ref("h" + kid, frel="Birth", mrel="Birth"))
            people[kid]["parent_family_list"].append(family["handle"])
    classes = {"people": "Person", "families": "Family", "events": "Event", "places": "Place",
               "citations": "Citation", "sources": "Source", "media": "Media", "notes": "Note",
               "repositories": "Repository", "tags": "Tag"}
    return {"format": "gwt-apply-snapshot/1", "tree": "example-tree", "taken": "2026-01-01T00:00:00+00:00",
            "objects": {cls: d[t] for t, cls in classes.items()}}


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("tree.json.gz"))
    tree = build()
    raw = json.dumps(tree, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")
    with open(out, "wb") as fh, gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0) as gz:
        gz.write(raw)
    print(f"{out}: {len(tree['objects']['Person'])} people")
    return 0


if __name__ == "__main__":
    sys.exit(main())
