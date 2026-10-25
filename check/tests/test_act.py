import copy
import json

import pytest

from gwt_check import selftest
from gwt_check.act import compare_act, load_act
from gwt_check.cli import main
from gwt_check.tree import BadInput, Tree


def _tree():
    return Tree(copy.deepcopy(selftest.clean_tree()))


def _act(**kw):
    a = {"kind": "birth", "date": "1900-03-01", "place": "Villaejemplo", "reference": "folio 1",
         "subject": {"given": "Lucía", "surname": "Ortega"},
         "father": {"given": "Mateo", "surname": "Ortega"}, "mother": {"given": "Clara", "surname": "Sanz"}}
    a.update(kw)
    return a


def test_an_act_that_agrees_reports_every_field_as_same():
    [r] = compare_act(_tree(), _act())["candidates"]
    [e] = r["events"]
    assert (e["date"], e["place"]) == ("same", "same")
    assert e["cited"].startswith("text found in C0001") and "not checked" in e["cited"]
    assert r["parents"] == {"father": "names agree in F0001", "mother": "names agree in F0001"}


def test_a_different_date_is_reported_and_nothing_is_corrected():
    t = _tree()
    before = copy.deepcopy(t.objs)
    [r] = compare_act(t, _act(date="1900-03-02"))["candidates"]
    assert r["events"][0]["date"].startswith("differs")
    assert t.objs == before


def test_a_year_only_act_is_compatible_with_the_exact_day_not_equal():
    [r] = compare_act(_tree(), _act(date="1900"))["candidates"]
    assert r["events"][0]["date"].startswith("compatible, not equal")


def test_a_different_place_and_a_different_father_are_reported():
    [r] = compare_act(_tree(), _act(place="Otrolugar", father={"given": "Pedro", "surname": "Ortega"}))["candidates"]
    assert r["events"][0]["place"].startswith("differs")
    assert r["parents"]["father"].startswith("names differ in")


def test_a_place_that_only_encloses_the_tree_place_is_not_called_the_same():
    [r] = compare_act(_tree(), _act(place="Provincia Imaginaria"))["candidates"]
    assert r["events"][0]["place"] == "encloses the tree place (tree Villaejemplo)"


def test_an_act_naming_no_citation_reference_matches_none():
    [r] = compare_act(_tree(), _act(reference="folio 9"))["candidates"]
    assert r["events"][0]["cited"] == "no citation text contains it"
    [r] = compare_act(_tree(), _act(subject={"given": "Tomás", "surname": "Ortega"}, reference="folio 1"))["candidates"]
    assert r["events"][0]["cited"] == "event has no citation"


def test_a_subject_not_in_the_tree_is_absent_and_nothing_is_proposed():
    rep = compare_act(_tree(), _act(subject={"given": "Nadie", "surname": "Inventado"}))
    assert rep["verdict"] == "subject not in the tree" and rep["candidates"] == []


def test_two_people_who_fit_are_both_listed_and_none_is_chosen():
    rep = compare_act(_tree(), _act(subject={"given": "", "surname": "Ortega"}, kind="birth"))
    assert rep["verdict"].startswith("several people") and len(rep["candidates"]) > 1


def test_a_marriage_is_found_through_the_family_with_that_spouse():
    act = {"kind": "marriage", "date": "1895", "place": "Villaejemplo", "subject": {"given": "Mateo", "surname": "Ortega"},
           "spouse": {"given": "Clara", "surname": "Sanz"}}
    [r] = compare_act(_tree(), act)["candidates"]
    assert r["family"] == ["F0001"] and r["events"][0]["date"] == "same"
    act["spouse"] = {"given": "Inés", "surname": "Ruiz"}
    [r] = compare_act(_tree(), act)["candidates"]
    assert "no family with that spouse" in r["event"]


def test_a_bad_act_file_is_refused(tmp_path):
    p = tmp_path / "a.json"
    for bad in ({"kind": "census"}, {**_act(), "date": "1900-13"}, {**_act(), "subject": {}},
                {"kind": "marriage", "date": "1900", "subject": {"given": "A"}}):
        p.write_text(json.dumps(bad))
        with pytest.raises(BadInput):
            load_act(str(p))


def test_the_command_never_sets_the_exit_code_for_a_disagreement(tmp_path, capsys):
    d = tmp_path / "t.json"
    d.write_text(json.dumps({"tree": "x", "dumped_at": "", "objects": selftest.clean_tree()}))
    a = tmp_path / "a.json"
    a.write_text(json.dumps(_act(date="1901-01-01")))
    assert main(["act", str(a), str(d)]) == 0
    assert "differs" in capsys.readouterr().out


