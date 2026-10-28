# Changelog

Every release of gramps-web-toolkit, newest first. The format follows https://keepachangelog.com/en/1.1.0/ and the versions follow https://semver.org/. A release is a `v*` tag; PyPI gets the package from it.

Every release has a **Change lists** heading. It names each change that makes a change list that worked before behave differently: an op that is now refused, a field that is now required, a different skip or duplicate rule, a different default. Read it before applying an old list with a new version. "None" is a valid entry; a missing heading is not, and the release workflow stops on it.

## [Unreleased]

### Change lists

None.

### Added

- `gwt-apply` `add_tag` puts a tag on an object the tree already has, for example a research mark such as `Hypothesis` on a person, an event or a note.
- `gwt-check check` reports `note-marker-no-tag`: a note that says «hipótesis» or «homónimo» without the tag of that name, in a tree that has such a tag. For information only: it never sets the exit code. A mention with «descartada», «refutada» or a like word next to it in the same sentence is not reported, unless a «no» comes between them. A tree with no such tag is never blamed.
- `gwt-check check` reports `place-vs-description`: an event whose description names another place of the tree and not its own place or one enclosing it. Names are compared without accents, as whole words, and names under four letters are skipped. It is for information only (it never sets the exit code) and reports only events in scope with `--since` or `--tag`.

## [0.2.6] - 2026-10-28

### Change lists

None.

### Fixed

- `gwt-check check`: a finding key that lists several IDs (`dup-media`, `dup-citation`, `own-ancestor` and the others sorted by ID) no longer depends on the order the dump lists the objects in when two IDs have the same number (`O01` and `O001`). The ID itself breaks the tie, so `--known` and `--baseline` keep matching. `gwt-leads` orders such IDs the same way.

## [0.2.5] - 2026-10-28

### Change lists

None.

### Added

- `gwt-apply` `update_place` takes `name` in `set`: it renames a place and keeps the date and language of its name. A sibling of the same type that already has the name is a conflict, a rerun is a skip, and `--verify` reports a place renamed back as differing.

## [0.2.4] - 2026-10-28

### Change lists

None.

### Added

- `gwt-apply` `update_place` takes `enclosed_by` in `set`: it moves a place under another parent, refusing a parent that does not exist or one inside the place. A rerun is a skip, and `--verify` reports a place moved back as differing.
- `gwt-check repos --classify` sorts the sources with no repository by what they name (a repository the tree has, another institution, nothing), with the institutions grouped so one new repository can serve many sources. It only reports.
- `gwt-check check` reports `dup-media`: two media objects with the same checksum, the same file entered twice.

## [0.2.3] - 2026-10-26

### Change lists

None.

### Changed

- `gwt-check dump` and `check` read the first page of every type together, then the remaining pages, instead of one type after another; the dump holds the same objects. `check --from` does not page the tree, so it gains nothing. The per-type progress lines now appear together once the read ends. On a 260-person tree the read went from 20 s to 14 s.
- `gwt-leads` says how many of the people it held back are marked private and how many may be living, instead of one count for both.

### Fixed

- `gwt-leads --links` no longer stops with exit 2 on a links report that has a link that is itself cited (`the link itself is cited`), the first level `gwt-check links` writes.

## [0.2.2] - 2026-10-26

### Change lists

None.

### Changed

- `gwt-apply --snapshot` and `--since` accept a `gwt-check` dump as well as a snapshot, so `gwt-apply LIST --verify --snapshot POST` works on the `--save-dump` of `gwt-check check --from PRE`. Before, a dump was refused with "is not a gwt-apply snapshot".

### Fixed

- apply: a timeout or busy answer in the whole-tree list reads before the first op is retried like an op's read (`--retries`, `--retry-wait`): the duplicate check's, and on an `--apply --snapshot` the check of what changed since the snapshot. Before, one slow read ended the run with exit 1 before any op ran.
- apply: a timeout on the run's first login is retried the same way; only a login inside an op was retried before.
- apply: `--retries` below 1 is refused with exit 2. With 0, a run took no lock, ran no op and reported every op done.

## [0.2.1] - 2026-10-26

### Change lists

None.

### Added

- `gwt-check links`: a read-only report of which parent links have a source, on the link itself or in a record whose text names both parents. It never sets the exit code.
- `gwt-check cites`: events whose citation's page names another kind of act (a marriage act under a birth) or a marginal mention in another act; a report that never fails.
- `gwt-check act`: sets one transcribed act beside a dump and says, field by field, whether the tree's event agrees on date, place, parents and citation reference; `--person` names the subject. A report that always exits 0.
- `gwt-leads --links FILE`: an opt-in `unsupported-link` kind that turns birth links no cited record supports, read from `gwt-check links --json`, into verify tasks. A report from another tree or an older dump stops with exit 2.

### Changed

- `gwt-check fill-places` no longer proposes a place that only a citation of another kind of act or a marginal mention names; the event appears under `held_back` instead, so an `--ops` file has fewer ops than before.

## [0.2.0] - 2026-10-26

The first release: everything since the first public commit.

### Change lists

Lists written for the first public commit of `gwt-apply` behave differently in these ways:

