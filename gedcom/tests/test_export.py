"""gwt-import-gedcom export: the GEDCOM writer and its privacy rules. Every name is invented."""

import json
from datetime import date as _today

from gwt_apply.dates import (CAL_JULIAN, MOD_ABOUT, MOD_RANGE, MOD_TEXTONLY)
from gwt_gedcom.cli import main
from gwt_gedcom.export import build, load
from gwt_gedcom.parse import read

YEAR = _today.today().year
CLASSES = ["Person", "Family", "Event", "Place", "Citation", "Source", "Repository", "Media", "Note", "Tag"]


def snap(**objects):
    return {"format": "gwt-apply-snapshot/1", "tree": "t", "objects": {c: list(objects.get(c, [])) for c in CLASSES}}


def person(handle, gid, first="", surname="", sex=1, events=(), families=(), parents=(), notes=(), private=False):
    return {"handle": handle, "gramps_id": gid, "gender": sex,
            "primary_name": {"first_name": first, "surname_list": [{"surname": surname}] if surname else []},
            "event_ref_list": [{"ref": h, "role": "Primary"} for h in events],
            "family_list": list(families), "parent_family_list": list(parents),
            "note_list": list(notes), "citation_list": [], "private": private}


def event(handle, gid, etype, year=0, month=0, day=0, place=None, modifier=0, quality=0, calendar=0, text=""):
    dateval = [day, month, year] + ([0, 0, 0] if modifier == MOD_RANGE else [])
    return {"handle": handle, "gramps_id": gid, "type": etype, "place": place,
            "date": {"calendar": calendar, "modifier": modifier, "quality": quality, "dateval": dateval,
                     "text": text, "sortval": 0, "newyear": 0},
            "description": "", "note_list": [], "citation_list": [], "private": False}


def family(handle, gid, father=None, mother=None, children=(), events=(), private=False):
    return {"handle": handle, "gramps_id": gid, "father_handle": father, "mother_handle": mother,
            "child_ref_list": [{"ref": h} for h in children],
            "event_ref_list": [{"ref": h, "role": "Primary"} for h in events],
            "note_list": [], "citation_list": [], "private": private}


def source(handle, gid, title, private=False):
    return {"handle": handle, "gramps_id": gid, "title": title, "author": "", "pubinfo": "", "abbrev": "",
            "note_list": [], "citation_list": [], "private": private}


def note(handle, gid, text, private=False):
    return {"handle": handle, "gramps_id": gid, "text": {"string": text}, "type": "General", "private": private}


def export(data, **kw):
    lines, _ = build(data, **kw)
    return "\n".join(lines) + "\n"


def test_dead_person_exports_in_full():
    data = snap(Person=[person("p1", "I1", "Ana", "Vega", sex=0, events=["e1", "e2"])],
                Event=[event("e1", "E1", "Birth", 1890, 3, 12), event("e2", "E2", "Death", 1970)])
    out = export(data, this_year=YEAR)
    assert "0 @I1@ INDI" in out
    assert "1 NAME Ana /Vega/" in out
    assert "1 SEX F" in out
    assert "1 BIRT\n  2 DATE 12 MAR 1890" in out.replace("1 BIRT\n2", "1 BIRT\n  2") or "2 DATE 12 MAR 1890" in out
    assert "1 DEAT" in out and "2 DATE 1970" in out


def test_living_person_is_only_a_placeholder():
    data = snap(Person=[person("p1", "I1", "Ana", "Vega", events=["e1"], notes=["n1"])],
                Event=[event("e1", "E1", "Birth", YEAR - 50)],
                Note=[note("n1", "N1", "secret life detail")])
    out = export(data, this_year=YEAR)
    assert "1 NAME Living" in out
    assert "Ana" not in out and "Vega" not in out
    assert "BIRT" not in out and "secret life detail" not in out
    assert "1 SEX M" in out  # the placeholder keeps the sex, nothing else


def test_undated_person_counts_as_living():
    data = snap(Person=[person("p1", "I1", "Ana", "Vega")])
    out = export(data, this_year=YEAR)
    assert "1 NAME Living" in out
    assert "Ana" not in out


