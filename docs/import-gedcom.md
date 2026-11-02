# gwt-import-gedcom

Turns a GEDCOM file (5.5.1 or 7) into a [gwt-apply](apply.md) change list for review. It never writes to Gramps. Anyone who looks like a person already in your tree is held back until you say who they are, so importing a GEDCOM a second time, or a cousin's GEDCOM that overlaps yours, does not fill the tree with duplicates. Gramps Web's own import never merges anyone. It also audits a GEDCOM before you import it, and splits one into separate trees. Python 3.11+, standard library plus gwt-apply.

```sh
gwt apply --save-snapshot tree.json.gz                  # or a gwt check dump
gwt import-gedcom audit cousin.ged                      # what is wrong with the file, first
gwt import-gedcom cousin.ged --tree tree.json.gz --batch cousin-ana \
    --out changes.jsonl --report report.json
gwt apply changes.jsonl --snapshot tree.json.gz         # dry run, as always
```

`--batch` names the import. It is part of every key, with the first eight characters of the file's SHA-256 and the record's own id: `ged-cousin-ana-1a2b3c4d-i12`. The same file under the same batch gives the same keys, so a rerun after an apply is a skip. An edited file gives new keys, and its people go through the duplicate check again; they are never merged by key. `--out` and `--report` are never overwritten without `--force`.

## People already in the tree

With `--tree`, each person in the file is compared with the tree the way gwt-apply's preflight compares them: the same full name, or the same first given name and first surname with a birth or baptism within two years, a parent in common or a spouse in common. A person who matches is not added. Their events, notes, citations and links wait with them, and the report lists them under `namesakes`:

```json
{"xref": "@I3@", "name": "Pedro García", "born": [1730], "namesake_of": ["I0007"],
 "why": ["same name"], "decide": "--same @I3@=<one of I0007>  or  --different \"@I3@=<why another person>\""}
```

Decide each one and run again:

- `--same @I3@=I0007`: that is the tree person. The file's families link to I0007, and nothing about I0007 is added: no second birth, no notes. Put new facts about them in with your own ops.
- `--different "@I3@=baptised in another parish"`: another person. They are added with `allow_namesake`, your reason and the IDs they were compared with, so gwt-apply lets them through.

Two records of the same file that look alike (a grandfather and a grandson with one name) are kept apart, because the file keeps them apart. They get `allow_namesake` with the reason `a separate record of the GEDCOM file`, and the report lists them under `file_namesakes`. Run `audit` first if you think the file has duplicates.

When the list would still be refused by gwt-apply's preflight (a couple that looks like a couple in the tree, after you said `--different` for both of them), the command prints why and exits 1.

People who may be living are held unless `--allow-living`, by the toolkit's [shared rule](rules.md): no death or burial, and born less than 110 years ago or with nothing dated around them. A person with no given name (`/García/` alone) is held too, and the family keeps the other parent.

## What it writes

