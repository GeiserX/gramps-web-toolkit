# gwt-read tna: The National Archives (UK) Discovery

[Discovery](https://discovery.nationalarchives.gov.uk/) is the catalogue of The National Archives at Kew and of more than 2,500 other UK archives. It names a person in many series: wills proved in the Prerogative Court of Canterbury (PROB 11, one entry per will), army and navy service records (WO 97, WO 363, ADM 188), naturalisation papers (HO 334, HO 405). For the local archives it says which record office holds a parish's registers or a family's papers. Its API is open, with no key and no login: https://discovery.nationalarchives.gov.uk/API/search/records .

## Commands

```sh
gwt read tna search --query "Will of John Smith" [--series "PROB 11"] [--held-by all|tna|other] \
                    [--from 1830] [--to 1835] [--count 20] [--page 1] --out DIR
gwt read tna record D280526 --out DIR
gwt read tna control
```

- `--series` keeps the search to a record series; repeat it for more.
- `--held-by tna` searches Kew only, `--held-by other` the other archives only.
- `--from` and `--to` are years of the record.
- `record` takes an entry's id (`C1234`, `D280526`, the long id of a local archive's entry) or its Discovery URL.

## Which archive holds it

A search prints one `archive` line per archive holding matching entries: the count and the archive's name. Use `--held-by other` with a place and a kind of record ("Halifax parish registers") to find the record office that holds them.

## What a candidate is

A row is a catalogue entry, never a person: role `item`, never `principal`, so gwt-rank ranks it weak. It carries `reference` (`PROB 11/1816/256`), `held_by` (the archives), `dates` as the catalogue writes them with `year` and `year_to`, `description`, `context` (the series it sits in) and `url` (`https://discovery.nationalarchives.gov.uk/details/r/<id>`). A `record` row adds `digitised` (Discovery marks the entry as scanned), `parent_id` and `legal_status`.

## When it stops

"None" is the API's own count: `count` 0 with no record. Any other answer is a STOP and nothing is written or cached: a renamed result container, no count, or a count with no rows. A `--page` past the last row is the end of the results, not a STOP: exit 2, with `past the end` on the results line. A 5xx or no answer is an outage: exit 3, and the gate pauses every lane.

The details API answers an id it does not have with HTTP 204 and no body. `record` reads that as "no record with this id", exit 1, and the gate hears nothing. Anywhere else an empty answer is still a STOP that pauses the lanes.

## Pace, cache and terms

- Discovery allows about 3,000 API calls a day. The gate (`~/.cache/gwt-gate-tna`) allows at most 120 an hour (2,880 a day) and waits 3 s between requests, never less than 2 s.
- The catalogue changes slowly: a search is cached 30 days, an entry 90.
- The catalogue data is under the Open Government Licence. robots.txt keeps robots off the site's browse, image and basket pages; the API is not listed.

## Canary

`--query "Will of John Smith" --series "PROB 11"`, at least 10 rows.

## Not yet

The images Kew sells or gives for download, and the browse pages, are not read.
