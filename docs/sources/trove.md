# gwt-read trove: Australian newspapers

[Trove](https://trove.nla.gov.au/), from the National Library of Australia, holds Australian newspapers from 1803 into the 1950s and later, and government gazettes, cut into articles with their OCR text: family notices (births, marriages, deaths), funerals, probate and court news, shipping arrivals. This source reads its v3 API, which needs a personal API key.

## Your API key

Trove gives a free API key for non-commercial use to anyone with a Trove account: sign in at https://trove.nla.gov.au/ and apply for one from your account's settings. The tool never creates one and never logs in. Put it in the environment:

```sh
export GWT_TROVE_KEY=...            # yours; never commit it
```

The key is sent only as the `X-API-KEY` header, added inside the fetcher, so it never reaches [gwt-fscache](../fscache.md), the evidence files or the URL. Without `GWT_TROVE_KEY` every command exits 4 with a message and makes no request. A 401 (a wrong, expired or revoked key) exits 4 too, says Trove refused the key, and the gate hears `dead`, which pauses every trove lane for 20 minutes. Fix the key, then end the pause yourself, or a rerun with the right key exits 6 until it runs out:

```sh
gwt gate --dir ~/.cache/gwt-gate-trove clear "new key"
```

Never retry in a loop.

## Commands

```sh
gwt read trove search --query '"Ann Smith" AND funeral' [--from 1890] [--to 1910] [--category "Family Notices"] \
                      [--state Victoria] [--count 1-100] [--start CURSOR] [--no-text] --out DIR
gwt read trove control
```

- `--query` is Trove's query syntax: words, `"a phrase"`, `AND`, `OR`. `--from` and `--to` (years) add a `date:[...]` range and put the query in brackets, `("Ann Smith" AND funeral) date:[...]`, so an `OR` in it never escapes the range.
- `--category` keeps one article category (Trove's names: "Family Notices", "Article", "Advertising" ...), `--state` one state of publication.
- Each article comes with its full text unless `--no-text`, in the same request.
- When there are more articles, the search prints a `next<TAB><cursor>` line; pass that cursor as `--start` for the next page.

## What a candidate is

A row is one article, never a person. Its candidate has the newspaper's title as `name`, role `mention`, and is never `principal`. It carries `url` (the article on trove.nla.gov.au), `date`, `year`, `title` (the article's heading), `type` (its category), `page` and `text`, the article text without markup (the snippet when the article has no text). The envelope's `text` holds every row's text, for a corpus.

## When it stops

"None" is the API's own count: `total` 0 in a well-shaped answer. A missing newspaper category, a missing or non-numeric total, or a total with the articles gone is an answer of the wrong shape: exit 1, nothing written. A cursor past the last article is the end (exit 2). HTTP 429 is a wall (exit 3, the gate pauses every lane); a 5xx or no answer is an outage, exit 3.

## Pace, cache and terms

- The gate (`~/.cache/gwt-gate-trove`) waits 2 s between requests, never less than 1 s; Trove allows about 200 requests a minute per key.
- A search is cached 7 days.
- Use is under Trove's API terms: non-commercial, with attribution to the National Library of Australia and the contributing library.

## Canary

`--query Smith`, at least 10 articles. Smith is the most common surname in Australia, with millions of newspaper mentions.
