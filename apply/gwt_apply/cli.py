"""gwt-apply command line."""

from __future__ import annotations

import argparse
import os
import secrets
import signal
import sys
from datetime import datetime
from pathlib import Path

from . import revert, snapshot
from .client import ApiError, Client
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


def _log_path(args, run_id: str, default: str) -> str:
    if args.log:
        p = Path(args.log)
        return str(p.with_name(f"{p.stem}.{run_id}{p.suffix or '.jsonl'}"))
    return default


def _revert(args) -> int:
    try:
        entries = revert.read_log(args.revert)
        snap = revert.read_pre(args.pre)
        pre = revert.pre_objects(snap)
    except (OSError, ValueError) as exc:
        print(f"revert: {exc}", file=sys.stderr)
        return 2
    conf = read_config(args.config)
    missing = [k for k in ("GWT_URL", "GWT_USERNAME", "GWT_PASSWORD") if not conf.get(k)]
    if missing:
        print(f"missing settings: {', '.join(missing)}", file=sys.stderr)
        return 2
    if args.apply and not conf.get("GWT_TREE"):
        print("--apply needs GWT_TREE (the tree id the login must land in)", file=sys.stderr)
        return 2
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
    client = Client(conf["GWT_URL"], conf["GWT_USERNAME"], conf["GWT_PASSWORD"], tree=conf.get("GWT_TREE"),
                    dry_run=not args.apply, log_path=_log_path(args, run_id, f"gwt-apply-revert-{run_id}.jsonl"))
    lock, handlers = None, {}
    try:
        client.login()
        bad = revert.pre_problems(snap, client.tree, entries)
        if bad:
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
        todo = [s for s in steps if s["action"] in ("restore", "delete")]
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
        done = sum(s["action"] in ("restore", "delete") for s in todo)
        changed = sum(bool(s.get("changed")) for s in steps)
        print(f"{done} undone and read back, {changed} changed since the run and left alone, "
              f"{len(manual)} for the backup, {len(problems)} problem(s)")
        return 1 if problems or manual or changed else 0
    except (LockHeld, ApiError) as exc:
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gwt-apply",
        description="Apply a change list to a Gramps Web tree. Dry-run unless --apply.",
    )
    ap.add_argument("change_list", nargs="?", help="JSON array, {\"ops\": [...]}, or JSONL")
    ap.add_argument("--apply", action="store_true", help="write; without it nothing is written")
    ap.add_argument("--config", help="KEY=VALUE file with GWT_URL, GWT_USERNAME, GWT_PASSWORD, GWT_TREE")
    ap.add_argument("--log", help="JSONL log of every API call; the run id is added to the name "
                    "(default gwt-apply-<run id>.jsonl)")
    ap.add_argument("--snapshot", metavar="FILE",
                    help="a tree snapshot: a dry run reads only it, with no server; --apply checks it is current, then reads it")
    ap.add_argument("--save-snapshot", metavar="FILE", help="read the whole tree into a new FILE (gzip JSON) and stop")
    ap.add_argument("--since", metavar="OLD",
                    help="with --save-snapshot: start from the older snapshot OLD and read only what changed since")
    ap.add_argument("--revert", metavar="LOG", help="undo the run that wrote LOG (a real run's log), using --pre, the tree "
                    "as it was before; a plan unless --apply (see docs/apply.md, Undo a run)")
    ap.add_argument("--pre", metavar="FILE", help="with --revert: a snapshot or gwt-check dump taken before that run")
    ap.add_argument("--tag", help="a Gramps tag put on every object this run creates, e.g. r12 (ops can add their own with \"tag\")")
    ap.add_argument("--retries", type=int, default=5)
    ap.add_argument("--retry-wait", type=float, default=30.0, help="seconds after a busy server")
    ap.add_argument("--break-lock", action="store_true",
                    help="take the tree's lock even if another apply's lock has not expired (only if that apply is dead)")
    args = ap.parse_args(argv)
    if args.revert:
        if args.change_list or args.save_snapshot or args.snapshot:
            ap.error("--revert stands alone: no change list, --snapshot or --save-snapshot")
        if not args.pre:
            ap.error("--revert needs --pre FILE, the tree as it was before the run")
        return _revert(args)
    if args.pre:
        ap.error("--pre goes with --revert")
    if not args.change_list and not args.save_snapshot:
        ap.error("give a change list, or --save-snapshot FILE, or --revert LOG --pre FILE")
    if args.since and not args.save_snapshot:
        ap.error("--since goes with --save-snapshot")

    ops = load_change_list(args.change_list) if args.change_list else []
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
    missing = [k for k in ("GWT_URL", "GWT_USERNAME", "GWT_PASSWORD") if not conf.get(k)]
    if missing and not offline:
        print(f"missing settings: {', '.join(missing)}", file=sys.stderr)
        return 2
    if args.apply and not conf.get("GWT_TREE"):
        print("--apply needs GWT_TREE (the tree id the login must land in)", file=sys.stderr)
        return 2
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
    log = _log_path(args, run_id, f"gwt-apply-{run_id}.jsonl")
    client = Client(
        conf.get("GWT_URL", ""),
        conf.get("GWT_USERNAME", ""),
        conf.get("GWT_PASSWORD", ""),
        tree=conf.get("GWT_TREE"),
        dry_run=not args.apply,
        log_path=log,
    )
    try:
        if offline:
            client.use_snapshot(snap, offline=True)
        else:
            client.login()
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
        print(exc, file=sys.stderr)
        return 1
    mode = "APPLY" if args.apply else "DRY-RUN (nothing written)"
    where = f"snapshot {args.snapshot} of {snap['taken']}" if snap else "the server"
    print(f"{mode} on tree {client.tree}, run {run_id}, reading {where}, {len(ops)} op(s), log {log}")
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
                print(f"the snapshot is of tree {snap.get('tree')!r}, the login is in {client.tree!r}", file=sys.stderr)
                return 1
            refused = _plan_on_snapshot(ops, snap, client, tag=args.tag)
            if refused:
                print("\n".join(refused) + "; nothing was written", file=sys.stderr)
                return 1
            client.use_snapshot(snap)
        problems = preflight(ops, client)
        if problems:
            print("\n".join(problems), file=sys.stderr)
            print(f"{len(problems)} likely duplicate(s); nothing was written", file=sys.stderr)
            return 1
        results = Applier(client, retries=args.retries, retry_wait=args.retry_wait, lock=lock, tag=args.tag).run(ops)
    except (LockHeld, ApiError) as exc:
        print(exc, file=sys.stderr)
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
    counts = {s: sum(r.status == s for r in results) for s in ("done", "skip", "conflict", "error")}
    not_run = len(ops) - len(results)
    print(", ".join(f"{v} {k}" for k, v in counts.items()) + (f", {not_run} not run" if not_run else ""))
    return 0 if not (counts["conflict"] or counts["error"]) else 1
