import copy
import json

from gwt_check import selftest
from gwt_check.cites import cites_report
from gwt_check.cli import main
from gwt_check.fill import propose_places
from gwt_check.tree import Tree
from test_check import live  # noqa: F401  (the fixture that serves a fake live tree)


def _tree(page="folio 1", event="E0001", etype=None, place=True):
    """The clean tree with citation C0001 given a page, cited by `event`."""
    d = copy.deepcopy(selftest.clean_tree())
    c = selftest._get(d, "citations", "C0001")
    c["page"] = page
    ev = selftest._get(d, "events", event)
    ev["citation_list"] = ["hC0001"]
    if etype:
        ev["type"] = etype
    if not place:
        ev["place"] = ""
    return d


def _rows(d, rule=None):
    return [r for r in cites_report(Tree(d))["rows"] if r["event"] == "E0001" and (rule is None or r["rule"] == rule)]


def test_a_birth_cited_by_a_marriage_act_is_flagged():
    assert _rows(_tree(page="acte de mariage de Mateo Ortega y Clara Sanz"), "other-act")


def test_a_birth_cited_by_a_birth_or_baptism_act_is_not():
    assert not _rows(_tree(page="Acta de nacimiento n°3"))
    assert not _rows(_tree(page="Partida de bautismo, folio 4"))


def test_a_citation_that_names_no_kind_of_act_is_not_flagged():
    assert not _rows(_tree(page="folio 1"))


def test_a_death_cited_by_a_marginal_mention_in_a_birth_act_is_flagged():
    d = _tree(page="Mención marginal n°2 del acta de nacimiento n°8 | C1 | Defunción", etype="Death")
    assert _rows(d, "marginal-mention")


def test_fill_places_holds_back_a_place_that_only_a_flagged_citation_names():
    d = _tree(page="acte de mariage de Mateo Ortega y Clara Sanz, Villaejemplo", place=False)
    rep = propose_places(Tree(d))
    assert [x["event"] for x in rep["held_back"]] == ["E0001"] and not rep["proposals"]
    assert "marriage act" in rep["held_back"][0]["why"]
    d = _tree(page="Acta de nacimiento, Villaejemplo", place=False)
    rep = propose_places(Tree(d))
    assert [x["event"] for x in rep["proposals"]] == ["E0001"] and not rep["held_back"]


def test_a_place_named_by_a_clean_citation_is_still_proposed_when_another_citation_is_flagged():
    d = _tree(page="acte de mariage, Villaejemplo", place=False)
    ev = selftest._get(d, "events", "E0001")
    ev["citation_list"] = ["hC0001", "hC0002"]
    selftest._get(d, "citations", "C0002")["page"] = "Acta de nacimiento, Villaejemplo"
    rep = propose_places(Tree(d))
    assert [x["event"] for x in rep["proposals"]] == ["E0001"] and "mariage" not in rep["proposals"][0]["quote"]
    assert not rep["held_back"]


def test_the_command_writes_nothing_and_never_sets_the_exit_code(tmp_path, capsys):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"tree": "t", "objects": _tree(page="acte de mariage de X")}))
    assert main(["cites", str(p)]) == 0
    assert "other-act" in capsys.readouterr().out


def test_without_a_dump_the_command_reads_the_live_tree(live, capsys):
    live(_tree(page="acte de mariage de X"))
    assert main(["cites"]) == 0
    assert "other-act" in capsys.readouterr().out


def test_a_word_that_only_starts_with_an_act_word_is_not_that_act():
    assert not _rows(_tree(page="1881 census, birthplace column", etype="Death"))
    assert _rows(_tree(page="Registro de nacimientos, folio 3", etype="Death"), "other-act")
