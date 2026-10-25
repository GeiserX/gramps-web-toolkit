# gwt-read

```bash
export GWT_FS_TOKEN_FILE=~/.fs-session        # your own session token, mode 600 (see below)
gwt read familysearch search --given Ana --surname Zorvalo --by 1851 --place Villazul --out runs/
gwt rank runs/<key>.candidates.json --given Ana --surname Zorvalo --facts "by=1851 bp=Villazul"
gwt read familysearch record 1:1:QZZ1-AAA --out runs/
```

`gwt-read` runs a FamilySearch historical-records search, or reads one indexed record, and writes the answer in the shape every record-site reader in the toolkit shares. It is slow on purpose and cached, because FamilySearch suspends accounts for volume: one of ours was suspended in 2026 after about 15 parallel lanes called the API through one night ([hard-won lesson 1](hard-won.md#1-volume-got-a-familysearch-account-suspended-and-an-ip-blocked)).

It never logs in and never asks for a password. You sign in to FamilySearch in your own browser, and the tool uses that session's token. Standard library only.

## The reader shape

[gwt-mh](myheritage.md) (MyHeritage) and `gwt-read` (FamilySearch) write the same files, from `read/gwt_reader.py`:

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

Every request goes the same way. A fresh answer in [gwt-fscache](fscache.md) is served from disk with no network call and no wait. Only on a miss does the request wait for its slot in [gwt-gate](gate.md), then leave. An answer that is not a result stops the run: nothing is written, nothing is cached, and the gate hears about it so every lane on the account pauses.

| The site answers | gwt-read does | Exit |
|---|---|---|
| HTTP 2xx with the JSON it expects | writes the envelope and the candidates | 0, or 2 with no results |
| HTTP 403 | blocks the account's gate for 24 h | 3 |
| HTTP 429, a captcha or block page | pauses every lane (20 min) and triples the gap | 3 |
| HTTP 200 with an empty body or a page that is not JSON (an empty frame) | read as a block: pauses every lane | 3 |
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

One account gets at best one request every **30 seconds** (120 an hour), never faster than one every 20 seconds, and after a hit one every 120 seconds. The gate's own defaults (15 s, floor 8 s) are for a machine-wide pace shared by many tools; this one is for one account and stays well under the volume that got an IP blocked in 2026: 461 requests in 42 minutes, one every 4.8 to 6 seconds ([hard-won lesson 2](hard-won.md#2-the-per-ip-ceiling-is-below-about-10-requests-a-minute-sustained)). A repeated search costs nothing, because the cache answers it before the gate is asked.

To change the pace for an account, drive its gate directly, for example `gwt gate --dir ~/.cache/gwt-gate-fs-default status`. After a 403, the gate stays blocked for 24 hours; `gwt gate --dir ... clear <reason>` ends the block early, which you should only do once you know why it happened.

### Account safety rules

- **One pace per account.** Every lane that uses an account points at the same `--account` (or `--gate-dir`), so the account sees one slow client, not several polite ones.
- **Never run parallel lanes on one account.** Lanes on the same account share its gate and wait their turn. Do not add accounts to go faster, and do not move to another account or IP to dodge a block.
- **Stop on 429 and 403.** The tool exits 3 and the gate pauses or blocks every lane on the account. Do other work. A search made after a block time that says "no results" is not a negative ([hard-won lesson 3](hard-won.md#3-a-no-result-during-a-block-is-not-a-negative)).
- **Keep a daily budget per lane.** We use about 80 requests a day.
- **Cache first.** Share one cache directory between lanes, so a record any lane has read is never asked for again.

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

## Tests

```bash
cd read && python3 -m unittest -v test_gwt_read
```

Invented people and ids, a fake transport and a fake gate: the tests make no request to any site. They check that a search writes an envelope and candidates `gwt-rank` ranks, that a repeated search is served from the cache with no network call, that the gate is asked before every request, that a 403, 429, captcha, sign-in page or empty frame stops the run with nothing written or cached, and that the token never appears in a written file or a log line.
