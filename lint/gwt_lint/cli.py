"""gwt-lint command line."""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from . import framework as F

EXIT = {0: "clean", 1: "findings", 2: "a rule had nothing to check"}


def _ids(values):
    return [i.strip() for v in values or [] for i in v.split(",") if i.strip()]


def load_config(path) -> dict:
    """lint.toml; folder settings are read relative to the file."""
    if not path:
        return {}
    try:
        with open(path, "rb") as fh:
            cfg = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise F.InputError(f"{path}: {e}") from None
    base = Path(path).resolve().parent
    for key in ("open", "applied"):
        v = (cfg.get("overlap") or {}).get(key)
        if isinstance(v, str):
            cfg["overlap"][key] = str(base / v)
    return cfg


def build_parser():
    ap = argparse.ArgumentParser(
        prog="gwt-lint",
        description="Check a gwt-apply change list against a tree snapshot before it is applied. "
                    "Exit 0: clean; 1: findings; 2: a rule had nothing to check (2 wins over 1).")
    ap.add_argument("list", nargs="?", help="the change list (JSON Lines)")
    ap.add_argument("--snapshot", help="a `gwt-check dump` or a gwt-apply snapshot (.json or .json.gz)")
    ap.add_argument("--config", help="lint.toml: which rules run and their settings")
    ap.add_argument("--rules", help="comma-separated rules to run (default: [rules] in the config, else all)")
    ap.add_argument("--records", help="a folder of saved records, for rules that compare a list with them")
    ap.add_argument("--root", action="append", help="scope: a root person's Gramps ID (repeat or comma-separate)")
    ap.add_argument("--protect", action="append", help="protect: a protected person's Gramps ID (repeat or comma)")
    ap.add_argument("--open", metavar="DIR", help="overlap: the folder of open lists to compare with")
    ap.add_argument("--applied", metavar="DIR", help="overlap: the folder of applied lists")
    ap.add_argument("--weak-tag", help="weak: the tag a weak claim must carry")
    ap.add_argument("--prove", action="store_true",
                    help="run every rule on its own mutant of the list; exit 1 unless each fires its own code")
    ap.add_argument("--json", action="store_true", help="one JSON object on stdout")
    ap.add_argument("-v", "--verbose", action="store_true", help="also print each rule's notes (scope paths)")
    ap.add_argument("--list-rules", action="store_true", help="print every rule and its finding codes, then exit")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    rules = F.registry()
    if args.list_rules:
        for r in rules.values():
            print(r.name)
            for code, what in r.codes.items():
                print(f"  {code:<18} {what}")
        return 0
    if not args.list or not args.snapshot:
        print("gwt-lint: give a LIST and --snapshot DUMP", file=sys.stderr)
        return 2
    try:
        cfg = load_config(args.config)
        for key, sec, field in (("root", "scope", "roots"), ("protect", "protect", "ids")):
            if getattr(args, key):
                cfg.setdefault(sec, {})[field] = _ids(getattr(args, key))
        for key, sec, field in (("open", "overlap", "open"), ("applied", "overlap", "applied"),
                                ("weak_tag", "weak", "tag")):
            if getattr(args, key):
                cfg.setdefault(sec, {})[field] = getattr(args, key)
        names = _ids([args.rules]) if args.rules else cfg.get("rules") or list(rules)
        unknown = [n for n in names if n not in rules]
        if unknown:
            print(f"gwt-lint: no rule {', '.join(unknown)}; rules: {', '.join(rules)}", file=sys.stderr)
            return 2
        ops = F.read_list(args.list)
        tree = F.Tree.load(args.snapshot)
    except F.InputError as e:
        print(f"gwt-lint: {e}", file=sys.stderr)
        return 2
    if not ops:
        print(f"gwt-lint: {args.list} has no ops", file=sys.stderr)
        return 2
    ctx = F.Context(ops, tree, cfg, path=args.list, records=args.records)
    if args.prove:
        return _prove(ctx, [rules[n] for n in names], args.json)
    results = []
    for n in names:
        try:
            results.append(F.run_rule(rules[n], ctx))
        except F.InputError as e:
            results.append(F.Result(n, "nothing", reason=str(e)))
    code = F.exit_code(results)
    if args.json:
        print(json.dumps({"list": args.list, "exit": code, "rules": [r.as_dict() for r in results]},
                         ensure_ascii=False, indent=1))
        return code
    for r in results:
        if r.status == "nothing":
            print(f"{r.rule}: nothing to check: {r.reason}")
            continue
        print(f"{r.rule}: {len(r.findings)} finding{'s' * (len(r.findings) != 1)}" if r.findings else f"{r.rule}: ok")
        for f in r.findings:
            where = f"op {f.key!r} line {f.line}" if f.line else "config"
            print(f"  {f.code:<18} {where}: {f.message}" + (f" (fix: {f.fix})" if f.fix else ""))
        if args.verbose:
            for line in r.info:
                print(f"  . {line}")
    n = sum(len(r.findings) for r in results)
    print(f"exit {code}: {EXIT[code]} ({n} finding{'s' * (n != 1)} from {len(results)} rules)")
    return code


def _prove(ctx, rules, as_json):
    lines, ok = [], True
    for r in rules:
        try:
            passed, line = F.prove(r, ctx)
        except F.InputError as e:
            passed, line = False, f"{r.name}: {e}"
        ok &= passed
        lines.append({"rule": r.name, "proved": passed, "line": line})
    if as_json:
        print(json.dumps({"list": ctx.path, "exit": 0 if ok else 1, "rules": lines}, ensure_ascii=False, indent=1))
    else:
        for x in lines:
            print(f"{'proved' if x['proved'] else 'FAILED'} {x['line']}")
        print(f"exit {0 if ok else 1}: " + ("every rule fired its own code on its mutant" if ok
                                            else "a rule did not catch its own mutant"))
    return 0 if ok else 1
