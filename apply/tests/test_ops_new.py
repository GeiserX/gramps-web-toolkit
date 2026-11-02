"""The ops that close the gaps writers used to fill with direct API calls: each one through a dry run, an apply,
a --verify and a --revert that puts the tree back as it was. Every name and place here is invented."""

import copy
import gzip
import hashlib
import json

import pytest

from fake_api import FakeGrampsWeb, Resp
from gwt_apply import revert
from gwt_apply.client import NAMESPACE, Client
from gwt_apply.ops import FAMILYSEARCH, SPECS, Applier, ops_table, stable_handle, validate

R = "ruling-0001"


class Fake(FakeGrampsWeb):
    """The fake API plus what these ops reach: the merge endpoints of places, sources and citations (every
    reference to the loser becomes one to the winner, its lists join the winner's, the loser goes), and a file
    replacement on PUT /media/<handle>/file that checks If-Match against the stored MD5, as the server does."""

    DEFAULTS = {"Person": {"alternate_names": [], "urls": []}, "Family": {"father_handle": None, "mother_handle": None},
                "Event": {"date": None, "description": "", "place": ""}, "Place": {"alt_names": [], "placeref_list": [], "urls": []},
                "Citation": {"date": None, "confidence": 2}, "Media": {"desc": "", "date": None},
                "Source": {"reporef_list": []}, "Repository": {"urls": []}, "Note": {}}

    def add(self, ns, obj):
        """Like the server, every field there with its default, so a revert can tell a value the run added."""
        cls = [c for n, (c, _) in __import__("fake_api").NS.items() if n == ns][0]
        if cls != "Tag":
            obj = {"private": False, **copy.deepcopy(self.DEFAULTS[cls]), **obj}
        if cls == "Person":
            obj["primary_name"] = {"call": "", "nick": "", "title": "", "suffix": "", **obj.get("primary_name", {})}
        return super().add(ns, obj)

    def _family_refs(self, old, new):
        """Also what the server does when a family loses a member: the person's side goes too."""
        super()._family_refs(old, new)
        def members(f):
            return {f.get("father_handle"), f.get("mother_handle")} - {None}, {r["ref"] for r in f.get("child_ref_list") or []}
        if old:
            (old_p, old_k), (new_p, new_k) = members(old), members(new)
            for h, lst in [*((h, "family_list") for h in old_p - new_p), *((h, "parent_family_list") for h in old_k - new_k)]:
                self.store["people"][h][lst] = [x for x in self.store["people"][h][lst] if x != new["handle"]]

    def _route(self, method, path, headers, params, body, data):
        parts = [p for p in path.split("/") if p][1:]
        if method == "POST" and len(parts) == 4 and parts[2] == "merge":
            ns, w, l_ = parts[0], parts[1], parts[3]
            win, lose = self.store[ns][w], self.store[ns].pop(l_)
            for lst in ("citation_list", "note_list", "media_list", "alt_names", "placeref_list", "attribute_list"):
                win[lst] = win.get(lst, []) + [x for x in lose.get(lst, []) if x not in win.get(lst, [])]
            for store in self.store.values():
                for h, o in list(store.items()):
                    text = json.dumps(o)
                    if l_ in text:
                        store[h] = json.loads(text.replace(l_, w))
            win["change"] = self.tick()
            return Resp(200, {})
        if method == "PUT" and len(parts) == 3 and parts[0] == "media" and parts[2] == "file" and not params.get("uploadmissing"):
            media = self.store["media"][parts[1]]
            if headers.get("If-Match") and headers["If-Match"] != media["checksum"]:
                return Resp(412, "ETag mismatch. Resource has been modified.")
            new = hashlib.md5(data).hexdigest()
            if new == media["checksum"]:
                return Resp(409, "Uploaded file has the same checksum as the existing media object")
            media.update(checksum=new, path=f"{new}.jpg", mime=headers.get("Content-Type"), change=self.tick())
            self.files[parts[1]] = data
            return Resp(200, [])
        return super()._route(method, path, headers, params, body, data)


@pytest.fixture
def fake():
    f = Fake()
    f.add("places", {"handle": "plc1", "name": {"value": "Springfield"}, "place_type": "Town"})  # P0001
    f.add("people", {"handle": "anna", "gender": 0, "primary_name": {
        "first_name": "Anna", "surname_list": [{"surname": "Example", "primary": True}]}})  # I0001
    f.add("sources", {"handle": "src1", "title": "Parish register of Springfield, book 3"})  # S0001
    return f


