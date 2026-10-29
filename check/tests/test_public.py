import copy
import json

import pytest

from gwt_check import selftest
from gwt_check.cli import main
from gwt_check.public import public_report
from gwt_check.tree import BadInput, Tree

YEAR = 2026


def _pair(mark=("I0002",), keep=False):
    """The clean tree as the owner reads it, and as a guest does: the objects marked private are gone."""
    owner = copy.deepcopy(selftest.clean_tree())
    gone = set()
    for t in owner:
        for o in owner[t]:
            if o["gramps_id"] in mark:
                o["private"] = True
                gone.add((t, o["handle"]))
    guest = copy.deepcopy(owner)
    if not keep:
        for t in guest:
            guest[t] = [_scrub(o, {h for _, h in gone}) for o in guest[t] if (t, o["handle"]) not in gone]
    return owner, guest


def _scrub(x, handles):
    """What the server does for a guest: no reference to an object the guest cannot see."""
    if isinstance(x, dict):
        return {k: ("" if isinstance(v, str) and v in handles else _scrub(v, handles)) for k, v in x.items()}
    if isinstance(x, list):
        return [_scrub(v, handles) for v in x
                if not (isinstance(v, str) and v in handles) and not (isinstance(v, dict) and v.get("ref") in handles)]
    return x


def _rep(owner, guest):
    return public_report(Tree(owner, source="owner"), Tree(guest, source="guest"), this_year=YEAR)


def _kinds(rep):
    return {r["check"] for r in rep["rows"]}


def test_a_guest_who_sees_no_private_object_leaks_nothing_of_that_kind():
    rep = _rep(*_pair())
    assert not _kinds(rep) & {"private-visible", "private-reference", "private-name"}


def test_a_private_object_the_guest_still_gets_is_reported():
    rep = _rep(*_pair(keep=True))
    assert {"check": "private-visible", "object": "I0002", "type": "people"} in rep["rows"]


def test_a_visible_object_that_points_at_a_private_one_is_reported():
    owner, guest = _pair()
    fam = guest["families"][0]
    fam["child_ref_list"].append({"ref": "hI0002", "private": False})
    rep = _rep(owner, guest)
    assert any(r["check"] == "private-reference" and r["points_at"] == "I0002" for r in rep["rows"])


def test_a_private_persons_name_in_a_visible_note_is_reported_without_the_text():
    owner, guest = _pair()
    name = " ".join([owner["people"][1]["primary_name"]["first_name"]]
                    + [s["surname"] for s in owner["people"][1]["primary_name"]["surname_list"]])
    for n in guest["notes"]:
        n["text"]["string"] = f"Visita de {name} en 1890."
    rep = _rep(owner, guest)
    rows = [r for r in rep["rows"] if r["check"] == "private-name"]
    assert rows and all(r["person"] == "I0002" for r in rows)
    assert name not in json.dumps(rep, ensure_ascii=False)


def test_a_name_a_visible_person_also_has_is_not_reported():
    owner, guest = _pair()
    twin = copy.deepcopy(owner["people"][1])
    twin.update(handle="hI9999", gramps_id="I9999", private=False)
    guest["people"].append(twin)
    owner["people"].append(twin)
    name = " ".join([twin["primary_name"]["first_name"]] + [s["surname"] for s in twin["primary_name"]["surname_list"]])
    guest["notes"][0]["text"]["string"] = name
    assert not [r for r in _rep(owner, guest)["rows"] if r["check"] == "private-name"]


def test_a_person_with_no_death_and_a_recent_birth_is_reported_as_possibly_living():
    owner, guest = _pair()
    p = guest["people"][0]
    ev = {e["handle"]: e for e in guest["events"]}
    for r in p["event_ref_list"]:
        ev[r["ref"]]["type"] = "Birth"
        ev[r["ref"]]["date"]["dateval"] = [1, 1, 1990, False]
    rep = _rep(owner, guest)
    assert any(r["check"] == "living-visible" and r["object"] == p["gramps_id"] for r in rep["rows"])


def test_a_guest_dump_with_objects_the_owner_lacks_is_refused():
    owner, guest = _pair()
    extra = copy.deepcopy(guest["people"][0])
    extra.update(handle="hZ", gramps_id="I7777")
    guest["people"].append(extra)
    with pytest.raises(BadInput):
        _rep(owner, guest)


def _write(tmp_path, name, d):
    p = tmp_path / name
    p.write_text(json.dumps({"objects": d}))
    return str(p)


def test_the_command_exits_1_on_a_leak_and_0_on_none(tmp_path, capsys):
    owner, guest = _pair(keep=True)
    o, g = _write(tmp_path, "o.json", owner), _write(tmp_path, "g.json", guest)
    assert main(["public", o, g]) == 1
    assert "private-visible" in capsys.readouterr().out
    owner, guest = _pair()
    clean_o, clean_g = _write(tmp_path, "o2.json", owner), _write(tmp_path, "g2.json", guest)
    rc = main(["public", clean_o, clean_g, "--json"])
    rows = json.loads(capsys.readouterr().out)["rows"]
    assert rc == (1 if rows else 0)
    assert not [r for r in rows if r["check"] != "living-visible"]
