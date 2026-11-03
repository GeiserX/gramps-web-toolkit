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

With `--root` the groups are split by side (`father's side`, `mother's side`, `both sides`, `descendants`, `not blood kin`), ordered nearest first by the number of generations between the person and the root, and headed with the relationship name in `--lang`. A person whose path to the root has a probable or weak link says how many. Without `--root` there is one group, `other`, by Gramps id. Headings and item text are in English; only the relationship name follows `--lang`.

Each item has its Gramps Web link (`URL/person/I0012`, `URL/event/E0045`, `URL/citation/C0012`, `URL/media/O0003`) when `--base-url` is given, and the web addresses its citations hold in their page, attributes or notes. An address the item's own text already shows, such as one written in a citation's page, is not added again. A weak-tagged object, or a citation of confidence 0 or 1, says `[weak]`. Objects and people marked private are left out unless `--include-private`.

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
