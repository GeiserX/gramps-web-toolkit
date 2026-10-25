# Offline demo

Everything here is invented: the people, the places, the parish books and the tree id. Nothing needs a Gramps Web server.

| File | What it is |
|---|---|
| `tree.json.gz` | A `gwt-apply` snapshot of a 30-person tree: three generations around Lucía Ortega Sanz (`I0001`), built on the tree `gwt check selftest` plants its errors in |
| `changes.jsonl` | Nine ops that add a wife and a son for Tomás Ortega Sanz, with their marriage, its source, a citation and a note. It also adds Tomás again as a new person, although the tree already has him as `I0008`. That is the mistake the dry run must refuse |
| `changes-fixed.jsonl` | The same list with that op gone and `I0008` used instead |
| `demo.sh` | Runs the README quick start and checks every exit code |
| `make_example.py` | Writes `tree.json.gz` again. You never need to: the file is committed |

Run it with the toolkit installed (`pipx install .` at the root of the clone):

```sh
./demo.sh
```

It runs five commands and stops at the first one that ends with an exit code other than the one below:

1. `gwt check selftest` exits 0: every planted error is found.
2. `gwt apply changes.jsonl --snapshot tree.json.gz` exits 1 and prints `op 'p-tomas': key:p-tomas (Tomás Ortega Sanz) looks like I0008 (Tomás Ortega Sanz, 1903) in the tree (same name)`. Nothing is planned past that point, so no op runs.
3. `gwt apply changes-fixed.jsonl --snapshot tree.json.gz` exits 0 and prints the plan: eight `done` ops and what each would create or change.
4. `gwt check check tree.json.gz` exits 0 with no findings.
5. `gwt leads --root I0001 --limit 10 tree.json.gz` exits 0 and lists the gaps, Lucía's direct ancestors first. It holds back the nine people born after 1926 with no death, who may be living.

The two `gwt apply` runs pass `--log /tmp/gwt-demo.jsonl`, so their call logs go to `/tmp` and not into this folder. Without `--apply` they write nothing anywhere else.

Then `demo.sh` checks that the README's quick-start block runs the same `gwt` commands in the same order. CI runs `demo.sh` in a fresh virtualenv on every pull request, so a README line that stops working fails the build.

When a new person really is someone else with the same name, the op says so: give the `p-tomas` op in `changes.jsonl` `"allow_namesake": true`, a `namesake_reason` and `"namesake_of": ["I0008"]`, and the dry run plans it instead of refusing.
