"""gwt-import-gedcom: reading, dates, the change list, the duplicate hold, audit and split. Every name is invented."""

import codecs
import gzip
import json
import os
from pathlib import Path

import pytest

from gwt_apply.dates import gregorian_sdn
from gwt_apply.dupes import preflight
from gwt_apply.ops import validate
from gwt_gedcom import audit as audit_mod
from gwt_gedcom import split as split_mod
from gwt_gedcom.cli import main
from gwt_gedcom.mapping import Snapshot, build
from gwt_gedcom.parse import GedcomError, ansel_decode, ged_date, read

FIX = Path(__file__).resolve().parent.parent / "fixtures"
CLASSES = ["Person", "Family", "Event", "Place", "Citation", "Source", "Repository", "Media", "Note", "Tag"]


def snap(**objects):
    return {"format": "gwt-apply-snapshot/1", "tree": "t", "objects": {c: list(objects.get(c, [])) for c in CLASSES}}


def tree_person(handle, gid, first, surname, birth=None):
    p = {"handle": handle, "gramps_id": gid, "primary_name": {"first_name": first, "surname_list": [{"surname": surname}]},
         "event_ref_list": [], "parent_family_list": [], "family_list": []}
    ev = []
    if birth:
        ev = [{"handle": handle + "-b", "gramps_id": "E" + gid[1:], "type": "Birth",
               "date": {"dateval": [0, 0, birth, False]}}]
        p["event_ref_list"] = [{"ref": handle + "-b", "role": "Primary"}]
    return p, ev


def ged(text: str) -> bytes:
    return text.strip().encode("utf-8") + b"\n"


def run(source, tree=None, **kw):
    g = read(source if isinstance(source, bytes) else str(source))
    kw.setdefault("batch", "t")
    kw.setdefault("ruling", "R")
    kw.setdefault("this_year", 2026)
    return build(g, tree or snap(), **kw)


def by_op(ops, name):
    return [o for o in ops if o["op"] == name]


# --- reading -------------------------------------------------------------------------------------------------

def test_latin1_file_with_a_utf8_line_is_read_and_the_line_reported():
    g = read(str(FIX / "latin1-551.ged"))
    assert (g.version, g.declared, g.encoding) == ("5.5.1", "ANSI", "cp1252")
    names = [r.val("NAME") for r in g.individuals]
    assert "María /López/" in names
    places = {c.first("PLAC").value for r in g.individuals for c in r.children if c.first("PLAC")}
    assert "San Pedro, Villanueva, Tierra Alta, España" in places
    assert "Villanueva, Tierra Alta, EspaÃ±a" not in places  # the UTF-8 line is not read as mojibake
    assert [(r["line"], r["read_as"]) for r in g.repairs] == [(39, "utf-8")]


def test_conc_and_cont_join_the_value_they_continue():
    note = read(str(FIX / "latin1-551.ged")).by_xref["@I1@"].first("NOTE").value
    assert note == ("Juan worked the mill for forty years and his son took it over after him.\n"
                    "A second line of the note.")


def test_ansel_combining_marks_go_after_their_letter():
    assert ansel_decode(b"Jos\xe2e Mu\xe4noz \xa5gidius")[0] == "José Muñoz Ægidius"
    raw = b"0 HEAD\n1 CHAR ANSEL\n1 GEDC\n2 VERS 5.5.1\n0 @I1@ INDI\n1 NAME Jos\xe2e /Mu\xe4noz/\n0 TRLR\n"
    g = read(raw)
    assert g.individuals[0].val("NAME") == "José /Muñoz/"
    assert g.repairs == []


def test_a_utf8_line_in_an_ansel_file_is_read_as_utf8_and_reported():
    raw = b"0 HEAD\n1 CHAR ANSEL\n0 @I1@ INDI\n1 NAME Jos\xc3\xa9 /Ruiz/\n0 TRLR\n"
    g = read(raw)
    assert g.individuals[0].val("NAME") == "José /Ruiz/"
    assert [r["read_as"] for r in g.repairs] == ["utf-8"]


def test_a_byte_order_mark_wins_and_utf16_files_read():
    body = "0 HEAD\n1 CHAR UNICODE\n0 @I1@ INDI\n1 NAME Zoë /Smith/\n0 TRLR\n"
    for raw in (codecs.BOM_UTF8 + body.encode("utf-8"), codecs.BOM_UTF16_LE + body.encode("utf-16-le")):
        assert read(raw).individuals[0].val("NAME") == "Zoë /Smith/"


