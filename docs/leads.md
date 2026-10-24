# gwt-leads: turn the gaps of a tree into queue tasks

[gwt-queue](queue.md) holds what to search next. Somebody has to decide what that is. In the pipeline the judge did it by reading weak-points tables and branch depth by hand, every round. `gwt-leads` reads a tree dump offline and lists the gaps, ranked, ready to seed the queue. It never touches the tree.

Standard library only. Run it as `leads/gwt-leads DUMP` where `DUMP` is the file `gwt-check dump` writes: `{tree, dumped_at, objects: {people, families, events, places}}`, gzipped or not. A file in any other shape, a dump with no people, or a record with no handle exits `2` with a message instead of printing an empty list.

`gwt-check dump` pages the whole tree. Do not take a dump while that tree's writer is applying a change list.

## What it finds

| kind | the gap |
|---|---|
| `branch-end` | a person with no birth parents recorded: where a line stops |
| `missing-vital` | no birth or baptism; no death or burial for someone born in `--living-after` or earlier; a birth or death (or its stand-in) with no date or no place; a family with children and no marriage event |
| `uncited` | a birth or death (or its stand-in) with no citation: a fact nobody can check |

`--kinds` picks a subset. Only a person's own events count, the same way `gwt-check` and `gwt-apply` read them:

- An event ref counts only with role Primary. A godparent's ref to a child's baptism is not the godparent's baptism.
- The birth is the event `birth_ref_index` points at, else the first Birth, else Baptism, else Christening. The death is the event `death_ref_index` points at, else Death, else Burial, else Cremation. The gap text names the type it used: `baptism has no place`.
- Parents are the parents of a family the person is a Birth child of. An adoptive, step or foster family does not continue the line.

A date counts as present when it has a structured year. A date with only text ("before the war") is reported as `date text without an interpretable year`, with the text, never as a missing date. A homonym or an unverified source is not a gap: the tool only reports what is missing from the tree, never what the tree should say.

## Who is held back

Every output format leaves out:

- anyone marked private in Gramps, and
- anyone who may be living: no Death, Burial or Cremation event (with or without a date), and born after `--living-after` or in a year the tree does not know. The default is this year minus 100. A baptism year counts as the birth year.

Their Gramps ids, never their names, go to stderr in one line. `--include-living` turns the hold off; use it only for a private review on your own machine, never for a list that feeds searches on outside sites. Private events never feed the facts below.

## Priority

With `--root I0001` (a Gramps id), the root's ancestors rank first: 100 minus one per generation, never below 40. Everyone else is 10. That puts the tips of the direct lines ahead of collateral branches, the same order the pipeline's sweep waves use. Without `--root` every gap is 10. Priority belongs to the person, so all of one person's gaps share it.

## Output

- `--format tsv` (default): `priority`, person id, label, kind, what. Tabs and newlines inside a value become spaces.
- `--format json`: one JSON object per gap, with `gap` (a stable code such as `birth-place` or `branch-end`), `generation`, `weak` and `facts`.
- `--format queue`: one JSON line per task for `gwt-queue add --jsonl`. One task per person and task kind (`search` for branch ends and missing vitals, `verify` for uncited facts), with every gap in the note. `--per-gap` gives one task per gap instead.

`--limit N` keeps the N highest-priority rows; in queue format it counts tasks.

### Queue tasks

```bash
gwt-check dump tree.json.gz
gwt-leads --root I0001 --source SRC --format queue --limit 200 tree.json.gz > seeds.jsonl \
    && gwt-queue add --jsonl seeds.jsonl --dedupe
gwt-queue report
```

Write the seeds to a file and chain with `&&`: piped into another command, a bad dump's exit 2 is lost.

- The **query** is what a lane types into a search: the name, the birth year and the birth place, `Ana Peiró 1850 Villa`.
- The **note** lists the gaps by code, and starts with what the tree already knows: `facts: bp=San_Juan,_Villa by=<1860 dy=1933 | branch-end: no parents recorded`. Keys are `b`, `bap`, `d` or `bur` (birth, baptism, death, burial) plus `y` or `p`. A year is plain only for a regular, exact date. An "about", range or estimated year reads `~1860`, a "before" year `<1860`, an "after" year `>1860`. A place is the place and the one it sits in. Spaces become `_`, and `%`, `|`, `;`, `=` and `_` are percent-encoded, so the note can be split on ` | ` and `=` safely.
- The **dedupe key** is the person, the task kind and `--source`, plus the gap code with `--per-gap`. The name is not part of it, so a corrected name does not make a second task. With `--dedupe`, `gwt-queue` skips a task that is already queued, running or done, and on an open one it takes the new label, note and priority, so a re-seed carries the latest gaps. Keep the same mode and `--source` across rounds: grouped and per-gap keys differ, and so do keys with different sources.
- `--skip-weak` leaves out people with no given name or no surname (or none at all), unless they are the root or one of its ancestors. Their ids go to stderr. A thin name makes a poor outside search, but an ancestor stays on the list.

`--source`, `--per-gap` and `--skip-weak` only apply to `--format queue`; with another format they are an error.
