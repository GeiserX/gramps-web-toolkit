# gwt-import-dna

Gramps Web has a DNA view: open a person, and it lists their DNA matches with the shared segments drawn on a chromosome map. It has no importer, though, so a tester with hundreds of matches would have to type them in one by one. `gwt-import-dna` reads the match lists a testing site lets you download and writes a [gwt-apply](apply.md) change list that fills the DNA view. It never writes to Gramps itself. Python 3.11+, standard library only.

```sh
gwt-apply --save-snapshot tree.json.gz                       # or a gwt-check dump
gwt-import-dna --site myheritage --tested I0001 --snapshot tree.json.gz --out dna-mh.jsonl \
    matches.csv segments.csv
gwt-apply dna-mh.jsonl --snapshot tree.json.gz --private-new  # dry run first, as always
gwt-apply dna-mh.jsonl --private-new --apply
```

`--tested` is the Gramps ID of the person who took the test. Pass the match list, the segment file, or both; the tool tells them apart by their header (a file with a chromosome column is a segment file).

## What each match becomes

Every op names the ruling `gwt-import-dna` (change it with `--ruling`). For one match, with all names invented:

```jsonl
{"op": "add_person", "key": "dna-myheritage-D-11AA", "first_name": "Tom", "surname": "Sample", "tag": ["dna-match"], ...}
{"op": "add_association", "key": "dna-myheritage-D-11AA-I0001-assoc", "person": "I0001", "target": "key:dna-myheritage-D-11AA", "relation": "DNA", ...}
{"op": "add_note", "key": "dna-myheritage-D-11AA-I0001-summary", "type": "Research", "text": "gwt-import-dna site=myheritage id=D-11AA cM=48\nMyHeritage DNA match of I0001 (kit I0001).\nShared: 48 cM in 3 segments; longest 22.1 cM.\n...", "attach": [{"person": "key:dna-myheritage-D-11AA"}], ...}
{"op": "add_note", "key": "dna-myheritage-D-11AA-I0001-segments", "text": "Chromosome,Start Location,End Location,Centimorgans,Matching SNPs\n2,30000000,52000000,22.1,4000\n...", "attach": [{"association": {"person": "I0001", "target": "key:dna-myheritage-D-11AA", "relation": "DNA"}}], ...}
```

- **The person.** A match not yet in the tree becomes a loose person, in no family, tagged `dna-match` (`--tag`, or `--tag ''` for none). A match whose name is someone else's in the tree, or another new match's, gets `allow_namesake` with the reason "a DNA match: another person unless placed" and every namesake in `namesake_of`, so gwt-apply's preflight lets it through without merging them. A match with no usable name becomes `(no name, <site> <match id>)`.
- **The association.** One `add_association` from the tested person with relation `DNA`, which is what the DNA view lists. gwt-apply skips it when the tested person already has it.
- **The summary note.** A Research note on the match person: the site, the tested person and kit (`--kit`, default the tested ID), the shared cM, segments and longest segment, and the site's estimated relationship or generations and ancestral surnames when the file has them. Its first line, `gwt-import-dna site=<site> id=<match> cM=<cM>`, is how a later import finds this person again; leave it as it is.
- **The segment note.** When segments are known, a note on the association with one segment per line, chromosome order, X after 22. This is the text the DNA view parses. The format is fixed (one header, then whole numbers and a cM per line), and the tests pin it to a string Gramps Web's parser, `POST /api/parsers/dna-match`, was seen to accept, read back with a copy of that parser (`gramps_webapi/api/dna.py`).

The keys hold the site and the match id, so a rerun of the same export produces the same keys and gwt-apply skips everything it already made.

## Matches already in the tree

A match is pointed at instead of made again, in this order:

