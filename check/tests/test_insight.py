import json

import pytest

from gwt_check.cli import main
from gwt_check.insight import discrepancies_report, citations_report, fill_report, frontier_report, insight, parse_citation, witnesses_report
from gwt_check.tree import Tree


def date(y, m=0, d=0):
    return {"dateval": [d, m, y, False], "modifier": 0, "quality": 0, "calendar": 0, "newyear": 0, "text": ""}


def note(h, gid, text):
    return {"handle": h, "gramps_id": gid, "text": {"string": text}}


def base():
    return {
        "people": [], "families": [], "events": [], "citations": [], "sources": [{"handle": "s1", "gramps_id": "S1"}],
        "media": [], "notes": [], "repositories": [], "tags": [],
        "places": [{"handle": "pl1", "gramps_id": "L1", "name": {"value": "Lowtown"}},
                   {"handle": "pl2", "gramps_id": "L2", "name": {"value": "Highfield"}}],
    }


def cit(h, gid, page, notes=(), source="s1"):
    return {"handle": h, "gramps_id": gid, "page": page, "note_list": list(notes), "source_handle": source}


def event(h, gid, typ, dt=None, place="", cites=()):
    return {"handle": h, "gramps_id": gid, "type": typ, "date": dt or {}, "place": place, "citation_list": list(cites), "note_list": []}


def person(h, gid, events=(), parents=(), cites=(), notes=(), refs=()):
    return {"handle": h, "gramps_id": gid, "event_ref_list": [{"ref": e} for e in events], "parent_family_list": list(parents),
            "citation_list": list(cites), "note_list": list(notes), "person_ref_list": list(refs), "birth_ref_index": 0 if events else -1}


def test_parse_citation_reads_what_the_text_states_and_nothing_else():
    p = parse_citation("Acte n°12/1851, vue 84-85, registry https://example.org/a/1.")
    assert p == {"act": 12, "year": 1851, "act_year": True, "image": "84-85", "url": "https://example.org/a/1"}
    assert parse_citation("page 3, register 1880-1890") == {"image": "3", "year": 1880}
    assert parse_citation("a loose remark") == {}


def test_citations_count_structure_and_find_acts_behind_several_claims():
    d = base()
    d["citations"] = [cit("c1", "C1", "Acte n°7/1850, vue 10 | Nacimiento"), cit("c2", "C2", "acta n°7/1850, imagen 10"),
                      cit("c3", "C3", "free text")]
    r = citations_report(Tree(d))
    assert (r["total"], r["with_act_number"], r["with_image"], r["with_own_date"]) == (3, 2, 2, 0)
    assert r["same_act_in_several_citations"] == [{"citations": ["C1", "C2"], "act": 7, "year": 1850, "source": "S1"}]


def test_year_outside_event_only_compares_the_same_kind_of_act():
    d = base()
    d["citations"] = [cit("c1", "C1", "Acte n°7/1907 | Nacimiento"), cit("c2", "C2", "Acte n°8/1907 | Matrimonio"),
                      cit("c3", "C3", "Margen del acta de nacimiento n°9/1907 | Nacimiento"), cit("c4", "C4", "Acte n°10/1871 | Nacimiento")]
    d["events"] = [event("e1", "E1", "Birth", date(1871), cites=["c1", "c2", "c3", "c4"])]
    odd = citations_report(Tree(d))["year_outside_event"]
    assert [(o["citation"], o["text_year"]) for o in odd] == [("C1", 1907)]  # the matrimony and the margin note are not birth acts


def test_discrepancies_list_the_owner_the_citations_and_what_is_settled():
    d = base()
    d["notes"] = [note("n1", "N1", "DISCREPANCIA: DNI 1923, libro 1922 (C000999)"), note("n2", "N2", "Conflict was resolved"), note("n3", "N3", "plain"), note("n4", "N4", "date differs by a year; se conserva la fecha civil")]
    d["citations"] = [cit("c1", "C1", "p", notes=["n1", "n2", "n3", "n4"])]
    r = discrepancies_report(Tree(d))
    assert (r["notes_with_a_conflict_marker"], r["open"], r["one reading kept"], r["resolved"]) == (3, 1, 1, 1)
    first = r["items"][0]
    assert first["note"] == "N1" and first["on"] == ["C1"] and first["citations_named"] == ["C000999"] and first["status"] == "open"
    assert [i["status"] for i in r["items"]] == ["open", "one reading kept", "resolved"]


