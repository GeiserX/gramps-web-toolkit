# gwt-read digitalarkivet: Norwegian censuses and church books

The Arkivverket's Digitalarkivet has transcribed the Norwegian censuses from 1801 to 1920, many church books and emigrant lists, and searches every person in them from one page. https://www.digitalarkivet.no/en/search/persons

## Commands

```sh
gwt read digitalarkivet search --name "WORDS" [--from 1850] [--to 1900] [--page 1] --out DIR
gwt read digitalarkivet control
```

- `--name` is the site's own search box: names, and a place or a year if you like; the site reads them all. An empty `--name` is refused (exit 64): the site would answer with a page of unrelated people.
- `--from` and `--to` limit the year of the event.
- A page holds 50 persons; `--page` reads the next ones. The site shows at most 10,000 hits (200 pages), so a `--page` above 200 is refused (exit 64) before any request: the site answers it with HTTP 500, which would pause the gate.

## How it reads

There is no public API. The reader reads the English search page, `/en/search/persons?s=...`.

## What a candidate is

A transcription, a lead: open the scanned page before citing it. Each one has `id` (the site's person id, the last part of its page address: `pf...` for a census, `pd...` or `pv...` for a church book), the name with the given names and surname as the site splits them, `sex`, `born` (as the site writes it), the event the row is for (`events.event`: the site's label and date), `domicile`, `position` (the census's position or status), `role` (in a church book: `mor`, `far`, `fadder`, ...; a person with a role is not the principal), and the source's title and link (`record_source`, `source_url`). `url` is the person's own page.

## When it stops

"None" is the site's own count: "Your search resulted in 0 hits". Any page with no person row and no zero count is a STOP (exit 3, the gate pauses every lane, nothing written or cached): a block page, a new layout, an empty list with a count above zero, or a person row the reader cannot read (it never drops a row and reads the page as fewer persons).

A `--page` past the last one (an empty list when the earlier pages already hold every hit the site counts) is exit 2, with `past the end` on the results line, as for the other sources; a loop can page until exit 2.

## Pace, cache and terms

- The gate (`~/.cache/gwt-gate-digitalarkivet`) waits 6 s between requests, never less than 5 s: robots.txt asks every robot for `Crawl-delay: 5`.
- A search is cached 30 days.
- robots.txt blocks a list of AI crawlers by name; it allows the search to other agents with the delay above. The transcriptions are free to read; no licence for reuse is stated, so cite the source.

## Canary

`--name Hansen`, at least 50 rows (one full page; the site gave 10,000 hits in November 2026).