def _client(fake, *, dry_run=False, log=None):
    c = Client("https://gw.example", "u", "pw", tree="tree-test", dry_run=dry_run, log_path=log, session=fake)
    c.login()
    return c


def run(fake, ops, *, dry_run=False, log=None, verify=False):
    return Applier(_client(fake, dry_run=dry_run or verify, log=log), retries=3, retry_wait=0, verify=verify).run(ops)


def state(fake) -> dict:
    """The tree without the server's change stamps: what a revert must give back."""
    return {ns: {h: {k: v for k, v in o.items() if k != "change"} for h, o in rows.items()}
            for ns, rows in copy.deepcopy(fake.store).items()}


def roundtrip(fake, tmp_path, ops):
    """Dry run (nothing written), apply, --verify (all skip), --revert (the tree as before). Returns the results
    of the apply and the revert plan."""
    assert not validate(ops), validate(ops)
    before = state(fake)
    pre = tmp_path / "pre.json.gz"
    with gzip.open(pre, "wt") as fh:
        json.dump({"tree": "tree-test", "objects": {ns: list(fake.store[ns].values()) for ns in NAMESPACE.values()}}, fh)
    writes = len(fake.writes())
    dry = run(fake, ops, dry_run=True)
    assert [r.status for r in dry] == ["done"] * len(ops), [r.actions for r in dry]
    assert len(fake.writes()) == writes and state(fake) == before
    log = str(tmp_path / "run.jsonl")
    done = run(fake, ops, log=log)
    assert [r.status for r in done] == ["done"] * len(ops), [r.actions for r in done]
    assert state(fake) != before
    landed = run(fake, ops, verify=True)
    assert [r.status for r in landed] == ["skip"] * len(ops), [r.actions for r in landed]
    client = _client(fake)
    pre_objs = revert.load_pre(str(pre))
    steps = revert.plan(revert.read_log(log), pre_objs, client.get_live,
                        lambda c, h: (client.get_live(c, h, backlinks=True) or {}).get("backlinks"))
    todo = [s for s in steps if s["action"] in ("restore", "recreate", "delete")]
    assert revert.execute(client, todo, pre_objs, retry_wait=0) == []
    assert state(fake) == before
    return done, steps


def _note(fake, handle, text):
    return fake.add("notes", {"handle": handle, "type": "Research", "format": 0,
                              "text": {"_class": "StyledText", "string": text, "tags": []}})


# ---- fields the update ops lacked


def test_update_person_name_parts_and_private(fake, tmp_path):
    op = {"op": "update_person", "key": "u", "ruling": R, "person": "I0001",
          "set": {"call_name": "Anna", "nickname": "Annie", "title": "Dr.", "suffix": "Jr.", "private": True}}
    roundtrip(fake, tmp_path, [op])
    assert validate([dict(op, set={"nickname": 3})]) and validate([dict(op, set={"private": "yes"})])


def test_private_goes_through_every_update(fake, tmp_path):
    fake.add("events", {"handle": "ev", "type": "Birth"})
    fake.add("families", {"handle": "fam", "mother_handle": "anna", "type": "Unknown"})
    fake.add("citations", {"handle": "cit", "source_handle": "src1", "page": "folio 3"})
    fake.add("repositories", {"handle": "rep", "name": "County Archive", "type": "Archive"})
    fake.add("media", {"handle": "med", "path": "a.jpg", "checksum": "0" * 32, "mime": "image/jpeg", "desc": ""})
    _note(fake, "nt", "a research note")
    ids = {ns: next(iter(fake.store[ns].values()))["gramps_id"] for ns in fake.store if fake.store[ns]}
    field = {"event": "events", "place": "places", "note": "notes", "citation": "citations", "source": "sources",
             "family": "families", "repository": "repositories", "media": "media"}
    ops = [{"op": f"update_{kind}", "key": kind, "ruling": R, kind: ids[ns], "set": {"private": True}}
           for kind, ns in field.items()]
    roundtrip(fake, tmp_path, ops)


