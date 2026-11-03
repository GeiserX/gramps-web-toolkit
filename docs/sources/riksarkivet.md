# gwt-read riksarkivet: Swedish church books and other volumes

[Riksarkivet](https://riksarkivet.se/), the Swedish National Archives, has scanned the church books of every Swedish parish (births, marriages, deaths, household examination rolls), estate inventories, court and military rolls, and shows them free. Its catalogue has an open search API, with no key and no login: https://data.riksarkivet.se/api/records . A search here finds the volumes a parish or a series holds and gives each volume's IIIF manifest, which [`gwt fetch iiif`](../fetch.md) reads to save the pages.

## Commands

```sh
gwt read riksarkivet search --query "Uppsala domkyrkoförsamling" [--type Volume] [--all] \
                            [--count 20] [--page 1] --out DIR
gwt read riksarkivet control
```

- `--query` takes the words of a catalogue entry, such as a parish name ("Uppsala domkyrkoförsamling"). Each row names its series (`series`: "Husförhörslängder", "Födelse- och dopböcker"), so a search by parish lists every kind of volume the parish holds.
- By default the search keeps to volumes (`--type Volume`) that are scanned. `--all` adds the volumes that are not scanned, which have no manifest. `--type ""` searches every kind of entry: archives, series, maps.

## What a candidate is

A row is a volume, never a person: role `volume`, never `principal`, so gwt-rank ranks it weak. It carries `reference_code` (`SE/ULA/11632/A I ac/5`), `archive` (the parish archive), `series` (the series path below it, such as "Husförhörslängder"), `date` as the catalogue writes it with `year` and `year_to` (empty for an open end), `held_by` (the archive that keeps it), `url` (the volume's page on sok.riksarkivet.se) and `iiif_manifests`, the manifest of each scanned copy (`https://lbiiif.riksarkivet.se/arkis!<id>/manifest`).

Each manifest prints an `item` line: the reference code and the manifest URL. A scanned volume without a manifest link prints its reference code with an empty URL.

## When it stops

"None" is the API's own count: `totalHits` 0 with no row. Any other answer is a STOP and nothing is written or cached: a renamed result container, no count, or a count with no rows. A `--page` past the last row is the end of the results, not a STOP: exit 2, with `past the end` on the results line. A 5xx or no answer is an outage: exit 3, and the gate pauses every lane.

## Pace, cache and terms

- The gate (`~/.cache/gwt-gate-riksarkivet`) waits 3 s between requests and never less than 2 s.
- The API sends its facets with every answer, so an answer is large. A page is 20 rows by default and never more than 100, every answer is cached 30 days, and the evidence file keeps the rows without the facets.
- `data.riksarkivet.se` has no robots.txt (HTTP 404). The scans are open data, free to use since 2018.

## Canary

`--query "Uppsala domkyrkoförsamling"`, at least 10 rows: the cathedral parish of Uppsala has several hundred scanned volumes.

## Not yet

The OAI-PMH harvest (https://oai-pmh.riksarkivet.se/OAI) that would list every parish's volumes offline is not read yet.