def test_birth_older_than_the_limit_is_not_living():
    data = snap(Person=[person("p1", "I1", "Ana", "Vega", events=["e1"])],
                Event=[event("e1", "E1", "Birth", YEAR - 111)])
    out = export(data, this_year=YEAR)
    assert "1 NAME Ana /Vega/" in out
    # born exactly at the limit still counts as living
    data = snap(Person=[person("p1", "I1", "Ana", "Vega", events=["e1"])],
                Event=[event("e1", "E1", "Birth", YEAR - 110)])
    assert "1 NAME Living" in export(data, this_year=YEAR)


def test_private_person_never_leaves_the_tree():
    fam = family("f1", "F1", father="p2", mother="p1", children=["p3"])
    data = snap(Person=[person("p1", "I1", "Ana", "Vega", sex=0, events=["e1"], families=["f1"]),
                        person("p2", "I2", "Hidden", "Man", events=["e2"], families=["f1"], private=True),
                        person("p3", "I3", "Child", "Vega", events=["e3"], parents=["f1"])],
                Event=[event("e1", "E1", "Death", 1970), event("e2", "E2", "Death", 1960),
                       event("e3", "E3", "Death", 1980)],
                Family=[fam])
    out = export(data, this_year=YEAR)
    assert "Hidden" not in out and "@I2@" not in out
    assert "1 HUSB" not in out          # the private father leaves the family
    assert "1 WIFE @I1@" in out and "1 CHIL @I3@" in out
    # a family that loses every member is not written
    data = snap(Person=[person("p2", "I2", "Hidden", "Man", private=True)],
                Family=[family("f1", "F1", father="p2")])
    out = export(data, this_year=YEAR)
    assert "FAM" not in out


def test_private_note_and_source_never_leave():
    ev = event("e1", "E1", "Death", 1970)
    ev["citation_list"] = ["c1", "c2"]
    data = snap(Person=[person("p1", "I1", "Ana", "Vega", sex=0, events=["e1"], notes=["n1", "n2"])],
                Event=[ev],
                Citation=[{"handle": "c1", "gramps_id": "C1", "source_handle": "s1", "page": "p. 12",
                           "confidence": 3, "note_list": [], "private": False},
                          {"handle": "c2", "gramps_id": "C2", "source_handle": "s2", "page": "p. 99",
                           "confidence": 1, "note_list": [], "private": False}],
                Source=[source("s1", "S1", "Parish book"), source("s2", "S2", "Private dossier", private=True)],
                Note=[note("n1", "N1", "public note"), note("n2", "N2", "private note", private=True)])
    out = export(data, this_year=YEAR)
    assert "public note" in out and "private note" not in out and "@N2@" not in out
    assert "Parish book" in out and "Private dossier" not in out
    assert "2 SOUR @S1@" in out and "3 PAGE p. 12" in out and "3 QUAY 3" in out
    assert "@S2@" not in out and "p. 99" not in out


def test_family_events_only_when_neither_spouse_is_living():
    wed = family("f1", "F1", father="p1", mother="p2", events=["e3"])
    data = snap(Person=[person("p1", "I1", "Ana", "Vega", sex=0, events=["e1"], families=["f1"]),
                        person("p2", "I2", "Luis", "Rio", events=["e2"], families=["f1"])],
                Event=[event("e1", "E1", "Birth", YEAR - 50),  # Ana living
                       event("e2", "E2", "Death", 1960),
                       event("e3", "E3", "Marriage", 1950, place="pl1")],
                Place=[{"handle": "pl1", "gramps_id": "P1", "name": {"value": "Village"},
                        "placeref_list": [{"ref": "pl2"}], "private": False},
                       {"handle": "pl2", "gramps_id": "P2", "name": {"value": "Province"},
                        "placeref_list": [], "private": False}],
                Family=[wed])
    out = export(data, this_year=YEAR)
    assert "MARR" not in out  # a spouse may be living: the family's events stay in the tree
    # both dead: the marriage is written, with its place hierarchy
    data["objects"]["Person"][0]["event_ref_list"] = [{"ref": "e1", "role": "Primary"}]
    data["objects"]["Event"][0]["type"] = "Death"
    out = export(data, this_year=YEAR)
    assert "1 MARR" in out and "2 DATE 1950" in out and "2 PLAC Village, Province" in out


