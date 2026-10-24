# gwt-queue: what to search next, for every lane at once

[gwt-ledger](ledger.md) remembers what was already searched. Nothing remembered what comes **next**: each lane picked its own targets, the planner's priorities lived in a brief nobody else could read, and a lane that died mid-task took its claimed work to the grave. `gwt-queue` is one TSV file that holds the pending research tasks, hands them out to lanes one at a time, and reopens the ones a dead lane dropped.

Standard library only, Python 3.8 or newer. Run it as `queue/gwt-queue` (symlink it onto your `PATH` if you like) or `python3 queue/gwt_queue.py`.

## The file

One row per task, tab-separated, with a header line:

| column | meaning |
|---|---|
| `id` | `q1`, `q2`, ... short enough to name in a brief |
| `person_key` | slug of the person to research (or a tree id) |
| `person_label` | readable name |
| `kind` | `search` (one source, one query), `sweep` (one person, every source), `verify` (check a claim against its source), `followup` (act on a hit) |
| `source` | slug of where: `familysearch`, `gaceta`, `ad33`; empty for a sweep |
| `query` | the search, normalized like the ledger does |
| `priority` | integer, higher goes first; suggested: 100 direct ancestor, 50 their sibling, 10 collateral |
| `status` | `open`, `claimed`, `done`, `failed` |
| `lane`, `claimed_at` | who holds it and since when |
| `done_at`, `result`, `evidence` | how it ended; `result` uses the ledger's words: `hit`, `none`, `partial`, `blocked`, `denied` |
| `tries` | attempts so far: a claim adds one, a `release` gives it back, so it counts the attempts that ran |
| `next_at` | not claimable before this instant (a back-off or a cool-down) |
| `note` | free text: why it waits, what a hit should trigger |
| `token` | the claim's fencing token, new on every `claim`; `renew`, `done`, `fail` and `release` must present it |
| `lease_until` | when the claim runs out unless `renew` pushes it out; set by `claim --lease-minutes` (default 120) |
| `dedupe_key` | optional: when set, `--dedupe` matches on this instead of person, kind, source and query |

The queue path comes from `--queue`, else `$GWT_QUEUE`, else `./cola.tsv` in the current directory. Times are UTC; a value with no zone is read as UTC.

Keep the file untracked, and on one host. Every command rewrites it whole under a local `flock`, which does not hold across git clones or across machines.

## The loop

A planner (a person, a judge, or `gwt-leads`) seeds the queue. Every lane then loops:

```bash
while row=$(gwt-queue claim --lane lane-2); do
    id=$(cut -f1 <<<"$row"); token=$(cut -f17 <<<"$row")
    # ... run the task, write the evidence file; on a long task, now and then:
    gwt-queue renew --id "$id" --token "$token"
    gwt-queue done --id "$id" --token "$token" --result hit --evidence hits/ana-peiro-fs.txt
done
```

- `claim` takes the highest-priority task that is open and due (at equal priority the older one, `q2` before `q10`), marks it yours with a lease, and prints the row. Column 17 is the claim's token. Exit 1 means nothing is due: the lane is finished, or waits for the next wave.
- `renew` pushes the lease out again. A lane on a task longer than its lease must renew, or the task reopens under it.
- `done` works only on a task that is `claimed` under your token. A lane whose claim went stale and was handed to another lane holds a dead token: its `done`, `fail` and `release` are refused (exit 2) and change nothing. `fail` and `release` follow the same rule, so a finished task is never reopened. `done` closes the task with a result and the evidence file. Mirror the row into `gwt-ledger` so the negative (or the hit) is never searched twice: the queue tracks the work, the ledger tracks the search.
- `fail` is for an attempt that ran and could not finish: a site block mid-search, a missing login. The task reopens with a back-off of `--backoff-minutes` (default 60) per attempt so far. After `--max-tries` attempts (a top-level option, default 5) it becomes `failed` and waits for a person to read it.
- `release` is only for "nothing ran": the gate was paused, the lane is shutting down before it searched. It reopens the task and gives the try back. `--retry-after MINUTES` keeps it unclaimable for a while, so a lane that keeps finding the gate paused does not re-claim the same task at once. Anything that touched the source must `fail` instead.
- `report` is the open-leads list: what is due now, by priority, then how much waits and how much failed. Read it at the start of a round; the judge reads it too.

Two lanes never receive the same task: every command runs under an exclusive lock on `<queue>.lock`, and the queue file is replaced atomically (temp file, fsync, rename), so a crash leaves the old queue or the new one, never a half-written file. Keep the `.lock` file next to the queue; do not delete it while lanes run. A lane that dies holding a claim loses it when the claim's own lease runs out: the next `claim`, `report` or `stats` reopens the task. The lease is stored on the row, so no caller can revoke a live claim early. The dead attempt keeps its try, so a task that kills every lane that takes it parks as `failed` after `--max-tries`, like one that keeps failing, and shows up in `report` and `stats`.

## Seeding

`add` one task, or many with `--jsonl FILE` (`-` for stdin): one JSON object per line with `person` and any of `label`, `kind`, `source`, `query`, `priority`, `note`, `next_at` and `dedupe_key`. A batch runs under one lock and one write, and a bad line stops it before anything is written.

`--dedupe` skips a task that is already queued, running or done: the same `dedupe_key` when either side has one, else the same person, kind, source and query. A `failed` match is skipped too, unless `--retry-failed` is given. On an open match the new label, note and priority replace the old ones, so a re-seed carries the latest picture.

```bash
gwt-queue add --person "Ana Peiró Toledo" --label "Ana Peiró Toledo" \
    --kind sweep --priority 100 --note "direct line, round 31" --dedupe
```

`--dedupe` only knows this queue. A search logged in [gwt-ledger](ledger.md) from outside the queue is not seen, so check the ledger before a lane runs a task.

Keep the queue next to the ledger: `buscado.tsv` says what was searched, `cola.tsv` says what is still to do.
