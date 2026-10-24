# gwt-mh: MyHeritage records through a signed-in browser

`gwt-mh` searches MyHeritage's historical records, opens record pages, and keeps everything a record gives on our own storage: the record data, the citation, the collection's metadata and, where the plan allows it, the original image. It works on a free account and does more on a paid one. It finds out which plan the account has from what the pages show, never from a setting.

```sh
pip install playwright        # over CDP no browser download is needed
G="myheritage/gwt-mh --cdp http://<chrome-host>:9223 --storage-state ~/.config/mh/state.json --account ana"
$G search --given Ana --surname Zorvalo --by 1851 --exact --out runs/        # -> runs/<key>.json + <key>.candidates.json
rank/gwt-rank runs/<key>.candidates.json --given Ana --surname Zorvalo --facts "by=1851"
$G fetch --collection 12040 --item 800001- --search runs/<key>.json --out records/
$G record --collection 10182 --item 70001 --out records/
$G plan                       # what this account's plan has been seen to allow
```

## 1. What it is, and why a browser

MyHeritage sits behind Imperva. A logged-out browser context from our addresses got an Imperva bot page on every research, record and catalogue page. Plain HTTP with copied cookies would risk a challenge on the account itself. So every request goes through a real Chrome, reached over CDP, inside a context signed in as the account. The tool opens the same URLs a person opens and reads the answers from inside the page.

Searches are read from the site's own search JSON, not from the page text. Opening a `/research?...action=query` URL makes the page call `POST /web-family-graphql/search_in_historical_records_split/`, and the tool captures that answer with a response listener. It never builds GraphQL calls and never handles the bearer token. The answer is structured: id, name, link, collection (`is_free`, `has_images`, record count), thumbnail and the display fields. The server already hides what the plan does not cover. A hidden value comes back as a link to `/paywall?...subscription_type=data` with the word `Día`, `Mes` or `Lugar`. The letters the page shows in its place are decoration, so text scraping would read nonsense.

Logging in is per account and private (vault, a code mailed to the owner, which box), so it is not in the toolkit. A login script writes the account's Playwright state file (mode 600), and `gwt-mh --storage-state FILE` opens a fresh context of its own from it. It never attaches to a context already open in that Chrome, because that context may belong to another tool or agent. A session is never copied to another account or tool.

| File | What it is |
|---|---|
| [`myheritage/gwt_mh.py`](../myheritage/gwt_mh.py) | Pure logic, standard library: URLs, cache key, search JSON and record page parsers, walls, plan inference, image headers, manifest, slugs, mirror verdict |
| [`myheritage/gwt_mh_browser.py`](../myheritage/gwt_mh_browser.py) | The only module that imports Playwright: gated, cached page capture, byte fetches, screenshots |
| [`myheritage/gwt-mh`](../myheritage/gwt-mh) | The CLI |

It reuses [gwt-gate](gate.md) (one gate per account, `start=17 floor=10 ceil=60`, plus a random 0-8 s), [gwt-fscache](fscache.md) (cache key with the plan in it), [gwt-rank](rank.md) (it writes the `{"candidates": [...]}` shape), [gwt-ledger](ledger.md) and [gwt-rawstore](rawstore.md), without changing any of them.

## 2. Plans

Prices read on 2026-10-24 from https://www.myheritage.es/pricing for Spain, in EUR per year, VAT excluded, first year then renewal.

| Plan | Price | Tree | What it is for |
|---|---|---|---|
| Basic | free | 250 people | search, a small tree |
| Premium | 59 / 89 | 2,500 | tree plan: Smart Matches, advanced DNA |
| PremiumPlus | 89 / 139 | unlimited | adds Instant Discoveries, consistency checker |
| Data | 89 / 129 | 250 | every historical record and its images, Record Matches |
| Complete | 129 / 199 | unlimited | PremiumPlus plus Data |
| Omni | 159 / 269 | unlimited | Complete plus OldNews.com, Geni Pro, webinars |
| OldNews Pro | 99 USD | (separate site) | full newspaper pages on OldNews.com |

The monthly tree backup ("Copia de seguridad") is a separate add-on at 5 EUR a month.

## 3. Free vs paid

