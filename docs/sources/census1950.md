# gwt-read census1950: the 1950 US census

The National Archives (NARA) released the [1950 United States census](https://1950census.archives.gov/) in 2022, for every state, Puerto Rico and the other territories, with a name index made by handwriting recognition. The search behind its viewer answers JSON, with no key and no login: https://1950census.archives.gov/api/search . The page scans are in NARA's public bucket on AWS Open Data, https://nara-1950-census.s3.amazonaws.com/ , readable without an account.

## Commands

```sh
gwt read census1950 search --name garcia --state PR [--county "San Juan"] [--page 1] --out DIR
gwt read census1950 control
```

- `--name` is a name as the schedule writes it: a surname, or surname then given name ("garcia juan").
- `--state` is the two-letter code (`PR`, `NY`, `FL`). `--county` is the county, or the municipio in Puerto Rico.
- A page of results is 25 schedule pages, the API's own size. The API counts at most 10,000 pages and serves pages 1 to 400, so a broad search reads `10000 pages`: narrow it with `--county`. A `--page` above 400 asks nothing and exits 64.
- The API matches similar spellings, not only the name asked for: a `garcia` search also returns lines such as `Garcio` or `Maria`. Read the name on each row.

## What a candidate is

A row is one line of a schedule page whose name matches: role `census`, `principal`, with a residence event in 1950 at the county and state. It carries `row` (the line number), `schedule_id`, `ed` (the enumeration district), `county`, `state`, `state_abbr`, `sheet` (the image number in the district), and the page image twice:

- `image`, the scan in NARA's bucket (`https://nara-1950-census.s3.amazonaws.com/1950census/...jpg`), anonymous, for curl or gwt-rawstore; `url` points to it;
- `iiif_info`, the page's IIIF Image API base: `gwt fetch iiif --image-base <iiif_info>` saves the page.

The machine index is noisy: misread letters, given names split from surnames, lines with no number. A row read from it carries `machine: true`. Where volunteers have corrected the page, their line is read instead and carries `machine: false`; the machine's reading of any line number they transcribed is dropped, whatever name they wrote there. A page the API matched but marked no line on gives one row for the page, with no name and no `row`, to read by eye.

Every row has its own `id`: `<schedule>:<row>` for a numbered line, `<schedule>:m<n>` for the machine's n-th line when it has no number (`t<n>` for the volunteers'), and `.2`, `.3` after the line number when one line was read under two names.

Each page prints an `item` line: the schedule id, the S3 image and the IIIF base.

## When it stops

"None" is the API's own count: `total` 0 with no page. Any other answer is a STOP and nothing is written or cached: a renamed result container, no count, a count with no pages, or a page with no image key or no lines. A page only the volunteers have read has no machine lines (`names` is null); that is a page like any other. A `--page` past the last one is the end of the results, not a STOP: exit 2, with `past the end` on the results line. A 5xx or no answer is an outage: exit 3, and the gate pauses every lane.

## Pace, cache and terms

- robots.txt asks for 1 s between requests. The gate (`~/.cache/gwt-gate-census1950`) waits 3 s and never less than 2 s: the API is the one behind the public viewer, not a documented service.
- Volunteers still correct the index, so a search is cached 30 days.
- The census is a US federal record in the public domain; the bucket is part of the AWS Open Data registry.

## Canary

`--name Garcia --state PR`, at least 10 rows.
