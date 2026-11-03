---
name: gramps-web-toolkit
description: Change a Gramps Web family tree safely with the gramps-web-toolkit command-line tools, in the order that keeps guesses and duplicates out of it. Use when asked to add, correct, merge or undo people, events, sources or citations in a Gramps Web tree, to write or apply a change list, to run gwt apply, gwt run, gwt lint or gwt check, or to research records for a tree with gwt ledger, gwt gate or gwt corpus.
---

# gramps-web-toolkit

Every write to the tree goes through a change list that `gwt apply` reads. The tool checks the list, plans each op against the tree, logs every write call and stops at the first conflict. A direct call to the Gramps Web REST API skips all of that, so never write to the API yourself.

This file gives the order of steps and the traps. Options live in `gwt <tool> --help`. The full rules live in the docs: https://geiserx.github.io/gramps-web-toolkit/

## Steps

1. **Connect.** The tools come from `pipx install gramps-web-toolkit` (Python 3.11+). Put `GWT_URL`, `GWT_USERNAME` and `GWT_PASSWORD` (or `GWT_TOKEN`) and `GWT_TREE` in a `KEY=VALUE` file outside git. Pass it with `--config`. Setup: https://geiserx.github.io/gramps-web-toolkit/getting-started/#connect-to-your-tree
2. **Snapshot.** `gwt apply --save-snapshot pre.json.gz --config gw.env` reads the whole tree into a new file. Take it while no other list is being applied. Lint and the dry run read this file, so they need no server.
3. **Write the change list.** One JSON op per line, in `changes.jsonl`. Every op has a `key` (unique, stable, such as `birth-ana-example-1890`) and a `ruling` (the decision it comes from). A reference is a Gramps ID such as `I0001`, or `key:<key>` for an object this list or an earlier run made. Dates: `1890`, `1890-03`, `1890-03-14`, optionally after `abt`, `bef` or `aft`; ranges `bet 1890 and 1895`. Ops, fields and every date form: https://geiserx.github.io/gramps-web-toolkit/apply/#the-change-list
4. **Lint.** `gwt lint changes.jsonl --snapshot pre.json.gz --config lint.toml`. Done at exit 0. `lint.toml` names the rules to run and gives each its setting: the root person for `scope`, the protected IDs for `protect`, the list folders for `overlap`, the weak tag for `weak`. Without them those rules have nothing to check and the run exits 2. How to write it: https://geiserx.github.io/gramps-web-toolkit/lint/#settings
5. **Dry run.** `gwt apply changes.jsonl --snapshot pre.json.gz`. Read the whole plan. Done when it shows 0 conflict and 0 error, and any skip is named on the `NOOP:` line (printed only when there are skips).
6. **Write, only when the user asked for it.** `gwt run changes.jsonl --config gw.env --tag r12` takes a backup, dry-runs, applies, verifies and checks the whole tree, in one command. Add `--private-new` when new objects may concern living people.
7. **Report** the run folder (`changes.jsonl.run/` by default), the tag, and the verify result. To prove later that the list is still in the tree: `gwt apply changes.jsonl --verify --config gw.env`. To find problems in the whole tree: `gwt check check pre.json.gz` (a dump or snapshot; without one it reads the live tree). Read the JSON files the tools write (`run.json`, `--json`) rather than their printed text.

## Traps

- **A dry run's exit 0 proves nothing landed.** `gwt apply` without `--apply` exits 0 even when every op would still write. Exit 0 only means no conflict and no error. Only `--verify` proves a list is in the tree: it exits 1 and prints `missing:` or `differs:` for each op that is not.
- **Exit 2 from `gwt lint` is not clean.** It means a rule had nothing to check (a missing `--root` or `--protect`, say), or an input could not be read, or the list itself is invalid. 2 wins over 1. Give the rule its setting in `lint.toml`. Leave a rule out with `--rules` only when it really does not apply to this list, never just to reach exit 0.
- **A namesake is usually the same person.** When the dry run flags a new person who matches someone in the tree, the fix is almost always to use the existing Gramps ID. Set `allow_namesake` only when the documents show two different people. It needs `namesake_of` (every ID you compared) and a `namesake_reason` that says what tells them apart. The same goes for `allow_new_couple` on families.
- **A wrong birth or death is corrected, never doubled.** Use `update_event` on the event the person has. When the person already has two events for one fact, join them with `merge_event`. An `add_event` for a second birth or death is a conflict.
- **Touch only what the ruling covers.** Do not tidy names, dates or places on the way past.
- **A skip in a first dry run means the tree already holds that object.** A skip that is not on the `NOOP:` line was either made by this list's key on an earlier run (`already made by key`), or the op matched an object the tree had (`reused ...`, `already has that child`). Read the skip line. If the list ran before, find out which run made it before you go on. If the match is expected, pass `gwt run --accept-skip KEY`, or the run stops at its dry run (exit 3).