1. **`--id-map TSV`**: lines `site<TAB>match id or name<TAB>Gramps ID`, for a match you placed yourself, or one of your own kits. Lines for other sites are skipped, so one file serves every site. A Gramps ID not in the snapshot stops the run, and so does a name that two matches in the file share (map those by id). A match that maps to the tested person gets no association (a person is never their own match).
2. **The same site, an earlier import.** A person whose note starts with the mark line for this site and match id. A second kit of the same site (a parent's test) gets its own association and notes on the same person.
3. **Another site.** A person whose mark is from another site, with the same name (case, accents and punctuation folded) and a close cM: both share at least 20 cM and the two differ by at most 30 % of the larger, because sites count cM differently. When two people qualify, neither is used: the match becomes a new person and the report lists it under `cross_ambiguous`. Same name with a far cM is another person, made with `allow_namesake`. Below 20 cM a name proves nothing (most matches are small, and common names repeat among them), so a small match is always a new namesake person; join the two by hand, or with `--id-map` on the next run, once you know they are one.

Import one site, apply it, take a new snapshot, then import the next site from it: the second site finds the first site's people only in the snapshot.

## Columns

Columns are found by their header, folded (lowercase, no accents or punctuation), so `Shared DNA (cM)` and `shared dna cm` are one. A match list needs a name or id column and a shared cM column; a segment file needs a chromosome, start, end and cM column and a match id or match name column. `--column FIELD=HEADER` names a column for any field, in each file that has that header (a match list and its segment file can name a field differently, so `--column id=Ref` for the match list leaves the segment file's id column alone; the run stops only when no file has `HEADER`). It is how a file from another site or a third-party tool is read (`--site other`, or `livingdna` and `ancestry`, whose own sites offer no match download).

| Site | Match list headers read | Segment file headers read |
|---|---|---|
| `myheritage` | `DNA Match ID`, `Name`, `Shared DNA` (`3.2% (227.6 cM)` reads as 227.6), `Shared segments`, `Largest segment`, `Estimated relationship` | `DNA Match ID`, `Match name`, `Chromosome`, `Start Location`, `End Location`, `Centimorgans`, `SNPs` |
| `ftdna` | `First Name`, `Middle Name`, `Last Name` (or `Full Name`), `Shared DNA` or `Shared cM`, `Longest Block`, `Relationship Range`, `Ancestral Surnames` | `Match Name`, `Chromosome`, `Start Location`, `End Location`, `Centimorgans`, `Matching SNPs` |
| `gedmatch` | `Kit` or `kit_num`, `Name`, `Sex`, `Total cM` or `shared_cm`, `Largest Seg` or `largest`, `Gen` (the one-to-many download, old and current layout) | `MatchedKit`, `MatchedName`, `chr`, `B37Start`, `B37End`, `Segment cM`, `SNPs` |

Fields and every header they accept: `gwt-import-dna --help` lists the fields; the header lists are `MATCH_COLUMNS` and `SEGMENT_COLUMNS` at the top of `dna/gwt_dna.py`. Sites rename columns now and then; when a file stops reading, `--column` fixes it at once, and an issue gets the new header added.

Segments join their match by id when both files have one, else by name. A segment file alone (GEDmatch's segment search) makes its matches from the segments: cM is their sum. When two matches share a name and the files have no id (FamilyTreeDNA), both are imported as two people, but their segments are held back and listed under `segments_ambiguous_name`: put them in by hand.

## Matches are living people

A DNA match took a test, so they are alive ([shared rules](rules.md): a DNA match counts as possibly living). Only the columns above are read: contact links, e-mail addresses, countries, locations, ages, tree names and "managed by" never reach the change list, and the run prints them under `columns not read`. A display name that is an e-mail keeps only the part before `@`; a run of six or more digits in a name (a phone or an identity number) is removed; a place a site prints in brackets after an ancestral surname, `Sample (Ohio)`, is removed. Apply with `--private-new` so a guest login never sees the new people and notes.

## Output and report

`--out` is JSON Lines. When the list is longer than `--batch` ops (default 1,500), it is split into `<out>-01.jsonl`, `<out>-02.jsonl`... in order, never splitting a match's ops; apply them in that order, since a namesake of a later file only names earlier ones. With `--force`, a rerun that writes fewer files than the last one removes the old extra files, so none of them gets applied by mistake. `--report FILE` writes JSON with the counts, `in_tree` (and how each was found), `cross_site` (both cM), `cross_ambiguous`, `self`, `namesakes`, and under `columns` each file's columns read and ignored and its `skipped_rows` (rows with neither a name nor an id). Neither file is overwritten without `--force`. Exit 0 on success, 2 on a bad file or option (a missing file or column, a tested ID not in the snapshot). A match id listed twice (the same file passed twice) is read once and listed under `duplicate_ids`.

## Limits

- No harvesting: it reads files you downloaded. A site that offers no download (Living DNA, AncestryDNA) needs a third-party export and `--column`.
- A match is never placed in a family. Research the relationship first, then link the person with gwt-apply.
- Each site's segments go in their own note, so the DNA view draws a match found on two sites with both sets.
