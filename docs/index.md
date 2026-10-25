---
hide:
  - navigation
---

# gramps-web-toolkit { .gwt-visually-hidden }

<p align="center">
  <img src="images/banner.svg" alt="gramps-web-toolkit banner" width="100%">
</p>

<p align="center">
  <a href="https://github.com/GeiserX/gramps-web-toolkit/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/GeiserX/gramps-web-toolkit/ci.yml?branch=main&style=flat-square&label=CI"></a>
  <a href="https://github.com/GeiserX/gramps-web-toolkit/blob/main/LICENSE"><img alt="License: GPL-3.0-or-later" src="https://img.shields.io/github/license/GeiserX/gramps-web-toolkit?style=flat-square"></a>
</p>

---

**gramps-web-toolkit** is a set of command-line tools for running AI research agents against a family tree in [Gramps Web](https://www.grampsweb.org) without letting them write guesses into it. Agents research in parallel and never touch the tree; a verifier checks each finding against its source, a judge decides, and one writer applies the rulings with a backup before each block and a whole-tree check after it. Each tool fixes a failure we hit while running about 30 research rounds over two trees of roughly 10,000 people each.

The toolkit works for genealogy in any country. `gwt-spanish-poller` and some `gwt-fetch` sources are Spain-only, and their pages say so.

<div class="grid cards" markdown>

-   :material-download: **[Getting started](getting-started.md)**

    ---

    What each tool needs, how to install it, and a first dry run against your tree.

-   :material-sitemap: **[The research pipeline](pipeline.md)**

    ---

    How agents, verifiers, a judge and a writer share one tree.

-   :material-shield-check: **[gwt-apply](apply.md)**

    ---

    Applies a change list, reads before every write, and stays dry-run until you pass `--apply`.

-   :material-alert-octagon: **[Hard-won lessons](hard-won.md)**

    ---

    Every failure that cost us time, data or an account, and the rule that followed.

</div>

## Get started

- [Getting started](getting-started.md): requirements, install, connecting to a tree, a first run.
- [The research pipeline](pipeline.md): who researches, who verifies, who decides, who writes.
- [The autonomous research loop](agent-loop.md): the queue, leads, ledger, gate and cache wired into one loop an agent runs on its own.

## Tools that read and write the tree

- [gwt-apply](apply.md): applies a JSON Lines change list, reads before every write, skips what is already there.
- [gwt-check](check.md): reads the whole tree and reports orphans, dangling links, broken pointers, likely duplicates, dates that contradict each other and what changed.
- [gwt-import-excel](import-excel.md): turns a research workbook into a change list for review.
- [gwt-leads](leads.md): reads a tree dump offline and ranks its gaps as queue tasks.

## Research tools

- [gwt-queue](queue.md): what to search next, handed out to lanes, with leases and back-off.
- [gwt-ledger](ledger.md): the shared "already searched" ledger, so lanes stop repeating negative searches.
- [gwt-rank](rank.md): orders record-search results by how well they fit a person, and never hides a row.
- [gwt-fetch](fetch.md): archive images and text without a browser: IIIF, Europeana, and Spanish sources.
- [gwt-mh](myheritage.md): MyHeritage records through a signed-in browser, on a free or a paid account.
- [gwt-read](read.md): FamilySearch record search and records with your own session token, slow and cached by default.
- [gwt-rawstore](rawstore.md): source images in an S3-compatible bucket, a small manifest in git.
- [ocr-bench](ocr-bench.md): scores OCR, HTR and LLM readings of the same pages on the names they recover.
- [gwt-spanish-poller](spanish-poller.md): watches Spanish civil-registry requests and the DEHú inbox, read-only.

## Safety and pacing

- [Hard-won lessons](hard-won.md): the failures behind the rules, and the rules.
- [gwt-gate](gate.md): one request pace shared by every agent on a machine, which backs off on a block.
- [gwt-fscache](fscache.md): an on-disk HTTP cache for rate-limited genealogy APIs.

## Development

- [Development](development.md): tests, building this site, and the rules for a change.

## License

gramps-web-toolkit is released under the [GPL-3.0-or-later](https://github.com/GeiserX/gramps-web-toolkit/blob/main/LICENSE) license.
