# gwt-read hiski: Finnish parish registers

HisKi is the Finnish Genealogical Society's index of parish registers: baptisms, marriages, burials and moves, parish by parish, from the 1600s to about 1900 (each parish's own years are on its page). https://hiski.genealogia.fi/hiski?en

## Commands

```sh
gwt read hiski search --parish 0035 --book baptisms|marriages|burials|moves \
                      [--given G] [--surname S] [--patronymic P] [--village V] [--field NAME=VALUE ...] \
                      [--from 1750] [--to 1800] [--count 50] [--start N] --out DIR
gwt read hiski control
```

- `--parish` is the parish's four-digit code, as in its HisKi address: `https://hiski.genealogia.fi/hiski?en+0035` is `0035`. The parish list at https://hiski.genealogia.fi/hiski?en gives every code.
- Each parish keeps only some books: its book list, `https://hiski.genealogia.fi/hiski?en+<code>`, shows which.
- `--given`, `--surname`, `--patronymic` and `--village` fill the form's fields for the first person of the book (the man of a marriage, the father of a baptism).
- `--field NAME=VALUE` fills any other field of the form by its own name: `aetunimi` (the wife's or the mother's first name), `asukunimi`, `etunimi` (the child of a baptism), `ksukunimi`. Repeat it for more. A `--field` without `NAME=`, or a value with a letter outside ISO-8859-1, is refused (exit 64) before any request.
- `--count` is one of the site's own page sizes (15, 30, 50, 100, 250, 500, 1000). `--start` is the site's position to continue from (its `hakupos`).

## How it reads

HisKi is an old CGI program with no API. Its search form also answers a plain GET, so the reader sends the form's fields as a query string, in ISO-8859-1 (the encoding the program reads) and in English (`kieli=en`).

Cloudflare sits in front of the site and adds a small script to ordinary pages, whose path holds `challenge-platform`. gwt-read reads that script as what it is, not a wall. A real Cloudflare challenge loads `/cdn-cgi/challenge-platform/h/...`, and stays a wall.

## What a candidate is

An index entry, a lead: read the parish register before citing it. `id` is `<parish>-<book>-<n>`; `columns` keeps every cell with its header, in order; `name` and `role` are the first person column (`Man` for a marriage), and the other persons are `relatives`. `events` holds the first date of the row. `url` is the entry's own page, `https://hiski.genealogia.fi/hiski?en+<parish>+<book>+<n>`.

## When it stops

"None" is the site's own sentence, "No events found with this search criteria". Any other page with no row is a STOP (exit 3, the gate pauses every lane, nothing written or cached), and so is a page with an entry row the reader cannot read. That covers a block page and a new layout.

A book the parish does not keep, or a parish code the site does not have, gets the site's empty search page, titled "Search - <parish> - <book>". That is a mistake in the search, not a block: the reader names it, exits 64, writes nothing, and does not tell the gate. The answer stays in the cache like any search; `--refresh` asks again.

## Pace, cache and terms

- The gate (`~/.cache/gwt-gate-hiski`) waits 10 s between requests, never less than 5 s. robots.txt names no delay, and the site is a volunteer society's old program.
- The index grows slowly: a search is cached 30 days.
- robots.txt allows the search pages to every agent and says `search=yes, ai-train=no`. The entries are for reference: cite the register.

## Canary

`--parish 0035 --book marriages --given Johan`, at least 15 rows (the site gave about 595 in November 2026).
