"""gwt-apply command line."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from . import revert, snapshot, version
from .client import ApiError, Client, Retryable, url_problem
from .dupes import preflight
from .lock import LockHeld, TreeLock, read_lock
from .ops import Applier, load_change_list, validate


def read_config(path: str | None) -> dict:
    """GWT_* settings: the environment wins over KEY=VALUE lines in the config file."""
    conf: dict[str, str] = {}
    if path:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    conf[k.strip()] = v.strip().strip("'\"")
    conf.update({k: v for k, v in os.environ.items() if k.startswith("GWT_")})
    return conf


def settings_problem(conf: dict) -> str | None:
    """What stops a login: a missing setting, or a GWT_URL that is no web address."""
    need = ["GWT_URL"] + ([] if conf.get("GWT_TOKEN") else ["GWT_USERNAME", "GWT_PASSWORD"])
    missing = [k for k in need if not conf.get(k)]
    if missing:
        hint = " (or GWT_TOKEN instead of a username and password)" if missing != ["GWT_URL"] else ""
        return f"missing settings: {', '.join(missing)}{hint}"
    return url_problem(conf["GWT_URL"])


def _client(conf: dict, **kw) -> Client:
    return Client(conf.get("GWT_URL", ""), conf.get("GWT_USERNAME", ""), conf.get("GWT_PASSWORD", ""),
                  tree=conf.get("GWT_TREE"), token=conf.get("GWT_TOKEN") or None, **kw)


def _plan_on_snapshot(ops: list[dict], snap: dict, client: Client, *, tag: str | None = None) -> list[str]:
    """Plan the whole list on the snapshot, offline; then what stops the plan or changed since the snapshot."""
    planner = Client(client.base[: -len("/api")], "", "", dry_run=True)
    planner.use_snapshot(snap, offline=True)
    problems = preflight(ops, planner)
    plan = Applier(planner, retries=1, retry_wait=0, tag=tag).run(ops)
    problems += [f"[{r.status}] {r.key}: {r.actions[-1]}" for r in plan if r.status in ("conflict", "error")]
    if problems:
        return problems + ["the dry run on the snapshot does not go through"]
    changed = snapshot.check(client, snap, planner)
    if changed:
        more = [f"... and {len(changed) - 40} more"] if len(changed) > 40 else []
        return changed[:40] + more + ["the tree changed since the snapshot; take a new one"]
    return []


def state_dir() -> Path:
    """$XDG_STATE_HOME, or ~/.local/state when it is unset or not an absolute path (the XDG rule)."""
    env = os.environ.get("XDG_STATE_HOME", "")
    return Path(env) if os.path.isabs(env) else Path.home() / ".local" / "state"


def _log_path(args, run_id: str, tree: str | None, *, prefix: str, writes: bool) -> str | None:
    """`--log PATH` with the run id before the extension; else, for a run that writes, a file under the state
    dir, one folder per tree; else (a dry run, a snapshot) no log."""
    if args.log:
        p = Path(args.log)
        return str(p.with_name(f"{p.stem}.{run_id}{p.suffix or '.jsonl'}"))
    if not writes:
        return None
    folder = re.sub(r"[^A-Za-z0-9._-]", "_", tree or "")
    folder = "_" if folder in ("", ".", "..") else folder  # never the state dir itself or the one above it
    return str(state_dir() / "gwt-apply" / folder / f"{prefix}-{run_id}.jsonl")


def _sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _first_line(run_id: str, log: str | None) -> None:
    """Printed before the login, so a run that stops there still names its run id."""
    print(f"run {run_id}, log " + (f"{log} (created once logged in)" if log else "none (add --log PATH to keep one)"),
          flush=True)


def _retried(what: str, fn, client: Client, args, lock=None):
    """`fn()`, retried like an op on a busy server: the login and the whole-tree reads before the first op are the
    run's slowest calls, and one timeout there must not end a run that has not written anything yet."""
    for attempt in range(1, args.retries + 1):
        if lock is not None:
            lock.refresh()
        try:
            return fn()
        except Retryable as exc:
            if attempt == args.retries:
                raise
            client.log(event="retry", attempt=attempt, error=f"{what}: {exc}")
            time.sleep(args.retry_wait)
    raise AssertionError("unreachable: --retries is at least 1")


def _ended(client: Client, run, outcome: dict) -> int:
    """Run `run()` and close the log with an `end` line that says how it ended, also on a kill or a crash."""
    code = None
    try:
        code = run()
        return code
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        raise
    except BaseException as exc:
        outcome.setdefault("error", f"{type(exc).__name__}: {exc}"[:500])
        raise
    finally:
        client.end_log(exit=code, counts=outcome.get("counts"), not_run=outcome.get("not_run"),
                       **({"error": outcome["error"]} if "error" in outcome else {}))


