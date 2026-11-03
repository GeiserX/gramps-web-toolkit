# gwt-corpus

Agents go back to rate-limited sites for records they already fetched weeks ago, and books and gazettes get crossed with the tree by one-off scripts. `gwt-corpus` keeps one offline full-text index of everything already on disk: the [gwt-fscache](fscache.md) answers, the envelopes [gwt-read](read.md) and [gwt-mh](myheritage.md) write, the text [gwt-fetch](fetch.md) saves, and any PDF, text, HTML or JSON file you point it at. Search it first; go to the site only for what it does not have.

Standard library only (SQLite with FTS5, which every current Python ships). PDFs need `pdftotext` from poppler on PATH; without it they are counted, left out of the index and read by the first update that finds `pdftotext`.

```sh
gwt corpus update ROOT... [--db FILE]
gwt corpus search TEXT [--source fscache|reader|file] [--limit N] [--near N | --phrase] [--db FILE]
gwt corpus search --fts EXPR --control EXPR [--db FILE]
gwt corpus sweep (--names persons.tsv | --dump DUMP) --control NAME [--control NAME...] [--max-pages 6] [--ledger FILE --agent A]
gwt corpus xref BOOK.txt|BOOK.pdf --dump DUMP [--max-pair-pages 6] [--max-surname-people 40] [--max-place-pages 6] [--format json|tsv]
```

The index lives in `--db`, else `$GWT_CORPUS_DB`, else `~/.cache/gwt-corpus/corpus.db`. A typical day:

```sh
gwt corpus update ~/.cache/gwt-fscache research/raw    # after a big download
gwt corpus search "Ana Olmedo Quiroga"                  # before any site
gwt read familysearch search --given Ana --surname Olmedo --surname Quiroga ...   # only if the corpus had nothing
```

Exit codes, for every command: 0 hits, 1 none, 2 bad input (no index yet, a bad file, a PDF without `pdftotext`, a PDF `pdftotext` cannot read or with no text layer), 3 void (a control found nothing, so no answer can be trusted).

## update

`update ROOT...` walks each folder and indexes:

| File | Source | Pointer kept |
|---|---|---|
| `<key>.body` with its `<key>.json` (a gwt-fscache entry) | `fscache` | the URL from the metadata, the fetch date; a search answer with an `entries` list (GEDCOM X) is one row per entry, each with the entry's own record link when it has one |
| `<key>.json` with `url` and `text` or `json` (a reader envelope) | `reader` | `final_url`, the capture date |
| `.txt`, `.md`, `.html`, other `.json` | `file` | the URL from the folder's `manifest.tsv` (gwt-fetch or reader shape), if any |
| `.pdf` | `file` | the same, one row per page |

