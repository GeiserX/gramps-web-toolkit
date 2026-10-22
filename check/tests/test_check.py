import base64
import copy
import gzip
import io
import json
import ssl
import urllib.parse

import pytest

from gwt_check import checks, selftest
from gwt_check.cli import main
from gwt_check.client import ApiError, Client
from gwt_check.tree import load


def test_selftest_passes():
    assert selftest.selftest() == 0


@pytest.mark.parametrize("name", sorted(checks.CHECKS))
def test_selftest_goes_red_when_any_check_is_silenced(monkeypatch, name):
    """Every check has a plant: silence one and the selftest must fail."""
    import gwt_check.cli as cli

    for attr in ("run_checks", "diff", "compare_ahnentafel"):
        real = getattr(cli, attr)

        def silenced(*a, _real=real, **k):
            findings, info = _real(*a, **k)
            return [f for f in findings if f["check"] != name], info

        monkeypatch.setattr(cli, attr, silenced)
    assert selftest.selftest() == 1


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_exit_codes_and_known(tmp_path, capsys):
    clean = selftest.clean_tree()
    assert main(["check", _write(tmp_path, "clean.json", clean)]) == 0

    planted = copy.deepcopy(clean)
    selftest.p_orphan_note(planted)
    selftest.p_surname_marker(planted)
    dump = _write(tmp_path, "planted.json", planted)
    report = str(tmp_path / "report.json")
    assert main(["check", dump, "--json", report]) == 1
    found = {(f["check"], f["key"]) for f in json.load(open(report))["findings"]}
    assert found == {("orphan-note", "N0099"), ("surname-marker", "I0007 Vidal [¿?]")}

    # the first run's report accepts its findings; a new problem still fails
    assert main(["check", dump, "--known", report]) == 0
    selftest.p_orphan_media(planted)
    assert main(["check", _write(tmp_path, "more.json", planted), "--known", report]) == 1
    out = capsys.readouterr().out
    assert "FAIL orphan-media: 1 new" in out and "known orphan-note: 0 new, 1 known" in out

    assert main(["check", str(tmp_path / "absent.json")]) == 2
    assert main(["check", _write(tmp_path, "bad.json", [1, 2])]) == 2
    assert main(["check", dump, "--known", _write(tmp_path, "k.json", [{"check": "x"}])]) == 2


def _to_xml(data):
    """A minimal Gramps XML backup of the synthetic tree, gzipped like a real .gramps file."""
    ns = "http://gramps-project.org/xml/1.7.1/"
    tags = {"people": "person", "families": "family", "events": "event", "places": "placeobj",
            "citations": "citation", "sources": "source", "media": "object", "notes": "note",
            "repositories": "repository"}
    out = [f'<?xml version="1.0" encoding="UTF-8"?><database xmlns="{ns}">']
    for t, tag in tags.items():
        for o in data[t]:
            inner = ""
            if t == "people":
                inner = "".join(f'<childof hlink="_{h}"/>' for h in o["parent_family_list"])
                inner += "".join(f'<parentin hlink="_{h}"/>' for h in o["family_list"])
            if t == "families":
                inner = "".join(f'<{k} hlink="_{o[k + "_handle"]}"/>' for k in ("father", "mother") if o[k + "_handle"])
                inner += "".join(f'<childref hlink="_{c["ref"]}"/>' for c in o["child_ref_list"])
            out.append(f'<{tag} handle="_{o["handle"]}" change="{o["change"]}" id="{o["gramps_id"]}">{inner}</{tag}>')
    out.append("</database>")
    return gzip.compress("".join(out).encode())


def test_gramps_backup_as_the_earlier_tree(tmp_path, capsys):
    clean = selftest.clean_tree()
    backup = tmp_path / "before.gramps"
    backup.write_bytes(_to_xml(clean))
    old = load(str(backup))
    assert old.partial and old.counts()["people"] == 8
    assert old.objs["families"]["hF0001"]["child_ref_list"] == [{"ref": "hI0001"}, {"ref": "hI0008"}]

    after = copy.deepcopy(clean)
    selftest.p_ahnentafel_lost(after)
    selftest.p_object_removed(after)
    dump = _write(tmp_path, "after.json", after)
    report = str(tmp_path / "r.json")
    assert main(["check", dump, "--since", str(backup), "--root", "I0001", "--json", report]) == 1
    r = json.load(open(report))
    assert {(f["check"], f["key"]) for f in r["findings"]} == {
        ("missing-object", "notes N0001"), ("ahnentafel-lost", "6 I0006")}
    assert r["diff"]["changed"]["families"] == []  # the plant left change times alone

    # a backup is only ever the earlier tree
    assert main(["check", str(backup)]) == 2


