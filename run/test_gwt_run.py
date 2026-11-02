"""gwt-run end to end: every step is a real gwt-apply or gwt-check process, talking over HTTP to the in-memory
fake of the Gramps Web API that the apply tests use. Every name and id here is invented."""

from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "apply" / "tests")]

import gwt_run  # noqa: E402
from fake_api import FakeGrampsWeb  # noqa: E402

R = "ruling-0001"
JOHN = {"op": "add_person", "key": "p-john", "ruling": R, "first_name": "John", "surname": "Example", "gender": "M"}
BIRTH = {"op": "add_event", "key": "e-john-birth", "ruling": R, "person": "key:p-john", "type": "Birth",
         "date": "1890-03-14", "place": "P0001"}
ANNA = {"op": "update_person", "key": "u-anna", "ruling": R, "person": "I0001", "set": {"first_name": "Anna Maria"}}
NOBODY = {"op": "update_person", "key": "u-nobody", "ruling": R, "person": "I0099", "set": {"first_name": "Ann"}}
DEATH_EARLY = {"op": "add_event", "key": "e-john-death", "ruling": R, "person": "key:p-john", "type": "Death",
               "date": "1880-01-01"}


class Served:
    """The fake API behind a real HTTP port. `delay(method, path)` gives seconds to wait before answering."""

    def __init__(self, fake: FakeGrampsWeb):
        self.fake, self.lock = fake, threading.Lock()
        self.delay = lambda method, path: 0
        served = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _any(self):
                url = urlsplit(self.path)
                pause = served.delay(self.command, url.path)
                if pause:
                    time.sleep(pause)
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = json.loads(raw) if raw and "json" in (self.headers.get("Content-Type") or "") else None
                with served.lock:
                    r = served.fake.request(self.command, f"http://fake{url.path}", headers=dict(self.headers),
                                            params=dict(parse_qsl(url.query)), json=body, data=raw)
                out = (r._body if isinstance(r._body, str) else json.dumps(r._body)).encode()
                self.send_response(r.status_code)
                for k, v in r.headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            do_GET = do_POST = do_PUT = do_DELETE = _any

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"


@pytest.fixture
def server():
    f = FakeGrampsWeb()
    f.add("places", {"handle": "plc1", "name": {"value": "Springfield"}, "lat": "39.80", "long": "-89.65"})  # P0001
    f.add("people", {"handle": "anna", "gender": 0,
                     "primary_name": {"first_name": "Anna", "surname_list": [{"surname": "Example", "primary": True}]}})
    s = Served(f)
    yield s
    s.httpd.shutdown()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for k in [k for k in __import__("os").environ if k.startswith("GWT_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def setup(tmp_path, server, ops):
    conf = tmp_path / "gw.env"
    conf.write_text(f"GWT_URL={server.url}\nGWT_USERNAME=editor\nGWT_PASSWORD=pw\nGWT_TREE=tree-test\n")
    lst = tmp_path / "changes.jsonl"
    lst.write_text("".join(json.dumps(op) + "\n" for op in ops))
    return lst, conf


def gwt_run_main(lst, conf, *extra):
    return gwt_run.main([str(lst), "--config", str(conf), "--retries", "1", "--retry-wait", "0", *extra])


def state(lst):
    return json.loads(Path(f"{lst}.run/run.json").read_text())


def test_full_run_goes_through_every_step(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH, ANNA])
    assert gwt_run_main(lst, conf) == 0
    st = state(lst)
    assert st["status"] == "done"
    assert [st["steps"][s]["status"] for s in gwt_run.STEPS] == ["ok"] * 5
    pre = Path(f"{lst}.run/pre.json.gz")
    assert st["steps"]["backup"]["md5"] == hashlib.md5(pre.read_bytes()).hexdigest()
    with gzip.open(pre, "rt") as fh:  # the backup is the tree before the run
        assert len(json.load(fh)["objects"]["Person"]) == 1
    assert server.fake.by_id("people", "I0001")["primary_name"]["first_name"] == "Anna Maria"
    verify = json.loads(Path(f"{lst}.run/verify.json").read_text())
    assert [r["status"] for r in verify] == ["skip"] * 3
    # a run that is done is done: the same command again writes nothing
    calls = len(server.fake.writes())
    assert gwt_run_main(lst, conf) == 0
    assert len(server.fake.writes()) == calls


def test_a_conflict_in_the_dry_run_stops_before_any_write(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, NOBODY])
    assert gwt_run_main(lst, conf) == 3
    st = state(lst)
    assert st["steps"]["dry_run"]["status"] == "refused"
    assert any("u-nobody" in r for r in st["steps"]["dry_run"]["refused"])
    assert st["steps"]["apply"]["status"] == "todo"
    assert server.fake.writes() == []