def test_update_family_sets_a_missing_father_and_never_replaces_one(fake, tmp_path):
    fake.add("people", {"handle": "ben", "gender": 1, "primary_name": {"first_name": "Ben", "surname_list": []}})  # I0002
    fake.add("people", {"handle": "carl", "gender": 1, "primary_name": {"first_name": "Carl", "surname_list": []}})  # I0003
    fake.add("families", {"handle": "fam", "mother_handle": "anna", "type": "Unknown"})  # F0001
    op = {"op": "update_family", "key": "f", "ruling": R, "family": "F0001", "set": {"father": "I0002", "type": "Married"}}
    roundtrip(fake, tmp_path, [op])
    assert run(fake, [op])[0].status == "done"
    assert fake.store["people"]["ben"]["family_list"] == ["fam"]  # the server keeps the person's side in step
    other = run(fake, [dict(op, key="g", set={"father": "I0003"})])[0]
    assert other.status == "conflict" and "already has a father, I0002" in other.actions[-1], other.actions
    # a second family of the same couple is a merge, not a fix
    fake.add("families", {"handle": "fam2", "mother_handle": "anna", "type": "Unknown"})  # F0002
    twin = run(fake, [dict(op, key="h", family="F0002", set={"father": "I0002"})])[0]
    assert twin.status == "conflict" and "merge_family" in twin.actions[-1], twin.actions
    assert validate([dict(op, set={"father": "I0002", "mother": "I0002"})])
    assert validate([dict(op, set={"father": "0123456789abcdef0123456789ab"})])  # never a handle


def test_update_citation_moves_to_another_source_only_from_the_one_it_names(fake, tmp_path):
    fake.add("sources", {"handle": "src2", "title": "Parish register of Springfield, book 4"})  # S0002
    fake.add("citations", {"handle": "cit", "source_handle": "src1", "page": "folio 3"})  # C0001
    op = {"op": "update_citation", "key": "c", "ruling": R, "citation": "C0001", "set": {"source": "S0002"},
          "expect_source": "S0001"}
    roundtrip(fake, tmp_path, [op])
    res = run(fake, [dict(op, expect_source="S0002", key="d")])[0]  # it is on S0001
    assert res.status == "conflict" and "no longer on Source S0002" in res.actions[-1], res.actions
    assert fake.store["citations"]["cit"]["source_handle"] == "src1"
    assert validate([{k: v for k, v in op.items() if k != "expect_source"}])
    assert validate([dict(op, set={"page": "folio 4"}, expect="folio 3")])  # expect_source goes with set.source


# ---- media


def test_update_media_description_date_path_and_private(fake, tmp_path):
    fake.add("media", {"handle": "med", "path": "scans/a.jpg", "checksum": "0" * 32, "mime": "image/jpeg", "desc": ""})
    op = {"op": "update_media", "key": "m", "ruling": R, "media": "O0001",
          "set": {"desc": "Baptism entry, folio 12", "date": "1890-03-16", "path": "scans/b.jpg", "private": True}}
    roundtrip(fake, tmp_path, [op])
    assert validate([dict(op, set={"path": "/abs/b.jpg"})]) and validate([dict(op, set={"colour": "x"})])


def test_update_media_replaces_the_file_only_from_the_md5_it_names(fake, tmp_path):
    old = b"old scan"
    fake.add("media", {"handle": "med", "path": "scans/a.jpg", "checksum": hashlib.md5(old).hexdigest(),
                       "mime": "image/jpeg", "desc": "Baptism"})
    new = tmp_path / "better.jpg"
    new.write_bytes(b"a sharper scan of the same page")
    op = {"op": "update_media", "key": "m", "ruling": R, "media": "O0001", "set": {"file": str(new)},
          "expect": hashlib.md5(b"another file").hexdigest()}
    res = run(fake, [op])[0]  # the MD5 it names is not the one the object has
    assert res.status == "conflict" and "no longer holds the file" in res.actions[-1], res.actions
    assert fake.writes() == []
    op["expect"] = hashlib.md5(old).hexdigest()
    assert run(fake, [op], dry_run=True)[0].status == "done" and fake.writes() == []
    res = run(fake, [op])[0]
    assert res.status == "done" and fake.files["med"] == new.read_bytes(), res.actions
    assert fake.store["media"]["med"]["checksum"] == hashlib.md5(new.read_bytes()).hexdigest()
    assert run(fake, [op], verify=True)[0].status == "skip"
    calls = [h for (m, p), h in zip(fake.calls, fake.headers) if p == "/api/media/med/file"]
    assert calls and calls[0]["If-Match"] == op["expect"]  # the server checks it too
    assert validate([{k: v for k, v in op.items() if k != "expect"}])  # a new file needs the old MD5
    assert validate([dict(op, set={"file": str(new), "path": "x.jpg"})])