def test_tag_scope_limits_duplicates_and_diff(tmp_path):
    clean = selftest.clean_tree()
    tree = copy.deepcopy(clean)
    selftest.p_dup_person_birth(tree)  # untagged new person: out of a --tag scope
    report = str(tmp_path / "r.json")
    assert main(["check", _write(tmp_path, "t.json", tree), "--tag", "run-1", "--json", report]) == 0
    r = json.load(open(report))
    assert r["scope"] == 3  # I0001, F0001, E0001 carry the tag
    assert main(["check", _write(tmp_path, "t.json", tree)]) == 1  # whole tree: found

    no_tags = {k: v for k, v in tree.items() if k != "tags"}
    assert main(["check", _write(tmp_path, "nt.json", no_tags), "--tag", "run-1"]) == 2


def test_fuzzy_names_leave_siblings_alone(tmp_path):
    """A sibling «Tomás Mateo» next to «Tomás», same parents, no birth year: not a duplicate."""
    tree = selftest.clean_tree()
    p = selftest._new_person(tree, "I0090", "Tomás Mateo", "Ortega Sanz", parent_family="hF0001")
    selftest._get(tree, "families", "F0001")["child_ref_list"].append(selftest._ref(p["handle"], frel="Birth", mrel="Birth"))
    assert main(["check", _write(tmp_path, "s.json", tree)]) == 0


def test_names_fold_accents_bv_iy_and_de():
    assert checks.norm("Benita  de Yuste Bázquez") == checks.norm("venita iuste vazquez")


class FakeServer:
    """Pages of a fake tree; counts calls; can answer 429 to the first login."""

    def __init__(self, data, *, throttle_login=False, short=None):
        self.data, self.calls, self.throttle, self.short = data, [], throttle_login, short

    def __call__(self, req, timeout):
        url = urllib.parse.urlparse(req.full_url)
        self.calls.append(url.path)
        if url.path.endswith("/token/"):
            if self.throttle:
                self.throttle = False
                return Resp(429, b"", {"Retry-After": "0"})
            claims = base64.urlsafe_b64encode(json.dumps({"tree": "tree-1"}).encode()).decode().rstrip("=")
            return Resp(200, json.dumps({"access_token": f"x.{claims}.y"}).encode())
        t = url.path.rstrip("/").rsplit("/", 1)[-1]
        q = dict(urllib.parse.parse_qsl(url.query))
        page, size = int(q["page"]), int(q["pagesize"])
        rows = self.data[t][(page - 1) * size: page * size]
        total = len(self.data[t]) + (1 if t == self.short else 0)
        return Resp(200, json.dumps(rows).encode(), {"X-Total-Count": str(total)})


class Resp(io.BytesIO):
    def __init__(self, status, body, headers=None):
        super().__init__(body)
        self.status, self.headers = status, headers or {}


def test_dump_pages_every_type_with_one_login():
    data = selftest.clean_tree()
    server = FakeServer(data, throttle_login=True)
    client = Client("https://gramps.example.org", "u", "secret-pw", tree="tree-1", opener=server,
                    sleep=lambda s: None)
    client.login()
    out = client.dump(pagesize=3, workers=2)
    assert {t: len(v) for t, v in out.items()} == {t: len(v) for t, v in data.items()}
    assert server.calls.count("/api/token/") == 2  # one 429, then the login that is reused


class Flaky:
    """Raises a network error on the first `fails` page reads (or on every one), then lets the server answer."""

    def __init__(self, server, fails, error=TimeoutError("The read operation timed out")):
        self.server, self.fails, self.error, self.raised = server, fails, error, 0

    def __call__(self, req, timeout):
        if not req.full_url.split("?")[0].endswith("/token/") and (self.fails is None or self.raised < self.fails):
            self.raised += 1
            raise self.error
        return self.server(req, timeout)