def _revert(args, argv: list[str]) -> int:
    try:
        entries = revert.read_log(args.revert)
        snap = revert.read_pre(args.pre)
        pre = revert.pre_objects(snap)
    except (OSError, ValueError) as exc:
        print(f"revert: {exc}", file=sys.stderr)
        return 2
    conf = read_config(args.config)
    problem = settings_problem(conf)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    if args.apply and not conf.get("GWT_TREE"):
        print("--apply needs GWT_TREE (the tree id the login must land in)", file=sys.stderr)
        return 2
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
    log = _log_path(args, run_id, conf.get("GWT_TREE"), prefix="gwt-apply-revert", writes=args.apply)
    client = _client(conf, dry_run=not args.apply, log_path=log)
    _first_line(run_id, log)
    outcome: dict = {}
    return _ended(client, lambda: _revert_run(args, argv, client, run_id, entries, snap, pre, outcome), outcome)


def _revert_run(args, argv, client: Client, run_id: str, entries, snap, pre, outcome: dict) -> int:
    lock, handlers = None, {}
    client.run_info = {"run": run_id, "version": version(), "argv": argv, "list": args.revert,
                       "list_sha256": _sha256(args.revert), "snapshot": None, "pre": args.pre}
    try:
        client.login()
        bad = revert.pre_problems(snap, client.tree, entries)
        if bad:
            client.log(event="refused", reason="pre", problems=bad)
            print("\n".join(f"revert: {b}" for b in bad), file=sys.stderr)
            print("revert: --pre must be a --save-snapshot or gwt-check dump of this tree taken right before the run;"
                  " nothing was written", file=sys.stderr)
            return 2
        if args.apply:  # the plan is made under the lock, so nothing another apply writes slips in between
            lock = TreeLock(client, run_id, retries=args.retries, retry_wait=args.retry_wait)
            took = lock.acquire(break_lock=args.break_lock)
            if took:
                print(took)
            handlers = {sig: signal.signal(sig, lambda *_: sys.exit(1)) for sig in (signal.SIGTERM, signal.SIGHUP)}
        steps = revert.plan(entries, pre, lambda cls, h: client.get_live(cls, h),
                            lambda cls, h: (client.get_live(cls, h, backlinks=True) or {}).get("backlinks"))
        for s in steps:
            print(f"[{s['action']}] {s['cls']} {s['handle']}: {s['why']}")
        todo = [s for s in steps if s["action"] in ("restore", "recreate", "delete")]
        manual = [s for s in steps if s["action"] == "manual"]
        print(f"{len(todo)} to undo, {sum(s['action'] == 'skip' for s in steps)} skipped "
              f"({sum(bool(s.get('changed')) for s in steps)} changed since the run), {len(manual)} for the backup")
        if not args.apply:
            print("PLAN only: nothing was written; add --apply to undo")
            return 0
        problems = revert.execute(client, todo, pre, lock=lock, retries=args.retries, retry_wait=args.retry_wait)
        for s in todo:
            if s["action"] == "skip":
                print(f"[skip] {s['cls']} {s['handle']}: {s['why']}")
        for p in problems:
            print(f"PROBLEM: {p}", file=sys.stderr)
        done = sum(s["action"] in ("restore", "recreate", "delete") for s in todo)
        changed = sum(bool(s.get("changed")) for s in steps)
        outcome["counts"] = {"undone": done, "changed": changed, "manual": len(manual), "problems": len(problems)}
        print(f"{done} undone and read back, {changed} changed since the run and left alone, "
              f"{len(manual)} for the backup, {len(problems)} problem(s)")
        return 1 if problems or manual or changed else 0
    except (LockHeld, ApiError) as exc:
        outcome["error"] = str(exc)
        print(exc, file=sys.stderr)
        if lock is not None:
            print("stopped; running the same command again is safe: what is already undone reads as a skip",
                  file=sys.stderr)
        return 1
    finally:
        if lock is not None and lock.expires:
            lock.release()
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def _at_least_one(text: str) -> int:
    n = int(text)
    if n < 1:  # zero tries would take no lock, run no op, and report every op done
        raise argparse.ArgumentTypeError(f"{text}: at least 1")
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gwt-apply",
        description="Apply a change list to a Gramps Web tree. Dry-run unless --apply.",
    )
    ap.add_argument("change_list", nargs="?", help="JSON array, {\"ops\": [...]}, or JSONL")
    ap.add_argument("--apply", action="store_true", help="write; without it nothing is written")
    ap.add_argument("--verify", action="store_true",
                    help="prove the list landed: a dry run that exits 1 if any op is not in the tree, and says why")
    ap.add_argument("--json", metavar="FILE", help="write one result per op (key, op, status, reason) to FILE")
    ap.add_argument("--config", help="KEY=VALUE file with GWT_URL, GWT_USERNAME and GWT_PASSWORD (or GWT_TOKEN), GWT_TREE")
    ap.add_argument("--log", help="JSONL log of every API call; the run id is added to the name (default: a real run "
                    "logs under $XDG_STATE_HOME/gwt-apply/<tree>/, a dry run keeps no log)")
    ap.add_argument("--snapshot", metavar="FILE",
                    help="a tree snapshot or gwt-check dump: a dry run reads only it, with no server; --apply checks it "
                    "is current, then reads it")
    ap.add_argument("--save-snapshot", metavar="FILE", help="read the whole tree into a new FILE (gzip JSON) and stop")
    ap.add_argument("--since", metavar="OLD",
                    help="with --save-snapshot: start from OLD, an older snapshot or gwt-check dump, and read only what "
                    "changed since")
    ap.add_argument("--revert", metavar="LOG", help="undo the run that wrote LOG (a real run's log), using --pre, the tree "
                    "as it was before; a plan unless --apply (see docs/apply.md, Undo a run)")
    ap.add_argument("--pre", metavar="FILE", help="with --revert: a snapshot or gwt-check dump taken before that run")
    ap.add_argument("--tag", help="a Gramps tag put on every object this run creates, e.g. r12 (ops can add their own with \"tag\")")
    ap.add_argument("--retries", type=_at_least_one, default=5,
                    help="tries per op, and for the login and reads before the first op (at least 1)")
    ap.add_argument("--retry-wait", type=float, default=30.0, help="seconds after a busy server")
    ap.add_argument("--server-backup", action="store_true",
                    help="with --apply: ask the server to store a backup of the tree before the first write, and write "
                    "nothing if it refuses (never restores one)")
    ap.add_argument("--break-lock", action="store_true",
                    help="take the tree's lock even if another apply's lock has not expired (only if that apply is dead)")
    argv = list(sys.argv[1:] if argv is None else argv)
    args = ap.parse_args(argv)
    if args.revert:
        if args.change_list or args.save_snapshot or args.snapshot:
            ap.error("--revert stands alone: no change list, --snapshot or --save-snapshot")
        if not args.pre:
            ap.error("--revert needs --pre FILE, the tree as it was before the run")
        return _revert(args, argv)
    if args.pre:
        ap.error("--pre goes with --revert")
    if args.server_backup and not args.apply:
        ap.error("--server-backup goes with --apply: a dry run writes nothing, so it needs no backup")
    if args.verify and (args.apply or args.save_snapshot):
        ap.error("--verify is a dry run: no --apply or --save-snapshot")
    if args.json and not args.change_list:
        ap.error("--json goes with a change list")
    if not args.change_list and not args.save_snapshot:
        ap.error("give a change list, or --save-snapshot FILE, or --revert LOG --pre FILE")
    if args.since and not args.save_snapshot:
        ap.error("--since goes with --save-snapshot")

    try:
        ops = load_change_list(args.change_list) if args.change_list else []
    except (OSError, ValueError) as exc:
        print(f"change list: {exc}", file=sys.stderr)
        return 2
    errors = validate(ops)
    if args.tag is not None and not args.tag.strip():
        errors.append("--tag: a tag is a non-empty name")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        print(f"{len(errors)} problem(s); nothing was read or written", file=sys.stderr)
        return 2
    snap = old = None
    try:
        snap = snapshot.load(args.snapshot) if args.snapshot else None
        old = snapshot.load(args.since) if args.since else None
    except (OSError, ValueError) as exc:
        print(f"snapshot: {exc}", file=sys.stderr)
        return 2
    offline = snap is not None and not args.apply

    conf = read_config(args.config)
    problem = None if offline else settings_problem(conf)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    if args.apply and not conf.get("GWT_TREE"):
        print("--apply needs GWT_TREE (the tree id the login must land in)", file=sys.stderr)
        return 2
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
    log = _log_path(args, run_id, conf.get("GWT_TREE"), prefix="gwt-apply",
                    writes=args.apply and not args.save_snapshot)
    client = _client(conf, dry_run=not args.apply, log_path=log)
    _first_line(run_id, log)
    outcome: dict = {}
    return _ended(client, lambda: _run(args, argv, client, run_id, ops, snap, old, offline, outcome), outcome)


