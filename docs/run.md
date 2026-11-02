# gwt-run

Writing a change list safely takes five commands: a backup, a dry run, the apply, a verify, and a check that the tree has no new problems. People and agents ended up writing that chain again for every project, as shell scripts that read gwt-apply's human output, and each copy broke in its own way: a new output line read as a failure, a read that hung for half an hour with nothing to say, an apply stopped halfway by a busy server and finished by hand. `gwt-run` is that chain as one command. It reads only the JSON the steps write, ends a step that hangs, and can be run again after any stop: it goes on from the first step that is not done.

Standard library only, plus what `gwt-apply` and `gwt-check` need. Each step is a `gwt-apply` or `gwt-check` run of its own, with the same `--config` file.

## Run a list

```sh
gwt run changes.jsonl --config gw.env [--known known.json] [--tag r12] [--dir DIR]
```

| Step | What it runs | Stops the run when |
|---|---|---|
| `backup` | `gwt-apply --save-snapshot DIR/pre.json.gz`, then reads the file back whole and keeps its md5 | the read fails or the file does not decompress |
| `dry_run` | `gwt-apply LIST --snapshot DIR/pre.json.gz`, offline on the backup | an op is a conflict or an error, or a skip that is not a no-op (see below) |
| `apply` | `gwt-apply LIST --apply --log DIR/apply.jsonl` | an op is a conflict, or the ops still fail after `--apply-tries` whole tries |
| `verify` | `gwt-apply LIST --verify` | an op is not in the tree |
| `gate` | `gwt-check check --from DIR/pre.json.gz --baseline DIR/pre.json.gz [--known K]` | gwt-check finds a problem the tree did not have before the run |

`DIR` defaults to the list's path plus `.run` (`changes.jsonl.run/`). It holds `run.json`, the backup, each step's JSON and its full output (`backup.out`, `apply.out`, ...). The backup is a gwt-apply snapshot: it is what `gwt-apply --revert DIR/apply.<run>.jsonl --pre DIR/pre.json.gz` needs to undo the run (see [Undo a run](apply.md#undo-a-run)). `--server-backup` also asks the server for its own backup before the first write, once per run.

### Skips in the dry run

A first dry run of a new list should find nothing already done. A skip whose op finds the tree already as it asks (gwt-apply calls it a no-op, `"noop": true` in its JSON) is harmless and never stops the run. Any other skip, such as `already made by key`, means the list or part of it ran before; the run stops at the dry run unless that op's key is named with `--accept-skip KEY` (repeatable).

### Run it again

`run.json` records every step's status. Running the same command again skips the steps marked `ok` and goes on from the first that is not; a run that finished prints `done before` and writes nothing. The backup is never taken again once it is done, so it stays the tree as it was before the first write.

An apply that stops halfway (a busy server answering `database is locked`, a dropped connection) is tried again by itself up to `--apply-tries` times (default 3), `--retry-wait` seconds apart. Each try reads the tree again, so what is already written reads as a skip and nothing is written twice; removals the earlier tries logged as done read as landed (gwt-apply reads its own logs in `DIR`). A conflict is not tried again: the tree changed under the list.

A list edited after the run began is refused (exit 2): it is a new run, so give it a new `--dir`. Two `gwt-run` on one `DIR` at once are refused too.

### A step that hangs

A step that prints no line and makes no API call (as its log in `DIR` shows) for `--stall` seconds (default 600) is ended, and the run exits 5 with the last thing the step did:

```text
gwt-run: backup: no progress for 600 s; last line: 'snapshot: GET /people/ page 7 (3000 Person so far)'
```

The whole-tree read in `gwt-apply --save-snapshot` prints one such line before every page for this reason. The verify also has a deadline, `--verify-timeout` (default 900 s), and is tried `--verify-tries` times (default 2). A step is ended with SIGTERM first, so a gwt-apply that holds the tree lock releases it.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | every step went through |
| 1 | an unexpected failure (a backup that does not read back, a step that crashed) |
| 2 | bad input: a missing list, a list changed since the run began, another gwt-run on the same folder, a list gwt-apply refuses to read |
| 3 | the dry run refused the list; nothing was written |
| 4 | the apply did not finish: a conflict, or failures after every try |
| 5 | a step showed no progress for `--stall` seconds, or the verify ran past its deadline |
| 6 | the verify found ops not in the tree |
| 7 | the gate found problems the tree did not have before the run |

## run.json

```json
{
 "format": "gwt-run/1",
 "list": "/work/changes.jsonl",
 "list_sha256": "9f2c...",
 "status": "stopped",
 "exit": 3,
 "steps": {
  "backup": {"status": "ok", "md5": "41d8...", "objects": {"Person": 1204, "Family": 410}},
  "dry_run": {"status": "refused", "refused": ["[conflict] u-anna: conflict: no Person with Gramps ID I0099"]},
  "apply": {"status": "todo"},
  "verify": {"status": "todo"},
  "gate": {"status": "todo"}
 }
}
```

A step's `status` is `todo`, `running`, `ok`, `refused`, `failed` or `stalled`. The apply step also keeps `tries` and the counts of its last try; the run's own `why` says in words where it stopped.

## Options

| Option | Default | |
|---|---|---|
| `--dir DIR` | `LIST.run` | the run's folder |
| `--config FILE` | | GWT_URL, the login and GWT_TREE, for every step |
| `--tree ID` | | the tree the login must land in (GWT_TREE for every step) |
| `--known FILE` | | a gwt-check known file: findings the gate accepts |
| `--tag NAME`, `--private-new` | | passed to every gwt-apply step |
| `--server-backup` | off | also ask the server for a backup before the first write |
| `--accept-skip KEY` | | a dry-run skip that may pass (repeatable) |
| `--retries N`, `--retry-wait S` | 5, 30 | gwt-apply's tries per op and wait; the wait is also the pause between apply tries |
| `--apply-tries N` | 3 | whole apply runs before giving up |
| `--verify-tries N`, `--verify-timeout S` | 2, 900 | verify runs and the deadline of each |
| `--stall S` | 600 | seconds with no progress before a step is ended |
