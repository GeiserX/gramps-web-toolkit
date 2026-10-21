import json

import pytest

from fake_api import FakeGrampsWeb, Resp
from gwt_apply.client import ApiError, Client
from gwt_apply.dates import DateError, parse_date
from gwt_apply.ops import Applier, stable_handle, validate
from gwt_apply.cli import main

R = "ruling-0001"


def run(fake, ops, *, dry_run=False, tree="tree-test", tmp_path=None):
    log = str(tmp_path / "log.jsonl") if tmp_path else None
    client = Client("https://gw.example", "u", "s3cret-pw", tree=tree, dry_run=dry_run, log_path=log, session=fake)
    client.login()
    return Applier(client, retries=3, retry_wait=0).run(ops)


@pytest.fixture
def fake():
    f = FakeGrampsWeb()
    f.add("places", {"handle": "plc1", "name": {"value": "Springfield"}})  # P0001
    f.add("people", {"handle": "anna", "gender": 0, "primary_name": {"first_name": "Anna", "surname_list": [{"surname": "Example", "primary": True}]}})  # I0001
    f.add("sources", {"handle": "src1", "title": "Parish register of Springfield, book 3"})  # S0001
    return f


@pytest.fixture
def scan(tmp_path):
    p = tmp_path / "baptism-1890.jpg"
    p.write_bytes(b"\xff\xd8 not really a jpeg")
    return p


def full_list(scan):
    return [
        {"op": "add_person", "key": "p-john", "ruling": R, "first_name": "John", "surname": "Example", "gender": "M"},
        {"op": "add_event", "key": "e-john-birth", "ruling": R, "person": "key:p-john", "type": "Birth", "date": "1890-03-14", "place": "P0001"},
        {"op": "add_family", "key": "f-anna", "ruling": R, "mother": "I0001", "children": ["key:p-john"]},
        {"op": "add_citation", "key": "c-bapt", "ruling": R, "source": "S0001", "page": "folio 12v, no. 40", "confidence": 3,
         "attach": [{"event": "key:e-john-birth"}, {"person": "key:p-john"}]},
        {"op": "add_media", "key": "m-bapt", "ruling": R, "file": str(scan), "path": "sources/baptism-1890.jpg",
         "desc": "Baptism entry", "attach": [{"citation": "key:c-bapt"}]},
        {"op": "add_note", "key": "n-john", "ruling": R, "type": "Research", "text": "Godparents named in the entry.",
         "attach": [{"person": "key:p-john"}]},
        {"op": "set_attribute", "key": "a-john-occ", "ruling": R, "target": {"person": "key:p-john"}, "type": "Occupation", "value": "miller"},
        {"op": "update_person", "key": "u-anna", "ruling": R, "person": "I0001", "set": {"first_name": "Anna Maria"}},
    ]


def test_validate_refuses_bad_change_lists(scan):
    errors = validate([
        {"op": "add_person", "key": "a", "first_name": "X", "surname": "Y"},
        {"op": "add_event", "key": "a", "ruling": R, "type": "Birth", "person": "0123b4e9269542049080d7a4c798"},
        {"op": "add_event", "key": "b", "ruling": R, "type": "Birth", "person": "I1", "date": "14 March 1890"},
        {"op": "add_note", "key": "c", "ruling": R, "text": "x", "attach": [{"person": "I1"}], "colour": "red"},
        {"op": "delete_person", "key": "d", "ruling": R},
    ])
    text = "\n".join(errors)
    assert "no ruling" in text
    assert "key used twice" in text
    assert "never a handle" in text
    assert "unreadable date" in text
    assert "unknown fields ['colour']" in text
    assert "unknown op 'delete_person'" in text
    assert validate(full_list(scan)) == []


def test_full_apply_then_rerun_writes_nothing(fake, scan, tmp_path):
    results = run(fake, full_list(scan), tmp_path=tmp_path)
    assert [r.status for r in results] == ["done"] * 8, [r.actions for r in results]

    john = fake.store["people"][stable_handle("p-john")]
    birth = fake.store["events"][stable_handle("e-john-birth")]
    assert birth["place"] == "plc1"  # the Gramps ID P0001 became a handle
    assert birth["date"]["dateval"] == [14, 3, 1890, False]
    assert john["event_ref_list"] == [{"_class": "EventRef", "ref": birth["handle"], "role": "Primary"}]
    assert john["birth_ref_index"] == 0
    fam = fake.store["families"][stable_handle("f-anna")]
    assert fam["mother_handle"] == "anna" and fam["child_ref_list"][0]["ref"] == john["handle"]
    cit = stable_handle("c-bapt")
    assert cit in birth["citation_list"] and cit in john["citation_list"]
    media = fake.store["media"][stable_handle("m-bapt")]
    assert media["checksum"] == __import__("hashlib").md5(scan.read_bytes()).hexdigest()
    assert fake.files[media["handle"]] == scan.read_bytes()
    assert fake.store["citations"][cit]["media_list"] == [{"_class": "MediaRef", "ref": media["handle"]}]
    assert john["attribute_list"] == [{"_class": "Attribute", "type": "Occupation", "value": "miller"}]
    assert fake.store["people"]["anna"]["primary_name"]["first_name"] == "Anna Maria"
    # media went in through /api/objects/ and the upload-missing file route, never POST /api/media/
    assert ("POST", "/api/media/") not in fake.calls
    # every write is in the log with its ruling
    log = [json.loads(l) for l in (tmp_path / "log.jsonl").read_text().splitlines()]
    writes = [l for l in log if l.get("method") in ("POST", "PUT") and l["path"] != "/token/"]
    assert writes and all(l["ruling"] == R for l in writes)
    assert "s3cret-pw" not in (tmp_path / "log.jsonl").read_text()

    before = len(fake.writes())
    again = run(fake, full_list(scan))
    assert [r.status for r in again] == ["skip"] * 8, [r.actions for r in again]
    # the only call with a write verb is the file probe, which the server answers 409 (already stored)
    assert [w for w in fake.writes()[before:] if not w[1].endswith("/file")] == []
    assert "file: file already on server" in again[4].actions


def test_dry_run_writes_nothing_and_follows_keys(fake, scan):
    results = run(fake, full_list(scan), dry_run=True)
    assert [r.status for r in results] == ["done"] * 8, [r.actions for r in results]
    assert fake.writes() == []
    plan = "\n".join(a for r in results for a in r.actions)
    assert "create Event" in plan and '"place": "plc1"' in plan
    assert '"first_name": "Anna"' in plan and '"Anna Maria"' in plan  # the diff shows old -> new


def test_same_birth_is_skipped_other_date_is_a_conflict(fake):
    ev = fake.add("events", {"handle": "b1", "type": "Birth", "date": parse_date("1890-03-14")})
    fake.store["people"]["anna"]["event_ref_list"] = [{"ref": ev["handle"], "role": "Primary"}]
    same = [{"op": "add_event", "key": "e1", "ruling": R, "person": "I0001", "type": "Birth", "date": "1890-03-14"}]
    res = run(fake, same)
    assert res[0].status == "skip" and fake.writes() == []

    other = [{"op": "add_event", "key": "e2", "ruling": R, "person": "I0001", "type": "Birth", "date": "1891"},
             {"op": "add_note", "key": "n", "ruling": R, "text": "x", "attach": [{"person": "I0001"}]}]
    res = run(fake, other)
    assert res[0].status == "conflict" and "already has a Birth" in res[0].actions[-1]
    assert len(res) == 1  # stops at the first conflict
    assert fake.writes() == []


