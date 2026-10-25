import json
from urllib.parse import urlparse

import pytest
import requests

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


@pytest.fixture(autouse=True)
def state_home(tmp_path, monkeypatch):
    """Where a real run's default log goes: never the user's own ~/.local/state."""
    d = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(d))
    return d


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


def test_add_repository_and_link_it_to_a_source(fake):
    ops = [
        {"op": "add_repository", "key": "r-archive", "ruling": R, "name": "Example County Archives", "type": "Archive"},
        {"op": "link_repository", "key": "lk", "ruling": R, "source": "S0001", "repository": "key:r-archive",
         "call_number": "5MI149/R13"},
    ]
    res = run(fake, ops)
    assert [r.status for r in res] == ["done", "done"], [r.actions for r in res]
    repo = fake.store["repositories"][stable_handle("r-archive")]
    assert (repo["name"], repo["type"]) == ("Example County Archives", "Archive")
    src = fake.store["sources"]["src1"]
    assert src["reporef_list"] == [{"_class": "RepoRef", "ref": repo["handle"], "call_number": "5MI149/R13"}]
    assert [r.status for r in run(fake, ops)] == ["skip", "skip"]  # nothing is made or linked twice
    again = [{"op": "add_repository", "key": "r-other-key", "ruling": R, "name": "example county archives", "type": "Archive"}]
    assert [r.status for r in run(fake, again)] == ["skip"]  # same name, found by name
    assert len(fake.store["repositories"]) == 1


def test_link_repository_needs_an_existing_repository(fake):
    ops = [{"op": "link_repository", "key": "lk", "ruling": R, "source": "S0001", "repository": "R0099"}]
    assert [r.status for r in run(fake, ops)] == ["conflict"]


def _two_repos(fake):
    fake.add("repositories", {"handle": "repo1", "name": "Example County Archives", "type": "Archive", "urls": [], "address_list": [], "note_list": []})  # R0001
    fake.add("repositories", {"handle": "repo2", "name": "County Archive (old name)", "type": "Library", "urls": [{"path": "https://old.example", "type": "Web Link", "desc": ""}], "address_list": [], "note_list": []})  # R0002
    fake.add("sources", {"handle": "src2", "title": "Parish register of Shelbyville", "reporef_list": [{"ref": "repo2", "call_number": "B7", "media_type": "Book", "note_list": []}]})  # S0002


def test_update_repository_edits_fields_and_is_idempotent(fake):
    _two_repos(fake)
    ops = [{"op": "update_repository", "key": "u", "ruling": R, "repository": "R0001",
            "set": {"name": "Example County Record Office", "type": "Library", "url": "https://records.example",
                    "address": {"street": "1 Fake Street", "city": "Springfield", "country": "Nowhere"}}}]
    assert [r.status for r in run(fake, ops)] == ["done"]
    repo = fake.store["repositories"]["repo1"]
    assert (repo["name"], repo["type"]) == ("Example County Record Office", "Library")
    assert [u["path"] for u in repo["urls"]] == ["https://records.example"]
    assert repo["address_list"][0]["street"] == "1 Fake Street"
    assert [r.status for r in run(fake, ops)] == ["skip"]
    assert len(fake.store["repositories"]["repo1"]["urls"]) == 1
    clash = [{"op": "update_repository", "key": "u2", "ruling": R, "repository": "R0001",
              "set": {"name": "county archive (OLD name)"}}]
    assert [r.status for r in run(fake, clash)] == ["conflict"]  # another repository has that name


def test_update_repository_dry_run_writes_nothing(fake):
    _two_repos(fake)
    ops = [{"op": "update_repository", "key": "u", "ruling": R, "repository": "R0001", "set": {"type": "Church"}}]
    res = run(fake, ops, dry_run=True)
    assert [r.status for r in res] == ["done"] and fake.writes() == []
    assert any("type" in a for a in res[0].actions)


def test_link_repository_updates_call_number_and_media_type_then_unlinks(fake):
    _two_repos(fake)
    link = [{"op": "link_repository", "key": "l", "ruling": R, "source": "S0001", "repository": "R0001",
             "call_number": "A1", "media_type": "Manuscript"}]
    assert [r.status for r in run(fake, link)] == ["done"]
    ref = fake.store["sources"]["src1"]["reporef_list"][0]
    assert (ref["call_number"], ref["media_type"]) == ("A1", "Manuscript")
    assert [r.status for r in run(fake, link)] == ["skip"]
    fix = [{"op": "link_repository", "key": "l2", "ruling": R, "source": "S0001", "repository": "R0001", "call_number": "A2"}]
    assert [r.status for r in run(fake, fix)] == ["done"]  # an existing link takes the new call number, no second link
    refs = fake.store["sources"]["src1"]["reporef_list"]
    assert len(refs) == 1 and refs[0]["call_number"] == "A2" and refs[0]["media_type"] == "Manuscript"
    off = [{"op": "unlink_repository", "key": "x", "ruling": R, "source": "S0001", "repository": "R0001"}]
    assert [r.status for r in run(fake, off)] == ["done"]
    assert fake.store["sources"]["src1"]["reporef_list"] == []
    assert [r.status for r in run(fake, off)] == ["skip"]
    assert "R0001" in fake.by_id("repositories", "R0001")["gramps_id"]  # the repository itself stays


def test_merge_repository_moves_sources_and_removes_the_loser(fake):
    _two_repos(fake)
    fake.by_id("sources", "S0001")["reporef_list"] = [{"ref": "repo1", "call_number": "", "media_type": "Book", "note_list": []}]
    fake.by_id("sources", "S0001")["reporef_list"].append({"ref": "repo2", "call_number": "C9", "media_type": "Book", "note_list": []})
    ops = [{"op": "merge_repository", "key": "m", "ruling": R, "winner": "R0001", "loser": "R0002", "reason": "same archive"}]
    res = run(fake, ops)
    assert [r.status for r in res] == ["done"], res[0].actions
    assert "repo2" not in fake.store["repositories"]
    assert [r["ref"] for r in fake.store["sources"]["src2"]["reporef_list"]] == ["repo1"]
    assert fake.store["sources"]["src2"]["reporef_list"][0]["call_number"] == "B7"
    both = fake.store["sources"]["src1"]["reporef_list"]
    assert [r["ref"] for r in both] == ["repo1"] and both[0]["call_number"] == "C9"  # one entry, call number kept
    assert [u["path"] for u in fake.store["repositories"]["repo1"]["urls"]] == ["https://old.example"]
    assert [r.status for r in run(fake, ops)] == ["skip"]  # a rerun finds the merge note


def test_merge_repository_keeps_media_type_and_notes_of_a_twin_entry(fake):
    _two_repos(fake)
    fake.by_id("sources", "S0001")["reporef_list"] = [
        {"ref": "repo1", "call_number": "A1", "media_type": "", "note_list": ["n-winner"]},
        {"ref": "repo2", "call_number": "C9", "media_type": "Film", "note_list": ["n-loser", "n-winner"]},
    ]
    ops = [{"op": "merge_repository", "key": "m", "ruling": R, "winner": "R0001", "loser": "R0002"}]
    assert [r.status for r in run(fake, ops)] == ["done"]
    (ref,) = fake.store["sources"]["src1"]["reporef_list"]
    assert ref["call_number"] == "A1"  # the winner's own call number stays
    assert ref["media_type"] == "Film" and ref["note_list"] == ["n-winner", "n-loser"]


def test_merge_repository_retries_a_delete_that_failed(fake):
    # the DELETE of the loser answers 503 once: the retry must still delete it, not skip on the merge note
    _two_repos(fake)
    real, failed = fake.request, []

    def request(method, url, **kw):
        if method == "DELETE" and "/repositories/" in url and not failed:
            failed.append(url)
            return Resp(503, "busy")
        return real(method, url, **kw)

    fake.request = request
    ops = [{"op": "merge_repository", "key": "m", "ruling": R, "winner": "R0001", "loser": "R0002"}]
    res = run(fake, ops)
    assert failed and res[0].status == "done", res[0].actions
    assert "repo2" not in fake.store["repositories"]


def test_merge_repository_dry_run_keeps_everything(fake):
    _two_repos(fake)
    ops = [{"op": "merge_repository", "key": "m", "ruling": R, "winner": "R0001", "loser": "R0002"}]
    res = run(fake, ops, dry_run=True)
    assert res[0].status == "done" and fake.writes() == [] and "repo2" in fake.store["repositories"]
    assert any(a.startswith("delete Repository R0002") for a in res[0].actions)


def test_repository_ops_are_validated():
    base = {"key": "k", "ruling": R}
    bad = [
        {**base, "op": "add_repository", "name": "A", "type": " "},
        {**base, "key": "k2", "op": "add_repository", "name": "A", "type": "Archive", "url": "ftp://x"},
        {**base, "key": "k3", "op": "add_repository", "name": "A", "type": "Archive", "address": {"planet": "Mars"}},
        {**base, "key": "k4", "op": "update_repository", "repository": "R0001", "set": {"gramps_id": "R5"}},
        {**base, "key": "k5", "op": "link_repository", "source": "S0001", "repository": "R0001", "media_type": ""},
        {**base, "key": "k6", "op": "unlink_repository", "source": "S0001", "repository": "0a1b2c3d4e5f"},
        {**base, "key": "k7", "op": "merge_repository", "winner": "R0001", "loser": "R0001"},
        {**base, "key": "k8", "op": "add_repository", "name": 123, "type": "Archive"},
    ]
    errors = validate(bad)
    assert len(errors) == 8, errors


def test_a_note_can_be_attached_to_a_repository(fake):
    _two_repos(fake)
    ops = [{"op": "add_note", "key": "n", "ruling": R, "text": "Opening hours checked on the website.",
            "attach": [{"repository": "R0001"}]}]
    assert [r.status for r in run(fake, ops)] == ["done"]
    assert len(fake.store["repositories"]["repo1"]["note_list"]) == 1


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


def test_namesake_of_a_key_from_an_earlier_list(fake):
    """namesake_of may name key:<key> of a person an earlier list made; once that person is in the tree
    (with a Gramps ID of its own) the key still counts as compared, in preflight and in the apply."""
    from gwt_apply.dupes import preflight
    from gwt_apply.client import Client
    first = [{"op": "add_person", "key": "list1-rose", "ruling": R, "first_name": "Rose", "surname": "Sample"}]
    assert run(fake, first)[0].status == "done"
    made = fake.store["people"][stable_handle("list1-rose")]["gramps_id"]
    assert made
    second = [{"op": "add_person", "key": "list2-rose", "ruling": R, "first_name": "Rose", "surname": "Sample",
               "allow_namesake": True, "namesake_reason": "born forty years later, other parents",
               "namesake_of": "key:list1-rose"}]
    c = Client("https://gw.example", "u", "pw", session=fake)
    assert preflight(second, c) == []
    res = run(fake, second)
    assert res[0].status == "done", res[0].actions
    # a key that names nobody in the tree compares with nobody: the real namesake is still flagged
    third = [dict(second[0], key="list3-rose", namesake_of="key:no-such-person")]
    assert preflight(third, c) and made in preflight(third, c)[0]
    assert run(fake, third)[0].status == "conflict"


def test_update_event_fixes_the_existing_event(fake):
    ev = fake.add("events", {"handle": "b1", "type": "Birth", "date": parse_date("1890"), "description": "old"})
    ops = [{"op": "update_event", "key": "u", "ruling": R, "event": ev["gramps_id"],
            "set": {"date": "1891-02-03", "place": "P0001", "description": "fixed"}}]
    assert run(fake, ops)[0].status == "done"
    e = fake.store["events"]["b1"]
    assert (e["date"]["dateval"], e["place"], e["description"]) == ([3, 2, 1891, False], "plc1", "fixed")
    assert run(fake, ops)[0].status == "skip"


def test_update_place_sets_coordinates_on_an_existing_place(fake):
    ops = [{"op": "update_place", "key": "up", "ruling": R, "place": "P0001", "set": {"lat": 47.39, "long": 0.689}}]
    assert run(fake, ops)[0].status == "done"
    pl = fake.store["places"]["plc1"]
    assert (pl["lat"], pl["long"]) == ("47.39", "0.689")
    assert run(fake, ops)[0].status == "skip"
    for bad in ({"lat": 47.39}, {"lat": 95, "long": 0}, {"name": "x"}, {}):
        assert validate([dict(ops[0], key="bad", set=bad)]), bad


