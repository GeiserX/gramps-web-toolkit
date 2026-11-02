# gwt-read archiveorg: Internet Archive

The [Internet Archive](https://archive.org/) holds millions of scanned books with their OCR text: printed transcriptions of parish registers, county and family histories, city directories, nobility books and official gazettes, from many countries. Its search is open, with no key and no login.

## Commands

```sh
gwt read archiveorg search --query 'title:(parish register) AND date:[1800 TO 1900]' [--mediatype texts] \
                           [--count 50] [--page 1] --out DIR
gwt read archiveorg search --fulltext --query '"John Smith" baptized' [--count 50] [--page 1] --out DIR
gwt read archiveorg control
```

- Without `--fulltext`, `--query` searches the catalogue (title, creator, subject, date) in the site's own query syntax, through https://archive.org/advancedsearch.php. `--mediatype` keeps it to `texts` unless you pass another type or `""` for any.
- With `--fulltext`, the words are searched inside the scanned text, through the full-text backend the archive.org search page uses. It is slower: the site can take tens of seconds.

## What a candidate is

A row is a book or an item, never a person. Its candidate has the item's title as `name`, role `item`, and is never `principal`, so gwt-rank ranks it weak. It carries `url` (`https://archive.org/details/<id>`), `year`, `creator`, `mediatype`, `iiif_manifest` (`https://iiif.archive.org/iiif/<id>/manifest.json`), and for a text, `ocr` (`https://archive.org/download/<id>/<id>_djvu.txt`). A full-text row also has `snippets`, the text around the words.

Each row prints an `item` line: the id, the IIIF manifest, and the OCR link (empty for an item that is not a text). The manifest is what `gwt fetch iiif` reads to save the pages; the OCR link is the whole text of the book.

## Privacy

The full-text answer repeats the caller's IP address and User-Agent in a `session_context` block. The envelope keeps only the answer's `response` part, so they never reach the evidence files. The raw answer in your local [gwt-fscache](../fscache.md) still has them.

## When it stops

"None" is the API's own count: `numFound` (catalogue) or `hits.total` (full text) 0 in a well-shaped answer. Any other answer is a STOP and nothing is written or cached: an error status, a full-text answer that says it did not succeed, or a renamed result container. A `--page` past the last row is the end of the results, not a STOP: exit 2, with `past the end` on the results line. A 5xx or no answer is an outage: exit 3, and the gate pauses every lane.

## Pace, cache and terms

- The gate (`~/.cache/gwt-gate-archiveorg`) waits 3 s between requests, never less than 2 s. The archive does not publish a limit.
- A search is cached 7 days.
- No key and no login; robots.txt only disallows `/control/` and `/report/`. Each item has its own rights; public-domain scans are free to reuse.

## Canary

A catalogue search, `--query 'title:(parish register)'`, at least 10 items.
