"""gwt-check command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .checks import CHECKS, INFO_CHECKS, run_checks
from .client import ApiError, Client
from . import compare
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


PASSES = 10  # rereads of what moved before a read of a tree that never stops changing gives up
SEEN_IN = 3  # passes in a row a finding must show in while the tree is being written to


def _client(args) -> Client:
    conf = read_config(args.config)
    missing = [k for k in ("GWT_URL", "GWT_USERNAME", "GWT_PASSWORD") if not conf.get(k)]
    if missing:
        raise BadInput(f"missing settings: {', '.join(missing)}")
    client = Client(conf["GWT_URL"], conf["GWT_USERNAME"], conf["GWT_PASSWORD"], tree=conf.get("GWT_TREE"))
    try:
        client.login()
    except ApiError as exc:
        raise BadInput(str(exc)) from exc
    return client


def _dump(client: Client, objs: dict) -> dict:
    return {"tree": client.tree, "dumped_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "objects": {t: list(rows.values()) for t, rows in objs.items()}}


def live_dump(args, settled=None) -> dict:
    """The live tree, read while it may change. Pages are read without trusting them, then the change stamps
    of every type are compared and what moved is read again, pass after pass, until a pass finds nothing
    moved, or `settled(dump)` says the findings no longer depend on what is moving. With --live, the old
    strict read: one pass, and a type whose count changes mid-read stops it."""
    client = _client(args)
    t0 = time.monotonic()
    progress = lambda t, n: print(f"  {t}: {n}", file=sys.stderr, flush=True)  # noqa: E731
    try:
        if args.live:
            data = {"tree": client.tree, "dumped_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "objects": client.dump(pagesize=args.pagesize, workers=args.workers, progress=progress)}
        else:
            objs = client.read_all(pagesize=args.pagesize, workers=args.workers, progress=progress)
            for n in range(1, PASSES + 1):
                moved = client.refresh(objs)
                data = _dump(client, objs)
                if not moved:
                    break
                if settled is not None and settled(data):
                    data["settled"] = True  # by findings, while writes went on
                    break
                print(f"  pass {n}: {moved} object(s) changed during the read, reading them again", file=sys.stderr)
            else:
                raise BadInput(f"the tree kept changing: {moved} object(s) still moving after {PASSES} passes")
            data["passes"] = n
    except ApiError as exc:
        raise BadInput(str(exc)) from exc
    print(f"read tree {client.tree} in {time.monotonic() - t0:.0f} s, {client.requests} requests",
          file=sys.stderr)
    return data


def load_known(path: str) -> dict[tuple[str, str], str]:
    """A previous `--json` output, or a list of {"check", "key"} objects, each with an optional "reason"."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise BadInput(f"--known {path}: {exc}") from exc
    rows = data.get("findings", []) if isinstance(data, dict) else data
    try:
        return {(r["check"], r["key"]): str(r.get("reason") or "") for r in rows}
    except (TypeError, KeyError) as exc:
        raise BadInput(f"--known {path}: every finding needs check and key") from exc


def check_names(names: set) -> None:
    unknown = set(names) - set(CHECKS)
    if unknown:
        raise BadInput(f"--ignore-check: no such check {sorted(unknown)}; the checks are {sorted(CHECKS)}")


def check(tree: Tree, *, since: Tree | None = None, tag: str | None = None, root: str | None = None,
          known: dict | set | None = None, ignore: set | None = None) -> dict:
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
    findings, info = run_checks(tree, scope, root)
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
        if a.complete_depth is not None:
            report["ahnentafel"]["truncated"] = f"over {compare.MAX_POSITIONS} positions; walked up to generation {a.complete_depth}"
            findings.append({"check": "ahnentafel-truncated", "key": f"root {root}",
                             "detail": report["ahnentafel"]["truncated"]})
    known = dict.fromkeys(known, "") if isinstance(known, set) else (known or {})
    ignore = ignore or set()
    check_names(ignore)
    for f in findings:
        f["ignored"] = f["check"] in ignore
        f["known"] = f["ignored"] or (f["check"], f["key"]) in known
        if known.get((f["check"], f["key"])):
            f["reason"] = known[(f["check"], f["key"])]
    report["findings"] = findings
    for f in findings:
        f["info"] = f["check"] in INFO_CHECKS
    report["new"] = [f for f in findings if not f["known"] and not f["info"]]
    report["ignored"] = {c: sum(1 for f in findings if f["check"] == c) for c in sorted(ignore)}
    return report


