# gwt-read

```bash
export GWT_FS_TOKEN_FILE=~/.fs-session        # your own session token, mode 600 (see below)
gwt read familysearch search --given Ana --surname Zorvalo --by 1851 --place Villazul --out runs/
gwt rank runs/<key>.candidates.json --given Ana --surname Zorvalo --facts "by=1851 bp=Villazul"
gwt read familysearch record 1:1:QZZ1-AAA --out runs/
gwt read pares search --given Ana --surname Zorvalo --out runs/     # public, no account
gwt read pares record 9000001 --out runs/
gwt read genae search --given Ana --surname Zorvalo --out runs/    # public, no account
gwt read boe search --name "Ana Zorvalo Quemada" --out runs/      # public, no account
gwt read insee search --files deces-1985.txt deces-1986.txt --surname Ortega --surname Sanz --out runs/   # offline
```

`gwt-read` runs a record search, or reads one record, on a record site, and writes the answer in the shape every record-site reader in the toolkit shares. It reads four sites: [FamilySearch](#familysearch) historical records, [PARES](#pares), the catalogue of the Spanish state archives, [Genae](#genae), a volunteer index of parish records that `gwt-read` only searches: its rows are leads, not records, and the [BOE and Gaceta de Madrid](#boe-and-gaceta-de-madrid) text search, whose hits are mentions of a name in an official act. It also reads one source offline: [INSEE's French deaths files](#insee-french-deaths-files), downloaded once and read with no request at all. It is slow on purpose and cached, because FamilySearch suspends accounts for volume: one of ours was suspended in 2026 after about 15 parallel lanes called the API through one night ([hard-won lesson 1](hard-won.md#1-volume-got-a-familysearch-account-suspended-and-an-ip-blocked)).

It never logs in and never asks for a password. PARES, Genae, the BOE and the INSEE files need no account. For FamilySearch you sign in in your own browser, and the tool uses that session's token. Standard library only.

## The reader shape

[gwt-mh](myheritage.md) (MyHeritage) and `gwt-read` (FamilySearch, PARES, Genae, BOE, INSEE) write the same files, from `read/gwt_reader.py`:

| File | What it holds |
|---|---|
| `<out>/<key>.json` | the envelope: `url`, `final_url`, `captured_at`, and the answer as `text` (a page) or `json` (an API answer), plus the site's own keys (gwt-mh adds `html`, `graphql`, `plan`) |
| `<out>/<key>.candidates.json` | `{"candidates": [...]}`, the normalized records [gwt-rank](rank.md#the-candidate-record) reads |
| `manifest.tsv` | one row per file saved beside a record (an original image, a record page), in the columns [gwt-rawstore](rawstore.md) keeps |

Each command prints the lines a lane needs for its ledger row, one per line, tab-separated:

```text
source	familysearch
evidence	runs/fs-ana-zorvalo-1851-1a2b3c4d5e.json
candidates	runs/fs-ana-zorvalo-1851-1a2b3c4d5e.candidates.json
results	2 total, 2 read
```

A reader never writes the ledger: the lane runs `gwt-ledger check` before the search and `gwt-ledger add` after, with that `source` and `evidence`.

Every request goes the same way (the INSEE source makes none). A fresh answer in [gwt-fscache](fscache.md) is served from disk with no network call and no wait. Only on a miss does the request wait for its slot in [gwt-gate](gate.md), then leave. An answer that is not a result stops the run: nothing is written, nothing is cached, and the gate hears about it so every lane on the account pauses.

| The site answers | gwt-read does | Exit |
|---|---|---|
| HTTP 2xx with the JSON it expects, or a page with the record text it asked for | writes the envelope and the candidates | 0, or 2 with no results |
| HTTP 403 | blocks the account's gate for 24 h | 3 |
| HTTP 429, a captcha or block page | pauses every lane (20 min) and triples the gap | 3 |
| HTTP 200 with an empty body, a page that is not JSON from a JSON site, or a page without the record text from a page site (an empty frame, an Imperva block inside an iframe, the site's own error page) | read as a block: pauses every lane | 3 |
| HTTP 401, or a sign-in page | pauses every lane; get a fresh token | 4 |
| JSON of an unexpected shape, or another HTTP error | nothing saved, the gate is not told | 1 |
| the gate is paused | no request at all | 6 |

## FamilySearch

### The session token

FamilySearch has no API key for personal projects ([hard-won lesson 4](hard-won.md#4-the-official-api-is-not-an-option-for-personal-projects)). Sign in at familysearch.org in a normal browser, then copy the value of the `fssessionid` cookie (in Chrome: Developer tools, Application, Cookies). Put it in a file only you can read and point `GWT_FS_TOKEN_FILE` at it, or put it in `GWT_FS_TOKEN`. It is sent as a Bearer token. It never reaches a file the tool writes, a cache entry or a log line, and the tests check that.

When the session expires the tool exits 4. Sign in again in the browser and copy the new token. Never script the login: FamilySearch's login blocks automation browsers ([hard-won lesson 5](hard-won.md#5-automation-browsers-get-blocked-at-login)).

### Commands

| Command | What it does |
|---|---|
| `search [--given G] [--surname S [--surname S2]] [--by Y] [--dy Y] [--years N] [--place P] [--father "G S"] [--mother "G S"] [--collection ID] [--count N] --out DIR` | one records search. `--by` and `--dy` are the birth and death years, searched as a range of `--years` either side (default 2). `--place` matches any place in the record. `--collection` is a FamilySearch collection id. `--count` is the rows asked for, 1 to 100 (default 20) |
| `record ARK --out DIR` | one indexed record, by its ark (`1:1:XXXX-XXX`, or the `https://www.familysearch.org/ark:/61903/1:1:...` URL) |

| Option (before the command) | Default | |
|---|---|---|
| `--account NAME` | `$GWT_FS_ACCOUNT` or `default` | names the account; each account gets its own gate |
| `--gate-dir DIR` | `~/.cache/gwt-gate-fs-<account>` | the account's pace, shared by every lane on it |
| `--cache-dir DIR` | `$GWT_FSCACHE_DIR` or `~/.cache/gwt-fscache` | the same cache `gwt-fscache` uses |
| `--lane NAME` | `$GWT_AGENT` or `gwt-read` | who asked, for the gate log |
| `--user-agent UA` | `$GWT_FS_USER_AGENT` or `gramps-web-toolkit (+repo URL)` | the User-Agent header |
| `--refresh` | off | skip the cache; the request still waits for the gate |

The search uses the JSON endpoint the site's own search page calls (`/service/search/hr/v2/personas`), and the record uses the ark URL asking for GEDCOM X JSON. Both answers are turned into candidates by gwt-rank's own GEDCOM X reader, so `gwt-rank` ranks them like any other search. Searches stay in the cache for 7 days and records forever (`FAMILYSEARCH_RULES` in [gwt-fscache](fscache.md#freshness)).

### The default pace, and why

One account starts at one request every **30 seconds** (120 an hour). After an hour without a hit the gate shortens the gap by a fifth, never below one every 20 seconds, and a hit triples it, up to one every 120 seconds. The gate's own defaults (15 s, floor 8 s) are for a machine-wide pace shared by many tools; this one is for one account and stays well under the volume that got an IP blocked in 2026: 461 requests in 42 minutes, one every 4.8 to 6 seconds ([hard-won lesson 2](hard-won.md#2-the-per-ip-ceiling-is-below-about-10-requests-a-minute-sustained)). A repeated search costs nothing, because the cache answers it before the gate is asked.

These numbers are fixed in `gwt-read` (`FS_PACE`); `gwt gate --dir ~/.cache/gwt-gate-fs-default status` shows where an account's pace stands now. After a 403, the gate stays blocked for 24 hours; `gwt gate --dir ... clear <reason>` ends the block early, which you should only do once you know why it happened.

Every 403 counts as a block, even one that only refuses a single restricted record. That can park an account for a day over one record its access level cannot see. We chose it on purpose: from the outside, a 403 for one record looks the same as a 403 for the account or the IP, and guessing wrong the other way is how an account gets suspended. `gwt gate denied` exists for tools that can tell the two apart; `gwt-read` cannot.

### Account safety rules

- **One pace per account.** Every lane that uses an account points at the same `--account` (or `--gate-dir`), so the account sees one slow client, not several polite ones.
- **Never run parallel lanes on one account.** Lanes on the same account share its gate and wait their turn. Do not add accounts to go faster, and do not move to another account or IP to dodge a block.
- **Stop on 429 and 403.** The tool exits 3 and the gate pauses or blocks every lane on the account. Do other work. A search made after a block time that says "no results" is not a negative ([hard-won lesson 3](hard-won.md#3-a-no-result-during-a-block-is-not-a-negative)).
- **Keep a daily budget per lane.** We use about 80 requests a day.
- **Cache first.** Share one cache directory between lanes, so a record any lane has read is never asked for again. The cache does not record which account fetched an answer: a record read with an affiliate library's access is replayed to a free account that shares the cache. Give an account its own `--cache-dir` if that matters.

### What each kind of access sees

FamilySearch decides who sees an index or an image collection by collection, following the record custodian's terms. Clicking the camera icon on a record says where its image can be seen. Rows marked *unconfirmed* are what we have seen or read in community answers, not on a FamilySearch help page.

| Access | Indexes (search results, record details) | Images |
|---|---|---|
| Signed out | No historical-records search; the catalog, books, genealogies and Wiki stay open (FamilySearch help: "Why do I have to sign in to view historical records?", since 13 December 2017) | none through search |
| Signed in, free account | every collection the custodian allows online | the images the custodian allows online; the rest show a camera with a key |
| Affiliate library | as signed in | some restricted images, when the restriction message lists affiliate libraries (FamilySearch help: "What are the image restrictions in Historical Records?") |
| FamilySearch center | as signed in | as an affiliate library, and some images limited to centers; only on a computer the site recognizes as in the center (*unconfirmed*) |
| Church member account | as signed in (*unconfirmed*) | some collections are limited to members of The Church of Jesus Christ of Latter-day Saints (*unconfirmed*) |

Some images can only be seen at the custodian or on a partner site, not at a center or a library. `gwt-read` uses the access of the account whose token it holds, nothing more.

### Not yet

`gwt-read` does not download images or full-text transcriptions. [gwt-fetch](fetch.md) downloads archive images; keeping FamilySearch originals in rawstore with a manifest is a follow-up.

## PARES

PARES (Portal de Archivos Españoles, https://pares.cultura.gob.es) is the catalogue of the Spanish state archives: the Archivo Histórico Nacional, Simancas, the Archivo General de Indias, the Corona de Aragón and others. A search finds archival descriptions (a lawsuit, a file of proofs of nobility, a licence to sail to the Indies), not indexed people. It is public: no account, no token.

### Commands

| Command | What it does |
|---|---|
| `search [--given G] [--surname S [--surname S2]] [--words "W ..."] --out DIR` | the site's simple search: every word anywhere in a description, accents and case ignored. The words are the given name, the surnames, then `--words` |
| `record ID --out DIR` | one description, by its id or its `https://pares.cultura.gob.es/ParesBusquedas20/catalogo/description/<id>` URL |

| Option (before the command) | Default | |
|---|---|---|
| `--gate-dir DIR` | `~/.cache/gwt-gate-pares` | the site's pace, shared by every lane on the machine |
| `--cache-dir DIR` | `$GWT_FSCACHE_DIR` or `~/.cache/gwt-fscache` | the same cache `gwt-fscache` uses |
| `--lane NAME` | `$GWT_AGENT` or `gwt-read` | who asked, for the gate log |
| `--user-agent UA` | `$GWT_PARES_USER_AGENT` or `gramps-web-toolkit (+repo URL)` | the User-Agent header |
| `--refresh` | off | skip the cache; the requests still wait for the gate |

### What a candidate is

A PARES candidate is one description. Its title is the `name` gwt-rank matches the person's names against; a long title the results page cuts is read whole from the link. Each also carries `archive`, `dates` (of the file, as written), `reference` (the signatura) and `url`, and a `record` adds `fields`: every field on the description page, labelled as the site words them. A description is never the record's principal and carries no birth or death, because the dates of a file are not a person's: gwt-rank orders PARES results by how well the names match and never calls one strong. Open the description and read it before any fact goes into the tree.

### Requests, pace and cache

The site answers a search only inside a session. Without one it answered its own error page in our test, so a search that is not in the cache first fetches the search form for the session cookie, then the results: two requests, each waiting for its slot in the gate. The cookie lives in memory for that run only and never reaches the cache or a file. A `record` needs no session and costs one request. Requests ask for Spanish pages (`Accept-Language: es`).

The gate starts at one request every 15 seconds, never faster than one every 10, and a hit triples the gap up to one every 120 (`PACE` in `read/gwt_pares.py`). Searches stay in the cache for 7 days and descriptions for 90 (`PARES_RULES` in [gwt-fscache](fscache.md#freshness)). A page that is not the one asked for (no results block on a search page, no title on a description page) is a STOP like a block: nothing written, nothing cached, every lane pauses.

### Terms we rely on

- robots.txt (read 2026-10-25) has one group, `User-agent: *`, which disallows only `/*docroot/` and some language paths under `/man/`. The search (`/ParesBusquedas20/catalogo/find`) and the descriptions are not disallowed.
- The legal notice (https://pares.cultura.gob.es/comunes/aviso-legal.html) licenses reuse of the portal's content under Real Decreto 1495/2011, including copying and "extracción, reordenación y combinación de la información", on condition that the meaning is not distorted, the source is cited ("Origen de los datos: Ministerio de Cultura") and no endorsement is implied. It has no clause against automated access.
- Some images carry their own restrictions. `gwt-read` reads descriptions only, never images.

### What each kind of access sees

| Access | Descriptions and search | Images |
|---|---|---|
| Anyone, no account | every published description | the digitized images the archive publishes |
| Registered PARES user (free) | the same | the same; registration adds a notebook (agenda) and saved searches (*unconfirmed*) |

PARES has no paid tier.

### Not yet

No advanced search (dates, archive, fonds, reference number) and no second page of results: a search reads the first page the site gives (25 rows). No image download; [gwt-fetch](fetch.md) downloads archive images.

## Genae

Genae (https://genae.org) is a free volunteer index of parish baptisms, marriages and burials of the Region of Murcia and nearby villages of Albacete, Alicante, Almería and Granada. It is public: no account, no token. `genae search` asks the site's own search for the four blocks it shows (baptisms, grooms, brides, burials) and merges them into one candidates file for [gwt-rank](rank.md).

```bash
gwt read genae search --given Ana --surname Zorvalo --province mu --out runs/
```

| Option | Meaning |
| --- | --- |
| `--given`, `--surname` (twice for both surnames) | the name as the site's `n`, `a1`, `a2` fields; at least one is needed |
| `--province` | `mu` (default), `ab`, `al`, `am`, `gr` |
| `--locality` | the site's numeric locality id (its own `l=` field); with it the site leaves out the place column, so candidates carry no place |
| `--from`, `--to` | year range |

Each row becomes a candidate. A baptism gives the child with the parents' first names. A marriage gives two candidates, one per spouse, with the full name only (the site does not split it) and the other spouse as a relative. A burial gives a death event. A marriage both spouses match appears in the groom and the bride blocks; it is written once. Rows the site marks as phonetic suggestions carry `phonetic: true`. gwt-rank reads neither the marriage event nor the phonetic mark, so a groom or bride ranks on names and spouse only, and the lane reads `phonetic` itself. Only the first page of each block is read (15 rows); a block that fills it prints as `15+ (cut: narrow the search)`. The gate keeps one request every 20 seconds at best (`PACE` in `read/gwt_genae.py`), so one search costs about a minute. Searches stay in the cache for 7 days (`GENAE_RULES`).

A page that is not the block asked for (an empty frame, the search form, a captcha) is a STOP: nothing written, nothing cached, every lane pauses. A block the site answers with its notice that no books are indexed for those years in that place is a real answer with no rows.

Genae publishes no terms for scripted use, and its robots.txt disallows `/buscar/` for crawlers. The reader keeps to a person's pace: one search of four requests a minute at most, each answer cached for a week, and it never pages. Genae rows are an index made by volunteers: a hit is a lead, and the act or the image behind it is the evidence.

## BOE and Gaceta de Madrid

The BOE's open-data API (https://boe.es/datosabiertos/api/api.php) answers by date and by law, never by a person's name, and its daily summaries start in 1961. The site's own search (https://www.boe.es/buscar/gazeta.php) takes a phrase in the text of the acts of the Gaceta de Madrid and the BOE. The collection starts in 1661, but the site says its text search works only from 1836 to 1959, and gives approximate results. It is public: no account, no token. `boe search` asks it for one phrase and reads the first page.

```bash
gwt read boe search --name "Ana Zorvalo Quemada" --born 1880 --out runs/
gwt read boe search --name "Ana Zorvalo Quemada" --born 1880 --parents "Juan y Antonia" --pdf-context 2 --out runs/
```

The phrase is always sent in quotes, so the site reads it as one name; it ignores accents. Each result becomes a candidate of role `mention`, never a principal, with the act's title in the note. A mention in an official act (a pardon, a pension, a call for places, an appointment) says a name was printed there. It does not say which person it is, and a common name hits many namesakes, so `gwt-rank` never ranks a mention above `weak`. A row whose title holds the whole name is marked `[name in title]`; the others matched in the act's text only.

| Option | Meaning |
| --- | --- |
| `--name` | given name and surname(s), sent as one phrase; at least two words |
| `--from`, `--to` | first and last publication, a year or `yyyy-mm-dd`; text search covers 1836 to 1959 |
| `--pages N` | read up to N pages of 50 results (1 to 5, default 1); `results` prints how many rows were read and how many the site found |
| `--born YEAR` | the person's birth year: a row published before that year or more than 100 years after it is marked `IMPLAUSIBLE`, never hidden. A child is not ruled out: pension lists name orphans |
| `--parents "Father y Mother"` | the parents' first names the tree holds; with `--pdf-context`, an act that writes the parents after the name (the draft lists do: "Ana Zorvalo, de Juan y Antonia") is marked as the tree's parents or as other ones. Each parent is compared by first given name, so "José María" matches "José" |
| `--pdf-context N` | read the PDF of the first N rows `--born` has not ruled out (0 to 10, default 0), one request each, and add the words around the name to the note; needs `pdftotext` (poppler-utils), and without it nothing is fetched |
| `--near WORD` | mark a row whose title or department holds the word (a place, a trade); it marks, never hides. Repeat for more |
| `--out DIR` | where the envelope and candidates files go; another `--born`, `--parents` or `--near` writes another file |

Every act gets a `verdict` (also at the start of its note), and the search prints the three counts:

- `excluded`: published before the birth or over 100 years after it, or the act writes other parents at every place the name stands.
- `consistent`: the act writes the tree's parents after the name at least once.
- `open`: anything else.

No act is dropped and no verdict is proof. The name must stand as whole words ("Ana Zorvalo" is not inside "Juana Zorvalo"); a rank or title before it ("al Cabo Ana Zorvalo") changes nothing. When the text layer of the PDF does not hold the whole name (edicts and lists split names across columns), the note says `[name not whole in the text layer: open the PDF]`. The BOE prints in two or three columns, so the words around a name can mix lines of the next column; read them as a pointer to the page, never as the sentence.

The search does not index the annexes «Anuncios, avisos y Administración de Justicia» of many years, where edicts and requisitorias sit. Zero hits does not mean the person was never printed: [`gwt-fetch gazeta`](fetch.md#gazeta-the-historical-gaceta-de-madrid) downloads whole issues to search their text. `gwt-read boe` ranks a person's mentions; `gwt-fetch gazeta` lists and downloads acts and issues.

The BOE's robots.txt disallows `/buscar/gazeta.php?` and `/datos/pdfs/` for crawlers. The reader keeps to a person's pace: one search typed by a person at a time, one request every 6 s at best and never faster (`PACE` in `read/gwt_boe.py`), searches cached 7 days and PDFs 90 days (`BOE_RULES`), no parallel requests and no batch mode. The results link each act's PDF under `/datos/pdfs/`; the reader fetches the same file from `/gazeta/dias/yyyy/mm/dd/pdfs/`, which robots.txt allows, and uses the site's link only for a row without a date. To search several names, loop over `search` and rank the files together: `gwt rank runs/*.candidates.json`.

A page that is not a results page (the search form, a captcha) is a STOP: nothing written, nothing cached. So is an HTML page served at a PDF URL.

## INSEE French deaths files

INSEE publishes the "fichier des personnes décédées", every death registered in France since 1970, as one fixed-width text file per year (and per month for the current year), under the Licence Ouverte: https://www.data.gouv.fr/datasets/fichier-des-personnes-decedees/ . You download the years you need once. `insee search` reads those files on your machine and never touches the network, so it has no gate and no cache. Each line whose surname matches a `--surname` becomes a candidate for [gwt-rank](rank.md):

```bash
gwt read insee search --files deces-1985.txt deces-1986.txt --surname Ortega --surname Sanz --out runs/
gwt rank runs/<key>.candidates.json --given Tomás --surname Ortega --facts "by=1921 bp=Villazul"
```

| Option | Meaning |
| --- | --- |
| `--files` | one or more INSEE files, read in the order given |
| `--surname` | a surname to keep. Repeat it: a line matching any of them is kept, so one pass over decades of files serves a whole branch; each line looks its surname up, so hundreds of `--surname` cost about what one does. Case and accents are folded and the surname must stand as whole words: `Ortega` keeps `ORTEGA` and `ORTEGA SANZ`, never `ORTEGAS`. At least one is needed |
| `--out` | where the envelope and candidates files go |

Here `--surname` is a list of surnames to keep, not the first and second surname it is for the other sources. To find who to look for, `gwt-leads --kinds missing-vital --format json` lists everyone with no death event (gap `death-missing`). gwt-leads holds back anyone who may still be alive, and those are often the people a post-1970 deaths file can answer: matching them against a file on your own machine is the private use `--include-living` is for. Never feed that list into searches on outside sites.

The layout is the one the dataset page gives ("Dessin d'enregistrement"), positions counted from 1:

| Positions | Field | Becomes |
| --- | --- | --- |
| 1-80 | name, `NOM*PRENOMS`, often closed by `/` | `surnames` (the part before `*`), `given`, `name` |
| 81 | sex, `1` male, `2` female | `sex` (`M`, `F`) |
| 82-89 | birth date `AAAAMMJJ`, `0000`, `00`, `00` when the year, month or day is unknown | `events.birth.year`, and `birth_date` as written |
| 90-94 | birth place code (the official geographic code; `99xxx` is a country) | `birth_place_code` |
| 95-124 | birth commune as written | `events.birth.place`, and `birth_commune` |
| 125-154 | birth country as written, blank for France | added to `events.birth.place` after a comma, and `birth_country` |
| 155-162 | death date `AAAAMMJJ` | `events.death.year`, and `death_date` |
| 163-167 | death place code | `events.death.place`, and `death_place_code` |
| 168-176 | death act number | `act` |

Every candidate is the `principal` of its line, with role `Principal`. Its `id` is the death date, place code and act number, which is how INSEE itself names a death. The full dates, the codes, the country, the act number, the `file` and its `line` stay in the candidate as fields gwt-rank does not score: gwt-rank scores years, and a year plus the birthplace is already enough for `strong`. A `strong` row is a lead for the verifier, never an identification. The reader writes no change list: the verifier reads the line and writes the [gwt-apply](apply.md) change. INSEE warns that its files are published once and never corrected, and that 1970 to 1975 may have gaps.

Every line of every file is checked, matched or not. A line whose columns do not line up stops the run with exit 2, the file and line in the message, and nothing written: a date that is not eight digits, a sex that is not `1` or `2`, a line too short to hold the death date (162 characters), or longer than two records (352 characters: two records glued on one line, or a file whose lines end in a carriage return alone, which would otherwise lose every record after the first). Real lines are 198 characters. A UTF-8 byte order mark at the start of a file is dropped. Blank lines are skipped. The death place code and the act number may be cut short, since the dataset page warns that some lines end in meaningless characters.

Some lines break INSEE's own rules with every column still in place. Dates are kept as written: INSEE's files hold days that never existed (29 February of a common year). They also hold a month 13 (1996), a day 60 (1991) and a full 80-character name with no `*` (2012), one line each in those years. These are what INSEE wrote, so the line is kept, never skipped and never fatal: the year is the date's first four digits, a name with no `*` is the surname whole with no given name, and the candidate lists the field under `"malformed"` (`["birth_date"]`, `["name"]`). The envelope gives each file's count of such lines under `malformed`, matched or not, and stderr names the file and the count.

The files are ASCII in practice: names and communes in upper case, without accents. The columns count characters, not bytes. A few lines, mostly foreign birthplaces, are not ASCII, and the encoding differs between years:

| Seen in | Encoding of the non-ASCII lines | Examples |
| --- | --- | --- |
| 2005, 2010 | the DOS code page CP850 | 0xA5 Ñ, 0xC7 Ã, 0xE0 Ó, 0xF8 the degree sign in `PARIS 12°` |
| 1999 | CP850 and Latin-1 lines mixed in one file | Latin-1 0xF1 ñ, 0xD1 Ñ, 0xB0 °, 0xE9 é |
| 2021, 2022 | a few UTF-8 lines | `°`, `²`, `é` |

So each line is read as UTF-8 when it is valid UTF-8 (plain ASCII is). Otherwise it is read both as CP850 and as Latin-1, and the reading with more accented letters of the French, Spanish and Portuguese alphabets (and `°`) wins; a tie goes to CP850. Such a candidate carries `"encoding": "cp850"` or `"encoding": "latin-1"`: its odd characters are our guess, so read them against the file.

The envelope lists each file with its sha256, its line count and how many lines matched, and the surnames asked for. The `results` line prints `N of M lines in K files`. | Exit | Meaning | Files written |
| --- | --- | --- |
| 0 | at least one line matched | envelope and candidates |
| 2 | no line matched | envelope and an empty candidates file |
| 2 | a line out of the layout (the message names the file and line) | none |
| 1 | a file that cannot be read, or holds no line (a failed download) | none |
| 64 | no `--surname` | none |

So exit 2 means "none found" only when the candidates file was written. A download cut short at a line boundary cannot be seen from the file alone: compare the sha256 and line count the envelope lists for each file with the download.

## Tests

```bash
cd read && python3 -m unittest -v test_gwt_read test_gwt_pares test_gwt_genae test_gwt_boe test_gwt_insee
```

Invented people, descriptions and ids (the PARES pages in `read/fixtures/` keep the site's markup with invented content), a fake transport and a fake gate: the tests make no request to any site. They check that a search writes an envelope and candidates `gwt-rank` ranks, that a repeated search is served from the cache with no network call, that the gate is asked before every request, that a 403, 429, captcha, sign-in page or empty frame stops the run with nothing written or cached, and that the token never appears in a written file or a log line. The INSEE tests use invented fixed-width lines (`read/fixtures/insee-deces-1985.txt`, and lines built in the test). They check that the source asks for no gate, cache or network, and that each malformed line exits 2 with nothing written.
