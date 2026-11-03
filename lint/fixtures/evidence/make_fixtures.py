#!/usr/bin/env python3
"""Write the evidence-rule fixtures: an invented tree, invented saved records and the lists the tests lint.

Every name, place, ark and date is made up; the arks follow FamilySearch's shape only.

    records/baptism-ana.json          a gwt-read envelope: a baptism, with the principal (Ana), her father,
                                      mother and godfather as personas, and the parish as the index typed it
    records/baptism-ana.candidates.json   the candidate row gwt-read writes beside it (gwt_read.record_candidates)
    records/baptism-pedro.json        a raw GEDCOM X baptism, in a chapel the index typed differently
    records/baptism-lucia.json        a raw GEDCOM X baptism whose two parents are both women
    records/baptism-pedro.candidates.json its candidate row
    records/fscache-copy/9f/<sha>.json/.body a gwt-fscache entry: a marriage, the bride not the principal, the
                                      Marriage fact on the Couple relationship as FamilySearch serves it
    records/search.candidates.json    gwt-read candidates: a father's persona, nothing else saved for it
    records/image-ana.txt             a saved transcription of an image ark (named on its first line)
    readings.tsv                      a signed reading of a second image

    python3 make_fixtures.py    # from lint/fixtures/evidence/; the output is the same on every run
"""
import json
import sys
from pathlib import Path

GX = "http://gedcomx.org/"
FS = "https://www.familysearch.org/ark:/61903/"
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "read"), str(HERE.parents[2] / "core")]
import gwt_read  # noqa: E402  the candidate rows exactly as gwt-read writes them


def persona(pid, ark, name, gender, facts=(), principal=False, role=""):
    p = {"id": pid, "principal": principal, "gender": {"type": GX + gender},
         "identifiers": {GX + "Persistent": [FS + "1:1:" + ark]},
         "names": [{"nameForms": [{"fullText": name}]}],
         "facts": [{"type": GX + t, "date": {"original": d}, "place": {"original": pl}} for t, d, pl in facts]}
    if role:
        p["display"] = {"name": name, "role": role}
    return p


def parent(a, b):
    return {"type": GX + "ParentChild", "person1": {"resource": "#" + a}, "person2": {"resource": "#" + b}}


ANA = {"persons": [
    persona("p1", "QZZ9-AAA", "Ana Zorvalo Quirell", "Female",
            [("Christening", "12 marzo 1851", "Villazul, Nortia"), ("Birth", "11 marzo 1851", "Villazul, Nortia")],
            True, "Principal"),
    persona("p2", "QZZ9-AAB", "Luis Zorvalo", "Male", role="Father"),
    persona("p3", "QZZ9-AAC", "Rosa Quirell", "Female", role="Mother"),
    persona("p4", "QZZ9-AAD", "Tomás Brin", "Male", role="Godparent")],
    "relationships": [parent("p2", "p1"), parent("p3", "p1")],
    "fields": [{"labelId": "EVENT_PLACE_ORIG", "text": "Parroquia de Santa Eulalia, Villazul"},
               {"labelId": "NOTES", "text": "bauticé solemnemente a una niña que nació el día de ayer, "
                                           "hija legítima de Luis y Rosa"}]}
PEDRO = {"persons": [
    persona("q1", "QZZ8-BBA", "Pedro Lurre", "Male", [("Christening", "1860", "Villazul")], True),
    persona("q2", "QZZ8-BBB", "Juan Morel", "Male"),
    persona("q3", "QZZ8-BBC", "Teresa Vidán", "Female")],
    "relationships": [parent("q2", "q1"), parent("q3", "q1")],
    "fields": [{"labelId": "EVENT_PLACE_ORIG", "text": "Santísimo Cristo del Valle, Villazul"},
               {"labelId": "NOTES", "text": "bauticé en la ermita del Santo Cristo del Valle por estar la "
                                           "parroquia en obras"}]}
# the index gives Lucía two mothers: one of them is likely her godmother
LUCIA = {"persons": [
    persona("s1", "QZZ5-EEA", "Lucía Arnau", "Female", [("Christening", "1870", "Villazul")], True),
    persona("s2", "QZZ5-EEB", "Clara Arnau", "Female"),
    persona("s3", "QZZ5-EEC", "Inés Toral", "Female")],
    "relationships": [parent("s2", "s1"), parent("s3", "s1")]}