def test_fill_proposes_a_place_only_when_the_citation_names_exactly_one():
    d = base()
    d["notes"] = [note("n1", "N1", "Valor afirmado: acta del 09/07/1807 (Lowtown): nace Ann")]
    d["citations"] = [cit("c1", "C1", "acte n°8", notes=["n1"]), cit("c2", "C2", "Lowtown and Highfield")]
    d["events"] = [event("e1", "E1", "Birth", cites=["c1"]), event("e2", "E2", "Birth", cites=["c2"]), event("e3", "E3", "Death", date(1900), "pl1")]
    r = fill_report(Tree(d))
    assert r["events_without_place"] == 2 and r["events_without_readable_date"] == 2
    one = [p for p in r["place_proposals"] if p["event"] == "E1"][0]
    assert one["place"] == "L1" and "Lowtown" in one["quote"]
    assert [p for p in r["place_proposals"] if p["event"] == "E2"][0]["ambiguous"] == ["Highfield", "Lowtown"]
    assert r["date_proposals"] == [{"event": "E1", "kind": "birth", "date": "1807-07-09", "quote": r["date_proposals"][0]["quote"]}]
    assert "09/07/1807" in r["date_proposals"][0]["quote"]


def test_frontier_orders_by_workability_and_leaves_out_the_possibly_living():
    d = base()
    d["events"] = [event("b1", "E1", "Birth", date(1850), "pl1"), event("b2", "E2", "Birth", date(1970)), event("b3", "E3", "Birth", date(1860), "pl2")]
    d["notes"] = [note("n1", "N1", "negativo: no hallada en 1860")]
    d["people"] = [person("p1", "P1", ["b1"], cites=["c"]), person("p2", "P2", ["b2"]), person("p3", "P3", ["b3"], notes=["n1"]),
                   person("p4", "P4"), person("p5", "P5", ["b1"], parents=["f"])]
    r = frontier_report(Tree(d))
    assert r["skipped_possibly_living"] == 1 and [x["person"] for x in r["ordered"]] == ["P1", "P3", "P4"]
    assert "a note records a negative search" in r["ordered"][1]["why"]
    r2 = frontier_report(Tree(d), {"Lowtown": {"from": 1800, "to": 1900, "online": True}})
    assert r2["ordered"][0]["person"] == "P1" and "records online there" in r2["ordered"][0]["why"]


def test_witnesses_report_recurring_names_without_calling_them_relatives():
    d = base()
    d["notes"] = [note("n1", "N1", "Valor afirmado: boda; testigos Marlow Pierre (56, boulanger) y Tanner Luc (44, maréchal). Oficial Quill Remy."),
                  note("n2", "N2", "Valor afirmado: nacimiento; témoins Marlow Pierre (60 ans, tisserand)")]
    d["citations"] = [cit("c1", "C1", "p", notes=["n1"]), cit("c2", "C2", "p", notes=["n2"])]
    r = witnesses_report(Tree(d))
    assert r["citations_with_witness_names"] == 2
    assert r["names_in_two_or_more_citations"] == [{"name": "marlow pierre", "citations": ["C1", "C2"]}]
    assert "witness is not a relative" in r["note"]


