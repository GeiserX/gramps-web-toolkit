# gwt-read wikitree: WikiTree

[WikiTree](https://www.wikitree.com/) is a free shared family tree that covers the whole world, with one profile per person. Its API (https://github.com/wikitree/wikitree-api) answers without a login for public profiles. A profile is what other researchers believe, not a record: a candidate here is a lead to a person and to the sources the profile cites, never evidence on its own.

## Commands

```sh
gwt read wikitree search [--given G] [--surname S] [--born 1851] [--died 1920] [--birth-place "Villazul"] \
                         [--count 25] [--start 0] --out DIR
gwt read wikitree record Surname-123 [--ancestors N] --out DIR
gwt read wikitree control
```

- `search` needs `--given` or `--surname`. `--surname` matches the surname at birth or the current one. With `--born` or `--died` the search keeps only profiles that have the dates (`dateInclude=both`); without it, WikiTree also returns every profile with no date at all.
- `record` takes a WikiTree ID (`Smith-1234`) or its profile URL. With `--ancestors N` (1 to 10) one request reads the profile and N generations of its ancestors.

## Public profiles only

Every call sends `appId=gramps-web-toolkit`, as WikiTree asks of every client. The tool never logs in. For a private profile the API gives only its id; such a profile never becomes a candidate. The `results` line and the envelope's `withheld` field count them.

## What a candidate is

One public profile: `id` the WikiTree ID, the given and middle names, the surnames at birth and current, birth and death events (year and place as typed; an unknown date `0000-00-00` is no year), `sex`, the dates as written, `url` (`https://www.wikitree.com/wiki/<ID>`), and `living: true` when WikiTree marks the person living. With `--ancestors`, a parent who is also in the answer and public is listed as a `father` or `mother` relative. A private parent is never named.

## When it stops

"None" is the API's own count: `total` 0 with no match, or its own `Invalid WikiTree ID` for a record. Any other answer is a STOP and nothing is written or cached: a non-zero `status`, a renamed result container, or a count with no rows. A 5xx or no answer is an outage: exit 3, and the gate pauses every lane.

## Pace, cache and terms

- The gate (`~/.cache/gwt-gate-wikitree`) waits 3 s between requests, never less than 2 s. WikiTree asks clients to be gentle and does not publish a limit.
- Profiles are edited every day: a search is cached 1 day, a profile 7 days.
- No key and no login. Profile content belongs to its contributors under WikiTree's terms; link the profile and cite its sources, not the profile text.

## Canary

`--surname Smith`, at least 10 public profiles.
