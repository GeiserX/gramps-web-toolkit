# Archives catalogue

A gap in a tree is only half a task. The other half is where the missing record is kept. `gwt-leads --archives FILE` can say that, but only from a file you write yourself, so a new user starts with nothing. And a search done in the wrong place gets counted as a negative. A city's index cannot hold a birth from the next town, and a register that starts in 1871 cannot hold a birth from 1850.

`gwt-leads` ships a catalogue of about 200 archives and record portals, one file per country. Each row says which places and years it covers, which record types it holds and how you reach it. Every row comes from a probe of the source's public site.

```bash
gwt-check dump tree.json.gz
gwt-leads --archives builtin:es,xx --format json tree.json.gz      # Spain, plus the sources that span countries
gwt-leads --archives builtin:all --format queue --root I0001 tree.json.gz > seeds.jsonl
```

`builtin:` takes country codes (ISO 3166, any case, comma-separated) or `all`. `xx` holds the sources that cover several countries: FamilySearch, Find a Grave, Open Archives outside the Netherlands, Matricula outside its home dioceses, and the paid tree sites. An unknown code exits `2` and lists the codes there are. A path that does not start with `builtin:` is read as your own file, exactly as before.

## What a gap gets

The record a gap asks for is the same one as with your own file: the birth or baptism for a branch end, a birth gap or an unsupported link, and the death or burial for a death gap. A marriage gap gets no hint. Neither does an event with no place, nor a private event.

A row fits that record when all of these hold:

- one of the names in its `match` cell is a level of the event's place: the place itself, the province it sits in, the country (names compare folded, the same way `--archives FILE` compares them). A `*` row (a world source) fits any place;
- a `region` or `place` row of a country's file fits only when the place also names that country, or names no country the catalogue knows. Barcelona, Venezuela never gets the Catalan archives, and Paris, Texas never gets the Archives de Paris. Country rows only fit through the country's own names, so a place chain needs a country level (`España`, `France`, `USA`) before any country row can match; a tree whose places stop at the province gets only the region rows and the world rows;
- its `record_types` hold the record: `birth` for a birth or baptism, `death` for a death or burial;
- its `years` hold the event's year (an exact year, or an "about" or estimated one; a "before", "after" or range year is no year, so it never rules a row out);
- its `coverage_places`, when it has any, include a level of the event's place.

The fitting rows are listed deepest level first (a province's archive before the country's), then the world rows. Rows of one level go by `access`: `online` first, then `browser`, `login`, `paid`, `letter` and `in-person`, and in file order within the same access.

- `--format json`: the first fitting row is `where`, with the same keys as with your own file (`archive`, `series`, `url`, `online`, `access`). The others are `also`, a list in the same shape. A row that matches a named level of the place and the record but not the year or the places is listed under `outside`, with why: `{"archive": "matchID: décès INSEE", "why": "covers 1970-, not 1930"}`. A search there cannot find this record, so it is not a negative. World rows are never listed there: a death in 1890 being outside a 1939-1945 index tells nobody anything.
- `--format queue`: the note's `where:` part lists up to three rows per record: `where` and the first two of `also`.
- `--format tsv` does not change.

`online` is `true` only for a source a lane can search (`access` `online`) when the year is known. A row whose robots.txt forbids scripts is never `online`. It is `false` for any other access and `null` when the year is unknown.

In the url, `{place}` is the event's own place, `{year}` its year, `{year_from}` and `{year_to}` five years either side, `{given}` and `{surname}` the person's first given name and first surname, and `{code}` the place's code in the tree (an INSEE, INE or ISTAT code, if you keep one). Every value is URL-encoded, and a value the tree does not hold is left empty. `gwt-leads` never opens the url; it only writes it down.

## The files

`leads/archives/<CC>.tsv`, UTF-8, tab-separated, one header row:

| column | holds |
|---|---|
| `match` | place names separated by vertical bars: a country in its own language and in a few others, or a region and its provinces. `*` for a world source |
| `match_level` | `country`, `region`, `place`, or `world` (only with `*`) |
| `archive` | the archive or portal |
| `series` | what it holds, in a few words |
| `record_types` | some of `birth`, `marriage`, `death`, `census`, `migration`, `military`, `probate`, `land`, `notarial`, `court`, `press`, `directory`, `tree`, `gazetteer`, `catalogue`, `other`. A parish register counts as `birth,marriage,death`; a war-dead list as `death` |
| `years` | the years it covers, as ranges separated by commas. An open end, `1871-`, means it still runs. Empty means not known: the row fits any year |
| `civil_start` | the year civil registration began there, when the row is about vital records |
| `url` | where to search, with the placeholders above, or empty |
| `access` | `online` (a lane can search it), `browser` (a person has to open it: a bot challenge, or robots.txt or the terms forbid scripts), `login` (a free account), `paid`, `letter` (by request) or `in-person` |
| `robots` | what its robots.txt or front door said: `ok`, `disallow` (robots.txt or the terms forbid scripts), `challenge` (a bot check), or empty when not known |
| `coverage_places` | places separated by vertical bars, when the source holds less than its `match`: the Archives de Paris match the old Seine département but hold only Paris |
| `tested` | the date a probe last got a 2xx or 3xx answer from the url, or empty |
| `notes` | rate limits, closures, terms |

