import copy
import json

import pytest

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


@pytest.mark.parametrize("etype,page", [
    ("Marriage", "Bodas, libro 2"), ("Marriage", "Velaciones de 1801"), ("Marriage", "Desposorios de 1790"),
    ("Marriage", "Processetti matrimoniali 1850"), ("Marriage", "Matrimoni 1850"),
    ("Death", "Sepultura de Tomás"), ("Death", "Esquela en el diario"), ("Death", "Atti di morte 1890"),
    ("Death", "Sepolture 1890"), ("Birth", "Atti di nascita 1890"), ("Birth", "Battesimi 1890"),
    ("Birth", "Bautizo de Lucía")])
def test_a_page_that_names_the_events_own_kind_is_not_another_act(etype, page):
    other = "acta de nacimiento" if etype == "Death" else "acta de defuncion"
    assert not _rows(_tree(page=f"{page}; {other}", etype=etype), "other-act")


def test_an_event_of_no_act_kind_is_never_another_act():
    # a residence or occupation cited by a birth act is that act's ordinary use, not a mismatch
    assert not _rows(_tree(page="Acta de nacimiento n°3", etype="Residence"))


def test_a_baptism_and_a_burial_are_read_as_birth_and_death_acts():
    assert not _rows(_tree(page="Partida de bautismo", etype="Baptism"))
    assert _rows(_tree(page="acte de mariage", etype="Baptism"), "other-act")
    assert not _rows(_tree(page="Libro de entierros", etype="Burial"))
    assert _rows(_tree(page="acte de mariage", etype="Burial"), "other-act")


def test_an_act_word_inside_a_longer_word_is_not_that_act():
    assert not _rows(_tree(page="Colegio del Renacimiento, folio 2", etype="Death"))


def test_a_birth_cited_by_a_marginal_mention_is_not_flagged():
    assert not _rows(_tree(page="Mención marginal n°2", etype="Birth"))


def test_a_citation_missing_from_the_dump_is_skipped():
    d = _tree(page="acte de mariage")
    selftest._get(d, "events", "E0001")["citation_list"] = ["hC0001", "hGONE"]
    assert [r["citation"] for r in _rows(d)] == ["C0001"]


def test_the_command_refuses_a_gramps_backup(tmp_path, capsys):
    from test_check import _to_xml
    backup = tmp_path / "t.gramps"
    backup.write_bytes(_to_xml(selftest.clean_tree()))
    assert main(["cites", str(backup)]) == 2
    assert "has no citation pages" in capsys.readouterr().err


def test_fill_places_text_output_counts_the_held_back(tmp_path, capsys):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"tree": "t", "objects": _tree(page="acte de mariage, Villaejemplo", place=False)}))
    assert main(["fill-places", str(p)]) == 0
    assert "held back, not proposed: 1" in capsys.readouterr().out