def test_a_tree_name_with_fewer_words_than_the_act_is_listed_but_never_taken_as_the_subject():
    rep = compare_act(_tree(), _act(subject={"given": "Lucía Maria", "surname": "Ortega Sanz"}))
    assert rep["verdict"].startswith("the tree has people with fewer names")
    [r] = rep["candidates"]
    assert r["name_note"] == "the tree gives fewer names than the act"
    assert compare_act(_tree(), _act(subject={"given": "Nadie", "surname": "Inventado"}))["candidates"] == []


def test_a_reference_matches_whole_words_so_folio_1_is_not_folio_15():
    t = _tree()
    selftest._get(t.objs and {k: list(v.values()) for k, v in t.objs.items()}, "citations", "C0001")["page"] = "folio 15"
    [r] = compare_act(t, _act(reference="folio 1"))["candidates"]
    assert r["events"][0]["cited"] == "no citation text contains it"
    selftest._get({k: list(v.values()) for k, v in t.objs.items()}, "citations", "C0001")["page"] = "libro 2, folio 1, acta 3"
    [r] = compare_act(t, _act(reference="folio 1"))["candidates"]
    assert r["events"][0]["cited"].startswith("text found in C0001")


def test_a_second_parental_family_that_agrees_is_not_hidden_by_a_first_that_differs():
    d = copy.deepcopy(selftest.clean_tree())
    P = {p["gramps_id"]: p for p in d["people"]}
    fam = copy.deepcopy(next(f for f in d["families"] if f["gramps_id"] == "F0001"))
    fam["gramps_id"], fam["handle"] = "F0009", "hF0009"
    fam["father_handle"], fam["mother_handle"] = "hI0004", "hI0005"
    fam["child_ref_list"], fam["event_ref_list"] = [], []
    d["families"].insert(0, fam)  # a different couple comes first
    P["I0001"]["parent_family_list"] = ["hF0009", "hF0001"]
    [r] = compare_act(Tree(d), _act())["candidates"]
    assert r["parents"]["father"] == "names agree in F0001; not in F0009"
    assert r["parents"]["mother"] == "names agree in F0001; not in F0009"
    [r] = compare_act(Tree(d), _act(father={"given": "Zeno", "surname": "Nadie"}))["candidates"]
    assert r["parents"]["father"] == "names differ in F0009, F0001"


def test_a_tree_date_equal_to_the_registration_date_is_named_as_such_and_registered_is_never_compared():
    t = _tree()
    ev = next(e for e in t.objs["events"].values() if e["gramps_id"] == "E0001")
    ev["date"] = {"dateval": [2, 3, 1900, False], "modifier": 0, "quality": 0}
    [r] = compare_act(t, _act(registered="1900-03-02"))["candidates"]
    assert "matches the registered day, which is not the day of the event" in r["events"][0]["date"]
    [r] = compare_act(t, _act(date="1900-03-02", registered="1900-03-09"))["candidates"]
    assert r["events"][0]["date"] == "same"
    [r] = compare_act(t, _act())["candidates"]
    assert r["events"][0]["date"] == "differs (tree 1900-03-02)"


def test_a_bad_registered_date_is_refused(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps({**_act(), "registered": "1900-02-31"}))
    with pytest.raises(BadInput):
        load_act(str(p))


def test_the_command_needs_a_dump_and_never_reads_the_whole_live_tree(tmp_path, capsys):
    f = tmp_path / "a.json"
    f.write_text(json.dumps(_act()))
    with pytest.raises(SystemExit) as exc:
        main(["act", str(f)])
    assert exc.value.code == 2


def test_a_godparent_ref_to_a_baptism_is_not_the_persons_own_event():
    t = _tree()
    p = next(x for x in t.objs["people"].values() if x["gramps_id"] == "I0001")
    p["event_ref_list"][0]["role"] = "Godparent"
    [r] = compare_act(t, _act())["candidates"]
    assert r["event"] == "tree has no such event" and "events" not in r


def test_a_private_person_is_counted_but_nothing_of_them_is_shown(capsys):
    t = _tree()
    next(x for x in t.objs["people"].values() if x["gramps_id"] == "I0001")["private"] = True
    rep = compare_act(t, _act())
    assert rep["candidates"] == [{"person": "I0001", "private": True}]
    from gwt_check.act import print_act
    print_act(rep)
    out = capsys.readouterr().out
    assert "I0001 (marked private: held back)" in out and "lucia" not in out.lower()