def test_existing_citation_page_and_media_checksum_are_reused(fake, scan):
    fake.add("citations", {"handle": "cit0", "source_handle": "src1", "page": "folio 12v, no. 40"})
    import hashlib
    fake.add("media", {"handle": "med0", "path": "old/scan.jpg", "checksum": hashlib.md5(scan.read_bytes()).hexdigest()})
    ops = [
        {"op": "add_citation", "key": "c", "ruling": R, "source": "S0001", "page": "folio 12v, no. 40 ", "attach": [{"person": "I0001"}]},
        {"op": "add_media", "key": "m", "ruling": R, "file": str(scan), "path": "new/scan.jpg", "attach": [{"citation": "key:c"}]},
    ]
    res = run(fake, ops)
    assert [r.status for r in res] == ["done", "done"]
    assert len(fake.store["citations"]) == 1 and len(fake.store["media"]) == 1
    assert fake.store["people"]["anna"]["citation_list"] == ["cit0"]
    assert fake.store["citations"]["cit0"]["media_list"][0]["ref"] == "med0"
    assert ("POST", "/api/objects/") not in fake.calls


def test_crash_between_create_and_link_is_finished_by_a_rerun(fake):
    ops = [{"op": "add_event", "key": "e", "ruling": R, "person": "I0001", "type": "Death", "date": "abt 1950"}]
    # the answer to the create is lost, but the server made the event: the op starts over and only links it
    fake.drop_after = ("POST", "/api/objects/")
    res = run(fake, ops)
    assert res[0].status == "done", res[0].actions
    assert len(fake.store["events"]) == 1
    assert fake.store["people"]["anna"]["event_ref_list"][0]["ref"] == stable_handle("e")
    assert fake.store["people"]["anna"]["death_ref_index"] == 0
    # the same after a real crash: a new run finds the event and links it
    fake.store["people"]["anna"].update(event_ref_list=[], death_ref_index=-1)
    assert run(fake, ops)[0].status == "done"
    assert len(fake.store["events"]) == 1 and fake.store["people"]["anna"]["death_ref_index"] == 0


def test_busy_server_is_retried_from_fresh_reads(fake):
    fake.busy = 2
    ops = [{"op": "add_note", "key": "n", "ruling": R, "text": "x", "attach": [{"person": "I0001"}]}]
    res = run(fake, ops)
    assert res[0].status == "done"
    assert len(fake.store["notes"]) == 1 and fake.store["people"]["anna"]["note_list"] == [stable_handle("n")]


def test_login_into_the_wrong_tree_is_refused(fake):
    with pytest.raises(ApiError, match="expected"):
        run(fake, [], tree="another-tree")


def test_unknown_gramps_id_is_a_conflict_before_any_write(fake):
    res = run(fake, [{"op": "link_child", "key": "l", "ruling": R, "family": "F0099", "child": "I0001"}])
    assert res[0].status == "conflict" and fake.writes() == []


def test_dates():
    assert parse_date("1798-11-20")["sortval"] == 2378090  # value Gramps stored for this date
    d = parse_date("est bet 1890 and 1895-06")
    assert (d["quality"], d["modifier"], d["dateval"]) == (1, 4, [0, 0, 1890, False, 0, 6, 1895, False])
    assert parse_date("bef 1900")["modifier"] == 1
    assert parse_date("")["dateval"] == [0, 0, 0, False]
    for bad in ("March 1890", "1890-13", "abt", "1890-00-05"):
        with pytest.raises(DateError):
            parse_date(bad)


def test_cli_validation_stops_before_any_network(tmp_path, capsys):
    p = tmp_path / "changes.jsonl"
    p.write_text(json.dumps({"op": "add_person", "key": "k", "first_name": "A", "surname": "B"}) + "\n")
    assert main([str(p)]) == 2
    assert "no ruling" in capsys.readouterr().err


def test_add_place_dedupes_on_name_type_parent_and_feeds_event_places(fake):
    fake.add("places", {"handle": "town", "name": {"value": "Oakford"}, "place_type": "Town", "placeref_list": [{"ref": "plc1"}]})
    ops = [
        {"op": "add_place", "key": "pl-oakford", "ruling": R, "name": "Oakford", "type": "Town", "enclosed_by": "P0001"},
        {"op": "add_place", "key": "pl-st-mary", "ruling": R, "name": "St Mary", "type": "Parish", "enclosed_by": "key:pl-oakford", "lat": 51.5, "long": -1.25},
        {"op": "add_place", "key": "pl-oakford-2", "ruling": R, "name": "Oakford", "type": "Town"},  # no parent: another place
        {"op": "add_event", "key": "e", "ruling": R, "person": "I0001", "type": "Baptism", "date": "1890", "place": "key:pl-st-mary"},
    ]
    res = run(fake, ops)
    assert [r.status for r in res] == ["skip", "done", "done", "done"], [r.actions for r in res]
    parish = fake.store["places"][stable_handle("pl-st-mary")]
    assert parish["placeref_list"] == [{"_class": "PlaceRef", "ref": "town"}]  # the existing town, found by dedupe
    assert (parish["lat"], parish["long"]) == ("51.5", "-1.25")
    assert fake.store["events"][stable_handle("e")]["place"] == parish["handle"]
    assert [r.status for r in run(fake, ops)] == ["skip"] * 4
    assert any("lat and long go together" in e for e in validate(
        [{"op": "add_place", "key": "x", "ruling": R, "name": "A", "type": "Town", "lat": 1}]))


def test_a_concurrent_edit_is_not_overwritten(fake):
    # another writer renames Anna right after our first read; the op must start over and keep that rename
    fake.edit_after_read = ("anna", lambda o: o["primary_name"].update(first_name="Annie"))
    ops = [{"op": "set_attribute", "key": "a", "ruling": R, "target": {"person": "I0001"}, "type": "Nickname", "value": "Nan"}]
    res = run(fake, ops)
    assert res[0].status == "done"
    anna = fake.store["people"]["anna"]
    assert anna["primary_name"]["first_name"] == "Annie"
    assert anna["attribute_list"][-1]["value"] == "Nan"


def test_namesake_is_a_conflict_unless_allowed(fake):
    ops = [{"op": "add_person", "key": "p", "ruling": R, "first_name": "anna", "surname": "Example"}]
    res = run(fake, ops)
    assert res[0].status == "conflict" and "already exists as I0001" in res[0].actions[-1]
    assert fake.writes() == []
    ops[0].update(allow_namesake=True, namesake_reason="born forty years later, other parents", namesake_of="I0099")
    res = run(fake, ops)
    assert res[0].status == "conflict" and "namesake_of does not name" in res[0].actions[-1]
    ops[0]["namesake_of"] = ["I0001"]
    res = run(fake, ops)
    assert res[0].status == "done" and "kept apart: born forty years later" in res[0].actions[0]


