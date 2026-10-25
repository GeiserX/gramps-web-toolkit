# AGENTS.md: gramps-web-toolkit

Instructions for coding agents working in this repository. `CLAUDE.md` imports this file.

## What this is

Command-line tools for running AI research agents against a Gramps Web family tree. Each folder is one
tool with its own tests; `docs/` has one page per tool, plus `pipeline.md` (how the pieces fit) and
`hard-won.md` (the failures behind them). `docs/` is also the site at https://geiserx.github.io/gramps-web-toolkit/: a new
page goes into the `mkdocs.yml` nav and `docs/index.md`, and `mkdocs build --strict` must pass. The toolkit is for any country; Spain-specific code says so in its name
(`spanish-poller`) or in its docs (the Spanish sources in `gwt-fetch`).

| Folder | Tool | Runtime |
|---|---|---|
| `apply/` | `gwt-apply`, change-list applier | Python 3.11+, `requests` |
| `check/` | `gwt-check`, read-only whole-tree checks | Python 3.11+, standard library |
| `fetch/` | `gwt-fetch`, archive downloads | Python 3.11+, `requests` |
| `ledger/` | `gwt-ledger`, shared search ledger | Python standard library |
| `fscache/` | `gwt-fscache`, HTTP cache | Python standard library |
| `gate/` | `gwt-gate`, shared request pace | Python standard library |
| `rank/` | `gwt-rank`, orders search results | Python standard library |
| `myheritage/` | `gwt-mh`, MyHeritage through a signed-in browser | Python 3.11+, `playwright` (tests: standard library) |
| `rawstore/` | `gwt-rawstore`, images in S3, manifest in git | Python standard library |
| `importxl/` | `gwt-import-excel`, workbook to change list | Python standard library (its end-to-end test needs `requests`) |
| `queue/` | `gwt-queue`, search tasks handed out to lanes | Python standard library |
| `leads/` | `gwt-leads`, tree gaps ranked as queue tasks | Python standard library |
| `spanish-poller/` | `gwt-spanish-poller`, Spain-only sede and DEHú watcher | Node 18+, Playwright |
| `ocr-bench/` | OCR, HTR and LLM reading benchmark | Python, one venv per engine |

## Repository rules

- Licence: GPL-3.0-or-later. Commits follow Conventional Commits.
- Every change reaches `main` through a pull request with green CI. Never force push.
- No AI attribution anywhere: no `Co-Authored-By` trailers, no "Generated with" lines.
- **No private data, ever.** Examples and test data are invented: no real people's names from anyone's
  tree, no real case or expediente numbers, no real tree ids, hostnames, IPs, accounts or emails.
  Public records (a Gaceta page, a published parish entry) are fine with their source.
- Never commit a secret, a certificate or a config file with credentials.
- `README.md` is a short overview: one plain line per tool, with no options, plan or tier differences, limits or deep links
  into the docs. Every detail lives on the tool's page in `docs/` (the site).

## Test

```bash
cd apply && python3 -m pytest -q
cd check && python3 -m pytest -q
cd fetch && python3 -m pytest -q
cd ledger && python3 -m unittest test_gwt_ledger
cd fscache && python3 -m unittest test_gwt_fscache
cd gate && python3 -m unittest test_gwt_gate
cd rank && python3 -m unittest test_gwt_rank
cd myheritage && python3 -m unittest test_gwt_mh
cd rawstore && python3 -m unittest test_gwt_rawstore
cd importxl && python3 -m unittest test_gwt_import_excel test_end_to_end
cd queue && python3 -m unittest test_gwt_queue
cd leads && python3 -m unittest test_gwt_leads
cd spanish-poller && node --test
```

## Beads Issue Tracker

`bd` (Beads) is the maintainer's local task tracker. Its data is not part of this repository. Outside contributors and
their agents use GitHub issues and follow `CONTRIBUTING.md`; ignore `bd` unless you are the maintainer.