def test_gedcom7_escapes_and_pointers():
    g = read(str(FIX / "seven.ged"))
    assert g.major == 7
    assert g.by_xref["@N1@"].value == "@home: the family lived above the forge."
    assert g.by_xref["@I1@"].first("SNOTE").pointer == "@N1@"
    assert read(ged("0 HEAD\n0 @I1@ INDI\n1 FAMC @VOID@\n0 TRLR")).individuals[0].first("FAMC").pointer is None


def test_lines_the_reader_cannot_place_are_reported_with_their_numbers():
    g = read(ged("0 HEAD\n0 @I1@ INDI\n3 NAME Jump /Level/\nnot a line\n0 @I1@ INDI\n0 TRLR"))
    assert any("line 3" in p and "no parent" in p for p in g.problems)
    assert any("line 4" in p for p in g.problems)
    assert any("two records" in p for p in g.problems)
    with pytest.raises(GedcomError):
        read(b"\n\n")


# --- dates ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, date, problem", [
    ("12 JAN 1850", "1850-01-12", None),
    ("JAN 1850", "1850-01", None),
    ("ABT 1850", "abt 1850", None),
    ("EST 1800", "est 1800", None),
    ("CAL 1801", "calc 1801", None),
    ("BEF 3 MAR 1850", "bef 1850-03-03", None),
    ("BET 1850 AND 1860", "bet 1850 and 1860", None),
    ("FROM 3 MAR 1850 TO 1851", "from 1850-03-03 to 1851", None),
    ("@#DJULIAN@ 11 FEB 1700", "julian 1700-02-11", None),
    ("JULIAN 11 FEB 1700", "julian 1700-02-11", None),
    ("@#DJULIAN@ BET 1700 AND 1701", "julian bet 1700 and 1701", None),
    ("@#DFRENCH R@ 1 VEND 12", "french 12-01-01", None),
    ("FRENCH_R 18 BRUM 8", "french 8-02-18", None),
    ("1 JAN 1700/01", "1701-01-01", "dual year 1700/01 read as 1701"),
    ("FROM 1850", "aft 1850", "an open span FROM date read as after that date"),
    ("(during the war)", "text: during the war", None),
    ("31 FEB 1850", "text: 31 FEB 1850", "impossible date '1850-02-31': that month has no day 31"),
    ("Easter 1850", "text: Easter 1850", "unknown month 'EASTER' for the gregorian calendar"),
    ("@#DHEBREW@ 1 TSH 5600", "text: @#DHEBREW@ 1 TSH 5600", "the hebrew calendar is not one the tool converts"),
    ("", None, None),
    ("1 JAN 1850/49", "text: 1 JAN 1850/49", "dual year 1850/49: the second year is not the one after the first"),
    ("1 JAN 1699/00", "1700-01-01", "dual year 1699/00 read as 1700"),
    ("BET 1900 AND 1800", "text: BET 1900 AND 1800", "the range ends before it starts"),
    ("FROM 1850 TO 1840", "text: FROM 1850 TO 1840", "the range ends before it starts"),
])
def test_gedcom_dates_become_gwt_apply_dates(text, date, problem):
    assert ged_date(text) == (date, problem)


# --- the change list -----------------------------------------------------------------------------------------

def test_latin1_file_becomes_a_valid_list_with_calendars_places_sources_and_ids():
    ops, rep = run(FIX / "latin1-551.ged")
    assert validate(ops) == []
    assert preflight(ops, Snapshot(snap())) == []
    people = {o["key"].rsplit("-", 1)[1]: o for o in by_op(ops, "add_person")}
    assert set(people) == {"i1", "i2", "i3", "i4", "i6"}  # i5 was born in 1990: held as maybe living
    assert rep["living_held"] == [{"xref": "@I5@", "name": "Luis Martín", "why": "born-1990"}]
    births = {o["person"].rsplit("-", 1)[1]: o.get("date") for o in by_op(ops, "add_event") if o["type"] == "Birth"}
    assert births["i1"] == "julian 1700-02-11"
    assert births["i3"] == "text: 31 FEB 1730"  # kept, flagged, never guessed
    assert rep["date_problems"][0]["text"] == "31 FEB 1730"
    places = by_op(ops, "add_place")
    assert [(p["name"], p["type"]) for p in places[:3]] == [("España", "Country"), ("Tierra Alta", "County"),
                                                            ("Villanueva", "City")]
    assert len([p for p in places if p["name"] == "Villanueva"]) == 1  # one place, used by every event there
    fam = by_op(ops, "add_family")[0]
    assert fam["children"] == ["key:" + people["i3"]["key"]]
    link = by_op(ops, "link_child")[0]
    assert (link["child"], link["frel"]) == ("key:" + people["i4"]["key"], "Adopted")
    assert rep["families_skipped"][0]["xref"] == "@F2@"
    assert by_op(ops, "set_external_id")[0]["id"] == "ABCD-123"
    assert by_op(ops, "link_repository")[0]["call_number"] == "12/3"
    assert {c["page"]: c["confidence"] for c in by_op(ops, "add_citation")} == {"folio 12": 4, "folio 40": 2,
                                                                               "folio 3": 2}
    assert all(o["key"].startswith("ged-t-" + read(str(FIX / "latin1-551.ged")).sha256[:8]) for o in ops)


