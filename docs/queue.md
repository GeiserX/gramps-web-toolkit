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
| `tries` | how many times it was claimed |
| `next_at` | not claimable before this instant (a back-off or a cool-down) |
| `note` | free text: why it waits, what a hit should trigger |
| `token` | the claim's fencing token, new on every `claim`; `done`, `fail` and `release` must present it |

The queue path comes from `--queue`, else `$GWT_QUEUE`, else `./cola.tsv` in the current directory.

## The loop

A planner (a person, a judge, or `gwt-leads`) seeds the queue. Every lane then loops:

```bash
while row=$(gwt-queue claim --lane lane-2); do
    id=$(cut -f1 <<<"$row"); token=$(cut -f17 <<<"$row")
    # ... run the task, write the evidence file ...
    gwt-queue done --id "$id" --token "$token" --result hit --evidence hits/ana-peiro-fs.txt
done
```

- `claim` takes the highest-priority task that is open and due, marks it yours, and prints the row. The last column is the claim's token. Exit 1 means nothing is due: the lane is finished, or waits for the next wave.
- `done` works only on a task that is `claimed` under your token. A lane whose claim went stale and was handed to another lane holds a dead token: its `done`, `fail` and `release` are refused (exit 2) and change nothing. `fail` and `release` follow the same rule, so a finished task is never reopened. `done` closes the task with a result and the evidence file. Mirror the row into `gwt-ledger` so the negative (or the hit) is never searched twice: the queue tracks the work, the ledger tracks the search.
- `fail` is for "could not run now": a site block, a missing login. The task reopens with a back-off that grows with `tries` (`--backoff-minutes`, default 60). After `--max-tries` claims (default 5) it becomes `failed` and waits for a person to read it.
- `release` gives a claimed task back with no penalty, for a clean hand-off between lanes.
- `report` is the open-leads list: what is due now, by priority, then how much waits and how much failed. Read it at the start of a round; the judge reads it too.

Two lanes never receive the same task: every command runs under an exclusive lock on `<queue>.lock`, and the queue file is replaced atomically (temp file, fsync, rename), so a crash leaves the old queue or the new one, never a half-written file. Keep the `.lock` file next to the queue; do not delete it while lanes run. A lane that dies holding a claim loses it after `--stale-minutes` (default 120): the next `claim` or `report` reopens the task. A task that keeps failing does not loop forever; it parks as `failed` and shows up in `report` and `stats`.

## Seeding

`add` one task per row, or pipe a list in from a planner script. `--dedupe` skips a task that is already open or claimed for the same person, kind, source and query, so a seeder can run blindly after every round:

```bash
gwt-queue add --person "Ana Peiró Toledo" --label "Ana Peiró Toledo" \
    --kind sweep --priority 100 --note "direct line, round 31" --dedupe
```

Keep the queue next to the ledger: `buscado.tsv` says what was searched, `cola.tsv` says what is still to do.