def test_custom_event_type_is_even_with_type():
    data = snap(Person=[person("p1", "I1", "Ana", "Vega", sex=0, events=["e1"])],
                Event=[event("e1", "E1", "Death", 1970),
                       event("e2", "E2", "Custom Rite", 1950)])
    data["objects"]["Person"][0]["event_ref_list"].append({"ref": "e2", "role": "Primary"})
    out = export(data, this_year=YEAR)
    assert "1 EVEN" in out and "2 TYPE Custom Rite" in out


def test_dates_never_guessed():
    ev = event("e1", "E1", "Death", modifier=MOD_TEXTONLY, text="Easter 1700")
    data = snap(Person=[person("p1", "I1", "Ana", "Vega", sex=0, events=["e1"])], Event=[ev])
    out = export(data, this_year=YEAR)
    assert "2 DATE (Easter 1700)" in out
    # range and calendar escapes keep their meaning
    ev2 = event("e2", "E2", "Birth", 1700, 0, 0, modifier=MOD_RANGE)
    ev2["date"]["dateval"] = [0, 0, 1700, False, 0, 0, 1701, False]  # Gramps layout: d,m,y,slash,d2,m2,y2
    ev3 = event("e3", "E3", "Baptism", 1700, 2, 11, calendar=CAL_JULIAN)
    ev4 = event("e4", "E4", "Burial", 1700, 0, 0, modifier=MOD_ABOUT)
    data["objects"]["Event"] += [ev2, ev3, ev4]
    data["objects"]["Person"][0]["event_ref_list"] += [{"ref": h, "role": "Primary"} for h in ("e2", "e3", "e4")]
    out = export(data, this_year=YEAR)
    assert "2 DATE BET 1700 AND 1701" in out
    assert "2 DATE @#DJULIAN@ 11 FEB 1700" in out
    assert "2 DATE ABT 1700" in out


def test_family_events_hidden_when_a_spouse_is_private():
    fam = family("f1", "F1", father="p2", mother="p1", events=["e3"])
    fam["note_list"] = ["n9"]
    data = snap(Person=[person("p1", "I1", "Ana", "Vega", sex=0, events=["e1"], families=["f1"]),
                        person("p2", "I2", "Hidden", "Man", events=["e2"], families=["f1"], private=True)],
                Event=[event("e1", "E1", "Death", 1970), event("e2", "E2", "Death", 1960),
                       event("e3", "E3", "Marriage", 1950)],
                Note=[note("n9", "N9", "PRIVATE_SPOUSE_FAMILY_SECRET")],
                Family=[fam])
    out = export(data, this_year=YEAR)
    assert "1 WIFE @I1@" in out          # the family stays, without the private father
    assert "MARR" not in out             # but its events never leave: a spouse was private
    assert "PRIVATE_SPOUSE_FAMILY_SECRET" not in out


def test_sources_only_when_cited_by_an_exported_citation():
    ev = event("e1", "E1", "Death", 1970)
    ev["citation_list"] = ["c1"]
    living_ev = event("e2", "E2", "Birth", YEAR - 30)
    living_ev["citation_list"] = ["c2"]
    data = snap(Person=[person("p1", "I1", "Ana", "Vega", sex=0, events=["e1"]),
                        person("p2", "I2", "Bob", "Ray", events=["e2"])],
                Event=[ev, living_ev],
                Citation=[{"handle": "c1", "gramps_id": "C1", "source_handle": "s1", "page": "p. 1",
                           "confidence": 2, "note_list": [], "private": False},
                          {"handle": "c2", "gramps_id": "C2", "source_handle": "s2", "page": "p. 2",
                           "confidence": 2, "note_list": [], "private": False}],
                Source=[source("s1", "S1", "Parish book"),
                        source("s2", "S2", "cited only by a living person"),
                        source("s3", "S3", "LIVING_REAL_NAME medical biography")])
    out = export(data, this_year=YEAR)
    assert "Parish book" in out                       # cited by an exported citation
    assert "cited only by a living person" not in out  # its only citation was anonymized away
    assert "LIVING_REAL_NAME" not in out              # never cited: never written


