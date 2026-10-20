# gwt-apply

`gwt-apply` applies a change list to one Gramps Web tree. It reads the tree before every write, skips what is already there, turns Gramps IDs into handles itself, and logs every API call. It is dry-run unless you pass `--apply`.

It exists because agents that call the Gramps Web REST API directly keep making the same mechanical mistakes. Those are listed under [Traps](#traps).

## Install and run

```sh
cd apply
python3 -m pip install -e .          # Python 3.11+, needs only requests
gwt-apply changes.jsonl              # dry-run: prints the plan and the diff
gwt-apply changes.jsonl --apply      # writes
```

Settings come from the environment, or from a `KEY=VALUE` file passed with `--config` (keep it out of git; `.env` is already ignored). The environment wins.

| Setting | Meaning |
|---|---|
| `GWT_URL` | Base URL of the Gramps Web server, e.g. `https://gramps.example.org` |
| `GWT_USERNAME`, `GWT_PASSWORD` | A user of the tree you want to change |
| `GWT_TREE` | The tree id the login must land in. Required with `--apply` |

On a multi-tree server, the tree comes from the user: the login (`POST /api/token/`) returns a token whose `tree` claim names that user's tree. `gwt-apply` refuses to go on if that claim is not `GWT_TREE`, so a wrong account cannot write into the wrong tree.

Other flags: `--log PATH` (default `gwt-apply-<time>.jsonl`), `--retries N` (default 5), `--retry-wait SECONDS` (default 30).

Exit codes: `0` every op done or skipped; `1` a conflict or an error stopped the run; `2` the change list or the settings are invalid (nothing was read or written).

## The change list

A JSON array of ops, an object `{"ops": [...]}`, or JSONL with one op per line. Every op has:

- `op`: one of the operations below.
- `key`: a natural key, unique in the file and stable across runs, such as `birth-john-smith-1890`. The object an op creates gets a handle derived from its key (`gwt` + 25 hex characters of SHA-256), so a second run finds it.
- `ruling`: the id of the decision the change comes from. An op without one is refused.

References to other objects are either a **Gramps ID** (`I0042`, `P0007`) or **`key:<key>`** for an object made by an op (in this file or an earlier run). A bare handle is refused.

| op | Required | Optional | What it does |
|---|---|---|---|
| `add_person` | `first_name`, `surname` | `gender` (`M`, `F`, `U`), `gramps_id`, `allow_namesake` | New person. If someone with the same first name and surname exists, the op is a conflict that names them; set `allow_namesake: true` when it really is another person |
| `update_person` | `person`, `set` | | `set` takes only `first_name`, `surname`, `gender` |
| `add_place` | `name`, `type` | `enclosed_by`, `lat` and `long`, `gramps_id` | New place inside its parent, or the existing one with the same name, type and parent |
| `add_event` | `type`, and `person` or `family` | `date`, `place`, `description`, `role`, `gramps_id` | New event, linked to the person (role `Primary` by default) or the family (role `Family`) |
| `update_event` | `event`, `set` | | `set` takes only `type`, `date`, `place`, `description`: the way to correct an event instead of adding a second one |
| `add_family` | `father` or `mother` | `children`, `type`, `gramps_id` | New family, or the existing one with the same parents |
| `link_child` | `family`, `child` | `frel`, `mrel` (default `Birth`) | Adds a child to a family |
| `add_source` | `title` | `author`, `pubinfo`, `abbrev`, `gramps_id` | New source, or the existing one with the same title |
| `add_citation` | `source`, `page`, `attach` | `confidence` (0-4, default 2), `date`, `gramps_id` | New citation, or the existing one with the same page on that source, attached to each target |
| `add_media` | `file`, `path`, `attach` | `desc`, `mime`, `gramps_id` | Media object for a local file, or the existing one with the same MD5, attached to each target |
| `add_note` | `text`, `attach` | `type` (default `General`), `gramps_id` | New note, or a note with the same text already on a target |
| `set_attribute` | `target`, `type`, `value` | | Sets an attribute; replaces the value of an attribute of the same type |

`attach` and `target` take objects with one key, the kind of object: `{"person": "I0042"}`, `{"event": "key:birth-john"}`. Kinds: `person`, `family`, `event`, `citation`, `source`, `media`, `place`, and `child_ref`, the link between a child and a family, written `{"child_ref": {"family": "F0003", "child": "I0042"}}`. A child_ref carries citations and notes (no media, no attributes); that is where a doubtful parent link gets its source and its "probable" note. The child must already be in that family.

Place names, place types and person names are compared without case, accents or repeated spaces: `Mónte  Alto` matches `monte alto`.

Types (event, note, place, family, attribute) are Gramps' internal English names, whatever language the web UI shows: `Research`, `Transcript`, `Baptism`, `Parish`. A name the server does not recognise is stored as a new custom type, separate from the built-in one; whether a translated name is recognised depends on the server's language, so do not rely on it. Custom types the tree already uses are written as they are.

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

## Safety rules

- **Dry-run by default.** Without `--apply` nothing is written. Reads run, so the plan reflects the real tree, and objects the plan would create are kept in memory so later ops can refer to them.
- **The whole list is checked before the first request**: known ops and fields, a `ruling` and a unique `key` on each op, references that are Gramps IDs or keys, dates in the format above, media files that exist.
- **Read before every write, skip what exists.** The same event type and date on that person or family; the same page on that source; a media object with the same MD5; a note with the same text on a target; a family with the same parents; a place with the same name, type and parent; a source with the same title; a link that is already there.
- **No silent namesakes.** A new person whose first name and surname already exist in the tree stops the run until the change list either uses the existing ID or says `allow_namesake`.
- **One birth and one death.** If the person already has a `Birth` (or `Death`) in role `Primary` with another date, the op is a conflict. Correct the existing event with `update_event` instead of adding a second one.
- **Stop at the first conflict or error.** Later ops are not run, because they may depend on this one.
- **Writes are narrow.** No op deletes anything. Objects are created with `POST /api/objects/`. Changes to an existing object are a `GET` followed by a `PUT` of the same object with only the named field changed. Right before the `PUT` the object is read again; if it changed since the op read it, the op starts over, so a concurrent edit is not overwritten.
- **Busy server.** On "database is locked", any 5xx, or an object that changed since it was read, the op waits `--retry-wait` seconds and starts over from fresh reads. A login answered 429 (the server rate-limits logins) waits and tries again.
- **A log of every call.** One JSONL line per API call with the op's `key` and `ruling`, the method, path, status, and the body of every write. The password and the token are never logged.
- **Re-running is safe.** After a crash, run the same list again: what was written is skipped, and an object that was created but not yet linked gets linked.

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