def test_namesakes_inside_the_file_are_kept_apart_with_the_reason_said():
    ops, rep = run(FIX / "latin1-551.ged")
    twin = next(o for o in by_op(ops, "add_person") if o["key"].endswith("-i6"))
    assert twin["allow_namesake"] is True and twin["namesake_of"] == ["key:" + twin["key"][:-1] + "3"]
    assert rep["file_namesakes"][0]["xref"] == "@I6@"


def test_a_person_already_in_the_tree_is_held_not_added():
    p, ev = tree_person("h1", "I0007", "Pedro", "García López", 1730)
    tree = snap(Person=[p], Event=ev)
    ops, rep = run(FIX / "latin1-551.ged", tree)
    assert not [o for o in by_op(ops, "add_person") if o["key"].endswith(("-i3", "-i6"))]
    assert {r["xref"]: r["namesake_of"] for r in rep["namesakes"]} == {"@I3@": ["I0007"], "@I6@": ["I0007"]}
    # nothing points at a held person: their events, citations and family links wait with them
    assert not [o for o in ops if "i3" in json.dumps(o) or "-i6" in json.dumps(o)]
    assert validate(ops) == []
    assert preflight(ops, Snapshot(tree)) == []


def test_positive_control_without_the_tree_check_the_namesake_is_added():
    p, ev = tree_person("h1", "I0007", "Pedro", "García López", 1730)
    tree = snap(Person=[p], Event=ev)
    ops, _ = run(FIX / "latin1-551.ged", tree, check_tree=False)
    assert [o for o in by_op(ops, "add_person") if o["key"].endswith("-i3")]
    assert preflight(ops, Snapshot(tree)), "gwt-apply would refuse this list: the hold is what keeps it out"


def test_same_links_the_tree_person_and_adds_nothing_about_them():
    p, ev = tree_person("h1", "I0007", "Juan", "García", 1700)
    tree = snap(Person=[p], Event=ev)
    ops, rep = run(FIX / "latin1-551.ged", tree, same={"@I1@": "I0007"})
    assert not [o for o in by_op(ops, "add_person") if o["key"].endswith("-i1")]
    assert by_op(ops, "add_family")[0]["father"] == "I0007"
    assert not [o for o in ops if o.get("person") == "I0007"]  # no second birth, death or note on him
    assert rep["namesakes"] == []
    assert validate(ops) == []


def test_different_adds_the_person_with_the_reason_and_the_ids_compared():
    p, ev = tree_person("h1", "I0007", "Juan", "García", 1701)
    tree = snap(Person=[p], Event=ev)
    ops, _ = run(FIX / "latin1-551.ged", tree, different={"@I1@": "born in another parish"})
    juan = next(o for o in by_op(ops, "add_person") if o["key"].endswith("-i1"))
    assert (juan["allow_namesake"], juan["namesake_reason"], juan["namesake_of"]) == (
        True, "born in another parish", ["I0007"])
    assert preflight(ops, Snapshot(tree)) == []


def test_a_couple_already_in_the_tree_is_left_for_preflight_to_refuse():
    j, ej = tree_person("h1", "I0001", "Juan", "García", 1650)
    m, em = tree_person("h2", "I0002", "María", "López", 1655)
    j["family_list"] = m["family_list"] = ["fam"]
    fam = {"handle": "fam", "gramps_id": "F0001", "father_handle": "h1", "mother_handle": "h2"}
    tree = snap(Person=[j, m], Event=ej + em, Family=[fam])
    _, rep = run(FIX / "latin1-551.ged", tree, different={"@I1@": "another Juan", "@I2@": "another María"})
    assert len(rep["preflight"]) == 1 and "F0001" in rep["preflight"][0]