def test_lines_never_exceed_the_limit_or_split_an_escape():
    data = snap(Person=[person("p1", "I1", "Ana", "Vega", sex=0, events=["e1"], notes=["n1"])],
                Event=[event("e1", "E1", "Death", 1970)],
                Note=[note("n1", "N1", "@" * 240 + "\n" + "x" * 239 + "@@" + "y" * 300)])
    lines = export(data, this_year=YEAR).split("\n")
    assert all(len(l) <= 255 for l in lines)
    # no @@ pair is split at the end of a content line (pointers like "@N1@" are fine)
    import re as _re
    content = [l for l in lines if len(l.split(" ", 2)) > 2 and l.split(" ", 2)[1] in ("NOTE", "CONT", "CONC", "TITL", "PAGE", "TYPE")
               and not _re.match(r"^@[^@]+@$", l.split(" ", 2)[2])]  # pointers like "1 NOTE @N1@" are not content
    assert all((len(l) - len(l.rstrip("@"))) % 2 == 0 for l in content)
    import tempfile, os
    from gwt_gedcom.parse import read as ged_read
    with tempfile.NamedTemporaryFile("w", suffix=".ged", delete=False, encoding="utf-8") as fh:
        fh.write("\n".join(lines))
        path = fh.name
    ged = ged_read(path)
    assert ged.problems == []
    os.unlink(path)


def test_names_cannot_inject_records():
    evil = "A\n0 @EVIL@ INDI\n1 NAME Injected"
    data = snap(Person=[person("p1", "I1", evil, "Vega", sex=0, events=["e1"])],
                Event=[event("e1", "E1", "Death", 1970)])
    out = export(data, this_year=YEAR)
    lines = out.split("\n")
    assert not any(l == "0 @EVIL@ INDI" for l in lines)          # no injected record
    assert not any(l.startswith("1 NAME Injected") for l in lines)
    assert sum(1 for l in lines if l.startswith("0 @") and l.endswith(" INDI")) == 1


def test_xrefs_never_collide_or_take_subm():
    people = [person("p1", "I1", "Ana", "Vega", sex=0, events=["e1"]),
              person("p2", "I1", "Bob", "Vega", events=["e2"]),          # duplicate id
              person("p3", "SUBM", "Kim", "Vega", events=["e3"])]        # reserved xref
    people[2]["gramps_id"] = "SUBM1"
    src = source("s1", "I1", "a source whose id collides with a person")
    ev = event("e1", "E1", "Death", 1970)
    ev["citation_list"] = ["c1"]
    data = snap(Person=people,
                Event=[ev, event("e2", "E2", "Death", 1960), event("e3", "E3", "Death", 1950)],
                Citation=[{"handle": "c1", "gramps_id": "C1", "source_handle": "s1", "page": "p. 1",
                           "confidence": 2, "note_list": [], "private": False}],
                Source=[src])
    out = export(data, this_year=YEAR)
    heads = [l.split(" ")[1] for l in out.split("\n") if l.startswith("0 @")]
    assert len(heads) == len(set(heads))           # every record xref is unique
    assert "@SUBM@" in heads and "@SUBM1@" in heads


def test_output_is_a_gedcom_the_reader_takes_back(tmp_path):
    ev = event("e1", "E1", "Death", 1970)
    ev["citation_list"] = ["c1"]
    data = snap(Person=[person("p1", "I1", "Ana @home", "Vega", sex=0, events=["e1"], notes=["n1"],
                               families=["f1"]),
                        person("p2", "I2", "Luis", "Rio", events=["e2"], families=["f1"])],
                Event=[ev, event("e2", "E2", "Death", 1960)],
                Family=[family("f1", "F1", father="p2", mother="p1")],
                Citation=[{"handle": "c1", "gramps_id": "C1", "source_handle": "s1", "page": "p. 12",
                           "confidence": 2, "note_list": [], "private": False}],
                Source=[source("s1", "S1", "Parish book")],
                Note=[note("n1", "N1", "a long " + "x" * 500 + "\nsecond line")])
    out = export(data, this_year=YEAR)
    assert out.startswith("0 HEAD\n") and "1 CHAR UTF-8" in out and "1 SUBM @SUBM@" in out
    assert out.rstrip().endswith("0 TRLR")
    assert "1 NAME Ana @@home /Vega/" in out  # @ doubles so it cannot read as a pointer
    ged = read.__wrapped__ if hasattr(read, "__wrapped__") else read
    f = tmp_path / "out.ged"
    f.write_text(out, encoding="utf-8")
    ged = read(str(f))
    assert ged.problems == []
    indi = ged.individuals
    assert len(indi) == 2