def test_a_skip_that_is_not_a_noop_stops_the_run_unless_accepted(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, ANNA])
    first = tmp_path / "first"
    assert gwt_run_main(lst, conf, "--dir", str(first)) == 0
    # the same list again: p-john reads as "already made by key", which a first dry run never should
    again = tmp_path / "again"
    assert gwt_run_main(lst, conf, "--dir", str(again)) == 3
    assert any("p-john" in r for r in json.loads((again / "run.json").read_text())["steps"]["dry_run"]["refused"])
    # u-anna is a no-op skip (the tree already has that name) and never stops it
    ok = tmp_path / "accepted"
    assert gwt_run_main(lst, conf, "--dir", str(ok), "--accept-skip", "p-john") == 0


def test_database_locked_mid_apply_is_tried_again_without_writing_twice(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH, ANNA])
    real = server.fake.request
    seen = {"puts": 0}

    def busy_on_second_write(method, url, **kw):
        if method == "PUT" and not seen["puts"]:
            seen["puts"] += 1
            server.fake.busy = 1  # the first PUT of the run (the birth's link) meets "database is locked"
        return real(method, url, **kw)

    server.fake.request = busy_on_second_write
    assert gwt_run_main(lst, conf, "--apply-tries", "3") == 0
    st = state(lst)
    assert st["steps"]["apply"]["tries"] == 2
    assert Path(f"{lst}.run/apply-1.json").exists()
    persons = [o for o in server.fake.store["people"].values() if o["primary_name"]["first_name"] == "John"]
    assert len(persons) == 1  # the second try found John made and did not make him again
    assert len(server.fake.store["events"]) == 1


def test_apply_gives_up_after_its_tries_and_a_rerun_goes_on(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH])
    server.fake.busy = 100
    assert gwt_run_main(lst, conf, "--apply-tries", "2") == 4
    assert state(lst)["steps"]["apply"]["status"] == "failed"
    server.fake.busy = 0
    assert gwt_run_main(lst, conf, "--apply-tries", "2") == 0
    st = state(lst)
    assert st["steps"]["apply"]["tries"] == 3
    assert len(server.fake.store["events"]) == 1


def test_a_verify_past_its_deadline_stops_and_a_rerun_resumes_at_verify(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH])
    slow = {"on": False}
    real = server.fake.request

    def watch_writes(method, url, **kw):
        r = real(method, url, **kw)
        if method == "PUT":
            slow["on"] = True  # the apply is done with its last write: from here on reads are slow
        return r

    server.fake.request = watch_writes
    server.delay = lambda method, path: 3 if slow["on"] and method == "GET" and path != "/api/token/" else 0
    assert gwt_run_main(lst, conf, "--verify-timeout", "1", "--verify-tries", "2") == 5
    st = state(lst)
    assert st["steps"]["apply"]["status"] == "ok"
    assert st["steps"]["verify"]["status"] == "stalled"
    pre_md5 = st["steps"]["backup"]["md5"]
    made = len(server.fake.writes())
    server.delay = lambda method, path: 0
    assert gwt_run_main(lst, conf) == 0
    st = state(lst)
    assert st["steps"]["backup"]["md5"] == pre_md5  # the backup is never taken again over a written tree
    assert hashlib.md5(Path(f"{lst}.run/pre.json.gz").read_bytes()).hexdigest() == pre_md5
    assert len(server.fake.writes()) == made


