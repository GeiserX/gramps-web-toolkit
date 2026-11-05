"""gwt-run: one change list through the whole write chain, in one command that can be run again after a crash.

    gwt-run changes.jsonl --config gw.env --known known.json

Five steps, each one a gwt-apply or gwt-check run of its own:

    backup   gwt-apply --save-snapshot DIR/pre.json.gz, then the file is read back whole and its md5 kept
    dry_run  gwt-apply LIST --snapshot DIR/pre.json.gz: stops on a conflict, an error, or a skip that is not a no-op
    apply    gwt-apply LIST --apply --log DIR/apply.jsonl, tried again while ops fail on a busy server
    verify   gwt-apply LIST --verify: every op must read as in the tree
    gate     gwt-check check --from DIR/pre.json.gz --baseline DIR/pre.json.gz [--known K]: no new finding

DIR (default LIST.run) keeps run.json with every step's status. Until the apply writes, running the same command
again starts over (an edited list is accepted), from a backup that reads only what changed. After that it skips the
steps that are done and goes on from the first that is not; a changed list, another tree or a changed backup is
refused.

A watchdog ends a step that shows no progress (no output line and no API call in its log) for --stall seconds,
and the run exits 5 naming the last thing the step did.

Exit codes: 0 done; 1 an unexpected failure (or a verify or gate that could not read the tree); 2 bad input (a
missing file, a list changed after the apply wrote, another tree, another gwt-run on the same DIR); 3 the dry run
refused the list (nothing written); 4 the apply did not finish; 5 a step stalled or ran past its deadline; 6 the
verify found ops not in the tree; 7 the gate found new problems; 128 + N gwt-run got signal N (Ctrl-C is 130).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

FORMAT = "gwt-run/1"
STEPS = ("backup", "dry_run", "apply", "verify", "gate")
# gwt-apply's --json reason for every op when it stopped before its first write, and what that means
BEFORE_WRITE = {
    "not run: the snapshot check refused the list": "the tree changed since the backup (or it is another tree)",
    "not run: the server backup was refused": "the server did not store the backup --server-backup asks for",
    "not run: the list stopped on likely duplicates": "the list stopped on likely duplicates",
}
EXIT = {"done": 0, "failed": 1, "input": 2, "refused": 3, "apply": 4, "stalled": 5, "verify": 6, "gate": 7}


class Signalled(BaseException):
    """gwt-run got SIGTERM or SIGHUP: the step is ended and the run stops, like after Ctrl-C."""

    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum


def _signalled(signum, frame):
    raise Signalled(signum)


class Stop(Exception):
    """The run stops here with this exit code; run.json keeps why."""

    def __init__(self, code: int, why: str):
        super().__init__(why)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _child_env(tree: str | None) -> dict:
    """The environment of every step: the toolkit's own folders on the path when this runs from a clone, so the
    steps use the gwt-apply and gwt-check beside it; an installed package finds them anyway."""
    env = dict(os.environ)
    root = Path(__file__).resolve().parent.parent
    dirs = [str(root / d) for d in ("apply", "check", "core") if (root / d).is_dir()]
    if dirs:
        env["PYTHONPATH"] = os.pathsep.join(dirs + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    if tree:
        env["GWT_TREE"] = tree
    return env


def _target(args) -> dict:
    """The server and tree every step works on, as gwt-apply works them out (the environment over the --config
    file, --tree over both). No secrets: a run.json names where it writes, never how it logs in."""
    try:
        from gwt_apply.cli import read_config
    except ImportError:  # run from a clone: gwt-apply sits beside run/
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apply"))
        from gwt_apply.cli import read_config
    try:
        conf = read_config(args.config)
    except OSError as exc:
        raise Stop(EXIT["input"], f"--config: {exc}")
    return {"url": conf.get("GWT_URL"), "tree": args.tree or conf.get("GWT_TREE")}


def _last_call(logs: list[Path]) -> str:
    """The last API call in the newest of `logs`, as `METHOD path`."""
    for log in sorted(logs, key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            lines = log.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("path"):
                return f"{e.get('method')} {e['path']}"
    return ""


def watch(cmd: list[str], *, out: Path, stall: float, deadline: float | None = None, logs=None,
          env: dict | None = None) -> tuple[int | None, str | None, str]:
    """Run `cmd`, copying its output to the terminal and to `out`. It is ended when nothing moved for `stall`
    seconds (no output line, and no growth of the files `logs()` names) or after `deadline` seconds.
    Returns (exit code or None when ended, None or "stall" or "deadline", the last thing it did)."""
    logs = logs or (lambda: [])
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True,
                            errors="replace", start_new_session=True)
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: [lines.put(x) for x in proc.stdout] + [lines.put(None)], daemon=True).start()
    start = last = time.monotonic()
    last_line, sizes, why = "", {}, None
    try:
        with open(out, "a", encoding="utf-8") as fh:
            while True:
                try:
                    line = lines.get(timeout=0.2)
                except queue.Empty:
                    line = ""
                if line is None:
                    break
                now = time.monotonic()
                if line:
                    fh.write(line)
                    fh.flush()
                    print("    " + line, end="", flush=True)
                    last_line, last = line.strip(), now
                grown = {str(p): p.stat().st_size for p in logs() if p.exists()}
                if grown != sizes:
                    sizes, last = grown, now
                if now - last >= stall:
                    why = "stall"
                elif deadline is not None and now - start >= deadline:
                    why = "deadline"
                if why:
                    _end(proc)
                    break
    except BaseException:  # Ctrl-C or a signal on gwt-run: the step must not go on writing alone
        _end(proc)
        raise
    code = proc.wait()
    call = _last_call([Path(p) for p in sizes])
    return (None if why else code), why, (f"{call} (from its log); " if call else "") + f"last line: {last_line!r}"


def _end(proc: subprocess.Popen) -> None:
    """SIGTERM first, so gwt-apply releases the tree lock on the way out; SIGKILL if it does not go."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class Run:
    def __init__(self, args):
        self.args = args
        self.list = Path(args.change_list).resolve()
        self.dir = Path(args.dir or f"{args.change_list}.run").resolve()
        self.state_path = self.dir / "run.json"
        self.pre = self.dir / "pre.json.gz"
        self.prev = self.dir / "pre.prev.json.gz"  # the backup of a run that wrote nothing, for a cheap new one
        self.env = _child_env(args.tree)
        self.target = _target(args)
        self.state: dict = {}
        self.current: str | None = None  # the step running now

    # -- run.json --------------------------------------------------------

    def save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_path)  # never half a file after a crash

    def load(self) -> None:
        sha = _sha256(self.list)
        if not self.state_path.exists():
            if not self.target["tree"]:
                raise Stop(EXIT["input"], "no tree: give --tree, or GWT_TREE in --config or the environment")
            self.state = {"format": FORMAT, "list": str(self.list), "list_sha256": sha, "target": self.target,
                          "started": _now(), "steps": {s: {"status": "todo"} for s in STEPS}}
            self.save()
            return
        try:
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            steps = self.state["steps"]
            written = steps["apply"]["status"] not in ("todo", "refused")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise Stop(EXIT["input"], f"{self.state_path} does not read ({exc}); give the run a new --dir")
        if self.state.get("target") != self.target:
            raise Stop(EXIT["input"], f"{self.dir} is a run on {self.state.get('target')}; this command points at "
                                      f"{self.target}: give it a new --dir")
        if self.state.get("list_sha256") != sha and written:
            raise Stop(EXIT["input"], f"{self.list} changed since this run wrote to the tree in {self.dir}; "
                                      "a changed list is a new run: give it a new --dir")
        if not written and (self.state.get("list_sha256") != sha or self.state.get("status") == "stopped"):
            self.start_over(sha)
        elif steps["backup"]["status"] == "ok" and self._md5() != steps["backup"].get("md5"):
            raise Stop(EXIT["input"], f"backup: {self.pre} changed or went away since it was taken; the gate and "
                                      "--revert need the tree as it was before the run")
        self.save()

    def _md5(self) -> str | None:
        return hashlib.md5(self.pre.read_bytes()).hexdigest() if self.pre.exists() else None

    def start_over(self, sha: str) -> None:
        """Nothing was written yet: the list or the tree may have been fixed since the stop, so every step runs
        again. The old backup, when it is intact, is the base of a new one that reads only what changed since."""
        if self.step("backup")["status"] == "ok" and self._md5() == self.step("backup").get("md5"):
            os.replace(self.pre, self.prev)
        else:
            self.pre.unlink(missing_ok=True)
        for k in ("status", "exit", "why", "ended"):
            self.state.pop(k, None)
        self.state.update(list_sha256=sha, restarts=self.state.get("restarts", 0) + 1,
                          steps={s: {"status": "todo"} for s in STEPS})
        print("nothing was written yet: every step runs again"
              + (", the backup from the last one" if self.prev.exists() else ""), flush=True)

    def step(self, name: str) -> dict:
        return self.state["steps"][name]

    def mark(self, name: str, status: str, **info) -> None:
        self.step(name).update(status=status, at=_now(), **info)
        self.save()

    # -- steps -----------------------------------------------------------

    def apply_cmd(self, *extra: str) -> list[str]:
        cmd = [sys.executable, "-m", "gwt_apply", str(self.list), *extra]
        if self.args.config:
            cmd += ["--config", self.args.config]
        if self.args.tag:
            cmd += ["--tag", self.args.tag]
        if self.args.private_new:
            cmd += ["--private-new"]
        return cmd

    def watch(self, name: str, cmd: list[str], *, deadline=None, log_glob: str | None = None):
        logs = (lambda: list(self.dir.glob(log_glob))) if log_glob else None
        return watch(cmd, out=self.dir / f"{name}.out", stall=self.args.stall, deadline=deadline, logs=logs, env=self.env)

    def stalled(self, name: str, why: str, last: str) -> Stop:
        self.mark(name, "stalled", why=why, last=last)
        return Stop(EXIT["stalled"], f"{name}: {f'no progress for {self.args.stall:g} s' if why == 'stall' else 'past its deadline'}; {last}")

    def backup(self) -> None:
        if self.pre.exists():
            self.pre.unlink()  # half a snapshot from a step that did not finish; a done step never comes here
        cmd = [sys.executable, "-m", "gwt_apply", "--save-snapshot", str(self.pre)]
        if self.prev.exists():  # reads only what changed since the last backup of this run
            cmd += ["--since", str(self.prev)]
        if self.args.config:
            cmd += ["--config", self.args.config]
        code, why, last = self.watch("backup", cmd)
        if why:
            raise self.stalled("backup", why, last)
        if code != 0:
            self.mark("backup", "failed", exit=code)
            raise Stop(EXIT["input"] if code == 2 else EXIT["failed"],
                       f"backup: gwt-apply --save-snapshot exited {code}; see {self.dir / 'backup.out'}")
        try:  # read it back whole: a snapshot that does not decompress or parse is no backup
            with gzip.open(self.pre, "rt", encoding="utf-8") as fh:
                snap = json.load(fh)
        except (OSError, EOFError, ValueError) as exc:
            self.mark("backup", "failed", error=str(exc))
            raise Stop(EXIT["failed"], f"backup: {self.pre} does not read back ({exc})")
        self.prev.unlink(missing_ok=True)
        self.mark("backup", "ok", file=str(self.pre), md5=self._md5(), tree=snap.get("tree"),
                  objects={c: len(v) for c, v in (snap.get("objects") or {}).items()})

    def _dry_run_log(self) -> tuple[list[str], dict[str, str]]:
        """From the newest dry-run log: why gwt-apply refused the list as a whole (a likely duplicate, a missing
        file), and the last action of every op, which says why a skip is one."""
        logs = sorted(self.dir.glob("dry_run.*.jsonl"), key=lambda p: p.stat().st_mtime)
        problems, actions = [], {}
        for line in (logs[-1].read_text(encoding="utf-8").splitlines() if logs else []):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("event") == "refused":
                problems += [str(p) for p in e.get("problems") or []]
            elif e.get("event") == "op" and e.get("key") and e.get("actions"):
                actions[e["key"]] = str(e["actions"][-1])
        return problems, actions

    def dry_run(self) -> None:
        out = self.dir / "dry_run.json"
        out.unlink(missing_ok=True)
        code, why, last = self.watch("dry_run", self.apply_cmd("--snapshot", str(self.pre), "--json", str(out),
                                                               "--log", str(self.dir / "dry_run.jsonl")))
        if why:
            raise self.stalled("dry_run", why, last)
        if not out.exists():
            self.mark("dry_run", "failed", exit=code)
            raise Stop(EXIT["input"], f"dry run: gwt-apply refused the list (exit {code}); see {self.dir / 'dry_run.out'}")
        rows = json.loads(out.read_text(encoding="utf-8"))
        accept = set(self.args.accept_skip)
        refused = [r for r in rows if r["status"] in ("conflict", "error", "not run")]
        refused += [r for r in rows if r["status"] == "skip" and not r.get("noop") and r["key"] not in accept]
        if refused:
            problems, actions = self._dry_run_log()
            lines = [f"[refused] {p}" for p in problems]
            lines += [f"[{r['status']}] {r['key']}: {r['reason'] or actions.get(r['key'], '')}" for r in refused]
            self.mark("dry_run", "refused", exit=code, refused=lines)
            raise Stop(EXIT["refused"], f"dry run: {len(refused)} op(s) would not go through; nothing was written:\n"
                       + "\n".join(f"  {x}" for x in lines))
        counts = {s: sum(r["status"] == s for r in rows) for s in ("done", "skip")}
        self.mark("dry_run", "ok", exit=code, counts=counts)

    def _server_backup_stored(self) -> bool:
        """True when an apply log in DIR holds the server's confirmation of a backup."""
        for log in self.dir.glob("apply.*.jsonl"):
            try:
                if any('"server-backup"' in line and json.loads(line).get("event") == "server-backup"
                       for line in log.read_text(encoding="utf-8").splitlines()):
                    return True
            except (OSError, ValueError):
                continue
        return False

    def apply(self) -> None:
        step = self.step("apply")
        for this in range(1, self.args.apply_tries + 1):  # --apply-tries counts the tries of this invocation
            tries = step.get("tries", 0) + 1
            out = self.dir / f"apply-{tries}.json"
            extra = ["--apply", "--log", str(self.dir / "apply.jsonl"), "--json", str(out),
                     "--retries", str(self.args.retries), "--retry-wait", str(self.args.retry_wait)]
            if tries == 1:  # nothing written yet: gwt-apply refuses a tree that changed since the backup, or another tree
                extra += ["--snapshot", str(self.pre)]
            if self.args.server_backup and not self._server_backup_stored():
                extra.append("--server-backup")  # until the server confirms one; gwt-apply writes nothing without it
            self.mark("apply", "running", tries=tries)
            code, why, last = self.watch("apply", self.apply_cmd(*extra), log_glob="apply.*.jsonl")
            rows = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
            counts = {s: sum(r["status"] == s for r in rows) for s in ("done", "skip", "conflict", "error", "not run")}
            if code == 0:
                self.mark("apply", "ok", counts=counts)
                return
            before = {r["reason"] for r in rows} & set(BEFORE_WRITE)
            if code == 2 or (rows and before and counts["not run"] == len(rows)):
                # gwt-apply stopped before its first write; another try finds the same. A rerun starts over, unless
                # an earlier try wrote: then the backup must stay the tree before that write
                why_not = BEFORE_WRITE.get(next(iter(before), ""), "gwt-apply refused its input")
                self.mark("apply", "refused" if tries == 1 else "failed", exit=code)
                raise Stop(EXIT["input"] if code == 2 else EXIT["apply"],
                           f"apply: {why_not}; nothing was written; see {self.dir / 'apply.out'}")
            if counts["conflict"]:  # the tree changed under the list: another try finds the same
                self.mark("apply", "failed", exit=code, counts=counts)
                raise Stop(EXIT["apply"], f"apply: {counts['conflict']} conflict(s); see {self.dir / 'apply.out'}")
            if this == self.args.apply_tries:
                if why:
                    raise self.stalled("apply", why, last)
                self.mark("apply", "failed", exit=code, counts=counts)
                raise Stop(EXIT["apply"], f"apply: did not finish after {this} tries (exit {code}); running the same "
                                          f"gwt-run again goes on from here; see {self.dir / 'apply.out'}")
            print(f"apply: try {tries} {'stalled' if why else f'exited {code}'}; another in {self.args.retry_wait:g} s "
                  "(what is done reads as a skip)", flush=True)
            time.sleep(self.args.retry_wait)

    def verify(self) -> None:
        out = self.dir / "verify.json"
        for tries in range(1, self.args.verify_tries + 1):
            out.unlink(missing_ok=True)
            code, why, last = self.watch("verify", self.apply_cmd("--verify", "--json", str(out), "--log",
                                         str(self.dir / "verify.jsonl")),
                                         deadline=self.args.verify_timeout, log_glob="verify.*.jsonl")
            rows = json.loads(out.read_text(encoding="utf-8")) if out.exists() and not why else []
            read = code == 0 or any(r["status"] != "not run" for r in rows)  # else it never read the tree
            if not why and read:
                break
            print(f"verify: try {tries} {why or f'could not read the tree (exit {code})'}", flush=True)
            if tries < self.args.verify_tries and not why:
                time.sleep(self.args.retry_wait)
        else:
            if why:
                raise self.stalled("verify", why, last)
            self.mark("verify", "failed", exit=code)
            raise Stop(EXIT["failed"], f"verify: could not read the tree after {tries} tries (exit {code}); running "
                                       f"the same gwt-run again goes on from here; see {self.dir / 'verify.out'}")
        if code != 0:
            missing = [f"[{r['status']}] {r['key']}: {r['reason']}" for r in rows if r["status"] != "skip"]
            self.mark("verify", "failed", exit=code, missing=missing)
            raise Stop(EXIT["verify"], f"verify: {len(missing)} op(s) not in the tree; see {self.dir / 'verify.out'}")
        self.mark("verify", "ok")

    def gate(self) -> None:
        out = self.dir / "gate.json"
        out.unlink(missing_ok=True)
        cmd = [sys.executable, "-m", "gwt_check", "check", "--from", str(self.pre), "--baseline", str(self.pre),
               "--json", str(out)]
        if self.args.known:
            cmd += ["--known", self.args.known]
        if self.args.config:
            cmd += ["--config", self.args.config]
        code, why, last = self.watch("gate", cmd)
        if why:
            raise self.stalled("gate", why, last)
        if code == 0:
            self.mark("gate", "ok")
            return
        try:
            new = json.loads(out.read_text(encoding="utf-8")).get("new") or []
        except (OSError, ValueError, AttributeError):
            new = []
        if code == 1 and new:
            self.mark("gate", "failed", exit=code, new=len(new))
            raise Stop(EXIT["gate"], f"gate: gwt-check found {len(new)} new problem(s) since the backup; "
                                     f"see {self.dir / 'gate.out'}")
        self.mark("gate", "failed", exit=code)  # it crashed or could not read the tree: no finding to triage
        raise Stop(EXIT["failed"], f"gate: gwt-check did not finish (exit {code}); running the same gwt-run again "
                                   f"tries it again; see {self.dir / 'gate.out'}")

    def run(self) -> int:
        for name in STEPS:
            if self.step(name).get("status") == "ok":
                print(f"{name}: done before, skipped", flush=True)
                continue
            print(f"{name}:", flush=True)
            out = self.dir / f"{name}.out"
            # this step ran before: its .out keeps this attempt only, the last one with output stays beside it
            # as .out.prev, a name a *.out glob does not match; an empty .out (a killed step) is just reused
            if out.exists() and out.stat().st_size:
                os.replace(out, self.dir / f"{name}.out.prev")
            self.current = name
            getattr(self, name)()
        self.state.update(status="done", ended=_now())
        self.save()
        print(f"done: every step went through; {self.state_path}")
        return 0