def test_coordinates_that_are_not_numbers_are_a_validation_error():
    # float(None) is a TypeError: it must come out as a line in the error list, not a crash
    for bad in (None, "north", [1]):
        up = {"op": "update_place", "key": "up", "ruling": R, "place": "P0001", "set": {"lat": bad, "long": 0.6}}
        add = {"op": "add_place", "key": "ap", "ruling": R, "name": "A", "type": "Town", "lat": 1, "long": bad}
        errors = validate([up, add])
        assert len(errors) == 2 and all("number" in e for e in errors), errors


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
    monkeypatch.chdir(tmp_path)
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
    assert main([str(changes), "--snapshot", str(snap), "--log", str(tmp_path / "plan.jsonl")]) == 0
    out = capsys.readouterr().out
    plan = list(tmp_path.glob("plan.2*.jsonl"))
    assert len(plan) == 1 and _lines(plan[0])[0]["event"] == "start"  # no server, still the log asked for
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
    assert main([str(changes), "--snapshot", str(snap), "--apply", "--log", str(tmp_path / "run.jsonl")]) == 1
    err = capsys.readouterr().err
    assert "Person I0001 changed after the snapshot" in err and "take a new one" in err
    lines = _lines(next(tmp_path.glob("run.2*.jsonl")))
    assert [e["event"] for e in lines if e.get("event")] == ["start", "refused", "end"]
    assert next(e for e in lines if e.get("event") == "refused")["reason"] == "snapshot-plan"
    from gwt_apply.lock import LOCK_HANDLE
    assert fake.writes()[before:] == [("POST", "/api/objects/"), ("DELETE", f"/api/notes/{LOCK_HANDLE}")]  # the lock only
    # a new person after the snapshot: the plan searched every person for namesakes
    snap2 = tmp_path / "tree2.json.gz"
    assert main(["--save-snapshot", str(snap2)]) == 0
    fake.add("people", {"handle": "late", "primary_name": {"first_name": "Late", "surname_list": [{"surname": "Comer"}]}})
    assert main([str(changes), "--snapshot", str(snap2), "--apply"]) == 1
    assert "1 Person object(s) added" in capsys.readouterr().err


def test_apply_on_a_snapshot_of_another_tree_is_refused_and_logged(fake, tmp_path, env, capsys):
    import gzip
    snap = tmp_path / "tree.json.gz"
    assert main(["--save-snapshot", str(snap)]) == 0
    with gzip.open(snap, "rt") as fh:
        data = json.load(fh)
    other = tmp_path / "other.json.gz"
    with gzip.open(other, "wt") as fh:
        json.dump(dict(data, tree="another-tree"), fh)
    changes = _changes(tmp_path, [{"op": "add_note", "key": "n", "ruling": R, "text": "x", "attach": [{"person": "I0001"}]}])
    assert main([str(changes), "--snapshot", str(other), "--apply", "--log", str(tmp_path / "run.jsonl")]) == 1
    assert "another-tree" in capsys.readouterr().err
    lines = _lines(next(tmp_path.glob("run.2*.jsonl")))
    assert [(e["event"], e.get("reason")) for e in lines if e.get("event")] == [
        ("start", None), ("refused", "snapshot-tree"), ("end", None)]


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
            if ns == "events":
                self._merge_event_refs(win, lose, w, l_)
            elif ns == "people":
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

    def _merge_event_refs(self, win, lose, w, l_):
        """Gramps' MergeEventQuery: lists move to the winner; a holder's ref to the loser becomes a ref to the
        winner, and is dropped when the holder already lists the winner in the same role."""
        for lst in ("media_list", "attribute_list"):
            win[lst] = win.get(lst, []) + [x for x in lose.get(lst, []) if x not in win.get(lst, [])]
        for holder in [*self.store["people"].values(), *self.store["families"].values()]:
            refs = holder.get("event_ref_list") or []
            if not any(r["ref"] == l_ for r in refs):
                continue
            first = next((r for r in refs if r["ref"] == w), None)
            for vital in ("birth_ref_index", "death_ref_index"):
                if holder.get(vital, -1) >= 0 and refs[holder[vital]]["ref"] == l_:
                    holder[vital] = -2  # recalculated below, as Gramps does
            kept = []
            for r in refs:
                if r["ref"] == l_ and first is not None and first.get("role") == r.get("role"):
                    continue
                kept.append(dict(r, ref=w) if r["ref"] == l_ else r)
            for vital, kind in (("birth_ref_index", "Birth"), ("death_ref_index", "Death")):
                if holder.get(vital, -1) >= 0:
                    holder[vital] = kept.index(refs[holder[vital]])
                elif holder.get(vital) == -2:
                    holder[vital] = next((i for i, r in enumerate(kept) if r.get("role") == "Primary"
                                          and self.store["events"].get(r["ref"], win).get("type") == kind), -1)
            holder["event_ref_list"] = kept


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
    assert res[0].status == "done", res[0].actions  # the retry finds the merge done: the lost answer was a write
    assert res[0].actions[-1].startswith("answer lost on try 1: POST /people/anna/merge/")
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


def _dna_block(n, start=0):
    """n matches the way a DNA block lists them: the person, the association, a note on the person."""
    ops = []
    for i in range(start, start + n):
        ops += [
            {"op": "add_person", "key": f"m{i}", "ruling": R, "first_name": f"Match{i}", "surname": "Kit"},
            {"op": "add_association", "key": f"a{i}", "ruling": R, "person": "I0001", "target": f"key:m{i}", "relation": "DNA"},
            {"op": "add_note", "key": f"n{i}", "ruling": R, "text": f"match {i}", "attach": [{"person": f"key:m{i}"}]},
        ]
    return ops


