#!/usr/bin/env python3
"""Fail when a top-level package has tests that CI never runs.

A package is a top-level directory holding a test_*.py or *.test.js file,
or a tests/ or test/ directory, anywhere below it. CI runs it when some
non-comment line of the workflow cds into it. Standard library only.

Usage: python3 .github/scripts/ci_covers_every_package.py [repo root]
"""
import re
import sys
from pathlib import Path

WORKFLOW = Path(".github/workflows/ci.yml")


def has_tests(pkg):
    for p in pkg.rglob("*"):
        if "node_modules" in p.parts:
            continue
        if p.is_dir() and p.name in ("tests", "test"):
            return True
        if p.is_file() and (re.fullmatch(r"test_.*\.py", p.name) or p.name.endswith(".test.js")):
            return True
    return False


def main(root):
    packages = sorted(d.name for d in root.iterdir()
                      if d.is_dir() and not d.name.startswith(".") and has_tests(d))
    if not packages:
        print("FAIL: found no tested packages; the scan itself is broken")
        return 1
    lines = [ln for ln in (root / WORKFLOW).read_text().splitlines()
             if not ln.lstrip().startswith("#")]
    text = "\n".join(lines)
    missing = [p for p in packages
               if not re.search(r"\bcd\s+" + re.escape(p) + r"(?=\s|&&|;|$)", text, re.M)]
    for p in packages:
        print(("MISSING " if p in missing else "ok      ") + p)
    if missing:
        print(f"FAIL: {WORKFLOW} has no step that cds into: {', '.join(missing)}")
        return 1
    print(f"all {len(packages)} tested packages run in CI")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()))