def test_person_with_empty_surname(fake):
    """A DNA match known only as "Ruth" has no surname: "" is a real value, a missing field is not."""
    ops = [{"op": "add_person", "key": "p-ruth", "ruling": R, "first_name": "Ruth", "surname": "", "gender": "F"}]
    assert validate(ops) == []
    res = run(fake, ops)
    assert res[0].status == "done"
    person = fake.store["people"][stable_handle("p-ruth")]
    assert person["primary_name"]["first_name"] == "Ruth"
    assert person["primary_name"]["surname_list"][0]["surname"] == ""
    assert any("missing ['surname']" in e for e in validate([{"op": "add_person", "key": "q", "ruling": R, "first_name": "Ruth"}]))


def test_strict_change_list():
    errors = "\n".join(validate([
        {"op": "add_person", "key": "a", "ruling": R, "first_name": "X", "surname": "Y", "allow_namesake": True},
        {"op": "add_person", "key": "b", "ruling": R, "first_name": "X", "surname": "Y", "namesake_reason": "r"},
        {"op": "link_child", "key": "c", "ruling": R, "family": "F1", "child": "I1", "probable": True},
        {"op": "add_note", "key": "d1", "ruling": R, "text": "same", "attach": [{"person": "I1"}]},
        {"op": "add_note", "key": "d2", "ruling": "another-ruling", "text": "same", "attach": [{"person": "I1"}]},
    ]))
    assert "allow_namesake needs namesake_reason" in errors
    assert "go with allow_namesake" in errors
    assert "probable: true and probable_reason go together" in errors
    assert "op 5 (add_note 'd2'): the same change as op 4" in errors


def test_probable_link_is_marked_on_the_child_ref(fake):
    kid = fake.add("people", {"handle": "kid", "gender": 1, "primary_name": {"first_name": "Tom", "surname_list": [{"surname": "Example", "primary": True}]}})
    fam = fake.add("families", {"handle": "fam", "mother_handle": "anna"})
    ops = [{"op": "link_child", "key": "l", "ruling": R, "family": fam["gramps_id"], "child": kid["gramps_id"],
            "probable": True, "probable_reason": "parents from the index only"}]
    assert run(fake, ops)[0].status == "done"
    ref = fake.store["families"]["fam"]["child_ref_list"][0]
    note = fake.store["notes"][ref["note_list"][0]]
    assert note["text"]["string"] == "[probable] parents from the index only" and note["type"] == "Research"
    assert run(fake, ops)[0].status == "skip"
    # a link made earlier without its mark gets it; an existing mark with other words counts
    fake.store["families"]["fam"]["child_ref_list"][0]["note_list"] = []
    assert run(fake, [dict(ops[0], key="l2")])[0].status == "done"
    assert run(fake, [dict(ops[0], key="l3", probable_reason="other words")])[0].status == "skip"


def test_preflight_flags_likely_duplicates_before_any_write(fake):
    from gwt_apply.dupes import preflight
    from gwt_apply.client import Client
    birth = fake.add("events", {"handle": "ab", "type": "Baptism", "date": parse_date("1890-05-01")})
    fake.store["people"]["anna"]["event_ref_list"] = [{"ref": birth["handle"], "role": "Primary"}]
    ops = [
        # Anna Example, born 1891: same first name and first surname as I0001, born a year apart
        {"op": "add_person", "key": "p1", "ruling": R, "first_name": "Anna", "surname": "Example Other"},
        {"op": "add_event", "key": "e1", "ruling": R, "person": "key:p1", "type": "Birth", "date": "1891"},
        # two new people with the same name in one list
        {"op": "add_person", "key": "p2", "ruling": R, "first_name": "Rose", "surname": "Sample"},
        {"op": "add_person", "key": "p3", "ruling": R, "first_name": "rose", "surname": "Sámple"},
        # a namesake born much later, with no shared parent: not flagged
        {"op": "add_person", "key": "p4", "ruling": R, "first_name": "Anna", "surname": "Example Third"},
        {"op": "add_event", "key": "e4", "ruling": R, "person": "key:p4", "type": "Birth", "date": "1950"},
    ]
    c = Client("https://gw.example", "u", "pw", session=fake)
    problems = preflight(ops, c)
    assert len(problems) == 2, problems
    assert "'p1'" in problems[0] and "I0001" in problems[0] and "born within two years" in problems[0]
    assert "'p3'" in problems[1] and "key:p2" in problems[1] and "made by another op" in problems[1]
    ops[0].update(allow_namesake=True, namesake_reason="another baptism book", namesake_of="I0001")
    ops[3].update(allow_namesake=True, namesake_reason="twins", namesake_of="key:p2")
    assert preflight(ops, c) == []
    assert fake.writes() == []
    # a rerun after the list went in: the people it made are not duplicates of themselves
    assert [r.status for r in run(fake, ops)] == ["done"] * 6
    assert preflight(ops, c) == []


def test_update_event_fixes_the_existing_event(fake):
    ev = fake.add("events", {"handle": "b1", "type": "Birth", "date": parse_date("1890"), "description": "old"})
    ops = [{"op": "update_event", "key": "u", "ruling": R, "event": ev["gramps_id"],
            "set": {"date": "1891-02-03", "place": "P0001", "description": "fixed"}}]
    assert run(fake, ops)[0].status == "done"
    e = fake.store["events"]["b1"]
    assert (e["date"]["dateval"], e["place"], e["description"]) == ([3, 2, 1891, False], "plc1", "fixed")
    assert run(fake, ops)[0].status == "skip"


def test_citation_and_note_on_a_child_ref(fake):
    kid = fake.add("people", {"handle": "kid", "gender": 1, "primary_name": {"first_name": "Tom", "surname_list": [{"surname": "Example", "primary": True}]}})
    fake.add("families", {"handle": "fam", "mother_handle": "anna", "child_ref_list": [{"ref": "kid", "frel": "Birth", "mrel": "Birth"}]})  # F0001
    link = {"child_ref": {"family": "F0001", "child": kid["gramps_id"]}}
    ops = [
        {"op": "add_citation", "key": "c", "ruling": R, "source": "S0001", "page": "no. 9", "attach": [link, {"person": kid["gramps_id"]}]},
        {"op": "add_note", "key": "n", "ruling": R, "type": "Research", "text": "[probable] parents from the index only", "attach": [link]},
    ]
    res = run(fake, ops)
    assert [r.status for r in res] == ["done", "done"], [r.actions for r in res]
    ref = fake.store["families"]["fam"]["child_ref_list"][0]
    assert ref["citation_list"] == [stable_handle("c")] and ref["note_list"] == [stable_handle("n")]
    assert fake.store["people"]["kid"]["citation_list"] == [stable_handle("c")]
    # a note with the same text already on the child_ref is reused, by another key
    ops2 = [dict(ops[1], key="n2")]
    assert run(fake, ops2)[0].status == "skip" and len(fake.store["notes"]) == 1
    # a child that is not in the family is a conflict
    bad = [dict(ops[1], key="n3", text="other", attach=[{"child_ref": {"family": "F0001", "child": "I0001"}}])]
    res = run(fake, bad)
    assert res[0].status == "conflict" and "is not a child of" in res[0].actions[-1]
    media = {"op": "add_media", "key": "m", "ruling": R, "file": __file__, "path": "x.py", "attach": [link]}
    assert any("cannot carry" in e for e in validate([media]))