def test_a_page_that_times_out_twice_is_read_on_the_third_try():
    data = selftest.clean_tree()
    waits = []
    flaky = Flaky(FakeServer(data), 2)
    client = Client("https://gramps.example.org", "u", "pw", opener=flaky, sleep=waits.append)
    out = client.dump(pagesize=1000)
    assert {t: len(v) for t, v in out.items()} == {t: len(v) for t, v in data.items()}
    assert flaky.raised == 2 and waits == [5.0, 10.0]  # backoff doubles


def test_a_page_that_never_answers_fails_after_the_retry_limit():
    flaky = Flaky(FakeServer(selftest.clean_tree()), None, ssl.SSLError("The read operation timed out"))
    waits = []
    client = Client("https://gramps.example.org", "u", "pw", opener=flaky, sleep=waits.append)
    with pytest.raises(ApiError, match="gave up after 6 tries, last SSLError"):
        client.dump(pagesize=1000)
    assert flaky.raised == 6 and waits == [5.0, 10.0, 20.0, 40.0, 60.0]  # capped, no wait after the last


def test_dump_refuses_a_short_type_and_a_wrong_tree():
    data = selftest.clean_tree()
    client = Client("https://gramps.example.org", "u", "pw", opener=FakeServer(data, short="events"))
    with pytest.raises(ApiError, match="events: server says"):
        client.dump(pagesize=3)
    client = Client("https://gramps.example.org", "u", "pw", tree="other", opener=FakeServer(data))
    with pytest.raises(ApiError, match="expected 'other'"):
        client.login()


def test_living_media_rule_skips_the_dead_and_the_old_and_never_prints_the_number():
    from gwt_check.tree import Tree

    def findings(birth, desc, death=None, private=None):
        d = selftest.clean_tree()
        p = selftest._sensitive_media(d, "O0090", "I0090", birth, desc)
        if death:  # the role this person has in a Burial
            ev = copy.deepcopy(selftest._get(d, "events", "E0001"))
            ev.update(handle="hD90", gramps_id="E9090", type="Burial", tag_list=[])
            d["events"].append(ev)
            p["event_ref_list"].append(selftest._ref("hD90", role=death))
        if private == "person":
            p["private"] = True
        elif private == "media":
            selftest._get(d, "media", "O0090")["private"] = True
        elif private == "ref":
            p["media_list"][0]["private"] = True
        found, _ = checks.run_checks(Tree(d, source="t"))
        return [f for f in found if f["check"] == "living-media-sensitive"]

    assert findings((2001, 5, 2), "Pasaporte") != []
    assert findings((2001, 5, 2), "Acta de nacimiento") == []      # nothing sensitive in the metadata
    assert findings((2001, 5, 2), "Pasaporte", death="Primary") == []  # buried
    assert findings((2001, 5, 2), "Pasaporte", death="Witness") != []  # at someone else's burial
    assert findings((1880, 5, 2), "Pasaporte") == []              # too old to be living
    hit = findings((2001, 5, 2), "scan 12345678Z")
    assert hit and "12345678" not in str(hit)
    # already private: the person, the media or the reference
    assert [findings((2001, 5, 2), "Pasaporte", private=w) for w in ("person", "media", "ref")] == [[], [], []]


def test_living_media_patterns_need_a_valid_check_letter_and_no_bare_words():
    for text in ("scan 12345678Z", "dni_12345678Z.jpg", "nie X1234567L", "c/ Mayor 3", "C/Mayor",
                 "Carnet de identidad"):
        assert checks.sensitive_kind(text), text
    for text in ("scan-20230512a.jpg", "12345678A", "Calle de la iglesia", "direccion general", "dni wolne",
                 "nie wiem", "abc/def.jpg", "fotos/c/x.jpg"):
        assert not checks.sensitive_kind(text), text


