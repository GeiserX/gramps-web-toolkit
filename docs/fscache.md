# gwt-fscache

FamilySearch and similar sites block an account or an IP address after too many requests. Research agents ask for the same record, image or film listing again and again. `gwt-fscache` keeps every good answer on disk, so a repeated request costs zero network calls and does not use up the rate limit.

Standard library only, Python 3.8 or newer.

## What it stores

- **Key:** SHA-256 of method + URL + the request headers named in `vary` (default `accept`), plus a hash of the body for a POST. `authorization` is never part of the key, so a rotated token still hits.
- **Entry:** `<dir>/<2 hex>/<key>.body` (the raw bytes) and `<key>.json` (method, URL, status, response headers, fetch time). The body is written first, then the metadata, each by atomic rename, so a reader never sees half an entry. Directories are created mode `0700`.
- **Only 2xx answers.** A 403, 429, 5xx or a failed connection (status 0) is returned to the caller and never stored. A block is never replayed as if it were the answer.
- **No secrets on disk.** Request auth headers are not stored; `set-cookie`, `authorization` and `www-authenticate` are dropped from the stored response headers. The bodies are still the user's own authenticated data: keep the cache directory private.

## Freshness

TTLs go by URL pattern, first match wins. `None` means the entry never expires. The defaults (`FAMILYSEARCH_RULES`):

| URL pattern | TTL |
|---|---|
| `/ark:/61903/1:` (indexed records), `/ark:/61903/3:` (images) | never |
| `/service/records/storage/`, `/sls/image/records/`, image and PDF files | never |
| `/search/filmdatainfo/` (images in a film) | 30 days |
| `/platform/tree/`, `/tree/` | 1 day |
| `/search/`, `/service/search/` | 7 days |
| anything else | 7 days |

Pass `rules=[(regex, seconds_or_None), ...]` and `default_ttl=` for another site. `GENAE_RULES` keeps a [gwt-read genae](read.md#genae) search for 7 days. `BOE_RULES` keeps a [gwt-read boe](read.md#boe-and-gaceta-de-madrid) search for 7 days and an act PDF for 90 days. `PARES_RULES` is the set [gwt-read pares](read.md#pares) uses:

| URL pattern | TTL |
|---|---|
| `/catalogo/search` (the search form, fetched only for its session cookie) | 0, never served |
| `/catalogo/description/` (one archival description) | 90 days |
| `/catalogo/find?` (a search) | 7 days |

## As a module

Your fetcher has the signature `fetch(method, url, headers, data) -> (status, headers, body)`.

```python
from gwt_fscache import FSCache

cache = FSCache('~/.cache/gwt-fscache')              # GET only by default
status, headers, body = cache.fetch('GET', url, {'accept': 'application/json'}, None, my_fetch)

cached = cache.wrap(my_fetch)                        # drop-in replacement with the same signature
status, headers, body = cached('GET', url, {'accept': 'application/json'})

post_cache = FSCache('~/.cache/gwt-fscache', methods=('GET', 'POST'))   # opt in for deterministic POSTs
```

`cache.lookup(...)` returns the entry or `None` without calling anything. Put your own pacing or rate gate inside `my_fetch`: a hit returns before the fetcher runs, so it skips the gate too.

## As a CLI

```sh
export GWT_FSCACHE_DIR=~/.cache/gwt-fscache
export GWT_FSCACHE_TOKEN_FILE=~/.config/my-session-token     # or GWT_FSCACHE_TOKEN=...

fscache/gwt-fscache get 'https://api.example.org/ark:/61903/1:1:ABCD-123' > record.json
fscache/gwt-fscache get -H 'Accept: image/jpeg' -o page.jpg 'https://api.example.org/ark:/61903/3:1:XYZ9-77'
fscache/gwt-fscache get --offline <url>     # cache only; exit 4 on a miss (useful while blocked)
fscache/gwt-fscache get --refresh <url>     # ignore the cache, fetch, store again
fscache/gwt-fscache stats
```

The cache directory is `--dir`, else `$GWT_FSCACHE_DIR`, else `~/.cache/gwt-fscache`. `-H`/`--header` adds a request header and `-o`/`--output` writes the body to a file instead of stdout.

stderr says `HTTP 200 HIT` or `HTTP 200 MISS`. The exit code is 0 for a 2xx and 1 otherwise. The token is only read on a miss.

## Traps

- **Setup in the client defeats the cache.** The first client we wrapped opened its network tunnel at import time, so even a hit set up a connection. Put connection setup, pacing and the rate gate in the miss path, inside the fetcher.
- **Keep auth out of the key.** Session tokens rotate. A key that includes `authorization` misses on every new session, and the cache fills with copies of the same answer.
- **Retries go inside the fetcher.** The cache then sees one final answer per request. A 5xx retried outside the fetcher is just a 5xx, and it is not stored.
- **A 200 can still be a wall.** Only the status decides what gets stored. If a site answers a block or an expired session with a 200 page (a captcha, a login form), that page gets cached. FamilySearch answers those with 401, 403 or 429, which are not stored. For other sites, check the body in your fetcher and return a non-2xx status for a wall. `--refresh` overwrites a bad entry.
- **Empty is not an error.** A 2xx with an empty result (for example, an image with no transcription) is cached on purpose. It is the answer.

## Tests

```sh
python3 -m unittest discover -s fscache
```

The tests use a counting fake fetcher. They prove that 50 repeats of a cached URL make exactly one call. They also patch `urllib.request.urlopen` to fail on any use and check that a hit never reaches it. They check that no error status is ever stored, that records and images survive ten years of clock while searches expire after seven days, that a token rotation still hits, and that no token or cookie reaches disk.