def test_places_are_matched_top_down_against_the_tree():
    country = {"handle": "pc", "gramps_id": "P0001", "name": {"value": "Espana"}, "placeref_list": []}
    county = {"handle": "pk", "gramps_id": "P0002", "name": {"value": "TIERRA ALTA"}, "placeref_list": [{"ref": "pc"}]}
    other = {"handle": "px", "gramps_id": "P0003", "name": {"value": "Villanueva"}, "placeref_list": [{"ref": "zz"}]}
    ops, rep = run(FIX / "latin1-551.ged", snap(Place=[country, county, other]))
    village = next(o for o in by_op(ops, "add_place") if o["name"] == "Villanueva")
    assert village["enclosed_by"] == "P0002"  # the Villanueva under another parent is not this one
    assert not [o for o in by_op(ops, "add_place") if o["name"] in ("España", "Tierra Alta")]
    assert rep["places_matched"] == 2


def test_an_ambiguous_place_is_kept_as_text_not_guessed():
    twins = [{"handle": h, "gramps_id": g, "name": {"value": "Dorpstad"}, "placeref_list": []}
             for h, g in (("a", "P0001"), ("b", "P0002"))]
    ops, rep = run(FIX / "record-oa.ged", snap(Place=twins))
    bap = by_op(ops, "add_event")[0]
    assert "place" not in bap and bap["description"] == "Place as written: Dorpstad"
    assert rep["places_ambiguous"][0]["tree_ids"] == ["P0001", "P0002"]


def test_a_second_birth_becomes_a_note_and_a_repeated_event_is_one_event():
    text = """0 HEAD
1 GEDC
2 VERS 5.5.1
0 @I1@ INDI
1 NAME Ana /Ruiz/
1 BIRT
2 DATE 1801
1 BIRT
2 DATE 1802
1 RESI
2 PLAC Dorpstad
2 SOUR @S1@
3 PAGE p 1
1 RESI
2 PLAC Dorpstad
2 SOUR @S1@
3 PAGE p 2
1 DEAT
2 DATE 1870
0 @S1@ SOUR
1 TITL Census
0 TRLR"""
    ops, rep = run(ged(text))
    assert [o.get("date") for o in by_op(ops, "add_event") if o["type"] == "Birth"] == ["1801"]
    assert "1802" in by_op(ops, "add_note")[0]["text"]
    resi = [o for o in by_op(ops, "add_event") if o["type"] == "Residence"]
    assert len(resi) == 1 and rep["duplicate_events"] == 1
    assert sorted(c["page"] for c in by_op(ops, "add_citation")) == ["p 1", "p 2"]
    assert all(c["attach"] == [{"event": "key:" + resi[0]["key"]}] for c in by_op(ops, "add_citation"))
    assert validate(ops) == []


def test_one_page_cited_with_two_qualities_gets_two_pages():
    text = """0 HEAD
0 @I1@ INDI
1 NAME Ana /Ruiz/
1 BIRT
2 DATE 1801
2 SOUR @S1@
3 PAGE p 1
3 QUAY 3
1 DEAT
2 DATE 1870
2 SOUR @S1@
3 PAGE p 1
3 QUAY 1
0 @S1@ SOUR
1 TITL Register
0 TRLR"""
    ops, _ = run(ged(text))
    assert sorted(c["page"] for c in by_op(ops, "add_citation")) == ["p 1", "p 1 [quality 1]"]
    assert validate(ops) == []


def test_a_familysearch_id_that_is_not_one_is_refused_not_written():
    text = "0 HEAD\n0 @I1@ INDI\n1 NAME Ana /Ruiz/\n1 DEAT\n2 DATE 1870\n1 _FSFTID not-an-id\n0 TRLR"
    ops, rep = run(ged(text))
    assert by_op(ops, "set_external_id") == []
    assert rep["external_ids_refused"][0]["id"] == "not-an-id"