def _backup_id_of_task(t: dict):
    """The id of the backup a finished task made: `result_object.id`, else the JSON in `info` or `result`."""
    ro = t.get("result_object")
    if isinstance(ro, dict) and ro.get("id"):
        return ro["id"]
    for key in ("info", "result"):
        try:
            v = json.loads(t.get(key) or "")
        except ValueError:
            continue
        if isinstance(v, dict) and v.get("id"):
            return v["id"]
    return None


def _server_backup(client: Client, args, lock) -> str | None:
    """Ask the server to store a backup of the tree. None when it did, else why not. It only creates: a restore is
    never called from here. The server deletes its oldest backup beyond the number it keeps per tree. A server with a
    task queue answers 202 with a task: the backup counts only when that task succeeds and the backup it names is in
    the list and restorable. Every read is retried like the login; the POST is never repeated."""
    def get(path):
        return _retried("server backup", lambda: client._request("GET", path), client, args, lock)

    def make():
        return client._request("POST", "/backups/", write=True)
    try:
        r = make()  # never repeated: a second POST could store a second backup
    except Retryable as exc:
        return f"the server backup request failed ({exc}); nothing was written"
    if r.status_code == 202:
        try:
            tid = (r.json().get("task") or {}).get("id")
        except (ValueError, AttributeError):
            tid = None
        if not tid:
            return "the server answered HTTP 202 without a task id, so the backup cannot be confirmed"
        info = None
        for _ in range(30):
            t = get(f"/tasks/{tid}")
            task = t.json() if t.status_code == 200 else {}
            if task.get("state") == "FAILURE" or task.get("status") in ("failed", "lost"):
                return f"the server's backup task {tid} failed"
            if task.get("state") == "SUCCESS" or task.get("status") == "succeeded":
                bid = _backup_id_of_task(task)
                lst = get("/backups/")
                found = [b for b in (lst.json() if lst.status_code == 200 else []) if b.get("id") == bid]
                if not bid or not found:
                    return f"the backup task {tid} succeeded but its backup is not in the server's list"
                if found[0].get("restorable") is not True or not found[0].get("size"):
                    return f"the backup {bid} is listed but not restorable or empty"
                info = found[0]
                break
            time.sleep(2)
        if info is None:
            return f"the server's backup task {tid} did not finish in 60 s"
    elif r.status_code != 201:
        return f"the server did not store a backup (HTTP {r.status_code}; creating one needs an owner or admin login)"
    else:
        try:
            info = r.json()
        except ValueError:
            info = {}
    client.log(event="server-backup", id=info.get("id"), size=info.get("size"))
    print(f"server backup stored: {info.get('id')}")
    return None


