# gwt-apply

`gwt-apply` applies a change list to one Gramps Web tree. It reads the tree before every write, skips what is already there, turns Gramps IDs into handles itself, and logs every API call. It is dry-run unless you pass `--apply`.

It exists because agents that call the Gramps Web REST API directly keep making the same mechanical mistakes. Those are listed under [Traps](#traps).

## Install and run

```sh
pipx install gramps-web-toolkit      # Python 3.11+, see Getting started
gwt apply changes.jsonl              # dry-run: prints the plan and the diff
gwt apply changes.jsonl --apply      # writes
```

Settings come from the environment, or from a `KEY=VALUE` file passed with `--config` (keep it out of git; `.env` is already ignored). The environment wins.

| Setting | Meaning |
|---|---|
| `GWT_URL` | Base URL of the Gramps Web server, e.g. `https://gramps.example.org` |
| `GWT_USERNAME`, `GWT_PASSWORD` | A user of the tree you want to change |
| `GWT_TREE` | The tree id the login must land in. Required with `--apply` |

On a multi-tree server, the tree comes from the user: the login (`POST /api/token/`) returns a token whose `tree` claim names that user's tree. `gwt-apply` refuses to go on if that claim is not `GWT_TREE`, so a wrong account cannot write into the wrong tree.

Other flags: `--log PATH`, `--retries N` (default 5), `--retry-wait SECONDS` (default 30), `--snapshot FILE`, `--save-snapshot FILE` and `--since OLD` ([Snapshots](#snapshots)), `--tag NAME` ([Tags](#tags)), `--break-lock` (below).

Every run gets a run id, `<date>-<time>-<6 hex>`, printed on the first line. The log goes to `PATH` with the run id before the extension (`--log apply.jsonl` writes `apply.20261021-101500-a1b2c3.jsonl`), or to `gwt-apply-<run id>.jsonl`. The file is created with `O_EXCL`: a run never appends to or overwrites another run's log, so rerunning the same command after a crash leaves both logs.

Exit codes: `0` every op done or skipped; `1` a conflict or an error stopped the run; `2` the change list or the settings are invalid (nothing was read or written).

## The change list

A JSON array of ops, an object `{"ops": [...]}`, or JSONL with one op per line. Every op has:

- `op`: one of the operations below.
- `key`: a natural key, unique in the file and stable across runs, such as `birth-john-smith-1890`. The object an op creates gets a handle derived from its key (`gwt` + 25 hex characters of SHA-256), so a second run finds it.
- `ruling`: the id of the decision the change comes from. An op without one is refused.
- `tag` (optional, any op): a Gramps tag name for the objects this op creates, such as `r12-weak`.

### Tags

Every object a run creates gets the Gramps tag named by `--tag` (for example `--tag r12`), plus the op's own `tag` if it has one: a name, or a list of names. A tag the tree does not have yet is created the first time it is needed; one it has is reused by name. Objects the run only changes (a person who gets a new event or citation) are not tagged.

A tag makes a block findable: filter by it in the web UI's lists, or list it through the API with the Gramps filter rule `HasTag`, for example `GET /api/people/?rules={"rules":[{"name":"HasTag","values":["r12"]}]}`, the same for `events`, `families`, `citations`, `notes`, `media`, `places` and `sources`. That is the list to review, or to delete in reverse order (notes, media and citations first, then events and families, then people) if a block has to be rolled back; the changes the block made to existing objects are in its log.

References to other objects are either a **Gramps ID** (`I0042`, `P0007`, or with the suffix an import may add, `D051-b`) or **`key:<key>`** for an object made by an op (in this file or an earlier run). A bare handle is refused.

| op | Required | Optional | What it does |
|---|---|---|---|
| `add_person` | `first_name`, `surname` | `gender` (`M`, `F`, `U`), `gramps_id`, `allow_namesake` with `namesake_reason` and `namesake_of` | New person. `surname` may be `""` for someone known by one name only (a DNA match shown by first name); the field must still be there. If someone with the same first name and surname exists, the op is a conflict that names them. When it really is another person, set `allow_namesake: true`, say why in `namesake_reason`, and list in `namesake_of` the Gramps ID (or `key:<key>`) of every namesake you compared; one the op does not name is still a conflict. A `key:<key>` from an earlier list still counts once that person is in the tree under a Gramps ID of its own |
| `update_person` | `person`, `set` | | `set` takes only `first_name`, `surname`, `gender` |
| `add_place` | `name`, `type` | `enclosed_by`, `lat` and `long`, `gramps_id` | New place inside its parent, or the existing one with the same name, type and parent |
| `add_event` | `type`, and `person` or `family` | `date`, `place`, `description`, `role`, `gramps_id` | New event, linked to the person (role `Primary` by default) or the family (role `Family`) |
| `update_event` | `event`, `set` | | `set` takes only `type`, `date`, `place`, `description`: the way to correct an event instead of adding a second one |
| `update_place` | `place`, `set` | | `set` takes only `lat` and `long`, given together: puts coordinates on a place the tree already has, for what `gwt-check` reports as `place-no-coords`. A place that already has those coordinates is skipped |
| `update_note` | `note`, `set` | `expect` | `set` takes only `text` and `type`. A new `text` needs `expect`, the text the note has now: a note that changed since you read it is a conflict and is never overwritten. The old text stays in the log line. A note that already has the new values is skipped |
| `update_citation` | `citation`, `set` | `expect` | `set` takes only `page`, `confidence` (0-4) and `date`. A new `page` needs `expect`, the page the citation has now, with the same conflict rule |
| `update_family` | `family`, `set` | | `set` takes only `type` (for example `Married`) |
| `update_child_ref` | `family`, `child` | `set`, `confirm_probable`, `expect` | `set` takes only `frel` and `mrel`. `confirm_probable` is the reason, the document that settles a `[probable]` link: the mark becomes `[confirmed] <reason> (was: [probable] <old reason>)`, so the doubt stays on the link. A link with no `[probable]` mark is a conflict, one already confirmed is skipped. `expect` is the mark's current text |
| `detach` | `target`, `object` | | Takes a note, citation or media reference off a target (`{"note": "N0042"}`). The object stays in the tree, which `gwt-check` lists as an orphan, and `attach_existing` puts it back. A media reference that carries a region, notes, citations or attributes is a conflict, because `attach_existing` cannot put those back |
| `add_family` | `father` or `mother` | `children`, `type`, `gramps_id` | New family, or the existing one with the same parents |
| `link_child` | `family`, `child` | `frel`, `mrel` (default `Birth`), `probable: true` with `probable_reason` | Adds a child to a family. With `probable`, the link also gets a `Research` note on its child_ref that reads `[probable] <probable_reason>`; a link that is already there gets the note if it has none starting with `[probable]` |
| `add_source` | `title` | `author`, `pubinfo`, `abbrev`, `gramps_id` | New source, or the existing one with the same title |
| `add_repository` | `name`, `type` | `gramps_id`, `url`, `address` | New repository, or the existing one with the same name, ignoring case and accents. `type` is a non-empty string; the built-in ones are `Library`, `Cemetery`, `Church`, `Archive`, `Album`, `Web site`, `Bookstore`, `Collection`, `Safe`, and another name becomes a custom type. `url` starts with `http://` or `https://`; `address` takes some of `street`, `locality`, `city`, `county`, `state`, `country`, `postal`, `phone` |
| `update_repository` | `repository`, `set` | | `set` takes only `name`, `type`, `url`, `address`. A `url` already listed is kept, `address` completes the first address, and a `name` another repository already has is a conflict (use `merge_repository`). A note goes on with `add_note` and `{"repository": ...}` as the target |
| `link_repository` | `source`, `repository` | `call_number`, `media_type` | Adds the repository to the source's repository list. If the source already lists it, a `call_number` or `media_type` that differs is updated in that entry, and nothing else changes. `media_type` is a non-empty string; the built-in ones are `Audio`, `Book`, `Card`, `Electronic`, `Fiche`, `Film`, `Magazine`, `Manuscript`, `Map`, `Newspaper`, `Photo`, `Tombstone`, `Video`, `Unknown` |
| `unlink_repository` | `source`, `repository` | | Takes the repository out of the source's list. The repository stays. Skipped when the source does not list it |
| `merge_repository` | `winner`, `loser` | `reason` | Moves every source from the loser to the winner (a source that lists both keeps one entry, with the loser's call number and media type where the winner's are empty, and the notes of both), adds the loser's urls, addresses and notes to the winner, then deletes the loser. A `Research` note on the winner records the merge, so a rerun skips. A dry run shows each source that moves and the delete |
| `add_citation` | `source`, `page`, `attach` | `confidence` (0-4, default 2), `date`, `gramps_id` | New citation, or the existing one with the same page on that source, attached to each target |
| `add_media` | `file`, `path`, `attach` | `desc`, `mime`, `gramps_id` | Media object for a local file, or the existing one with the same MD5, attached to each target |
| `add_note` | `text`, `attach` | `type` (default `General`), `gramps_id` | New note, or a note with the same text already on a target |
| `add_transcription` | `text`, `margin_reading`, `attach` | `gramps_id` | A transcript as a `Transcript` note (never as media) whose text ends with `Margin reading: <margin_reading>`. `margin_reading` is required: what the page margins say (annotations, later notes, corrections) or `none` once you have looked. Same reuse rule as `add_note` |
| `attach_existing` | `object`, `attach` | | Links a citation, media object or note the tree already has (`{"media": "O0054"}`) to more targets. For what an import left with no reference, which `gwt-check` reports as `orphan-citation`, `orphan-note` and `orphan-media`, or a document that belongs to a second person; `add_citation`, `add_media` and `add_note` only make new objects. Targets already carrying it are skipped |
| `dedupe_list` | `target`, `list` | | Keeps the first of each repeated reference in one `citation_list`, `note_list` or `media_list`: what `gwt-check` reports as `repeated-ref` there, with the same identity: two media refs to one photo with different regions are not a repeat. A conflict when the dropped media ref carries notes, citations or attributes the kept one lacks. Nothing repeated is a skip |
| `set_attribute` | `target`, `type`, `value` | | Sets an attribute; replaces the value of an attribute of the same type |
| `add_association` | `person`, `target`, `relation` | | An association (Gramps `PersonRef`) from `person` to `target` with that relation, skipped when the person already has one to the same target with the same relation. Gramps Web's DNA view reads associations with relation `DNA`: put the match's segments in a note attached to the association |
| `merge_person` | `winner`, `loser` | `use_loser` (`birth`, `death`), `reason` | Gramps' own person merge: everything that points at the loser moves to the winner, the loser's families with the same partner merge, and the loser is deleted. See [Merges](#merges) |
| `merge_family` | `winner`, `loser` | `reason` | Gramps' own family merge. Refused when the two families have different fathers or different mothers: merge those people first |
| `merge_event` | `winner`, `loser` | `reason` | Gramps' own event merge, for two events that are one fact (a death entered once per citation): the loser's citations, notes, media and attributes move to the winner, every person and family that listed the loser lists the winner (once, when it already listed it in the same role), and the loser is deleted. Refused when the two differ in type, date (the whole date: `about 1884-05-02` is not `1884-05-02`), place or description, when the loser has one of those and the winner does not (the merge would lose it), and when a person or family lists both in different roles: correct one with `update_event` first. See [Merges](#merges) |

`attach` and `target` take objects with one key, the kind of object: `{"person": "I0042"}`, `{"event": "key:birth-john"}`. Kinds: `person`, `family`, `event`, `citation`, `source`, `media`, `place`, `repository`, `association` and `child_ref`, the link between a child and a family, written `{"child_ref": {"family": "F0003", "child": "I0042"}}`. A child_ref carries citations and notes (no media, no attributes); that is where a doubtful parent link gets its source and its "probable" note. Mark the link with `probable` on `link_child` rather than with a separate `add_note`, so the mark cannot be left out. The child must already be in that family. An association is written `{"association": {"person": "I0042", "target": "I0107"}}`, with `"relation": "DNA"` when the person has more than one association with that target; it carries citations and notes (no media, no attributes) and must already exist.

Place names, place types and person names are compared without case, accents or repeated spaces: `Mónte  Alto` matches `monte alto`.

Types (event, note, place, family, attribute, repository, media type) are Gramps' internal English names, whatever language the web UI shows: `Research`, `Transcript`, `Baptism`, `Parish`. A name the server does not recognise is stored as a new custom type, separate from the built-in one; whether a translated name is recognised depends on the server's language, so do not rely on it. Custom types the tree already uses are written as they are.

Dates are written in this format: `1890`, `1890-03`, `1890-03-14`, optionally after `abt`, `bef` or `aft`, and optionally after `est` or `calc`. Ranges are `bet 1890 and 1895`, spans `from 1890 to 1895`. Anything else is refused rather than stored as text.

### Example

All people here are invented.

```jsonl
{"op": "add_person", "key": "p-john-smith", "ruling": "R-12", "first_name": "John", "surname": "Smith", "gender": "M"}
{"op": "add_place", "key": "pl-st-mary", "ruling": "R-12", "name": "St Mary", "type": "Parish", "enclosed_by": "P0007", "lat": 39.51, "long": -89.26}
{"op": "add_event", "key": "e-john-birth", "ruling": "R-12", "person": "key:p-john-smith", "type": "Birth", "date": "1890-03-14", "place": "key:pl-st-mary"}
{"op": "link_child", "key": "l-john-parents", "ruling": "R-12", "family": "F0003", "child": "key:p-john-smith"}
{"op": "add_citation", "key": "c-john-bapt", "ruling": "R-12", "source": "S0011", "page": "book 3, folio 12v, no. 40", "confidence": 3, "attach": [{"event": "key:e-john-birth"}, {"person": "key:p-john-smith"}]}
{"op": "add_media", "key": "m-john-bapt", "ruling": "R-12", "file": "scans/smith-baptism-1890.jpg", "path": "sources/parish/smith-baptism-1890.jpg", "desc": "Baptism of John Smith, 1890", "attach": [{"citation": "key:c-john-bapt"}]}
{"op": "add_note", "key": "n-john-parents", "ruling": "R-12", "type": "Research", "text": "[probable] Parents taken from the index only.", "attach": [{"child_ref": {"family": "F0003", "child": "key:p-john-smith"}}]}
{"op": "add_note", "key": "n-john-godparents", "ruling": "R-12", "type": "Research", "text": "Godparents: Peter Brown and Mary Green.", "attach": [{"person": "key:p-john-smith"}]}
{"op": "set_attribute", "key": "a-john-occupation", "ruling": "R-12", "target": {"person": "key:p-john-smith"}, "type": "Occupation", "value": "miller"}
```

The dry-run prints one block per op:

```
[done] e-john-birth (add_event, ruling R-12)
    create Event: {"_class": "Event", "handle": "gwt7f36...", "type": "Birth", ...}
    update Person gwtaf04...: birth_ref_index: -1 -> 0; event_ref_list: [] -> [{"ref": "gwt7f36...", "role": "Primary"}]
[skip] c-john-bapt (add_citation, ruling R-12)
    skip: Citation C0123 already cites that page
    skip: Event E0456 already has it in citation_list
```

### DNA matches

Gramps Web's DNA view shows, for a person, every association with relation `DNA` and parses the notes on that association. The first line of a note is the CSV header and every other line one segment, so the note text goes in exactly as the DNA view expects it; `add_note` stores it as given. A match is three ops (all people invented):

```jsonl
{"op": "add_person", "key": "m-kit-a17", "ruling": "R-40", "first_name": "Ruth", "surname": "Example", "tag": ["dna-match"]}
{"op": "add_association", "key": "a-kit-a17", "ruling": "R-40", "person": "I0042", "target": "key:m-kit-a17", "relation": "DNA"}
{"op": "add_note", "key": "n-kit-a17", "ruling": "R-40", "text": "Chromosome,Start Location,End Location,Centimorgans,Matching SNPs\n7,1000000,9000000,12.5,1500", "attach": [{"association": {"person": "I0042", "target": "key:m-kit-a17"}}]}
```

An association lives inside its person, and a `PUT` rewrites the whole person, so writing associations one by one gets slower with every match: a tested person with thousands of matches is a large object rewritten once per op. `gwt-apply` holds them instead. Every association a run adds to one person goes in a single `PUT`, made when the run ends or as soon as another op reads, writes or merges that person (the segment note on an association, for example). Each `add_association` still reports `done` or `skip` on its own, the dry run shows the same thing, and the log has one `op` line per op plus a `held-write` line for the write itself. If that write fails, every op it carried reports the error and a rerun adds what is missing; a write whose answer was lost is read back first, so nothing is added twice.

### Merges

A merge is the one op that deletes something, so it says everything it will do before it does it. The dry run lists, list by list, what moves from the loser to the winner (events, citations, notes, media, attributes, names, families), which families (for an event merge, which people and families) now point at the winner instead of the loser, which of the loser's families merge with the winner's because they share the partner, and the loser that goes away.

The merge itself is the server's (`POST /api/people/<winner>/merge/<loser>`, `POST /api/families/<winner>/merge/<loser>`, `POST /api/events/<winner>/merge/<loser>`), the same code the Gramps Web merge screen runs, so references are moved by Gramps and never by hand-written `PUT`s. `merge_repository` is the exception: Gramps Web has no repository merge, so it moves each source's entry with a `PUT` and then deletes the loser with `DELETE`. On a person merge the winner keeps its own name, gender, birth and death; `use_loser: ["birth"]` makes the loser's birth event the winner's birth instead (likewise `death`). That change is written to the winner before the merge.

Before the merge, the winner gets a note that records it: `Merged Person I0042 (John Smith) into this person (ruling R-12). <reason>`. The note is how a rerun knows the merge is done: when the loser is gone and the winner has that note, the op is skipped; when the loser is gone and there is no note, the op is a conflict.

## Snapshots

A dry run against the server reads the tree op by op, which takes hours for a list of thousands of ops. With a snapshot it reads nothing from the server:

```sh
gwt-apply --save-snapshot tree-0412.json.gz                     # the whole tree, once; a few minutes
gwt-apply changes.jsonl --snapshot tree-0412.json.gz            # dry run, offline, needs no settings
gwt-apply changes.jsonl --snapshot tree-0412.json.gz --apply    # checks the snapshot is current, then writes
```

`--save-snapshot` pages through every object of every class and writes them, as the API returns them, to a new gzip JSON file; it never overwrites one. The dry run on a snapshot plans exactly as against the server, op by op, and never opens a connection.

`--apply` with a snapshot first plans the whole list on the snapshot, offline. If the plan stops at a conflict, nothing is written. Then it reads the `change` stamp of every object in each class the plan touched, one request per class, and refuses to write if, since the snapshot:

- an object the plan read or would change was changed, deleted or made (a keyed object made by an earlier run counts: after a crash, take a new snapshot), or
- a class the plan searched as a whole (names for namesakes, places, sources, citation pages, media checksums) gained or lost an object, because the plan's "already there" answers could be wrong.

Then it applies, reading from the snapshot. Before each `PUT` of an object read from the snapshot it reads the object's `change` stamp from the server, and stops with an error if it moved. Objects the run itself wrote, and the people whose family lists the server updated for it, are read from the server from then on.

### Checking an apply quickly

After an apply, the usual check is to run the same list again as a dry run: every op must come out `skip`. Against the server that rereads the tree op by op. Instead, bring the snapshot taken before the apply up to date and run the dry run on it, offline:

```sh
gwt-apply --save-snapshot tree-0413.json.gz --since tree-0412.json.gz   # only what changed since 0412
gwt-apply changes.jsonl --snapshot tree-0413.json.gz                    # every op must say skip
```

`--since` reads one list of `change` stamps per class, then only the objects whose stamp moved and the new ones, and leaves out the deleted ones. The new file holds what a full `--save-snapshot` would, so the check is as strong as a dry run against the server: an object the list touched and someone changed afterwards, or one that went away, makes its op come out `done` or `conflict` instead of `skip`. That holds for any change since the older snapshot, not only the ones the list made, so the new snapshot is also the one to plan and apply the next list on. Gramps' `change` stamp counts seconds, so take the older snapshot while nothing writes to the tree, as an apply's lock guarantees.

## Safety rules

- **Dry-run by default.** Without `--apply` nothing is written. Reads run, so the plan reflects the real tree, and objects the plan would create are kept in memory so later ops can refer to them.
- **The whole list is checked before the first request**: known ops and fields, a `ruling` and a unique `key` on each op, references that are Gramps IDs or keys, dates in the format above, media files that exist, a reason and the compared IDs on every `allow_namesake`, a reason on every `probable`, and no two ops that are the same change under different keys (everything but `key` and `ruling` equal).
- **Duplicates are looked for before the first write.** After login, and before any op runs, every `add_person` is compared with everyone in the tree and with the other people the list makes. It is flagged when someone has the same first name and surname, or the same first given name and first surname and also a birth or baptism year within two years, a parent in common or a spouse in common. Every flag is printed at once and nothing is written; an op clears a flag by naming that person in `namesake_of`, with `allow_namesake` and a `namesake_reason`.
- **Read before every write, skip what exists.** The same event type and date on that person or family; the same page on that source; a media object with the same MD5; a note with the same text on a target; a family with the same parents; a place with the same name, type and parent; a source with the same title; a link that is already there.
- **No silent namesakes.** A new person whose first name and surname already exist in the tree stops the run until the change list either uses the existing ID or says `allow_namesake`, with the reason and the IDs it compared. The reason goes into the plan and the log.
- **Repositories.** Link a source to a repository only where the source is unambiguous, for example the archive named in the register's own title, and never from a guess. A source that already lists the repository is never linked twice: a different `call_number` or `media_type` is updated in that entry. `merge_repository` moves the loser's call number to a source's entry when the winner's is empty, so a throwaway repository made to try a merge should have no call number.
- **One birth and one death.** If the person already has a `Birth` (or `Death`) in role `Primary` with another date, the op is a conflict. Correct the existing event with `update_event` instead of adding a second one.
- **One apply per tree.** `--apply` takes a lock before its first write and holds it to the end: a private note with a fixed handle and Gramps ID `GWTLOCK`, inside the tree, so it works whichever machine the apply runs on. Creating it is the test: the server refuses a second object with the same handle. The note says which run, host and process hold it and until when; the holder moves that time 15 minutes ahead while it works and deletes the note when it stops, also on Ctrl-C, `SIGTERM` or `SIGHUP`. A second `--apply` on the tree stops before writing and names the holder. A lock whose holder died (power cut, `kill -9`) expires after 15 minutes and the next apply takes it over and says so; `--break-lock` takes it at once, for when you know the holder is dead. A holder that finds its lock gone stops. A dry run takes no lock, and says so when an apply holds the tree. While the lock is held, an export of the tree includes the lock note.
- **Stop at the first conflict or error.** Later ops are not run, because they may depend on this one.
- **Writes are narrow.** Only a merge deletes anything, and only the loser: through Gramps' own merge, or for `merge_repository` with a `DELETE` once no source lists it. Objects are created with `POST /api/objects/`. Changes to an existing object are a `GET` followed by a `PUT` of the same object with only the named field changed. Right before the `PUT` the object is read again; if it changed since the op read it, the op starts over, so a concurrent edit is not overwritten.
- **Busy server.** On "database is locked", any 5xx, a request that timed out or lost its connection, or an object that changed since it was read, the op waits `--retry-wait` seconds and starts over from fresh reads. A create whose answer never came is looked up again before the op tries it a second time, so a timeout cannot make an object twice. A login answered 429 (the server rate-limits logins) waits and tries again.
- **A log of every call.** One JSONL line per API call with the op's `key` and `ruling`, the method, path, status, and the body of every write. The password and the token are never logged.
- **Re-running is safe.** After a crash, run the same list again: what was written is skipped, and an object that was created but not yet linked gets linked.

## Speed

An op costs a few requests, and on a real server the writes dominate: 100 to 150 ms for a `POST` or `PUT` on a LAN, while a single read takes about 20 ms. Two things help.

- **Lookups are read once per run.** Gramps ID to handle, "does this handle exist", and the whole-class lists the checks need (sources by title, places by name, media by checksum) are each one `GET` per class for the whole run, with the run's own writes added on top. They stay right because the [lock](#safety-rules) keeps other appliers out. The lock does not stop someone editing in the web UI, so do not edit a tree by hand while an apply runs: a source or place added by hand halfway through is not in the lists, and the run would make a second one. The object an op changes is still read fresh, and read again right before its `PUT`.
- **Run it near the server.** Every request pays the round trip. A client behind a VPN that sends the server's traffic the long way round can take 250 ms a request where the LAN takes 70 ms; for a long list, run `gwt-apply` from a machine on the server's network.

There is a `POST /api/transactions/` endpoint that takes many changes in one call, and `gwt-apply` does not use it. It wants objects in Gramps' internal form (types as `{"_class": "EventType", "value": 12, "string": ""}`, which no read endpoint returns), it answers "Object has changed" when the old object is sent in the form a `GET` returns, and its own documentation says it does not keep references or the birth and death indexes in step, so it can leave the tree inconsistent.

`apply/bench/` measures this on a throwaway server: `synth.py seed` fills an empty tree with invented people, `synth.py ops` writes an invented change list, and `run.py` runs `gwt-apply` with a timing session and prints the requests by kind.

## Traps

These are the mechanical mistakes `gwt-apply` makes impossible.

- **A vital event added twice.** An agent adds a birth the person already has. Here the op is skipped when the type and date match, and refused when a different birth or death exists.
- **A Gramps ID where a handle belongs.** `"place": "P0007"` in an event body stores a dangling reference. Here every reference is resolved to a handle by the tool, and a bare handle is refused in the change list.
- **Media uploaded the wrong way.** `POST /api/media/` with the file creates a new object stored under a name the server derives from the checksum, outside the folder layout you keep, and a repeat call makes another one. Here the media object is created with `POST /api/objects/`, with your `path` and the file's MD5 as `checksum`, and the file is stored with `PUT /api/media/<handle>/file?uploadmissing=true`, which writes it to that path and changes nothing in the record. A file whose MD5 is already in the tree is not uploaded again.
- **A change beyond its ruling.** An agent asked to add a citation also "tidies" a name. Here each op touches only the objects and fields it names, every write carries its `ruling` in the log, and there is no generic "PUT this object" op.
- **A lost update.** Two writers read the same person and the second `PUT` erases the first one's change. The obvious guard does not work: the `ETag` a `GET` returns is a hash of the request cache key (plus `:br` when the response is compressed), while `PUT` compares `If-Match` with a hash of the object, so `If-Match` always answers 412 (seen on Gramps Web API 3.23.1). Here the object is read again right before the `PUT` and compared with the first read.
- **Half-applied runs.** A crash between creating an event and linking it leaves an orphan, and a naive rerun creates a second event. Here the key gives the event a fixed handle, so the rerun finds it and only adds the link.
- **A misnamed type.** A type string the server does not recognise silently becomes a new custom type, so the object no longer groups with the built-in one. Use the internal English names.
- **Login storms.** Several logins in a few seconds get 429. Here the client spaces logins out, waits on 429, and reuses one token until it expires.

## Traps from real applies

Ten rounds of LLM-driven applies on two trees, each followed by a verification pass, turned up these mechanical failures. The left column is what happened; the right column is what covers it now.

| What went wrong | Covered by |
|---|---|
| An event's `place` held a Gramps ID instead of a handle | `gwt-apply`: references are resolved by the tool, bare handles refused |
| The same marriage was added twice to one family | `gwt-apply`: same type and date on the family is skipped |
| An event was created but linked to nobody | `gwt-apply`: create and link are one op, and a rerun links a created event |
| A child link pointed to notes that were never created | `gwt-apply`: every reference is read before it is written |
| A new place duplicated one already in the tree | `gwt-apply`: same name, type and parent is reused, ignoring case and accents. Alternative names and spelling variants are not caught: search before adding |
| A birth date was written into the baptism event | `gwt-apply`: one op per event, with its own date |
| A citation, note or media object was left with no references | `gwt-apply`: `add_citation`, `add_media` and `add_note` require `attach` |
| A media object was linked to one citation when two needed it | Change list: one `attach` entry per target; the second op with the same file reuses the object by MD5 |
| Birth and death pointers left at -1 or pointing at the wrong event | `gwt-apply` sets the pointer when it adds a `Primary` birth or death and none is set. A baptism without a birth leaves it at -1, by design |
| A correction was interrupted halfway by a usage limit | `gwt-apply`: rerun the same list; done ops skip |
| The apply printed the password in its output | `gwt-apply`: the password and token never reach the output or the log |
| New events had no place although their description names one | Change list: give `place`. Verification: count events without a place |
| New places used by events had no coordinates | Change list: give `lat`/`long` on `add_place`, or geocode after the apply. Verification: count places used by events without coordinates. Containers that no event uses stay without coordinates, so the map shows one marker per event |
| The geocoder put a place on a same-name building or the town centre | Verification: check each new point against a map |
| Events had no date although the source gives a range | Change list: use `bet A and B`, `bef`, `aft` |
| Notes repeated values the reviewer had rejected | Change list: the text is the author's job; fix the source file too, or the next apply copies it again |
| Citations from machine indexes were written at high confidence | Change list: set `confidence` by your own rule |
| Person and family links were one-sided | The server keeps them in step when a family is written; `gwt-apply` writes families, never a person's family lists |
| Listing the whole tree with `profile=all` answered 500 | Verification: page through large trees (100 per page) |
| The verifier's dump was older than the tree and reported false gaps | Verification: dump the tree after the apply, not before |

Two habits from those rounds belong around every apply, with or without this tool: export a backup of the tree before writing (`POST /api/exporters/gramps/file`) and compare its checksum after copying it; and prove the verification script can fail by running it on a copy of the dump with errors injected.

## Undo a run

A backup restores the whole tree. When one run went wrong and the rest of the tree has moved on since, undo only that run:

```bash
gwt-apply --save-snapshot before.json.gz   # or gwt-check dump before.json.gz, right before the run
gwt-apply changes.jsonl --apply --log run.jsonl
gwt-apply --revert run.<run id>.jsonl --pre before.json.gz           # plan: prints every step, writes nothing
gwt-apply --revert run.<run id>.jsonl --pre before.json.gz --apply   # undo, then read each object back
```

`--pre` must be a `--save-snapshot` or a `gwt-check dump` of the same tree, taken right before the run. A `.gramps` export is not accepted. The revert refuses, with exit 2 and nothing written, a copy of another tree, a copy taken after the run's first write, and a copy that already holds an object the run made.

`--revert` reads the real run's log (dry-run lines are ignored) and the pre-run copy, and goes through the writes newest first. A write whose answer was lost, or that came back 5xx, counts as a write: the server may have done it.

| In the log | Step |
|---|---|
| PUT of an object that exists in the pre-run copy | `restore`: the object goes back to its pre-run copy, once, however many times the run wrote it |
| POST of a note, citation, event, place, source, repository or tag | `delete`, after every restore, and only once nothing points at it |
| anything else: a person or family the run made, a merge, a delete, a media upload | `manual`: the server also edits the objects around these, so restore the backup |

The revert never overwrites anything that differs from what the run left:

- An object that changed since the run is a `skip` line that says why, and so is a delete of something a changed object still points at.
- A delete reads the object's backlinks first. While anything still points at it, it is kept: a later run that used the same `--tag`, or attached the note somewhere else, keeps the tag and the note.
- A restore is `manual` when the pre-run copy is older than what the run read. Restoring it would also undo the edits made in between.
- An object already as before the run is a `skip` too, so running the revert twice is safe.

With `--apply` the tree lock is taken before the plan reads anything. Each object is read again right before it is written, and an object that changed since the plan is skipped. Busy answers are tried again. If the revert stops halfway, run the same command again: what is already undone reads as a skip. At the end it reads every restored object back and checks every deleted one again.

The exit code is 0 when the run is undone. It is 1 on a problem, when a `manual` step is left, or when an object changed since the run and was left alone. The revert writes its own log: `gwt-apply-revert-<run id>.jsonl`, or `<stem>.<run id><suffix>` next to `--log`, as for an apply. A revert is a tree write like any other, so run it as that tree's only writer and check the tree after it.
