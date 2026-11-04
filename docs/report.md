# gwt-report

Two questions come back again and again while a tree grows: *how are these two people related?* and *what is new since we last told the family?* Both used to mean walking the tree by hand. `gwt-report` answers them from an offline file and never touches the tree.

Standard library only. Both subcommands read a `gwt-check dump` or a `gwt-apply` snapshot, gzipped or not. A file in another shape, or a Gramps id that is not a person in it, exits `2`.

## kin: how two people are related

```sh
gwt report kin [--lang en|es|fr] [--json] [--weak-tag TEXT] DUMP A B
```

Gramps Web already names the relationship between two people, in every language Gramps has a calculator for (`/api/relations/HANDLE1/HANDLE2` in its API). Use that when the name in your language is all you need. `kin` is for the rest of the answer, offline: every path, how solid each link is, the Ahnentafel numbers on both sides, and implex.

It prints every blood relationship between `A` and `B`, nearest first. For each one:

- **the name of what B is to A**, in English, Spanish or French (`--lang`, default `en`; `--json` gives all three). It follows B's gender when the tree records it;
- **the common ancestors**: the couple, or the one person, where the two lines meet, with the Ahnentafel number of each seen from A and from B (A is 1, a father is 2n, a mother 2n+1);
- **the path from each side**, as Gramps ids up to the common ancestor;
- **how solid the path is**: how many of its parent links are `confirmed`, `probable`, `weak` or `unmarked`.

Then it lists **implex** in A's and B's pedigrees: an ancestor or a couple reached at two or more Ahnentafel numbers, because two of their descendants married. Only the lowest repeated couple is listed, not every ancestor above it, who is repeated too.

```text
$ gwt report kin report/fixtures/kin-tree.json A3 B4
Arno3 Field (A3) -> Bea4 Hill (B4)
  second cousin once removed
    common ancestors: Adam Root (I1) and Eve Stone (I2); Ahnentafel from A 8-9, from B 30-31
    path from A: A3 > A2 > A1 > I1
    path from B: B4 > B3 > B2 > B1 > I1
    links: 1 confirmed, 1 probable, 1 weak, 4 unmarked
```

With implex there is one relationship per line of descent. In the invented tree, X1's parents are first cousins, so A1 is X1's great-uncle twice, and the pedigree shows Adam and Eve at two places:

```text
$ gwt report kin report/fixtures/kin-tree.json X1 A1 --lang fr
Xena Root (X1) -> Arno1 Field (A1)
  grand-oncle
    common ancestors: Adam Root (I1) and Eve Stone (I2); Ahnentafel from A 8-9, from B 2-3
    ...
  grand-oncle
    common ancestors: Adam Root (I1) and Eve Stone (I2); Ahnentafel from A 14-15, from B 2-3
    ...
  implex in A's pedigree: Adam Root (I1) and Eve Stone (I2) at 8-9, 14-15
```

Two people with no common ancestor print `no blood path`. The exit code is `0` either way.

### What counts as a blood path

- A path goes from child to parent through the person's parent families, never through a marriage. A spouse, a spouse's parents and a spouse's siblings are in-laws, never blood kin.
- A parent link counts when its relation (`frel` for the father, `mrel` for the mother) is `Birth`, `Adopted`, `Unknown` or empty. `Stepchild`, `Foster` and `Sponsored` never do. A path through an adoption is named as usual and says `(through an adoption)`.
- Two lines that meet below a common ancestor (they share a person on the way up) belong to that nearer ancestor: a direct line is one relationship, not one more per generation above. A line counts as soon as the other side reaches the ancestor without that person, so double cousins (two siblings who married two siblings, or a cousin who also descends from a sibling of your grandparent) get both relationships.
- **Half**: the two lines reach the shared ancestor through two different families, or through one family where both parents are recorded but only one is shared. A child of a family that records only a father is not called half: the mother is unknown, not different.
- A person recorded as their own ancestor (bad data) stops that path; it never loops. Paths stop at 30 generations. The walk goes one generation at a time and keeps 2,000 paths per person, nearest first, so on a big pedigree only the most distant relationships are left out, and a note says the list was cut. `digest` says so too, under the list.

### How solid a path is

Each child-to-parent link of the path is one of:

| Class | When |
|---|---|
| `weak` | The child or the family carries a tag whose name holds `hipotesis`, `hypothes`, `weak`, `debil` or `faible` (accents and case ignored, so `Hipótesis` and `r1-débil` count). `--weak-tag TEXT` replaces that list; repeat it for several |
| `probable` | A note on the link (the child_ref) starts with `[probable]`, the mark `gwt-apply link_child` writes with `probable: true` |
| `confirmed` | A note on the link starts with `[confirmed]` (what `update_child_ref` with `confirm_probable` writes), or a citation on the link has confidence 3 or 4 |
| `unmarked` | None of these |

The first class that applies wins, in the order of the table.

### Relationship names

B is what the name says, seen from A. `g1` is the number of generations from A up to the common ancestor, `g2` from B.

