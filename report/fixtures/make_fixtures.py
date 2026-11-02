#!/usr/bin/env python3
"""Write the invented trees the gwt-report tests read. Every name is made up.

    python3 make_fixtures.py   # from report/fixtures/

kin-tree.json: Adam and Eve (F1) have A1, B1 and C1. Line A goes A1 > A2 > ... > A6 (sons), line B goes
B1 > B2 > ... > B6 (daughters), so A_i and B_j are cousins of every degree. Adam also has H1 with Lilith (F2), a half
sister of A1. C1's son C2 marries B1's daughter E2: their child X has Adam and Eve twice in the pedigree. K is
adopted into A1's family, S1 is a stepchild there. B2's husband Sam is an in-law, never blood.
Marks on links: A2's link to A1 carries a [probable] note, A3 is tagged "hipótesis", B2's link to B1 carries a
citation of confidence 3.

digest-old.json / digest-new.json: the same small tree before and after a research round (new people, an event,
a citation with a web address, a media file, one item tagged weak).
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def person(gid, first, surname, gender, **kw):
    p = {"handle": "h" + gid, "gramps_id": gid, "gender": gender, "private": False,
         "primary_name": {"first_name": first, "surname_list": [{"surname": surname, "primary": True}]},
         "event_ref_list": [], "birth_ref_index": -1, "death_ref_index": -1, "family_list": [],
         "parent_family_list": [], "citation_list": [], "media_list": [], "note_list": [], "tag_list": []}
    p.update(kw)
    return p


class Tree:
    def __init__(self):
        self.o = {k: [] for k in ("Person", "Family", "Event", "Citation", "Media", "Note", "Tag", "Place", "Source",
                                  "Repository")}
        self.people = {}

    def add(self, gid, first, surname, gender, **kw):
        p = person(gid, first, surname, gender, **kw)
        self.o["Person"].append(p)
        self.people[gid] = p
        return p

    def family(self, gid, father, mother, children, rel="Birth"):
        refs = []
        for c in children:
            cid, frel, mrel = (c, rel, rel) if isinstance(c, str) else c
            refs.append({"ref": "h" + cid, "frel": frel, "mrel": mrel, "citation_list": [], "note_list": [],
                         "private": False})
            self.people[cid]["parent_family_list"].append("h" + gid)
        for s in (father, mother):
            if s:
                self.people[s]["family_list"].append("h" + gid)
        f = {"handle": "h" + gid, "gramps_id": gid, "father_handle": "h" + father if father else None,
             "mother_handle": "h" + mother if mother else None, "child_ref_list": refs, "event_ref_list": [],
             "citation_list": [], "media_list": [], "note_list": [], "tag_list": [], "private": False}
        self.o["Family"].append(f)
        return f

    def dump(self, name, taken):
        data = {"format": "gwt-apply-snapshot/1", "tree": "example", "taken": taken, "objects": self.o}
        (HERE / name).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def kin_tree():
    t = Tree()
    t.add("I1", "Adam", "Root", 1)
    t.add("I2", "Eve", "Stone", 0)
    t.add("I3", "Lilith", "Moss", 0)
    for i in range(1, 7):
        t.add("A%d" % i, "Arno%d" % i, "Field", 1)
        t.add("B%d" % i, "Bea%d" % i, "Hill", 0)
    for i in range(2, 7):
        t.add("WA%d" % i, "Wife%d" % i, "Lake", 0)
        t.add("HB%d" % i, "Husband%d" % i, "Brook", 1)
    for gid, first, sn, g in (("H1", "Hanna", "Root", 0), ("H2", "Hugo", "Vale", 1), ("HV", "Victor", "Vale", 1),
                              ("C1", "Carl", "Root", 1), ("C2", "Cid", "Root", 1), ("E2", "Elsa", "Brook", 0),
                              ("X1", "Xena", "Root", 0), ("K1", "Kai", "Field", 1), ("S1", "Saul", "Ford", 1),
                              ("SF", "Sara", "Ford", 0)):
        t.add(gid, first, sn, g)
    t.family("F1", "I1", "I2", ["A1", "B1", "C1"])
    t.family("F2", "I1", "I3", ["H1"])
    t.family("F3", "HV", "H1", ["H2"])
    # line A: A_i with wife WA_{i+1}... sons; line B: HB_{i+1} with B_i... daughters
    for i in range(1, 6):
        t.family("FA%d" % i, "A%d" % i, "WA%d" % (i + 1), ["A%d" % (i + 1)] + ([("K1", "Adopted", "Adopted"),
                 ("S1", "Stepchild", "Stepchild")] if i == 1 else []))
        t.family("FB%d" % i, "HB%d" % (i + 1), "B%d" % i, ["B%d" % (i + 1)] + (["E2"] if i == 1 else []))
    t.family("FS", None, "SF", ["S1"])
    t.family("FC", "C1", None, ["C2"])
    t.family("FX", "C2", "E2", ["X1"])
    # marks on links
    t.o["Note"].append({"handle": "hN1", "gramps_id": "N1", "text": {"string": "[probable] index line only"},
                        "type": "Research"})
    fa1 = next(f for f in t.o["Family"] if f["gramps_id"] == "FA1")
    next(c for c in fa1["child_ref_list"] if c["ref"] == "hA2")["note_list"].append("hN1")
    t.o["Tag"].append({"handle": "htag1", "name": "Hipótesis", "gramps_id": ""})
    t.people["A3"]["tag_list"].append("htag1")
    t.o["Source"].append({"handle": "hS1", "gramps_id": "S1", "title": "Parish book"})
    t.o["Citation"].append({"handle": "hC1", "gramps_id": "C1", "source_handle": "hS1", "page": "folio 3",
                            "confidence": 3, "note_list": [], "attribute_list": [], "media_list": [], "tag_list": []})
    fb1 = next(f for f in t.o["Family"] if f["gramps_id"] == "FB1")
    next(c for c in fb1["child_ref_list"] if c["ref"] == "hB2")["citation_list"].append("hC1")
    t.dump("kin-tree.json", "2026-01-01T00:00:00+00:00")


def digest_trees():
    def base():
        t = Tree()
        t.add("I1", "Nora", "Gale", 0)          # the root
        t.add("I2", "Otto", "Gale", 1)           # her father
        t.add("I3", "Rita", "Pine", 0)           # her mother
        t.add("I4", "Ugo", "Gale", 1)            # father's brother
        t.add("I5", "Paul", "Gale", 1)           # grandfather (father's side)
        t.add("I6", "Mia", "Reed", 0)            # Ugo's wife, an in-law
        t.add("I10", "Gus", "Pine", 1)           # grandfather (mother's side)
        t.family("F1", "I2", "I3", ["I1"])
        t.family("F2", "I5", None, ["I2", "I4"])
        t.family("F3", "I4", "I6", [])
        t.family("F4", "I10", None, ["I3"])
        t.o["Place"].append({"handle": "hP1", "gramps_id": "P1", "name": {"value": "Greenford"}})
        t.o["Source"].append({"handle": "hS1", "gramps_id": "S1", "title": "Greenford parish register"})
        return t
    old = base()
    old.dump("digest-old.json", "2026-01-01T00:00:00+00:00")
    new = base()
    new.add("I12", "Lena", "Gale", 0)              # a new cousin: Ugo and Mia's daughter
    next(f for f in new.o["Family"] if f["gramps_id"] == "F3")["child_ref_list"].append(
        {"ref": "hI12", "frel": "Birth", "mrel": "Birth", "citation_list": [], "note_list": []})
    new.people["I12"]["parent_family_list"].append("hF3")
    new.add("I8", "Rosa", "Pine", 0, tag_list=["htag1"])   # mother's sister, tagged weak
    next(f for f in new.o["Family"] if f["gramps_id"] == "F4")["child_ref_list"].append(
        {"ref": "hI8", "frel": "Birth", "mrel": "Birth", "citation_list": [], "note_list": []})
    new.people["I8"]["parent_family_list"].append("hF4")
    new.add("I11", "Tim", "Reed", 1)              # Mia's brother: an in-law's kin, no blood path
    new.o["Tag"].append({"handle": "htag1", "name": "hypothesis", "gramps_id": ""})
    new.o["Event"].append({"handle": "hE1", "gramps_id": "E1", "type": {"string": "Baptism"},
                           "date": {"dateval": [3, 5, 1890, False], "year": 1890}, "place": "hP1",
                           "description": "", "citation_list": ["hC1"], "media_list": [], "tag_list": []})
    new.people["I5"]["event_ref_list"].append({"ref": "hE1", "role": "Primary"})
    new.o["Citation"].append({"handle": "hC1", "gramps_id": "C1", "source_handle": "hS1", "page": "folio 12",
                              "confidence": 3, "note_list": ["hN1"], "attribute_list": [],
                              "media_list": [{"ref": "hO1"}], "tag_list": []})
    new.o["Note"].append({"handle": "hN1", "gramps_id": "N1",
                          "text": {"string": "Image: https://archive.example.org/register/12.jpg"}})
    new.o["Media"].append({"handle": "hO1", "gramps_id": "O1", "desc": "Register page 12", "path": "reg12.jpg",
                           "tag_list": []})
    new.add("I9", "Ivo", "Reed", 1, private=True)            # private: never in a digest
    new.dump("digest-new.json", "2026-02-01T00:00:00+00:00")


if __name__ == "__main__":
    kin_tree()
    digest_trees()