*Observed* means we saw it on an account with that plan in October 2026: Basic on a free account, Omni on a paid one. *Documented* means MyHeritage's pricing page or help says so and we did not see it. Premium, PremiumPlus, Data and Complete were not observed directly. The Basic observations come from a single run on one free account on 2026-10-24; a second check of the original-image and OldNews rows is still due, and that account was locked for 24 h by the attempt to make it (see Traps).

| Feature | Basic | Premium, PremiumPlus | Data | Complete | Omni |
|---|---|---|---|---|---|
| Search lists | Every collection searchable. Name, birth and death year, collection or tree owner, some relatives. Day, month, place, parents and spouse masked by the server. *Observed* | As Basic, except collections from MyHeritage's own family sites, members and trees. *Documented* | Every field. *Documented* | Every field. *Documented* | Every field, with parents, spouses, children. *Observed* |
| OldNews entries in search | OCR snippet, summary, newspaper, date and place of publication, the person's place and occupation, all unmasked. *Observed* | As Basic. *Documented* | As Basic. *Documented* | As Basic. *Documented* | As Basic. *Observed* |
| Record page (`showRecord`) | Only collections marked free; any other ends at `/paywall`, even typed directly. *Observed* | Only MyHeritage family sites, members, trees. *Documented* | Every collection. *Documented* | Every collection. *Documented* | Every collection, with citation and collection blurb. *Observed* |
| Original record image | No viewer, no download. Search gives a 96 px thumbnail; its URL hash is tied to the size, so other sizes answer 403. *Observed* | No. *Documented* | View and download "hi-res scans ... when available". Many collections are indexes with no image. Resolution not measured. *Documented* | Same as Data. *Documented* | Same as Data; no image viewer seen yet, resolution not measured. *Documented* |
| OldNews full page, OCR, download | OldNews.com shows the page 2178×3072 but blurred at JPEG quality 20 through a signed URL; removing the filter answers 400, the bare path 403. No OCR, no download. *Observed* | No. *Documented* | Only newspapers that were on MyHeritage before OldNews.com launched in March 2024. *Documented* | Same as Data. *Documented* | Included: record pages say "incluido en su plan". Format and resolution not measured. *Observed* (the link) |
| Record Matches | List per person and source, other fields masked. "Revisar coincidencia" goes to the paywall. *Observed* | List only. *Documented* | Review, confirm, extract. *Documented* | Same as Data. *Documented* | Review button shown (never clicked). *Observed* |
| Smart Matches | The match, the other site and its manager are listed; review goes to `/paywall?...reason=178`. *Observed* | Review. *Documented* | Not included. *Documented* | Review. *Documented* | Listed. *Observed* |
| Instant Discoveries | List and detail readable with full dates and names; accepting not tested. The pricing table puts it in PremiumPlus and up. *Observed* | PremiumPlus only. *Documented* | Not included. *Documented* | Yes. *Documented* | Yes. *Documented* |
| Collection catalogue | Every collection, 50 per page over 35 pages (1,738 in October 2026), with category, count, last update and an images tag. *Observed* | Same. *Documented* | Same. *Documented* | Same. *Documented* | Same. *Documented* |
| Tree size | 250. *Observed* (a tree over it shows a warning on every page) | 2,500 / unlimited. *Documented* | 250. *Documented* | Unlimited. *Documented* | Unlimited. *Documented* |
| GEDCOM export | Export form with photos available (not started). *Observed* | Yes. *Documented* | Yes. *Documented* | Yes. *Documented* | Yes. *Documented* |
| DNA match list | Matches with shared %, cM, segments; chromosome browser page. *Observed* | Plus advanced DNA tools. *Documented* | Not the advanced tools. *Documented* | Advanced tools. *Documented* | Advanced tools. *Documented* |

FamilySearch-sourced collections on MyHeritage (the FamilySearch Family Tree, collection 40001, about 1.18 billion profiles) were masked on Basic like any other paid collection. We found no free tier for them. Free MyHeritage access exists for Latter-day Saint members through FamilySearch partner access, and as Library Edition on site at FamilySearch centers and many public libraries.

## 4. What a paid plan adds to gwt-mh

| Plan | `gwt-mh` gains |
|---|---|
| Basic | `search` with names, years, relatives' names and OldNews snippets; `record` on free collections; `fetch` keeps the search data, a built citation, the collection metadata and the thumbnail, all marked not original; `collections`; `matches` lists |
| Premium, PremiumPlus | nothing for records |
| Data, Complete | `record` on every collection; `fetch` saves the original image where the collection has one; searches return unmasked days, places and parents, so `gwt-rank` can score places and the mirror test can compare them |
| Omni | adds the full OldNews page image and its OCR text |