def test_a_private_citation_or_note_is_not_searched_for_the_reference():
    t = _tree()
    c = next(x for x in t.objs["citations"].values() if x["gramps_id"] == "C0001")
    c["page"] = "nothing here"
    next(x for x in t.objs["notes"].values() if x["gramps_id"] == "N0002")["text"]["string"] = "folio 1"
    [r] = compare_act(t, _act())["candidates"]
    assert r["events"][0]["cited"].startswith("text found in C0001")
    next(x for x in t.objs["notes"].values() if x["gramps_id"] == "N0002")["private"] = True
    [r] = compare_act(t, _act())["candidates"]
    assert r["events"][0]["cited"] == "no citation text contains it"
    c["page"], c["private"] = "folio 1", True
    [r] = compare_act(t, _act())["candidates"]
    assert r["events"][0]["cited"] == "no citation text contains it"


@pytest.mark.parametrize("bad", [{"date": 1900}, {"kind": ["birth"]}, {"father": "Mateo Ortega"}, {"subject": 5},
                                 {"place": 5}, {"reference": 1}, {"registered": 1900},
                                 {"subject": {"given": ["Lucía"], "surname": "Ortega"}}])
def test_a_badly_typed_act_is_refused_as_bad_input_not_a_crash(tmp_path, capsys, bad):
    a = tmp_path / "a.json"
    a.write_text(json.dumps({**_act(), **bad}))
    d = tmp_path / "t.json"
    d.write_text(json.dumps({"tree": "x", "dumped_at": "", "objects": selftest.clean_tree()}))
    assert main(["act", str(a), str(d)]) == 2


def _second_mateo(d):
    """Another Mateo Ortega, married to someone else."""
    me = copy.deepcopy(next(p for p in d["people"] if p["gramps_id"] == "I0002"))
    me.update(gramps_id="I0009", handle="hI0009", family_list=["hF0009"], parent_family_list=[],
              event_ref_list=[])
    fam = copy.deepcopy(next(f for f in d["families"] if f["gramps_id"] == "F0002"))
    fam.update(gramps_id="F0009", handle="hF0009", father_handle="hI0009", child_ref_list=[], event_ref_list=[])
    d["people"].append(me)
    d["families"].append(fam)
    return d


def test_a_marriage_act_counts_only_the_namesake_who_married_that_spouse():
    t = Tree(_second_mateo(copy.deepcopy(selftest.clean_tree())))
    act = {"kind": "marriage", "date": "1895", "subject": {"given": "Mateo", "surname": "Ortega"},
           "spouse": {"given": "Clara", "surname": "Sanz"}}
    rep = compare_act(t, act)
    assert [r["person"] for r in rep["candidates"]] == ["I0002"]
    assert rep["verdict"] == "one person's name fits and has a family with that spouse"
    act["spouse"] = {"given": "Nadie", "surname": "Inventado"}  # nobody married her: every namesake is shown
    rep = compare_act(t, act)
    assert len(rep["candidates"]) == 2 and rep["verdict"].startswith("several people's names fit")


def test_person_names_the_subject_so_no_name_search_is_made(tmp_path, capsys):
    t = Tree(_second_mateo(copy.deepcopy(selftest.clean_tree())))
    act = _act(subject={"given": "Mateo", "surname": "Ortega"}, date="1870-03-02", father=None, mother=None)
    assert len(compare_act(t, act)["candidates"]) == 2
    rep = compare_act(t, act, "I0002")
    [r] = rep["candidates"]
    assert r["person"] == "I0002" and r["events"][0]["date"] == "same" and "name_note" not in r
    assert rep["verdict"] == "the person was named, not searched for"
    [r] = compare_act(t, act, "I0003")["candidates"]
    assert r["name_note"] == "the act's name does not fit this person"
    with pytest.raises(BadInput):
        compare_act(t, act, "I9999")
    d = tmp_path / "t.json"
    d.write_text(json.dumps({"tree": "x", "dumped_at": "", "objects": selftest.clean_tree()}))
    a = tmp_path / "a.json"
    a.write_text(json.dumps(act))
    assert main(["act", str(a), str(d), "--person", "I0002"]) == 0
    assert "I0002" in capsys.readouterr().out


def test_a_tree_date_kept_as_text_only_is_read():
    t = _tree()
    ev = next(e for e in t.objs["events"].values() if e["gramps_id"] == "E0001")
    ev["date"] = {"dateval": [0, 0, 0, False], "modifier": 6, "quality": 0, "text": "1900-03-01"}
    [r] = compare_act(t, _act())["candidates"]
    assert r["events"][0]["date"] == "same"


def test_the_text_report_prints_at_most_limit_people(capsys):
    from gwt_check.act import print_act
    rep = compare_act(_tree(), _act(subject={"surname": "Ortega"}))
    print_act(rep, 1)
    out = capsys.readouterr().out
    assert f"first 1 of {len(rep['candidates'])} people" in out and out.count("  I00") == 1
