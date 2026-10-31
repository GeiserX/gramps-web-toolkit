# gwt-leads

[gwt-queue](queue.md) holds what to search next. Somebody has to decide what that is. In the pipeline the judge did it by reading weak-points tables and branch depth by hand, every round. `gwt-leads` reads a tree dump offline and lists the gaps, ranked, ready to seed the queue. It never touches the tree.

Standard library only. Run it as `leads/gwt-leads DUMP` where `DUMP` is either of two files, gzipped or not (gzip is told by its first bytes, not by a `.gz` name):

- the file `gwt-check dump` writes: `{tree, dumped_at, objects: {people, families, events, places}}`;
- a snapshot `gwt-apply` saves, the one [Getting started](getting-started.md) tells you to keep: `{format: "gwt-apply-snapshot/1", tree, taken, objects: {Person, Family, Event, Place, ...}}`.

The same tree gives the same leads from either file. A file in any other shape, a dump with no people, or a record with no handle exits `2` with a message naming both formats instead of printing an empty list.

`gwt-check dump` pages the whole tree. Do not take a dump while that tree's writer is applying a change list.

## What it finds

| kind | the gap |
|---|---|
| `branch-end` | a person with no birth parents recorded: where a line stops |
| `missing-vital` | no birth or baptism; no death or burial for someone born in `--living-after` or earlier; a birth or death (or its stand-in) with no date or no place; a family with children and no marriage event |
| `uncited` | a birth or death (or its stand-in) with no citation: a fact nobody can check |
| `unsupported-link` | a birth link whose cited records name neither parent, or only one of the two. `--links FILE` turns it on: the JSON `gwt-check links --json` wrote from the same dump. A report that does not fit the dump (an unknown level, or a child not in that family) stops with exit 2. Left out: a link that carries its own citation (`the link itself is cited`), a family with one parent whose record names that parent, adoptive, step or foster links, and a child with no cited birth or baptism (`uncited` and `missing-vital` list that). The task carries the report's flags. It is a gap in the record text, not a sign the link is wrong, and it is only as good as the name matching of `gwt-check links` |

`--kinds` picks a subset. Only a person's own events count, the same way `gwt-check` and `gwt-apply` read them:

- An event ref counts only with role Primary. A godparent's ref to a child's baptism is not the godparent's baptism.
- The birth is the event `birth_ref_index` points at, else the first Birth, else Baptism, else Christening. The death is the event `death_ref_index` points at, else Death, else Burial, else Cremation. The gap text names the type it used: `baptism has no place`.
- Parents are the parents of a family the person is a Birth child of. An adoptive, step or foster family does not continue the line.

A date counts as present when it has a structured year. A date with only text ("before the war") is reported as `date text without an interpretable year`, with the text, never as a missing date. A homonym or an unverified source is not a gap: the tool only reports what is missing from the tree, never what the tree should say.

## Who is held back

Every output format leaves out:

- anyone marked private in Gramps, and
- anyone who may be living: no Death, Burial or Cremation event (with or without a date), and no date that proves a birth in `--living-after` or earlier. The default is this year minus 100.

A birth is proven old by any of these, each one ending in `--living-after` or earlier:

- the person's own birth, baptism or christening year, any of them;
- the person's death year;
- the marriage of a family the person heads. Other family events, such as a divorce, do not count;
- any of those for anyone descending from the person, through any parent family. A grandchild baptised in 1890 proves every ancestor above was born before 1890, so a dated person anywhere below keeps the whole direct line above them on the list.

For this test a date counts by its latest possible year: the end of a range or span, the year plus 10 for an "about" or estimated date, and no year at all for "after" or "from", which have no upper bound. A date with only text proves nothing.

Their Gramps ids, never their names, go to stderr in one line, after how many are marked private and how many may be living. `--include-living` turns the hold off; use it only for a private review on your own machine, never for a list that feeds searches on outside sites. Private events never feed the facts, and their year and date text stay out of the gap text too.

A death is asked (`no death or burial event`) of everyone proven old who has none.

## Priority

With `--root I0001` (a Gramps id), the root's ancestors rank first: 100 minus one per generation, never below 40. Everyone else is 10. That puts the tips of the direct lines ahead of collateral branches, the same order the pipeline's sweep waves use. Without `--root` every gap is 10. Priority belongs to the person, so all of one person's gaps share it.

## Output

- `--format tsv` (default): `priority`, person id, label, kind, what. Tabs and newlines inside a value become spaces.
- `--format json`: one JSON object per gap, with `gap` (a stable code such as `birth-place` or `branch-end`), `generation`, `weak` and `facts`, plus `where` when `--archives` matched the gap's place (see [Archive hints](#archive-hints)).
- `--format queue`: one JSON line per task for `gwt-queue add --jsonl`. One task per person and task kind (`search` for branch ends and missing vitals, `verify` for uncited facts and unsupported links), with every gap in the note. `--per-gap` gives one task per gap instead.

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
- The **dedupe key** is the person, the task kind and `--source`, plus the gap code with `--per-gap`. The name is not part of it, so a corrected name does not make a second task. With `--dedupe`, `gwt-queue` skips a task that is already queued, running or done, and on an open one it takes the new label, query, note and priority, so a re-seed carries the latest gaps. Keep the same mode and `--source` across rounds: grouped and per-gap keys differ, and so do keys with different sources.
- `--skip-weak` leaves out people with no given name or no surname (or none at all), unless they are the root or one of its ancestors. Their ids go to stderr. A thin name makes a poor outside search, but an ancestor stays on the list.