## gwt run: stops and exit codes

When `gwt run` stops, read the `why` in `run.json` inside the run folder, fix the cause, and run the same command again. Before the apply wrote, it starts over. After that, it goes on from the first step that is not done. A list edited after the apply wrote is a new run: give it a new `--dir`.

| Exit | Meaning |
|---|---|
| 2 | bad input: a missing list, a list changed after the apply wrote, another tree than the run began on |
| 3 | the dry run refused the list; nothing was written |
| 4 | the apply did not finish: fix the cause it names (a conflict, a refusal before the first write), or run the same command again after a busy server |
| 5 | a step showed no progress for `--stall` seconds, or the verify ran past `--verify-timeout` |
| 6 | the verify found ops not in the tree (`missing:` and `differs:` lines) |
| 7 | the whole-tree check found new problems; accept expected ones with `--known` |

Exit 1 is an unexpected failure. Details: https://geiserx.github.io/gramps-web-toolkit/run/#exit-codes

## One writer per tree

`gwt apply --apply` takes a lock inside the tree (a private note with Gramps ID `GWTLOCK`) and keeps renewing its 15-minute lease while it works. A second apply on the same tree stops and names the holder's run, host and process. Wait for it. A lock whose holder died expires when its lease runs out. Use `--break-lock` only after you have checked, on the holder's own machine or container, that its process ended. A PID checked on your machine proves nothing.

Do not edit the tree by hand in the web UI while an apply runs.

## Undo one run

`gwt apply --revert LOG --pre pre.json.gz` prints the plan to undo the run that wrote `LOG`. Add `--apply` to undo it. `--pre` must be a snapshot of the same tree taken right before that run. For a `gwt run`, the snapshot is `pre.json.gz` in the run folder, and the logs are the `apply.<run id>.jsonl` files beside it, one per apply try. Revert each one, newest first, with the same `pre.json.gz`. Steps the revert marks `manual` (a merge, a person or family the run made, a media upload) need the full backup. Details: https://geiserx.github.io/gramps-web-toolkit/apply/#undo-a-run

## Research before writing

- Search what is already on disk first: `gwt corpus search "Ana Example"`. Build or refresh the index with `gwt corpus update ROOT`. 0 hits there means "not downloaded yet", never "does not exist".
- Before a search on a site: `gwt ledger check --exact --person P --source S --query Q`. Exit 0 means it already ran with a result that stands (a hit, or a proven `none`, not due again). Exit 1 means search. After it, log it with `gwt ledger add`, empty results too. A `none` needs `--control-ok` with a control run that proved the site could answer; without it the row is stored as `unproven`.
- Pace every request through `gwt gate wait LANE`. On a 429, a captcha or a block page, call `gwt gate hit LANE` so every lane pauses.
- A "no result" while a site blocks or throttles you is not a negative. Log it as `blocked` and search again later.

The whole loop: https://geiserx.github.io/gramps-web-toolkit/agent-loop/ and the lessons behind it: https://geiserx.github.io/gramps-web-toolkit/hard-won/#3-a-no-result-during-a-block-is-not-a-negative

## Living people

What the tools do:

- `gwt apply --private-new` and `gwt run --private-new` save every object the run creates as private, so a guest login does not see it. Objects that already exist keep their own setting.
- `gwt lint` rule `living` flags a full date, a place, a long quote or a marriage of someone who may be alive, and an identity number in any new text.
- `gwt check public` compares an owner dump and a guest dump of one tree and exits 1 when a guest still sees something.

Our advice, not enforced by any tool: before a change list, log or report leaves the machine, replace the names and Gramps IDs of living people in it.