def test_a_block_of_associations_is_one_write_of_the_person(fake, tmp_path):
    ops = _dna_block(5)
    plan = run(fake, ops, dry_run=True)
    res = run(fake, ops, tmp_path=tmp_path)
    assert [r.status for r in res] == ["done"] * 15, [r.actions for r in res if r.status != "done"]
    assert [r.status for r in plan] == [r.status for r in res]
    assert [r.actions for r in plan if r.op == "add_association"] == [r.actions for r in res if r.op == "add_association"]
    assert fake.writes().count(("PUT", "/api/people/anna")) == 1
    refs = fake.store["people"]["anna"]["person_ref_list"]
    assert [r["ref"] for r in refs] == [stable_handle(f"m{i}") for i in range(5)]  # in the order of the list
    log = [json.loads(line) for line in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert sorted(e["key"] for e in log if e.get("event") == "op") == sorted(op["key"] for op in ops)  # every op logged once
    assert [e["written"] for e in log if e.get("event") == "held-write"] == [5]
    before = len(fake.writes())
    assert all(r.status == "skip" for r in run(fake, ops))  # a rerun changes nothing
    assert len(fake.writes()) == before


def test_a_rerun_adds_only_the_missing_associations(fake):
    run(fake, _dna_block(3))
    res = run(fake, _dna_block(5))
    assert [r.status for r in res if r.op == "add_association"] == ["skip"] * 3 + ["done"] * 2
    assert len(fake.store["people"]["anna"]["person_ref_list"]) == 5


def test_anything_else_reading_the_person_gets_the_held_associations_written_first(fake):
    ops = _dna_block(2) + [
        {"op": "set_attribute", "key": "s", "ruling": R, "target": {"person": "I0001"}, "type": "Nickname", "value": "Nan"},
    ] + _dna_block(2, start=2)
    res = run(fake, ops)
    assert all(r.status == "done" for r in res), [r.actions for r in res]
    anna = fake.store["people"]["anna"]
    assert len(anna["person_ref_list"]) == 4 and anna["attribute_list"][0]["value"] == "Nan"
    assert fake.writes().count(("PUT", "/api/people/anna")) == 3  # two held writes, one attribute


def test_a_merge_writes_the_held_associations_first(scan, tmp_path):
    fake = MergingFake()
    fake.add("people", {"handle": "anna", "primary_name": {"first_name": "Anna", "surname_list": [{"surname": "Example"}]}})
    fake.add("people", {"handle": "k1", "primary_name": {"first_name": "Kit", "surname_list": [{"surname": "One"}]}})
    fake.add("people", {"handle": "k2", "primary_name": {"first_name": "Kit", "surname_list": [{"surname": "Two"}]}})
    ops = [
        {"op": "add_association", "key": "a", "ruling": R, "person": "I0001", "target": "I0003", "relation": "DNA"},
        {"op": "merge_person", "key": "mp", "ruling": R, "winner": "I0002", "loser": "I0003"},
    ]
    res = run(fake, ops)
    assert all(r.status == "done" for r in res), [r.actions for r in res]
    writes = fake.writes()
    assert writes.index(("PUT", "/api/people/anna")) < writes.index(("POST", "/api/people/k1/merge/k2"))


def test_a_lost_answer_to_the_held_write_does_not_add_an_association_twice(fake):
    for i in range(3):
        fake.add("people", {"handle": f"k{i}", "primary_name": {"first_name": f"K{i}", "surname_list": [{"surname": "Kit"}]}})
    fake.drop_after = ("PUT", "/api/people/anna")
    ops = [{"op": "add_association", "key": f"a{i}", "ruling": R, "person": "I0001", "target": f"I000{i + 2}",
            "relation": "DNA"} for i in range(3)]
    res = run(fake, ops)
    assert [r.status for r in res] == ["done"] * 3
    assert len(fake.store["people"]["anna"]["person_ref_list"]) == 3
    assert fake.writes().count(("PUT", "/api/people/anna")) == 1  # the retry found them all there


def test_a_held_write_that_fails_fails_every_op_it_carried(fake, tmp_path):
    for i in range(2):
        fake.add("people", {"handle": f"k{i}", "primary_name": {"first_name": f"K{i}", "surname_list": [{"surname": "Kit"}]}})
    fake.busy = 10
    ops = [{"op": "add_association", "key": f"a{i}", "ruling": R, "person": "I0001", "target": f"I000{i + 2}",
            "relation": "DNA"} for i in range(2)]
    res = run(fake, ops, tmp_path=tmp_path)
    assert [r.status for r in res] == ["error"] * 2
    assert "held associations of Person I0001 failed" in res[0].actions[-1]
    assert not fake.store["people"]["anna"].get("person_ref_list")
    fake.busy = 0
    assert [r.status for r in run(fake, ops)] == ["done"] * 2  # and a rerun writes them


def test_held_associations_are_written_when_a_later_op_crashes(fake, tmp_path):
    fake.add("people", {"handle": "k0", "primary_name": {"first_name": "K0", "surname_list": [{"surname": "Kit"}]}})
    ops = [
        {"op": "add_association", "key": "a", "ruling": R, "person": "I0001", "target": "I0002", "relation": "DNA"},
        {"op": "add_media", "key": "m", "ruling": R, "file": str(tmp_path / "gone.jpg"), "path": "x.jpg",
         "attach": [{"person": "I0001"}]},
    ]
    res = run(fake, ops)
    assert [r.status for r in res] == ["done", "error"]
    assert res[1].actions[-1].startswith("error: FileNotFoundError"), res[1].actions
    assert len(fake.store["people"]["anna"]["person_ref_list"]) == 1


def test_a_held_write_that_fails_inside_a_later_op_stops_the_run_there(fake):
    fake.add("people", {"handle": "k0", "primary_name": {"first_name": "K0", "surname_list": [{"surname": "Kit"}]}})
    ops = [
        {"op": "add_association", "key": "a", "ruling": R, "person": "I0001", "target": "I0002", "relation": "DNA"},
        {"op": "set_attribute", "key": "s", "ruling": R, "target": {"person": "I0001"}, "type": "Nickname", "value": "Nan"},
        {"op": "add_person", "key": "later", "ruling": R, "first_name": "Not", "surname": "Run"},
    ]
    fake.busy = 10
    res = run(fake, ops)
    assert [r.status for r in res] == ["error", "error"]  # the association, and the op that needed it written
    assert "were not written" in res[1].actions[-1]


def _event_fixture():
    fake = MergingFake()
    fake.add("people", {"handle": "anna", "gender": 0, "primary_name": {"first_name": "Anna", "surname_list": [{"surname": "Example", "primary": True}]}})
    fake.add("events", {"handle": "d1", "type": "Death", "date": parse_date("1884-05-02"), "citation_list": ["c1"]})   # E0001
    fake.add("events", {"handle": "d2", "type": "Death", "date": parse_date("1884-05-02"), "citation_list": ["c2"]})   # E0002
    anna = fake.store["people"]["anna"]
    anna["event_ref_list"] = [{"_class": "EventRef", "ref": "d1", "role": "Primary"},
                              {"_class": "EventRef", "ref": "d2", "role": "Primary"}]
    anna["death_ref_index"] = 1  # points at the loser
    return fake


def test_merge_event_is_gramps_own_merge_and_shows_every_holder_first():
    fake = _event_fixture()
    fake.add("families", {"handle": "fam", "event_ref_list": [{"_class": "EventRef", "ref": "d2", "role": "Family"}]})
    op = {"op": "merge_event", "key": "me", "ruling": R, "winner": "E0001", "loser": "E0002", "reason": "one death, two citations"}
    plan = run(fake, [op], dry_run=True)
    text = "\n".join(plan[0].actions)
    assert plan[0].status == "done" and fake.writes() == []
    assert 'merge Event E0002 into E0001: citation_list +["c2"]' in text
    assert "Person I0001: already lists E0001, drops E0002" in text
    assert "Family F0001: lists E0001 instead of E0002" in text
    assert plan[0].actions[-1] == "delete Event E0002"
    res = run(fake, [op])
    assert res[0].status == "done", res[0].actions
    assert [p for m, p in fake.calls if m == "DELETE" or (m == "PUT" and "/events/" not in p)] == []  # holders: Gramps
    assert sum(1 for m, p in fake.calls if m == "POST" and p.endswith("/events/d1/merge/d2")) == 1
    assert "d2" not in fake.store["events"] and fake.store["events"]["d1"]["citation_list"] == ["c1", "c2"]
    anna = fake.store["people"]["anna"]
    assert [r["ref"] for r in anna["event_ref_list"]] == ["d1"] and anna["death_ref_index"] == 0
    note = fake.store["notes"][stable_handle("me#merge")]
    assert "Merged Event E0002" in note["text"]["string"] and note["handle"] in fake.store["events"]["d1"]["note_list"]
    assert run(fake, [op])[0].status == "skip"  # a rerun finds it done


@pytest.mark.parametrize("change, why", [
    ({"date": "1884-05-03"}, "differ in date"),
    ({"date": "about 1884-05-02"}, "differ in date"),  # the same day, but not the same fact
    ({"type": "Burial"}, "is a Death"),
    ({"description": "at sea"}, "differ in description"),
    ({"place": "pl1"}, "has none, the merge would lose it"),
])
def test_merge_event_never_picks_a_fact(change, why):
    fake = _event_fixture()
    if "date" in change:
        change = {"date": parse_date(change["date"])}
    fake.store["events"]["d2"].update(change)
    res = run(fake, [{"op": "merge_event", "key": "me", "ruling": R, "winner": "E0001", "loser": "E0002"}])
    assert res[0].status == "conflict" and why in res[0].actions[-1], res[0].actions
    assert "d2" in fake.store["events"] and fake.writes() == []


def test_merge_event_refuses_a_holder_with_both_events_in_different_roles():
    fake = _event_fixture()
    fake.store["people"]["anna"]["event_ref_list"][1]["role"] = "Witness"
    res = run(fake, [{"op": "merge_event", "key": "me", "ruling": R, "winner": "E0001", "loser": "E0002"}])
    assert res[0].status == "conflict" and "in different roles" in res[0].actions[-1]
    assert "d2" in fake.store["events"] and fake.writes() == []
    assert "winner and loser are the same" in "\n".join(
        validate([{"op": "merge_event", "key": "x", "ruling": R, "winner": "E0001", "loser": "E0001"}]))


def test_merge_event_moves_a_holder_that_only_had_the_loser():
    fake = _event_fixture()
    anna = fake.store["people"]["anna"]
    anna["event_ref_list"] = [{"_class": "EventRef", "ref": "d2", "role": "Primary"}]
    anna["death_ref_index"] = 0
    res = run(fake, [{"op": "merge_event", "key": "me", "ruling": R, "winner": "E0001", "loser": "E0002"}])
    assert res[0].status == "done", res[0].actions
    anna = fake.store["people"]["anna"]
    assert [r["ref"] for r in anna["event_ref_list"]] == ["d1"] and anna["death_ref_index"] == 0


def test_transcription_is_a_transcript_note_with_a_required_margin_reading(fake):
    base = {"op": "add_transcription", "key": "t", "ruling": R, "text": "Joseph, son of Anna.",
            "attach": [{"person": "I0001"}]}
    assert any("margin_reading" in e for e in validate([base]))
    assert any("margin_reading" in e for e in validate([dict(base, margin_reading="  ")]))
    ok = dict(base, margin_reading="none")
    assert validate([ok]) == []
    assert run(fake, [ok])[0].status == "done"
    note = next(iter(fake.store["notes"].values()))
    assert note["type"] == "Transcript"
    assert note["text"]["string"].endswith("Margin reading: none")
    assert fake.store["people"]["anna"]["note_list"] == [stable_handle("t")]
    assert run(fake, [dict(ok, key="t2")])[0].status == "skip"  # same text on the same person: reused
    on_child_ref = dict(ok, key="m", attach=[{"child_ref": {"family": "F0001", "child": "I0001"}}])
    assert validate([on_child_ref]) == []


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


def test_held_associations_on_a_snapshot_apply(fake, tmp_path, env, capsys):
    snap = tmp_path / "tree.json.gz"
    assert main(["--save-snapshot", str(snap)]) == 0
    changes = tmp_path / "changes.jsonl"
    changes.write_text("\n".join(json.dumps(op) for op in _dna_block(4)))
    assert main([str(changes), "--snapshot", str(snap)]) == 0  # the offline plan
    assert main([str(changes), "--snapshot", str(snap), "--apply"]) == 0, capsys.readouterr()
    assert fake.writes().count(("PUT", "/api/people/anna")) == 1
    assert len(fake.store["people"]["anna"]["person_ref_list"]) == 4


def test_a_snapshot_since_an_older_one_reads_only_what_changed(fake, scan, tmp_path, env, capsys):
    before, after, full = (tmp_path / f"{n}.json.gz" for n in ("before", "after", "full"))
    assert main(["--save-snapshot", str(before)]) == 0
    changes = tmp_path / "changes.jsonl"
    changes.write_text("\n".join(json.dumps(op) for op in full_list(scan)))
    assert main([str(changes), "--apply"]) == 0
    fake.store["places"]["plc1"].update(name={"value": "Shelbyville"}, change=fake.tick())  # an edit by someone else
    start = len(fake.calls)
    assert main(["--save-snapshot", str(after), "--since", str(before)]) == 0
    calls = fake.calls[start:]
    assert not [c for c in calls if c[0] != "GET" and c[1] != "/api/token/"]
    assert len([c for c in calls if c[0] == "GET"]) <= 9 * 2  # per class: the stamps, and the changed objects at most
    assert main(["--save-snapshot", str(full)]) == 0
    from gwt_apply import snapshot
    by_handle = lambda p: {cls: {o["handle"]: o for o in rows} for cls, rows in snapshot.load(str(p))["objects"].items()}
    assert by_handle(after) == by_handle(full)  # the same as reading the whole tree again
    capsys.readouterr()
    assert main([str(changes), "--snapshot", str(after)]) == 0  # the fast redry: offline, every op skips
    assert "0 done, 8 skip" in capsys.readouterr().out


def test_the_fast_redry_reports_an_object_changed_after_the_apply(fake, scan, tmp_path, env, capsys):
    before, after = tmp_path / "before.json.gz", tmp_path / "after.json.gz"
    assert main(["--save-snapshot", str(before)]) == 0
    changes = tmp_path / "changes.jsonl"
    changes.write_text("\n".join(json.dumps(op) for op in full_list(scan)))
    assert main([str(changes), "--apply"]) == 0
    anna = fake.store["people"]["anna"]  # in the older snapshot too
    anna["primary_name"]["first_name"] = "Anna"  # someone undoes the list's rename
    anna["change"] = fake.tick()
    gone = fake.store["citations"].pop(stable_handle("c-bapt"))  # and deletes its citation
    assert main(["--save-snapshot", str(after), "--since", str(before)]) == 0
    from gwt_apply import snapshot
    assert gone["handle"] not in {o["handle"] for o in snapshot.load(str(after))["objects"]["Citation"]}
    capsys.readouterr()
    main([str(changes), "--snapshot", str(after)])
    out = capsys.readouterr().out
    assert "[done] u-anna" in out and "[done] c-bapt" in out


def test_since_refuses_a_snapshot_of_another_tree(fake, tmp_path, env, monkeypatch, capsys):
    old = tmp_path / "old.json.gz"
    assert main(["--save-snapshot", str(old)]) == 0
    fake.tree = "tree-other"
    monkeypatch.setenv("GWT_TREE", "tree-other")
    assert main(["--save-snapshot", str(tmp_path / "new.json.gz"), "--since", str(old)]) == 2
    assert "tree-test" in capsys.readouterr().err
    assert not (tmp_path / "new.json.gz").exists()


# ---- corrections: update_note, update_citation, update_family, update_child_ref, detach


def _note(fake, handle, text, ntype="Research"):
    return fake.add("notes", {"handle": handle, "type": ntype, "format": 0,
                              "text": {"_class": "StyledText", "string": text, "tags": []}})


def test_update_note_needs_the_old_text_and_never_overwrites_a_changed_note(fake):
    n = _note(fake, "n1", "Father read as Denis (?)")
    op = {"op": "update_note", "key": "u", "ruling": R, "note": n["gramps_id"],
          "set": {"text": "Father read as Rene (?)"}, "expect": "Father read as Denis (?)"}
    assert validate([{k: v for k, v in op.items() if k != "expect"}])  # a text change without expect is refused
    assert run(fake, [op])[0].status == "done"
    assert fake.store["notes"]["n1"]["text"]["string"] == "Father read as Rene (?)"
    assert run(fake, [op])[0].status == "skip"  # already the new text
    res = run(fake, [dict(op, key="u2", set={"text": "Something else"}, expect="Father read as Denis (?)")])
    assert res[0].status == "conflict" and fake.store["notes"]["n1"]["text"]["string"] == "Father read as Rene (?)"


def test_update_note_type_only_and_validation(fake):
    n = _note(fake, "n1", "text", "General")
    op = {"op": "update_note", "key": "u", "ruling": R, "note": n["gramps_id"], "set": {"type": "Research"}}
    assert run(fake, [op])[0].status == "done" and fake.store["notes"]["n1"]["type"] == "Research"
    assert run(fake, [op])[0].status == "skip"
    for bad in ({}, {"colour": "red"}, {"text": ""}):
        assert validate([dict(op, key="b", set=bad)]), bad
    assert validate([dict(op, key="b", expect="x")])  # expect without set.text


def test_update_citation_page_confidence_and_date(fake):
    c = fake.add("citations", {"handle": "c1", "source_handle": "src1", "page": "folio 12", "confidence": 2})
    op = {"op": "update_citation", "key": "u", "ruling": R, "citation": c["gramps_id"],
          "set": {"page": "folio 12v, no. 40", "confidence": 3, "date": "1890-03-14"}, "expect": "folio 12"}
    assert run(fake, [op])[0].status == "done"
    got = fake.store["citations"]["c1"]
    assert (got["page"], got["confidence"], got["date"]["dateval"]) == ("folio 12v, no. 40", 3, [14, 3, 1890, False])
    assert run(fake, [op])[0].status == "skip"
    assert run(fake, [dict(op, key="u2", set={"page": "other"}, expect="folio 99")])[0].status == "conflict"
    assert validate([dict(op, key="b", expect=None)])
    assert validate([dict(op, key="b", set={"confidence": 7})])
    assert validate([dict(op, key="b", set={"source": "S0001"})])


def test_update_family_type(fake):
    f = fake.add("families", {"handle": "fam", "mother_handle": "anna", "type": "Unknown"})
    op = {"op": "update_family", "key": "u", "ruling": R, "family": f["gramps_id"], "set": {"type": "Married"}}
    assert run(fake, [op])[0].status == "done" and fake.store["families"]["fam"]["type"] == "Married"
    assert run(fake, [op])[0].status == "skip"
    assert validate([dict(op, key="b", set={"father": "I1"})]) and validate([dict(op, key="b", set={})])


def test_update_child_ref_relations_and_confirming_a_probable_link(fake):
    kid = fake.add("people", {"handle": "kid", "gender": 1, "primary_name": {"first_name": "Tom", "surname_list": [{"surname": "Example", "primary": True}]}})
    fam = fake.add("families", {"handle": "fam", "mother_handle": "anna"})
    link = {"op": "link_child", "key": "l", "ruling": R, "family": fam["gramps_id"], "child": kid["gramps_id"],
            "probable": True, "probable_reason": "same names in the same parish"}
    assert run(fake, [link])[0].status == "done"
    op = {"op": "update_child_ref", "key": "c", "ruling": R, "family": fam["gramps_id"], "child": kid["gramps_id"],
          "confirm_probable": "the 1890 baptism names both parents", "set": {"mrel": "Adopted"}}
    assert run(fake, [op])[0].status == "done"
    ref = fake.store["families"]["fam"]["child_ref_list"][0]
    assert ref["mrel"] == "Adopted"
    assert fake.store["notes"][ref["note_list"][0]]["text"]["string"] == (
        "[confirmed] the 1890 baptism names both parents (was: [probable] same names in the same parish)")
    assert run(fake, [op])[0].status == "skip"  # settled once
    # a link that was never probable cannot be "confirmed"
    kid2 = fake.add("people", {"handle": "kid2", "gender": 1, "primary_name": {"first_name": "Sam", "surname_list": [{"surname": "Example", "primary": True}]}})
    run(fake, [{"op": "link_child", "key": "l2", "ruling": R, "family": fam["gramps_id"], "child": kid2["gramps_id"]}])
    assert run(fake, [dict(op, key="c2", child=kid2["gramps_id"], set={})])[0].status == "conflict"
    for bad in ({"set": {}}, {"set": {"colour": "x"}}, {"expect": "x", "confirm_probable": None}):
        base = {k: v for k, v in op.items() if k not in ("set", "confirm_probable")}
        assert validate([dict(base, key="b", **bad)]), bad


def test_detach_takes_a_reference_off_and_keeps_the_object(fake):
    n = _note(fake, "n1", "stray")
    fake.store["people"]["anna"]["note_list"] = ["n1"]
    fake.store["people"]["anna"]["media_list"] = []
    op = {"op": "detach", "key": "d", "ruling": R, "target": {"person": "I0001"}, "object": {"note": n["gramps_id"]}}
    assert run(fake, [op])[0].status == "done"
    assert fake.store["people"]["anna"]["note_list"] == [] and "n1" in fake.store["notes"]
    assert run(fake, [op])[0].status == "skip"
    # the way back is attach_existing
    back = {"op": "attach_existing", "key": "a", "ruling": R, "object": {"note": n["gramps_id"]}, "attach": [{"person": "I0001"}]}
    assert run(fake, [back])[0].status == "done" and fake.store["people"]["anna"]["note_list"] == ["n1"]
    assert validate([dict(op, key="b", object={"person": "I0001"})])
    assert validate([dict(op, key="b", target={"source": "S0001"}, object={"note": "N0001"}, )]) == []  # a source carries notes


# ---- --revert: undo a run from its log and the pre-run copy


def _pre_copy(fake, path):
    """What `--save-snapshot` or `gwt-check dump` would hold: every object, by class."""
    from gwt_apply.client import NAMESPACE
    objs = {ns: [json.loads(json.dumps(o)) for o in fake.store[ns].values()] for ns in NAMESPACE.values() if ns in fake.store}
    import gzip
    with gzip.open(path, "wt") as fh:
        json.dump({"tree": "tree-test", "objects": objs}, fh)


def _revert(fake, tmp_path, log, pre, *, apply):
    from gwt_apply import revert
    client = Client("https://gw.example", "u", "s3cret-pw", tree="tree-test", dry_run=False, session=fake)
    client.login()
    steps = revert.plan(revert.read_log(log), revert.load_pre(pre), lambda c, h: client.get_live(c, h))
    problems = revert.execute(client, [s for s in steps if s["action"] in ("restore", "delete")], revert.load_pre(pre)) if apply else []
    return steps, problems


def test_revert_undoes_a_run_and_leaves_what_changed_since(fake, tmp_path):
    n = _note(fake, "n1", "Father read as Denis (?)")
    fam = fake.add("families", {"handle": "fam", "mother_handle": "anna", "type": "Unknown"})
    pre = str(tmp_path / "pre.json.gz")
    _pre_copy(fake, pre)
    ops = [
        {"op": "update_note", "key": "u", "ruling": R, "note": n["gramps_id"], "set": {"text": "Father read as Rene (?)"},
         "expect": "Father read as Denis (?)"},
        {"op": "update_family", "key": "f", "ruling": R, "family": fam["gramps_id"], "set": {"type": "Married"}},
        {"op": "add_note", "key": "new", "ruling": R, "text": "A note this run made", "attach": [{"family": fam["gramps_id"]}]},
    ]
    log = str(tmp_path / "run.jsonl")
    client = Client("https://gw.example", "u", "s3cret-pw", tree="tree-test", dry_run=False, log_path=log, session=fake)
    client.login()
    assert [r.status for r in Applier(client, retries=1, retry_wait=0).run(ops)] == ["done", "done", "done"]
    # someone edits the family after the run: the revert must not overwrite that
    fake.store["families"]["fam"]["type"] = "Civil Union"
    steps, _ = _revert(fake, tmp_path, log, pre, apply=False)
    by = {(s["cls"], s["handle"]): s for s in steps}
    assert by[("Family", "fam")]["action"] == "skip" and "changed since" in by[("Family", "fam")]["why"]
    assert by[("Note", "n1")]["action"] == "restore"
    # the family changed since and still lists the run's new note, so that note is kept too
    new = [s for s in steps if s["cls"] == "Note" and s["handle"] != "n1"]
    assert [s["action"] for s in new] == ["skip"] and "still points at it" in new[0]["why"]
    # a plan writes nothing
    assert fake.store["notes"]["n1"]["text"]["string"] == "Father read as Rene (?)"
    _, problems = _revert(fake, tmp_path, log, pre, apply=True)
    assert problems == []
    assert fake.store["notes"]["n1"]["text"]["string"] == "Father read as Denis (?)"
    assert fake.store["families"]["fam"]["type"] == "Civil Union"  # untouched
    assert len(fake.store["notes"]) == 2  # nothing the changed family points at is deleted
    again, _ = _revert(fake, tmp_path, log, pre, apply=False)
    assert all(s["action"] in ("skip", "manual") for s in again)


def test_revert_sends_people_merges_and_deletes_to_the_backup(fake, tmp_path):
    from gwt_apply import revert
    entries = [
        {"method": "POST", "path": "/objects/", "status": 201, "body": [{"_class": "Person", "handle": "p9"}]},
        {"method": "POST", "path": "/people/a/merge", "status": 200, "body": {}},
        {"method": "DELETE", "path": "/notes/n1", "status": 200},
        {"method": "PUT", "path": "/notes/n2", "status": 400, "body": {}},   # a refused write is not a write
        {"method": "PUT", "path": "/notes/n3", "status": 200, "body": {"handle": "n3"}, "dry_run": True},
    ]
    log = tmp_path / "log.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    steps = revert.plan(revert.read_log(str(log)), {}, lambda c, h: None)
    assert [s["action"] for s in steps] == ["manual", "manual", "manual"]
    assert any("merge" in s["why"] for s in steps) and any("restore the backup" in s["why"] for s in steps)


def test_revert_keeps_a_note_a_changed_digest_only_object_still_points_at(tmp_path):
    # no backlinks asked: only the live copy of the changed object shows it still points at the run's note
    from gwt_apply import revert
    from gwt_apply.client import digest
    note = {"_class": "Note", "handle": "nN", "text": {"string": "made by the run"}}
    sent = {"_class": "Person", "handle": "pP", "gender": 1, "note_list": ["nN"]}
    entries = [
        {"method": "POST", "path": "/objects/", "status": 201, "body": [note]},
        {"method": "PUT", "path": "/people/pP", "status": 200,
         "body_digest": {"sha256": digest(sent), "bytes": 70000}},
    ]
    log = tmp_path / "log.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    pre = {("Person", "pP"): {"_class": "Person", "handle": "pP", "gender": 1, "note_list": []}}
    now = {**sent, "gender": 0}  # edited since the run, still lists the note
    live = {("Person", "pP"): now, ("Note", "nN"): note}
    steps = revert.plan(revert.read_log(str(log)), pre, lambda c, h: live.get((c, h)))
    n = next(s for s in steps if s["handle"] == "nN")
    assert n["action"] == "skip" and "still points at it" in n["why"]


def test_revert_cli_needs_pre_and_stands_alone(capsys):
    for argv in (["--revert", "x.jsonl"], ["--revert", "x.jsonl", "--pre", "p.gz", "changes.json"], ["--pre", "p.gz"]):
        with pytest.raises(SystemExit):
            main(argv)


def test_revert_deletes_what_the_run_made_when_nothing_points_at_it_from_a_changed_object(fake, tmp_path):
    fam = fake.add("families", {"handle": "fam", "mother_handle": "anna", "type": "Unknown"})
    pre = str(tmp_path / "pre.json.gz")
    _pre_copy(fake, pre)
    log = str(tmp_path / "run.jsonl")
    client = Client("https://gw.example", "u", "s3cret-pw", tree="tree-test", dry_run=False, log_path=log, session=fake)
    client.login()
    op = {"op": "add_note", "key": "new", "ruling": R, "text": "made by the run", "attach": [{"family": fam["gramps_id"]}]}
    Applier(client, retries=1, retry_wait=0).run([op])
    assert len(fake.store["notes"]) == 1
    _, problems = _revert(fake, tmp_path, log, pre, apply=True)
    assert problems == [] and fake.store["notes"] == {} and fake.store["families"]["fam"]["note_list"] == []


def test_revert_compares_what_the_server_keeps_not_its_defaults(tmp_path):
    from gwt_apply import revert
    written = {"handle": "n1", "text": {"_class": "StyledText", "string": "x", "tags": []}, "change": 1, "gramps_id": ""}
    live = {"handle": "n1", "text": {"string": "x", "tags": []}, "change": 9, "gramps_id": "N0007", "private": False}
    assert revert._same(live, written)
    assert not revert._same(dict(live, text={"string": "y", "tags": []}), written)
    log = tmp_path / "l.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in [
        {"method": "POST", "path": "/token/", "status": 200},
        {"method": "POST", "path": "/token/sync/", "status": 200},  # the GWT_TOKEN login
        {"method": "DELETE", "path": "/notes/" + revert.LOCK_HANDLE, "status": 200},
        {"method": "POST", "path": "/objects/", "status": 201, "body": [{"_class": "Note", "handle": revert.LOCK_HANDLE}]},
    ]) + "\n")
    assert revert.read_log(str(log)) == []  # login and lock note are not changes to the tree