MARRIAGE = {"persons": [
    persona("m1", "QZZ6-CCA", "Mario Lurre", "Male", [("Birth", "1860", "Villazul")], True),
    persona("m2", "QZZ6-CCB", "Elsa Quirell", "Female")],
    "relationships": [{"type": GX + "Couple", "person1": {"resource": "#m1"}, "person2": {"resource": "#m2"},
                       "facts": [{"type": GX + "Marriage", "date": {"original": "1885"},
                                  "place": {"original": "Villazul"}}]}]}
CANDIDATES = {"candidates": [
    {"source": "familysearch", "id": "QZZ7-DDA", "name": "Marcos Fenol", "role": "Father", "principal": False,
     "given": None, "surnames": None, "events": {}, "relatives": []}]}
IMAGE = ("ark:/61903/3:1:QZZ9-IMG1\nEn la villa de Villazul a doce de marzo de mil ochocientos cincuenta y uno,\n"
         "yo el cura propio bauticé a Ana.\n")
READINGS = "quote\timage sha\treader\nnota marginal de difícil lectura aquí\t3f9a0c1b2d4e\tA. Reader\n"


def place(gid, name, ptype, parent_gid=None):
    return {"handle": "h" + gid, "gramps_id": gid, "name": {"value": name}, "place_type": ptype,
            "placeref_list": [{"ref": "h" + parent_gid}] if parent_gid else []}


def tree():
    people = [{"handle": "h" + g, "gramps_id": g, "gender": s, "event_ref_list": [], "family_list": [],
               "parent_family_list": [], "person_ref_list": [], "tag_list": [], "note_list": [], "citation_list": [],
               "primary_name": {"first_name": f, "surname_list": [{"surname": n}]}}
              for g, f, n, s in (("I1", "Ana", "Zorvalo", 0), ("I2", "Luis", "Zorvalo", 1), ("I3", "Rosa", "Quirell", 0),
                                 ("I4", "Pedro", "Lurre", 1), ("I5", "Mario", "Lurre", 1), ("I6", "Elsa", "Quirell", 0),
                                 ("I7", "Tomás", "Brin", 1))]
    people[0]["event_ref_list"] = [{"ref": "hE1", "role": "Primary"}]
    families = [{"handle": "hF1", "gramps_id": "F1", "father_handle": "hI2", "mother_handle": "hI3",
                 "child_ref_list": [{"ref": "hI1", "frel": "Birth", "mrel": "Birth"}], "event_ref_list": [],
                 "tag_list": []},
                {"handle": "hF2", "gramps_id": "F2", "father_handle": "hI5", "mother_handle": "hI6",
                 "child_ref_list": [], "event_ref_list": [], "tag_list": []},
                # the godfather and the mother as a couple: linking Ana here makes her godfather her father
                {"handle": "hF3", "gramps_id": "F3", "father_handle": "hI7", "mother_handle": "hI3",
                 "child_ref_list": [], "event_ref_list": [], "tag_list": []}]
    people[0]["parent_family_list"] = ["hF1"]
    for i in (1, 2):
        people[i]["family_list"] = ["hF1"]
    for i in (4, 5):
        people[i]["family_list"] = ["hF2"]
    people[2]["family_list"] = ["hF1", "hF3"]
    people[6]["family_list"] = ["hF3"]
    events = [{"handle": "hE1", "gramps_id": "E1", "type": "Baptism", "place": "hP2", "citation_list": ["hC1"],
               "date": {"dateval": [12, 3, 1851, False], "text": ""}}]
    places = [place("P3", "Nortia", "Province"), place("P1", "Villazul", "Town", "P3"),
              place("P2", "Parroquia de Santa Eulalia", "Church", "P1"),
              place("P4", "Ermita del Santo Cristo del Valle", "Church", "P1"),
              place("P5", "Iglesia de Santa María", "Church", "P1")]
    return {"people": people, "families": families, "events": events, "places": places,
            "citations": [{"handle": "hC1", "gramps_id": "C1", "page": FS + "1:1:QZZ9-AAA", "source_handle": "hS1"},
                          {"handle": "hC2", "gramps_id": "C2", "page": FS + "1:1:QZZ9-AAB", "source_handle": "hS1"}],
            "sources": [{"handle": "hS1", "gramps_id": "S1", "title": "Invented parish register"}],
            "notes": [], "media": [], "repositories": [], "tags": []}


