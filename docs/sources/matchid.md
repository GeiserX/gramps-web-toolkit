# gwt-read matchid: France's deaths since 1970

[matchID](https://deces.matchid.io/) serves INSEE's deaths files, every death registered in France since 1970, through an open search API: https://deces.matchid.io/deces/api/v1/search . It holds the same data [`gwt-read insee`](../read.md#insee-french-deaths-files) reads offline from the downloaded files. Use it to check a few people without downloading the files.

## Commands

```sh
gwt read matchid search [--given G] [--surname S] [--born 1902] [--died 1985] [--birth-city C] [--death-city C] \
                        [--sex M|F] [--count 20] [--page 1] --out DIR
gwt read matchid search --q "Dupont Jean 1950" --out DIR
gwt read matchid control
```

- A search needs `--surname`, `--given` or `--q`, the site's own free text.
- `--born` and `--died` take a year, a date as `JJ/MM/AAAA` (day before month), or a range such as `"01/01/1900 - 31/12/1910"`, passed as written.

## The token

Anonymous use is limited to about one request a second and a capped number of calls. A free token from https://deces.matchid.io/ raises that. Put it in `$MATCHID_TOKEN`: the fetcher sends it as a Bearer header, so it never reaches the cache key, the envelope or a log line.

## What a candidate is

Each row is the principal of one INSEE death record, like an `insee` row: `id` matchID's own id, given names and surname, `sex`, a birth event (year and commune, or the country for a birth abroad), a death event (year and commune), the full dates as INSEE wrote them (`birth_date`, `death_date`), the INSEE place codes, the act number (`act`), the INSEE file and line it came from, and `url` (`https://deces.matchid.io/id/<id>`).

## When it stops

"None" is the API's own count: `total` 0 with no person. Any other answer is a STOP and nothing is written or cached: a renamed result container, no count, or a count with no rows.

The service has had outages. A 5xx, or no answer at all, is read as a block (exit 3, the gate pauses every lane), never as "no results". A 400 (a date the API cannot read) is an error, exit 1.

## Pace, cache and terms

- The gate (`~/.cache/gwt-gate-matchid`) waits 2 s between requests and never less than 1 s, the anonymous limit.
- INSEE adds deaths a month at a time, so a search is cached 30 days.
- The data is INSEE open data under the Licence Ouverte; the robots.txt of `deces.matchid.io` blocks only one crawler.

## Canary

`--given Jean --surname Dupont`, at least 10 rows.

## Not yet

The bulk endpoint (`POST /search/csv`, a whole list of people matched in one job, with a token) is not read yet.