def test_living_media_survives_an_empty_dateval():
    from gwt_check.tree import Tree

    d = selftest.clean_tree()
    selftest._sensitive_media(d, "O0090", "I0090", (2001, 5, 2), "Pasaporte")
    selftest._get(d, "events", "EI0090")["date"]["dateval"] = None
    found, _ = checks.run_checks(Tree(d, source="t"))
    assert [f["key"] for f in found if f["check"] == "living-media-sensitive"] == ["I0090 O0090"]


def test_ignore_check_leaves_a_whole_check_out_of_the_exit_code_but_still_counts_it(tmp_path, capsys):
    planted = selftest.clean_tree()
    selftest.p_private(planted)
    dump = _write(tmp_path, "private.json", planted)
    assert main(["check", dump]) == 1
    capsys.readouterr()
    assert main(["check", dump, "--ignore-check", "private"]) == 0
    assert "ignored private: 1 finding(s)" in capsys.readouterr().out

    # another problem still fails, and the report keeps the ignored one
    selftest.p_orphan_note(planted)
    report = str(tmp_path / "r.json")
    assert main(["check", _write(tmp_path, "two.json", planted), "--ignore-check", "private", "--json", report]) == 1
    data = json.load(open(report))
    assert data["ignored"] == {"private": 1}
    assert [f["check"] for f in data["new"]] == ["orphan-note"]
    assert any(f["check"] == "private" and f["ignored"] for f in data["findings"])


def test_ignore_check_refuses_a_name_that_is_not_a_check(tmp_path, capsys):
    dump = _write(tmp_path, "clean.json", selftest.clean_tree())
    assert main(["check", dump, "--ignore-check", "privat"]) == 2
    assert "no such check" in capsys.readouterr().err
    # the name is refused before any tree is read
    assert main(["check", str(tmp_path / "missing.json"), "--ignore-check", "privat"]) == 2
    assert "no such check" in capsys.readouterr().err


def test_baseline_makes_the_findings_it_already_had_known(tmp_path, capsys):
    old = selftest.clean_tree()
    selftest.p_orphan_note(old)
    old_dump = _write(tmp_path, "old.json", old)
    new = copy.deepcopy(old)
    selftest.p_surname_marker(new)
    new_dump = _write(tmp_path, "new.json", new)
    assert main(["check", new_dump]) == 1
    capsys.readouterr()
    report = str(tmp_path / "r.json")
    assert main(["check", new_dump, "--baseline", old_dump, "--json", report]) == 1
    data = json.load(open(report))
    assert [(f["check"], f["key"]) for f in data["new"]] == [("surname-marker", "I0007 Vidal [¿?]")]
    assert any(f["check"] == "orphan-note" and f["known"] for f in data["findings"])
    assert main(["check", old_dump, "--baseline", old_dump]) == 0
    # with --known as well, its reasons still show
    known = _write(tmp_path, "known.json", [{"check": "orphan-note", "key": "N0099", "reason": "kept on purpose"}])
    capsys.readouterr()
    assert main(["check", old_dump, "--baseline", old_dump, "--known", known]) == 0
    assert "known N0099: kept on purpose" in capsys.readouterr().out


def test_a_father_and_son_with_one_name_are_not_duplicates():
    from gwt_check.tree import Tree

    # three generations of one name, the younger two with no birth date: each pair shares a "relative"
    d = selftest.clean_tree()
    selftest._new_person(d, "I0097", "Mateo", "Ortega Ruiz", parent_family="hF0097", family="hF0096")
    selftest._new_family(d, "F0097", "hI0002", "", kids=["hI0097"])
    selftest._get(d, "people", "I0002")["family_list"].append("hF0097")
    selftest._new_person(d, "I0096", "Mateo", "Ortega Ruiz", parent_family="hF0096")
    selftest._new_family(d, "F0096", "hI0097", "", kids=["hI0096"])
    found, _ = checks.run_checks(Tree(d, source="t"))
    assert [f for f in found if f["check"].startswith("dup-person")] == []

    # the same two people, but not parent and child: still a duplicate pair
    d2 = selftest.clean_tree()
    selftest._new_person(d2, "I0097", "Mateo", "Ortega Ruiz", birth=(1870, 3, 2))
    found, _ = checks.run_checks(Tree(d2, source="t"))
    assert ("dup-person-birth", "I0002,I0097") in {(f["check"], f["key"]) for f in found}