def test_the_command_reads_a_dump_and_writes_nothing(tmp_path, capsys):
    d = base()
    d["citations"] = [cit("c1", "C1", "Acte n°7/1850")]
    path = tmp_path / "dump.json"
    path.write_text(json.dumps({"objects": d}))
    assert main(["insight", str(path), "--only", "citations", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert list(out) == ["citations"] and out["citations"]["total"] == 1
    assert main(["insight", str(path), "--only", "nope"]) == 2
    assert main(["insight", str(path)]) == 0
    assert "== witnesses" in capsys.readouterr().out
    assert set(insight(Tree(d))) == {"citations", "discrepancies", "fill", "frontier", "witnesses"}


def test_frontier_says_when_it_is_partial_and_the_terminal_repeats_every_caveat(tmp_path, capsys):
    d = base()
    d["people"] = [person("p4", "P4")]
    path = tmp_path / "dump.json"
    path.write_text(json.dumps({"objects": d}))
    assert frontier_report(Tree(d))["archives_given"] is False
    assert "PARTIAL" in frontier_report(Tree(d))["note"]
    assert frontier_report(Tree(d), {"Lowtown": {"from": 1, "to": 2, "online": True}})["archives_given"] is True
    assert main(["insight", str(path)]) == 0
    out = capsys.readouterr().out
    for word in ("PARTIAL", "HEURISTIC", "possible mismatches to review", "a witness is not a relative", "proposal quotes"):
        assert word in out


def test_frontier_filter_is_a_birth_year_after_1925_with_no_death_event():
    d = base()
    d["events"] = [event("b1", "E1", "Birth", date(1960)), event("b2", "E2", "Birth", date(1960)), event("d2", "E3", "Death"),
                   event("b3", "E4", "Birth", date(1930)), event("d3", "E5", "Death", date(1990))]
    d["people"] = [person("p1", "P1", ["b1"]), person("p2", "P2", ["b2", "d2"]), person("p3", "P3", ["b3", "d3"]), person("p4", "P4")]
    r = frontier_report(Tree(d))
    # P1: born 1960, no death event: left out. P2: a death event with no date counts as a death event: kept.
    # P3: born 1930 and died: kept. P4: no birth year at all: kept.
    assert sorted(x["person"] for x in r["ordered"]) == ["P2", "P3", "P4"] and r["skipped_possibly_living"] == 1
    assert "no death event" in r["note"] and "no birth year are kept" in r["note"]


def test_a_childs_act_backing_a_parents_event_is_only_a_possible_mismatch():
    d = base()
    d["citations"] = [cit("c1", "C1", "Acte de naissance n°24/1852 | Naissance")]
    d["events"] = [event("e1", "E1", "Birth", date(1807), cites=["c1"])]  # the 1852 act is the child's; it names the parent
    r = citations_report(Tree(d))
    assert [o["citation"] for o in r["year_outside_event"]] == ["C1"]
    assert "possible mismatches to review" in r["note"] and "not errors" in r["note"]


@pytest.mark.parametrize("text", ["31/02/1850", "29/02/1850", "31/04/1850", "00/01/1850", "01/13/1850"])
def test_impossible_calendar_dates_never_proposed(text):
    d = base(); d["citations"] = [cit("c", "C1", text)]
    d["events"] = [event("e", "E1", "Birth", cites=["c"])]
    r = fill_report(Tree(d))
    assert r["date_proposals"] == [] and r["invalid_date_candidates"] == 1

@pytest.mark.parametrize("text,want", [("29/02/1848", "1848-02-29"), ("28/02/1850", "1850-02-28")])
def test_valid_calendar_dates_still_proposed(text, want):
    d = base(); d["citations"] = [cit("c", "C1", text)]
    d["events"] = [event("e", "E1", "Birth", cites=["c"])]
    assert fill_report(Tree(d))["date_proposals"][0]["date"] == want

@pytest.mark.parametrize("text", [
    "Conflict not resolved: still waiting for the original record.",
    "Conflicto no resuelto: esperando el documento original.",
    "Discrepancia sin resolver; se conserva la fecha civil.",
    "Conflict unresolved; preferred reading remains.",
    "Conflict isn't resolved.", "Conflicto aún no zanjado.",
    "Conflict resolved? Not yet.", "Conflicto resuelto anteriormente; pendiente revisar.",
])
def test_negative_or_pending_resolution_stays_open(text):
    d = base(); d["notes"] = [note("n", "N1", text)]
    assert discrepancies_report(Tree(d))["items"][0]["status"] == "open"


@pytest.mark.parametrize("text", [
    "Conflict cannot be resolved without the original record.",
    "Conflict remains to be resolved.",
    "Conflicto debe ser resuelto mediante el acta original.",
    "Conflict will be resolved.", "Conflict must be resolved.",
    "Conflict may be resolved if the original arrives.",
    "Conflict would be resolved with another record.",
    "Conflicto será resuelto.", "Conflicto se resolverá.",
    "Discrepancia podría ser resuelta.", "Conflicto por resolver.",
    "Conflict resolved if the record agrees.",
    "Conflict resolved after we obtain the act.",
    "If the act arrives: Conflict resolved.", "¿Conflicto resuelto?",
    "Conflict: resolved?", "Discrepancia: si llega el acta; resuelta.",
    "Conflict to be resolved; preferred reading retained.",
])
def test_only_positive_completed_closure_not_modal_word_match(text):
    d = base(); d["notes"] = [note("n", "N1", text)]
    assert discrepancies_report(Tree(d))["items"][0]["status"] != "resolved"

@pytest.mark.parametrize("text", [
    "Conflict was resolved.", "Conflict has been resolved.", "Conflict resolved.",
    "Conflicto fue resuelto.", "Discrepancia ha sido resuelta.",
    "Discrepancia: resuelta.", "Conflict: settled.",
])
def test_exact_positive_completed_closure(text):
    d = base(); d["notes"] = [note("n", "N1", text)]
    assert discrepancies_report(Tree(d))["items"][0]["status"] == "resolved"