def cite(key, ark, target, tag=None):
    return {"op": "add_citation", "key": key, "ruling": "R-1", "source": "S1", "page": FS + ark, "attach": [target]}


def note(key, text, target):
    return {"op": "add_note", "key": key, "ruling": "R-1", "text": text, "attach": [target]}


def event(key, person, etype, place_):
    return {"op": "add_event", "key": key, "ruling": "R-1", "person": person, "type": etype, "place": place_}


LISTS = {
    "quote-clean": [
        cite("c-ana", "1:1:QZZ9-AAA", {"event": "E1"}),
        note("n-ana", "The priest wrote «bauticé solemnemente a una niña que nació el día de ayer».",
             {"citation": "key:c-ana"}),
        note("n-ocr", "The image reads «a doce de marzo de mil ochocientos … yo el cura propio» "
             f"{FS}3:1:QZZ9-IMG1", {"person": "I1"}),
        note("n-signed", f"The margin reads «nota marginal de difícil lectura aquí» {FS}3:1:QZZ9-IMG2",
             {"person": "I1"})],
    "quote-finding": [
        cite("c-ana", "1:1:QZZ9-AAA", {"event": "E1"}),
        note("n-accent", "The priest wrote «bautice solemnemente a una niña que nació el día de ayer».",
             {"citation": "key:c-ana"}),
        note("n-image", f"The margin reads «estas palabras no tienen lectura firmada» {FS}3:1:QZZ9-IMG2",
             {"person": "I1"}),
        note("n-invented", f"The record says «hija de padres muy conocidos en la villa» {FS}1:1:QZZ9-AAA",
             {"person": "I1"}),
        note("n-other-record", "The priest wrote «bauticé en la ermita del Santo Cristo del Valle».",
             {"citation": "key:c-ana"})],
    "principal-clean": [
        cite("c-ana", "1:1:QZZ9-AAA", {"event": "E1"}),
        cite("c-bride", "1:1:QZZ6-CCB", {"family": "F2"}),
        # a family the list adds takes the bride's persona like one already in the tree
        {"op": "add_family", "key": "f-couple", "ruling": "R-1", "father": "I5", "mother": "I6",
         "allow_new_couple": True, "new_couple_of": ["F2"], "new_couple_reason": f"their marriage, {FS}1:1:QZZ6-CCB"},
        cite("c-couple", "1:1:QZZ6-CCB", {"family": "key:f-couple"}),
        # replacing the father's ark with the principal's: the old value in `expect` is not cited
        {"op": "update_citation", "key": "u-fix", "ruling": "R-1", "citation": "C2", "expect": FS + "1:1:QZZ9-AAB",
         "set": {"page": FS + "1:1:QZZ9-AAA"}}],
    "principal-finding": [
        cite("c-father", "1:1:QZZ9-AAB", {"event": "E1"}),
        note("n-godfather", f"Ana's baptism, see {FS}1:1:QZZ9-AAD", {"person": "I1"}),
        event("e-death", "I1", "Death", "P1"),
        cite("c-death", "1:1:QZZ9-AAA", {"event": "key:e-death"}),
        cite("c-cand", "1:1:QZZ7-DDA", {"person": "I2"}),
        # C2's page in the snapshot is the father's persona; hanging it on the baptism cites it
        {"op": "attach_existing", "key": "a-father", "ruling": "R-1", "object": {"citation": "C2"},
         "attach": [{"event": "E1"}]}],
    "role-clean": [
        note("n-roles", f"padre: Luis Zorvalo, madre: Rosa Quirell, padrino Tomás Brin {FS}1:1:QZZ9-AAD",
             {"person": "I1"}),
        note("n-padre", f"padre: Luis Zorvalo {FS}1:1:QZZ9-AAB", {"person": "I2"}),
        note("n-far", f"Luis Zorvalo is named in {FS}1:1:QZZ9-AAA as well; a godparent is listed further down the "
             "same entry", {"person": "I2"}),
        note("n-namesake", f"padre: Luis Zorvalo, Brin's neighbour, {FS}1:1:QZZ9-AAA", {"person": "I2"}),
        {"op": "link_child", "key": "l-father", "ruling": "R-1", "family": "F1", "child": "I1", "probable": True,
         "probable_reason": f"the baptism names both parents: {FS}1:1:QZZ9-AAA"}],
    "role-finding": [
        note("n-father", f"Tomás Brin, father of the child ({FS}1:1:QZZ9-AAD)", {"person": "I1"}),
        note("n-padre-godfather", f"padre: Tomás Brin, see {FS}1:1:QZZ9-AAA", {"person": "I1"}),
        {"op": "link_child", "key": "l-godfather", "ruling": "R-1", "family": "F3", "child": "I1", "probable": True,
         "probable_reason": f"the baptism names the parents: {FS}1:1:QZZ9-AAA"},
        cite("c-lucia", "1:1:QZZ5-EEA", {"person": "I4"})],
    "placement-clean": [
        event("e-bap-ana", "I1", "Baptism", "P2"),
        cite("c-bap-ana", "1:1:QZZ9-AAA", {"event": "key:e-bap-ana"}),
        event("e-birth-ana", "I1", "Birth", "P1"),
        event("e-bap-pedro", "I4", "Baptism", "P4"),
        cite("c-bap-pedro", "1:1:QZZ8-BBA", {"event": "key:e-bap-pedro"})],
    "placement-finding": [
        event("e-bap-town", "I1", "Baptism", "P1"),
        event("e-birth-church", "I1", "Birth", "P2"),
        event("e-bap-cristo", "I4", "Baptism", "P4"),
        cite("c-bap-cristo", "1:1:QZZ8-BBA", {"event": "key:e-bap-cristo"}),
        event("e-bap-maria", "I1", "Baptism", "P5"),
        cite("c-bap-maria", "1:1:QZZ9-AAA", {"event": "key:e-bap-maria"}),
        {"op": "update_event", "key": "u-bap-town", "ruling": "R-1", "event": "E1", "set": {"place": "P1"}}],
}


