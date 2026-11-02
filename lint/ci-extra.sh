#!/usr/bin/env bash
# runs after the pytest suite in CI: every rule must catch its own mutant of the example list
set -euo pipefail
./gwt-lint --prove ../examples/changes.jsonl --snapshot ../examples/tree.json.gz
