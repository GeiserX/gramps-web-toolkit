# gwt-read kramerius: Czech digital libraries

Kramerius is the digital library software of Czech libraries. The Moravian Library (MZK) and the Czech National Library (NKP) each run an open search over the OCR text of every digitised page: newspapers, address books, school reports, printed lists of clergy, officials and soldiers. No key, no login.

## Commands

```sh
gwt read kramerius search --query '"Jan Novák"' [--library mzk|nkp] [--from 1900] [--to 1910] [--count 1-100] \
                          [--start N] --out DIR
gwt read kramerius control
```

- `--query` is searched in the page text as Solr reads it: words, `"a phrase"`, `AND`, `OR`.
- `--library mzk` (default) reads https://api.kramerius.mzk.cz (Kramerius 7); `--library nkp` reads https://kramerius5.nkp.cz (Kramerius 5). Each library has its own collection; search both.
- `--from` and `--to` keep pages whose dates overlap those years.
- Only public pages are asked for. Pages still under copyright (`dnnt`, `private`) can be read only inside the library or with a Czech library login, so the tool leaves them out.

## What a candidate is

A row is one page that holds the words, never a person. Its candidate has the book's or newspaper's title as `name`, role `mention`, and is never `principal`. It carries `url` (the page in the library's viewer, `https://www.digitalniknihovna.cz/<library>/uuid/<pid>`), `image` (the full page image), `date`, `year`, `page` (the printed page number), `model` (where the page sits: `periodical/periodicalvolume/periodicalitem/page` for a newspaper) and `text`, the words Solr shows around the match. Each row also prints a `page<TAB><url><TAB><image>` line; the image URL is a plain JPEG any downloader, or `gwt fetch`, can save. The envelope's `text` holds every row's words, for a corpus.

## When it stops

"None" is Solr's own count: `numFound` 0 in a well-shaped answer. A missing count, a count with the rows gone, or rows without their id is an answer of the wrong shape: exit 1, nothing written. A `--start` past the last row is the end (exit 2). Kramerius answers a query it cannot parse with HTTP 500 and "Bad Request" in its message: that is this search's fault, exit 1, and the gate is not told. Any other 5xx, or no answer, is an outage: exit 3, the gate pauses every lane.

## Pace, cache and terms

- The gate (`~/.cache/gwt-gate-kramerius`) waits 3 s between requests, never less than 2 s. The libraries publish no limit, and a text search can take a few seconds to answer.
- A search is cached 7 days.
- Neither API host has a robots.txt. Public pages are public domain or free to read; reuse terms are each library's.

## Canary

`--query Novák`, at least 10 pages, at the MZK. Novák is the most common Czech surname: 141,461 public pages on 2026-11-03.