def test_a_read_that_hangs_is_ended_and_named(tmp_path, server, capsys):
    lst, conf = setup(tmp_path, server, [JOHN])
    server.delay = lambda method, path: 30 if path == "/api/people/" else 0
    t0 = time.monotonic()
    assert gwt_run_main(lst, conf, "--stall", "2") == 5
    assert time.monotonic() - t0 < 25
    st = state(lst)
    assert st["steps"]["backup"]["status"] == "stalled"
    assert "/people/" in st["steps"]["backup"]["last"]
    assert "/people/" in capsys.readouterr().err


def test_the_gate_stops_on_a_new_problem(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH, DEATH_EARLY])
    assert gwt_run_main(lst, conf) == 7
    st = state(lst)
    assert st["steps"]["verify"]["status"] == "ok"
    assert st["steps"]["gate"]["status"] == "failed"
    report = json.loads(Path(f"{lst}.run/gate.json").read_text())
    assert any(f["check"] == "event-order" for f in report["new"])


def test_a_list_changed_after_the_run_began_is_refused(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, NOBODY])
    assert gwt_run_main(lst, conf) == 3
    before = Path(f"{lst}.run/run.json").read_text()
    lst.write_text(json.dumps(JOHN) + "\n")
    assert gwt_run_main(lst, conf) == 2
    assert Path(f"{lst}.run/run.json").read_text() == before


def test_the_watchdog_ends_a_silent_process():
    code, why, last = gwt_run.watch(
        [sys.executable, "-c", "import time; print('reading page 3', flush=True); time.sleep(20)"],
        out=Path("/dev/null"), stall=1)
    assert (code, why) == (None, "stall")
    assert "reading page 3" in last


def test_the_watchdog_counts_log_growth_as_progress(tmp_path):
    log = tmp_path / "x.jsonl"
    script = (f"import time\nfor i in range(6):\n    open({str(log)!r}, 'a').write('{{\"method\": \"GET\", "
              "\"path\": \"/people/\"}\\n'); time.sleep(0.5)")
    code, why, last = gwt_run.watch([sys.executable, "-c", script], out=tmp_path / "o", stall=1.5,
                                    logs=lambda: [log])
    assert (code, why) == (0, None)


def test_two_runs_on_one_folder_do_not_both_go(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN])
    d = tmp_path / "d"
    d.mkdir()
    held = gwt_run._lock(d / "run.lock")
    try:
        assert gwt_run_main(lst, conf, "--dir", str(d)) == 2
    finally:
        held.close()


def test_the_wrapper_runs(tmp_path):
    r = subprocess.run([sys.executable, str(HERE / "gwt-run"), "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "Exit codes" in r.stdout


def test_a_conflict_met_by_the_apply_is_not_tried_again(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, ANNA])
    real = server.fake.request
    logins = {"n": 0}

    def anna_goes_before_the_apply(method, url, **kw):
        if url.endswith("/api/token/"):
            logins["n"] += 1
            if logins["n"] == 2:  # backup logs in once, the offline dry run never: this is the apply
                del server.fake.store["people"]["anna"]
        return real(method, url, **kw)

    server.fake.request = anna_goes_before_the_apply
    assert gwt_run_main(lst, conf, "--apply-tries", "3") == 4
    st = state(lst)
    assert st["steps"]["apply"]["tries"] == 1
    assert st["steps"]["apply"]["counts"]["conflict"] == 1


def test_a_half_written_backup_is_taken_again(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN])
    d = Path(f"{lst}.run")
    d.mkdir()
    (d / "pre.json.gz").write_bytes(b"\x1f\x8b half")  # a backup step killed while writing
    assert gwt_run_main(lst, conf) == 0


def test_the_server_backup_is_asked_for_once_across_apply_tries(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH])
    real = server.fake.request
    seen = {"puts": 0}

    def busy_on_first_put(method, url, **kw):
        if method == "PUT" and not seen["puts"]:
            seen["puts"] += 1
            server.fake.busy = 1
        return real(method, url, **kw)

    server.fake.request = busy_on_first_put
    assert gwt_run_main(lst, conf, "--server-backup") == 0
    assert state(lst)["steps"]["apply"]["tries"] == 2
    assert sum(1 for m, p in server.fake.calls if m == "POST" and p == "/api/backups/") == 1
