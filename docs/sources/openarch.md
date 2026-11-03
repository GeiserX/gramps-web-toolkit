# gwt-read openarch: Open Archives

[Open Archives](https://www.openarchieven.nl/) (openarchieven.nl) indexes person records from hundreds of archives: civil registration, church books, population registers, notarial, court and military records. It is strongest for the Netherlands and Flanders, and also holds some French, German, Surinamese, Caribbean and South African series. Each row of a search is one person named in one record (the bride of a marriage, the father at a birth), with that person's role.

## Commands

```sh
gwt read openarch search --name "Jan Jansen" [--period 1850-1860] [--eventplace Amsterdam] [--archive CODE] \
                         [--sourcetype TYPE] [--relationtype ROLE] [--country nl] [--count 25] [--start 0] --out DIR
gwt read openarch record ARCHIVE:UUID [--gedcom] --out DIR
gwt read openarch control
```

- `--name` is read the way the site reads it: one name, a surname alone, or two people joined by `&` (`"Jan Jansen & Maria Bakker"`). `--period` adds a year or a range to it.
- `--count` is 1 to 100 rows; `--start` stops at 10000, because the API refuses deeper pages.
- `record` takes `ARCHIVE:UUID` (the id a search row carries) or the record's `https://www.openarchieven.nl/...` URL, and reads every person in the record. `--gedcom` also saves the record as GEDCOM 7.0 beside the envelope (`<key>.ged`) and prints a `gedcom` line with its path.

Exit codes are those of [gwt-read](../read.md): 0 rows, 2 none, 3 a block or an outage, 1 an answer of an unexpected shape.

## What a candidate is

A search row becomes a candidate with `id` `ARCHIVE:UUID`, the name as the index typed it, the `role` the API gives in English (`Groom`, `Bride`, `Father`, `Registered`), the record's event as `birth`, `death`, `marriage` or `event`, and `url`, the record's citation URL: `https://www.openarchieven.nl/{archive}:{uuid}`. A row is `principal` when its role is the record's main person: a groom or bride, the deceased, the registered person, or the child of a birth or baptism. A child named in a marriage record is not.

`record` gives one candidate per person (`id` `ARCHIVE:UUID:<the record's own person id>`), with given name and surname split, the role in English when it is one of the common Dutch roles (`Bruidegom` is `groom`, `Vader van de bruid` is `bride father`) and otherwise as written, the record's event (its place, or the source's place when the event names none), a birth event when the person has a birth date or place, the occupation, and every other person of the record as a relative with their role.

## When it stops

"None" is the API's own count: `number_found` 0 in a well-shaped answer. Anything else is a STOP and nothing is written or cached:

- an answer of another shape, such as the result container renamed;
- an error the API answers with HTTP 200 and an `error_code` (a missing archive code does this);
- a count above zero with no rows, unless `--start` is at or past that count: a page past the last row is the end of the results (exit 2, `past the end` on the results line);
- an HTML page where the GEDCOM should be.

A 5xx, or no answer at all, is an outage: exit 3, and the gate pauses every lane. A 429 is a wall, as on every source. A 404 or 410 for one record is an error (exit 1), not a block.

## Pace, cache and terms

- The API allows 4 requests a second per IP (https://api.openarchieven.nl/openapi.yaml). The gate (`~/.cache/gwt-gate-openarch`) waits 2 s between requests and never goes faster.
- A search is cached 7 days, a record and its GEDCOM 90 days.
- No key and no login. The robots.txt of `api.openarch.nl` allows everything. The datasets are open, mostly CC0 1.0 and some CC BY-SA 4.0, per archive; cite the archive the record names.

## Canary

`--name Jansen`, at least 10 rows. Jansen is the most common Dutch surname; the API has given well over a million rows for it.