## 5. Plan detection

Every capture adds observations `{at, signal, value, url}` to `plan.json` in the cache dir. The capabilities come from them, and the newest observation of each signal wins, so a lapsed or renewed plan is picked up.

| Capability | true when | false when |
|---|---|---|
| `records` | a non-free collection outside OldNews shows a day or a place in an event, or a non-free `showRecord` opens | any masked field on a search page, or a `showRecord` ending at `/paywall` |
| `images` | the record page of a collection with images gives a download link or an image | it gives none |

`images` is kept per collection (`collection` in the observation) and the capability is only a summary. It never decides whether `fetch` tries an image: on a plan with `records` true, every record of a collection with images gets the image step. One index-only collection therefore never switches images off for the others.
| `oldnews_pages` | the record page says the full page is "incluido en su plan" / "included in your plan" | only the blurred page is served |
| `tree_review` | (not observed yet) | a review ends at `/paywall?...reason=178` |

The label is `basic` (records false), `data+` (records true), `omni` (records and OldNews pages), else `unknown`, which behaves as Basic. It goes into every manifest row and into the cache key, so a masked Basic answer is never served to a paid account. OldNews results are excluded from the positive signal because they show places even on Basic.

## 6. Safe use

- One session per account, one gate per account (`--gate-dir`, default `~/.cache/gwt-gate-mh-<account>`). The session takes `session.lock` in the gate dir; a second session on the same account stops with exit 3. The kernel drops the lock when the process dies.
- A random 10-25 s between page loads (the gate's 17 s start, down to 10 s after an hour without a hit, plus 0-8 s), and a 2-4 min rest every `--rest-every` loads (default 45). An Omni account searched 148 times at that pace with no wall.
- On a wall, stop. The Imperva texts (`_Incapsula_Resource`, `Incapsula incident ID`, `Security check`, the Spanish `Control de Seguridad`, an hCaptcha), "actividad inusual" / "unusual activity", and the account lockout ("El acceso se ha desactivado temporalmente ... actividad irregular" / "temporarily disabled ... irregular activity") become a gate hit and exit 3. They are matched without regard to case and before the `/login` test, because the lockout is served on `/login`. A logged-out page (`/login`, a verification-code prompt, `Iniciar sesión` with no account menu) becomes exit 4. The tool never clicks anything on a wall; a person looks.
- **Never log in again after a wall.** The lockout lasts 24 h, and each new login attempt from the same account or address during it can extend it. Three logins in one day from one address got a free account locked and the address an Imperva hCaptcha. After exit 3, make no request on that account or from that address for at least 24 h. Exit 4 allows one new login, never a retry loop.
- Read only. The tool opens URLs and reads them. It has no code path that saves a record, confirms or rejects a match, uploads, sends a message, starts a trial or buys. `matches` lists cards and never opens a review.
- Never share, export or copy a session or its cookies between accounts or tools.
- Session tokens never reach disk. The page HTML carries several (`fgToken`, `webClientFgToken`, `mhXsrfToken`, `csrf_token`) besides the search's `bearer_token`; every envelope is scrubbed before it is cached or written.

Exit codes: 0 ok, 2 nothing parsed, 3 wall, lockout, gate paused or another session on the account, 4 session logged out, 5 paywall (recorded as plan evidence; nothing else saved unless `fetch --search` has the search record).

## 7. Storage: the most we can keep

`fetch` writes `DIR/<collection>/<item>/` and a `manifest.tsv` with `file kind url sha256 size width height plan original fetched_at collection_id item_id`. Kinds, in the order tried:

1. `record-html`, `record-text`, `record-json` (every field parsed from the page), `normalized` (the `gwt-rank` record), `citation`, `collection` (name, record count, description, provider). On Basic, where the record page is paywalled, `record-json` is the search record itself, the citation is built from it, and `collection` comes from the search's collection block.
2. `image`: the record page's image download link fetched with the session (at most two links, only ones on MyHeritage's image CDN or naming a record image or scan; app, paywall, GEDCOM and installer links are skipped), or else the largest image the page loaded, by pixel area. Tried on every record of a collection with images once `records` is true.
3. `oldnews-page` and `oldnews-ocr`: the unblurred page from OldNews.com and its text, only when `oldnews_pages` is true. The blurred page Basic gets is never saved; it adds nothing to the thumbnail.
4. `thumbnail`: the search thumbnail, `original=no`.
5. `screenshot-fallback`: only when step 2 or 3 fails on a plan that allows it, `original=no`.