# ---- --revert, the cases that must never lose or overwrite data


def _apply_list(tmp_path, ops, name, *extra):
    """A real --apply of `ops` through the CLI; returns its log."""
    p = tmp_path / f"{name}.changes.jsonl"
    p.write_text("".join(json.dumps(o) + "\n" for o in ops))
    assert main([str(p), "--apply", "--log", str(tmp_path / f"{name}.jsonl"), "--retry-wait", "0", *extra]) == 0
    return str(next(tmp_path.glob(f"{name}.2*.jsonl")))


def _undo(log, pre, *extra):
    return main(["--revert", log, "--pre", pre, "--retry-wait", "0", *extra])


def _live_client(fake):
    client = Client("https://gw.example", "u", "s3cret-pw", tree="tree-test", dry_run=False, session=fake)
    client.login()
    return client


def test_revert_undoes_a_write_whose_answer_was_lost(fake, tmp_path, env):
    _note(fake, "n1", "Father read as Denis (?)")
    pre = str(tmp_path / "pre.json.gz")
    assert main(["--save-snapshot", pre]) == 0
    fake.drop_after = ("PUT", "/api/notes/")  # the server writes, the answer never arrives; the retry reads it back
    log = _apply_list(tmp_path, [{"op": "update_note", "key": "u", "ruling": R, "note": "N0001",
                                  "set": {"text": "Father read as Rene (?)"}, "expect": "Father read as Denis (?)"}], "run")
    assert fake.store["notes"]["n1"]["text"]["string"] == "Father read as Rene (?)"
    mark = len(fake.calls)
    assert _undo(log, pre, "--apply") == 0
    assert fake.store["notes"]["n1"]["text"]["string"] == "Father read as Denis (?)"
    # the lock is taken before the plan reads anything
    calls = fake.calls[mark:]
    assert calls.index(("POST", "/api/objects/")) < calls.index(("GET", "/api/notes/n1"))


def test_revert_undoes_a_write_that_answered_5xx_but_was_written(fake, tmp_path):
    from gwt_apply import revert
    _note(fake, "n1", "old text")
    pre = {("Note", "n1"): json.loads(json.dumps(fake.store["notes"]["n1"]))}
    new = {"_class": "Note", "handle": "n1", "type": "Research", "format": 0,
           "text": {"_class": "StyledText", "string": "new text", "tags": []}}
    fake.store["notes"]["n1"]["text"]["string"] = "new text"  # the server kept it, then answered 500
    log = tmp_path / "run.jsonl"
    log.write_text(json.dumps({"ts": "2026-01-01T10:00:00+00:00", "dry_run": False, "method": "PUT",
                               "path": "/notes/n1", "status": 500, "body": new}) + "\n")
    client = _live_client(fake)
    steps = revert.plan(revert.read_log(str(log)), pre, client.get_live)
    assert [s["action"] for s in steps] == ["restore"]
    assert revert.execute(client, steps, pre, retry_wait=0) == []
    assert fake.store["notes"]["n1"]["text"]["string"] == "old text"


def test_revert_keeps_a_tag_and_a_note_that_a_later_run_still_uses(fake, tmp_path, env):
    fake.add("families", {"handle": "fam", "mother_handle": "anna", "type": "Unknown"})  # F0001
    pre = str(tmp_path / "pre.json.gz")
    assert main(["--save-snapshot", pre]) == 0
    log_a = _apply_list(tmp_path, [{"op": "add_note", "key": "a-note", "ruling": R, "text": "Note from list A",
                                    "attach": [{"family": "F0001"}]}], "list-a", "--tag", "r1")
    _apply_list(tmp_path, [
        {"op": "add_note", "key": "b-note", "ruling": R, "text": "Note from list B", "attach": [{"person": "I0001"}]},
        {"op": "attach_existing", "key": "b-move", "ruling": R, "object": {"note": "key:a-note"}, "attach": [{"source": "S0001"}]},
    ], "list-b", "--tag", "r1")
    tag = stable_handle("tag:r1")
    assert _undo(log_a, pre, "--apply") == 0
    assert fake.store["families"]["fam"]["note_list"] == []  # list A's own change is undone
    assert stable_handle("a-note") in fake.store["notes"]  # list B attached it to the source
    assert tag in fake.store["tags"]  # list B's note carries it
    for o in [*fake.store["notes"].values(), *fake.store["sources"].values()]:  # nothing points at a missing object
        for h in o.get("note_list", []) + o.get("tag_list", []):
            assert h in fake.store["notes"] or h in fake.store["tags"]