| GEDCOM | Change list |
|---|---|
| `INDI` with `NAME`, `SEX` | `add_person`; a second `NAME` becomes `add_alternate_name` |
| `BIRT`, `CHR`, `BAPM`, `DEAT`, `BURI`, `RESI`, `OCCU` and the other events and facts, `EVEN`/`FACT` with `TYPE` | `add_event` with date, place and description (an `OCCU` or `RELI` value goes in the description). A second birth or death goes in a note: gwt-apply keeps one of each. The same event twice is one event with the sources of both |
| `FAM` with `HUSB`, `WIFE`, `CHIL` | `add_family`; a child whose `FAMC` says `PEDI adopted` or `foster` gets `link_child` with that relation. A family with no parent left to add is skipped and reported |
| `PLAC` | `add_place`, one per level of the comma list, country first, typed from the header's `PLAC.FORM` when it has as many parts (`Unknown` otherwise). Each level is first looked for in the tree under the same parent, by name without case or accents: one match is used, several leave the event without a place and keep `Place as written: ...` in its description. `MAP` coordinates go on the last level |
| `SOUR` record, `REPO` | `add_source` (title, author, publication), `add_repository` and `link_repository` with the call number |
| `SOUR` citation with `PAGE`, `QUAY`, `DATA` | `add_citation`, one per source, page, quality and date, attached to every person, family or event that cites it. `QUAY` 0-3 becomes confidence 0, 1, 3, 4 (2 when absent). One page cited with two qualities gets the quality in the second page text, as gwt-apply requires |
| `NOTE`, `SNOTE`, citation `TEXT` | `add_note`, a shared note attached to every record that points at it |
| `_FSFTID`, `_APID`, `EXID` with `TYPE` | `set_external_id` (FamilySearch, Ancestry, WikiTree, Geni, MyHeritage, Wikidata, or the `TYPE` as written). A FamilySearch id that is not one is reported, not written |
| `OBJE`, `SUBM`, `CHAN`, anything else | not imported; the report counts each under `not_imported` |

## Dates

GEDCOM dates become gwt-apply dates: `12 JAN 1850` is `1850-01-12`, `ABT`, `BEF`, `AFT`, `EST`, `CAL`, `BET ... AND` and `FROM ... TO` keep their meaning. Julian (`@#DJULIAN@`, `JULIAN`) and French Republican (`@#DFRENCH R@`, `FRENCH_R`) dates keep their calendar and sort on the Gregorian day they fell on: `@#DFRENCH R@ 1 VEND 12` is 1803-09-24. A dual year `1700/01` is read as 1701. An open `FROM 1850` is read as after 1850 and `TO 1850` as before it, and the report says so.

A date the tool cannot turn into a sortable date (`31 FEB 1850`, `Easter 1850`, a Hebrew date, a date before the year 1) is kept as text (`text: 31 FEB 1850`), never dropped and never guessed, and listed under `date_problems` with the reason. A date phrase alone, `(during the war)`, is kept as text the same way.

## Encodings

A byte-order mark wins, then the header's `CHAR`: UTF-8, UNICODE (UTF-16), ANSEL, ANSI (Windows-1252), ASCII, IBMPC, LATIN1. GEDCOM 7 is UTF-8. Files from some sites mix encodings, so each line is read on its own: a line that is UTF-8 in a Latin-1 or ANSEL file is read as UTF-8 (so `María` does not turn into `MarÃ­a`), and a line that is not valid in the declared encoding is read as Windows-1252. Each such line is listed with its number under the report's `file`, and by `audit`.

## Audit

```sh
gwt import-gedcom audit cousin.ged [--json]
```

Writes nothing and always exits 0. It reports the version and the encoding (declared, as read, and every line read in another one), records that look like the same person by the preflight's rules, dates that cannot be read, deaths before births, deaths past 110, children born when a parent was under 14 or over 60, or after the parent died (nine months after, for a father), people linked to nobody, pointers to records that are not in the file, and records nothing points at.

## Split

```sh
gwt import-gedcom split family.ged --root @I1@ --root @I40@ --out-dir trees/
```

Writes `trees/I1.ged` and `trees/I40.ged`, in UTF-8. A root's tree is everyone who descends from one of the root's ancestors: the root, the root's ancestors, and every descendant of each of them. Every spouse of those people comes along as a stub: their own record and the family they share go in, their parents and other marriages do not. The sources, notes and repositories the kept records point at go in too. People in both trees (a couple whose roots you split) are in both files, and the summary says how many.

`--root @I1@` on an import does the same cut and imports only that tree.

## Limits

- Media (`OBJE`) is counted, not imported: a GEDCOM holds file paths, not files. Add them with `add_media`.
- Hebrew, Islamic and other calendars are kept as text.
- A person matched with `--same` gets no new facts from the file; add them with ops of your own.
- The import holds the whole file in memory, which is fine for files of a few hundred thousand lines.
