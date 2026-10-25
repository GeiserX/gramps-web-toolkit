#!/usr/bin/env bash
# The offline demo: an invented 30-person tree, no server. Run it from anywhere once the toolkit is installed.
# Each `expect` line is one command of the README quick start, with the exit code it must end with; the last
# step fails when the two lists differ, so the README cannot drift from what this script proves.
set -euo pipefail
cd "$(dirname "$0")"
self=$(basename "$0")
out=$(mktemp)
trap 'rm -f "$out"' EXIT

expect() {
  local want=$1 got=0
  shift
  printf '\n$ %s\n' "$*"
  "$@" >"$out" 2>&1 || got=$?
  cat "$out"
  if [ "$got" != "$want" ]; then
    printf 'demo: expected exit %s, got %s\n' "$want" "$got" >&2
    exit 1
  fi
}

# >>> quick start (the same commands, in the same order, as the README block)
expect 0 gwt check selftest
expect 1 gwt apply changes.jsonl --snapshot tree.json.gz --log /tmp/gwt-demo.jsonl
grep -q 'looks like I0008 (Tomás Ortega Sanz, 1903) in the tree' "$out" \
  || { echo 'demo: the refusal does not name the person already in the tree' >&2; exit 1; }
expect 0 gwt apply changes-fixed.jsonl --snapshot tree.json.gz --log /tmp/gwt-demo.jsonl
expect 0 gwt check check tree.json.gz
expect 0 gwt leads --root I0001 --limit 10 tree.json.gz
# <<< quick start

if ls gwt-apply-*.jsonl >/dev/null 2>&1; then
  echo 'demo: a gwt-apply log was left in examples/' >&2
  exit 1
fi

# the README's quick-start block must run exactly these commands
mine=$(sed -n '/^# >>> quick start/,/^# <<< quick start/s/^expect [0-9] //p' "$self")
readme=$(sed -n '/<!-- quick-start:begin -->/,/<!-- quick-start:end -->/p' ../README.md \
  | grep '^gwt ' | sed -e 's/ *#.*$//')
if [ "$mine" != "$readme" ]; then
  echo 'demo: the README quick start and demo.sh run different commands:' >&2
  diff <(printf '%s\n' "$readme") <(printf '%s\n' "$mine") >&2 || true
  exit 1
fi
printf '\ndemo: every step ended as expected, and the README quick start runs these commands\n'