def test_revert_does_not_overwrite_what_changed_between_plan_and_write(fake, tmp_path):
    from gwt_apply import revert
    _note(fake, "n1", "Father read as Denis (?)")
    pre = {("Note", "n1"): json.loads(json.dumps(fake.store["notes"]["n1"]))}
    log = str(tmp_path / "run.jsonl")
    client = Client("https://gw.example", "u", "s3cret-pw", tree="tree-test", dry_run=False, log_path=log, session=fake)
    client.login()
    Applier(client, retries=1, retry_wait=0).run([{"op": "update_note", "key": "u", "ruling": R, "note": "N0001",
                                                   "set": {"text": "Father read as Rene (?)"}, "expect": "Father read as Denis (?)"}])
    client = _live_client(fake)
    steps = revert.plan(revert.read_log(log), pre, client.get_live)
    assert [s["action"] for s in steps] == ["restore"]
    fake.store["notes"]["n1"]["text"]["string"] = "Father read as Rene, son of Paul"  # someone edits it now
    fake.store["notes"]["n1"]["change"] = fake.tick()

    class Lock:
        refreshed = 0

        def refresh(self):
            Lock.refreshed += 1

    assert revert.execute(client, steps, pre, lock=Lock(), retry_wait=0) == []
    assert fake.store["notes"]["n1"]["text"]["string"] == "Father read as Rene, son of Paul"
    assert steps[0]["action"] == "skip" and "changed since the plan" in steps[0]["why"]
    assert Lock.refreshed >= 1


def test_revert_waits_out_a_busy_server(fake, tmp_path):
    from gwt_apply import revert
    _note(fake, "n1", "old text")
    pre = {("Note", "n1"): json.loads(json.dumps(fake.store["notes"]["n1"]))}
    log = str(tmp_path / "run.jsonl")
    client = Client("https://gw.example", "u", "s3cret-pw", tree="tree-test", dry_run=False, log_path=log, session=fake)
    client.login()
    Applier(client, retries=1, retry_wait=0).run([{"op": "update_note", "key": "u", "ruling": R, "note": "N0001",
                                                   "set": {"text": "new text"}, "expect": "old text"}])
    client = _live_client(fake)
    steps = revert.plan(revert.read_log(log), pre, client.get_live)
    fake.busy = 2  # "database is locked" twice
    assert revert.execute(client, steps, pre, retries=3, retry_wait=0) == []
    assert fake.store["notes"]["n1"]["text"]["string"] == "old text"


def test_revert_refuses_a_pre_copy_that_is_not_from_before_the_run(fake, tmp_path, env, capsys):
    import gzip
    fake.add("families", {"handle": "fam", "mother_handle": "anna", "type": "Unknown"})
    good = str(tmp_path / "pre.json.gz")
    assert main(["--save-snapshot", good]) == 0
    log = _apply_list(tmp_path, [
        {"op": "update_family", "key": "f", "ruling": R, "family": "F0001", "set": {"type": "Married"}},
        {"op": "add_note", "key": "n", "ruling": R, "text": "made by the run", "attach": [{"family": "F0001"}]},
    ], "run")
    late = str(tmp_path / "late.json.gz")
    assert main(["--save-snapshot", late]) == 0
    with gzip.open(good, "rt") as fh:
        snap = json.load(fh)
    other = str(tmp_path / "other.json.gz")
    with gzip.open(other, "wt") as fh:
        json.dump(dict(snap, tree="another-tree"), fh)
    future = str(tmp_path / "future.json.gz")
    with gzip.open(future, "wt") as fh:
        json.dump(dict(snap, taken="2999-01-01T00:00:00+00:00"), fh)
    before = fake.writes()
    for i, (bad, why) in enumerate(((late, "the run made"), (other, "another-tree"), (future, "after the run's first write"))):
        capsys.readouterr()
        assert _undo(log, bad, "--apply", "--log", str(tmp_path / f"undo{i}.jsonl")) == 2, bad
        assert why in capsys.readouterr().err
        lines = _lines(next(tmp_path.glob(f"undo{i}.2*.jsonl")))
        assert [(e["event"], e.get("reason")) for e in lines if e.get("event")] == [
            ("start", None), ("refused", "pre"), ("end", None)]
        assert fake.writes() == before  # nothing written, not even the lock
    assert fake.store["families"]["fam"]["type"] == "Married"
    assert _undo(log, good, "--apply") == 0
    assert fake.store["families"]["fam"]["type"] == "Unknown" and fake.store["notes"] == {}


def test_revert_exits_1_when_something_changed_since_and_honours_log(fake, tmp_path, env, capsys):
    fake.add("families", {"handle": "fam", "mother_handle": "anna", "type": "Unknown"})
    pre = str(tmp_path / "pre.json.gz")
    assert main(["--save-snapshot", pre]) == 0
    log = _apply_list(tmp_path, [{"op": "update_family", "key": "f", "ruling": R, "family": "F0001",
                                  "set": {"type": "Married"}}], "run")
    fake.store["families"]["fam"]["type"] = "Civil Union"  # someone settles it by hand after the run
    capsys.readouterr()
    assert _undo(log, pre, "--apply", "--log", str(tmp_path / "undo.jsonl")) == 1
    out = capsys.readouterr().out
    assert "1 changed since the run and left alone" in out
    assert fake.store["families"]["fam"]["type"] == "Civil Union"
    assert list(tmp_path.glob("undo.2*.jsonl"))  # next to the run it undoes, not in the working directory


def test_update_child_ref_conflict_writes_nothing(fake):
    kid = fake.add("people", {"handle": "kid", "gender": 1, "primary_name": {"first_name": "Sam", "surname_list": [{"surname": "Example", "primary": True}]}})
    fam = fake.add("families", {"handle": "fam", "mother_handle": "anna"})
    assert run(fake, [{"op": "link_child", "key": "l", "ruling": R, "family": fam["gramps_id"], "child": kid["gramps_id"]}])[0].status == "done"
    puts = len(fake.writes())
    res = run(fake, [{"op": "update_child_ref", "key": "c", "ruling": R, "family": fam["gramps_id"], "child": kid["gramps_id"],
                      "set": {"mrel": "Adopted"}, "confirm_probable": "the 1890 baptism names both parents"}])
    assert res[0].status == "conflict"
    assert fake.store["families"]["fam"]["child_ref_list"][0]["mrel"] == "Birth"
    assert len(fake.writes()) == puts


@pytest.mark.parametrize("dry_run", [True, False])
def test_update_child_ref_confirms_a_link_made_earlier_in_the_same_list(fake, dry_run):
    kid = fake.add("people", {"handle": "kid", "gender": 1, "primary_name": {"first_name": "Tom", "surname_list": [{"surname": "Example", "primary": True}]}})
    fam = fake.add("families", {"handle": "fam", "mother_handle": "anna"})
    ops = [{"op": "link_child", "key": "l", "ruling": R, "family": fam["gramps_id"], "child": kid["gramps_id"],
            "probable": True, "probable_reason": "same names in the same parish"},
           {"op": "update_child_ref", "key": "c", "ruling": R, "family": fam["gramps_id"], "child": kid["gramps_id"],
            "confirm_probable": "the 1890 baptism names both parents"}]
    res = run(fake, ops, dry_run=dry_run)
    assert [r.status for r in res] == ["done", "done"], [r.actions for r in res]
    if not dry_run:
        ref = fake.store["families"]["fam"]["child_ref_list"][0]
        assert fake.store["notes"][ref["note_list"][0]]["text"]["string"].startswith("[confirmed] the 1890 baptism")


def test_revert_never_restores_a_copy_older_than_what_the_run_read(fake, tmp_path, env, capsys):
    _note(fake, "n1", "Father read as Denis (?)")
    pre = str(tmp_path / "pre.json.gz")
    assert main(["--save-snapshot", pre]) == 0
    fake.store["notes"]["n1"]["text"]["string"] = "Father read as Denis, miller"  # an edit between the copy and the run
    fake.store["notes"]["n1"]["change"] = fake.tick()
    log = _apply_list(tmp_path, [{"op": "update_note", "key": "u", "ruling": R, "note": "N0001",
                                  "set": {"text": "Father read as Rene, miller"}, "expect": "Father read as Denis, miller"}], "run")
    assert _undo(log, pre, "--apply") == 1
    assert "older than what the run read" in capsys.readouterr().out
    assert fake.store["notes"]["n1"]["text"]["string"] == "Father read as Rene, miller"  # not back to the older copy


def _links(client):
    return lambda c, h: (client.get_live(c, h, backlinks=True) or {}).get("backlinks")


def _run_a_note_on_a_family(fake, tmp_path):
    fake.add("families", {"handle": "fam", "mother_handle": "anna", "type": "Unknown"})
    pre = str(tmp_path / "pre.json.gz")
    assert main(["--save-snapshot", pre]) == 0
    log = _apply_list(tmp_path, [{"op": "add_note", "key": "a-note", "ruling": R, "text": "Note from list A",
                                  "attach": [{"family": "F0001"}]}], "list-a")
    return log, pre, stable_handle("a-note")


def test_revert_restores_when_the_live_object_holds_an_earlier_write_of_the_run(fake, tmp_path):
    from gwt_apply import revert
    _note(fake, "n1", "old text")
    pre = {("Note", "n1"): json.loads(json.dumps(fake.store["notes"]["n1"]))}
    first = {"_class": "Note", "handle": "n1", "type": "Research", "format": 0,
             "text": {"_class": "StyledText", "string": "first text", "tags": []}}
    fake.store["notes"]["n1"]["text"]["string"] = "first text"  # the second write never reached the server
    log = tmp_path / "run.jsonl"
    log.write_text("".join(json.dumps(e) + "\n" for e in (
        {"ts": "2026-01-01T10:00:00+00:00", "dry_run": False, "method": "PUT", "path": "/notes/n1", "status": 200,
         "body": first},
        {"ts": "2026-01-01T10:00:01+00:00", "dry_run": False, "method": "PUT", "path": "/notes/n1", "status": None,
         "body": dict(first, text={"_class": "StyledText", "string": "second text", "tags": []})})))
    client = _live_client(fake)
    steps = revert.plan(revert.read_log(str(log)), pre, client.get_live)
    assert [s["action"] for s in steps] == ["restore"]
    assert revert.execute(client, steps, pre, retry_wait=0) == []
    assert fake.store["notes"]["n1"]["text"]["string"] == "old text"


def test_revert_plan_already_shows_a_delete_kept_for_a_referrer(fake, tmp_path, env, capsys):
    log, pre, note = _run_a_note_on_a_family(fake, tmp_path)
    fake.add("sources", {"handle": "src", "title": "A later source", "note_list": [note]})
    capsys.readouterr()
    before = fake.writes()
    assert _undo(log, pre) == 0
    out = capsys.readouterr().out
    assert f"[skip] Note {note}: still referenced by Source src" in out
    assert fake.writes() == before


def test_revert_keeps_an_object_something_points_at_since_the_plan(fake, tmp_path, env):
    from gwt_apply import revert
    log, pre, note = _run_a_note_on_a_family(fake, tmp_path)
    client = _live_client(fake)
    snap = revert.load_pre(pre)
    steps = revert.plan(revert.read_log(log), snap, client.get_live, _links(client))
    assert sorted(s["action"] for s in steps) == ["delete", "restore"]
    fake.add("sources", {"handle": "src", "title": "A later source", "note_list": [note]})  # between plan and write
    assert revert.execute(client, steps, snap, retry_wait=0) == []
    assert note in fake.store["notes"]
    kept = next(s for s in steps if s["handle"] == note)
    assert kept["action"] == "skip" and "still referenced by Source src" in kept["why"]


def test_revert_keeps_a_made_object_edited_since_the_plan(fake, tmp_path, env):
    from gwt_apply import revert
    log, pre, note = _run_a_note_on_a_family(fake, tmp_path)
    client = _live_client(fake)
    snap = revert.load_pre(pre)
    steps = revert.plan(revert.read_log(log), snap, client.get_live, _links(client))
    fake.store["notes"][note]["text"]["string"] = "Note from list A, corrected by hand"
    assert revert.execute(client, steps, snap, retry_wait=0) == []
    assert fake.store["notes"][note]["text"]["string"] == "Note from list A, corrected by hand"
    kept = next(s for s in steps if s["handle"] == note)
    assert kept["action"] == "skip" and kept["changed"]


