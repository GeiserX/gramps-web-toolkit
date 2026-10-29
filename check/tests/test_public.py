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
    for d in (owner, guest):
        p = d["people"][0]
        ev = {e["handle"]: e for e in d["events"]}
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
    assert rc == 0
    assert not [r for r in rows if r["check"] != "living-visible"]


def _set_own(d, gid, kinds):
    """Give person `gid` own events of these (type, year) kinds, replacing theirs."""
    p = next(o for o in d["people"] if o["gramps_id"] == gid)
    tpl = copy.deepcopy(d["events"][0])
    p["event_ref_list"] = []
    for i, (typ, year) in enumerate(kinds):
        ev = copy.deepcopy(tpl)
        ev.update(handle=f"hX{gid}{i}", gramps_id=f"EX{gid}{i}", type=typ, private=False)
        ev["date"] = {"dateval": [1, 1, year, False]} if year else {"dateval": [0, 0, 0, False]}
        d["events"].append(ev)
        p["event_ref_list"].append({"ref": ev["handle"], "role": "Primary", "private": False})
    return p


def _living(rep):
    return {r["object"] for r in rep["rows"] if r["check"] == "living-visible"}


def test_a_person_with_no_dated_birth_is_not_reported_as_living():
    owner, guest = _pair()
    for d in (owner, guest):
        _set_own(d, "I0001", [("Birth", 0)])
    assert "I0001" not in _living(_rep(owner, guest))


def test_living_is_judged_from_the_owners_events_a_private_burial_still_counts():
    owner, guest = _pair()
    _set_own(owner, "I0001", [("Birth", 1990), ("Burial", 1991)])
    _set_own(guest, "I0001", [("Birth", 1990)])  # the burial is private, the guest does not get it
    assert "I0001" not in _living(_rep(owner, guest))


def test_possibly_living_alone_does_not_set_the_exit_code(tmp_path, capsys):
    owner, guest = _pair()
    for d in (owner, guest):
        _set_own(d, "I0001", [("Birth", 1990)])
    rep = _rep(owner, guest)
    assert "I0001" in _living(rep) and rep["leaks"] == 0
    o, g = _write(tmp_path, "o.json", owner), _write(tmp_path, "g.json", guest)
    assert main(["public", o, g]) == 0
    assert "living-visible" in capsys.readouterr().out


def test_a_private_sub_object_the_guest_still_gets_is_reported():
    owner, guest = _pair()
    for d in (owner, guest):
        d["people"][0]["primary_name"]["private"] = True
        d["people"][0]["event_ref_list"][0]["private"] = True
    rows = [r for r in _rep(owner, guest)["rows"] if r["check"] == "private-visible"]
    assert {(r["object"], r.get("path")) for r in rows} == {("I0001", "primary_name"), ("I0001", "event_ref_list")}


def test_a_visible_private_object_is_not_also_a_reference_to_itself():
    rep = _rep(*_pair(keep=True))
    assert not [r for r in rep["rows"] if r["check"] == "private-reference" and r["object"] == r["points_at"]]


def test_a_private_name_before_punctuation_is_reported_and_its_first_word_alone_is_not():
    owner, guest = _pair()
    pn = owner["people"][1]["primary_name"]
    name = " ".join([pn["first_name"]] + [s["surname"] for s in pn["surname_list"]])
    guest["notes"][0]["text"]["string"] = f"Visita de {name}."
    guest["notes"][1]["text"]["string"] = f"{name}, hijo"
    guest["citations"][0]["page"] = f"({name})"
    guest["events"][0]["description"] = f"{name}:"
    guest["sources"][0]["title"] = f"Libro de {pn['first_name']} el viejo"
    hits = {(r["type"], r["object"]) for r in _rep(owner, guest)["rows"] if r["check"] == "private-name"}
    assert hits == {("notes", guest["notes"][0]["gramps_id"]), ("notes", guest["notes"][1]["gramps_id"]),
                    ("citations", guest["citations"][0]["gramps_id"]), ("events", guest["events"][0]["gramps_id"])}


@pytest.mark.parametrize("first, surnames", [("Ana", ["Gil"]), ("Bartolomeo", [])])
def test_a_short_or_one_word_private_name_is_not_reported(first, surnames):
    owner, guest = _pair()
    p = owner["people"][1]["primary_name"]
    p["first_name"], p["surname_list"] = first, [{"surname": s} for s in surnames]
    for n in guest["notes"]:
        n["text"]["string"] = f"{first} {' '.join(surnames)} y {first}"
    assert not [r for r in _rep(owner, guest)["rows"] if r["check"] == "private-name"]


def test_a_guest_dump_of_another_tree_or_an_empty_one_is_refused():
    owner, guest = _pair()
    o, g = Tree(owner, source="owner"), Tree(guest, source="guest")
    o.tree_id, g.tree_id = "tree-a", "tree-b"
    with pytest.raises(BadInput):
        public_report(o, g, this_year=YEAR)
    with pytest.raises(BadInput):
        _rep(owner, {t: [] for t in guest})


def test_rows_follow_numeric_gramps_id_order():
    owner, guest = _pair()
    for i, gid in enumerate(("I10000", "I9001")):
        extra = copy.deepcopy(owner["people"][0])
        extra.update(handle=f"hP{i}", gramps_id=gid, private=True)
        owner["people"].append(extra)
        guest["people"].append(copy.deepcopy(extra))
    rows = [r["object"] for r in _rep(owner, guest)["rows"] if r["check"] == "private-visible"]
    assert rows.index("I9001") < rows.index("I10000")


def test_a_gramps_backup_is_refused_and_limit_cuts_the_list(tmp_path, capsys):
    from test_check import _to_xml

    owner, guest = _pair(keep=True)
    b = tmp_path / "t.gramps"
    b.write_bytes(_to_xml(owner))
    assert main(["public", _write(tmp_path, "o0.json", owner), str(b)]) == 2  # as the guest side
    assert "a .gramps backup has no private marks" in capsys.readouterr().err
    for d in (owner, guest):
        d["people"][0]["primary_name"]["private"] = True
    assert main(["public", _write(tmp_path, "o.json", owner), _write(tmp_path, "g2.json", guest), "--limit", "1"]) == 1
    out = capsys.readouterr().out
    assert "more (--limit 0 for all)" in out
    assert len([ln for ln in out.splitlines() if ln.startswith("  private-")]) == 1