def test_place_and_namesake_matching_ignore_case_and_accents(fake):
    fake.add("places", {"handle": "pz", "name": {"value": "Mónte Alto"}, "place_type": "Locality", "placeref_list": [{"ref": "plc1"}]})
    ops = [{"op": "add_place", "key": "pl", "ruling": R, "name": "monte  alto", "type": "locality", "enclosed_by": "P0001"},
           {"op": "add_person", "key": "p", "ruling": R, "first_name": "ANNA", "surname": "Exámple"}]
    res = run(fake, ops)
    assert res[0].status == "skip"
    assert res[1].status == "conflict" and "I0001" in res[1].actions[-1]


def test_one_apply_per_tree(fake, monkeypatch):
    from gwt_apply import lock as lockmod
    from gwt_apply.lock import LOCK_HANDLE, LockHeld, TreeLock, read_lock
    c = Client("https://gw.example", "u", "pw", dry_run=False, session=fake)
    other = TreeLock(c, "run-other", retry_wait=0)
    fake.busy = 2  # "database is locked" twice: the lock waits and tries again
    other.acquire()
    mine = TreeLock(c, "run-mine")
    with pytest.raises(LockHeld, match="run-other"):
        mine.acquire()
    # an apply that holds the lock keeps it fresh; one that lost it stops
    clock = [lockmod._now()]
    monkeypatch.setattr(lockmod, "_now", lambda: clock[0])
    clock[0] += 600
    other.refresh()
    assert read_lock(c)["expires_ts"] == clock[0] + 900
    # a dead holder's lock expires and is taken over; --break-lock takes it at once
    clock[0] += 1000
    assert "took over the lock of run run-other" in mine.acquire()
    with pytest.raises(LockHeld, match="lost the lock"):
        clock[0] += 500
        other.refresh()
    TreeLock(c, "run-third").acquire(break_lock=True)
    mine.release()  # not ours any more: left alone
    assert read_lock(c)["run"] == "run-third"
    TreeLock(c, "run-third").release()
    assert LOCK_HANDLE not in fake.store["notes"]


def test_cli_takes_the_lock_releases_it_and_never_reuses_a_log(fake, tmp_path, monkeypatch, capsys):
    import gwt_apply.client as clientmod
    from gwt_apply.lock import LOCK_HANDLE, TreeLock
    monkeypatch.setattr(clientmod.requests, "Session", lambda: fake)
    for k, v in {"GWT_URL": "https://gw.example", "GWT_USERNAME": "u", "GWT_PASSWORD": "pw", "GWT_TREE": "tree-test"}.items():
        monkeypatch.setenv(k, v)
    p = tmp_path / "changes.jsonl"
    p.write_text(json.dumps({"op": "add_note", "key": "n", "ruling": R, "text": "x", "attach": [{"person": "I0001"}]}) + "\n")
    log = tmp_path / "apply.jsonl"
    log.write_text("an older run\n")
    assert main([str(p), "--apply", "--log", str(log)]) == 0
    assert main([str(p), "--apply", "--log", str(log)]) == 0
    assert log.read_text() == "an older run\n"  # never touched
    runs = sorted(tmp_path.glob("apply.*.jsonl"))
    assert len(runs) == 2 and all(r.read_text() for r in runs)
    assert LOCK_HANDLE not in fake.store["notes"]  # released after each run
    assert any(c == ("POST", "/api/objects/") for c in fake.calls)
    # while another apply holds the tree, an apply writes nothing
    TreeLock(Client("https://gw.example", "u", "pw", dry_run=False, session=fake), "run-busy").acquire()
    before = len(fake.writes())
    p.write_text(json.dumps({"op": "add_note", "key": "n2", "ruling": R, "text": "y", "attach": [{"person": "I0001"}]}) + "\n")
    assert main([str(p), "--apply", "--log", str(log)]) == 1
    assert "another apply holds this tree: run run-busy" in capsys.readouterr().err
    assert fake.writes()[before:] == []


def test_lookups_are_read_once_per_run_not_once_per_op(fake):
    def whole_class_reads(n):
        f = fake.__class__()
        f.store = json.loads(json.dumps(fake.store))
        seen = []
        request = f.request

        def counting(method, url, params=None, **kw):
            if method == "GET" and "keys" in (params or {}):
                seen.append(url)
            return request(method, url, params=params, **kw)

        f.request = counting
        ops = [
            {"op": "add_event", "key": f"r{i}", "ruling": R, "person": "I0001", "type": "Residence", "date": f"{1900 + i}"}
            for i in range(n)
        ]
        assert all(r.status == "done" for r in run(f, ops))
        return len(seen)

    assert whole_class_reads(5) == whole_class_reads(1)


class NoServer:
    def request(self, *a, **kw):
        raise AssertionError("a dry run on a snapshot must not reach the server")


