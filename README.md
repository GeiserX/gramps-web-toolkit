<p align="center">
  <img src="docs/images/banner.svg" alt="gramps-web-toolkit banner" width="100%">
</p>

<h1 align="center">gramps-web-toolkit</h1>

<p align="center">
  <a href="https://github.com/GeiserX/gramps-web-toolkit/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/GeiserX/gramps-web-toolkit/ci.yml?style=flat-square&label=CI" alt="CI"></a>
  <a href="https://geiserx.github.io/gramps-web-toolkit/"><img src="https://img.shields.io/badge/docs-site-e8b04a?style=flat-square" alt="Docs"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/GeiserX/gramps-web-toolkit?style=flat-square" alt="License"></a>
</p>

gramps-web-toolkit is a set of command-line tools for running AI research agents against a family tree in [Gramps Web](https://www.grampsweb.org) without letting them write guesses into it. Each tool fixes a failure we hit while running about 30 research rounds over two trees of roughly 10,000 people each. The [pipeline design](docs/pipeline.md) and [every trap it taught us](docs/hard-won.md) sit next to the code.

The toolkit works for genealogy in any country. One module, `spanish-poller`, is Spain-only: it watches the Spanish Ministry of Justice sede and the DEHú notification inbox. In `gwt-fetch`, `iiif` works with any IIIF archive, while `digibib`, `gazeta` and `geo` cover Spanish sources and `europeana` searches every provider in any country (`--provider Hispana` narrows it to Spain).

## Features

- `gwt-apply`: applies a JSON Lines change list to a tree, reads before every write, skips what is already there, and stays dry-run until you pass `--apply`
- `gwt-check`: reads the whole tree after an apply and reports orphans, dangling links, broken birth and death pointers, likely duplicates, dates that contradict each other and what changed since the last copy, with a self-test that plants every error kind
- `gwt-import-excel`: turns a research workbook into a gwt-apply change list for review
- `gwt-fetch`: downloads archive images and text without a browser, with one manifest row per file: any IIIF archive, Europeana search, plus Spanish sources (DIGIBIB, the historical Gaceta de Madrid, CartoCiudad geocoding)
- `gwt-ledger`: a shared "already searched" ledger, so parallel agents stop repeating each other's negative searches
- `gwt-queue`: a durable "what to search next" queue that hands prioritized tasks to lanes, reopens the claims of dead lanes and backs off the tasks that keep failing
- `gwt-leads`: reads a tree dump offline and lists the gaps (lines that stop, missing vitals, uncited facts), ranked with the root's direct ancestors first, as `gwt-queue` tasks
- `gwt-fscache`: an on-disk HTTP cache for rate-limited genealogy APIs that never stores a block or an auth header
- `gwt-rank`: orders record-search results by how well they fit a person in the tree, from the cached FamilySearch JSON or from page text, and never hides a row
- `gwt-mh`: MyHeritage record search, record pages and originals through a signed-in browser
- `gwt-read`: record search and records on FamilySearch and PARES, index search on Genae, and a name in the BOE and Gaceta de Madrid, slow and cached so the account is not suspended
- `gwt-gate`: one request pace shared by every agent on a machine, which backs off on a block and lets priority lanes go first
- `gwt-rawstore`: keeps source images in an S3-compatible bucket and only a small manifest in git
- `gwt-spanish-poller` (Spain only): watches Spanish civil-registry requests and the DEHú inbox read-only, and flags cases that vanish
- `ocr-bench`: scores OCR, HTR and LLM readings of the same pages on how many names they recover

## Quick start

Watch the toolkit refuse a duplicate in two minutes, with no server. The tree in `examples/` is a snapshot of 30 invented people, and `changes.jsonl` adds a wife and a son for Tomás Ortega Sanz, but adds Tomás again as a new person although he is already in the tree.

<!-- quick-start:begin -->
```bash
git clone https://github.com/GeiserX/gramps-web-toolkit
cd gramps-web-toolkit
pipx install .                    # Python 3.11+, macOS or Linux; every Python tool, needs only requests
cd examples
gwt check selftest                # plants every kind of error in a made-up tree and finds each one
gwt apply changes.jsonl --snapshot tree.json.gz --log /tmp/gwt-demo.jsonl         # refused: Tomás is already I0008
gwt apply changes-fixed.jsonl --snapshot tree.json.gz --log /tmp/gwt-demo.jsonl   # uses I0008: the plan goes through
gwt check check tree.json.gz      # the whole tree, offline
gwt leads --root I0001 --limit 10 tree.json.gz   # the gaps to research next, Lucía's ancestors first
```
<!-- quick-start:end -->

`./demo.sh` in `examples/` runs the same commands and checks each exit code. Nothing above writes to a tree: without `--apply`, `gwt apply` only prints the plan. Every tool runs as `gwt <tool>` and keeps its old `gwt-<tool>` command too. `gwt mh` needs the `mh` extra (`pipx install '.[mh]'`) and a Chrome it reaches over CDP. `gwt-spanish-poller` needs Node 18+ and Playwright and is not in the package. PyPI gets the package with the first release. [Getting started](https://geiserx.github.io/gramps-web-toolkit/getting-started/) connects the tools to your own tree.

## Documentation

The full documentation is at https://geiserx.github.io/gramps-web-toolkit/

- [Getting started](https://geiserx.github.io/gramps-web-toolkit/getting-started/): what each tool needs, install, connecting to a tree, a first dry run
- [The research pipeline](https://geiserx.github.io/gramps-web-toolkit/pipeline/): how agents, verifiers, a judge and a writer share one tree
- [The autonomous research loop](https://geiserx.github.io/gramps-web-toolkit/agent-loop/): the queue, leads, ledger, gate and cache wired into one loop an agent runs on its own
- [Hard-won lessons](https://geiserx.github.io/gramps-web-toolkit/hard-won/): each failure that cost us time, data or an account, and the rule that followed
- One page per tool: [gwt-apply](https://geiserx.github.io/gramps-web-toolkit/apply/), [gwt-check](https://geiserx.github.io/gramps-web-toolkit/check/), [gwt-import-excel](https://geiserx.github.io/gramps-web-toolkit/import-excel/), [gwt-leads](https://geiserx.github.io/gramps-web-toolkit/leads/), [gwt-queue](https://geiserx.github.io/gramps-web-toolkit/queue/), [gwt-ledger](https://geiserx.github.io/gramps-web-toolkit/ledger/), [gwt-rank](https://geiserx.github.io/gramps-web-toolkit/rank/), [gwt-fetch](https://geiserx.github.io/gramps-web-toolkit/fetch/), [gwt-mh](https://geiserx.github.io/gramps-web-toolkit/myheritage/), [gwt-rawstore](https://geiserx.github.io/gramps-web-toolkit/rawstore/), [ocr-bench](https://geiserx.github.io/gramps-web-toolkit/ocr-bench/), [gwt-spanish-poller](https://geiserx.github.io/gramps-web-toolkit/spanish-poller/), [gwt-gate](https://geiserx.github.io/gramps-web-toolkit/gate/), [gwt-fscache](https://geiserx.github.io/gramps-web-toolkit/fscache/)
- [Development](https://geiserx.github.io/gramps-web-toolkit/development/): running the tests, building the site, the rules for a change

## License

[GPL-3.0-or-later](LICENSE)