def test_a_parent_and_child_with_one_name_and_one_birth_date_are_still_a_duplicate():
    from gwt_check.tree import Tree

    d = selftest.clean_tree()
    selftest._new_person(d, "I0097", "Mateo", "Ortega Ruiz", birth=(1870, 3, 2), parent_family="hF0097")
    selftest._new_family(d, "F0097", "hI0002", "", kids=["hI0097"])
    selftest._get(d, "people", "I0002")["family_list"].append("hF0097")
    found, _ = checks.run_checks(Tree(d, source="t"))
    assert ("dup-person-birth", "I0002,I0097") in {(f["check"], f["key"]) for f in found}


def test_known_may_be_a_set_of_check_and_key():
    from gwt_check.cli import check
    from gwt_check.tree import Tree

    planted = selftest.clean_tree()
    selftest.p_orphan_note(planted)
    report = check(Tree(planted, source="t"), known={("orphan-note", "N0099")})
    assert [f["key"] for f in report["findings"] if f["check"] == "orphan-note"] == ["N0099"]
    assert report["new"] == [] and all("reason" not in f for f in report["findings"])


def test_known_reasons_are_shown_and_mark_known_writes_them(tmp_path, capsys):
    planted = selftest.clean_tree()
    selftest.p_orphan_note(planted)
    dump = _write(tmp_path, "p.json", planted)
    report = str(tmp_path / "r.json")
    assert main(["check", dump, "--json", report]) == 1
    known = str(tmp_path / "known.json")
    assert main(["mark-known", report, "--check", "orphan-note", "--key", "N0099", "--reason", "kept on purpose",
                 "--out", known]) == 0
    assert json.load(open(known)) == [{"check": "orphan-note", "key": "N0099", "reason": "kept on purpose"}]
    capsys.readouterr()
    assert main(["check", dump, "--known", known]) == 0
    assert "known N0099: kept on purpose" in capsys.readouterr().out
    # a second call adds, never duplicates; a key that is not in the report, or no reason, is refused
    assert main(["mark-known", report, "--check", "orphan-note", "--key", "N0099", "--reason", "again", "--out", known]) == 0
    assert len(json.load(open(known))) == 1
    assert main(["mark-known", report, "--check", "orphan-note", "--key", "N0001", "--reason", "x", "--out", known]) == 2
    assert main(["mark-known", report, "--check", "orphan-note", "--key", "N0099", "--reason", " ", "--out", known]) == 2
    # an --out that is not a known file is refused with exit 2, not a traceback, and left as it was
    broken = tmp_path / "broken.json"
    broken.write_text("not json")
    assert main(["mark-known", report, "--check", "orphan-note", "--key", "N0099", "--reason", "x",
                 "--out", str(broken)]) == 2
    assert broken.read_text() == "not json"


class LiveServer:
    """A fake tree that someone writes to while it is read: `write(server)` runs before chosen requests."""

    def __init__(self, data, write=None):
        self.data, self.write, self.calls = data, write, []

    def __call__(self, req, timeout):
        url = urllib.parse.urlparse(req.full_url)
        self.calls.append((url.path, url.query))
        if url.path.endswith("/token/"):
            claims = base64.urlsafe_b64encode(json.dumps({"tree": "tree-1"}).encode()).decode().rstrip("=")
            return Resp(200, json.dumps({"access_token": f"x.{claims}.y"}).encode())
        t = url.path.rstrip("/").rsplit("/", 1)[-1]
        q = dict(urllib.parse.parse_qsl(url.query))
        if self.write:
            self.write(self, t, q)
        rows = self.data[t]
        if "handles" in q:
            wanted = q["handles"].split(",")
            rows = [o for o in rows if o["handle"] in wanted]
        if "keys" in q:
            rows = [{k: o.get(k) for k in q["keys"].split(",")} for o in rows]
        headers = {"X-Total-Count": str(len(rows))}
        if "page" in q:
            page, size = int(q["page"]), int(q["pagesize"])
            rows = rows[(page - 1) * size: page * size]
        return Resp(200, json.dumps(rows).encode(), headers)

    def tick(self, obj):
        obj["change"] = obj.get("change", 0) + 1


