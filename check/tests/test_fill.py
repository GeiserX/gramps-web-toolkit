import copy
import json
import time

from gwt_check import selftest
from gwt_check.cli import main
from gwt_check.fill import propose_places
from gwt_check.tree import Tree


def _tree(d):
    return Tree(copy.deepcopy(d))


def _placeless(d, page, gid="E0003"):
    """E0003 (a birth) loses its place and cites C0002, whose page is `page`."""
    ev = selftest._get(d, "events", gid)
    ev["place"] = ""
    ev["citation_list"] = ["hC0002"]
    selftest._get(d, "citations", "C0002")["page"] = page
    return d


def _place(d, gid, name, inside=None, used=True):
    d["places"].append(selftest._base(gid, name={"value": name}, place_type="Village", lat="", long="",
                                      placeref_list=[{"ref": "h" + inside}] if inside else [],
                                      citation_list=[], media_list=[], note_list=[], alt_names=[], urls=[]))
    if used:  # only places some event uses are candidates
        selftest._get(d, "events", "E0004")["place"] = "h" + gid


def test_one_place_named_is_proposed_with_its_quote():
    d = _placeless(selftest.clean_tree(), "Bautismos, folio 3, bautizado en Villaejemplo el día 2")
    rep = propose_places(_tree(d))
    assert rep["events_without_place"] == 1
    assert rep["ambiguous"] == []
    [p] = rep["proposals"]
    assert (p["event"], p["kind"], p["place"], p["name"]) == ("E0003", "birth", "P0001", "Villaejemplo")
    assert "bautizado en Villaejemplo" in p["quote"]


def test_a_town_and_the_province_that_encloses_it_propose_the_town():
    # on 0e28bac this came out as ambiguous: both names matched and neither was dropped
    d = _placeless(selftest.clean_tree(), "Parroquia de San Roque, Villaejemplo, Provincia Imaginaria")
    selftest._get(d, "events", "E0004")["place"] = "hP0002"  # the province is a candidate too
    rep = propose_places(_tree(d))
    assert [(p["event"], p["place"]) for p in rep["proposals"]] == [("E0003", "P0001")]
    assert rep["ambiguous"] == []


def test_a_place_no_event_uses_is_not_a_candidate():
    # the province of the clean tree only encloses the town: naming it alone proposes nothing
    d = _placeless(selftest.clean_tree(), "Censo de la Provincia Imaginaria")
    assert propose_places(_tree(d))["proposals"] == []


def test_case_accents_and_spacing_are_ignored():
    d = selftest.clean_tree()
    _place(d, "P0050", "Valdeníspero de Arriba")
    _placeless(d, "nacido en VALDENISPERO  de\narriba")
    [p] = propose_places(_tree(d))["proposals"]
    assert p["place"] == "P0050"


def test_a_name_inside_a_longer_word_does_not_match():
    d = _placeless(selftest.clean_tree(), "Libro de Villaejemplonuevo")
    assert propose_places(_tree(d))["proposals"] == []


def test_two_unrelated_places_named_are_ambiguous_and_listed_by_id():
    d = selftest.clean_tree()
    _place(d, "P0050", "Villaejemplo", inside=None)  # a namesake town elsewhere, not inside P0001
    _placeless(d, "nacido en Villaejemplo")
    rep = propose_places(_tree(d))
    assert rep["proposals"] == []
    assert rep["ambiguous"] == [{"event": "E0003", "kind": "birth",
                                 "places": ["P0001 Villaejemplo", "P0050 Villaejemplo"]}]


def test_many_places_stay_fast():
    # on 0e28bac one regex per place and event overflowed Python's 512-pattern cache: 700 places and
    # 3000 events took about two minutes
    d = selftest.clean_tree()
    for i in range(700):
        _place(d, f"P{1000 + i}", f"Lugarinventado{i:04d}", used=False)
        d["events"].append(selftest._base(f"E{5000 + i}", type="Birth", date=selftest._date(1900), place=f"hP{1000 + i}",
                                          description="", attribute_list=[], media_list=[], note_list=[],
                                          citation_list=[]))
    for i in range(3000):
        d["citations"].append(selftest._base(f"C{5000 + i}", source_handle="hS0001", page=f"acta {i} de Lugarinventado{i % 700:04d}",
                                             confidence=2, date=selftest._date(), attribute_list=[], media_list=[],
                                             note_list=[]))
        d["events"].append(selftest._base(f"E{9000 + i}", type="Death", date=selftest._date(1950), place="",
                                          description="", attribute_list=[], media_list=[], note_list=[],
                                          citation_list=[f"hC{5000 + i}"]))
    t = _tree(d)
    start = time.monotonic()
    rep = propose_places(t)
    assert time.monotonic() - start < 15
    assert len(rep["proposals"]) == 3000


def test_cli_prints_and_writes_ops_with_an_empty_ruling(tmp_path, capsys):
    d = _placeless(selftest.clean_tree(), "bautizado en Villaejemplo")
    dump = tmp_path / "t.json"
    dump.write_text(json.dumps(d), encoding="utf-8")
    ops = tmp_path / "ops.jsonl"
    assert main(["fill-places", str(dump), "--ops", str(ops)]) == 0
    out = capsys.readouterr().out
    assert "E0003 (birth): P0001 Villaejemplo" in out
    assert [json.loads(x) for x in ops.read_text().splitlines()] == [
        {"op": "update_event", "key": "fill-place-E0003", "ruling": "", "event": "E0003", "set": {"place": "P0001"}}]
    assert main(["fill-places", str(dump), "--ops", str(ops)]) == 2  # an existing file is never replaced
    assert main(["fill-places", str(dump), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["proposals"][0]["place"] == "P0001"