def test_revert_restore_whose_answer_is_lost_reads_as_done(fake, tmp_path):
    from gwt_apply import revert
    _note(fake, "n1", "old text")
    pre = {("Note", "n1"): json.loads(json.dumps(fake.store["notes"]["n1"]))}
    log = str(tmp_path / "run.jsonl")
    client = Client("https://gw.example", "u", "s3cret-pw", tree="tree-test", dry_run=False, log_path=log, session=fake)
    client.login()
    Applier(client, retries=1, retry_wait=0).run([{"op": "update_note", "key": "u", "ruling": R, "note": "N0001",
                                                   "set": {"text": "new text"}, "expect": "old text"}])
    client = _live_client(fake)
    steps = revert.plan(revert.read_log(log), pre, client.get_live)
    fake.drop_after = ("PUT", "/api/notes/")  # the restore lands, its answer does not
    assert revert.execute(client, steps, pre, retries=3, retry_wait=0) == []
    assert fake.store["notes"]["n1"]["text"]["string"] == "old text"
    assert steps[0]["action"] == "restore" and not steps[0].get("changed")


def test_revert_reports_a_restore_that_does_not_read_back(fake, tmp_path, monkeypatch):
    from gwt_apply import revert
    _note(fake, "n1", "old text")
    pre = {("Note", "n1"): json.loads(json.dumps(fake.store["notes"]["n1"]))}
    fake.store["notes"]["n1"]["text"]["string"] = "new text"
    log = tmp_path / "run.jsonl"
    log.write_text(json.dumps({"ts": "2026-01-01T10:00:00+00:00", "dry_run": False, "method": "PUT", "path": "/notes/n1",
                               "status": 200, "body": json.loads(json.dumps(fake.store["notes"]["n1"]))}) + "\n")
    client = _live_client(fake)
    steps = revert.plan(revert.read_log(str(log)), pre, client.get_live)
    monkeypatch.setattr(client, "update", lambda obj: None)  # the server says 200 and keeps nothing
    assert revert.execute(client, steps, pre, retry_wait=0) == ["Note n1 does not read back as before the run"]


def test_revert_never_deletes_what_the_server_gives_no_backlinks_for(fake, tmp_path, env, monkeypatch):
    from gwt_apply import revert
    log, pre, note = _run_a_note_on_a_family(fake, tmp_path)
    client = _live_client(fake)
    snap = revert.load_pre(pre)
    steps = revert.plan(revert.read_log(log), snap, client.get_live)  # planned without asking for backlinks
    live = client.get_live
    monkeypatch.setattr(client, "get_live", lambda c, h, backlinks=False: (
        o if (o := live(c, h)) is None else {k: v for k, v in o.items() if k != "backlinks"}))
    assert revert.execute(client, steps, snap, retry_wait=0) == []
    assert note in fake.store["notes"]
    assert "did not say what points at it" in next(s for s in steps if s["handle"] == note)["why"]


def test_update_child_ref_conflicts_when_the_probable_note_moved_on(fake):
    kid = fake.add("people", {"handle": "kid", "gender": 1, "primary_name": {"first_name": "Tom", "surname_list": [{"surname": "Example", "primary": True}]}})
    fam = fake.add("families", {"handle": "fam", "mother_handle": "anna"})
    assert run(fake, [{"op": "link_child", "key": "l", "ruling": R, "family": fam["gramps_id"], "child": kid["gramps_id"],
                       "probable": True, "probable_reason": "same names in the same parish"}])[0].status == "done"
    puts = len(fake.writes())
    res = run(fake, [{"op": "update_child_ref", "key": "c", "ruling": R, "family": fam["gramps_id"], "child": kid["gramps_id"],
                      "set": {"mrel": "Adopted"}, "confirm_probable": "the 1890 baptism names both parents",
                      "expect": "[probable] an older reason"}])
    assert res[0].status == "conflict"
    assert fake.store["families"]["fam"]["child_ref_list"][0]["mrel"] == "Birth"
    assert len(fake.writes()) == puts


def test_revert_plan_only_cannot_write_and_logs_as_a_dry_run(fake, tmp_path, env):
    log, pre, _ = _run_a_note_on_a_family(fake, tmp_path)
    before = fake.writes()
    assert _undo(log, pre, "--log", str(tmp_path / "plan.jsonl")) == 0
    assert fake.writes() == before
    lines = [json.loads(x) for p in tmp_path.glob("plan.2*.jsonl") for x in p.read_text().splitlines() if x]
    assert lines and all(e["dry_run"] for e in lines)


def test_update_citation_page_must_be_a_text():
    op = {"op": "update_citation", "key": "c", "ruling": R, "citation": "C0001", "set": {"page": 12}, "expect": "folio 12"}
    assert any("page is a non-empty text" in e for e in validate([op]))


def test_detach_refuses_a_media_reference_that_carries_its_own_data(fake):
    fake.add("media", {"handle": "m1", "path": "scan.jpg", "mime": "image/jpeg", "desc": "a scan"})
    mid = fake.store["media"]["m1"]["gramps_id"]
    fake.store["people"]["anna"]["media_list"] = [{"_class": "MediaRef", "ref": "m1", "rect": [10, 10, 60, 60],
                                                  "note_list": [], "citation_list": [], "attribute_list": []}]
    puts = len(fake.writes())
    res = run(fake, [{"op": "detach", "key": "d", "ruling": R, "target": {"person": "I0001"}, "object": {"media": mid}}])
    assert res[0].status == "conflict"
    assert len(fake.store["people"]["anna"]["media_list"]) == 1 and len(fake.writes()) == puts
    fake.store["people"]["anna"]["media_list"][0]["rect"] = []  # a plain reference goes
    res = run(fake, [{"op": "detach", "key": "d2", "ruling": R, "target": {"person": "I0001"}, "object": {"media": mid}}])
    assert res[0].status == "done" and fake.store["people"]["anna"]["media_list"] == []


def test_revert_deletes_a_note_the_run_made_and_then_corrected(fake, tmp_path, env):
    fake.add("families", {"handle": "fam", "mother_handle": "anna", "type": "Unknown"})
    pre = str(tmp_path / "pre.json.gz")
    assert main(["--save-snapshot", pre]) == 0
    log = _apply_list(tmp_path, [
        {"op": "add_note", "key": "n", "ruling": R, "text": "first draft", "attach": [{"family": "F0001"}]},
        {"op": "update_note", "key": "u", "ruling": R, "note": "key:n", "set": {"text": "second draft"}, "expect": "first draft"},
    ], "run")
    assert fake.store["notes"][stable_handle("n")]["text"]["string"] == "second draft"
    assert _undo(log, pre, "--apply") == 0
    assert fake.store["notes"] == {} and fake.store["families"]["fam"]["note_list"] == []


# ---- honest status: a write whose answer was lost, and inputs that would crash mid-run


def test_update_note_whose_answer_was_lost_reports_done(fake):
    _note(fake, "n1", "Father read as Denis (?)")
    fake.drop_after = ("PUT", "/api/notes/")  # the server writes, the answer never arrives
    op = {"op": "update_note", "key": "u", "ruling": R, "note": "N0001",
          "set": {"text": "Father read as Rene (?)"}, "expect": "Father read as Denis (?)"}
    res = run(fake, [op])
    assert res[0].status == "done", res[0].actions
    assert any(a.startswith("answer lost on try 1: PUT /notes/n1") for a in res[0].actions), res[0].actions
    assert fake.writes().count(("PUT", "/api/notes/n1")) == 1  # the retry read it back and wrote nothing
    assert run(fake, [op])[0].status == "skip"  # a rerun of the applied list writes nothing


def test_add_person_whose_answer_was_lost_reports_done(fake, env, tmp_path, capsys):
    op = {"op": "add_person", "key": "p-tom", "ruling": R, "first_name": "Tom", "surname": "Sample"}
    changes = tmp_path / "changes.jsonl"
    changes.write_text(json.dumps(op))
    fake.drop_after = ("POST", "/api/objects/", 1)  # after the lock note: the person is made, the answer is lost
    assert main([str(changes), "--apply", "--retry-wait", "0"]) == 0
    out = capsys.readouterr().out
    assert "[done] p-tom" in out and "answer lost on try 1: POST /objects/" in out, out
    assert "1 done (answer lost: 1), 0 skip" in out, out
    assert list(fake.store["people"]).count(stable_handle("p-tom")) == 1
    writes = len(fake.writes())
    assert main([str(changes), "--apply", "--retry-wait", "0"]) == 0
    assert "0 done, 1 skip" in capsys.readouterr().out
    assert [w for w in fake.writes()[writes:] if "/objects/" in w[1] or "/people/" in w[1]] == [
        ("POST", "/api/objects/")]  # only the lock note: no write for the person


@pytest.mark.parametrize("op, says", [
    ({"op": "add_person", "first_name": "Tom", "surname": "Sample", "attach": [{"person": "I1"}]}, "unknown fields ['attach']"),
    ({"op": "add_citation", "source": "S1", "page": "p. 1", "attach": [{"person": "I1"}], "confidence": "high"}, "confidence is a whole number 0-4"),
    ({"op": "add_citation", "source": "S1", "page": "p. 1", "attach": [{"person": "I1"}], "confidence": 9}, "confidence is a whole number 0-4"),
    ({"op": "add_event", "person": "I1", "type": "  "}, "type is a non-empty text"),
    ({"op": "add_event", "person": "I1", "type": "Birth", "date": "1890-02-31"}, "impossible date '1890-02-31'"),
    ({"op": "add_person", "first_name": 5, "surname": "Sample"}, "first_name is a non-empty text"),
    ({"op": "add_family", "mother": "I1", "children": "I2"}, "children is a list of"),
    ({"op": "add_note", "text": ["a"], "attach": [{"person": "I1"}]}, "text is a non-empty text"),
    ({"op": "add_person", "first_name": "Tom", "surname": "Sample", "tag": ["ok", 3]}, "tag is a non-empty name"),
    ({"op": "add_citation", "source": "S1", "page": "p. 1", "attach": [{"person": "I1"}], "confidence": True}, "confidence is a whole number 0-4"),
    ({"op": "add_person", "first_name": "Tom", "surname": "Sample", "gender": ["M"]}, "gender is one of"),
    ({"op": "update_person", "person": "I1", "set": {"gender": ["M"]}}, "gender is one of"),
    ({"op": "dedupe_list", "target": {"person": "I1"}, "list": ["note_list"]}, "list is one of"),
    ({"op": "update_event", "event": "E1", "set": {"place": 5}}, "5 is neither key:<key> nor a Gramps ID"),
    ({"op": "merge_person", "winner": "I1", "loser": "I2", "use_loser": [["birth"]]}, "unhashable type"),  # a TypeError
])
def test_validate_refuses_inputs_that_would_crash_mid_run(op, says):
    errors = validate([dict(op, key="k", ruling=R)])
    assert any(says in e for e in errors), errors


def test_validate_keeps_real_dates_and_allowed_empty_texts():
    assert validate([
        {"op": "add_event", "key": "a", "ruling": R, "person": "I1", "type": "Birth", "date": "1892-02-29"},
        {"op": "add_person", "key": "b", "ruling": R, "first_name": "Tom", "surname": ""},
        {"op": "add_citation", "key": "c", "ruling": R, "source": "S1", "page": "", "attach": [{"person": "I1"}], "confidence": 0},
        {"op": "add_citation", "key": "d", "ruling": R, "source": "S1", "page": "p. 2", "attach": [{"person": "I1"}], "date": ""},
        {"op": "add_event", "key": "e", "ruling": R, "person": "I1", "type": "Death", "date": "", "description": ""},
    ]) == []
    assert any("description is a non-empty text" in e for e in validate(  # "" is none, spaces are a mistake
        [{"op": "add_event", "key": "a", "ruling": R, "person": "I1", "type": "Death", "description": "  "}]))
    assert any("impossible date" in e for e in validate(
        [{"op": "add_event", "key": "a", "ruling": R, "person": "I1", "type": "Birth", "date": "1900-02-29"}]))



def test_an_optional_text_left_blank_or_null_is_none_as_older_lists_write_it(fake, env, tmp_path):
    ops = [{"op": "add_source", "key": "s", "ruling": R, "title": "Register of Testville", "author": ""},
           {"op": "add_event", "key": "e", "ruling": R, "person": "I0001", "type": "Residence", "date": None,
            "description": None}]
    assert validate(ops) == []
    assert [r.status for r in run(fake, ops)] == ["done", "done"]
    assert any("title is a non-empty text" in e for e in validate(  # a required text still may not be blank
        [{"op": "add_source", "key": "s", "ruling": R, "title": "  "}]))
    assert any("author is a non-empty text" in e for e in validate(  # nor may an optional one hold spaces
        [{"op": "add_source", "key": "s", "ruling": R, "title": "Register of Testville", "author": " "}]))

def test_a_list_that_would_crash_halfway_writes_nothing(fake, env, tmp_path):
    ops = [{"op": "add_note", "key": "n", "ruling": R, "text": "a note", "attach": [{"person": "I0001"}]},
           {"op": "add_citation", "key": "c", "ruling": R, "source": "S0001", "page": "p. 1",
            "attach": [{"person": "I0001"}], "confidence": "high"}]
    changes = tmp_path / "changes.jsonl"
    changes.write_text("".join(json.dumps(o) + "\n" for o in ops))
    assert main([str(changes), "--apply", "--retry-wait", "0"]) == 2
    assert fake.store["notes"] == {} and fake.writes() == []