def test_add_media_refuses_a_non_document_unless_forced_and_keeps_a_region(fake, tmp_path):
    page = tmp_path / "page.html"
    page.write_text("<html>a saved page</html>")
    op = {"op": "add_media", "key": "m", "ruling": R, "file": str(page), "path": "web/page.html",
          "attach": [{"person": "I0001"}]}
    res = run(fake, [op])[0]
    assert res.status == "conflict" and "text/html" in res.actions[-1] and fake.writes() == [], res.actions
    assert run(fake, [dict(op, force=True)])[0].status == "done"
    scan = tmp_path / "page.jpg"
    scan.write_bytes(b"\xff\xd8 a group photo")
    ops = [{"op": "add_media", "key": "p", "ruling": R, "file": str(scan), "path": "photos/group.jpg",
            "attach": [{"person": "I0001"}], "rect": [10, 20, 30, 60]}]
    roundtrip(fake, tmp_path, [{"op": "attach_existing", "key": "a", "ruling": R, "object": {"media": "O0001"},
                                "attach": [{"person": "I0001"}], "rect": [0, 0, 50, 50]}])
    run(fake, ops)
    refs = [r for r in fake.store["people"]["anna"]["media_list"] if r["ref"] == stable_handle("p")]
    assert refs == [{"ref": stable_handle("p"), "rect": [10, 20, 30, 60]}]
    for bad in ([0, 0, 0, 50], [0, 0, 101, 50], [0, 0, 50], ["0", 0, 50, 50]):
        assert validate([dict(ops[0], rect=bad)]), bad
    assert validate([{"op": "attach_existing", "key": "n", "ruling": R, "object": {"note": "N0001"},
                      "attach": [{"person": "I0001"}], "rect": [0, 0, 50, 50]}])  # a region is of an image


# ---- order, names, ids


def test_reorder_refs_moves_one_event_and_keeps_the_birth(fake, tmp_path):
    for h, t in (("e1", "Baptism"), ("e2", "Birth"), ("e3", "Death")):
        fake.add("events", {"handle": h, "type": t})
    anna = fake.store["people"]["anna"]
    anna["event_ref_list"] = [{"ref": h, "role": "Primary"} for h in ("e1", "e2", "e3")]
    anna["birth_ref_index"], anna["death_ref_index"] = 1, 2
    op = {"op": "reorder_refs", "key": "r", "ruling": R, "target": {"person": "I0001"}, "list": "event_ref_list",
          "ref": "E0002", "position": 0}
    roundtrip(fake, tmp_path, [op])
    run(fake, [op])
    anna = fake.store["people"]["anna"]
    assert [r["ref"] for r in anna["event_ref_list"]] == ["e2", "e1", "e3"]
    assert (anna["birth_ref_index"], anna["death_ref_index"]) == (0, 2)
    past = run(fake, [dict(op, key="p", position=3)])[0]
    assert past.status == "conflict" and "past its end" in past.actions[-1], past.actions
    assert validate([dict(op, list="child_ref_list")])  # a person has no children list
    assert validate([dict(op, position=-1)]) and validate([dict(op, position="1")])


def test_reorder_refs_puts_children_in_birth_order(fake, tmp_path):
    for h in ("k1", "k2"):
        fake.add("people", {"handle": h, "gender": 2, "primary_name": {"first_name": h, "surname_list": []}})
    fake.add("families", {"handle": "fam", "mother_handle": "anna", "child_ref_list": [
        {"ref": "k1", "frel": "Birth", "mrel": "Birth"}, {"ref": "k2", "frel": "Birth", "mrel": "Birth"}]})
    roundtrip(fake, tmp_path, [{"op": "reorder_refs", "key": "r", "ruling": R, "target": {"family": "F0001"},
                                "list": "child_ref_list", "ref": "I0003", "position": 0}])


