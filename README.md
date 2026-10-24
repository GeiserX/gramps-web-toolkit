<p align="center">
  <img src="docs/images/banner.svg" alt="gramps-web-toolkit banner" width="100%">
</p>

<h1 align="center">gramps-web-toolkit</h1>

<p align="center">
  <a href="https://github.com/GeiserX/gramps-web-toolkit/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/GeiserX/gramps-web-toolkit/ci.yml?style=flat-square&label=CI" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/GeiserX/gramps-web-toolkit?style=flat-square" alt="License"></a>
</p>

gramps-web-toolkit is a set of command-line tools for running AI research agents against a family tree in [Gramps Web](https://www.grampsweb.org) without letting them write guesses into it. Each tool fixes a failure we hit while running about 30 research rounds over two trees of roughly 10,000 people each. The [pipeline design](docs/pipeline.md) and [every trap it taught us](docs/hard-won.md) sit next to the code.

The toolkit works for genealogy in any country. One module, `spanish-poller`, is Spain-only: it watches the Spanish Ministry of Justice sede and the DEHú notification inbox. In `gwt-fetch`, `iiif` works with any IIIF archive, while `digibib`, `gazeta` and `geo` cover Spanish sources and `europeana` searches every provider in any country (`--provider Hispana` narrows it to Spain).

## Features

- `gwt-apply`: applies a JSON Lines change list to a tree, reads before every write, skips what is already there, and stays dry-run until you pass `--apply`
- `gwt-check`: reads the whole tree after an apply and reports orphans, dangling links, broken birth and death pointers, likely duplicates and what changed since the last copy, with a self-test that plants every error kind
- `gwt-import-excel`: reads a research workbook (.xlsx, standard library only) with a JSON column mapping and writes a gwt-apply change list for review. Namesakes in the tree are reported and pass gwt-apply's preflight only with a written reason, places are matched to the tree and never created, people who may be alive are held back
- `gwt-fetch`: downloads archive images and text without a browser, with one manifest row per file: any IIIF archive, Europeana search, plus Spanish sources (DIGIBIB, the historical Gaceta de Madrid, CartoCiudad geocoding)
- `gwt-ledger`: a shared "already searched" ledger, so parallel agents stop repeating each other's negative searches
- `gwt-queue`: a durable "what to search next" queue that hands prioritized tasks to lanes, reopens the claims of dead lanes and backs off the tasks that keep failing
- `gwt-leads`: reads a tree dump offline and lists the gaps (lines that stop, missing vitals, uncited facts), ranked with the root's direct ancestors first, as `gwt-queue` tasks
- `gwt-fscache`: an on-disk HTTP cache for rate-limited genealogy APIs that never stores a block or an auth header
- `gwt-rank`: orders record-search results by how well they fit a person in the tree, from the cached FamilySearch JSON or from page text, and never hides a row
- `gwt-gate`: one request pace shared by every agent on a machine, which backs off on a block and lets priority lanes go first
- `gwt-rawstore`: keeps source images in an S3-compatible bucket and only a small manifest in git
- `gwt-spanish-poller` (Spain only): watches Spanish civil-registry requests and the DEHú inbox read-only, and flags cases that vanish
- `ocr-bench`: scores OCR, HTR and LLM readings of the same pages on how many names they recover

## Quick start

```bash
git clone https://github.com/GeiserX/gramps-web-toolkit && cd gramps-web-toolkit
python3 -m pip install -e apply      # Python 3.11+, needs only requests
gwt-apply changes.jsonl              # dry-run: prints the plan and the diff
```

`gwt-check`, the ledger, queue, cache, rank and rawstore tools need only the Python standard library and run from their folders (`check/gwt-check`, `importxl/gwt-import-excel`, `ledger/gwt-ledger`, `queue/gwt-queue`, `leads/gwt-leads`, `fscache/gwt-fscache`, `gate/gwt-gate`, `rank/gwt-rank`, `rawstore/gwt-rawstore`). `gwt-spanish-poller` needs Node 18+ and Playwright.

## Documentation

- [The research pipeline](docs/pipeline.md): how agents, verifiers, a judge and a writer share one tree
- [Hard-won lessons](docs/hard-won.md): each failure that cost us time, data or an account, and the rule that followed
- [gwt-apply](docs/apply.md), [gwt-check](docs/check.md), [gwt-import-excel](docs/import-excel.md), [gwt-fetch](docs/fetch.md), [gwt-ledger](docs/ledger.md), [gwt-queue](docs/queue.md), [gwt-leads](docs/leads.md), [gwt-fscache](docs/fscache.md), [gwt-gate](docs/gate.md), [gwt-rank](docs/rank.md), [gwt-rawstore](docs/rawstore.md), [gwt-spanish-poller](docs/spanish-poller.md), [ocr-bench](docs/ocr-bench.md)

## License

[GPL-3.0-or-later](LICENSE)