@pytest.fixture
def env(monkeypatch, fake, tmp_path):
    import gwt_apply.client as clientmod
    monkeypatch.chdir(tmp_path)  # the default log lands here
    monkeypatch.setattr(clientmod.requests, "Session", lambda: fake)
    for k, v in {"GWT_URL": "https://gw.example", "GWT_USERNAME": "u", "GWT_PASSWORD": "pw", "GWT_TREE": "tree-test"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("gwt_apply.snapshot.PAGE", 2)  # several pages even in a small tree


def test_snapshot_dry_run_needs_no_server_and_plans_like_the_server(fake, scan, tmp_path, env, monkeypatch, capsys):
    snap = tmp_path / "tree.json.gz"
    assert main(["--save-snapshot", str(snap)]) == 0
    assert main(["--save-snapshot", str(snap)]) == 2  # never overwritten
    changes = tmp_path / "changes.jsonl"
    changes.write_text("\n".join(json.dumps(op) for op in full_list(scan)))
    live = run(fake, full_list(scan), dry_run=True)
    import gwt_apply.client as clientmod
    monkeypatch.setattr(clientmod.requests, "Session", lambda: NoServer())
    for k in ("GWT_URL", "GWT_USERNAME", "GWT_PASSWORD"):
        monkeypatch.delenv(k)
    capsys.readouterr()
    assert main([str(changes), "--snapshot", str(snap)]) == 0
    out = capsys.readouterr().out
    for r in live:  # the same plan, op by op
        assert f"[{r.status}] {r.key}" in out
        for a in r.actions:
            assert a in out


def test_apply_on_a_snapshot_refuses_what_changed_since(fake, scan, tmp_path, env, capsys):
    snap = tmp_path / "tree.json.gz"
    assert main(["--save-snapshot", str(snap)]) == 0
    changes = tmp_path / "changes.jsonl"
    changes.write_text("\n".join(json.dumps(op) for op in full_list(scan)))
    # someone renames Anna after the snapshot: the plan updates her, so the apply refuses
    fake.store["people"]["anna"].update(gender=1, change=fake.tick())
    before = len(fake.writes())
    assert main([str(changes), "--snapshot", str(snap), "--apply"]) == 1
    err = capsys.readouterr().err
    assert "Person I0001 changed after the snapshot" in err and "take a new one" in err
    from gwt_apply.lock import LOCK_HANDLE
    assert fake.writes()[before:] == [("POST", "/api/objects/"), ("DELETE", f"/api/notes/{LOCK_HANDLE}")]  # the lock only
    # a new person after the snapshot: the plan searched every person for namesakes
    snap2 = tmp_path / "tree2.json.gz"
    assert main(["--save-snapshot", str(snap2)]) == 0
    fake.add("people", {"handle": "late", "primary_name": {"first_name": "Late", "surname_list": [{"surname": "Comer"}]}})
    assert main([str(changes), "--snapshot", str(snap2), "--apply"]) == 1
    assert "1 Person object(s) added" in capsys.readouterr().err


def test_apply_on_a_current_snapshot_reads_almost_nothing(fake, scan, tmp_path, env, capsys):
    snap = tmp_path / "tree.json.gz"
    assert main(["--save-snapshot", str(snap)]) == 0
    changes = tmp_path / "changes.jsonl"
    changes.write_text("\n".join(json.dumps(op) for op in full_list(scan)))
    start = len(fake.calls)
    assert main([str(changes), "--snapshot", str(snap), "--apply"]) == 0, capsys.readouterr()
    calls = fake.calls[start:]
    gets = [c for c in calls if c[0] == "GET"]
    lists = [c for c in gets if c[1].endswith("/")]
    assert len(lists) == 6  # the check: one read of handles and stamps per class the plan touched
    # single reads: the stamp check before each PUT, and objects this run wrote, which the snapshot does not have
    # (Anna changed when her new family was written, so the update of her name reads her and checks her again)
    assert [c for c in gets if c[1] in ("/api/places/plc1", "/api/sources/src1")] == []
    assert gets.count(("GET", "/api/people/anna")) == 2
    john = fake.store["people"][stable_handle("p-john")]
    assert john["birth_ref_index"] == 0 and len(john["citation_list"]) == 1


def test_an_object_changed_during_a_snapshot_apply_stops_it(fake, tmp_path, env, capsys):
    from gwt_apply.client import Client, Stale
    from gwt_apply import snapshot
    c = Client("https://gw.example", "u", "pw", tree="tree-test", dry_run=False, session=fake)
    c.login()
    snapshot.save(c, str(tmp_path / "s.json.gz"))
    c.use_snapshot(snapshot.load(str(tmp_path / "s.json.gz")))
    fake.store["people"]["anna"].update(gender=1, change=fake.tick())
    ops = [{"op": "set_attribute", "key": "a", "ruling": R, "target": {"person": "I0001"}, "type": "Nickname", "value": "Nan"}]
    res = Applier(c, retries=1, retry_wait=0).run(ops)
    assert res[0].status == "error" and "changed since the snapshot" in res[0].actions[-1]
    assert fake.store["people"]["anna"]["attribute_list"] == []


def test_apply_on_a_snapshot_survives_a_lost_answer(fake, tmp_path, env):
    snap = tmp_path / "tree.json.gz"
    assert main(["--save-snapshot", str(snap)]) == 0
    changes = tmp_path / "changes.jsonl"
    op = {"op": "add_event", "key": "e", "ruling": R, "person": "I0001", "type": "Death", "date": "abt 1950"}
    changes.write_text(json.dumps(op))
    fake.drop_after = ("POST", "/api/objects/", 1)  # after the lock note, the event's create: made, answer lost
    assert main([str(changes), "--snapshot", str(snap), "--apply", "--retry-wait", "0"]) == 0
    assert len(fake.store["events"]) == 1
    assert fake.store["people"]["anna"]["death_ref_index"] == 0


def test_lock_whose_answer_was_lost_is_still_ours(fake, tmp_path, env):
    changes = tmp_path / "changes.jsonl"
    op = {"op": "add_event", "key": "e", "ruling": R, "person": "I0001", "type": "Death", "date": "abt 1950"}
    changes.write_text(json.dumps(op))
    fake.drop_after = ("POST", "/api/objects/")  # the lock note's create
    assert main([str(changes), "--apply", "--retry-wait", "0"]) == 0
    assert len(fake.store["events"]) == 1 and fake.store["notes"] == {}


def test_lock_is_kept_fresh_while_one_op_waits_on_a_busy_server(fake, monkeypatch):
    from gwt_apply import lock as lockmod
    import gwt_apply.ops as opsmod
    from gwt_apply.lock import TreeLock, read_lock
    clock = [lockmod._now()]
    monkeypatch.setattr(lockmod, "_now", lambda: clock[0])
    monkeypatch.setattr(opsmod.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + 600))  # a long wait
    c = Client("https://gw.example", "u", "pw", tree="tree-test", dry_run=False, session=fake)
    lock = TreeLock(c, "run-mine")
    lock.acquire()
    fake.busy = 1
    op = {"op": "add_event", "key": "e", "ruling": R, "person": "I0001", "type": "Death", "date": "abt 1950"}
    res = Applier(c, retries=3, retry_wait=1, lock=lock).run([op])
    assert res[0].status == "done", res[0].actions
    assert read_lock(c)["expires_ts"] == clock[0] + 900  # refreshed after the wait, before the second try


def test_login_that_times_out_is_retried(fake):
    import requests
    request, timeouts = fake.request, [1]

    def flaky(method, url, **kw):
        if url.endswith("/token/") and timeouts[0]:
            timeouts[0] -= 1
            raise requests.Timeout("no answer")
        return request(method, url, **kw)

    fake.request = flaky
    c = Client("https://gw.example", "u", "pw", tree="tree-test", dry_run=False, session=fake)
    op = {"op": "add_event", "key": "e", "ruling": R, "person": "I0001", "type": "Death", "date": "abt 1950"}
    res = Applier(c, retries=2, retry_wait=0).run([op])
    assert res[0].status == "done", res[0].actions


class MergingFake(FakeGrampsWeb):
    """The fake API plus the merge endpoints, doing what Gramps' merge does to the lists the tests look at."""

    def _route(self, method, path, headers, params, body, data):
        parts = [p for p in path.split("/") if p][1:]
        if method == "POST" and len(parts) == 4 and parts[2] == "merge":
            ns, w, l_ = parts[0], parts[1], parts[3]
            store = self.store[ns]
            win, lose = store[w], store.pop(l_)
            for lst in ("event_ref_list", "citation_list", "note_list", "child_ref_list", "family_list", "parent_family_list"):
                win[lst] = win.get(lst, []) + [x for x in lose.get(lst, []) if x not in win.get(lst, [])]
            if ns == "people":
                win["attribute_list"].append({"_class": "Attribute", "type": "Merged Gramps ID", "value": lose["gramps_id"]})
                for fam in self.store["families"].values():
                    for role in ("father_handle", "mother_handle"):
                        if fam.get(role) == l_:
                            fam[role] = w
                    for ref in fam.get("child_ref_list", []):
                        if ref["ref"] == l_:
                            ref["ref"] = w
            else:
                for p in self.store["people"].values():
                    for lst in ("family_list", "parent_family_list"):
                        p[lst] = [w if x == l_ else x for x in p.get(lst, [])]
            win["change"] = self.tick()
            return Resp(200, {})
        return super()._route(method, path, headers, params, body, data)


