# gwt-read delpher: Dutch newspapers

[Delpher](https://www.delpher.nl/) is the Dutch national library's (KB) collection of digitised newspapers from 1618 to 1995, cut into articles with their OCR text. Family announcements (births, marriages, deaths), notices and news name people for every decade the registers are closed. This source reads the KB's open SRU API, no key and no login.

## Commands

```sh
gwt read delpher search --query '"Jan Jansen" and overleden' [--from 1900] [--to 1910] [--count 1-100] [--start 1] \
                        [--text 0-20] --out DIR
gwt read delpher control
```

- `--query` is CQL, the API's query language: words, `"a phrase"`, `and`, `or`. `--from` and `--to` (years) add the API's own date filter and wrap the query in brackets: `("Jan Jansen" and overleden) and date within "01-01-1900 31-12-1910"`. Without the brackets the KB's parser reads a quoted phrase followed by a date filter as 0 rows.
- A query with an odd number of double quotes or unbalanced brackets is refused with exit 64 before any request: the API answers it with the same diagnostic as a failing index, which would pause every lane.
- `--text N` reads the OCR text of the first N articles (default 5, at most 20), one request each, from the resolver URL the search returned. `--text 0` reads none.
- `--start` is the first row, from 1; with `--count` it pages through a long result.

## What a candidate is

A row is one article, never a person. Its candidate has the newspaper's title as `name`, role `mention`, and is never `principal`. It carries `url` (`https://resolver.kb.nl/resolve?urn=<article>`, which opens the article), `ocr` (the same with `:ocr`, the article's text), `date`, `year`, `title` (the article's own heading, often "Familiebericht" or "Advertentie"), `type`, `page`, `place` (where the paper was published) and `text`, the OCR text when `--text` read it. The envelope's `text` holds every text read, for a corpus.

## When it stops

"None" is the API's own count: `numberOfRecords` 0 in a well-shaped SRU answer. The API reports its errors (a query it cannot parse, a failing index) with HTTP 200 and a `diagnostics` block; that answer, a missing count, a count with the records gone, or anything that is not an SRU answer is a STOP: nothing written or cached, exit 3. The stop message carries the diagnostic's own words. So is an OCR request that does not come back as OCR text. An OCR URL that answers 404 or 410 leaves that one row without text (`ocr_status` says which) and keeps the others. A `--start` past the last row is the end of the results (exit 2, `past the end` on the results line). A 5xx or no answer is an outage, exit 3, and the gate pauses every lane.

## Pace, cache and terms

- The gate (`~/.cache/gwt-gate-delpher`) waits 3 s between requests, never less than 2 s. The KB publishes no limit.
- A search is cached 7 days, an article's OCR text a year.
- The delpher.nl website closes itself to robots in its robots.txt, so this source never reads it: only the SRU API (https://jsru.kb.nl/sru/) and the resolver URLs the API returns. Both hosts also answer `Disallow: /` to crawlers; the API is the KB's documented way to read the collection by program, one search at a time. Rights differ by title; most pre-1880 papers are public domain.

## Canary

`--query Jansen --text 0`, at least 10 articles. Jansen is the most common Dutch surname: 2,162,854 articles on 2026-11-03.
