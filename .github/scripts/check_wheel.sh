#!/usr/bin/env bash
# Builds the wheel, installs it into a clean venv with the checkout off sys.path, and runs every tool
# from it: `gwt --version`, `gwt <tool> --help` and `gwt-<tool> --help` for each, and `gwt check selftest`.
# A tool file missing from the wheel fails here, not on a user's machine.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
python3 -m pip wheel --no-deps -q -w "$work/dist" "$root"
python3 -m venv "$work/venv"
"$work/venv/bin/pip" install -q "$work"/dist/*.whl
cd "$work"
bin="$work/venv/bin"
"$bin/gwt" --version
tools=$("$bin/python" - "$work/venv" <<'PY'
import importlib, os, sys
from gwt_core.cli import TOOLS
import gwt_reader
# gwt_mh_browser loads only on a real `gwt mh` run (not on --help), so it is listed by name here, and
# gwt_reader (the shape gwt-mh and gwt-read share) with every gwt-read source module its registry names
venv = os.path.realpath(sys.argv[1])   # macOS: mktemp gives /var/..., Python reports /private/var/...
for mod in [m for m, _ in TOOLS.values()] + ["gwt_mh", "gwt_mh_browser", "gwt_reader"] + list(gwt_reader.SOURCE_MODULES):
    f = os.path.realpath(importlib.import_module(mod).__file__)
    if not f.startswith(venv + os.sep):
        sys.exit(f"{mod} loaded from {f}, not from the installed wheel")
print(" ".join(TOOLS))
PY
)
sources=$("$bin/python" -c 'import gwt_read; print(" ".join(gwt_read.sources()))')
for t in $tools; do
  "$bin/gwt" "$t" --help >/dev/null
  "$bin/gwt-$t" --help >/dev/null
  echo "ok   gwt $t --help, gwt-$t --help"
done
for s in $sources; do
  "$bin/gwt" read "$s" --help >/dev/null
  echo "ok   gwt read $s --help"
done
"$bin/gwt" check selftest
