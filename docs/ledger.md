# gwt-ledger: the "already searched" ledger

When several research agents work the same tree at once, they repeat each other's searches. A negative search costs as much as a positive one and leaves nothing in the tree, so nobody sees it was done. `gwt-ledger` is one TSV file every agent reads before a search and appends to after it.

Standard library only, Python 3.8 or newer. Run it as `ledger/gwt-ledger` (symlink it onto your `PATH` if you like) or `python3 ledger/gwt_ledger.py`.

## The file

One row per search, tab-separated, with a header line:

| column | meaning |
|---|---|
| `person_key` | slug of the person searched, e.g. `ana-peiro-toledo` (or a tree id) |
| `person_label` | readable name |
| `source` | slug of where: `familysearch`, `gaceta`, `ad33`, `newspapers`. Names ending in `-verify`, `-sweep` or `-followup` are reserved for the [research loop](agent-loop.md), which logs those task kinds apart from plain searches; `any` stands for a task with no source |
| `query` | the search, normalized: lowercase, no accents, no punctuation |
| `date` | ISO date of the search |
| `result` | `hit`, `none`, `partial`, `blocked` (could not search: a site block, or an authorization you lack; the evidence file says which) or `denied` (the office refused the request) |
| `evidence` | file that holds the result or the negative |
| `agent` | who searched |
| `next_date` | optional ISO date to look again; for an appeal, a few days before the deadline, so there is time to act |

The path comes from `--ledger`, else `$GWT_LEDGER`, else `./buscado.tsv`. The agent name defaults to `$GWT_AGENT`.

## Commands

```sh
export GWT_LEDGER=research/buscado.tsv GWT_AGENT=lane-7

# Before a search. Exit 0 and the matching rows (score first) if it was done; exit 1 if not.
gwt-ledger check --person "Ana Peiro Toledo" --source familysearch \
  --query "Villanueva del Campo baptisms 1801-1850 Ana Peiro"

# After it, whatever came of it.
gwt-ledger add --person "Ana Peiro Toledo" --label "Ana Peiro Toledo" --source familysearch \
  --query "Villanueva del Campo baptisms 1801-1850 Ana Peiro" --result none \
  --evidence research/lane-7/negatives.md

gwt-ledger list --person ana-peiro-toledo [--source familysearch]
gwt-ledger stats

# Refused with an appeal window: log it with the date to look again, then list what is due.
gwt-ledger add --person ... --source ... --query ... --result denied --next-date 2026-11-10
gwt-ledger due            # rows whose next_date is today or earlier; --on YYYY-MM-DD for another day
```

A ledger written with the older 8 columns needs no upgrade: its rows read with an empty `next_date`, and new rows are appended with the ninth column.

In a script: `gwt-ledger check ... >/dev/null && echo "already done, skip"`.

## Matching

Person keys and sources compare as slugs, so `Ana Peiró Toledo` and `ana-peiro-toledo` are the same. `--person` and `--source` are optional on `check`; leave them out to look across the whole ledger.

Queries match fuzzily. The score is the best of three measures:

- word-set overlap (Jaccard);
- character similarity (`difflib`);
- for a new query of three words or more, the share of its words already in a logged query. A short query inside a longer logged one counts as done.

Before any of that, every word of the new query must already be in the logged one. Particles (`de`, `la`, `y`, `the`…) don't count, and a word of five letters or more may differ by one inserted, dropped or changed letter. Words with digits (years, film and catalogue numbers) must match exactly, and one logged word never covers two words of the new query. Without this gate, two searches of one catalogue that share a long prefix and differ in one surname (`Real Chancilleria de Granada: Toledo` after `Real Chancilleria de Granada: Peiro Villanueva`) scored over 0.8, and the second search was skipped as already done.

The default threshold is 0.75 (`--threshold` to change it). Reworded, reordered or re-accented queries still match. A different collection, place, name or date range does not.

## Traps

- **Log the search, not the result.** The query is what you typed into the source: collection or site, film or years, name ("St Mary baptisms film 004400001 Ana Peiro"). Two long write-ups of the same search do not match each other. On a real ledger, the round-30 write-up and the round-25 write-up of the same film and the same images scored under 0.5 against each other. The same search as a short query scored 1.00 against both.
- **Seeding from prose guesses the person.** A seeder that pulls names out of free text also catches place names: a town called "Villa Nueva" became a person key. Many negatives cover a whole collection and name nobody, so they have no person key. Run `check` without `--person` for collection-level searches, or you miss those rows. A negative that names two people must be filed under each of them: our first seeder used only the first name it matched, so a check for the second person found nothing.
- **A negative from a blocked period is not a negative.** Log it as `blocked`, not `none`, so the next agent repeats it.
- **The ledger in git:** agents that share one checkout also share its git index. Commit the ledger with `git commit -- <ledger path>`, or rows another agent staged go into your commit.

## Concurrency

`add` takes an exclusive `flock` on the ledger file, writes the header if the file is empty, appends one line, and calls `fsync` before it unlocks. Tabs and newlines inside values become spaces, so a row is always exactly one line. The test suite starts 16 processes that append 25 rows each at the same moment, then checks that all 400 rows are present and well formed.

`flock` works between processes on one machine. Agents on several machines need the file on one host, or a separate ledger per host merged afterwards.

### One file per lane

When the ledger lives in git and several lanes commit to the same repository, one shared file is a problem even with the lock: a lane's new rows sit uncommitted in a file the other lanes also change. With `--ledger-dir DIR` (or `$GWT_LEDGER_DIR`), `add` writes to `DIR/<agent>.tsv` instead, so each lane only ever changes its own file, and `add` refuses to run without an agent name. `check`, `list`, `due` and `stats` read the `--ledger` file plus every `DIR/*.tsv`, so a row one lane logged counts for all of them, and an older shared ledger keeps counting after the switch.

```sh
export GWT_LEDGER=research/buscado.tsv GWT_LEDGER_DIR=research/ledger.d GWT_AGENT=lane-7
```

## Tests

```sh
python3 -m unittest discover -s ledger
```