def attach_note_mid_read(server, t, q):
    """While the notes are paged, a new note goes in at the front and gets attached to I0001."""
    if t == "notes" and q.get("page") == "2" and not any(n["gramps_id"] == "N0099" for n in server.data["notes"]):
        note = {**copy.deepcopy(server.data["notes"][0]), "handle": "hN0099", "gramps_id": "N0099"}
        server.data["notes"].insert(0, note)
        person = next(p for p in server.data["people"] if p["gramps_id"] == "I0001")
        person["note_list"] = [*person.get("note_list", []), "hN0099"]
        server.tick(person)


@pytest.fixture
def live(monkeypatch):
    def serve(data, write=None):
        server = LiveServer(data, write)
        import gwt_check.client as clientmod
        monkeypatch.setattr(clientmod.urllib.request, "urlopen", server)
        monkeypatch.setattr(clientmod.time, "sleep", lambda s: None)
        for k, v in {"GWT_URL": "https://gramps.example.org", "GWT_USERNAME": "u", "GWT_PASSWORD": "pw"}.items():
            monkeypatch.setenv(k, v)
        return server
    return serve


def test_a_write_during_the_check_does_not_stop_it_or_leave_a_false_finding(live, tmp_path, capsys):
    data = selftest.clean_tree()
    live(data, attach_note_mid_read)
    report = str(tmp_path / "r.json")
    assert main(["check", "--pagesize", "1", "--json", report]) == 0, capsys.readouterr().err
    r = json.load(open(report))
    assert r["findings"] == [] and r["passes"] == 2  # the person read before the write was read again
    assert r["counts"]["notes"] == len(data["notes"])


def test_the_lock_note_of_a_running_apply_is_not_part_of_the_tree(tmp_path):
    from gwt_check import tree as tree_mod
    data = selftest.clean_tree()
    data["notes"].append({**copy.deepcopy(data["notes"][0]), "handle": tree_mod.APPLY_LOCK, "gramps_id": "GWTLOCK",
                          "private": True})
    assert main(["check", _write(tmp_path, "t.json", data)]) == 0


def test_live_keeps_the_old_strict_read(live, capsys):
    live(selftest.clean_tree(), attach_note_mid_read)
    assert main(["check", "--live", "--pagesize", "1"]) == 2
    assert "notes: server says" in capsys.readouterr().err


def test_a_tree_that_keeps_changing_settles_when_the_findings_stop_changing(live, tmp_path):
    def busy(server, t, q):  # a write on every stamp list: some other person's name keeps changing
        if q.get("keys"):
            p = server.data["people"][-1]
            p["primary_name"]["first_name"] += "x"
            server.tick(p)
    live(selftest.clean_tree(), busy)
    report = str(tmp_path / "r.json")
    assert main(["check", "--json", report]) == 0
    assert json.load(open(report))["passes"] == 4


def busy_people(server):
    """Some other person's name keeps changing, so no pass ever finds the tree still."""
    p = server.data["people"][-1]
    p["primary_name"]["first_name"] += "x"
    server.tick(p)


def test_a_write_caught_halfway_in_one_pass_is_left_out(live, tmp_path, capsys):
    """Each pass sees a note made and not attached yet; the next request attaches it."""
    def writer(server, t, q):
        person = next(p for p in server.data["people"] if p["gramps_id"] == "I0001")
        pending = [n["handle"] for n in server.data["notes"]
                   if n["handle"].startswith("hW") and n["handle"] not in person.get("note_list", [])]
        if pending:
            person["note_list"] = person.get("note_list", []) + pending
            server.tick(person)
        if q.get("keys") and t == "notes":
            n = len(server.data["notes"])
            server.data["notes"].append({**copy.deepcopy(server.data["notes"][0]), "handle": f"hW{n}", "gramps_id": f"W{n}"})
        if q.get("keys") and t == "people":
            busy_people(server)
    live(selftest.clean_tree(), writer)
    report = str(tmp_path / "r.json")
    assert main(["check", "--json", report]) == 0, capsys.readouterr().err
    r = json.load(open(report))
    assert r["findings"] == [] and r["in_flight"] == 1 and r["passes"] == 4