A `.txt` with form feeds (what `pdftotext` writes) is one row per page. Images and other binary cache bodies, `*.candidates.json` (a reader's normalized copy of its envelope), `manifest.tsv`, `rawstore.tsv` and a tree dump (JSON with `objects.people`, where every person would find themselves) are not indexed. The request a search answer repeats back (`query`, `q`, `params`, `responseHeader`) is not indexed either, and a search reads the page text, never the URL: a 0-result answer must not match the name it was asked for. A reader envelope whose `text` is the site's own «no results for X» page still holds X; trust the ledger, not the corpus, for what a site answered. Every row keeps the file's sha256, so a file you later moved to the bucket with [gwt-rawstore](rawstore.md) comes back with `gwt rawstore get <sha256>`.

A second run reads only files whose size or time changed and whose sha256 is new. A file gone from a ROOT leaves the index; files under a root you did not name this time stay. A cache body whose metadata is not written yet, a PDF met before `pdftotext` was installed and a PDF `pdftotext` failed on or timed out on are not recorded, so the next run reads them; the summary counts each kind. A PDF with no text layer is counted too. A file that cannot be read (a broken link, no permission, broken metadata) is listed, counted and skipped, and the run goes on; the index is saved every 1,000 files, so a long first run that stops keeps what it read. One update at a time: a second one exits 2 while the first holds the lock beside the database.

## search

Accents and case fold (`unicode61 remove_diacritics 2`): `jose olmedo` finds «José OLMEDO». The query goes through the same SQLite folding as the index, so `Weiß` finds «Weiß» (and not `Weiss`). Every word must appear, within `--near` words of each other (default 10), in any order. The search is **never OR**: punctuation, quotes and `+` only separate words, and a word such as `OR` or `AND` is searched as a word. `--phrase` asks for the words side by side, in your order.

`--limit` (default 20, at least 1) caps the hits; when it is reached the summary says «first N hits». Each hit prints its source, date, URL (or file) with the page, the sha256 and a snippet:

```text
[fscache] 2026-01-05  https://records.example.org/ark:/99999/1:1:AAAA-111  sha256:5e0b1c4d2a9f7e31
    … »José« »Olmedo« Quiroga · 3 marzo 1871 · Villazul …
```

**0 hits means «not downloaded yet»**, and the output says so. The corpus holds only what someone fetched; it never shows a record does not exist. Log a corpus search in [gwt-ledger](ledger.md) under its own source (`corpus`), never under the site's.

`--fts EXPR` passes a raw FTS5 expression (`"ana olmedo" AND villazul`, `olmed*`). A raw expression is easy to get wrong in a way that still runs and returns 0, so `--fts` needs `--control EXPR`, a raw expression you know matches this corpus. When the control finds nothing, the search is void (exit 3). `--control` also works with a plain search.

## sweep

Looks for a whole list of people at once: a TSV whose header names `key`, `given` and `surname` (and optionally `label`), or a `gwt-check dump`. For each person it searches the full name as a phrase, and, when the surname field holds two surnames («Olmedo Quiroga», «García de la Torre»), the last two side by side as a phrase. Particles (de, la, del, van, von, di and the others of every locale in `gwt_core.rules`) are never a surname on their own: «Luis de la Torre» has no pair, and never searches «la torre». A two-word phrase (a given name and one surname, or a pair) found on more than `--max-pages` pages (default 6) is too common to be a lead: no output row and no ledger row, only a count. One TSV row per hit on stdout. A TSV saved with a byte order mark or Windows line ends reads the same; a row with fewer columns than the header is bad input (exit 2). A dump may be gzipped.

At least one `--control` is required: a full name you know is in the corpus. Each control is searched before the first person and again after the last. When one finds nothing at either end (the wrong `--db`, an index rebuilt during the run), the run is void: exit 3, and no ledger row is written.

With `--ledger FILE`, each person and phrase becomes one [gwt-ledger](ledger.md) row: source `corpus`, the query in quotes, and `control` naming the run whose controls passed (`corpus-sweep-<time>-<n>-controls-ok`).

| Result | Evidence | `next_date` | Why |
|---|---|---|---|
| `partial` | the first hit's URL or file and page | empty | a match is a lead to read, never a finding |
| `none` | `corpus:<database file name>` | the run date | the corpus grows with every update: a miss is due again at once, so `gwt ledger check` never counts it as a search of the person |

The rows are written only after the closing controls pass. Running the same sweep twice on one day adds no rows.

## xref

Crosses a book, gazette or bulletin with the tree. The input is text with one page per form feed (what `pdftotext -layout` writes) or a PDF (through `pdftotext`); page numbers are the file's pages. A word broken over a line («maes-/tro») is joined, and a dictionary heading «SURNAME SURNAME, Given» (capitals before the comma, up to four words after it) is also read as «Given Surname Surname». Three kinds of match, at most one per person and page:

| Match | When |
|---|---|
| `name` | the person's given name and surnames, side by side |
| `pair` | the last two surnames side by side, only when the pair is on `--max-pair-pages` pages or fewer (default 6) |
| `place` | one of the surnames and the town of the person's birth or death on the same page, only for surnames `--max-surname-people` people of the tree or fewer carry (default 40), and only when that surname and town meet on `--max-place-pages` pages or fewer (default 6) |

Particles are never a surname for a pair or a place match, as in sweep. A `name` of only two words (a given name and one surname) counts like a pair: only on `--max-pair-pages` pages or fewer. A word broken over a Windows line end is joined too.

The town is the first settlement (city, town, village, municipality, parish and the like) at or above the event's place, so a death in a church counts its town.

The output is `{"candidates": [...]}` for [gwt-rank](rank.md): `source` `xref`, `id` `md5:<md5 of the file>#p<page>`, the tree person's name, `principal` false, plus `match`, `person` (the Gramps ID), `md5`, `page` and `context` (the folded text around the match). `--format tsv` prints the same as a table. Cite the book by its md5 and page. Each row is a lead to read, never a finding: the judgement (same person, namesake) comes from reading the entry with its parents, spouse, dates and place.

## Limits

- Matching is on folded words. An OCR error inside a name («Olrnedo») is missed; a name broken across a page is missed.
- A scanned PDF with no text layer gives no rows. Run OCR first (see [ocr-bench](ocr-bench.md)) and index its text.
- The corpus is local to the machine that holds the files. Several machines each keep their own.