def print_report(report: dict, limit: int) -> None:
    c = report["counts"]
    print("tree: " + ", ".join(f"{v} {k}" for k, v in c.items()))
    if report["scope"] is not None:
        print(f"scope: {report['scope']} objects (duplicates need one side in scope)")
    if report.get("in_flight"):
        print(f"left out: {report['in_flight']} finding(s) seen in one pass only, writes caught halfway")
    unconfirmed = [f for f in report["findings"] if f.get("unconfirmed")]
    if unconfirmed:
        print(f"unconfirmed: {len(unconfirmed)} finding(s) seen only in the last 2 passes while the tree was being "
              "written to; a write caught halfway or a problem made late in the read: run the check again")
    by_check: dict[str, list] = {}
    for f in report["findings"]:
        by_check.setdefault(f["check"], []).append(f)
    for name in CHECKS:
        fs = by_check.get(name, [])
        new = [f for f in fs if not f["known"]]
        if not fs:
            continue
        counts = (f"{len(fs)} found ({len(fs) - len(new)} known)" if name in INFO_CHECKS
                  else f"{len(new)} new, {len(fs) - len(new)} known")
        print(f"\n{'info' if name in INFO_CHECKS else 'FAIL' if new else 'known'} {name}: {counts}  ({CHECKS[name]})")
        for f in [f for f in fs if f["known"] and f.get("reason")][:limit]:
            print(f"  known {f['key']}: {f['reason']}")
        for f in new[:limit]:
            print(f"  {f['key']}" + (f"  [{f['detail']}]" if f["detail"] else ""))
        if len(new) > limit:
            print(f"  ... {len(new) - limit} more (--limit, or --json for all)")
    info = report["info"]
    print(f"\nevents without place: {info['events without place']} (places their citations name: gwt-check fill-places)"
          + (f", {len(info['events without place, in scope'])} in scope" if "events without place, in scope" in info else ""))
    if info.get("dates not read"):
        print(f"dates not read by the dated checks: {info['dates not read']} (another calendar, from/to, dual-dated or free text)")
    if info.get("dup-person-fuzzy skipped, parents differ by name"):
        print(f"dup-person-fuzzy pairs skipped, father and mother differ by name: "
              f"{len(info['dup-person-fuzzy skipped, parents differ by name'])} (listed with --json)")
    if "sources without repository" in info:
        print(f"sources without repository: {info['sources without repository']} (list them with gwt-check repos --unlinked)")
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
            print(f"Ahnentafel: {a['numbers']} numbers, {a['people']} people"
                  + (f" (truncated: {a['truncated']})" if "truncated" in a else ""))
    for name, count in report.get("ignored", {}).items():
        print(f"ignored {name}: {count} finding(s), not counted (--ignore-check)")
    n = len(report["new"])
    n_info = sum(1 for f in report["findings"] if f["info"] and not f["known"])
    print(f"\n{'FAIL' if n else 'OK'}: {n} new finding(s), {len(report['findings']) - n - n_info} known"
          + (f", {n_info} for information only" if n_info else ""))


