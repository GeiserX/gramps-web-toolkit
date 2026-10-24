# gwt-leads: turn the gaps of a tree into queue tasks

[gwt-queue](queue.md) holds what to search next. Somebody has to decide what that is. In the pipeline the judge did it by reading weak-points tables and branch depth by hand, every round. `gwt-leads` reads a tree dump offline and prints one task per gap, ranked, ready to pipe into the queue. It never touches the tree.

Standard library only. Run it as `leads/gwt-leads DUMP` where `DUMP` is the file `gwt-check dump` writes: `{tree, dumped_at, objects: {people, families, events, ...}}`, gzipped or not. A file in any other shape, or a dump with no people, exits `2` with a message instead of printing an empty list.

## What it finds

| kind | the gap |
|---|---|
| `branch-end` | a person with no parents recorded: where a line stops |
| `missing-vital` | no birth event; no death event for someone born in 1900 or earlier; a birth or death with no date or no place; a family with children and no marriage event |
| `uncited` | a birth or death event with no citation: a fact nobody can check |

`--kinds` picks a subset. A date counts as present when it has a structured year. A date with only text ("before the war") is reported as `date text without an interpretable year`, with the text, never as a missing date. A homonym or an unverified source is not a gap: the tool only reports what is missing from the tree, never what the tree should say.

## Priority

With `--root I0001` (a Gramps id), the root's ancestors rank first: 100 minus one per generation, never below 40. Everyone else is 10. That puts the tips of the direct lines ahead of collateral branches, the same order the pipeline's sweep waves use. Without `--root` every gap is 10.

## Output

- `--format tsv` (default): `priority`, person id, label, kind, what.
- `--format json`: one JSON object per line.
- `--format queue`: one `gwt-queue add ... --dedupe` line per person and task kind (`search` or `verify`), query = the person's name, the gaps listed in the note, priority = the best of the gaps. Several gaps of one person are one search, not one per gap. The note starts with `facts: by=1860 bp=Tours dy=1933 dp=Paris |` when the tree already knows a birth or death year or place (`b`/`d` + `y`/`p`, spaces as `_`), so a lane can filter its search by them instead of searching a bare name. `--keep-weak` also queues people whose name is one word (a given name alone matches thousands of people, so they are skipped by default and counted on stderr). `--per-gap` gives the old one-line-per-gap output; `--source NAME` puts a source on every task so the ledger key is complete. Each line has the person's Gramps id as key and every value shell-quoted. Because of `--dedupe` it can run after every round:

```bash
gwt-check dump tree.json.gz
gwt-leads --root I0001 --source SRC --format queue --limit 200 tree.json.gz | sh
gwt-queue report
```

`--limit N` keeps the N highest-priority gaps (tasks, in queue format). Run it against a fresh dump after each apply: a gap that was filled stops appearing, and the queue's `--dedupe` skips the ones already waiting.