def test_a_person_with_no_given_name_is_held_and_the_family_keeps_the_other_parent():
    text = """0 HEAD
0 @I1@ INDI
1 NAME /Ruiz/
1 FAMS @F1@
0 @I2@ INDI
1 NAME Pablo /Gil/
1 DEAT
2 DATE 1870
1 FAMS @F1@
0 @F1@ FAM
1 WIFE @I1@
1 HUSB @I2@
0 TRLR"""
    ops, rep = run(ged(text))
    assert [o["first_name"] for o in by_op(ops, "add_person")] == ["Pablo"]
    assert "mother" not in by_op(ops, "add_family")[0]
    assert {"xref": "@I1@", "why": "no given name"} in rep["held"]


def test_root_imports_only_that_persons_tree():
    ops, _ = run(FIX / "split.ged", only=set().union(*split_mod.reach(read(str(FIX / "split.ged")), "@I1@")[:2]))
    names = {o["first_name"] for o in by_op(ops, "add_person")}
    assert names == {"Tomás", "Elena", "Ramón", "Carmen"}


def test_record_gedcom_parents_are_dated_by_their_child_so_not_held_as_living():
    ops, rep = run(FIX / "record-oa.ged")
    assert len(by_op(ops, "add_person")) == 3 and rep["living_held"] == []


# --- round trip through gwt-apply ----------------------------------------------------------------------------

def test_round_trip_through_the_apply_fake_and_a_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    from fake_api import FakeGrampsWeb
    from gwt_apply import snapshot
    from gwt_apply.client import Client
    from gwt_apply.ops import Applier

    fake = FakeGrampsWeb()
    client = Client("https://gw.example", "u", "pw", dry_run=False, session=fake)
    client.login()
    path = tmp_path / "before.json.gz"
    snapshot.save(client, str(path))
    ops, _ = run(FIX / "seven.ged", snapshot.load(str(path)))
    assert preflight(ops, client) == []
    results = Applier(client, retries=1, retry_wait=0).run(ops)
    assert [(r.key, r.status, r.actions) for r in results if r.status != "done"] == []

    after = tmp_path / "after.json.gz"
    snapshot.save(client, str(after))
    objs = snapshot.load(str(after))["objects"]
    assert (len(objs["Person"]), len(objs["Family"]), len(objs["Event"]), len(objs["Place"])) == (3, 1, 8, 3)
    events = {(e["type"], e["date"]["calendar"]): e for e in objs["Event"]}
    assert events[("Birth", 3)]["date"]["sortval"] == gregorian_sdn(1799, 11, 9)  # 18 Brumaire an VIII
    assert events[("Birth", 1)]["date"]["sortval"] == gregorian_sdn(1801, 3, 13)
    jean = next(p for p in objs["Person"] if p["primary_name"]["first_name"] == "Jean")
    assert [a["first_name"] for a in jean["alternate_names"]] == ["Jean-Baptiste"]
    assert any(a["type"] == "WikiTree ID" and a["value"] == "1234567" for a in jean["attribute_list"])
    notes = {n["text"]["string"] if isinstance(n["text"], dict) else n["text"] for n in objs["Note"]}
    assert "@home: the family lived above the forge." in notes

    # the same file again, on the new snapshot: the same list, and every op a skip
    again, rep = run(FIX / "seven.ged", snapshot.load(str(after)))
    assert rep["namesakes"] == [] and rep["preflight"] == []
    writes = len(fake.writes())
    assert {r.status for r in Applier(client, retries=1, retry_wait=0).run(again)} == {"skip"}
    assert len(fake.writes()) == writes


# --- audit and split -----------------------------------------------------------------------------------------

def test_audit_finds_look_alikes_bad_dates_orphans_and_broken_pointers():
    text = """0 HEAD
0 @I1@ INDI
1 NAME Ana /Ruiz/
1 BIRT
2 DATE 1800
1 DEAT
2 DATE 1790
1 FAMS @F1@
0 @I2@ INDI
1 NAME Ana /Ruiz/
1 BIRT
2 DATE 1801
0 @I3@ INDI
1 NAME Luis /Gil/
1 BIRT
2 DATE 1805
1 FAMC @F1@
1 SOUR @S9@
0 @F1@ FAM
1 WIFE @I1@
1 CHIL @I3@
0 @N1@ NOTE nobody reads this
0 TRLR"""
    a = audit_mod.audit(read(ged(text)))
    assert [(d["a"][:4], d["b"][:4]) for d in a["duplicates"]] == [("@I1@", "@I2@")]
    problems = {d["problem"] for d in a["dates"]}
    assert {"dies before they are born", "born when the parent was 5", "born after the parent died"} <= problems
    assert a["orphans"] == ["@I2@"]
    assert a["dangling"] == [{"line": 18, "tag": "SOUR", "to": "@S9@"}]
    assert a["unreferenced"] == ["@N1@"]
    assert "people who look like another record: 1" in audit_mod.summary(a)