def test_alternate_names_of_a_person_and_a_place(fake, tmp_path):
    add = [{"op": "add_alternate_name", "key": "n1", "ruling": R, "target": {"person": "I0001"}, "type": "Married Name",
            "first_name": "Anna", "surnames": [{"surname": "Sample", "origin": "Taken"}]},
           {"op": "add_alternate_name", "key": "n2", "ruling": R, "target": {"place": "P0001"}, "name": "Springfeld",
            "lang": "de"}]
    roundtrip(fake, tmp_path, add)
    run(fake, add)
    names = fake.store["people"]["anna"]["alternate_names"]
    assert names[0]["surname_list"][0] == {"surname": "Sample", "prefix": "", "primary": True, "origintype": "Taken"}
    assert fake.store["places"]["plc1"]["alt_names"] == [{"value": "Springfeld", "lang": "de"}]
    remove = [dict(add[0], op="remove_alternate_name", key="r1"), dict(add[1], op="remove_alternate_name", key="r2")]
    del remove[0]["type"]  # found by the fields given
    (tmp_path / "rm").mkdir()
    roundtrip(fake, tmp_path / "rm", remove)
    assert [r.status for r in run(fake, remove)] == ["done", "done"]
    assert fake.store["people"]["anna"]["alternate_names"] == [] and fake.store["places"]["plc1"]["alt_names"] == []
    names_twice = [dict(add[0], key="t1"), dict(add[0], key="t2", type="Also Known As")]
    run(fake, names_twice)
    two = run(fake, [dict(remove[0], key="r3")])[0]
    assert two.status == "conflict" and "2 other names that match" in two.actions[-1], two.actions
    for bad in ({"name": "x"}, {"first_name": ""}, {"surnames": [{"surname": ""}]}, {"surnames": "Sample"},
                {"first_name": "A", "lang": "de"}):
        assert validate([{"op": "add_alternate_name", "key": "b", "ruling": R, "target": {"person": "I0001"}, **bad}]), bad
    assert validate([{"op": "add_alternate_name", "key": "b", "ruling": R, "target": {"place": "P0001"}, "first_name": "x"}])
    assert validate([{"op": "add_alternate_name", "key": "b", "ruling": R, "target": {"event": "E0001"}, "name": "x"}])


def test_set_external_id_writes_familysearch_where_gramps_reads_it(fake, tmp_path):
    op = {"op": "set_external_id", "key": "x", "ruling": R, "target": {"person": "I0001"}, "system": "FamilySearch",
          "id": "gwt0-001"}
    other = {"op": "set_external_id", "key": "y", "ruling": R, "target": {"person": "I0001"}, "system": "WikiTree",
             "id": "Example-1234", "url": "https://www.wikitree.com/wiki/Example-1234"}
    roundtrip(fake, tmp_path, [op, other])
    run(fake, [op, other])
    attrs = {a["type"]: a["value"] for a in fake.store["people"]["anna"]["attribute_list"]}
    assert attrs == {FAMILYSEARCH: "GWT0-001", "WikiTree ID": "Example-1234"}
    assert fake.store["people"]["anna"]["urls"][0]["path"] == other["url"]
    clash = run(fake, [dict(op, key="z", id="GWT0-002")])[0]
    assert clash.status == "conflict" and "GWT0-001" in clash.actions[-1], clash.actions
    assert validate([dict(op, id="not an id")]) and validate([dict(other, target={"event": "E0001"})])


# ---- deletes and merges


def test_delete_takes_an_unreferenced_object_and_revert_makes_it_again(fake, tmp_path):
    _note(fake, "orphan", "a note nothing points at")
    op = {"op": "delete", "key": "d", "ruling": R, "object": {"note": "N0001"}, "expect_unreferenced": True}
    _, steps = roundtrip(fake, tmp_path, [op])
    assert [s["action"] for s in steps] == ["recreate"]
    assert validate([{k: v for k, v in op.items() if k != "expect_unreferenced"}])
    assert validate([dict(op, expect_unreferenced="yes")]) and validate([dict(op, object={"tag": "T1"})])


def test_delete_refuses_an_object_something_points_at(fake):
    _note(fake, "held", "on Anna")
    fake.store["people"]["anna"]["note_list"] = ["held"]
    op = {"op": "delete", "key": "d", "ruling": R, "object": {"note": "N0001"}, "expect_unreferenced": True}
    res = run(fake, [op])[0]
    assert res.status == "conflict" and "still referenced by person anna" in res.actions[-1], res.actions
    assert "held" in fake.store["notes"] and fake.writes() == []
    # a detach earlier in the same list frees it, in the dry run as in the apply
    ops = [{"op": "detach", "key": "x", "ruling": R, "target": {"person": "I0001"}, "object": {"note": "N0001"}}, op]
    assert [r.status for r in run(fake, ops, dry_run=True)] == ["done", "done"]
    assert [r.status for r in run(fake, ops)] == ["done", "done"] and "held" not in fake.store["notes"]


