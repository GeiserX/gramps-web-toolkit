#!/usr/bin/env python3
"""Write tree.json: an invented 14-person tree, in the `gwt-check dump` shape, for the gwt-lint tests.

Every name, place and date is made up. The root is Ana Prieto Gil (I1):

    Pedro Prieto (I2) x Marta Gil (I3)            Ramón Vega (I13)
        Ana (I1, root), Luis (I4)                     Carlos (I5), Hugo (I7) x Rosa Luna (I14, F5)
    Carlos Vega (I5) x Ana (I1), F2, married 1924
        Elena (I6, birth), Sara (I8, Stepchild), Iker (I9, Adopted, a [probable] note on the link)
    Mario Ruiz (I11) x Elena (I6), F3, married 1955
        Nora (I10, born 1990, no death: possibly living)
    Kit Match (I12): no dates, a DNA association from Nora

    python3 make_tree.py    # from lint/fixtures/tree/; the output is the same on every run
"""
import json
from pathlib import Path

PEOPLE = [  # gramps id, first name, surname, gender, birth, death
    ("I1", "Ana", "Prieto Gil", 0, 1900, 1980), ("I2", "Pedro", "Prieto", 1, 1870, 1940),
    ("I3", "Marta", "Gil", 0, 1872, 1950), ("I4", "Luis", "Prieto Gil", 1, 1902, 1970),
    ("I5", "Carlos", "Vega", 1, 1898, 1975), ("I6", "Elena", "Vega Prieto", 0, 1930, 2000),
    ("I7", "Hugo", "Vega", 1, 1895, 1960), ("I8", "Sara", "Vega", 0, 1925, 1990),
    ("I9", "Iker", "Vega", 1, 1932, 2010), ("I10", "Nora", "Ruiz Vega", 0, 1990, None),
    ("I11", "Mario", "Ruiz", 1, 1928, 1999), ("I12", "Kit", "Match", 2, None, None),
    ("I13", "Ramón", "Vega", 1, 1860, 1930), ("I14", "Rosa", "Luna", 0, 1897, 1970),
]
FAMILIES = [  # gramps id, father, mother, [(child, frel/mrel)], marriage year
    ("F1", "I2", "I3", [("I1", "Birth"), ("I4", "Birth")], 1899),
    ("F2", "I5", "I1", [("I6", "Birth"), ("I8", "Stepchild"), ("I9", "Adopted")], 1924),
    ("F3", "I11", "I6", [("I10", "Birth")], 1955),
    ("F4", "I13", None, [("I5", "Birth"), ("I7", "Birth")], None),
    ("F5", "I7", "I14", [], 1920),
]


def date(year, month=5, day=1):
    return {"_class": "Date", "calendar": 0, "modifier": 0, "quality": 0, "dateval": [day, month, year, False],
            "text": "", "sortval": 0, "newyear": 0}


def ref(h, **kw):
    return {"ref": h, "note_list": [], "citation_list": [], "attribute_list": [], "private": False, **kw}


def build():
    people, families, events = {}, {}, []

    def event(gid, type_, year):
        events.append({"handle": "h" + gid, "gramps_id": gid, "type": type_, "date": date(year), "place": "hP1",
                       "description": "", "note_list": [], "citation_list": [], "media_list": [], "tag_list": [],
                       "attribute_list": []})
        return "h" + gid

    for gid, first, sur, gender, born, died in PEOPLE:
        evs = []
        if born:
            evs.append(ref(event("E" + gid[1:] + "b", "Birth", born), role="Primary"))
        if died:
            evs.append(ref(event("E" + gid[1:] + "d", "Death", died), role="Primary"))
        people[gid] = {"handle": "h" + gid, "gramps_id": gid, "gender": gender, "event_ref_list": evs,
                       "birth_ref_index": 0 if born else -1, "death_ref_index": 1 if died else -1,
                       "family_list": [], "parent_family_list": [], "person_ref_list": [], "note_list": [],
                       "citation_list": [], "media_list": [], "tag_list": [], "private": False,
                       "primary_name": {"first_name": first, "surname_list": [{"surname": sur, "primary": True}]}}
    people["I10"]["person_ref_list"].append(ref("hI12", rel="DNA"))
    people["I1"]["note_list"].append("hN2")
    for gid, fa, mo, kids, married in FAMILIES:
        fam = {"handle": "h" + gid, "gramps_id": gid, "father_handle": fa and "h" + fa,
               "mother_handle": mo and "h" + mo, "child_ref_list": [], "event_ref_list": [], "note_list": [],
               "citation_list": [], "media_list": [], "tag_list": [], "type": "Married", "private": False}
        if married:
            fam["event_ref_list"].append(ref(event("EM" + gid[1:], "Marriage", married), role="Family"))
        for p in (fa, mo):
            if p:
                people[p]["family_list"].append(fam["handle"])
        for kid, rel in kids:
            fam["child_ref_list"].append(ref("h" + kid, frel=rel, mrel=rel))
            people[kid]["parent_family_list"].append(fam["handle"])
        families[gid] = fam
    families["F2"]["child_ref_list"][2]["note_list"].append("hN1")
    notes = [{"handle": "hN1", "gramps_id": "N1", "type": "Research", "tag_list": [], "private": False,
              "text": {"_class": "StyledText", "string": "[probable] Adoption named only in a later census.",
                       "tags": []}},
             {"handle": "hN2", "gramps_id": "N2", "type": "General", "tag_list": [], "private": False,
              "text": {"_class": "StyledText", "string": "Ana kept the family bible.", "tags": []}}]
    sources = [{"handle": "hS1", "gramps_id": "S1", "title": "Parish book of Villaejemplo", "tag_list": []},
               {"handle": "hS2", "gramps_id": "S2", "title": "Machine index of baptisms", "tag_list": []}]
    citations = [{"handle": "hC1", "gramps_id": "C1", "source_handle": "hS1", "page": "folio 3", "confidence": 3,
                  "tag_list": [], "note_list": []},
                 {"handle": "hC2", "gramps_id": "C2", "source_handle": "hS2", "page": "row 7", "confidence": 1,
                  "tag_list": ["htagweak"], "note_list": []}]
    places = [{"handle": "hP1", "gramps_id": "P1", "name": {"value": "Villaejemplo"}, "tag_list": []}]
    tags = [{"handle": "htagweak", "name": "weak"}]
    return {"objects": {"people": list(people.values()), "families": list(families.values()), "events": events,
                        "notes": notes, "sources": sources, "citations": citations, "places": places,
                        "tags": tags, "media": [], "repositories": []}}


if __name__ == "__main__":
    out = Path(__file__).with_name("tree.json")
    out.write_text(json.dumps(build(), ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(out)
