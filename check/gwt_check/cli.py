"""gwt-check command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .checks import CHECKS, run_checks
from .client import ApiError, Client
from .compare import ahnentafel, compare_ahnentafel, diff
from .tree import BadInput, Tree, load, save


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


def live_dump(args) -> dict:
    conf = read_config(args.config)
    missing = [k for k in ("GWT_URL", "GWT_USERNAME", "GWT_PASSWORD") if not conf.get(k)]
    if missing:
        raise BadInput(f"missing settings: {', '.join(missing)}")
    client = Client(conf["GWT_URL"], conf["GWT_USERNAME"], conf["GWT_PASSWORD"], tree=conf.get("GWT_TREE"))
    t0 = time.monotonic()
    try:
        client.login()
        data = client.dump(pagesize=args.pagesize, workers=args.workers,
                           progress=lambda t, n: print(f"  {t}: {n}", file=sys.stderr, flush=True))
    except ApiError as exc:
        raise BadInput(str(exc)) from exc
    print(f"dumped tree {client.tree} in {time.monotonic() - t0:.0f} s, {client.requests} requests",
          file=sys.stderr)
    return {"tree": client.tree, "dumped_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "objects": data}


def load_known(path: str) -> set[tuple[str, str]]:
    """A previous `--json` output, or a list of {"check", "key"} objects."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise BadInput(f"--known {path}: {exc}") from exc
    rows = data.get("findings", []) if isinstance(data, dict) else data
    try:
        return {(r["check"], r["key"]) for r in rows}
    except (TypeError, KeyError) as exc:
        raise BadInput(f"--known {path}: every finding needs check and key") from exc


def check(tree: Tree, *, since: Tree | None = None, tag: str | None = None, root: str | None = None,
          known: set | None = None, ignore: set | None = None) -> dict:
    """Every check on one tree. Returns the report; report["new"] are the findings not in `known`."""
    if tree.partial:
        raise BadInput(f"{tree.source}: a .gramps backup can be the earlier tree (--since), not the one checked")
    tagged = None
    if tag:
        if not tree.has_tags:
            raise BadInput(f"--tag needs a dump with tags; {tree.source or 'this tree'} has none")
        tagged = tree.tagged(tag)
    scope = None
    if since is not None or tag:
        scope = set(tagged or ())
        if since is not None:
            scope |= {(t, h) for t, objs in tree.objs.items() for h in objs if h not in since.objs[t]}
    findings, info = run_checks(tree, scope)
    report = {"counts": tree.counts(), "scope": None if scope is None else len(scope), "info": info}
    if since is not None:
        f, report["diff"] = diff(since, tree, tagged)
        findings += f
        if root:
            f, report["ahnentafel"] = compare_ahnentafel(since, tree, root)
            findings += f
    elif root:
        a = ahnentafel(tree, root)
        if not a:
            raise BadInput(f"--root {root}: no such person")
        report["ahnentafel"] = {"numbers": len(a), "people": len(set(a.values()))}
    known = known or set()
    ignore = ignore or set()
    unknown = ignore - set(CHECKS)
    if unknown:
        raise BadInput(f"--ignore-check: no such check {sorted(unknown)}; the checks are {sorted(CHECKS)}")
    for f in findings:
        f["ignored"] = f["check"] in ignore
        f["known"] = f["ignored"] or (f["check"], f["key"]) in known
    report["findings"] = findings
    report["new"] = [f for f in findings if not f["known"]]
    report["ignored"] = {c: sum(1 for f in findings if f["check"] == c) for c in sorted(ignore)}
    return report


