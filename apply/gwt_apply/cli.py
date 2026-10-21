"""gwt-apply command line."""

from __future__ import annotations

import argparse
import os
import secrets
import signal
import sys
from datetime import datetime
from pathlib import Path

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gwt-apply",
        description="Apply a change list to a Gramps Web tree. Dry-run unless --apply.",
    )
    ap.add_argument("change_list", help="JSON array, {\"ops\": [...]}, or JSONL")
    ap.add_argument("--apply", action="store_true", help="write; without it nothing is written")
    ap.add_argument("--config", help="KEY=VALUE file with GWT_URL, GWT_USERNAME, GWT_PASSWORD, GWT_TREE")
    ap.add_argument("--log", help="JSONL log of every API call; the run id is added to the name "
                    "(default gwt-apply-<run id>.jsonl)")
    ap.add_argument("--retries", type=int, default=5)
    ap.add_argument("--retry-wait", type=float, default=30.0, help="seconds after a busy server")
    ap.add_argument("--break-lock", action="store_true",
                    help="take the tree's lock even if another apply's lock has not expired (only if that apply is dead)")
    args = ap.parse_args(argv)

    ops = load_change_list(args.change_list)
    errors = validate(ops)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        print(f"{len(errors)} problem(s); nothing was read or written", file=sys.stderr)
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
    if args.log:
        p = Path(args.log)
        log = str(p.with_name(f"{p.stem}.{run_id}{p.suffix or '.jsonl'}"))
    else:
        log = f"gwt-apply-{run_id}.jsonl"
    client = Client(
        conf["GWT_URL"],
        conf["GWT_USERNAME"],
        conf["GWT_PASSWORD"],
        tree=conf.get("GWT_TREE"),
        dry_run=not args.apply,
        log_path=log,
    )
    try:
        client.login()
    except ApiError as exc:
        print(exc, file=sys.stderr)
        return 1
    mode = "APPLY" if args.apply else "DRY-RUN (nothing written)"
    print(f"{mode} on tree {client.tree}, run {run_id}, {len(ops)} op(s), log {log}")
    lock, handlers = None, {}
    try:
        if args.apply:
            lock = TreeLock(client, run_id, retries=args.retries, retry_wait=args.retry_wait)
            took = lock.acquire(break_lock=args.break_lock)
            if took:
                print(took)
            # a kill or a closed terminal still releases the lock on the way out
            handlers = {sig: signal.signal(sig, lambda *_: sys.exit(1)) for sig in (signal.SIGTERM, signal.SIGHUP)}
        else:
            held = read_lock(client)
            if held is not None:
                print(f"note: run {held.get('run')} on {held.get('host')} holds this tree; it may change under this plan")
        problems = preflight(ops, client)
        if problems:
            print("\n".join(problems), file=sys.stderr)
            print(f"{len(problems)} likely duplicate(s); nothing was written", file=sys.stderr)
            return 1
        results = Applier(client, retries=args.retries, retry_wait=args.retry_wait, lock=lock).run(ops)
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
