# gwt-check

`gwt-check` reads a whole Gramps Web tree and reports what is broken in it: orphans, dangling links, wrong birth and death pointers, repeated references, likely duplicates, dates that contradict each other, and what a run changed compared with an earlier copy. It never writes to the tree.

It runs the [independent check](pipeline.md#the-independent-check) after an apply in one command. Before it, every round's checker wrote throwaway scripts, and no two rounds checked quite the same things.

## Install and run

```sh
pipx install gramps-web-toolkit    # Python 3.11+, see Getting started; `gwt check` = `gwt-check`
gwt-check selftest                 # proves every check can fail (no tree needed)
gwt-check check                    # reads the live tree, runs every check
gwt-check check dump.json.gz       # the same on a saved dump
gwt-check check tree.json.gz       # or on a gwt-apply snapshot, offline, in seconds
gwt-check dump dump.json.gz        # save the live tree (.gz compresses)
```

Without installing, run `check/gwt-check` from a clone.

Settings come from the environment, or from a `KEY=VALUE` file passed with `--config`, the same as [`gwt-apply`](apply.md): `GWT_URL`, `GWT_USERNAME`, `GWT_PASSWORD`, and `GWT_TREE`, the tree id the login must land in. Instead of a username and password, `GWT_TOKEN` takes a persistent Gramps Web access token of scope `sync`, which also works on servers that allow only single sign-on ([how to create and revoke one](getting-started.md#log-in-with-a-token-instead-of-a-password)); it is exchanged at `POST /api/token/sync/` and exchanged again when the short-lived login expires. A trailing `/api` on `GWT_URL` is dropped, and an address without `http://` or `https://` is a settings error. If `GWT_TREE` is set and the token's `tree` claim differs, it stops. One login serves the whole run, and the password or token never reaches the output. Every request carries the User-Agent `gramps-web-toolkit/<version> (gwt-check; +https://github.com/GeiserX/gramps-web-toolkit)`.

It exits `0` with no new findings and `1` with new findings. Findings of a check marked for information only, today `unconnected`, `parent-age`, `marriage-age`, `sibling-spacing`, `lifespan` and `dup-citation`, are printed as `info` and never count as new. It exits `2` on bad input, which covers a file that is not a dump, missing settings, a failed login, and a live tree that never settles (below).

## Reading a tree that is being written to

A live read takes a while, and an apply may be writing to the tree meanwhile. Reading page by page, a write shifts the pages, so an object comes twice or not at all, and a person read before a note was attached to it makes that note look like an orphan. So `gwt-check` does not trust the pages. After paging through every type it compares the `change` stamp of every object with one list per type, a single request each, and reads again whatever moved, appeared or went away, plus the father, mother and children of every family that moved (writing a family edits their family lists, see [Check after a write](#check-after-a-write)). It repeats that until a pass finds nothing moved.

When writes never stop, a pass can still catch one halfway: a note made and not yet attached is an orphan for a moment. A pass reads the types one after another, so such a write can show in two passes in a row but not in three. While the tree keeps moving, a finding is confirmed when three passes in a row found it, and the read ends once the confirmed set stops changing. A finding only the last two passes found may be a write caught halfway or a real problem made late in the read, so it is reported too, marked `unconfirmed`; run the check again to tell which. A finding only one pass found is left out. Findings for information only, such as `unconnected`, are not part of this comparison: a group that comes and goes while someone writes never keeps the read going, and the report gives them as the last pass found them. The report says how many passes it took (`passes` in the JSON) and how many findings it left out (`in_flight`). If the findings are still changing after 10 passes, it exits `2`. The note a running `gwt-apply` holds the tree with is left out of every check.

On a copy of a real tree (about 10,000 people, 50,000 objects), a quiet tree gives the same 208 findings this way, with `--live`, and on a `gwt-apply` snapshot. While an apply wrote 1,200 ops on the same server, the old read stopped within seconds every time. The new one finished every time (six runs), in 1 to 5 minutes on the loaded server against 9 s on a quiet one, with exactly the 208 findings the tree had once the apply was done. For a check between apply blocks, check the snapshot instead: about 4 s, no server reads.

`--live` keeps the old read: one pass, and a type whose count changes during the read stops it with exit `2`.

A `gwt-apply` snapshot (`gwt-apply --save-snapshot`, or `--save-snapshot NEW --since OLD` right after an apply) is a tree too, for `TREE`, `--since` and `--baseline`. Checking one needs no server and takes seconds. A snapshot from before snapshots kept repositories is refused: take a new one.

## Check after a write

A writer that keeps a dump from before it writes does not need to read the whole tree again to check what it wrote:

```sh
gwt-check dump PRE.json.gz                                # before the write (or keep the last POST)
# ... the apply writes ...
gwt-check check --from PRE.json.gz --save-dump POST.json.gz
```

`--from` loads `PRE` instead of paging through every type, then makes it match the live tree the same way a live read puts its own pages right: one list of `change` stamps per type, a single request each, and a read of whatever moved, appeared or went away. On an unchanged tree that is one request per type, whatever the tree's size; a write adds one request per 100 changed objects of a type, and one for the members of the families it changed. A full read pages through every object, so it grows with the tree. `POST` is then the `PRE` of the next write.

Two kinds of write leave a stamp that says nothing moved, and `--from` reads them again anyway:

- A stamp counts whole seconds, so an object written again in the second the earlier read saw it keeps the stamp the file holds. Every dump `gwt-check` writes records `read_started`, the Unix time its read began, and `--from` reads again every object stamped at or after that moment minus 2 s. A file without it (written by an older `gwt-check`, or a `gwt-apply` snapshot) is taken to have started its read an hour before its `dumped_at` or `taken`, with a warning; one with neither is refused.
- Writing a family edits its members' family lists, and nothing proves their stamps move with it. So the father, mother and children of every family that moved or went away, before and after the write, are read again. A live read does the same on every pass.

Objects read again this way that come back unchanged do not count as moved, so they never add a pass. The one exception is a lock note left behind by a `gwt-apply` that died: `--from` leaves it out of the tree it starts from, so it reads that note again every time and takes one more round of stamp lists. The note is still in the tree, so that reread puts it in `POST`, and the next `--from` pays the same round until the note is removed.

`read_started` comes from the clock of the machine that ran the read and stamps from the server's clock, so keep both in step (NTP).

`--from` refuses a `.gramps` backup, a file of another tree than the one the login lands in, a `TREE` argument and `--live`, all with exit `2`. By default the findings `PRE` already had count as known, so the exit code reflects what the write added.

It trusts the stamps for everything else. A change that reaches the database without moving its object's stamp, outside the two cases above, stays invisible to it. Keep a periodic full read (`gwt-check check` with no `--from`) as the safety net, and start the chain again from its `--save-dump`.

## Options of `check`

| Option | What it does |
|---|---|
| `TREE` | A dump file or a `gwt-apply` snapshot. Without it, the live tree is read |
| `--since OLD` | An earlier dump, a `gwt-apply` snapshot, or a `.gramps` backup. Adds the diff, and objects new since `OLD` become the scope |
| `--tag NAME` | Objects carrying this tag become the scope. `gwt-apply --tag` puts the tag only on the objects a run creates, so old objects the run changed are not in it: use `--since` to see those. With `--tag`, the diff counts only tagged objects as changed or new |
| `--root ID` | Gramps ID of the Ahnentafel root. With `--since`, a number that is lost or now holds another person is a finding |
| `--known FILE` | Findings to accept: an earlier `--json` report, or a list of `{"check", "key"}` |
| `--ignore-check NAME` | Leaves a whole check out of the exit code (repeatable). For a check that is noise in your tree, such as `private` after an import that marks every living person private on purpose. The report still counts the findings and prints `ignored NAME: N finding(s)`; a name that is not a check exits 2 |
| `--baseline OLD` | An earlier dump: the findings it already had count as known, so the exit code reflects only what is new since then. The same as running `check OLD --json` and passing that report to `--known`, in one command. The usual call is `--since OLD --baseline OLD`. It runs the checks twice, so a check takes about twice as long |
| `--json FILE` | The full report as JSON (`-` prints it instead of the text) |
| `--save-dump FILE` | Keeps the live dump, to use as `--since` or `--from` next time |
| `--from OLD` | A dump or `gwt-apply` snapshot of the same tree: start from it and read again only what changed since, instead of the whole tree ([below](#check-after-a-write)). Implies `--baseline OLD` unless `--baseline` or `--known` is given |
| `--limit N` | Findings printed per check (default 30); the JSON has all of them |
| `--pagesize`, `--workers` | Objects per request (default 1000) and requests in flight (default 4) |
| `--live` | The old strict read of the live tree, which stops when the tree changes during it |

**Scope.** The structural and dated checks always run on the whole tree. The fuzzy duplicate checks (`dup-couple-names`, `dup-person-birth`, `dup-person-relative`, `dup-person-fuzzy`, `dup-vital-event`, `dup-citation`) report a pair only when at least one side is in scope, so a run is not blamed for duplicates that were already there. Without `--since` or `--tag`, everything is in scope.

**Reasons.** Rows of a known file may carry a `reason`, and the report prints it (`known N0099: kept on purpose`). `gwt-check mark-known REPORT.json --check NAME --key KEY [--key KEY ...] --reason "..." --out known.json` adds findings of a report to a known file with the reason; it refuses a key the report does not have and a blank reason, and adding one twice changes nothing.

**Parent and child.** When one of a same-name pair is a parent of the other in the tree, a shared relative no longer makes them a duplicate (`dup-person-relative`, and the shared-relative branch of `dup-person-fuzzy`): a father and a son with one name share relatives by name across three generations. A shared birth date still does: `dup-person-birth` and "birth within 2 years" report the pair, because a person entered twice and then linked as their own child is a real duplicate.

**Known findings.** Every finding has a `check` and a `key` built from Gramps IDs, for example `repeated-ref` / `F0012 citation_list C0034`. The key stays the same from run to run while the problem does. Pass the first run's report as `--known` and the exit code then reflects only new problems.

## Places for events that have none (`fill-places`)

`gwt-check fill-places [DUMP]` reads a dump (or the live tree) and writes nothing to it. For each event with no place, it looks for a place named in the page text and the notes of the event's own citations, and prints the place with the passage that names it.

```sh
gwt-check fill-places dump.json.gz                    # proposals, with a quote each
gwt-check fill-places dump.json.gz --ops fill.jsonl   # the same as gwt-apply ops, for review
gwt-check fill-places dump.json.gz --json             # every proposal and every ambiguous event
```

- A candidate is a place that some event of the tree already uses, so a country or province that only encloses other places is never proposed on its own. Names of fewer than 4 letters are left out.
- Names are compared without case or accents, and only as whole words: `Villaejemplo` does not match inside `Villaejemplonuevo`.
- When the text names a place and also one that encloses it (`Villaejemplo, Provincia Imaginaria`), the enclosing one is dropped. One place left is a proposal. More than one, such as two towns with the same name, is listed as ambiguous with each place ID, and nothing is proposed.
- A place that only a flagged citation names (see `cites` below) is not proposed. It is listed under `held_back` in `--json` and counted in the text output. A place a clean citation of the same event also names is still proposed, from that citation's text.
- A citation can name where the record was made rather than where the event happened: a census town, the parish of a death record, a newspaper's city. Read each proposal against the record before you apply it.

`--ops FILE` writes one [`gwt-apply`](apply.md) `update_event` op per proposal, with `"ruling": ""`. `gwt-apply` refuses an op with no ruling, so the file applies only after someone has read each record, written the ruling and deleted the ops they reject. An `update_event` op replaces whatever place the event has when it is applied, so build the file from a fresh dump and apply it soon after; an event someone has placed in the meantime should have its op deleted. An existing file is never replaced.

The output quotes citations and notes, so it is as private as the tree.

## What the records say about parent links (`links`)

`gwt-check links [DUMP]` lists, for each child of each family, whether the text of the records names both parents. It reads the tree (a dump, or the live tree) and writes nothing. It never sets the exit code and never marks a link probable, proven or wrong.

```sh
gwt-check links dump.json.gz                      # counts per level, then the first rows
gwt-check links dump.json.gz --level flagged      # only the rows with a flag
gwt-check links dump.json.gz --json
```

For each link it looks at the link itself first: a citation on the child_ref (the link between the child and the family) puts the link at the top level, and a note on the child_ref that starts with `[probable]`, `[confirmed]` or `[wrong link]` (the marks `gwt-apply` writes) is shown as the row's `mark`. Then it reads the page text and the notes of the citations of the child's own birth or baptism (events where the child's role is Primary, so a sibling's baptism where the child stood godparent does not count), then of the child's other events, the child's own citations and the family's. Citations and notes marked private are not read. A parent counts as named when one sentence of that text holds the parent's first surname and either every given name, or one of the given names and a filiation word (`hijo`, `hija`, `padres`, `son of`, `filius`...), ignoring case and accents and words of fewer than 3 letters. Old spellings, Latin forms and abbreviations of a name are not matched, so a parent written differently in the record shows a level lower: the matching stays strict on purpose.

| Level | Meaning |
|---|---|
| the link itself is cited | the child_ref carries a citation |
| birth record names both | the citations of the child's birth or baptism name both parents |
| other record names both | both are named, but not both in the birth record |
| a record names one | only one parent is named anywhere |
| cited, names neither | the birth or baptism has a citation that names neither parent |
| no record names a parent | no citation text names a parent |

These are words in text. A record can name two people as parents of someone else, mention them in passing or deny the link, and a link proved by a document that is in no citation (an identity card, a family book) shows as the lowest level. Rows carry a flag to read first when:

- the link is marked `[probable]` or `[wrong link]`;
- the tree marks the relation as anything but by birth (adopted, foster, stepchild); empty and `Unknown` count as by birth, as in the other checks;
- the child has more than one parental family (each is its own row);
- a parent's name has no word of 3 or more letters, so it can never match.

The output holds IDs, levels and flags only, no record text.

## One act beside the tree (`act`)

`gwt-check act ACT.json DUMP [--person GRAMPS_ID]` sets one transcribed act beside the tree and says, field by field, what agrees. It reads a dump (`gwt-check dump`, or `check --save-dump`) and writes nothing; one act is not worth a whole live read, and one dump serves every act you check that day. It never sets the exit code: a disagreement is something to read, not a failure.

`ACT.json` is written by whoever read the act. Nothing here reads images.

```json
{"kind": "birth", "date": "1900-03-01", "place": "Villaejemplo", "reference": "folio 1",
 "subject": {"given": "Lucia", "surname": "Ortega"},
 "father": {"given": "Mateo", "surname": "Ortega"}, "mother": {"given": "Clara", "surname": "Sanz"}}
```

`date` is the day of the event the act records. `registered` (optional, same shapes) is the day the act was written. It is used only to explain a differing tree date: when the tree's date is that day and not the event's, the report says so. `kind` is `birth`, `baptism`, `death`, `burial` or `marriage` (a marriage names a `spouse` instead of parents). `date` is `YYYY`, `YYYY-MM` or `YYYY-MM-DD`; `place`, `reference`, `father` and `mother` are optional. `place` is one place name, as the tree spells it (`Villaejemplo`, not `Villaejemplo, Provincia Imaginaria`).

```sh
gwt-check act act.json dump.json.gz --person I0001
gwt-check act act.json dump.json.gz --json
```

- `--person` names the tree person the act is about, and no name search is made; the report notes when the act's name does not fit that person. Without it, a person is a candidate when every word of the act's name is in the tree's primary name, ignoring case and accents. The act may give fewer names than the tree. For a marriage act, only namesakes who have a family with the named spouse are kept, unless none has one.
- Only the person's own events count (event refs with role Primary): a godparent's ref to a baptism is not the godparent's baptism.
- For each candidate's matching event, the report says whether the date is the same, compatible but not equal (a year against a day), different, or missing in the tree; whether the place is the same, encloses the tree's place (a province against a village) or differs; whether the parents have names that agree in at least one of the person's parental families (every family is read, and the report names which agree and which do not); and whether the page text or notes of a citation of the event contain the act's `reference` as whole words (`folio 1` does not match `folio 15`). Agreeing names are not proof that two people are the same or related, and a string found in a citation does not show the record was checked against the act.
- A subject who is not in the tree is reported as absent. Nothing proposes a person, a family or a link from one act.
- When the name fits two people, both are listed and neither is chosen. When no one fits but the tree has people whose names are shorter than the act's (the act says "Armand Francois", the tree "Armand"), they are listed with a note and none is taken as the subject.
- A person marked private is counted, so a private namesake still keeps the verdict at "several", but only their ID is shown. Private citations and notes are not searched for the reference.
- The text output lists 30 people (`--limit`); `--json` has all of them.
- The report quotes the tree, so it is as private as the tree.

## Finding repositories

`gwt-check repos` reads a dump or the live tree and prints each repository with its source count. It never writes.

```sh
gwt-check repos dump.json.gz --name archives          # part of the name, compared like people's names
gwt-check repos --type Archive --unlinked             # live tree; adds the unlinked lists
gwt-check repos dump.json.gz --json                   # repositories with their sources, call numbers and media types
```

`--unlinked` adds the sources that list no repository and the repositories no source lists. Fix them with `link_repository` and `merge_repository` in [`gwt-apply`](apply.md).

`--classify` stands alone and only reports. It sorts the sources with no repository by what their own title, author and publication info say: sources that name a repository the tree has (by its name as whole words or by its web host; a letter or a request to an archive can name it without being kept there, so read each one before linking), institutions named that are not a repository of the tree (grouped, so one new repository can serve many sources; the opening words are in one table, `INSTITUTION_WORDS` in `gwt_check/repos.py`), and sources that name no institution. It also lists repositories with no URL and no address, two repositories on one web host, and how many linked sources have no call number. Nothing is written; link with `link_repository` in [`gwt-apply`](apply.md).

## The checks

| Check | Fires on |
|---|---|
| `orphan-event` | An event that no person or family lists |
| `orphan-citation`, `orphan-note`, `orphan-media`, `orphan-repository` | A citation, note, media object or repository nothing refers to |
| `dangling-ref` | A handle that points at nothing, in any list or sub-reference: `child_ref`, `event_ref`, names, attributes, `media_ref`, `placeref`, `reporef`, tags, a citation's source, a family's father or mother |
| `place-not-place` | A `place` field that holds a handle of another type, or of nothing |
| `citation-no-source` | A citation with an empty source |
| `birth-death-pointer` | A birth or death pointer out of range, or pointing at an event that is not a `Primary` birth or death; or a `Primary` birth or death with the pointer at -1 |
| `repeated-ref` | The same reference twice in one list: a child in a family, a family in `family_list` or `parent_family_list`, a citation in a `citation_list` |
| `family-backlink` | A person and a family that disagree about who is in it |
| `empty-family` | A family with no father, mother or child |
| `dup-couple` | Two families with the same father and mother |
| `dup-couple-names` | Two families whose couples are different people with the same two names |
| `dup-person-birth` | Two people with the same name and a birth or baptism in the same year (day and month must not contradict) |
| `dup-person-relative` | Two people with the same name and a parent, spouse or child of the same name, unless their birth years differ |
| `dup-person-fuzzy` | Two people whose names differ only by an extra given name or surname (every word of the shorter given name is in the longer one, and the same for the surnames), born within 2 years of each other, or with a spouse or child of the same name, or a parent of the same name who is a different person. Siblings share their parents, so the same parent does not count. A pair is left out when each side is the birth child of a family with both a father and a mother, and the two fathers have different names and the two mothers have different names: those are namesakes, two people. Only the first family a person is a birth child of counts, and adoptive, step or foster parents never do. If a parent is missing in that family on either side, or one parent's name matches, the pair is still reported. The pairs left out are counted in the report (below) |
| `dup-vital-event` | One person (role `Primary`) or family (role `Family`) with two Birth, Death, Baptism, Burial or Marriage events |
| `living-media-sensitive` | Media of a person with no death or burial event of their own (role `Primary`) who was born within 110 years (or has no dated birth), whose path, description or attributes hold an ID number or an ID or address word. Media already private (the person, the media or the reference) is skipped. It reads metadata only, never the image or its notes, and never prints the match. The patterns are Spanish-first: a DNI or NIE counts only with the right check letter, and the words are passport, home-address and ID-card terms in Spanish, English and French, plus `c/` before a street name. Another country adds its ID number to `ID_NUMBERS` and its words to `ID_WORDS` in `check/gwt_check/checks.py`. Accept a false positive with `--known` |
| `private` | Any object or sub-reference marked private |
| `dup-repository` | Two repositories with the same name, compared like people's names (below) |
| `dup-citation` | For information only. Two or more citations of one source with the same page text, ignoring case and repeated spaces (`folio 12v` and `folio 12` are different pages). Usually one record entered twice; merge them, or keep both with `--known` when they cite different entries on one page |
| `place-no-coords` | A place that an event uses, without latitude or longitude |
| `surname-marker` | A surname containing `[`, such as `Vidal [¿?]` |
| `missing-object` | With `--since`: an object of the earlier tree that is gone |
| `ahnentafel-lost`, `ahnentafel-changed` | With `--since` and `--root`: a number that disappeared, or holds another person |
| `ahnentafel-truncated` | With `--root`: the pedigree passes 100,000 Ahnentafel positions (pedigree collapse, or someone made their own ancestor), so the walk stopped there. With `--since`, only the generations both trees walked in full are compared. It fails the run, in a `--since --root` run and a `--root` run alike; accept it with `--known` and the key `root <ID>` |
| `unconnected` | For information only, it never sets the exit code. A group of people joined to each other by families (as father, mother or child) with no family link to the main group. The main group is the `--root` person's, or without `--root` (or with a root in no family) the largest one, the one with the lowest Gramps ID on a tie. One finding per other group, keyed by its lowest Gramps ID in numeric order (`I9001` before `I10000`), with its size and first five IDs in the detail, so the key holds while the group grows. It finds a batch written with no link to the main family, such as a research branch waiting for a documentary bridge; it does not find a person written into the wrong tree and linked to one of its families, who joins the main group like anyone else. People in no family are left out. A link through an event role (a godparent, a witness) or an association does not count, so a couple tied to the tree only as godparents is its own group. With `--since` or `--tag` it reports only the groups holding a person or a family in scope |
| `event-order` | A person's own dates in an order no reading allows: death before birth or baptism, baptism before birth, burial before death. Dates are compared as intervals (below) |
| `child-before-parent` | A child born before their father or mother |
| `child-after-parent-death` | A child born after the mother's death, or more than 300 days after the father's |
| `marriage-before-birth`, `marriage-after-death` | A Marriage event before a spouse's birth or after their death (or burial) |
| `own-ancestor` | A person who is their own ancestor through the families' father, mother and child links. Keyed by the IDs in the loop |
| `parent-age` | For information only. A mother under 14 or over 52, or a father under 14 or over 85, at a child's birth |
| `marriage-age` | For information only. A bride under 12 or a groom under 14 at the marriage: the canon-law minimum before 1917. A person of unknown sex is held to 12 |
| `sibling-spacing` | For information only. Two children of one mother born less than 240 days apart and more than a day apart (twins). Birth events with an exact day only: a baptism can be late |
| `lifespan` | For information only. A life of more than 105 years, from birth or baptism to death or burial |

Names are compared lowercased, without accents, with b/v and i/y folded and the particle "de" dropped. `dup-person-fuzzy` also drops "del", "la", "las", "los" and "y", and matches "Pedro Lasa" with "Pedro José Lasa Ibarra". For a pair to count, at least one side must be in scope, or belong to a family that is. That covers an old person the run gave a new spouse or new parents. A different spelling of the same word ("Lasa" against "Lassa") is still not matched. A run that allowed namesakes still needs its homonyms read one by one.

The report also gives, without failing: events without a place (in the whole tree and in scope), the `dup-person-fuzzy` pairs left out because their fathers and mothers differ by name (a count in the text report, the pair keys under `info` in `--json`, so a namesake entered with the wrong parents can still be read), sources without a repository (in a tree that has repositories: a source is linked only when its repository is certain, so some stay unlinked), the objects changed and new since `--since`, the last changed object (it should be the last item applied), and the Ahnentafel counts.

## Dated checks

The dated checks read every date as the interval it may stand for, and report two facts only when their intervals cannot overlap, so a vague date never produces a contradiction. A day is one day, a month or a year is the whole month or year, a range runs from its first day to its last, and "before" and "after" are open on one side. An "about" or estimated date gets 2 years of slack either side, and a calculated one (usually worked out from a stated age) 1 year. A text-only date is read only when it is a plain ISO date (`1878`, `1878-05`, `c. 1878`). Dates in another calendar, with a new year other than 1 January, dual-dated (`1699/1700`), or "from" and "to" are not read; the report counts them (`dates not read`).

The detail of each finding names the event types, IDs and dates behind it, and ends in `(firm)` when every date is a plain day, month or year, or `(soft)` when one has slack. A person with no Birth event is taken as born up to a year before their baptism, an infant baptism: a mother who died in childbirth and a child baptised the next day is no contradiction. A death with no Death event is bounded by the burial. Parent rules apply to children whose relation to that parent is `Birth`, `Unknown` or blank, so an adopted or stepchild is left out.

Each key is built from Gramps IDs: the family, the parent and the child for the parent rules, the person and both events for `event-order`. Between write blocks, `gwt-check check --baseline <dump before the block>` fails only on the contradictions the block wrote: a child linked to a father who died years before, or to a mother of 9, is the usual mark of a namesake or a godparent linked as a parent. Two Birth or two Death events on one person are left to `dup-vital-event`, whatever their dates.

A year worked out from an age (a marriage index that gives the bride's age) should be written as `calc 1801` or `abt 1801`, never as a plain year: a plain year is read as firm, and one that falls after the person's dated baptism is an `event-order` finding.

## Self-test

`gwt-check selftest` builds an invented three-generation tree, checks that it comes out clean (the positive control), then plants 54 errors, one at a time and then all together, and checks that each comes out as its finding. One plant must stay quiet instead: two namesakes whose fathers and mothers have other names must not come out as `dup-person-fuzzy`, and must be listed among the pairs left out. Its counterparts must still fire: the same pair with same-named parents, with only the mother's name or only the father's name in common, and with other-named parents where one side is an adopted child. The test suite also silences each check in turn and requires the self-test to fail, so a check without a planted error cannot slip in.

## Speed

On a tree of 13,035 people, 3,874 families, 10,831 events, 9,120 citations, 5,589 media and 18,294 notes (about 60,000 objects), read over the internet from one Gramps Web server:

| Reading | Requests | Time |
|---|---|---|
| 100 per page, 4 in flight | 620 | 349 s |
| 2000 per page, 4 in flight | 38 | 126 s |
| 1000 per page, 4 in flight (default) | 69 | 109 s |
| 1000 per page, 8 in flight | 69 | 64 s |

The first page of every type goes out together, then every page still missing, `--workers` at a time. On a small tree (about 260 people, 2,000 notes, 10 types) that read took 20 s with the types in a row and 14 s with them together; the rest is the stamp pass that follows, one list per type, which still runs one type after another. `check --from` skips the paged read and runs only that stamp pass, so it is no faster.

Each request costs about 1.3 s before the first object, so small pages waste most of the time. The checks themselves take under a second, and reading a saved dump about one. Large pages are safe without `profile=all`. It was `profile=all` that answered 500 on a whole-tree listing.

A request that fails on the network (a timeout, a reset connection, an SSL read error) is tried again, the same as a 429 or a 5xx: up to 6 tries per request, waiting 5, 10, 20, 40 and then 60 s between them, or as long as a `Retry-After` header says. Only a page that fails all 6 stops the run, with exit code 2 and the last error. On a loaded server, where a 1000-object page can take minutes, read with `--pagesize 100 --workers 1`: smaller pages answer within the timeout, and nothing else competes with them.

## A `.gramps` backup as the earlier tree

`--since` reads a Gramps XML backup (`POST /api/exporters/gramps/file`) as well as a dump. From the backup it takes handles, IDs, change times and family links, which is all the diff and the Ahnentafel need. The checks themselves need a JSON dump, so a backup can only be the earlier tree.

## Citations that do not fit their event (`cites`)

`gwt-check cites [DUMP]` reads a dump (or the live tree) and writes nothing. It never sets the exit code. It lists each event whose citation's page (its title) says the citation is about another kind of act. Read the act before you act on a row.

```sh
gwt-check cites dump.json.gz          # one line per row
gwt-check cites dump.json.gz --json   # the same rows as JSON
```

| rule | fires when |
|---|---|
| `other-act` | the citation's page names a kind of act (birth, death, marriage) and none of them is the event's kind; a birth event cited by a marriage act holds that act's place and date |
| `marginal-mention` | a death or marriage is cited by a marginal mention in another act; the act's place and date are that act's |

- Only the page is read, never the citation's notes, so the rules work on any tree whatever its note style.
- The words that name a kind of act are in one table, `ACT_WORDS`, at the top of `gwt_check/cites.py`, and the words of a marginal mention in `MARGINAL` beside it. A language that is not in them is not read. A word matches as a whole word, singular or plural (`nacimiento`, `nacimientos`), never inside a longer word (`birthplace` is not a birth act).
- `fill-places` holds back every row `cites` lists: a place that only such a citation names is listed under `held_back`, not proposed.