def test_merge_person_moves_everything_and_notes_it(scan, tmp_path):
    fake = MergingFake()
    fake.add("people", {"handle": "anna", "gender": 0, "primary_name": {"first_name": "Anna", "surname_list": [{"surname": "Example", "primary": True}]}})
    dup = fake.add("people", {"handle": "anna2", "gender": 0, "primary_name": {"first_name": "Ana", "surname_list": [{"surname": "Example", "primary": True}]}})
    b = fake.add("events", {"handle": "b2", "type": "Birth", "date": parse_date("1890")})
    dup["event_ref_list"] = [{"_class": "EventRef", "ref": "b2", "role": "Primary"}]
    dup["birth_ref_index"] = 0
    kid = fake.add("people", {"handle": "kid", "primary_name": {"first_name": "Tom", "surname_list": [{"surname": "Example"}]}})
    fake.add("people", {"handle": "bob", "gender": 1, "primary_name": {"first_name": "Bob", "surname_list": [{"surname": "Sample"}]}})
    fake.add("families", {"handle": "fam", "mother_handle": "anna2", "father_handle": "bob", "child_ref_list": [{"ref": "kid", "frel": "Birth", "mrel": "Birth"}]})
    fake.add("families", {"handle": "famw", "mother_handle": "anna", "father_handle": "bob"})
    op = {"op": "merge_person", "key": "mp", "ruling": R, "winner": "I0001", "loser": "I0002", "use_loser": ["birth"],
          "reason": "same baptism entry"}
    plan = run(fake, [op], dry_run=True)
    assert plan[0].status == "done" and fake.writes() == []
    text = "\n".join(plan[0].actions)
    assert "merge Person I0002 into I0001" in text and '"ref": "b2"' in text
    assert "Family F0001: mother I0002 -> I0001" in text and "delete Person I0002" in text
    assert "Family F0001 merges into Family F0002: the same partner" in text
    assert "birth_ref_index: -1 -> 0" in text
    res = run(fake, [op])
    assert res[0].status == "done", res[0].actions
    anna = fake.store["people"]["anna"]
    assert "anna2" not in fake.store["people"] and fake.store["families"]["fam"]["mother_handle"] == "anna"
    assert anna["event_ref_list"][0]["ref"] == "b2" and anna["birth_ref_index"] == 0
    note = fake.store["notes"][stable_handle("mp#merge")]
    assert note["text"]["string"].startswith("Merged Person I0002 (Ana Example) into this person (ruling ruling-0001). same baptism")
    assert note["handle"] in anna["note_list"]
    assert run(fake, [op])[0].status == "skip"  # a rerun finds the merge done


def test_merge_whose_answer_was_lost_is_not_done_twice_and_keeps_the_birth():
    fake = MergingFake()
    fake.add("people", {"handle": "anna", "gender": 0, "primary_name": {"first_name": "Anna", "surname_list": [{"surname": "Example", "primary": True}]}})
    dup = fake.add("people", {"handle": "anna2", "gender": 0, "primary_name": {"first_name": "Ana", "surname_list": [{"surname": "Example", "primary": True}]}})
    fake.add("events", {"handle": "b2", "type": "Birth", "date": parse_date("1890")})
    dup["event_ref_list"] = [{"_class": "EventRef", "ref": "b2", "role": "Primary"}]
    dup["birth_ref_index"] = 0
    fake.drop_after = ("POST", "/api/people/anna/merge/")
    op = {"op": "merge_person", "key": "mp", "ruling": R, "winner": "I0001", "loser": "I0002", "use_loser": ["birth"]}
    res = run(fake, [op])
    assert res[0].status == "skip", res[0].actions  # the retry finds the merge done
    assert sum(1 for m, p in fake.calls if m == "POST" and "/merge/" in p) == 1
    anna = fake.store["people"]["anna"]
    assert "anna2" not in fake.store["people"] and anna["birth_ref_index"] == 0
    assert anna["event_ref_list"][0]["ref"] == "b2"


def test_merge_family_needs_the_same_parents(tmp_path):
    fake = MergingFake()
    for h, n in (("dad", "John"), ("dad2", "Jon"), ("mum", "Mary"), ("kid", "Tom")):
        fake.add("people", {"handle": h, "primary_name": {"first_name": n, "surname_list": [{"surname": "Example"}]}})
    fake.add("families", {"handle": "f1", "father_handle": "dad", "mother_handle": "mum"})
    fake.add("families", {"handle": "f2", "mother_handle": "mum", "child_ref_list": [{"ref": "kid", "frel": "Birth", "mrel": "Birth"}]})
    fake.add("families", {"handle": "f3", "father_handle": "dad2", "mother_handle": "mum"})
    res = run(fake, [{"op": "merge_family", "key": "mf", "ruling": R, "winner": "F0001", "loser": "F0003"}])
    assert res[0].status == "conflict" and "different fathers" in res[0].actions[-1]
    res = run(fake, [{"op": "merge_family", "key": "mf2", "ruling": R, "winner": "F0001", "loser": "F0002"}])
    assert res[0].status == "done", res[0].actions
    assert "f2" not in fake.store["families"]
    assert fake.store["families"]["f1"]["child_ref_list"][0]["ref"] == "kid"
    assert fake.store["people"]["kid"]["parent_family_list"] == ["f1"]
    assert "winner and loser are the same" in "\n".join(validate([{"op": "merge_family", "key": "x", "ruling": R, "winner": "F1", "loser": "F1"}]))


def test_run_and_op_tags_go_on_every_object_created(fake, scan):
    fake.add("tags", {"handle": "tagold", "name": "r12"})
    ops = full_list(scan)
    ops[1]["tag"] = "r12-weak"
    client = Client("https://gw.example", "u", "pw", tree="tree-test", dry_run=False, session=fake)
    res = Applier(client, retries=1, retry_wait=0, tag="r12").run(ops)
    assert all(r.status == "done" for r in res), [r.actions for r in res]
    weak = stable_handle("tag:r12-weak")
    assert fake.store["tags"][weak]["name"] == "r12-weak"  # made once, when first needed
    assert len(fake.store["tags"]) == 2  # the existing r12 is reused by name
    created = [fake.store[ns][stable_handle(k)] for ns, k in
               [("people", "p-john"), ("families", "f-anna"), ("citations", "c-bapt"), ("media", "m-bapt"), ("notes", "n-john")]]
    assert all(o["tag_list"] == ["tagold"] for o in created)
    assert fake.store["events"][stable_handle("e-john-birth")]["tag_list"] == ["tagold", weak]
    assert fake.store["people"]["anna"].get("tag_list", []) == []  # changed objects are not tagged, only new ones
    assert any("tag is a non-empty name" in e for e in validate([dict(ops[0], key="x", tag="")]))