A source that forbids scripts stays in the catalogue as `access` `browser`, so a gap still points a person there. The files are checked when they load, and again by `leads/test_archives_data.py`. A wrong column, an unknown value, years that are not ranges, a repeated name or an unknown placeholder stops the run with exit `2` and the file and line.

## Countries

| code | country | rows |
|---|---|---|
| `ad` | Andorra | 1 |
| `ar` | Argentina | 2 |
| `at` | Austria | 3 |
| `au` | Australia | 2 |
| `ba` | Bosnia and Herzegovina | 1 |
| `be` | Belgium | 1 |
| `bg` | Bulgaria | 1 |
| `bo` | Bolivia | 1 |
| `br` | Brazil | 2 |
| `by` | Belarus | 1 |
| `ca` | Canada | 6 |
| `ch` | Switzerland | 3 |
| `cl` | Chile | 1 |
| `cn` | China | 1 |
| `co` | Colombia | 1 |
| `cr` | Costa Rica | 1 |
| `cu` | Cuba | 3 |
| `cy` | Cyprus | 1 |
| `cz` | Czech Republic | 5 |
| `de` | Germany | 9 |
| `dk` | Denmark | 3 |
| `do` | Dominican Republic | 1 |
| `dz` | Algeria | 1 |
| `ec` | Ecuador | 1 |
| `ee` | Estonia | 2 |
| `eg` | Egypt | 1 |
| `es` | Spain | 18 |
| `fi` | Finland | 4 |
| `fr` | France | 11 |
| `gb` | United Kingdom | 11 |
| `gg` | Guernsey | 1 |
| `gr` | Greece | 1 |
| `gt` | Guatemala | 1 |
| `hr` | Croatia | 1 |
| `ht` | Haiti | 1 |
| `hu` | Hungary | 2 |
| `ie` | Ireland | 5 |
| `il` | Israel | 1 |
| `im` | Isle of Man | 1 |
| `in` | India | 1 |
| `is` | Iceland | 2 |
| `it` | Italy | 2 |
| `je` | Jersey | 1 |
| `jm` | Jamaica | 1 |
| `jp` | Japan | 1 |
| `lb` | Lebanon | 1 |
| `li` | Liechtenstein | 2 |
| `lt` | Lithuania | 1 |
| `lu` | Luxembourg | 3 |
| `lv` | Latvia | 2 |
| `ma` | Morocco | 1 |
| `mc` | Monaco | 1 |
| `md` | Moldova | 1 |
| `mt` | Malta | 1 |
| `mx` | Mexico | 2 |
| `nl` | Netherlands | 4 |
| `no` | Norway | 2 |
| `nz` | New Zealand | 2 |
| `pe` | Peru | 1 |
| `ph` | Philippines | 1 |
| `pl` | Poland | 5 |
| `pr` | Puerto Rico | 1 |
| `pt` | Portugal | 2 |
| `py` | Paraguay | 1 |
| `ro` | Romania | 1 |
| `rs` | Serbia | 1 |
| `ru` | Russia | 1 |
| `se` | Sweden | 2 |
| `si` | Slovenia | 1 |
| `sk` | Slovakia | 1 |
| `sm` | San Marino | 1 |
| `tr` | Turkey | 1 |
| `ua` | Ukraine | 1 |
| `us` | United States | 7 |
| `uy` | Uruguay | 2 |
| `va` | Vatican City | 1 |
| `ve` | Venezuela | 1 |
| `za` | South Africa | 2 |
| `xx` | several countries | 19 |

## Checking the urls (maintainers)

```bash
leads/gwt-leads archives probe --country nl     # one country; no --country: every file
```

The probe fills each url with invented values (the surname `Example`, the year 1900), never a real person. It reads each host's robots.txt first (following its redirects, as RFC 9309 says) and skips a path it forbids; a robots.txt that errors or does not answer forbids the whole host. Then it sends a HEAD request, or a GET when the server refuses HEAD, waiting two seconds before each request (`--delay`, never less than one). It does not follow redirects. A 2xx or 3xx answer writes today's date into `tested`, and nothing else in the file changes. Any other answer leaves `tested` as it was and prints `FAIL` with the status, and the command exits `1`. It sends the User-Agent `gramps-web-toolkit archives probe`. It is a maintainer's command, run from a clone, never in CI. A row whose url keeps failing keeps its `access` and an empty `tested`; say what happened in `notes`. Many sites answer a script with 403 (a bot check) while a browser gets in, which is why `access` `browser` exists.

To add a source, add a row to the country's file (or a new `<CC>.tsv`), run the tests in `leads/`, probe that country, and name in the pull request where the facts come from.
