# Development

Each folder is one tool with its own tests. A change touches one folder, its page under `docs/`, and its line in the README when the tool's one-line summary changes.

## Run the tests

```sh
python3 -m pip install requests pytest
(cd apply && python3 -m pytest -q)
(cd check && python3 -m pytest -q && python3 -m gwt_check selftest)
(cd fetch && python3 -m pytest -q)
(cd ledger && python3 -m unittest test_gwt_ledger)
(cd fscache && python3 -m unittest test_gwt_fscache)
(cd gate && python3 -m unittest test_gwt_gate)
(cd rank && python3 -m unittest test_gwt_rank)
(cd myheritage && python3 -m unittest test_gwt_mh)
(cd rawstore && python3 -m unittest test_gwt_rawstore)
(cd importxl && python3 -m unittest test_gwt_import_excel test_end_to_end)
(cd queue && python3 -m unittest test_gwt_queue)
(cd leads && python3 -m unittest test_gwt_leads)
(cd spanish-poller && node --test)
```

CI runs the same commands on every pull request ([ci.yml](https://github.com/GeiserX/gramps-web-toolkit/blob/main/.github/workflows/ci.yml)). Its first step fails when a folder has tests that CI does not run, so a new tool needs its own CI step.

No test needs a Gramps Web server or a FamilySearch or MyHeritage account. The `apply` tests run against an in-process fake of the Gramps Web API, and the `gwt-mh` tests read invented fixture pages.

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