def test_tag_whose_answer_was_lost_is_made_once(fake):
    fake.drop_after = ("POST", "/api/objects/")  # the tag's create, the first write of the run
    client = Client("https://gw.example", "u", "pw", tree="tree-test", dry_run=False, session=fake)
    op = {"op": "add_event", "key": "e", "ruling": R, "person": "I0001", "type": "Death", "date": "abt 1950"}
    res = Applier(client, retries=2, retry_wait=0, tag="r12").run([op])
    assert res[0].status == "done", res[0].actions
    assert list(fake.store["tags"]) == [stable_handle("tag:r12")]
    assert fake.store["events"][stable_handle("e")]["tag_list"] == [stable_handle("tag:r12")]


def test_run_tag_is_checked_against_the_snapshot_and_never_empty(fake, scan, tmp_path, env, capsys):
    snap = tmp_path / "tree.json.gz"
    assert main(["--save-snapshot", str(snap)]) == 0
    changes = tmp_path / "changes.jsonl"
    changes.write_text("\n".join(json.dumps(op) for op in full_list(scan)))
    assert main([str(changes), "--tag", "  "]) == 2
    # the run's tag is made by hand after the snapshot: the plan would make it again, so the apply refuses
    fake.add("tags", {"handle": "late-tag", "name": "r12"})
    before = len(fake.writes())
    assert main([str(changes), "--snapshot", str(snap), "--apply", "--tag", "r12"]) == 1
    assert "1 Tag object(s) added" in capsys.readouterr().err
    from gwt_apply.lock import LOCK_HANDLE
    assert fake.writes()[before:] == [("POST", "/api/objects/"), ("DELETE", f"/api/notes/{LOCK_HANDLE}")]  # the lock only


def test_a_tag_renamed_after_the_snapshot_stops_the_apply(fake, scan, tmp_path, env, capsys):
    fake.add("tags", {"handle": "tag-r12", "name": "r12", "change": fake.tick()})
    snap = tmp_path / "tree.json.gz"
    assert main(["--save-snapshot", str(snap)]) == 0
    changes = tmp_path / "changes.jsonl"
    changes.write_text("\n".join(json.dumps(op) for op in full_list(scan)))
    fake.store["tags"]["tag-r12"].update(name="r13", change=fake.tick())
    assert main([str(changes), "--snapshot", str(snap), "--apply", "--tag", "r12"]) == 1
    assert "Tag" in capsys.readouterr().err


def test_dna_association_with_its_segment_note_and_citation(fake):
    segments = "Chromosome,Start Location,End Location,Centimorgans,Matching SNPs\n7,1000,90000,12.5,1500"
    ops = [
        {"op": "add_person", "key": "m1", "ruling": R, "first_name": "Match", "surname": "Kit", "tag": ["dna-match", "dna-small"]},
        {"op": "add_association", "key": "a1", "ruling": R, "person": "I0001", "target": "key:m1", "relation": "DNA"},
        {"op": "add_note", "key": "n1", "ruling": R, "text": segments,
         "attach": [{"association": {"person": "I0001", "target": "key:m1"}}]},
        {"op": "add_citation", "key": "c1", "ruling": R, "source": "S0001", "page": "match list, kit A",
         "attach": [{"association": {"person": "I0001", "target": "key:m1", "relation": "DNA"}}]},
    ]
    res = run(fake, ops)
    assert all(r.status == "done" for r in res), [r.actions for r in res]
    ref = fake.store["people"]["anna"]["person_ref_list"]
    assert len(ref) == 1 and ref[0]["ref"] == stable_handle("m1") and ref[0]["rel"] == "DNA"
    assert ref[0]["note_list"] == [stable_handle("n1")] and ref[0]["citation_list"] == [stable_handle("c1")]
    assert fake.store["notes"][stable_handle("n1")]["text"]["string"] == segments  # exactly as given
    match = fake.store["people"][stable_handle("m1")]
    assert len(match["tag_list"]) == 2
    assert all(r.status == "skip" for r in run(fake, ops))  # a rerun changes nothing


def test_association_checks():
    def errs(**kw):
        return "\n".join(validate([{"op": "add_association", "key": "a", "ruling": R, **kw}]))
    assert "missing ['relation']" in errs(person="I1", target="I2")
    assert "the same" in errs(person="I1", target="I1", relation="DNA")
    assert "never a handle" in errs(person="I1", target="f3a9e1c27b4d", relation="DNA")
    assert "no attributes" in "\n".join(validate([{"op": "set_attribute", "key": "s", "ruling": R, "type": "X", "value": "y",
                                                     "target": {"association": {"person": "I1", "target": "I2"}}}]))
    assert "cannot carry" in "\n".join(validate([{"op": "add_media", "key": "m", "ruling": R, "file": __file__, "path": "x.py",
                                                    "attach": [{"association": {"person": "I1", "target": "I2"}}]}]))


def test_association_reads_no_whole_class_per_op(fake):
    for i in range(3):
        fake.add("people", {"handle": f"k{i}", "primary_name": {"first_name": f"K{i}", "surname_list": [{"surname": "Kit"}]}})
    seen, request = [], fake.request

    def counting(method, url, params=None, **kw):
        if method == "GET" and "keys" in (params or {}):
            seen.append(url)
        return request(method, url, params=params, **kw)

    fake.request = counting
    ops = [{"op": "add_association", "key": f"a{i}", "ruling": R, "person": "I0001", "target": f"I000{i + 2}",
            "relation": "DNA"} for i in range(3)]
    assert all(r.status == "done" for r in run(fake, ops))
    assert len(seen) == 1  # the people index, once


def test_transcription_is_a_transcript_note_with_a_required_margin_reading(fake):
    base = {"op": "add_transcription", "key": "t", "ruling": R, "text": "Joseph, son of Anna.",
            "attach": [{"person": "I0001"}]}
    assert any("margin_reading" in e for e in validate([base]))
    assert any("margin_reading" in e for e in validate([dict(base, margin_reading="  ")]))
    ok = dict(base, margin_reading="none", language="es")
    assert validate([ok]) == []
    assert run(fake, [ok])[0].status == "done"
    note = next(iter(fake.store["notes"].values()))
    assert note["type"] == "Transcript"
    assert note["text"]["string"].endswith("Margin reading [es]: none")
    assert fake.store["people"]["anna"]["note_list"] == [stable_handle("t")]
    assert run(fake, [dict(ok, key="t2")])[0].status == "skip"  # same text on the same person: reused
    media = dict(ok, key="m", attach=[{"child_ref": {"family": "F0001", "child": "I0001"}}])
    assert validate([media]) == []