| g1, g2 | English | Spanish | French |
|---|---|---|---|
| 0, 1 and 1, 0 | son, father | hijo, padre | fils, père |
| 0, 2+ and 2+, 0 | grandson, great-grandson, great-great-grandson, 3x great-grandson | nieto, bisnieto, tataranieto, then *descendiente en N generaciones* | petit-fils, arrière-petit-fils, arrière-arrière-petit-fils, then *descendant à la Ne génération* |
| 1, 1 | brother, half-brother | hermano, medio hermano | frère, demi-frère |
| 1, 2+ | nephew, great-nephew, great-great-nephew | sobrino, sobrino nieto, sobrino bisnieto | neveu, petit-neveu, arrière-petit-neveu |
| 2+, 1 | uncle, great-uncle, great-great-uncle | tío, tío abuelo, tío bisabuelo | oncle, grand-oncle, arrière-grand-oncle |
| 2, 2 and 3, 3 and 4, 4 | first, second, third cousin | primo hermano, primo segundo, primo tercero | cousin germain, cousin issu de germain, cousin issu d'issu de germain |
| unequal, both 2+ | second cousin once removed: the degree comes from the nearer person, the gap is how removed | sobrino or tío with the generation gap and the ordinal of the nearer person: the child of a primo hermano is a *sobrino segundo*, a parent's primo hermano a *tío segundo*, a grandparent's primo hermano a *tío abuelo segundo* | the cousin of the nearer person, then *une génération au-dessus* or *au-dessous* |

Feminine forms follow B's gender (*prima segunda*, *cousine issue de germain*, *demi-sœur*). With no gender recorded English uses *sibling*, *cousin*, *parent*, and Spanish and French use the masculine form.

## digest: what is new since the family was last told

```sh
gwt report digest --from OLD --to NEW [--root I0001] [--base-url URL] [--lang en|es|fr]
                  [--told FILE --audience NAME [--mark]] [--format md|html] [--include-private]
```

It lists the people, events, citations and media in `NEW` that `OLD` lacks (by handle), grouped by the person they belong to:

- a new person is their own group;
- an event belongs to the people whose event list holds it, or to both spouses of a family event;
- a citation or a media object belongs to whoever holds it, through an event or a citation when that is where it hangs; a citation on a parent link belongs to the child.

With `--root` the groups are split by side (`father's side`, `mother's side`, `both sides`, `descendants`, `not blood kin`), ordered nearest first by the number of generations between the person and the root, and headed with the relationship name in `--lang`. A person whose path to the root has a probable or weak link says how many. Without `--root` there is one group, `other`, by Gramps id. The side names above are the English ones. `--lang` (English by default) sets the language of the title, side headings, relationship names, item labels, `[weak]` and the cut note; event types, source titles, pages, places and descriptions are printed as the tree stores them, so a Spanish digest still says `Baptism`.

Each item has its Gramps Web link (`URL/person/I0012`, `URL/event/E0045`, `URL/citation/C0012`, `URL/media/O0003`) when `--base-url` is given, and the web addresses its citations hold in their page, attributes or notes. An address the item's own text already shows, such as one written in a citation's page, is not added again. A weak-tagged object, or a citation of confidence 0 or 1, says `[weak]` (`[débil]`, `[faible]`). Objects and people marked private are left out unless `--include-private`.

Only new objects are listed. A changed name, a corrected date or an old event newly linked to someone is not. `OLD` and `NEW` must be copies of the same tree: two snapshots that name different trees exit `2`, and an `OLD` taken after `NEW` gets a warning that they may be swapped.

```text
$ gwt report digest --from report/fixtures/digest-old.json --to report/fixtures/digest-new.json \
    --root I1 --base-url https://gramps.example.org
# New in the tree since 2026-01-01T00:00:00+00:00

## father's side

### Paul Gale (I5), grandfather https://gramps.example.org/person/I5

- new event: Baptism, 1890, Greenford https://gramps.example.org/event/E1 https://archive.example.org/register/12.jpg
- new citation: Greenford parish register, folio 12, confidence 3 https://gramps.example.org/citation/C1 https://archive.example.org/register/12.jpg
- new media: Register page 12 https://gramps.example.org/media/O1

### Lena Gale (I12), first cousin https://gramps.example.org/person/I12

- new person: Lena Gale https://gramps.example.org/person/I12

## mother's side

### Rosa Pine (I8), aunt (path: 0 confirmed, 0 probable, 1 weak, 2 unmarked links) https://gramps.example.org/person/I8

- new person: Rosa Pine [weak] https://gramps.example.org/person/I8

## not blood kin

### Tim Reed (I11) https://gramps.example.org/person/I11

- new person: Tim Reed https://gramps.example.org/person/I11
```

`--format html` writes the same lines with only the inline tags chat apps accept: `<b>` for the headings, `<u>` for the side, `<i>` for a note and `<a href>` for every link, including an address inside the item's text, which is linked where it stands. There is no page around it (no `<html>`, `<h1>` or `<ul>`), so it pastes as it is into a Telegram message sent in HTML mode or into an email.

