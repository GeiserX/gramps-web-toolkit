import copy
import json

from gwt_check import selftest
from gwt_check.cli import main
from gwt_check.links import LEVELS, _words, link_report
from gwt_check.tree import Tree
from test_check import live  # noqa: F401  (the fixture that serves a fake live tree)


def _d(page):
    d = copy.deepcopy(selftest.clean_tree())
    selftest._get(d, "citations", "C0001")["page"] = page  # cited by the birth of I0001
    return d


def _row(d, child="I0001", fam="F0001"):
    return next(r for r in link_report(Tree(d))["rows"] if r["child"] == child and r["family"] == fam)


def test_the_birth_record_that_names_both_parents_is_the_top_level():
    r = _row(_d("Bautismo de Lucía, hija de Mateo Ortega y Clara Sanz"))
    assert r["level"] == LEVELS[0] and r["flags"] == []


def test_one_parent_named_then_neither_then_no_citation():
    assert _row(_d("hija de Mateo Ortega"))["level"] == LEVELS[2]
    assert _row(_d("folio 1, sin más"))["level"] == LEVELS[3]
    assert _row(_d("folio 1"), child="I0008")["level"] == LEVELS[4]  # I0008 has no citation anywhere


def test_words_of_fewer_than_three_letters_never_count_as_a_name():
    assert _words("Ana de la Ortega y Sanz") == ["ana", "ortega", "sanz"]


def test_a_name_split_across_sentences_is_not_a_name():
    assert _row(_d("hija de Mateo. Ortega vive en otra casa"))["level"] == LEVELS[3]


def test_a_parent_named_in_another_record_is_the_second_level():
    d = _d("folio 1")
    selftest._get(d, "families", "F0001")["citation_list"] = ["hC0002"]
    selftest._get(d, "citations", "C0002")["page"] = "Matrimonio de Mateo Ortega y Clara Sanz; hijos Lucía y Tomás"
    assert _row(d)["level"] == LEVELS[1]


def test_a_denial_word_in_the_sentence_of_a_name_is_flagged_not_hidden():
    r = _row(_d("Lucía no es hija de Mateo Ortega y Clara Sanz"))
    assert r["level"] == LEVELS[0]
    assert any("denial word" in f for f in r["flags"])


def test_a_second_person_with_the_parents_name_is_flagged():
    d = _d("hija de Mateo Ortega y Clara Sanz")
    twin = copy.deepcopy(selftest._get(d, "people", "I0002"))
    twin["gramps_id"], twin["handle"] = "I0099", "hI0099"
    twin["family_list"], twin["parent_family_list"], twin["event_ref_list"] = [], [], []
    d["people"].append(twin)
    assert any("another person in the tree bears the father's name" in f for f in _row(d)["flags"])


def test_a_relation_not_by_birth_is_flagged_and_never_raised():
    d = _d("hija de Mateo Ortega y Clara Sanz")
    selftest._get(d, "families", "F0001")["child_ref_list"][0]["frel"] = "Adopted"
    r = _row(d)
    assert any("not by birth" in f for f in r["flags"])


def test_a_child_in_two_families_has_a_row_for_each():
    d = _d("hija de Mateo Ortega y Clara Sanz")
    fam = copy.deepcopy(selftest._get(d, "families", "F0002"))
    fam["gramps_id"], fam["handle"] = "F0009", "hF0009"
    fam["child_ref_list"] = [{"ref": "hI0001", "frel": "Birth", "mrel": "Birth", "private": False, "citation_list": [], "note_list": []}]
    d["families"].append(fam)
    selftest._get(d, "people", "I0001")["parent_family_list"].append("hF0009")
    rows = [r for r in link_report(Tree(d))["rows"] if r["child"] == "I0001"]
    assert sorted(r["family"] for r in rows) == ["F0001", "F0009"]
    assert any("more than one parental family" in f for r in rows for f in r["flags"])


def test_the_command_writes_nothing_and_never_sets_the_exit_code(tmp_path, capsys):
    d = _d("folio 1")
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"tree": "x", "dumped_at": "", "objects": d}))
    assert main(["links", str(p)]) == 0
    out = capsys.readouterr().out
    assert "parent links" in out and "not proof" in out
    assert main(["links", str(p), "--json"]) == 0


def test_without_a_dump_the_command_reads_the_live_tree(live, capsys):
    live(selftest.clean_tree())
    assert main(["links"]) == 0
    assert "parent links" in capsys.readouterr().out