def test_revert_makes_a_deleted_object_again_from_the_log_alone(fake, tmp_path):
    fake.add("places", {"handle": "dup", "name": {"value": "Springfeld"}, "place_type": "Town"})  # P0002
    before = state(fake)
    log = str(tmp_path / "run.jsonl")
    run(fake, [{"op": "delete", "key": "d", "ruling": R, "object": {"place": "P0002"}, "expect_unreferenced": True}],
        log=log)
    assert "dup" not in fake.store["places"]
    client = _client(fake)
    steps = revert.plan(revert.read_log(log), {}, client.get_live)  # a pre-run copy without it
    assert [(s["action"], s["why"]) for s in steps] == [("recreate", "deleted by the run; made again from its copy in the log")]
    assert revert.execute(client, steps, {}, retry_wait=0) == []
    assert state(fake) == before


def _merged(fake, ops):
    assert [r.status for r in run(fake, ops, dry_run=True)] == ["done"] * len(ops) and fake.writes() == []
    done = run(fake, ops)
    assert [r.status for r in done] == ["done"] * len(ops), [r.actions for r in done]
    assert [r.status for r in run(fake, ops)] == ["skip"] * len(ops)  # a rerun finds the merge note
    assert [r.status for r in run(fake, ops, verify=True)] == ["skip"] * len(ops)
    return done


def test_merge_place_moves_events_and_refuses_another_type(fake):
    fake.add("places", {"handle": "dup", "name": {"value": "Springfeld"}, "place_type": "Town"})  # P0002
    fake.add("events", {"handle": "ev", "type": "Birth", "place": "dup"})
    op = {"op": "merge_place", "key": "m", "ruling": R, "winner": "P0001", "loser": "P0002", "reason": "one town"}
    done = _merged(fake, [op])
    assert "merge Place P0002 into P0001" in "\n".join(done[0].actions)
    assert fake.store["events"]["ev"]["place"] == "plc1" and "dup" not in fake.store["places"]
    fake.add("places", {"handle": "par", "name": {"value": "Springfield"}, "place_type": "Parish"})  # P0002 again
    res = run(fake, [dict(op, key="n")])[0]
    assert res.status == "conflict" and "a Town" in res.actions[-1] and "Parish" in res.actions[-1], res.actions
    fake.store["places"]["par"]["place_type"] = "Town"
    fake.store["places"]["par"]["placeref_list"] = [{"ref": "plc1"}]  # inside the winner
    res = run(fake, [dict(op, key="o")])[0]
    assert res.status == "conflict" and "encloses itself" in res.actions[-1], res.actions


def test_merge_source_moves_citations(fake):
    fake.add("sources", {"handle": "src2", "title": "Springfield parish register, book 3"})  # S0002
    fake.add("citations", {"handle": "cit", "source_handle": "src2", "page": "folio 3"})
    _merged(fake, [{"op": "merge_source", "key": "m", "ruling": R, "winner": "S0001", "loser": "S0002"}])
    assert fake.store["citations"]["cit"]["source_handle"] == "src1"
    note = fake.store["notes"][stable_handle("m#merge")]
    assert note["text"]["string"].startswith("Merged Source S0002 (Springfield parish register, book 3) into this source")


def test_merge_citation_needs_the_same_source_and_page(fake):
    fake.add("citations", {"handle": "c1", "source_handle": "src1", "page": "folio 3"})  # C0001
    fake.add("citations", {"handle": "c2", "source_handle": "src1", "page": "folio 3"})  # C0002
    fake.store["people"]["anna"]["citation_list"] = ["c2"]
    op = {"op": "merge_citation", "key": "m", "ruling": R, "winner": "C0001", "loser": "C0002"}
    _merged(fake, [op])
    assert fake.store["people"]["anna"]["citation_list"] == ["c1"]
    fake.add("sources", {"handle": "src2", "title": "Another book"})
    fake.add("citations", {"handle": "c3", "source_handle": "src2", "page": "folio 3"})  # C0002 again
    res = run(fake, [dict(op, key="n")])[0]
    assert res.status == "conflict" and "different sources" in res.actions[-1], res.actions
    fake.store["citations"]["c3"].update(source_handle="src1", page="folio 4")
    res = run(fake, [dict(op, key="o")])[0]
    assert res.status == "conflict" and "differ in page" in res.actions[-1], res.actions


def test_ops_table_lists_every_op_with_its_fields():
    table = ops_table()
    assert set(table) == set(SPECS)
    assert table["delete"] == {"required": ["expect_unreferenced", "object"], "optional": ["expect", "tag"]}
    assert all(set(row) == {"required", "optional"} for row in table.values())
