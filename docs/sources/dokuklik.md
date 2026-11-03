# gwt-read dokuklik: Basque parish registers

Euskadiko Artxibo Historikoa, the Basque Country's historical archive, indexes about 5.6 million baptisms, marriages and burials from the parishes of Araba, Bizkaia and Gipuzkoa, roughly 1490 to 1900. The entries come from the books of the three diocesan archives (Bilbao, San Sebastián and Vitoria). The index used to be called Dokuklik; its old host, dokuklik.euskadi.eus, is gone, and the index now lives in the archive's site: https://www.artxibo.euskadi.eus/webartxi00-container/es/ad53aArchivoHistoricoWar/sacramentales/maintSimple?locale=es

## Commands

```sh
gwt read dokuklik search [--kind baptism|marriage|death] [--given G] [--surname S] [--surname2 S2] \
                         [--from 1800] [--to 1850] [--archive bilbao|san-sebastian|vitoria] [--count 100] [--page 1] --out DIR
gwt read dokuklik control
```

- A search needs `--given`, `--surname` or `--surname2`.
- The site reads the same three name fields for every kind, and matches them in the record's people: a marriage search on a surname also finds brides with it.
- `--count` is 1 to 100 rows a page (default 100); `--page` reads the next ones.

## How it reads

The search page fills its table from a JSON endpoint, one per kind: `sacramentales/busquedaBautismo`, `busquedaMatrimonio` and `busquedaDefuncion`. The reader posts the same JSON body the page posts. The site answers its HTML error page to a body without the table's column list, so the reader always sends it.

## What a candidate is

An index entry, a lead, not the record. Each one has `id` (`bau-`, `mat-` or `def-` and the site's number), the name, given name and surnames, the event's date and place (parish, locality, municipality, territory), the parents of a baptism or the bride of a marriage as `relatives`, the book's title, the folio, the archive's call number (`call_number`), its reference and the diocese. `url` is the entry's record page (`bautismo/getFicha?bauid=N`, `matrimonio/getFicha?matid=N`, `defuncion/getFicha?defid=N`). Ask the diocesan archive for the page before citing it.

## When it stops

"None" is the answer's own count: `recordsFiltered` 0 with no row. A page past the last row is the end of the results: exit 2, with `past the end` on the results line. Any other answer is a STOP and nothing is written or cached:

- a renamed row list, no count, or a count with no rows: exit 1;
- the site's HTML error page, or any page that is not JSON: exit 3, the gate pauses every lane;
- a 5xx or no answer at all: an outage, exit 3.

## Pace, cache and terms

- The gate (`~/.cache/gwt-gate-dokuklik`) waits 5 s between requests, never less than 3 s. The archive's robots.txt does not cover this application and states no delay.
- The index is finished work: a search is cached 30 days, a record page for good. A search is a POST, so this source's cache keeps POST answers too.
- The archive is a public service of the Basque Government; the index is free to search with no account.

## Canary

`--surname Garcia`, at least 50 rows (the site gave about 19,800 baptisms in November 2026).