def mark_known(report_path: str, check_name: str, keys: list[str], reason: str, out: str) -> int:
    """Add findings of a report to a known file, each with the reason it is accepted."""
    if not reason.strip():
        raise BadInput("mark-known needs a --reason: why this is not a problem")
    try:
        with open(report_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise BadInput(f"{report_path}: {exc}") from exc
    found = {f["key"] for f in data.get("findings", []) if f["check"] == check_name}
    missing = [k for k in keys if k not in found]
    if missing:
        raise BadInput(f"not in the report as {check_name}: {missing}")
    rows: list[dict] = []
    if os.path.exists(out):
        try:
            with open(out, encoding="utf-8") as fh:
                loaded = json.load(fh)
            rows = loaded.get("findings", []) if isinstance(loaded, dict) else loaded
            have = {(r["check"], r["key"]) for r in rows}
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise BadInput(f"--out {out}: not a known file ({exc})") from exc
    else:
        have = set()
    rows += [{"check": check_name, "key": k, "reason": reason.strip()} for k in keys if (check_name, k) not in have]
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    print(f"{out}: {len(rows)} known finding(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gwt-check", description="Read-only checks of a whole Gramps Web tree.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def live_opts(p):
        p.add_argument("--config", help="KEY=VALUE file with GWT_URL, GWT_USERNAME, GWT_PASSWORD, GWT_TREE")
        p.add_argument("--pagesize", type=int, default=1000, help="objects per request (default 1000)")
        p.add_argument("--workers", type=int, default=4, help="requests in flight at once (default 4)")
        p.add_argument("--live", action="store_true",
                       help="the old strict read: stop when the tree changes mid-read instead of reading again what moved")

    p = sub.add_parser("dump", help="save the live tree to a JSON file (.gz to compress)")
    p.add_argument("out")
    live_opts(p)

    p = sub.add_parser("check", help="run every check on a dump, or on the live tree")
    p.add_argument("tree", nargs="?", help="dump file or gwt-apply snapshot; without it the live tree is read")
    p.add_argument("--since", help="earlier dump, gwt-apply snapshot or .gramps backup: diff, and new objects are the scope")
    p.add_argument("--tag", help="scope is the objects carrying this tag (a run tag)")
    p.add_argument("--root", help="Gramps ID of the Ahnentafel root; with --since, numbers lost or changed fail")
    p.add_argument("--known", help="earlier --json output (or list of {check, key}) to accept as known")
    p.add_argument("--baseline", help="earlier dump: its own findings count as known, so only what is new since then fails")
    p.add_argument("--ignore-check", action="append", default=[], metavar="NAME",
                   help="leave a whole check out of the exit code (repeatable); the report still counts it")
    p.add_argument("--json", help="write the full report as JSON to this file (- for stdout)")
    p.add_argument("--save-dump", help="keep the live dump in this file")
    p.add_argument("--limit", type=int, default=30, help="findings printed per check (default 30)")
    live_opts(p)

    p = sub.add_parser("mark-known", help="accept findings of a report as known, with the reason, in a known file")
    p.add_argument("report", help="a --json report")
    p.add_argument("--check", required=True, help="the check the findings belong to")
    p.add_argument("--key", action="append", required=True, help="the finding key (repeatable)")
    p.add_argument("--reason", required=True, help="why this is not a problem, e.g. father and son, same name")
    p.add_argument("--out", required=True, help="the known file to create or add to")
    p = sub.add_parser("repos", help="find repositories by name or type; list unlinked sources and repositories")
    p.add_argument("tree", nargs="?", help="dump file; without it the live tree is read")
    p.add_argument("--name", help="part of a repository's name (compared like people's names)")
    p.add_argument("--type", dest="rtype", help="repository type, e.g. Archive")
    p.add_argument("--unlinked", action="store_true", help="also list sources with no repository and repositories no source lists")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    p.add_argument("--limit", type=int, default=30, help="sources listed with --unlinked (default 30)")
    live_opts(p)

    p = sub.add_parser("fill-places", help="for each event with no place, the place its own citations name, with the quote; proposals only")
    p.add_argument("tree", nargs="?", help="dump file; without it the live tree is read")
    p.add_argument("--ops", metavar="FILE", help="also write the proposals to a new file as gwt-apply update_event ops with an empty ruling")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    p.add_argument("--limit", type=int, default=30, help="proposals printed (default 30)")
    live_opts(p)

    sub.add_parser("selftest", help="plant every error kind in a synthetic tree and prove each check fires")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "selftest":
            from .selftest import selftest
            return selftest(verbose=True)
        if args.cmd == "mark-known":
            return mark_known(args.report, args.check, args.key, args.reason, args.out)
        if args.cmd == "dump":
            save(live_dump(args), args.out)
            return 0
        if args.cmd == "fill-places":
            from .fill import print_places, propose_places, write_ops
            tree = load(args.tree) if args.tree else Tree(live_dump(args)["objects"], source="live tree")
            if tree.partial:
                raise BadInput(f"{tree.source}: a .gramps backup has no citation text; use a dump")
            rep = propose_places(tree)
            if args.ops:
                try:
                    n = write_ops(rep, args.ops)
                except OSError as exc:
                    raise BadInput(f"{args.ops}: {exc} (an existing file is never replaced)") from exc
                print(f"{n} ops written to {args.ops} with an empty ruling; nothing was applied", file=sys.stderr)
            if args.json:
                print(json.dumps(rep, ensure_ascii=False, indent=1))
            else:
                print_places(rep, args.limit)
            return 0
        if args.cmd == "repos":
            from .repos import print_repository_report, repository_report
            tree = load(args.tree) if args.tree else Tree(live_dump(args)["objects"], source="live tree")
            if tree.partial:
                raise BadInput(f"{tree.source}: a .gramps backup has no repository details; use a dump")
            rep = repository_report(tree, name=args.name, rtype=args.rtype)
            if args.json:
                json.dump(rep, sys.stdout, ensure_ascii=False, indent=1)
                print()
            else:
                print_repository_report(rep, unlinked=args.unlinked, limit=args.limit)
            return 0
        check_names(set(args.ignore_check))  # before a dump that can take minutes
        t0 = time.monotonic()
        known = load_known(args.known) if args.known else None
        if args.baseline:
            base = load(args.baseline)
            if base.partial:
                raise BadInput(f"{args.baseline}: a .gramps backup has no objects to check; --baseline needs a dump")
            old = check(base)["findings"]
            known = {**{(f["check"], f["key"]): "" for f in old}, **(known or {})}
        since = load(args.since) if args.since else None
        run = lambda tree: check(tree, since=since, tag=args.tag, root=args.root, known=known,  # noqa: E731
                                 ignore=set(args.ignore_check))
        if args.tree:
            tree = load(args.tree)
        else:
            # While something writes, a pass can catch it halfway: a note made and not yet attached is an orphan for
            # a moment. A pass reads the types one after another, so one such write can show in two passes in a
            # row, never in three. Confirmed is what three passes in a row found, and the read is settled once
            # that stops changing. What only the last two passes found may be a write caught halfway or a real
            # problem made late in the read: it is reported, marked unconfirmed. What only one pass found is not.
            seen: list[set] = []
            persistent: list[set] = []

            def settled(data):
                # info findings never fail the run, so a satellite that comes and goes mid-write must not keep it reading
                seen.append({(f["check"], f["key"]) for f in run(Tree(data["objects"]))["findings"] if not f["info"]})
                if len(seen) >= SEEN_IN:
                    persistent.append(set.intersection(*seen[-SEEN_IN:]))
                return len(persistent) >= 2 and persistent[-1] == persistent[-2]

            data = live_dump(args, settled)
            if args.save_dump:
                save(data, args.save_dump)
            tree = Tree(data["objects"], source="live tree")
        t1 = time.monotonic()
        report = run(tree)
        if not args.tree and "passes" in data:
            report["passes"] = data["passes"]
            if data.get("settled"):
                unconfirmed = (seen[-1] & seen[-2]) - seen[-3]
                keep = lambda fs: [f for f in fs if f["info"]  # noqa: E731
                                   or (f["check"], f["key"]) in persistent[-1] | unconfirmed]
                report["in_flight"] = len(report["findings"]) - len(keep(report["findings"]))
                report["findings"], report["new"] = keep(report["findings"]), keep(report["new"])
                for f in report["findings"]:
                    if (f["check"], f["key"]) in unconfirmed:
                        f["unconfirmed"] = True
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


if __name__ == "__main__":
    sys.exit(main())