def print_report(report: dict, limit: int) -> None:
    c = report["counts"]
    print("tree: " + ", ".join(f"{v} {k}" for k, v in c.items()))
    if report["scope"] is not None:
        print(f"scope: {report['scope']} objects (duplicates need one side in scope)")
    by_check: dict[str, list] = {}
    for f in report["findings"]:
        by_check.setdefault(f["check"], []).append(f)
    for name in CHECKS:
        fs = by_check.get(name, [])
        new = [f for f in fs if not f["known"]]
        if not fs:
            continue
        print(f"\n{'FAIL' if new else 'known'} {name}: {len(new)} new, {len(fs) - len(new)} known  ({CHECKS[name]})")
        for f in new[:limit]:
            print(f"  {f['key']}" + (f"  [{f['detail']}]" if f["detail"] else ""))
        if len(new) > limit:
            print(f"  ... {len(new) - limit} more (--limit, or --json for all)")
    info = report["info"]
    print(f"\nevents without place: {info['events without place']}"
          + (f", {len(info['events without place, in scope'])} in scope" if "events without place, in scope" in info else ""))
    if "diff" in report:
        d = report["diff"]
        for k in ("changed", "new"):
            print(f"{k}: " + ", ".join(f"{len(v)} {t}" for t, v in d[k].items() if v) or f"{k}: none")
        if d["last change"]:
            print(f"last change: {d['last change']['gramps_id']} at {d['last change']['change']}")
    if "ahnentafel" in report:
        a = report["ahnentafel"]
        if "before" in a:
            print(f"Ahnentafel: {a['before']['numbers']} numbers / {a['before']['people']} people before, "
                  f"{a['after']['numbers']} / {a['after']['people']} after, {len(a['new numbers'])} new numbers")
        else:
            print(f"Ahnentafel: {a['numbers']} numbers, {a['people']} people")
    for name, count in report.get("ignored", {}).items():
        print(f"ignored {name}: {count} finding(s), not counted (--ignore-check)")
    n = len(report["new"])
    print(f"\n{'FAIL' if n else 'OK'}: {n} new finding(s), {len(report['findings']) - n} known")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gwt-check", description="Read-only checks of a whole Gramps Web tree.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def live_opts(p):
        p.add_argument("--config", help="KEY=VALUE file with GWT_URL, GWT_USERNAME, GWT_PASSWORD, GWT_TREE")
        p.add_argument("--pagesize", type=int, default=1000, help="objects per request (default 1000)")
        p.add_argument("--workers", type=int, default=4, help="requests in flight at once (default 4)")

    p = sub.add_parser("dump", help="save the live tree to a JSON file (.gz to compress)")
    p.add_argument("out")
    live_opts(p)

    p = sub.add_parser("check", help="run every check on a dump, or on the live tree")
    p.add_argument("tree", nargs="?", help="dump file; without it the live tree is read")
    p.add_argument("--since", help="earlier dump or .gramps backup: diff, and new objects are the scope")
    p.add_argument("--tag", help="scope is the objects carrying this tag (a run tag)")
    p.add_argument("--root", help="Gramps ID of the Ahnentafel root; with --since, numbers lost or changed fail")
    p.add_argument("--known", help="earlier --json output (or list of {check, key}) to accept as known")
    p.add_argument("--ignore-check", action="append", default=[], metavar="NAME",
                   help="leave a whole check out of the exit code (repeatable); the report still counts it")
    p.add_argument("--json", help="write the full report as JSON to this file (- for stdout)")
    p.add_argument("--save-dump", help="keep the live dump in this file")
    p.add_argument("--limit", type=int, default=30, help="findings printed per check (default 30)")
    live_opts(p)

    sub.add_parser("selftest", help="plant every error kind in a synthetic tree and prove each check fires")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "selftest":
            from .selftest import selftest
            return selftest(verbose=True)
        if args.cmd == "dump":
            save(live_dump(args), args.out)
            return 0
        t0 = time.monotonic()
        known = load_known(args.known) if args.known else None
        since = load(args.since) if args.since else None
        if args.tree:
            tree = load(args.tree)
        else:
            data = live_dump(args)
            if args.save_dump:
                save(data, args.save_dump)
            tree = Tree(data["objects"], source="live tree")
        t1 = time.monotonic()
        report = check(tree, since=since, tag=args.tag, root=args.root, known=known,
                       ignore=set(args.ignore_check))
        report["seconds"] = {"read": round(t1 - t0, 1), "check": round(time.monotonic() - t1, 1)}
    except BadInput as exc:
        print(f"gwt-check: {exc}", file=sys.stderr)
        return 2
    if args.json == "-":
        json.dump(report, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        print_report(report, args.limit)
        print(f"read {report['seconds']['read']} s, checks {report['seconds']['check']} s")
        if args.json:
            with open(args.json, "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=1)
    return 1 if report["new"] else 0