def test_cli_writes_and_refuses_overwrite(tmp_path):
    data = snap(Person=[person("p1", "I1", "Ana", "Vega", sex=0, events=["e1"])],
                Event=[event("e1", "E1", "Death", 1970)])
    dump = tmp_path / "tree.json"
    dump.write_text(json.dumps(data), encoding="utf-8")
    ged, rep = tmp_path / "out.ged", tmp_path / "report.json"
    assert main(["export", str(dump), "--out", str(ged), "--report", str(rep)]) == 0
    assert "0 @I1@ INDI" in ged.read_text(encoding="utf-8")
    r = json.loads(rep.read_text(encoding="utf-8"))
    assert r["people"] == 1 and r["living"] == []
    assert main(["export", str(dump), "--out", str(ged)]) == 2  # exists: --force needed
    assert main(["export", str(dump), "--out", str(ged), "--force"]) == 0


def test_load_rejects_a_non_tree_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"hello": 1}', encoding="utf-8")
    try:
        load(str(bad))
        assert False, "a file without objects must be refused"
    except Exception as e:
        assert "objects" in str(e)


def test_long_name_and_place_stay_within_the_line():
    # 120-char first + 120-char surname + 120-char suffix would make a 371-char NAME line uncapped
    p = person("p1", "I1", "", "", sex=0, events=["e1"])
    p["primary_name"] = {"first_name": "x" * 120, "surname_list": [{"surname": "y" * 120}],
                         "suffix": "z" * 120}
    data = snap(Person=[p],
                Event=[event("e1", "E1", "Death", 1970, place="pl1")],
                Place=[{"handle": "pl1", "gramps_id": "P1", "name": {"value": "A" * 120},
                        "placeref_list": [{"ref": "pl2"}], "private": False},
                       {"handle": "pl2", "gramps_id": "P2", "name": {"value": "B" * 120},
                        "placeref_list": [{"ref": "pl3"}], "private": False},
                       {"handle": "pl3", "gramps_id": "P3", "name": {"value": "C" * 120},
                        "placeref_list": [], "private": False}])
    lines = export(data, this_year=YEAR).split("\n")
    assert all(len(l) <= 255 for l in lines)
    name_lines = [l for l in lines if l.startswith("1 NAME ")]
    assert any(l.startswith("1 NAME x") and "/yyy" in l for l in name_lines)  # truncated, still one NAME line
    assert any(l.startswith("2 PLAC ") for l in lines)                        # the place hierarchy is capped too
    # never cut inside an @@ escape pair (content lines only; pointer values like @F1@ are pairs already)
    assert all((len(l) - len(l.rstrip("@"))) % 2 == 0 for l in name_lines)


def test_xrefs_never_collide_across_kinds():
    # a person and a source sharing a handle must still get different XREFs
    ev = event("e1", "E1", "Death", 1970)
    ev["citation_list"] = ["c1"]
    data = snap(Person=[person("same", "I1", "Ana", "Vega", sex=0, events=["e1"])],
                Event=[ev],
                Citation=[{"handle": "c1", "gramps_id": "C1", "source_handle": "same", "page": "p. 1",
                           "confidence": 2, "note_list": [], "private": False}],
                Source=[source("same", "S1", "Parish book")])
    out = export(data, this_year=YEAR)
    heads = [l for l in out.split("\n") if l.startswith("0 @")]
    assert len(heads) == len(set(heads))  # no record written twice
    assert "0 @I1@ INDI" in heads         # the person keeps her own XREF...
    assert "0 @S1@ SOUR" in heads         # ...and the source keeps its own
    assert "2 SOUR @S1@" in out           # the citation points at the source, never at the person