def test_an_op_that_crashes_is_an_error_that_stops_the_run(fake):
    ops = [{"op": "add_note", "key": "n", "ruling": R, "text": "a note", "attach": [{"person": "I0001"}]},
           {"op": "add_citation", "key": "c", "ruling": R, "source": "S0001", "page": "p. 1",
            "attach": [{"person": "I0001"}], "confidence": "high"},  # what validate refuses, run without it
           {"op": "add_note", "key": "n2", "ruling": R, "text": "never reached", "attach": [{"person": "I0001"}]}]
    res = run(fake, ops)
    assert [r.status for r in res] == ["done", "error"]
    assert res[1].actions[-1].startswith("error: ValueError"), res[1].actions


LOCKED = "sqlite3.OperationalError: database is locked"


def _script(fake, *steps):
    """Answer requests by a script. Each step (method, path prefix, what) is used once, in order; requests that do
    not match the next step go through. `what` is "lost" (done, then the connection drops), a Resp (done, then that
    answer) or a function of the fake (not done; its return value is the answer)."""
    steps, real = list(steps), fake.request

    def request(method, url, **kw):
        path = urlparse(url).path
        if steps and method == steps[0][0] and path.startswith(steps[0][1]):
            what = steps.pop(0)[2]
            if callable(what):
                fake.calls.append((method, path))
                return what(fake)
            real(method, url, **kw)
            if what == "lost":
                raise requests.ConnectionError("connection dropped after the server did the work")
            return what
        return real(method, url, **kw)

    fake.request = request


def _refused(fake):
    return Resp(500, LOCKED)


def _rename(fake):  # another writer changes the note, and this write is refused
    fake.store["notes"]["n1"]["text"]["string"] = "Father read as Remy (?)"
    return Resp(500, LOCKED)


def _crash(fake):
    raise RuntimeError("the client broke")


NOTE_OP = {"op": "update_note", "key": "u", "ruling": R, "note": "N0001",
           "set": {"text": "Father read as Rene (?)"}, "expect": "Father read as Denis (?)"}


def _lost_lines(res):
    return [a for a in res.actions if a.startswith("answer lost")]


@pytest.mark.parametrize("answer", [Resp(500, LOCKED), Resp(200, LOCKED)])
def test_a_write_that_lands_and_answers_locked_is_done_with_the_lost_answer(fake, answer):
    _note(fake, "n1", "Father read as Denis (?)")
    _script(fake, ("PUT", "/api/notes/", answer))
    res = run(fake, [NOTE_OP])[0]
    assert res.status == "done" and res.answer_lost, res.actions
    assert _lost_lines(res) == ["answer lost on try 1: PUT /notes/n1; the re-read shows the change in place"], res.actions
    assert fake.store["notes"]["n1"]["text"]["string"] == "Father read as Rene (?)"


def test_each_lost_answer_is_named_once_with_its_own_try(fake):
    _note(fake, "n1", "Father read as Denis (?)")
    _script(fake, ("PUT", "/api/notes/", _refused), ("PUT", "/api/notes/", "lost"))
    res = run(fake, [NOTE_OP])[0]
    assert res.status == "done" and res.answer_lost, res.actions
    assert [a.split(";")[0] for a in _lost_lines(res)] == [
        "answer lost on try 1: PUT /notes/n1", "answer lost on try 2: PUT /notes/n1"], res.actions


def test_a_held_write_whose_answer_was_lost_is_not_the_answer_of_the_op_that_flushed_it(fake):
    fake.add("people", {"handle": "k0", "primary_name": {"first_name": "K0", "surname_list": [{"surname": "Kit"}]}})
    _script(fake, ("PUT", "/api/people/anna", "lost"),  # the held association write
            ("PUT", "/api/people/anna", _refused))  # set_attribute's own write, refused once
    ops = [{"op": "add_association", "key": "a", "ruling": R, "person": "I0001", "target": "I0002", "relation": "DNA"},
           {"op": "set_attribute", "key": "s", "ruling": R, "target": {"person": "I0001"}, "type": "Nickname", "value": "Nan"}]
    res = run(fake, ops)
    assert [r.status for r in res] == ["done", "done"], [r.actions for r in res]
    assert not res[1].answer_lost
    assert _lost_lines(res[1]) == [
        "answer lost on try 1: PUT /people/anna; the re-read did not find it, so this try wrote it"
    ], res[1].actions
    anna = fake.store["people"]["anna"]
    assert len(anna["person_ref_list"]) == 1 and len(anna["attribute_list"]) == 1


def test_an_op_that_gives_up_says_the_server_may_hold_each_lost_write(fake):
    _note(fake, "n1", "Father read as Denis (?)")
    fake.busy = 10
    res = run(fake, [NOTE_OP])[0]
    assert res.status == "error" and not res.answer_lost
    assert _lost_lines(res) == [f"answer lost on try {n}: PUT /notes/n1; the server may hold that write"
                                for n in (1, 2, 3)], res.actions
    assert res.actions[-1].endswith("(gave up after 3 tries)")


@pytest.mark.parametrize("second, status, last", [
    (None, "conflict", "conflict: Note N0001 no longer reads as expected"),
    (("GET", "/api/notes/", _crash), "error", "error: RuntimeError: the client broke"),
    (("PUT", "/api/notes/", lambda f: Resp(400, "bad request")), "error", "error: "),
])
def test_an_op_that_ends_in_conflict_or_error_says_the_server_may_hold_the_lost_write(fake, second, status, last):
    _note(fake, "n1", "Father read as Denis (?)")
    _script(fake, ("PUT", "/api/notes/", _rename if second is None else _refused), *([second] if second else []))
    res = run(fake, [NOTE_OP])[0]
    assert res.status == status, res.actions
    assert _lost_lines(res) == ["answer lost on try 1: PUT /notes/n1; the server may hold that write"], res.actions
    assert res.actions[-1].startswith(last), res.actions


# -- the connection: User-Agent, sync token, GWT_URL ---------------------------


def test_every_request_names_the_toolkit(fake, scan):
    run(fake, full_list(scan))
    assert len(fake.headers) == len(fake.calls) > 5
    for (method, path), headers in zip(fake.calls, fake.headers):
        ua = headers.get("User-Agent", "")
        assert ua.startswith("gramps-web-toolkit/"), (method, path, headers)
        assert "(gwt-apply; +https://github.com/GeiserX/gramps-web-toolkit)" in ua


def test_a_sync_token_logs_in_without_a_password(fake, tmp_path, env, monkeypatch, capsys):
    fake.sync_tokens["persistent-sync-token"] = "tree-test"
    for k in ("GWT_USERNAME", "GWT_PASSWORD"):
        monkeypatch.delenv(k)
    monkeypatch.setenv("GWT_TOKEN", "persistent-sync-token")
    assert main(["--save-snapshot", str(tmp_path / "s.json.gz")]) == 0, capsys.readouterr().err
    assert ("POST", "/api/token/sync/") in fake.calls and ("POST", "/api/token/") not in fake.calls
    sent = fake.headers[fake.calls.index(("POST", "/api/token/sync/"))]
    assert "Authorization" not in sent


def test_a_sync_token_is_exchanged_again_after_a_401(fake):
    fake.sync_tokens["persistent-sync-token"] = "tree-test"
    c = Client("https://gw.example", "", "", token="persistent-sync-token", tree="tree-test", dry_run=False,
               session=fake)
    op = {"op": "add_note", "key": "n", "ruling": R, "text": "x", "attach": [{"person": "I0001"}]}
    c.login()
    fake.expire = 1  # the short-lived JWT runs out mid-run; no refresh token to fall back on
    res = Applier(c, retries=1, retry_wait=0).run([op])
    assert res[0].status == "done", res[0].actions
    assert fake.calls.count(("POST", "/api/token/sync/")) == 2


def test_a_sync_token_for_another_tree_is_refused(fake):
    fake.sync_tokens["persistent-sync-token"] = "another-tree"
    c = Client("https://gw.example", "", "", token="persistent-sync-token", tree="tree-test", session=fake)
    with pytest.raises(ApiError, match="expected"):
        c.login()


def test_a_trailing_api_in_the_url_is_dropped(fake):
    for url in ("https://gw.example/api", "https://gw.example/api/", "https://gw.example/"):
        fake.calls.clear()
        Client(url, "u", "pw", session=fake).login()
        assert fake.calls == [("POST", "/api/token/")], url


def test_a_url_without_a_scheme_is_a_settings_error(env, monkeypatch, capsys):
    monkeypatch.setenv("GWT_URL", "gw.example")
    assert main(["--save-snapshot", "never.json.gz"]) == 2
    err = capsys.readouterr().err
    assert "GWT_URL" in err and "http" in err and "snapshot:" not in err


# -- one test per row of apply/mutants.txt: each fails when its guard is mutated away --


def test_preflight_flags_a_namesake_who_shares_a_spouse(fake):
    from gwt_apply.dupes import preflight
    fake.add("people", {"handle": "bob", "gender": 1, "primary_name": {"first_name": "Bob", "surname_list": [{"surname": "Sample"}]}})  # I0002
    fake.add("families", {"handle": "fam", "father_handle": "bob", "mother_handle": "anna"})
    # no birth years and no parents on either side: only the shared husband ties the new Anna to I0001
    ops = [
        {"op": "add_person", "key": "p", "ruling": R, "first_name": "Anna", "surname": "Example Other"},
        {"op": "add_family", "key": "f", "ruling": R, "father": "I0002", "mother": "key:p"},
    ]
    problems = preflight(ops, Client("https://gw.example", "u", "pw", session=fake))
    assert len(problems) == 1 and "I0001" in problems[0] and "a spouse in common" in problems[0], problems


def test_an_event_type_in_another_case_is_the_same_event(fake):
    ev = fake.add("events", {"handle": "bap", "type": "Baptism", "date": parse_date("1890-03-20")})
    fake.store["people"]["anna"]["event_ref_list"] = [{"ref": ev["handle"], "role": "Primary"}]
    res = run(fake, [{"op": "add_event", "key": "e", "ruling": R, "person": "I0001", "type": "baptism", "date": "1890-03-20"}])
    assert res[0].status == "skip", res[0].actions
    assert fake.writes() == []


def test_database_is_locked_is_retried_whatever_the_status(fake):
    fake.busy, fake.busy_status = 2, 400
    res = run(fake, [{"op": "add_note", "key": "n", "ruling": R, "text": "x", "attach": [{"person": "I0001"}]}])
    assert res[0].status == "done", res[0].actions
    assert fake.store["people"]["anna"]["note_list"] == [stable_handle("n")]


def test_update_event_refuses_a_place_given_as_a_handle():
    errors = "\n".join(validate([{"op": "update_event", "key": "u", "ruling": R, "event": "E0001",
                                  "set": {"place": "0123b4e9269542049080d7a4c798"}}]))
    assert "is neither key:<key> nor a Gramps ID" in errors


def test_add_place_with_the_same_name_and_parent_but_another_type_is_a_new_place(fake):
    fake.add("places", {"handle": "town", "name": {"value": "Oakford"}, "place_type": "Town", "placeref_list": [{"ref": "plc1"}]})
    ops = [{"op": "add_place", "key": "pl", "ruling": R, "name": "Oakford", "type": "Parish", "enclosed_by": "P0001"}]
    res = run(fake, ops)
    assert res[0].status == "done", res[0].actions
    assert fake.store["places"][stable_handle("pl")]["place_type"] == "Parish"


def test_a_birth_where_the_person_is_only_a_witness_does_not_block_their_own(fake):
    ev = fake.add("events", {"handle": "other", "type": "Birth", "date": parse_date("1910-06-01")})
    fake.store["people"]["anna"]["event_ref_list"] = [{"ref": ev["handle"], "role": "Witness"}]
    res = run(fake, [{"op": "add_event", "key": "b", "ruling": R, "person": "I0001", "type": "Birth", "date": "1885"}])
    assert res[0].status == "done", res[0].actions
    assert fake.store["people"]["anna"]["birth_ref_index"] == 1


def test_merge_person_refuses_an_unknown_use_loser_vital():
    errors = "\n".join(validate([{"op": "merge_person", "key": "m", "ruling": R, "winner": "I0001", "loser": "I0002",
                                  "use_loser": ["marriage"]}]))
    assert "use_loser takes ['birth', 'death']" in errors


# -- reuse only what is the same: a new citation, event, note or source never folds into one that differs --

def _two_baptisms(fake):
    for h in ("bap-a", "bap-b"):
        fake.add("events", {"handle": h, "type": "Baptism", "date": parse_date("1890-03-16")})  # E0001, E0002
    return [{"event": "E0001"}], [{"event": "E0002"}]


def _weak_and_firm(a, b):
    page = "book 3, folio 12v"
    return [{"op": "add_citation", "key": "weak", "ruling": R, "source": "S0001", "page": page, "confidence": 1, "attach": a},
            {"op": "add_citation", "key": "firm", "ruling": R, "source": "S0001", "page": page, "confidence": 3, "attach": b}]