def test_split_writes_each_roots_blood_relatives_with_spouse_stubs(tmp_path):
    g = read(str(FIX / "split.ged"))
    rows = split_mod.split(g, ["@I1@", "@I5@"], str(tmp_path))
    one = read(str(tmp_path / "I1.ged"))
    two = read(str(tmp_path / "I5.ged"))
    names = lambda x: sorted(r.val("NAME") for r in x.individuals)  # noqa: E731
    # Tomás's tree: his parents Ramón and Carmen, and him; his wife Elena is a stub
    assert names(one) == ["Carmen /Soler/", "Elena /Vidal/", "Ramón /Navarro/", "Tomás /Navarro/"]
    # Elena's tree: her parents and her; Tomás is a stub there, and his parents stay out
    assert names(two) == ["Elena /Vidal/", "Jaime /Vidal/", "Lucía /Prat/", "Tomás /Navarro/"]
    stub = next(r for r in two.individuals if r.val("NAME") == "Tomás /Navarro/")
    assert stub.all("FAMC") == [] and len(stub.all("FAMS")) == 1
    # carried with the person who cites it; a stub keeps their own record, sources included
    assert [r.val("TITL") for r in one.sources] == [r.val("TITL") for r in two.sources] == ["Padrón 1850"]
    assert not [r for r in two.individuals if r.val("NAME") in ("Ramón /Navarro/", "Carmen /Soler/")]
    assert {r["root"]: r["shared_with_other_roots"] for r in rows} == {"@I1@": 2, "@I5@": 2}
    # the parents are blood relatives reached through the root, not stubs
    assert {r["root"]: (r["people"], r["spouse_stubs"], r["families"]) for r in rows} == {
        "@I1@": (3, 1, 2), "@I5@": (3, 1, 2)}
    assert one.problems == [] and one.declared == "UTF-8"
    with pytest.raises(FileExistsError):
        split_mod.split(g, ["@I1@"], str(tmp_path))


def test_split_refuses_a_root_that_is_not_a_person():
    with pytest.raises(ValueError):
        split_mod.reach(read(str(FIX / "split.ged")), "@F1@")


# --- command line --------------------------------------------------------------------------------------------

def _tree_file(tmp_path, tree=None):
    p = tmp_path / "tree.json.gz"
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        json.dump(tree or snap(), fh)
    return str(p)


def test_cli_writes_the_list_and_never_overwrites_without_force(tmp_path, capsys):
    out, rep = tmp_path / "c.jsonl", tmp_path / "r.json"
    args = [str(FIX / "seven.ged"), "--tree", _tree_file(tmp_path), "--batch", "fam-a", "--out", str(out),
            "--report", str(rep)]
    assert main(args) == 0
    ops = [json.loads(line) for line in out.read_text().splitlines()]
    assert validate(ops) == [] and ops[0]["ruling"].startswith("GEDCOM import of seven.ged")
    assert main(args) == 2 and "exists" in capsys.readouterr().err
    assert main(args + ["--force"]) == 0


def test_cli_refuses_bad_decisions_before_reading_anything(tmp_path, capsys):
    base = [str(FIX / "seven.ged"), "--tree", _tree_file(tmp_path), "--batch", "b", "--out", str(tmp_path / "o")]
    assert main(base + ["--same", "@I1@=not an id"]) == 2
    assert main(base + ["--same", "@I1@=I0001", "--different", "@I1@=why"]) == 2
    assert main(base + ["--different", "@I99@=why"]) == 2
    assert main(base[:-4] + ["--batch", "Bad Name", "--out", str(tmp_path / "o")]) == 2
    assert not (tmp_path / "o").exists()


def test_cli_exits_1_when_preflight_would_still_refuse(tmp_path):
    j, ej = tree_person("h1", "I0001", "Juan", "García", 1650)
    m, em = tree_person("h2", "I0002", "María", "López", 1655)
    j["family_list"] = m["family_list"] = ["fam"]
    fam = {"handle": "fam", "gramps_id": "F0001", "father_handle": "h1", "mother_handle": "h2"}
    tree = _tree_file(tmp_path, snap(Person=[j, m], Event=ej + em, Family=[fam]))
    args = [str(FIX / "latin1-551.ged"), "--tree", tree, "--batch", "b", "--out", str(tmp_path / "o"),
            "--different", "@I1@=another Juan", "--different", "@I2@=another María"]
    assert main(args) == 1