def _run(args, argv, client: Client, run_id: str, ops, snap, old, offline: bool, outcome: dict) -> int:
    client.run_info = {"run": run_id, "version": version(), "argv": argv, "list": args.change_list,
                       "list_sha256": _sha256(args.change_list) if args.change_list else None,
                       "snapshot": args.snapshot}
    try:
        if offline:
            client.use_snapshot(snap, offline=True)
        else:
            _retried("login", client.login, client, args)
        if args.save_snapshot and old is not None:
            counts = snapshot.refresh(client, old, args.save_snapshot)
            print(f"snapshot of tree {client.tree} in {args.save_snapshot}, from {args.since} of {old['taken']}: "
                  + ", ".join(f"{n} {cls} ({read} read, {gone} deleted)" for cls, (n, read, gone) in counts.items()))
            return 0
        if args.save_snapshot:
            counts = snapshot.save(client, args.save_snapshot)
            print(f"snapshot of tree {client.tree} in {args.save_snapshot}: "
                  + ", ".join(f"{n} {cls}" for cls, n in counts.items()))
            return 0
    except FileExistsError as exc:
        print(f"{exc.filename} already exists; a snapshot is never overwritten", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"snapshot: {exc}", file=sys.stderr)
        return 2
    except ApiError as exc:
        outcome["error"] = str(exc)
        print(exc, file=sys.stderr)
        return 1
    mode = "APPLY" if args.apply else "VERIFY (nothing written)" if args.verify else "DRY-RUN (nothing written)"
    where = f"snapshot {args.snapshot} of {snap['taken']}" if snap else "the server"
    print(f"{mode} on tree {client.tree}, run {run_id}, reading {where}, {len(ops)} op(s)")
    lock, handlers = None, {}
    try:
        if args.apply:
            lock = TreeLock(client, run_id, retries=args.retries, retry_wait=args.retry_wait)
            took = lock.acquire(break_lock=args.break_lock)
            if took:
                print(took)
            # a kill or a closed terminal still releases the lock on the way out
            handlers = {sig: signal.signal(sig, lambda *_: sys.exit(1)) for sig in (signal.SIGTERM, signal.SIGHUP)}
        elif not offline:
            held = read_lock(client)
            if held is not None:
                print(f"note: run {held.get('run')} on {held.get('host')} holds this tree; it may change under this plan")
        if snap is not None and args.apply:
            if snap.get("tree") != client.tree:
                msg = f"the snapshot is of tree {snap.get('tree')!r}, the login is in {client.tree!r}"
                client.log(event="refused", reason="snapshot-tree", problems=[msg])
                print(msg, file=sys.stderr)
                _write_json(args.json, ops, [], "not run: the snapshot check refused the list")
                return 1
            refused = _retried("snapshot check", lambda: _plan_on_snapshot(ops, snap, client, tag=args.tag),
                               client, args, lock)
            if refused:
                client.log(event="refused", reason="snapshot-plan", problems=refused)
                print("\n".join(refused) + "; nothing was written", file=sys.stderr)
                _write_json(args.json, ops, [], "not run: the snapshot check refused the list")
                return 1
            client.use_snapshot(snap)
        problems = _retried("preflight", lambda: preflight(ops, client), client, args, lock)
        if problems:
            client.log(event="refused", reason="preflight", problems=problems)
            print("\n".join(problems), file=sys.stderr)
            print(f"{len(problems)} likely duplicate(s); nothing was written", file=sys.stderr)
            _write_json(args.json, ops, [], "not run: the list stopped on likely duplicates")
            return 1
        if args.server_backup:
            refused = _server_backup(client, args, lock)
            if refused:
                client.log(event="refused", reason="server-backup", problems=[refused])
                print(f"{refused}; nothing was written", file=sys.stderr)
                _write_json(args.json, ops, [], "not run: the server backup was refused")
                return 1
        results = Applier(client, retries=args.retries, retry_wait=args.retry_wait, lock=lock, tag=args.tag,
                          verify=args.verify).run(ops)
    except (LockHeld, ApiError) as exc:
        outcome["error"] = str(exc)
        print(exc, file=sys.stderr)
        _write_json(args.json, ops, [], f"not run: {exc}")
        return 1
    finally:
        if lock is not None and lock.expires:
            lock.release()
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    for r in results:
        print(f"[{r.status}] {r.key} ({r.op}, ruling {r.ruling})")
        for a in r.actions:
            print(f"    {a}")
        if args.verify and r.status == "done":
            for why in r.reasons:
                print(f"    {why}")
    counts = {s: sum(r.status == s for r in results) for s in ("done", "skip", "conflict", "error")}
    not_run = len(ops) - len(results)
    outcome.update(counts=counts, not_run=not_run)
    lost = sum(r.answer_lost for r in results)
    words = [f"{v} {k}" + (f" (answer lost: {lost})" if k == "done" and lost else "") for k, v in counts.items()]
    print(", ".join(words) + (f", {not_run} not run" if not_run else ""))
    noop = [r for r in results if r.noop]
    if counts["skip"]:
        # a first dry run of a list should skip nothing; a no-op skip is the exception a chain can accept: the tree
        # already had that value or tag. The line above keeps its form, so a check that reads it still works
        print(f"NOOP: {len(noop)} of {counts['skip']} skip(s) found the tree already as the op asks"
              + (" (" + ", ".join(f"{r.key} {r.op}" for r in noop) + ")" if noop else ""))
    _write_json(args.json, ops, results, "not run: an earlier op stopped the list")
    if args.verify:
        landed = counts["skip"]
        print(f"VERIFY: {landed} of {len(ops)} op(s) in the tree" + ("" if landed == len(ops) else
              f"; {len(ops) - landed} not (see the missing, differs, conflict and error lines above)"))
        return 0 if landed == len(ops) else 1
    return 0 if not (counts["conflict"] or counts["error"]) else 1


def _reason(r) -> str:
    if r.status == "done":
        return "; ".join(r.reasons) or (r.actions[-1] if r.actions else "")
    if r.status in ("conflict", "error") or r.noop:
        return r.actions[-1] if r.actions else ""
    return ""


def _write_json(path: str | None, ops: list[dict], results: list, not_run: str) -> None:
    """One object per op of the list, in its order: the ops a run never reached carry status `not run`."""
    if not path:
        return
    rows = [{"key": r.key, "op": r.op, "status": r.status, "reason": _reason(r), **({"noop": True} if r.noop else {})}
            for r in results]
    rows += [{"key": op["key"], "op": op["op"], "status": "not run", "reason": not_run} for op in ops[len(results):]]
    Path(path).write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
