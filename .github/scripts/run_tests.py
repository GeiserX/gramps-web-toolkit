#!/usr/bin/env python3
"""Run every package's tests, or check that its tests catch the mutations it lists.

A package is a top-level folder with Python tests (a test_*.py file or a tests/ folder
anywhere below it). For each one this runs `python -m pytest -q` inside the folder, then
`<pkg>/ci-extra.sh` when it exists, and prints one ok/FAIL line. A folder that ships a
tool (a `gwt-*` wrapper or a pyproject.toml) and has no Python tests fails, unless
UNTESTED names it with the reason.

With --mutants, every row of `<pkg>/mutants.txt` is applied to a scratch copy of the
package and its tests run there; a mutant the tests still pass "survived" and fails the
run. A row is `file | exact text | replacement | why`; the text must occur exactly once.

Usage:
  python3 .github/scripts/run_tests.py [--only PKG[,PKG...]]
  python3 .github/scripts/run_tests.py --mutants [--only PKG[,PKG...]]

Standard library only.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# folders that ship a tool but have no Python tests, and why that is acceptable
UNTESTED = {
    "ocr-bench": "a benchmark harness run by hand against sample scans; it has no pass/fail to test",
    "spanish-poller": "a Node tool; `node --test` runs in its own CI job",
}

PYTEST = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]


def _skip(p):
    return any(part in ("node_modules", "__pycache__", ".pytest_cache") for part in p.parts)


def has_tests(pkg):
    for p in pkg.rglob("*"):
        if _skip(p):
            continue
        if p.is_file() and re.fullmatch(r"test_.*\.py", p.name):
            return True
        if p.is_dir() and p.name == "tests" and any(p.glob("*.py")):
            return True
    return False


def ships_tool(pkg):
    return (pkg / "pyproject.toml").is_file() or any(p.is_file() for p in pkg.glob("gwt-*"))


def scan():
    """(tested packages, error lines) over the top-level folders."""
    tested, errors = [], []
    for d in sorted(ROOT.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if has_tests(d):
            if d.name in UNTESTED:
                errors.append(f"FAIL {d.name}: has tests now; remove it from UNTESTED in {Path(__file__).name}")
            tested.append(d.name)
        elif ships_tool(d) and d.name not in UNTESTED:
            errors.append(f"FAIL {d.name}: ships a tool but has no tests; add tests, "
                          f"or list it in UNTESTED in {Path(__file__).name} with the reason")
    for name in sorted(set(UNTESTED) - {d.name for d in ROOT.iterdir() if d.is_dir()}):
        errors.append(f"FAIL {name}: listed in UNTESTED but the folder is gone; remove the entry")
    return tested, errors


def summary(out):
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    return lines[-1].strip("= ") if lines else "(no output)"


def run_tests(pkgs):
    failed = 0
    for name in pkgs:
        pkg = ROOT / name
        r = subprocess.run(PYTEST, cwd=pkg, capture_output=True, text=True)
        out, ok, what = r.stdout + r.stderr, r.returncode == 0, summary(r.stdout + r.stderr)
        extra = pkg / "ci-extra.sh"
        if ok and extra.is_file():
            e = subprocess.run(["bash", extra.name], cwd=pkg, capture_output=True, text=True)
            out += e.stdout + e.stderr
            ok = e.returncode == 0
            what += ", ci-extra.sh " + ("ok" if ok else f"exit {e.returncode}")
        print(f"{'ok  ' if ok else 'FAIL'} {name:<16} {what}", flush=True)
        if not ok:
            failed += 1
            print(out.rstrip() + "\n", flush=True)
    return failed


def read_mutants(path):
    rows = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [p.strip() for p in line.split(" | ", 3)]
        if len(parts) != 4 or not all(parts[:2]) or not parts[3]:
            raise ValueError(f"{path}:{n}: want `file | exact text | replacement | why`")
        rows.append((n, *parts))
    return rows


def run_mutants(pkgs):
    total = killed = 0
    problems = []
    for name in pkgs:
        listing = ROOT / name / "mutants.txt"
        if not listing.is_file():
            continue
        try:
            rows = read_mutants(listing)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        for n, file, text, repl, why in rows:
            where = f"{name}/mutants.txt:{n} ({file})"
            with tempfile.TemporaryDirectory() as tmp:
                copy = Path(tmp) / name
                shutil.copytree(ROOT / name, copy, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
                target = copy / file
                src = target.read_text() if target.is_file() else None
                hits = src.count(text) if src is not None else 0
                if hits != 1:
                    problems.append(f"{where}: the text occurs {hits} times in {file}, want exactly once: {text}")
                    continue
                target.write_text(src.replace(text, repl))
                r = subprocess.run(PYTEST + ["-x"], cwd=copy, capture_output=True, text=True,
                                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            total += 1
            if r.returncode == 0:
                print(f"SURVIVED {where}: {why}", flush=True)
            elif r.returncode == 1:
                killed += 1
                print(f"killed   {where}", flush=True)
            else:
                problems.append(f"{where}: pytest exit {r.returncode}, not a test failure:\n{summary(r.stdout + r.stderr)}")
    for p in problems:
        print(f"FAIL {p}", flush=True)
    print(f"{total} mutants, {killed} killed")
    return (total - killed) + len(problems)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", action="append", default=[], metavar="PKG",
                    help="run only these packages (comma-separated or repeated)")
    ap.add_argument("--mutants", action="store_true", help="apply each <pkg>/mutants.txt row and fail if tests still pass")
    args = ap.parse_args(argv)
    tested, errors = scan()
    if not tested:
        print("FAIL: found no tested packages; the scan itself is broken")
        return 1
    only = [p for arg in args.only for p in arg.split(",") if p.strip()]
    if args.mutants:
        pkgs = [p for p in tested if not only or p in only]
        return 1 if run_mutants(pkgs) else 0
    unknown = sorted(set(only) - set(tested))
    if unknown:
        print(f"FAIL: no tested package named {', '.join(unknown)}; packages: {', '.join(tested)}")
        return 1
    for e in errors:
        print(e)
    failed = run_tests([p for p in tested if not only or p in only])
    return 1 if failed or errors else 0


if __name__ == "__main__":
    sys.exit(main())
