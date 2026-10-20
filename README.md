<p align="center">
  <img src="docs/images/banner.svg" alt="gramps-web-toolkit banner" width="100%">
</p>

<h1 align="center">gramps-web-toolkit</h1>

<p align="center">
  <a href="https://github.com/GeiserX/gramps-web-toolkit/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/GeiserX/gramps-web-toolkit/ci.yml?style=flat-square&label=CI" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/GeiserX/gramps-web-toolkit?style=flat-square" alt="License"></a>
</p>

gramps-web-toolkit is a set of command-line tools for running AI research agents against a family tree in [Gramps Web](https://www.grampsweb.org) without letting them write guesses into it. Each tool fixes a failure we hit while running about 30 research rounds over two trees of roughly 10,000 people each. The [pipeline design](docs/pipeline.md) and [every trap it taught us](docs/hard-won.md) sit next to the code.

The toolkit works for genealogy in any country. One module, `spanish-poller`, is Spain-only: it watches the Spanish Ministry of Justice sede and the DEHú notification inbox. In `gwt-fetch`, `iiif` works with any IIIF archive, while `digibib`, `gazeta` and `geo` cover Spanish sources and `europeana` filters to Spanish providers unless you pass `--provider any`.

## Features

- `gwt-apply`: applies a JSON Lines change list to a tree, reads before every write, skips what is already there, and stays dry-run until you pass `--apply`
- `gwt-fetch`: downloads archive images and text without a browser, with one manifest row per file: any IIIF archive, plus Spanish sources (DIGIBIB, the historical Gaceta de Madrid, Europeana's Spanish providers, CartoCiudad geocoding)
- `gwt-ledger`: a shared "already searched" ledger, so parallel agents stop repeating each other's negative searches
- `gwt-fscache`: an on-disk HTTP cache for rate-limited genealogy APIs that never stores a block or an auth header
- `gwt-rawstore`: keeps source images in an S3-compatible bucket and only a small manifest in git
- `gwt-spanish-poller` (Spain only): watches Spanish civil-registry requests and the DEHú inbox read-only, and flags cases that vanish
- `ocr-bench`: scores OCR, HTR and LLM readings of the same pages on how many names they recover

## Quick start

```bash
git clone https://github.com/GeiserX/gramps-web-toolkit && cd gramps-web-toolkit
python3 -m pip install -e apply      # Python 3.11+, needs only requests
gwt-apply changes.jsonl              # dry-run: prints the plan and the diff
```

The ledger, cache and rawstore tools need only the Python standard library and run from their folders (`ledger/gwt-ledger`, `fscache/gwt-fscache`, `rawstore/gwt-rawstore`). `gwt-spanish-poller` needs Node 18+ and Playwright.

## Documentation

- [The research pipeline](docs/pipeline.md): how agents, verifiers, a judge and a writer share one tree
- [Hard-won lessons](docs/hard-won.md): each failure that cost us time, data or an account, and the rule that followed
- [gwt-apply](docs/apply.md), [gwt-fetch](docs/fetch.md), [gwt-ledger](docs/ledger.md), [gwt-fscache](docs/fscache.md), [gwt-rawstore](docs/rawstore.md), [gwt-spanish-poller](docs/spanish-poller.md), [ocr-bench](docs/ocr-bench.md)

## License

[GPL-3.0-or-later](LICENSE)
