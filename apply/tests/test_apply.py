import json

import pytest

from fake_api import FakeGrampsWeb
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


def test_lock_whose_answer_was_lost_is_still_ours(fake, tmp_path, monkeypatch):
    import gwt_apply.client as clientmod
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(clientmod.requests, "Session", lambda: fake)
    for k, v in {"GWT_URL": "https://gw.example", "GWT_USERNAME": "u", "GWT_PASSWORD": "pw", "GWT_TREE": "tree-test"}.items():
        monkeypatch.setenv(k, v)
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