- `allow_namesake` needs `namesake_reason` and `namesake_of`; a namesake without them is refused.
- Two ops in one list that make the same change are refused, and a pre-flight read reports every likely duplicate person before the first write.
- `add_transcription` is refused without `margin_reading` (what the margins say, or `none` once looked).
- `--apply` holds a lock in the tree; a second `--apply` on the same tree stops before writing and names the holder.
- A list planned on a snapshot is refused when the tree moved since the snapshot.
- Every object a run creates carries the run's tag. An empty run tag is refused, and a tag the server reports as created but does not keep stops the op.
- A Gramps ID with a suffix (`I0042-b`) is accepted in every op; a bare handle is still refused.
- A person with one name and no surname is accepted.
- `namesake_of` by `key:` resolves people an earlier list created.
- A value of the wrong kind, a citation `confidence` outside 0-4, or a full date that is not a real calendar day (`1890-02-31`) is refused before the first write.
- A write whose answer was lost (timeout, dropped connection, 5xx) but which landed reports `done` instead of `skip`.
- `gwt-import-excel` stops with exit 2 on an unknown mapping field instead of dropping that column.
- An `add_family` with both parents whose couple looks like a tree family's couple, or another `add_family` of the list, is flagged before the first write even when each parent carries `allow_namesake`; `allow_new_couple` with `new_couple_reason` and `new_couple_of` clears it.
- New op `unlink_child` takes a child out of a family; a list that used it was refused as an unknown op before. `--revert` now makes a note the run deleted again instead of sending it to the backup.

### Added

- `gwt`, one command for every Python tool (`gwt <tool> ...`), installed with one `pip install gramps-web-toolkit`; each `gwt-<tool>` command stays.
- `gwt-apply --verify` proves a list landed: a dry run that exits 1 when any op would write, conflicts or errs, and names each missing object and each field that differs; a Gramps ID merged away is read on its winner. `--json FILE` writes one result per op. A dry-run op retried after a server error no longer reads its own first try's plan as already in the tree.
- `gwt-apply`: offline planning on a snapshot (`--save-snapshot`, `--since`), merges of people, families and events through Gramps' own merge, DNA matches as associations, `attach_existing`, `dedupe_list`, `update_place`, repositories, `update_note`, `update_citation`, `update_family`, `update_child_ref`, `detach`, `unlink_child`, and `--revert` to undo one run from its log.
- `gwt-check`: read-only whole-tree checks, `--baseline`, `--ignore-check`, accepted findings with a reason, disconnected family components, contradictory dates, living-people privacy checks, missing event places found in their citations, citations entered twice, and offline checks of saved snapshots.
- `gwt-ledger` (shared search ledger, one file per lane), `gwt-gate` (one request pace per machine), `gwt-queue` (search tasks for parallel lanes), `gwt-leads` (tree gaps as queue tasks), `gwt-rank` (orders FamilySearch results), `gwt-import-excel` (workbooks to change lists), `gwt-rawstore` (originals in S3) and `gwt-mh` (MyHeritage through a signed-in browser).
- Both Gramps Web clients send a toolkit User-Agent and log in with a sync token (`GWT_TOKEN`).
- A documentation site at https://geiserx.github.io/gramps-web-toolkit/.
- Tagged releases publish to PyPI, and `gwt --version` in a clone prints a version read from git.
- `gwt-read familysearch`: FamilySearch record search and records with your own session token, through the cache and a slow per-account gate (one request every 30 s at best), stopping on a 403, 429, captcha, sign-in page or empty frame; it writes the same envelope and candidates files as `gwt-mh`, whose shared parts now live in `read/gwt_reader.py`.
- `gwt-read pares`: search and descriptions from PARES, the Spanish state archives' catalogue, on the same reader shape, cache (`PARES_RULES`: searches 7 days, descriptions 90 days) and STOP rules, with its own gate (one request every 15 s at best); a page without the record text it asked for (an empty frame, the site's error page) is a STOP.

### Changed

- `gwt-fetch` searches every Europeana provider by default.
- `poller` is now `spanish-poller`.
- `gwt-apply` runs about 30% faster, writes a person's associations once, and checks an apply on an updated snapshot instead of the server.
- `gwt-check` after a write refreshes a saved tree file instead of reading the whole tree again.
- `gwt-apply` logs a real run under `$XDG_STATE_HOME/gwt-apply/<tree>/` instead of the working directory, and a dry run or `--save-snapshot` keeps no log unless `--log` is given. Each log opens with a `start` line (arguments, change list hash, tree) and closes with an `end` line (counts, exit code), records refusals, marks dry-run writes `planned`, and names the Gramps ID of each object created.

### Fixed

- `gwt-apply`: a missing or unreadable change list is one line and exit 2 instead of a traceback, and a run whose login fails still prints its run id.
- `gwt-check`: timeouts are retried, a pedigree-collapsed Ahnentafel walk is bounded and a truncated walk fails, info findings no longer hold a read open, writes during a check no longer abort it, and namesakes with differently named parents are no longer fuzzy duplicates.
- `gwt-ledger`: a shared catalogue prefix no longer hides a different search, an unreadable ledger exits 2, and non-Latin and blocked queries are found again.
- `gwt-gate`: a dead session or a permission refusal is no longer reported as a rate limit.
- `gwt-leads`, `gwt-rank`, `gwt-import-excel` and `gwt-queue`: fixes to living-person detection, surname-first rows and lease handling.