def test_cli_audit_and_split(tmp_path, capsys):
    assert main(["audit", str(FIX / "latin1-551.ged")]) == 0
    assert "lines read in another encoding: 1" in capsys.readouterr().out
    assert main(["audit", str(FIX / "latin1-551.ged"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["file"]["declared"] == "ANSI"
    assert main(["split", str(FIX / "split.ged"), "--root", "@I1@", "--out-dir", str(tmp_path)]) == 0
    assert os.path.exists(tmp_path / "I1.ged")
    assert main(["split", str(FIX / "split.ged"), "--root", "@I99@", "--out-dir", str(tmp_path / "x")]) == 2


# --- review round 1: encodings, bare lines, _UID, couples, decisions, speed ----------------------------------

def test_a_utf8_character_cut_across_conc_is_put_back_together():
    raw = (b"0 HEAD\n1 CHAR UTF-8\n0 @I1@ INDI\n1 NAME Ana /Ruiz/\n1 NOTE Born in Le\xc3\n"
           b"2 CONC \xb3n, daughter of Mar\xc3\n2 CONC \xada\n0 TRLR\n")
    g = read(raw)
    assert g.individuals[0].first("NOTE").value == "Born in León, daughter of María"
    assert g.repairs == []


def test_an_ansel_accent_cut_from_its_letter_by_conc_is_put_back():
    raw = b"0 HEAD\n1 CHAR ANSEL\n0 @I1@ INDI\n1 NOTE Born in Le\xe2\n2 CONC on.\n0 TRLR\n"
    g = read(raw)
    assert g.individuals[0].first("NOTE").value == "Born in León."
    assert g.repairs == []


def test_a_bare_line_inside_a_note_is_kept_as_a_new_line_of_it():
    raw = ged("0 HEAD\n0 @I1@ INDI\n1 NAME Ana /Ruiz/\n1 NOTE First line\n2 CONC  of the note\n"
              "https://www.example.org/record/1\n12 de marzo de 1850\n1 SEX F\n0 TRLR")
    g = read(raw)
    note = g.individuals[0].first("NOTE").value
    assert note == "First line of the note\nhttps://www.example.org/record/1\n12 de marzo de 1850"
    assert [(j["line"], j["into"]) for j in g.joined] == [(6, "NOTE"), (7, "NOTE")]
    assert g.problems == [] and g.individuals[0].val("SEX") == "F"
    # after a line that is not text, a bare line is still a problem, never glued to a name
    g = read(ged("0 HEAD\n0 @I1@ INDI\n1 NAME Ana /Ruiz/\nnot a line\n0 TRLR"))
    assert g.joined == [] and any("line 4" in p for p in g.problems)


def test_unicode_without_nul_bytes_is_read_as_utf8_and_reported():
    g = read(b"0 HEAD\n1 CHAR UNICODE\n0 @I1@ INDI\n1 NAME Ana /L\xc3\xb3pez/\n0 TRLR\n")
    assert g.individuals[0].val("NAME") == "Ana /López/"
    assert [(r["line"], r["read_as"]) for r in g.repairs] == [(2, "utf-8")]


def test_utf16_without_a_bom_and_utf32_with_one_are_read():
    body = "0 HEAD\n1 CHAR UNICODE\n0 @I1@ INDI\n1 NAME Zoë /Smith/\n0 TRLR\n"
    for raw in (body.encode("utf-16-le"), body.encode("utf-16-be"), codecs.BOM_UTF32_LE + body.encode("utf-32-le")):
        g = read(raw)
        assert g.individuals[0].val("NAME") == "Zoë /Smith/" and g.repairs == []


def test_a_windows_file_labelled_ansel_is_read_as_windows_1252_and_reported():
    g = read(b"0 HEAD\n1 CHAR ANSEL\n0 @I1@ INDI\n1 NAME Jos\xe9 /Mu\xf1oz/\n0 TRLR\n")
    assert g.individuals[0].val("NAME") == "José /Muñoz/"
    assert [(r["line"], r["read_as"]) for r in g.repairs] == [(4, "cp1252")]
