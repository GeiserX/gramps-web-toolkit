import copy
import json

from gwt_check import selftest
from gwt_check.cites import _name_set, _namesake, _subject, _words, cites_report
from gwt_check.cli import main
from gwt_check.fill import propose_places
from gwt_check.tree import Tree
from test_check import live  # noqa: F401  (the fixture that serves a fake live tree)


def _tree(page="folio 1", value=None, event="E0001", etype=None, description=None, date=None, place=True):
    """The clean tree with citation C0001 given a page and a «Valor afirmado» note, cited by `event`."""
    d = copy.deepcopy(selftest.clean_tree())
    c = selftest._get(d, "citations", "C0001")
    c["page"] = page
    n = selftest._get(d, "notes", "N0002")
    n["text"]["string"] = f"Valor afirmado: {value} Extracto: x" if value else "sin valor"
    ev = selftest._get(d, "events", event)
    ev["citation_list"] = ["hC0001"]
    if etype:
        ev["type"] = etype
    if description is not None:
        ev["description"] = description
    if date is not None:
        ev["date"] = date
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


def test_the_act_is_about_the_child_and_the_person_is_only_the_father():
    d = _tree(value="Lucía Ortega nació en Villaejemplo el 08/01/1900, hija de Mateo Ortega y Clara Sanz")
    assert not _rows(d)  # the owner of E0001 is Lucía
    d = _tree(value="Tomás Ortega nació en Villaejemplo el 08/01/1903, hijo de Lucía Ortega y Clara Sanz")
    assert _rows(d, "other-subject")


def test_a_person_named_only_in_another_place_in_the_text_is_not_a_relation():
    # Lucía is not named at all: nothing says the act is about her relation to someone, so no flag
    assert not _rows(_tree(value="Tomás Ortega nació en Villaejemplo el 08/01/1903"))


def test_a_value_that_opens_with_the_record_is_not_read_for_a_subject():
    assert not _rows(_tree(value="Acta de nacimiento: comparece Mateo Ortega; nace Lucía Ortega"))


def test_an_act_that_opens_with_the_record_names_the_person_only_as_a_parent():
    v = "Acta de nacimiento: nace Tomás Gil, hijo de Mateo Ortega y de Clara Sanz"
    assert not _rows(_tree(value=v))  # one of the owner's two words follows «hijo de», the other is nowhere
    d = _tree(value="Acta de nacimiento: nace Tomás Gil, hijo de Lucía Ortega y de Clara Sanz")
    assert _rows(d, "other-subject")
    d = _tree(value="Acta de nacimiento: nace Lucía Ortega, hija de Mateo Ortega y de Clara Sanz")
    assert not _rows(d)
    d = _tree(value="Acta de defunción: fallece Lucía Ortega, hija de la difunta Lucía Ortega")  # named before too
    assert not _rows(d, "other-subject") and _rows(d, "namesake")


def _dupont(value):
    d = _tree(value=value)
    selftest._get(d, "people", "I0001")["primary_name"]["surname_list"][0]["surname"] = "Dupont"
    return d


def test_a_surname_written_as_two_words_is_the_same_name():
    assert not _rows(_dupont("Lucía Du Pont nació en Villaejemplo el 08/01/1900, hija de Mateo Gil"), "other-subject")
    assert _rows(_dupont("Acta de nacimiento: nace Tomás Gil, hijo de Lucía Du Pont y de Clara Sanz"), "other-subject")
    assert not _rows(_dupont("Lucía Perez Le nació en Villaejemplo, hija de Mateo Gil"), "other-subject")


def _reg(value, date):
    return _rows(_tree(value=value, date=selftest._date(*date)), "registration-date")


def test_a_birth_dated_with_the_day_the_act_was_written_is_flagged():
    v = "Lucía Ortega n. 17/09/1900 en Villaejemplo; acta del 18/09/1900"
    assert _reg(v, (1900, 9, 18))
    assert not _reg(v, (1900, 9, 17))


def test_the_birth_day_after_the_act_number_is_not_the_registration_day():
    v = "acta n°7/1900 (Villaejemplo): Lucía Ortega, nacida 18/09/1900 a las 18h; nota marginal: matrimonio 16/07/1926"
    assert not _reg(v, (1900, 9, 18))
    assert _reg("Lucía Ortega n. 17/09/1900; acta n°7 del 18/09/1900", (1900, 9, 18))
    assert _reg("Lucía Ortega n. 17/09/1900; registrada el 18/09/1900", (1900, 9, 18))


def test_a_claim_with_one_date_is_not_read_for_a_registration_day():
    assert not _reg("Lucía Ortega nació en Villaejemplo; acta del 18/09/1900", (1900, 9, 18))


def test_a_date_with_a_modifier_is_not_read_for_a_registration_day():
    v = "Lucía Ortega n. 17/09/1900 en Villaejemplo; acta del 18/09/1900"
    d = _tree(value=v, date=dict(selftest._date(1900, 9, 18), modifier=1))
    assert not _rows(d, "registration-date")


def test_a_doubtful_reading_marker_does_not_end_the_subject():
    d = _tree(value="Lucía Mateo(?) Ortega Sanz, n. 08/01/1900 en Villaejemplo, hijo legítimo de Tomás Ortega Sanz")
    selftest._get(d, "people", "I0001")["primary_name"]["first_name"] = "Lucía Mateo(?)"
    assert not _rows(d, "other-subject")


def test_a_bracket_after_the_subject_that_holds_the_persons_name_is_the_same_person():
    d = _tree(value="Ortega Luci (Lucía Ortega, I0001) fallecio el 31/03/1924 en Villaejemplo, hija de Mateo Gil")
    assert not _rows(d, "other-subject")
    d = _tree(value="Tomás Gil (Gil Tomás, I0008) nació en Villaejemplo, hijo de Lucía Ortega y de Clara Sanz")
    assert _rows(d, "other-subject")


def test_name_words_join_a_short_prefix_to_the_next_word_and_nothing_else():
    assert _name_set("Ana de la Ortega y Sanz") == {"ana", "ortega", "sanz"}
    assert _name_set("Lucía Du Pont") == {"lucia", "dupont", "pont"}
    assert _name_set("Mateo y Sanz") == {"mateo", "sanz"}


def test_a_namesake_needs_the_name_on_both_sides_of_the_relation():
    assert _namesake("nace lucia ortega, hija de lucia ortega", ["lucia", "ortega"])
    assert not _namesake("nace tomas gil, hijo de lucia ortega", ["lucia", "ortega"])
    assert not _namesake("nace lucia ortega, sin mas", ["lucia", "ortega"])


def test_an_act_number_without_the_word_act_is_not_a_registration_day():
    assert not _reg("Lucía Ortega n. 17/09/1900; n°7 del 18/09/1900", (1900, 9, 18))


def test_subject_stops_at_the_first_word_that_ends_a_name():
    assert _subject("tomas ortega nacio en villaejemplo") == "tomas ortega"
    assert _subject("tomas ortega, esposa de lucia") == "tomas ortega"


def test_words_of_fewer_than_three_letters_and_particles_never_count_as_a_name():
    assert _words("Ana de la Ortega y Sanz") == ["ana", "ortega", "sanz"]


def test_a_plain_date_on_an_event_that_says_it_has_no_date_of_its_own_is_flagged_a_before_date_is_not():
    plain = selftest._date(1832, 8, 9)
    assert _rows(_tree(description="Declarado fallecido; sin fecha propia", date=plain), "date-not-its-own")
    before = dict(plain, modifier=1)
    assert not _rows(_tree(description="Declarado fallecido; sin fecha propia", date=before))


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
