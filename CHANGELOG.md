# Changelog

Every release of gramps-web-toolkit, newest first. The format follows https://keepachangelog.com/en/1.1.0/ and the versions follow https://semver.org/. A release is a `v*` tag; PyPI gets the package from it.

Every release has a **Change lists** heading. It names each change that makes a change list that worked before behave differently: an op that is now refused, a field that is now required, a different skip or duplicate rule, a different default. Read it before applying an old list with a new version. "None" is a valid entry; a missing heading is not, and the release workflow stops on it.

## [Unreleased]

Everything since the first public commit. The first tag will be `v0.2.0`.

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
