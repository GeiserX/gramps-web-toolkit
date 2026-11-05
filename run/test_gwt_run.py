"""gwt-run end to end: every step is a real gwt-apply or gwt-check process, talking over HTTP to the in-memory
fake of the Gramps Web API that the apply tests use. Every name and id here is invented."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import signal
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
from fake_api import FakeGrampsWeb, Resp  # noqa: E402

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
    refused = json.loads((again / "run.json").read_text())["steps"]["dry_run"]["refused"]
    assert any(r.startswith("[skip] p-john: ") and "already" in r for r in refused)  # says why it is a skip
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
    err = capsys.readouterr().err
    assert "/people/" in err and "no progress for 2 s;" in err


def test_the_gate_stops_on_a_new_problem(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH, DEATH_EARLY])
    assert gwt_run_main(lst, conf) == 7
    st = state(lst)
    assert st["steps"]["verify"]["status"] == "ok"
    assert st["steps"]["gate"]["status"] == "failed"
    report = json.loads(Path(f"{lst}.run/gate.json").read_text())
    assert any(f["check"] == "event-order" for f in report["new"])


def test_a_rerun_of_a_failed_gate_rewrites_gate_out(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH, DEATH_EARLY])
    assert gwt_run_main(lst, conf) == 7
    run = Path(f"{lst}.run")
    first = (run / "gate.out").read_text()
    assert "FAIL" in first
    # the finding is accepted, and the same run goes on from the gate
    known = tmp_path / "known.json"
    known.write_text((run / "gate.json").read_text())
    assert gwt_run_main(lst, conf, "--known", str(known)) == 0
    out = (run / "gate.out").read_text()
    assert "OK" in out and "FAIL:" not in out  # only this run's gate: a grep for FAIL no longer finds the old one
    assert (run / "gate.out.prev").read_text() == first  # the failed run's output is kept beside it
    # and under a name a glob over the run's .out files does not match, so it finds no old FAIL either
    assert not [p.name for p in run.glob("*.out") if "FAIL:" in p.read_text()]


def test_an_empty_out_never_replaces_the_last_real_one(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH, DEATH_EARLY])
    assert gwt_run_main(lst, conf) == 7
    assert gwt_run_main(lst, conf) == 7
    run = Path(f"{lst}.run")
    kept = (run / "gate.out").read_text()
    assert "FAIL" in kept
    (run / "gate.out").write_text("")  # a gwt-run killed right after the gate opened its .out
    known = tmp_path / "known.json"
    known.write_text((run / "gate.json").read_text())
    assert gwt_run_main(lst, conf, "--known", str(known)) == 0
    assert (run / "gate.out.prev").read_text() == kept  # the last real failure is still there


def test_a_list_changed_after_the_apply_wrote_is_refused(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH])
    server.fake.busy = 1000
    assert gwt_run_main(lst, conf, "--apply-tries", "1") == 4
    before = Path(f"{lst}.run/run.json").read_text()
    lst.write_text(json.dumps(JOHN) + "\n")
    assert gwt_run_main(lst, conf) == 2
    assert Path(f"{lst}.run/run.json").read_text() == before


def test_a_list_fixed_before_any_write_runs_again_from_a_cheap_backup(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, NOBODY])
    assert gwt_run_main(lst, conf) == 3
    lst.write_text(json.dumps(JOHN) + "\n")  # the fix: drop the op on a person the tree does not have
    assert gwt_run_main(lst, conf) == 0
    st = state(lst)
    assert st["restarts"] == 1 and st["steps"]["dry_run"]["status"] == "ok"
    assert "change stamps" in Path(f"{lst}.run/backup.out").read_text()  # read only what changed
    assert not Path(f"{lst}.run/pre.prev.json.gz").exists()


def test_a_tree_fixed_after_a_refused_dry_run_is_read_again(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, NOBODY])
    assert gwt_run_main(lst, conf) == 3
    server.fake.add("people", {"handle": "nobody", "gramps_id": "I0099", "gender": 0,
                               "primary_name": {"first_name": "Anne", "surname_list": [{"surname": "Example"}]}})
    assert gwt_run_main(lst, conf) == 0  # the new backup has I0099, so the dry run goes through
    assert server.fake.by_id("people", "I0099")["primary_name"]["first_name"] == "Ann"


def test_a_rerun_on_another_tree_is_refused(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH])
    server.fake.busy = 1000
    assert gwt_run_main(lst, conf, "--apply-tries", "1") == 4
    server.fake.busy = 0
    made = len(server.fake.writes())
    assert gwt_run_main(lst, conf, "--tree", "tree-other") == 2
    assert len(server.fake.writes()) == made


def test_a_backup_changed_after_the_apply_wrote_is_refused(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH])
    server.fake.busy = 1000
    assert gwt_run_main(lst, conf, "--apply-tries", "1") == 4
    server.fake.busy = 0
    pre = Path(f"{lst}.run/pre.json.gz")
    pre.write_bytes(gzip.compress(gzip.decompress(pre.read_bytes()).replace(b"Anna", b"Hana")))
    made = len(server.fake.writes())
    assert gwt_run_main(lst, conf) == 2
    assert len(server.fake.writes()) == made


def test_a_run_json_that_does_not_read_is_bad_input(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN])
    Path(f"{lst}.run").mkdir()
    Path(f"{lst}.run/run.json").write_text("{")
    assert gwt_run_main(lst, conf) == 2


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


def test_a_tree_changed_after_the_backup_is_refused_before_any_write(tmp_path, server):
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
    assert st["steps"]["apply"]["tries"] == 1  # not tried again: another try finds the same
    assert st["steps"]["apply"]["status"] == "refused"
    assert not server.fake.store["people"]  # gwt-apply checked the tree against the backup before its first write
    server.fake.request = real
    assert gwt_run_main(lst, conf) == 3  # nothing was written: the rerun takes a new backup, without Anna
    assert state(lst)["restarts"] == 1


def test_a_conflict_met_by_a_later_apply_try_is_not_tried_again(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH, ANNA])
    real = server.fake.request
    seen = {"puts": 0, "logins": 0}

    def busy_then_anna_goes(method, url, **kw):
        if method == "PUT" and not seen["puts"]:
            seen["puts"] += 1
            server.fake.busy = 1  # the first try stops on "database is locked"
        if url.endswith("/api/token/"):
            seen["logins"] += 1
            if seen["logins"] == 3:  # the second apply try
                del server.fake.store["people"]["anna"]
        return real(method, url, **kw)

    server.fake.request = busy_then_anna_goes
    assert gwt_run_main(lst, conf, "--apply-tries", "5") == 4
    st = state(lst)
    assert st["steps"]["apply"]["tries"] == 2
    assert st["steps"]["apply"]["counts"]["conflict"] == 1


def test_a_refused_server_backup_writes_nothing_and_is_not_tried_without_it(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH])
    server.fake.backup_status = 429
    assert gwt_run_main(lst, conf, "--server-backup", "--apply-tries", "3") == 4
    assert sum(1 for m, p in server.fake.calls if m == "POST" and p == "/api/backups/") == 1  # one ask
    assert [o["primary_name"]["first_name"] for o in server.fake.store["people"].values()] == ["Anna"]  # no write
    st = state(lst)
    assert st["steps"]["apply"]["status"] == "refused" and st["steps"]["apply"]["tries"] == 1


def test_an_apply_that_refuses_its_input_is_not_tried_again(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN])
    conf.write_text(conf.read_text().replace("GWT_TREE=tree-test\n", ""))  # gwt-apply --apply needs a tree
    assert gwt_run_main(lst, conf, "--apply-tries", "3", "--retry-wait", "5") == 2
    run_json = Path(f"{lst}.run/run.json")
    assert not run_json.exists() or state(lst)["steps"]["apply"].get("tries", 0) <= 1
    assert server.fake.writes() == []


def test_apply_tries_count_per_run_of_gwt_run(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH])
    server.fake.busy = 1000
    assert gwt_run_main(lst, conf, "--apply-tries", "2") == 4
    assert state(lst)["steps"]["apply"]["tries"] == 2
    assert gwt_run_main(lst, conf, "--apply-tries", "2") == 4
    assert state(lst)["steps"]["apply"]["tries"] == 4  # the rerun had its own two tries, not one


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
    assert state(lst)["steps"]["apply"]["tries"] == 2  # the server confirmed the first; the second try asks no more
    assert sum(1 for m, p in server.fake.calls if m == "POST" and p == "/api/backups/") == 1


def logins_fail_from(server, n, until=None):
    """Every login from the n-th on (until the until-th, not included) answers HTTP 503."""
    real, seen = server.fake.request, {"n": 0}

    def maybe_down(method, url, **kw):
        if url.endswith("/api/token/"):
            seen["n"] += 1
            if seen["n"] >= n and (until is None or seen["n"] < until):
                return Resp(503, "Service Unavailable")
        return real(method, url, **kw)

    server.fake.request = maybe_down


def test_a_verify_that_cannot_log_in_is_tried_again_not_called_missing(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH])
    logins_fail_from(server, 3, until=4)  # backup, apply, then the first verify's login fails once
    assert gwt_run_main(lst, conf, "--verify-tries", "2") == 0


def test_a_verify_that_never_reads_the_tree_is_a_failure_not_missing_ops(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH])
    logins_fail_from(server, 3)
    assert gwt_run_main(lst, conf, "--verify-tries", "2") == 1
    st = state(lst)
    assert st["steps"]["apply"]["status"] == "ok" and st["steps"]["verify"]["status"] == "failed"
    assert "missing" not in st["steps"]["verify"]


def test_a_gate_that_cannot_read_the_tree_is_a_failure_not_new_problems_or_bad_input(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH])
    logins_fail_from(server, 4)  # backup, apply, verify; the gate's login fails
    assert gwt_run_main(lst, conf) == 1
    st = state(lst)
    assert st["steps"]["verify"]["status"] == "ok" and st["steps"]["gate"]["status"] == "failed"


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_on_gwt_run_ends_the_step_it_runs(tmp_path, server, sig):
    lst, conf = setup(tmp_path, server, [JOHN])
    server.delay = lambda method, path: 30 if path == "/api/people/" else 0  # the backup's read hangs
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(str(HERE.parent / d) for d in ("apply", "check", "core"))}
    proc = subprocess.Popen([sys.executable, str(HERE / "gwt-run"), str(lst), "--config", str(conf)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env, text=True)
    out = Path(f"{lst}.run/backup.out")
    for _ in range(100):  # until the step is reading
        if out.exists() and "snapshot: GET /people/" in out.read_text():
            break
        time.sleep(0.1)
    proc.send_signal(sig)
    assert proc.wait(timeout=20) == 128 + sig
    st = state(lst)
    assert st["steps"]["backup"]["status"] == "stopped" and st["status"] == "stopped"
    # ww: the whole command line; Linux ps cuts it at 80 columns in a pipe, and then the path never matches
    ps = subprocess.run(["ps", "-axww", "-o", "command"], capture_output=True, text=True).stdout
    assert str(lst) not in ps  # no gwt-apply left reading (or writing) on its own


def test_a_dry_run_stopped_on_a_likely_duplicate_names_it(tmp_path, server):
    twin = {"op": "add_person", "key": "p-anna-twin", "ruling": R, "first_name": "Anna", "surname": "Example",
            "gender": "F"}
    lst, conf = setup(tmp_path, server, [JOHN, twin])
    assert gwt_run_main(lst, conf) == 3
    refused = state(lst)["steps"]["dry_run"]["refused"]
    assert any(r.startswith("[refused] ") and "p-anna-twin" in r and "I0001" in r for r in refused)


def test_an_apply_try_refused_after_an_earlier_try_wrote_keeps_the_backup(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH])
    real = server.fake.request
    seen = {"puts": 0}

    def busy_then_bad_config(method, url, **kw):
        if method == "PUT" and not seen["puts"]:
            seen["puts"] += 1
            server.fake.busy = 1  # try 1 made John, then meets "database is locked"
            conf.write_text(conf.read_text().replace("GWT_TREE=tree-test\n", ""))  # try 2 exits 2 before writing
        return real(method, url, **kw)

    server.fake.request = busy_then_bad_config
    assert gwt_run_main(lst, conf, "--apply-tries", "3") == 2
    assert state(lst)["steps"]["apply"]["status"] == "failed"  # an earlier try wrote: never "refused"
    md5 = state(lst)["steps"]["backup"]["md5"]
    server.fake.request = real
    conf.write_text(conf.read_text() + "GWT_TREE=tree-test\n")
    assert gwt_run_main(lst, conf) == 0
    st = state(lst)
    assert st["steps"]["backup"]["md5"] == md5 and not st.get("restarts")  # the backup from before John stayed
    assert len([o for o in server.fake.store["people"].values() if o["primary_name"]["first_name"] == "John"]) == 1


def test_a_verify_that_reads_an_op_missing_stops_with_exit_6(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, BIRTH])
    real, logins = server.fake.request, {"n": 0}

    def birth_goes_before_the_verify(method, url, **kw):
        if url.endswith("/api/token/"):
            logins["n"] += 1
            if logins["n"] == 3:  # backup, apply, then the verify
                server.fake.store["events"].clear()
        return real(method, url, **kw)

    server.fake.request = birth_goes_before_the_verify
    assert gwt_run_main(lst, conf) == 6
    st = state(lst)
    assert st["steps"]["verify"]["status"] == "failed"
    assert any("e-john-birth" in m for m in st["steps"]["verify"]["missing"])
    assert st["steps"]["gate"]["status"] == "todo"


def test_no_tree_is_bad_input_before_any_step(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN])
    conf.write_text(conf.read_text().replace("GWT_TREE=tree-test\n", ""))
    assert gwt_run_main(lst, conf) == 2
    assert not Path(f"{lst}.run/backup.out").exists() and server.fake.calls == []


def test_a_backup_gwt_apply_refuses_to_take_is_bad_input(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN])
    conf.write_text(conf.read_text().replace("GWT_PASSWORD=pw\n", ""))  # no login: gwt-apply exits 2
    assert gwt_run_main(lst, conf) == 2
    assert state(lst)["steps"]["backup"]["status"] == "failed"


def test_a_backup_that_does_not_read_back_stops_the_run(tmp_path, server, monkeypatch):
    lst, conf = setup(tmp_path, server, [JOHN])
    real = gwt_run.Run.watch

    def spoil_the_backup(self, name, cmd, **kw):
        result = real(self, name, cmd, **kw)
        if name == "backup":
            self.pre.write_bytes(b"\x1f\x8b not a gzip")
        return result

    monkeypatch.setattr(gwt_run.Run, "watch", spoil_the_backup)
    assert gwt_run_main(lst, conf) == 1
    assert state(lst)["steps"]["backup"]["status"] == "failed" and server.fake.writes() == []


def test_a_list_gwt_apply_refuses_to_read_stops_at_the_dry_run(tmp_path, server):
    lst, conf = setup(tmp_path, server, [{"op": "add_person", "key": "p-x", "ruling": R, "gender": "M"}])
    assert gwt_run_main(lst, conf) == 2  # no names: gwt-apply writes no JSON and exits 2
    assert state(lst)["steps"]["dry_run"]["status"] == "failed" and server.fake.writes() == []


def test_a_damaged_backup_is_no_base_for_the_next_one(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN, NOBODY])
    assert gwt_run_main(lst, conf) == 3
    Path(f"{lst}.run/pre.json.gz").write_bytes(b"\x1f\x8b damaged")
    lst.write_text(json.dumps(JOHN) + "\n")
    assert gwt_run_main(lst, conf) == 0
    assert "page 1" in Path(f"{lst}.run/backup.out").read_text()  # a whole read, not --since the damaged one


def test_an_apply_that_hangs_on_its_last_try_is_a_stall(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN])
    server.delay = lambda method, path: 20 if method != "GET" and path != "/api/token/" else 0  # every write hangs
    assert gwt_run_main(lst, conf, "--apply-tries", "1", "--stall", "2") == 5
    assert state(lst)["steps"]["apply"]["status"] == "stalled"


def test_a_server_backup_refused_once_is_asked_for_again_on_the_rerun(tmp_path, server):
    lst, conf = setup(tmp_path, server, [JOHN])
    server.fake.backup_status = 429
    assert gwt_run_main(lst, conf, "--server-backup") == 4
    server.fake.backup_status = 201
    assert gwt_run_main(lst, conf, "--server-backup") == 0
    assert sum(1 for m, p in server.fake.calls if m == "POST" and p == "/api/backups/") == 2


def test_a_finished_run_says_done_before(tmp_path, server, capsys):
    lst, conf = setup(tmp_path, server, [JOHN])
    assert gwt_run_main(lst, conf) == 0
    capsys.readouterr()
    assert gwt_run_main(lst, conf) == 0
    assert "done before: every step went through" in capsys.readouterr().out


def test_a_step_that_ignores_sigterm_is_killed():
    t0 = time.monotonic()
    code, why, _ = gwt_run.watch(
        [sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                               "print('up', flush=True); time.sleep(60)"], out=Path("/dev/null"), stall=1)
    assert why == "stall" and time.monotonic() - t0 < 30