def test_a_write_caught_halfway_in_two_passes_is_reported_unconfirmed(live, tmp_path, capsys):
    """The worst case: a note made while the notes are listed, attached only in the next pass."""
    def writer(server, t, q):
        if q.get("keys") and t == "notes":
            person = next(p for p in server.data["people"] if p["gramps_id"] == "I0001")
            pending = [n["handle"] for n in server.data["notes"] if n["handle"].startswith("hW")]
            person["note_list"] = sorted(set(person.get("note_list", []) + pending))  # attach the last one
            server.tick(person)
            n = len(server.data["notes"])
            server.data["notes"].append({**copy.deepcopy(server.data["notes"][0]), "handle": f"hW{n}", "gramps_id": f"W{n}"})
    live(selftest.clean_tree(), writer)
    report = str(tmp_path / "r.json")
    assert main(["check", "--json", report]) == 1
    r = json.load(open(report))
    assert [(f["check"], f.get("unconfirmed")) for f in r["findings"]] == [("orphan-note", True)]
    assert "unconfirmed: 1 finding(s)" in capsys.readouterr().out


def test_a_real_problem_made_late_in_the_read_is_not_dropped(live, tmp_path):
    passes = []

    def writer(server, t, q):
        if q.get("keys") and t == "people":
            passes.append(1)
            busy_people(server)
            if len(passes) == 3:  # in the third pass: a real orphan that stays
                selftest.p_orphan_note(server.data)
    live(selftest.clean_tree(), writer)
    report = str(tmp_path / "r.json")
    assert main(["check", "--json", report]) == 1
    assert [(f["check"], f["key"]) for f in json.load(open(report))["findings"]] == [("orphan-note", "N0099")]


def test_a_real_problem_made_during_the_read_is_still_reported(live, tmp_path):
    def writer(server, t, q):  # one orphan note, made once, after the first pass started
        if q.get("keys") and t == "notes" and not any(n["gramps_id"] == "N0099" for n in server.data["notes"]):
            selftest.p_orphan_note(server.data)
        if q.get("keys") and t == "people":  # and writes that never stop
            busy_people(server)
    live(selftest.clean_tree(), writer)
    report = str(tmp_path / "r.json")
    assert main(["check", "--json", report]) == 1
    assert [(f["check"], f["key"]) for f in json.load(open(report))["findings"]] == [("orphan-note", "N0099")]


def test_a_tree_whose_findings_never_settle_gives_up_with_exit_2(live, capsys):
    def churn(server, t, q):  # every pass adds one more lasting orphan: the findings never stop changing
        if q.get("keys") and t == "notes":
            n = len(server.data["notes"])
            server.data["notes"].append({**copy.deepcopy(server.data["notes"][0]), "handle": f"hX{n}", "gramps_id": f"X{n}"})
    live(selftest.clean_tree(), churn)
    assert main(["check"]) == 2
    assert "kept changing" in capsys.readouterr().err


def test_a_gwt_apply_snapshot_is_a_tree(tmp_path, capsys):
    from gwt_check.tree import APPLY_CLASSES
    data = selftest.clean_tree()
    snap = {"format": "gwt-apply-snapshot/1", "tree": "tree-1", "taken": "2026-01-01T00:00:00+00:00",
            "objects": {cls: data[t] for cls, t in APPLY_CLASSES.items()}}
    path = tmp_path / "snap.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(snap, fh)
    assert main(["check", str(path)]) == 0
    selftest.p_orphan_note(data)
    snap["objects"]["Note"] = data["notes"]
    with gzip.open(tmp_path / "planted.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(snap, fh)
    assert main(["check", str(tmp_path / "planted.json.gz"), "--since", str(path)]) == 1
    del snap["objects"]["Repository"]
    with gzip.open(tmp_path / "old.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(snap, fh)
    assert main(["check", str(tmp_path / "old.json.gz")]) == 2
    assert "take a new one" in capsys.readouterr().err
    with gzip.open(tmp_path / "bad.json.gz", "wt", encoding="utf-8") as fh:
        json.dump({"format": "gwt-apply-snapshot/1", "objects": []}, fh)
    assert main(["check", str(tmp_path / "bad.json.gz")]) == 2
    assert "objects map" in capsys.readouterr().err
