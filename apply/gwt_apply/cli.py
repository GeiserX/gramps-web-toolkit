"""gwt-apply command line."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from .client import ApiError, Client
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
    ap.add_argument("--log", help="JSONL log of every API call (default gwt-apply-<time>.jsonl)")
    ap.add_argument("--retries", type=int, default=5)
    ap.add_argument("--retry-wait", type=float, default=30.0, help="seconds after a busy server")
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
    log = args.log or f"gwt-apply-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
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
    print(f"{mode} on tree {client.tree}, {len(ops)} op(s), log {log}")
    results = Applier(client, retries=args.retries, retry_wait=args.retry_wait).run(ops)
    for r in results:
        print(f"[{r.status}] {r.key} ({r.op}, ruling {r.ruling})")
        for a in r.actions:
            print(f"    {a}")
    counts = {s: sum(r.status == s for r in results) for s in ("done", "skip", "conflict", "error")}
    not_run = len(ops) - len(results)
    print(", ".join(f"{v} {k}" for k, v in counts.items()) + (f", {not_run} not run" if not_run else ""))
    return 0 if not (counts["conflict"] or counts["error"]) else 1
