# Development

Each folder is one tool with its own tests. A change touches one folder, its page under `docs/`, and its line in the README when the tool's one-line summary changes.

## Run the tests

```sh
python3 -m pip install requests pytest
python3 .github/scripts/run_tests.py              # every package
python3 .github/scripts/run_tests.py --only apply # one package (comma-separate several)
(cd spanish-poller && node --test)
```

`run_tests.py` finds every top-level folder with Python tests (a `test_*.py` file or a `tests/` folder), runs `python -m pytest -q` inside it, then the folder's `ci-extra.sh` when there is one (`check/ci-extra.sh` runs the `gwt-check` self-test), and prints one `ok` or `FAIL` line per package. A new tool needs no CI edit: give its folder tests and CI runs them. A folder that ships a tool (a `gwt-*` wrapper or a `pyproject.toml`) with no tests fails the run, unless `UNTESTED` in the script names it with the reason.

CI runs the same script on every pull request ([ci.yml](https://github.com/GeiserX/gramps-web-toolkit/blob/main/.github/workflows/ci.yml)), and `node --test` for `spanish-poller` in its own job.

No test needs a Gramps Web server or a FamilySearch or MyHeritage account. The `apply` tests run against an in-process fake of the Gramps Web API, and the `gwt-mh` tests read invented fixture pages.

## Mutants: prove a test can fail

A test that passes whether or not its guard is there protects nothing. `<pkg>/mutants.txt` lists guards with the mutation that removes each one:

```text
gwt_apply/ops.py | if name == "merge_person" and set(op.get("use_loser") or []) - set(USE_LOSER): | if False: | validate refuses an unknown use_loser vital
```

The four fields are the file (relative to the package), the exact text to replace (it must occur exactly once), the replacement, and what the guard protects. `python3 .github/scripts/run_tests.py --mutants [--only PKG]` copies the package to a scratch folder once per row, applies the replacement, and runs the package's tests there. The run fails when a mutant survives (the tests still pass), or when a row's text is not found.

When you add a guard, add its row and a test that fails with the row applied. CI runs the mutants of every package a pull request touches, and all of them on a push to `main` or when a pull request changes `.github/`.

## Build this site

```sh
python3 -m pip install -r docs/requirements-docs.txt
mkdocs serve            # http://127.0.0.1:8000, rebuilds on save
mkdocs build --strict   # the build CI runs
```

The build is strict: a broken link, a link to a missing heading, a page left out of the nav, or a link that leaves `docs/` fails it. Link a file outside `docs/` by its GitHub URL. MkDocs logs a link to an excluded file (a capitalised `.md`) only as `INFO`, which `--strict` lets through, so CI also fails the build when the log says `which is excluded from the built site`. A push to `main` that changes the docs publishes the site.

## Rules for a change

- Every change reaches `main` through a pull request with green CI. Commits follow [Conventional Commits](https://www.conventionalcommits.org).
- No private data. Examples and test data are invented: no real people's names from anyone's tree, no real case numbers, tree ids, hostnames, IP addresses, accounts or email addresses. Public records, such as a Gaceta page or a published parish entry, are fine with their source.
- Never commit a secret, a certificate or a configuration file with credentials.
- A new trap goes into [Hard-won lessons](hard-won.md): what happened, why, and the rule.

Security problems go through the [security policy](https://github.com/GeiserX/gramps-web-toolkit/blob/main/SECURITY.md), never a public issue. The toolkit is licensed [GPL-3.0-or-later](https://github.com/GeiserX/gramps-web-toolkit/blob/main/LICENSE).