### What was already told

`--told FILE` is a tab-separated file of `audience`, `object id` (the Gramps id) and `date`, with an optional header row. With `--audience NAME`, every object that audience was told is left out; rows of other audiences do not count. A missing file means nobody was told anything yet. `--mark` appends one row per item the digest shows, with today's date, so the next digest for that audience starts after it. A row with fewer than two cells, a told file that is not UTF-8 or cannot be read, and a `--mark` target that cannot be written exit `2`. A file edited by hand without a last newline is fine: `--mark` starts its rows on a new line. `--told` needs `--audience`, and `--audience` and `--mark` need `--told`.

```text
audience	object	date
cousins	I12	2026-02-03
cousins	E1	2026-02-03
```

The fixtures behind these examples are invented and made by `report/fixtures/make_fixtures.py`.

## public-preflight: structural checks before a deceased-only public workflow

```sh
gwt report public-preflight candidate.csv --surname-required
```

This offline mode reads the Spanish-column CSV staging schema used for independent,
source-backed profiles. It makes no network calls, does not change the tree, emits
no upload payload, and never approves publication. Structurally valid rows are
`pending-review`, with `publish_allowed: false` even when the command exits `0`.
Exit `2` means structural blockers, an invalid header or an unreadable file.

Rows keep literal names and separate date indicators (`exacta` or `antes de`). The
command does not infer a surname, repair a date, copy a modifier from another event,
infer relationships, merge people or deduplicate a destination. `--surname-required`
blocks a blank surname when that destination requires one. Without it, a blank surname
requires `VACIO_NO_INFERIR_APELLIDO`. Relationships must be `NINGUNA`.

Source URLs must parse as HTTPS with a nonempty hostname and no embedded credentials; this does not authenticate the host or fetch it. Missing source/citation fields, unsupported status/deceased-basis labels, missing
claimed two-reading agreement, invalid calendar dates/indicators, schema differences,
Unicode Cc control characters (including DEL and C1) and repeated internal IDs or literal identities block rows.
Duplicates block every involved row rather than silently choosing one. The output
contains only row numbers and fixed reason codes, not names, identifiers, notes or
source URLs. No example or test uses a real family record.

A CSV claim such as `2+` is **not** independent reading evidence. The labels are
structural checks, not proof someone is deceased. Every row still needs authenticated
independent source readings, checked deceased evidence, a destination schema contract
and destination duplicate checks. Those contracts and the write workflow are outside
this first patch. Passing this command must never trigger a public upload.

### WikiTree attestation contract (still blocked for publication)

`--wikitree-evidence evidence.json` adds structured checks and always requires a
literal surname. `--exclude-id LOCAL_ID` is repeatable for private exclusions; do
not put a family's excluded identities in source code, fixtures or public logs.

The JSON shape is `{"format":"gwt-public-evidence/1","people":{...}}`, keyed by
local CSV `id_gramps`. Each value has:

- `record`: an `ark` beginning `ark:/`, or nonblank `film` and `image`;
- `readings`: at least two objects with distinct `reader`, the same `record`, and
  `values` equal to the CSV's literal given/surname, both event kinds, both dates
  and both date indicators. Free-text notes cannot replace these fields;
- `primary_date_basis`: `primary-record` or `secondary-only`; the latter requires
  the primary date indicator `antes de`, not an invented exact birth/death date;
- `privacy`: `Open` when the birth is over 150 years before the cutoff;
- `relationships`: an empty list;
- `match_search`: `completed: true`, an ISO `checked_at`, and `matches: []`.
  Any match blocks creation rather than choosing or merging a profile.

Death/burial event evidence or a birth age strictly greater than 110 at the cutoff
is required. Pre-1700 dates stay held unless structured `owner_certification` has `pre1700_self_certified: true`, a real `date` no later than cutoff, and a nonblank `reference` to the owner decision. This is an attestation to verify, never an instruction inside a source document. Execution guidance is one create per step and
moderate site-specific pacing; this command performs zero creates and enforces
no remote request cadence. All attestations still require independent review:
JSON can lie, an ARK-shaped string is not a verified act, and an empty match list
is not a live destination search. No preflight result authenticates these claims.

FamilySearch GEDCOM preparation is not implemented here. A separate contract must
cap batches at about 100 profiles, standardize dates/places and include sources as
text, with no living people or relationships. Neither this mode nor exit `0`
authorizes a FamilySearch import.

Exclusions apply in both structural-only and evidence modes; IDs are stripped the same way as internal duplicate IDs. Age thresholds use dates after the 110th/150th anniversary, not completed whole ages. For a leap-day birth in a non-leap anniversary year, this begins on March 1. Future death/burial events block the row and cannot establish deceased status at the cutoff.

Missing per-person evidence accumulates all applicable codes rather than stopping early. Match searches must be no later than cutoff and within `--max-search-age-days` (default 7, configurable 0-365). Date-only timestamps mean midnight UTC. These checks use the explicit cutoff, not the computer clock; an old packet still requires fresh validation before publishing.