def main():
    rec = HERE / "records"
    (rec / "fscache-copy" / "9f").mkdir(parents=True, exist_ok=True)

    def dump(path, obj):
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    dump(rec / "baptism-ana.json", {"url": FS + "1:1:QZZ9-AAA", "final_url": FS + "1:1:QZZ9-AAA",
                                    "captured_at": "2026-01-02T03:04:05Z", "json": ANA})
    dump(rec / "baptism-ana.candidates.json", {"candidates": gwt_read.record_candidates(ANA, "1:1:QZZ9-AAA")})
    dump(rec / "baptism-pedro.json", PEDRO)
    dump(rec / "baptism-lucia.json", LUCIA)
    dump(rec / "baptism-pedro.candidates.json", {"candidates": gwt_read.record_candidates(PEDRO, "1:1:QZZ8-BBA")})
    sha = "9f" + "0" * 62
    dump(rec / "fscache-copy" / "9f" / f"{sha}.json", {"method": "GET", "url": FS + "1:1:QZZ6-CCA", "status": 200,
                                                "headers": {}, "fetched": 1767323045, "date": "2026-01-02T03:04:05Z"})
    (rec / "fscache-copy" / "9f" / f"{sha}.body").write_text(json.dumps(MARRIAGE, ensure_ascii=False), encoding="utf-8")
    dump(rec / "search.candidates.json", CANDIDATES)
    (rec / "image-ana.txt").write_text(IMAGE, encoding="utf-8")
    (HERE / "readings.tsv").write_text(READINGS, encoding="utf-8")
    dump(HERE / "tree.json", tree())
    for name, ops in LISTS.items():
        (HERE / f"{name}.jsonl").write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in ops),
                                            encoding="utf-8")


if __name__ == "__main__":
    main()