def _lock(path: Path):
    """One gwt-run per folder at a time. None when another holds it."""
    try:
        import fcntl
    except ImportError:  # no flock on this system: the caller keeps to one run per folder
        return open(path, "a")
    fh = open(path, "a")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gwt-run", description=__doc__.split("\n\n")[0],
                                 epilog="Exit codes: 0 done, 1 unexpected, 2 bad input, 3 dry run refused, "
                                        "4 apply did not finish, 5 stalled, 6 verify failed, 7 gate failed, 128+N signal N.")
    ap.add_argument("change_list", help="the gwt-apply change list")
    ap.add_argument("--dir", help="the run's folder: run.json, the backup, each step's output (default LIST.run)")
    ap.add_argument("--config", help="KEY=VALUE file with GWT_URL, the login and GWT_TREE, passed to every step")
    ap.add_argument("--tree", help="the tree id the login must land in (GWT_TREE for every step)")
    ap.add_argument("--known", help="gwt-check known file: findings the gate accepts")
    ap.add_argument("--tag", help="passed to gwt-apply: a tag on every object the run creates")
    ap.add_argument("--private-new", action="store_true", help="passed to gwt-apply: new objects are private")
    ap.add_argument("--server-backup", action="store_true",
                    help="also ask the server for a backup before the first write (gwt-apply --server-backup, until it confirms one)")
    ap.add_argument("--accept-skip", action="append", default=[], metavar="KEY",
                    help="an op key whose dry-run skip is expected (repeatable); a no-op skip never stops the run")
    ap.add_argument("--retries", type=int, default=5, help="gwt-apply --retries: tries per op (default 5)")
    ap.add_argument("--retry-wait", type=float, default=30.0,
                    help="seconds after a busy server, per op and between apply tries (default 30)")
    ap.add_argument("--apply-tries", type=int, default=3, help="whole apply runs before giving up, per run of gwt-run (default 3)")
    ap.add_argument("--verify-tries", type=int, default=2, help="verify runs before giving up (default 2)")
    ap.add_argument("--verify-timeout", type=float, default=900.0, help="seconds one verify may take (default 900)")
    ap.add_argument("--stall", type=float, default=600.0,
                    help="seconds a step may go with no output and no API call before it is ended (default 600)")
    args = ap.parse_args(argv)
    for name in ("retries", "apply_tries", "verify_tries"):
        if getattr(args, name) < 1:
            ap.error(f"--{name.replace('_', '-')}: at least 1")
    if not Path(args.change_list).is_file():
        print(f"gwt-run: {args.change_list}: no such file", file=sys.stderr)
        return EXIT["input"]
    try:
        run = Run(args)
    except Stop as stop:
        print(f"gwt-run: {stop}", file=sys.stderr)
        return stop.code
    run.dir.mkdir(parents=True, exist_ok=True)
    lock = _lock(run.dir / "run.lock")
    if lock is None:
        print(f"gwt-run: another gwt-run is using {run.dir}", file=sys.stderr)
        return EXIT["input"]
    handlers = {}
    if threading.current_thread() is threading.main_thread():  # signal handlers live in the main thread only
        handlers = {sig: signal.signal(sig, _signalled) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        try:
            run.load()
        except Stop as stop:  # run.json stays as it was: it belongs to the run that wrote it
            print(f"gwt-run: {stop}", file=sys.stderr)
            return stop.code
        if run.state.get("status") == "done":
            print(f"done before: every step went through; {run.state_path}")
            return 0
        return run.run()
    except Stop as stop:
        run.state.update(status="stopped", exit=stop.code, why=str(stop), ended=_now())
        run.save()
        print(f"gwt-run: {stop}", file=sys.stderr)
        return stop.code
    except (KeyboardInterrupt, Signalled) as exc:  # watch() has ended the step's process by now
        signum = exc.signum if isinstance(exc, Signalled) else signal.SIGINT
        why = f"{signal.Signals(signum).name} during {run.current or 'the start'}; the step was ended"
        if run.current and run.state.get("steps"):
            run.mark(run.current, "stopped", why=why)
        if run.state.get("steps"):
            run.state.update(status="stopped", exit=128 + signum, why=why, ended=_now())
            run.save()
        print(f"gwt-run: {why}", file=sys.stderr)
        return 128 + signum
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