Width and height come from the image header. Files are written as `*.part` and renamed. Then `gwt-rawstore put --remove` moves the HTML and image kinds to the bucket (`--no-rawstore` skips it); the `.txt`, `.json` and `.tsv` files stay in git.

The OldNews image path names the newspaper, date, issue and page (`<title>/<YYYY>/<MM>/<DD>/<issue>/<page>.jpg`). The normalized record keeps that as `mh.image_hint`, which is often enough to find a free copy of the same page in a national library.

Not measured yet: how a paid account serves image bytes, and at what size. Steps 2 and 3 fall back to a marked screenshot when they fail, so nothing is lost before that measurement.

## 8. Search output and the ledger

`search` writes the envelope (`<key>.json`: URL, final URL, page text, HTML, captured search JSON) and `<key>.candidates.json`, and prints the ledger `source` and `evidence` path. Sources: `myheritage`, `myheritage-oldnews` (collection 12040 and other OldNews collections), and `myheritage-espejo-fs-<cid>` for a collection that mirrors a FamilySearch one. The ledger compares sources as exact slugs, so a mirror row never counts as the FamilySearch search done. `gwt-mh` never writes the ledger; the lane runs `gwt-ledger check` before and `add` after.

Each normalized candidate is the matched person (`principal`), with relatives under `relatives` (`father`, `mother`, `parent`, `spouse`, `child`, `other`). A masked value is `null` or `""` and its field is listed in `mh.masked`, so a verifier can tell "hidden on this plan" from "not in the record". Extra fields stay under `mh` and the ranker ignores them.

## 9. The FamilySearch-mirror test

Some MyHeritage collections are copies of FamilySearch indexes. `mirror-check` decides whether one can stand in while FamilySearch is unavailable. Input: a JSON list of about 20 FamilySearch records we already have (`name` or `given`/`surnames`, `event`, `year`, `place`, `father`, `mother`). For each, it runs an exact search in the MyHeritage collection and compares name, year, place and parents, only where both sides show the field. The row in `mirrors.tsv` is `cid fs_collection n found agree date verdict plan compared`:

- `equivalent`: at least 90% found and every found one agrees on every compared field;
- `partial`: at least half found;
- `not`: otherwise.

Only `equivalent` lets MyHeritage go first for that collection, under `myheritage-espejo-fs-<cid>`; the FamilySearch row stays due. On Basic, place and parents are masked, so the verdict rests on name and year, and the `compared` column says so. A verdict reached on a paid plan is judged again before it is trusted on Basic.

## 10. FamilySearch, for a follow-up

FamilySearch gives original images for many collections at no cost; some are limited to signed-in users, affiliate libraries or FamilySearch centers. Our FamilySearch tools download images ([gwt-fetch](fetch.md)) but do not yet keep the image, the record JSON and the citation in rawstore with a manifest like this one. Aligning them is a follow-up.

## Traps

- MyHeritage's 24 h lockout ("El acceso se ha desactivado temporalmente ... actividad irregular") appears on `/login` after a password is sent. Read as "logged out" it invited another login, which keeps the lock going. It is a wall now. Repeated logins from one address in one day also bring an Imperva hCaptcha on every page; a re-check belongs on another day, from an address that has not just logged in.

- OldNews search results show places on a free account. Counting them as paid evidence labelled a free account `data+`.
- The record page puts the place on its own line under the date, starting with a space and a no-break space.
- A credentialed in-page `fetch` to the image CDN fails on CORS; image bytes fall back to the context's request client, which sends the same cookies.
- The catalogue cards show no region or years; the site filters those through the page, and the tool does not guess that filter's parameters.

## Tests

```sh
cd myheritage && python3 -m unittest -v test_gwt_mh
```

Synthetic fixtures only, no network, no browser. They cover the normalizer (checked against `gwt_rank._bad_record`), plan inference and its precedence, walls, the cache key and token scrubbing, image paths and headers, the manifest, fetch planning, ledger slugs, the mirror verdict, the record page from saved HTML and from page text, match cards and the catalogue.
