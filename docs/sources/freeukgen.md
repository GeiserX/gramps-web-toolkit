# gwt-read freeukgen: FreeREG and FreeCEN

Free UK Genealogy's volunteers transcribe English, Welsh and Scottish records. FreeREG (https://www.freereg.org.uk/) holds parish baptisms, marriages and burials from 1538; FreeCEN (https://www.freecen.org.uk/) the censuses of 1841 to 1911, strongest for Scotland and Cornwall. Both run the same open-source application, MyopicVicar (https://github.com/FreeUKGen/MyopicVicar), so one source reads both.

## Commands

```sh
gwt read freeukgen search [--site freereg|freecen] [--surname S] [--given G] [--from 1800] [--to 1820] \
                          [--type baptisms|marriages|burials|1841..1911] [--county DEV ...] --out DIR
gwt read freeukgen control
```

- `--site` is `freereg` (the default) or `freecen`.
- A search needs `--surname` or `--given`.
- `--type` is a FreeREG record type (`baptisms`, `marriages`, `burials`) or a FreeCEN census year (`1841` to `1911`).
- `--county` is a county's Chapman code (`DEV`, `CON`, `LAN`, ...); repeat it for more.

## How it reads

A search on these sites is a form, as a person fills it in: the reader reads `/search_queries/new` for the session cookie and the form's token, posts the form to `/search_queries`, and reads the results page the site redirects to, `/search_queries/<id>`, where the site keeps the search. The cookie and the token live in memory for the run only; neither reaches the cache, a file or a log line. The form is read fresh for every search.

The reader follows the markup of the application's templates. The sites were down for maintenance (HTTP 503) when it was written, so it has not yet read a live answer: run `gwt read freeukgen control` before relying on it.

## What a candidate is

A transcription, a lead: open the record page and the register image or census page it names before citing it. `id` is the record's own id (24 hex digits), `site` the site it came from, `name` the first person shown, and any other person shown on the row is a relative named `also named`. A FreeREG row has one event, named by the site's record type, with its date, place (place, church and register type) and county; a FreeCEN row has the census (year, county, district) and the birth (year, place, county). `url` is the record's own page, `/search_records/<id>/<name>`.

## When it stops

"None" is the site's own heading, "No results found". "Too many search results" (the sites show none past 500 rows on FreeREG, 1000 on FreeCEN) is neither: nothing is written and the search exits 1, so narrow it. Any other page is a STOP (exit 3, the gate pauses every lane): a block page, a new layout, a form with no token, a result row the reader cannot read. The sites' maintenance page comes with HTTP 503: an outage, exit 3.

## Pace, cache and terms

- The gate (`~/.cache/gwt-gate-freeukgen`) waits 10 s between requests, never less than 5 s. A search is two requests: the form, then the search.
- A search's answer is not cached (each one is a new search on the site). A record page is kept 30 days.
- The robots.txt files limit only two advertising crawlers and state no delay. The transcriptions are free to search; Free UK Genealogy asks for a credit when you cite them.

## Canary

`--surname Smith --given John --from 1800 --to 1801 --type baptisms` on FreeREG, at least 5 rows. Not yet checked live (see above).
