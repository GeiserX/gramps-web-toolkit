#!/usr/bin/env bash
# runs after the pytest suite in CI: the self-test plants every error kind and checks each is found
set -euo pipefail
python3 -m gwt_check selftest
