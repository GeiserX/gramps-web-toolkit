# gwt-read sources

[gwt-read](../read.md) reads several record sites through one shape: the same files, the same gate and cache, the same STOP rules. Each site is a *source*, declared once in a registry, and gwt-read builds the source's subcommands, its gate, its cache rules and its canary controls from that declaration.

| Source | Site | Page |
|---|---|---|
| `familysearch` | FamilySearch historical records | [gwt-read](../read.md#familysearch) |
| `pares` | PARES, the Spanish state archives' catalogue | [gwt-read](../read.md#pares) |
| `genae` | Genae, a volunteer index of parish records | [gwt-read](../read.md#genae) |
| `boe` | BOE and Gaceta de Madrid text search | [gwt-read](../read.md#boe-and-gaceta-de-madrid) |
| `insee` | INSEE's French deaths files, offline | [gwt-read](../read.md#insee-french-deaths-files) |
| `openarch` | Open Archives: Dutch, Belgian and other archives' person records | [openarch](openarch.md) |
| `wikitree` | WikiTree, the shared one-world tree, public profiles | [wikitree](wikitree.md) |
| `archiveorg` | Internet Archive: scanned books and registers, catalogue and full text | [archiveorg](archiveorg.md) |
| `matchid` | matchID: France's deaths since 1970, searched online | [matchid](matchid.md) |
| `riksarkivet` | Riksarkivet (Sweden): scanned church books and other volumes, with IIIF manifests | [riksarkivet](riksarkivet.md) |
| `tna` | The National Archives (UK) Discovery: wills, service records, 2,500 UK archives | [tna](tna.md) |
| `census1950` | The 1950 US census name index (NARA), with the page scans | [census1950](census1950.md) |
| `digitalnz` | Papers Past, New Zealand newspapers, through the DigitalNZ API | [digitalnz](digitalnz.md) |
| `dokuklik` | Dokuklik: Basque parish baptisms, marriages and burials | [dokuklik](dokuklik.md) |
| `hiski` | HisKi: Finnish parish registers index | [hiski](hiski.md) |
| `digitalarkivet` | Digitalarkivet: Norway's transcribed censuses and church books | [digitalarkivet](digitalarkivet.md) |
| `geneteka` | Geneteka: Polish parish and civil registers index | [geneteka](geneteka.md) |
| `freeukgen` | FreeREG and FreeCEN: UK parish registers and censuses | [freeukgen](freeukgen.md) |

`gwt read --help` lists every source the installed version knows.

## What a source declares

A source is one module, `read/gwt_<id>.py`. It sets `SOURCE`, the slug the ledger and each candidate carry, and `SPEC`:

| Key | What it is |
|---|---|
| `id` | the slug again; the subcommand is `gwt read <id>` |
| `help` | one line for `gwt read --help` |
| `pace` | `{start, floor, ceil}` seconds between requests, for the source's own gate (`~/.cache/gwt-gate-<id>`) |
| `cache_rules` | `((url regex, seconds or None), ...)`: how long [gwt-fscache](../fscache.md) keeps each kind of answer; `None` never expires |
| `controls` | `[{argv: [...], min_count: N}]`: canary searches, given as `search` options, that any working copy of the site answers with at least N rows |
| `result_markers` | regexes found only on a page holding at least one row of results |
| `negative_markers` | regexes of the site's own words for "nothing found" |

and the functions `add_search_args(sp)` and `search(a, R)`, plus `add_record_args(sp)` and `record(a, R)` when the site has a record page. `search` asks for every page through `R.get(url, headers, refresh=a.refresh, page=gwt_reader.page_check(SPEC))`, writes the envelope and candidates with `gwt_reader.write_search`, prints the lines with `gwt_reader.print_paths`, and returns 0 with rows or 2 with none.

A source that reads a JSON API, not HTML pages, works the same way with one change: it passes `expect=<a function that checks the answer's shape>` to `R.get` instead of `page=`, as `familysearch` does with `is_search`. An answer of another shape is a STOP. It leaves `result_markers` and `negative_markers` empty, because with no marker every page classifies `unknown`, and tells "none" by the answer's own count field (zero rows in a well-shaped answer).

An open API calls `gwt_reader.get_api(R, url, headers, expect=...)` instead of `R.get`. It is the same call, except that a 5xx or no answer at all (a timeout, a refused connection) means the service is down: a wall (exit 3, the gate hears a hit), never an error a lane logs and moves past. A 4xx stays an error. `openarch`, `wikitree`, `archiveorg` and `matchid` read this way.

## The page classifier

`gwt_reader.classify(page, SPEC)` gives one of three answers:

| Answer | When | What gwt-read does |
|---|---|---|
| `hits` | a result marker is on the page | writes the rows, exit 0 |
| `none` | no result marker, and a negative marker is | writes an empty candidates file, exit 2 |
| `unknown` | neither | a STOP: nothing written or cached, the gate pauses every lane, exit 3 |

So a page skeleton with no rows, a block page, or wording the site changed is never logged as "no results". Pick a result marker that only appears on a row (a row's class, a record link), never the empty container the skeleton also has.

## Canaries

A canary is a public search with a known minimum: a very common name with a count the site has given for years, with room to spare. Never use a family's own surname or a person from someone's tree; the canary is in public code. `gwt read <id> control` runs them without the cache, and `search --controls before|after|both` runs them around one search ([canary controls](../read.md#canary-controls)). An offline source has none.

## Add a source

1. Copy `read/_template.py` to `read/gwt_<id>.py` and fill in `SPEC` and the functions. The template reads an invented site, so it runs as written.
2. Add the module name to `SOURCE_MODULES` in `read/gwt_reader.py`, and its line to `[tool.hatch.build.targets.wheel.force-include]` in `pyproject.toml`. The wheel check imports every module the registry names.
3. Write `read/test_gwt_<id>.py` with invented fixture pages under `read/fixtures/<id>/`: one with rows, one with the site's "nothing found" words, one skeleton. The `Template` class in `read/test_registry.py` is the pattern.
4. Put the source's mutant rows in `read/mutants.d/<id>.txt` (same format as `mutants.txt`), so sources added at the same time never edit one file. At least: the negative marker removed, the result marker removed, the pace made faster.
5. Write `docs/sources/<id>.md`: what the site holds, its terms and robots.txt, the pace and why, the canary and its minimum, what a candidate is.

Before the first live run, read the site's robots.txt and terms, send the project User-Agent, and keep to a person's pace. A source that needs a login, solves a captcha or works around a block does not belong here.
