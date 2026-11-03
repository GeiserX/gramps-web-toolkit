# gwt-read digitalnz: Papers Past, New Zealand newspapers

[Papers Past](https://paperspast.natlib.govt.nz/) (National Library of New Zealand) holds the scanned text of New Zealand newspapers from 1839: births, deaths and marriages notices, shipping lists, court reports, obituaries. Its own site sits behind a bot wall, so this reader never asks it. It searches the [DigitalNZ](https://digitalnz.org/) API instead, which indexes the same articles with their text, kept to the Papers Past collection: https://api.digitalnz.org/records.json .

## Commands

```sh
gwt read digitalnz search --query '"John Smith" Dunedin' [--year 1896] [--count 20] [--page 1] --out DIR
gwt read digitalnz control
```

- `--query` takes words, or a phrase in double quotes.
- `--year` keeps the search to issues of one year.

## What a candidate is

A row is an article, never a person: role `article`, never `principal`, so gwt-rank ranks it weak. It carries the article's title as `name` (the paper and the date are part of it), `date`, `year`, `publisher` (the newspaper), `url` (the article on Papers Past), `text_url` (the article's full text from the API, `https://api.digitalnz.org/records/<id>.json?fields=fulltext`) and `snippet`, the words around the first query word found in the text, about 200 characters each side. The API matches word stems, so a snippet can be empty when the text holds only another form of the word.

Each row prints an `item` line: the id, the Papers Past link and the text link.

## When it stops

"None" is the API's own count: `result_count` 0 with no row. Any other answer is a STOP and nothing is written or cached: a renamed result container, no count, a count with no rows, or an article with no link. Without an API key DigitalNZ serves pages 1 to 100, so a `--page` above 100 asks nothing and exits 64: narrow with `--year` or more words, or raise `--count`. A `--page` past the last row is the end of the results, not a STOP: exit 2, with `past the end` on the results line. A 5xx or no answer is an outage: exit 3, and the gate pauses every lane.

## Pace, cache and terms

- DigitalNZ allows anonymous use at a low volume and asks heavier users for a free key; terms: https://api.digitalnz.org/terms-of-use . The gate (`~/.cache/gwt-gate-digitalnz`) waits 3 s between requests and never less than 2 s.
- A search is cached 30 days.
- Each article's reuse rights are its own: read them on its Papers Past page before you copy the text.

## Canary

`--query Smith`, at least 10 rows.

## Not yet

A DigitalNZ key, and the other DigitalNZ collections (photos, archives), are not read.