def test_attach_existing_links_an_orphan_and_is_a_skip_the_second_time():
    fake = FakeGrampsWeb()
    fake.add("people", {"handle": "anna", "gender": 0, "primary_name": {"first_name": "Anna", "surname_list": [{"surname": "Example", "primary": True}]}})  # I0001
    fake.add("citations", {"handle": "cit", "source_handle": "src1", "page": "p. 1"})                  # C0001
    fake.add("media", {"handle": "med", "path": "a.jpg"})                                             # O0001
    fake.add("notes", {"handle": "nt", "text": {"string": "x"}})                                       # N0001
    ops = [
        {"op": "attach_existing", "key": "a1", "ruling": R, "object": {"citation": "C0001"}, "attach": [{"person": "I0001"}]},
        {"op": "attach_existing", "key": "a2", "ruling": R, "object": {"media": "O0001"}, "attach": [{"citation": "C0001"}]},
        {"op": "attach_existing", "key": "a3", "ruling": R, "object": {"note": "N0001"}, "attach": [{"person": "I0001"}]},
    ]
    assert validate(ops) == []
    plan = run(fake, ops, dry_run=True)
    assert [r.status for r in plan] == ["done"] * 3 and fake.writes() == []
    assert [r.status for r in run(fake, ops)] == ["done"] * 3
    assert fake.store["people"]["anna"]["citation_list"] == ["cit"]
    assert fake.store["people"]["anna"]["note_list"] == ["nt"]
    assert fake.store["citations"]["cit"]["media_list"][0]["ref"] == "med"
    assert [r.status for r in run(fake, [dict(o, key=o["key"] + "b") for o in ops])] == ["skip"] * 3
    fake.add("media", {"handle": "scan", "gramps_id": "D051-b", "path": "b.jpg"})  # a suffix an import added
    op = {"op": "attach_existing", "key": "a4", "ruling": R, "object": {"media": "D051-b"}, "attach": [{"person": "I0001"}]}
    assert validate([op]) == [] and run(fake, [op])[0].status == "done"
    assert fake.store["people"]["anna"]["media_list"][0]["ref"] == "scan"


def test_attach_existing_refuses_what_cannot_be_attached_or_carried():
    bad = [
        {"op": "attach_existing", "key": "b1", "ruling": R, "object": {"person": "I0001"}, "attach": [{"person": "I0002"}]},
        {"op": "attach_existing", "key": "b2", "ruling": R, "object": {"media": "O0001"}, "attach": [{"child_ref": {"family": "F0001", "child": "I0001"}}]},
        {"op": "attach_existing", "key": "b3", "ruling": R, "object": {"media": "0123b4e9269542049080d7a4c798"}, "attach": [{"person": "I0001"}]},
        {"op": "attach_existing", "key": "b4", "ruling": R, "object": {"note": "N0001"}},
        {"op": "set_attribute", "key": "b5", "ruling": R, "target": {"note": "N0001"}, "type": "x", "value": "y"},
    ]
    errors = "\n".join(validate(bad))
    assert "object is a citation, media or note, got person" in errors
    assert "cannot carry a media" in errors
    assert "neither key:<key> nor a Gramps ID" in errors
    assert "missing ['attach']" in errors
    assert "bad target {'note': 'N0001'}" in errors  # a note is an object to attach, never a target
    fake = FakeGrampsWeb()
    res = run(fake, [{"op": "attach_existing", "key": "c", "ruling": R, "object": {"note": "N0099"}, "attach": [{"person": "I0099"}]}])
    assert res[0].status == "conflict"


def test_dedupe_list_keeps_the_first_of_a_repeated_reference():
    fake = FakeGrampsWeb()
    fake.add("people", {"handle": "anna", "gender": 0, "citation_list": ["c1", "c2", "c1"], "primary_name": {"first_name": "Anna", "surname_list": [{"surname": "Example", "primary": True}]}})
    op = {"op": "dedupe_list", "key": "d", "ruling": R, "target": {"person": "I0001"}, "list": "citation_list"}
    assert validate([op]) == []
    assert run(fake, [op], dry_run=True)[0].status == "done" and fake.writes() == []
    assert run(fake, [op])[0].status == "done"
    assert fake.store["people"]["anna"]["citation_list"] == ["c1", "c2"]
    assert run(fake, [dict(op, key="d2")])[0].status == "skip"
    assert any("list is one of" in e for e in validate([dict(op, list="event_ref_list")]))
    assert any("has no media_list" in e for e in validate([dict(op, target={"child_ref": {"family": "F0001", "child": "I0001"}}, list="media_list")]))


def test_dedupe_list_keys_a_media_ref_on_its_region_and_keeps_what_a_repeat_carries():
    fake = FakeGrampsWeb()
    whole, face = {"ref": "m1", "rect": None}, {"ref": "m1", "rect": [10, 10, 40, 40]}
    fake.add("people", {"handle": "anna", "gender": 0, "media_list": [whole, face, dict(face)],
                        "primary_name": {"first_name": "Anna", "surname_list": [{"surname": "Example", "primary": True}]}})
    op = {"op": "dedupe_list", "key": "d", "ruling": R, "target": {"person": "I0001"}, "list": "media_list"}
    assert run(fake, [op])[0].status == "done"
    assert fake.store["people"]["anna"]["media_list"] == [whole, face]  # a second region of one photo stays
    fake.store["people"]["anna"]["media_list"].append(dict(face, note_list=["n1"]))
    res = run(fake, [dict(op, key="d2")])
    assert res[0].status == "conflict" and "note_list" in res[0].actions[-1]
    assert len(fake.store["people"]["anna"]["media_list"]) == 3


def test_a_tag_the_server_says_it_made_but_does_not_have_stops_the_op_loudly(fake):
    real = fake._route

    def forgetful(method, path, headers, params, body, data):
        if method == "POST" and path.endswith("/objects/") and body and body[0].get("_class") == "Tag":
            return Resp(201, [{"type": "add", "handle": body[0]["handle"]}])  # says yes, keeps nothing
        return real(method, path, headers, params, body, data)

    fake._route = forgetful
    op = {"op": "add_note", "key": "n", "ruling": R, "text": "x", "attach": [{"person": "I0001"}], "tag": "batch-a"}
    res = run(fake, [op])
    assert res[0].status == "error" and "is not there when read back" in res[0].actions[-1]
    assert fake.store["notes"] == {}  # nothing was tagged with a handle that does not exist


def test_a_gramps_id_may_carry_a_suffix_but_a_handle_is_still_refused():
    note = {"op": "add_note", "key": "n", "ruling": R, "text": "x"}
    ok = validate([dict(note, attach=[{"person": "I0001-b"}])])
    assert ok == []
    bad = validate([dict(note, attach=[{"person": "0123b4e9269542049080d7a4c798"}])])
    assert any("neither key:<key> nor a Gramps ID" in e for e in bad)
    fake = FakeGrampsWeb()
    fake.add("people", {"handle": "anna", "gender": 0, "gramps_id": "I0001-b", "primary_name": {"first_name": "Anna", "surname_list": [{"surname": "Example", "primary": True}]}})
    assert run(fake, [dict(note, attach=[{"person": "I0001-b"}])])[0].status == "done"
    fake.add("media", {"handle": "scan", "gramps_id": "D051-b", "path": "scan.jpg", "desc": "scan"})
    assert run(fake, [dict(note, key="m", attach=[{"media": "D051-b"}])])[0].status == "done"