`--source`, `--per-gap` and `--skip-weak` only apply to `--format queue`; with another format they are an error.

## Archive hints

`--archives FILE` tells each gap which archive keeps its record, and whether the year is online there. The file is a TSV with a header row, so it can be edited in a spreadsheet. `examples/archives.tsv` is an invented one:

```
match	archive	series	years	url	access
Villaejemplo	Invented parish archive of Villaejemplo	Sacramental books	1850-1920	https://archives.example.org/parish?place={place}&year={year}	online
Provincia Imaginaria	Invented provincial archive	Civil register	1871-1950	https://archives.example.org/civil?q={place}	browser
Exempleshire	Archives of an invented department	3E	1793-1912	https://archives.example.org/search?commune={place}&year={year}	letter
France	Invented national archive				
```

| column | holds |
|---|---|
| `match` | a place name, at any level: a parish, a town, a province, a country |
| `archive` | the archive's name |
| `series` | the series or call number, or empty |
| `years` | the years that are online, as ranges separated by commas: `1600-1792,1793-1902`. A single year is a range of one. Empty means none |
| `url` | a search address, or empty. `{place}` becomes the name of the event's own place (the deepest level, URL-encoded) and `{year}` the year counted for `online` (empty when there is none). Write a literal brace as `{{` or `}}` |
| `access` | optional: `online` (a lane can search it), `browser` (a person has to open it, behind a login or a captcha), `letter` (only by writing) or `unknown`. An empty cell, or no column, is `unknown` |

The file is read as plain UTF-8 text split on tabs, the way the ledger and the queue read theirs: a quote is an ordinary character, and a byte-order mark and spaces around a cell do not count. A spreadsheet's "UTF-8 tab-separated" export reads as it is; save other encodings as UTF-8 first.

The file must be right, or nothing runs: a file that is not UTF-8, a missing, unknown or repeated column, an empty `match` or `archive`, a row with more cells than the header (fewer is fine: the missing cells at the end read as empty), `years` that are not ranges, an `access` value outside the four, or a placeholder other than `{place}` and `{year}` exits `2` and prints no gaps.

**Which record.** A branch end, a birth gap or an unsupported link asks for the birth record: the birth, or the baptism that dates it, the same event `facts` reads. A death gap asks for the death or burial. A marriage gap, a gap with no event (`birth-missing`, `death-missing`), an event with no place and a private event get no hint.

**Matching.** Every level of the event's place is compared, from the place itself up to the top, not just the two levels in `bp`, so a row for a department or a country matches. Names compare whole, folded the way `gwt-ledger` folds them: case, accents and punctuation do not count, so `SAINT EXEMPLE` matches `Saint-Éxemple`, but `Ville` never matches `Villexemple`. When several rows match, the deepest level wins, then the first row in the file. No matching row is not an error: the gap is listed as before, with no hint. Matching needs a real place hierarchy: a place whose title holds the whole chain (`Villaejemplo, Provincia Imaginaria, España`, as a GEDCOM import leaves it) is one level, and only a row with that whole title matches it.

`{place}` is always the event's own place, whatever level the row matched, so a department row can search for the commune. If your events sit on parishes and a portal searches by town, a town row's `{place}` gets the parish name: leave `{place}` out of that row's `url`.

**`online`** is `true` when the year falls in one of the row's ranges and `false` when it does not. The year is the one `facts` shows for that record: an exact year, or an "about" or estimated one. A "before", "after", range or span year, or none at all, leaves `online` unknown (`null`), never a guess.

**Output.**

- `--format json`: a gap with a match gets `"where": {"archive", "series", "url", "online", "access"}`. A gap with no match has no `where` key.
- `--format queue`: the note gets a `where:` part after `facts:`, escaped the same way, with empty values and an unknown `online` left out: `facts: bp=Villaejemplo,_Provincia_Imaginaria by=1870 | where: access=online archive=Invented_parish_archive_of_Villaejemplo online=true series=Sacramental_books url=https://archives.example.org/parish?place%3DVillaejemplo&year%3D1870 | birth-uncited: birth has no citation`. To use a value, turn `_` back into spaces, then percent-decode it. A task whose gaps sit on two records (a birth and a death) lists both hints, separated by `; `, in gap order.
- `--format tsv` does not change.

Without `--archives` every format prints exactly what it printed before. The hint changes no priority and no dedupe key, and `gwt-leads` never opens the URL: it only writes it down.
