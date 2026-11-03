# gwt-corpus

Agents go back to rate-limited sites for records they already fetched weeks ago, and books and gazettes get crossed with the tree by one-off scripts. `gwt-corpus` keeps one offline full-text index of everything already on disk: the [gwt-fscache](fscache.md) answers, the envelopes [gwt-read](read.md) and [gwt-mh](myheritage.md) write, the text [gwt-fetch](fetch.md) saves, and any PDF, text, HTML or JSON file you point it at. Search it first; go to the site only for what it does not have.

Standard library only (SQLite with FTS5, which every current Python ships). PDFs need `pdftotext` from poppler on PATH; without it they are counted and skipped, never silently.

```sh
gwt corpus update ROOT... [--db FILE]
gwt corpus search TEXT [--source fscache|reader|file] [--limit N] [--near N | --phrase] [--db FILE]
gwt corpus search --fts EXPR --control EXPR [--db FILE]
gwt corpus sweep (--names persons.tsv | --dump DUMP) --control NAME [--control NAME...] [--ledger FILE --agent A]
gwt corpus xref BOOK.txt|BOOK.pdf --dump DUMP [--max-pair-pages 6] [--max-surname-people 40] [--format json|tsv]
```

The index lives in `--db`, else `$GWT_CORPUS_DB`, else `~/.cache/gwt-corpus/corpus.db`. A typical day:

```sh
gwt corpus update ~/.cache/gwt-fscache research/raw    # after a big download
gwt corpus search "Ana Olmedo Quiroga"                  # before any site
gwt read familysearch search --given Ana --surname Olmedo --surname Quiroga ...   # only if the corpus had nothing
```

Exit codes, for every command: 0 hits, 1 none, 2 bad input (no index yet, a bad file, a PDF without `pdftotext`), 3 void (a control found nothing, so no answer can be trusted).

## update

`update ROOT...` walks each folder and indexes:

| File | Source | Pointer kept |
|---|---|---|
| `<key>.body` with its `<key>.json` (a gwt-fscache entry) | `fscache` | the URL from the metadata, the fetch date |
| `<key>.json` with `url` and `text` or `json` (a reader envelope) | `reader` | `final_url`, the capture date |
| `.txt`, `.md`, `.html`, other `.json` | `file` | the URL from the folder's `manifest.tsv` (gwt-fetch or reader shape), if any |
| `.pdf` | `file` | the same, one row per page |

A `.txt` with form feeds (what `pdftotext` writes) is one row per page. Images and other binary cache bodies, `*.candidates.json` (a reader's normalized copy of its envelope), `manifest.tsv` and `rawstore.tsv` are not indexed. Every row keeps the file's sha256, so a file you later moved to the bucket with [gwt-rawstore](rawstore.md) comes back with `gwt rawstore get <sha256>`.

A second run reads only files whose size or time changed and whose sha256 is new. A file gone from a ROOT leaves the index; files under a root you did not name this time stay. A cache body whose metadata is not written yet is skipped and read at the next run. One update at a time: a second one exits 2 while the first holds the lock beside the database.

## search

Accents and case fold (`unicode61 remove_diacritics 2`): `jose olmedo` finds «José OLMEDO». Every word must appear, within `--near` words of each other (default 10), in any order. The search is **never OR**: punctuation, quotes and `+` only separate words, and a word such as `OR` or `AND` is searched as a word. `--phrase` asks for the words side by side, in your order.

Each hit prints its source, date, URL (or file) with the page, the sha256 and a snippet:

```text
[fscache] 2026-01-05  https://records.example.org/ark:/99999/1:1:AAAA-111  sha256:5e0b1c4d2a9f7e31
    … »José« »Olmedo« Quiroga · 3 marzo 1871 · Villazul …
```

**0 hits means «not downloaded yet»**, and the output says so. The corpus holds only what someone fetched; it never shows a record does not exist. Log a corpus search in [gwt-ledger](ledger.md) under its own source (`corpus`), never under the site's.

`--fts EXPR` passes a raw FTS5 expression (`"ana olmedo" AND villazul`, `olmed*`). A raw expression is easy to get wrong in a way that still runs and returns 0, so `--fts` needs `--control EXPR`, a raw expression you know matches this corpus. When the control finds nothing, the search is void (exit 3). `--control` also works with a plain search.

## sweep

Looks for a whole list of people at once: a TSV whose header names `key`, `given` and `surname` (and optionally `label`), or a `gwt-check dump`. For each person it searches the full name as a phrase, and, when the surname field holds two surnames («Olmedo Quiroga»), the two surnames side by side as a phrase. One TSV row per hit on stdout.

At least one `--control` is required: a full name you know is in the corpus. Each control is searched before the first person and again after the last. When one finds nothing at either end (the wrong `--db`, an index rebuilt during the run), the run is void: exit 3, and no ledger row is written.

With `--ledger FILE`, each person and phrase becomes one [gwt-ledger](ledger.md) row: source `corpus`, the query in quotes, `hit` with the first hit's URL or file and page as evidence, or `none` with the database path. The rows are written only after the closing controls pass.

## xref

Crosses a book, gazette or bulletin with the tree. The input is text with one page per form feed (what `pdftotext -layout` writes) or a PDF (through `pdftotext`); page numbers are the file's pages. A word broken over a line («maes-/tro») is joined, and a dictionary heading «SURNAME SURNAME, Given» (capitals before the comma, up to four words after it) is also read as «Given Surname Surname». Three kinds of match, at most one per person and page:

| Match | When |
|---|---|
| `name` | the person's given name and surnames, side by side |
| `pair` | the two surnames side by side, only when the pair is on `--max-pair-pages` pages or fewer (default 6) |
| `place` | one of the surnames and the town of the person's birth or death on the same page, only for surnames `--max-surname-people` people of the tree or fewer carry (default 40) |

The town is the first settlement (city, town, village, municipality, parish and the like) at or above the event's place, so a death in a church counts its town.

The output is `{"candidates": [...]}` for [gwt-rank](rank.md): `source` `xref`, `id` `md5:<md5 of the file>#p<page>`, the tree person's name, `principal` false, plus `match`, `person` (the Gramps ID), `md5`, `page` and `context` (the folded text around the match). `--format tsv` prints the same as a table. Cite the book by its md5 and page. Each row is a lead to read, never a finding: the judgement (same person, namesake) comes from reading the entry with its parents, spouse, dates and place.

## Limits

- Matching is on folded words. An OCR error inside a name («Olrnedo») is missed; a name broken across a page is missed.
- A scanned PDF with no text layer gives no rows. Run OCR first (see [ocr-bench](ocr-bench.md)) and index its text.
- The corpus is local to the machine that holds the files. Several machines each keep their own.