def test_two_citations_of_one_page_that_differ_are_a_validation_error(fake):
    a, b = _two_baptisms(fake)
    errors = "\n".join(validate(_weak_and_firm(a, b)))
    assert "op 2" in errors and "confidence" in errors and "own page" in errors, errors
    other_date = [dict(_weak_and_firm(a, b)[0], date="1890"), dict(_weak_and_firm(a, b)[0], key="w2", attach=b, date="1891")]
    assert "date" in "\n".join(validate(other_date))
    same = [_weak_and_firm(a, b)[0], dict(_weak_and_firm(a, b)[0], key="w2", attach=b)]
    assert validate(same) == []  # the same citation on two targets
    no_confidence = [_weak_and_firm(a, b)[0], {k: v for k, v in _weak_and_firm(a, b)[1].items() if k != "confidence"}]
    assert validate(no_confidence) == []  # sets nothing that differs: it reuses


def test_a_firm_citation_never_reuses_a_weak_one_of_the_same_page(fake):
    a, b = _two_baptisms(fake)
    weak, firm = _weak_and_firm(a, b)
    assert run(fake, [weak])[0].status == "done"
    res = run(fake, [firm])
    assert res[0].status == "conflict", res[0].actions
    assert "C0001" in res[0].actions[-1] and "confidence 1" in res[0].actions[-1] and "update_citation" in res[0].actions[-1]
    assert fake.store["events"]["bap-b"]["citation_list"] == []  # event B unchanged
    assert len(fake.store["citations"]) == 1


def test_an_identical_citation_is_reused_and_says_so(fake):
    a, b = _two_baptisms(fake)
    weak, _ = _weak_and_firm(a, b)
    run(fake, [weak])
    res = run(fake, [dict(weak, key="weak2", attach=b)])
    assert res[0].status == "done", res[0].actions
    assert any(x.startswith("reused C0001 (same ") for x in res[0].actions), res[0].actions
    assert fake.store["events"]["bap-b"]["citation_list"] == [stable_handle("weak")]
    again = run(fake, [dict(weak, key="weak2", attach=b)])
    assert again[0].status == "skip"  # a rerun of an applied list still skips


def test_a_second_undated_residence_with_another_place_is_not_skipped(fake):
    fake.add("places", {"handle": "plc2", "name": {"value": "Shelbyville"}})  # P0002
    ops = [{"op": "add_event", "key": "res1", "ruling": R, "person": "I0001", "type": "Residence", "place": "P0001"},
           {"op": "add_event", "key": "res2", "ruling": R, "person": "I0001", "type": "Residence", "place": "P0002"}]
    res = run(fake, ops)
    assert res[0].status == "done"
    assert res[1].status == "conflict", res[1].actions
    assert "place" in res[1].actions[-1] and "E0001" in res[1].actions[-1]
    assert len(fake.store["events"]) == 1
    # the same place again is the same residence: reused, and a rerun skips
    res = run(fake, [dict(ops[0], key="res3")])
    assert res[0].status == "skip" and any(x.startswith("reused E0001 (same ") for x in res[0].actions), res[0].actions
    assert run(fake, [ops[0]])[0].status == "skip"



def test_a_citation_of_the_same_page_with_another_date_is_not_reused(fake):
    a, b = _two_baptisms(fake)
    weak, _ = _weak_and_firm(a, b)
    assert run(fake, [dict(weak, date="1890-03-16")])[0].status == "done"
    res = run(fake, [dict(weak, key="w2", attach=b, date="1891")])
    assert res[0].status == "conflict", res[0].actions
    assert "C0001" in res[0].actions[-1] and "date 1890-03-16" in res[0].actions[-1], res[0].actions
    assert fake.store["events"]["bap-b"]["citation_list"] == []
    assert len(fake.store["citations"]) == 1


def test_a_second_undated_residence_with_another_description_is_not_skipped(fake):
    ops = [{"op": "add_event", "key": "r1", "ruling": R, "person": "I0001", "type": "Residence", "description": "farm"},
           {"op": "add_event", "key": "r2", "ruling": R, "person": "I0001", "type": "Residence", "description": "mill"}]
    res = run(fake, ops)
    assert res[0].status == "done"
    assert res[1].status == "conflict", res[1].actions
    assert "description 'farm'" in res[1].actions[-1] and "E0001" in res[1].actions[-1], res[1].actions
    assert len(fake.store["events"]) == 1

def test_a_note_of_another_type_with_the_same_text_is_not_reused(fake):
    ops = [{"op": "add_note", "key": "n1", "ruling": R, "type": "Research", "text": "Born in the mill house.",
            "attach": [{"person": "I0001"}]},
           {"op": "add_note", "key": "n2", "ruling": R, "type": "Transcript", "text": "Born in the mill house.",
            "attach": [{"person": "I0001"}]}]
    res = run(fake, ops)
    assert res[0].status == "done"
    assert res[1].status == "conflict", res[1].actions
    assert "type" in res[1].actions[-1] and "N0001" in res[1].actions[-1]
    assert len(fake.store["notes"]) == 1
    res = run(fake, [dict(ops[0], key="n3")])
    assert res[0].status == "skip" and any(x.startswith("reused N0001 (same ") for x in res[0].actions), res[0].actions


def test_a_source_with_the_same_title_but_another_author_is_not_reused(fake):
    fake.store["sources"]["src1"]["author"] = "Parish of Springfield"
    op = {"op": "add_source", "key": "s", "ruling": R, "title": "Parish register of Springfield, book 3"}
    res = run(fake, [dict(op, author="Diocese of Capital City")])
    assert res[0].status == "conflict", res[0].actions
    assert "author" in res[0].actions[-1] and "S0001" in res[0].actions[-1]
    assert len(fake.store["sources"]) == 1
    res = run(fake, [dict(op, author="Parish of Springfield")])
    assert res[0].status == "skip" and any(x.startswith("reused S0001 (same ") for x in res[0].actions), res[0].actions


# ---- where the log goes and what it says


def _lines(path):
    return [json.loads(x) for x in open(path, encoding="utf-8").read().splitlines() if x]


def _changes(tmp_path, ops, name="changes.jsonl"):
    p = tmp_path / name
    p.write_text("".join(json.dumps(o) + "\n" for o in ops))
    return p


def test_a_dry_run_and_a_snapshot_leave_no_log_anywhere(fake, tmp_path, env, state_home, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    changes = _changes(tmp_path, [{"op": "add_note", "key": "n", "ruling": R, "text": "x", "attach": [{"person": "I0001"}]}])
    assert main([str(changes)]) == 0
    assert main(["--save-snapshot", str(tmp_path / "s.json.gz")]) == 0
    assert list(cwd.iterdir()) == []
    assert not state_home.exists()


def test_a_real_run_logs_under_the_state_dir_and_names_it_first(fake, tmp_path, env, state_home, monkeypatch, capsys):
    import hashlib
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    changes = _changes(tmp_path, [{"op": "add_person", "key": "p-tom", "ruling": R, "first_name": "Tom", "surname": "Sample"}])
    argv = [str(changes), "--apply", "--retry-wait", "0"]
    assert main(argv) == 0
    first = capsys.readouterr().out.splitlines()[0]
    logs = list((state_home / "gwt-apply" / "tree-test").glob("gwt-apply-*.jsonl"))
    assert len(logs) == 1 and str(logs[0]) in first, first
    assert list(cwd.iterdir()) == []
    lines = _lines(logs[0])
    start, end = lines[0], lines[-1]
    assert start["event"] == "start" and start["argv"] == argv and start["list"] == str(changes)
    assert start["list_sha256"] == hashlib.sha256(changes.read_bytes()).hexdigest()
    assert start["tree"] == "tree-test" and start["snapshot"] is None and start["run"] in first and start["version"]
    assert end["event"] == "end" and end["exit"] == 0 and end["not_run"] == 0
    assert end["counts"] == {"done": 1, "skip": 0, "conflict": 0, "error": 0}
    # the create's line names the Gramps ID the server gave the new person
    made = [e for e in lines if e.get("method") == "POST" and e.get("path") == "/objects/"
            and e["body"][0]["_class"] == "Person"]
    assert [e["gramps_id"] for e in made] == [fake.store["people"][stable_handle("p-tom")]["gramps_id"]]


def test_a_preflight_refusal_is_in_the_log(fake, tmp_path, env, state_home):
    changes = _changes(tmp_path, [
        {"op": "add_person", "key": "p2", "ruling": R, "first_name": "Rose", "surname": "Sample"},
        {"op": "add_person", "key": "p3", "ruling": R, "first_name": "rose", "surname": "Sámple"},
    ])
    log = tmp_path / "run.jsonl"
    assert main([str(changes), "--apply", "--log", str(log), "--retry-wait", "0"]) == 1
    lines = _lines(next(tmp_path.glob("run.2*.jsonl")))
    refused = [e for e in lines if e.get("event") == "refused"]
    assert len(refused) == 1 and refused[0]["reason"] == "preflight" and "'p3'" in refused[0]["problems"][0]
    assert lines[-1]["event"] == "end" and lines[-1]["exit"] == 1


def test_a_dry_run_logs_its_writes_as_planned_never_as_status_null(fake, tmp_path, env, scan):
    changes = _changes(tmp_path, full_list(scan))
    assert main([str(changes), "--log", str(tmp_path / "dry.jsonl")]) == 0
    lines = _lines(next(tmp_path.glob("dry.2*.jsonl")))
    writes = [e for e in lines if e.get("method") in ("POST", "PUT", "DELETE") and not e["path"].startswith("/token/")]
    assert writes and all(e.get("planned") is True and "status" not in e for e in writes), writes
    assert lines[0]["event"] == "start" and lines[-1]["event"] == "end" and lines[-1]["exit"] == 0


def test_a_big_put_logs_a_hash_and_revert_still_restores_it(fake, tmp_path, env):
    old, new = "a" * 100_000, "b" * 100_000
    _note(fake, "n1", old)
    pre = str(tmp_path / "pre.json.gz")
    assert main(["--save-snapshot", pre]) == 0
    op = {"op": "update_note", "key": "u", "ruling": R, "note": "N0001", "set": {"text": new}, "expect": old}
    assert main([str(_changes(tmp_path, [op], "dry.changes.jsonl")), "--log", str(tmp_path / "dry.jsonl")]) == 0
    planned = [e for e in _lines(next(tmp_path.glob("dry.2*.jsonl"))) if e.get("method") == "PUT"]
    assert len(planned) == 1 and "body" not in planned[0] and planned[0]["body_digest"]["bytes"] > 65536
    log = _apply_list(tmp_path, [op], "big")
    put = [e for e in _lines(log) if e.get("method") == "PUT"]
    assert len(put) == 1 and "body" not in put[0] and put[0]["body_digest"]["bytes"] > 65536
    assert len(put[0]["body_digest"]["sha256"]) == 64
    assert fake.store["notes"]["n1"]["text"]["string"] == new
    assert _undo(log, pre, "--apply") == 0
    assert fake.store["notes"]["n1"]["text"]["string"] == old


def test_a_big_put_edited_since_the_run_is_left_alone(fake, tmp_path, env):
    old, new = "a" * 100_000, "b" * 100_000
    _note(fake, "n1", old)
    pre = str(tmp_path / "pre.json.gz")
    assert main(["--save-snapshot", pre]) == 0
    log = _apply_list(tmp_path, [{"op": "update_note", "key": "u", "ruling": R, "note": "N0001",
                                  "set": {"text": new}, "expect": old}], "big")
    fake.store["notes"]["n1"]["text"]["string"] = new + " edited by hand"
    assert _undo(log, pre, "--apply") == 1
    assert fake.store["notes"]["n1"]["text"]["string"] == new + " edited by hand"


def test_a_login_into_another_tree_leaves_a_refused_line(fake, tmp_path, env, state_home, capsys):
    fake.tree = "tree-other"
    changes = _changes(tmp_path, [{"op": "add_note", "key": "n", "ruling": R, "text": "x", "attach": [{"person": "I0001"}]}])
    assert main([str(changes), "--apply", "--retry-wait", "0"]) == 1
    assert "expected 'tree-test'" in capsys.readouterr().err
    logs = list((state_home / "gwt-apply" / "tree-test").glob("gwt-apply-*.jsonl"))
    assert len(logs) == 1
    events = [e.get("event") for e in _lines(logs[0]) if e.get("event")]
    assert events == ["start", "refused", "end"]
    assert fake.writes() == []


def test_a_missing_change_list_is_one_line_and_exit_2(tmp_path, capsys):
    assert main([str(tmp_path / "missing.jsonl")]) == 2
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "missing.jsonl" in err, err


def test_a_failed_login_prints_the_run_id_and_writes_no_log(fake, tmp_path, env, state_home, monkeypatch, capsys):
    monkeypatch.setenv("GWT_TOKEN", "a-revoked-token")  # the fake answers 403 to a token it does not know
    changes = _changes(tmp_path, [{"op": "add_note", "key": "n", "ruling": R, "text": "x", "attach": [{"person": "I0001"}]}])
    assert main([str(changes), "--apply", "--retry-wait", "0"]) == 1
    out = capsys.readouterr()
    assert "run 2" in out.out and "login failed" in out.err, out
    assert not state_home.exists()
