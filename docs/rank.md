# gwt-rank: order search results by how well they fit

A search by name returns thousands of rows, and a lane that stops at "N results" has found nothing. `gwt-rank` reads the rows and puts first the ones that agree with what the tree already knows. It only orders. Every row is printed, so a page with no strong row is still a list of leads to read.

```sh
gwt-fscache get --offline "$SEARCH_URL" | gwt-rank - --given "Maria del Carmen" --surname Perez --surname Lopez \
    --facts "by=1901 bp=Villazul,_Murcia dy=1970" --spouse "Juan Ruiz"
gwt-rank page1.json page2.json --given Ana --surname Perez     # several pages of one search
gwt-rank results.txt --given Ana --surname Perez               # page text, for sites with no JSON
```

## Input

Every input is first turned into the normalized candidate record below, one per row, and the scorer reads only that record. Three inputs are accepted, told apart by content:

- **Normalized records**, a JSON object `{"candidates": [...]}` holding records in the shape below. This is what a normalizer for another site (MyHeritage, for one) writes.
- **FamilySearch search JSON**, the GEDCOM X body of a `/service/search/hr/` request, as [gwt-fscache](fscache.md) stores it. It has no language, so the web UI can stay in any language. Each entry gives the persona the search matched: the principal of the record, or a relative (Father, Mother, Spouse, Other). Only that persona's own facts are scored, never another person's in the same record. Spouses come from the record's Couple relationships.
- **Page text**, the fallback for pages with no JSON (MyHeritage, other sites): the visible text of a results page, with Spanish or English labels (`Principal` or `Persona principal`, `Birth` or `Nacimiento`, `Baptism` or `Bautismo`, `Spouse` or `Cónyuge`). A row starts at a name line followed by a role line. When the page puts a blank line between them, only that shape starts a row, so a `Cónyuge` label inside a row is read as a field. The first line of a row is the collection title and is never read as an event. Text layouts vary, so a JSON source is always better.

## The candidate record

```json
{
  "source": "familysearch",
  "id": "ABCD-123",
  "name": "Ana Perez Lopez",
  "role": "Principal",
  "principal": true,
  "given": "Ana",
  "surnames": ["Perez Lopez"],
  "events": {
    "birth": {"year": 1901, "year_to": 1901, "place": "Villazul, Murcia, Spain"},
    "death": {"year": 1970, "year_to": 1972, "place": ""}
  },
  "relatives": [{"role": "spouse", "name": "Juan Ruiz"}, {"role": "father", "name": "Luis Perez"}]
}
```

| Field | Meaning |
|---|---|
| `source` | Where the row came from: `familysearch`, `text`, or the normalizer's own name. Printed, never scored |
| `id` | The source's record id, so the row can be opened again. May be empty |
| `name` | Full name as indexed. Required. Used for matching when `given` or `surnames` is missing |
| `role` | The candidate's role in the record, as the source words it (Principal, Father, Mother, Spouse, Other) |
| `principal` | `true` only when the candidate is the record's main person. Only a principal can be strong |
| `given` | Given name(s) as one string, or `null` when the source gives only the full name |
| `surnames` | List of surname strings, or `null`. One entry may hold both surnames (`"Perez Lopez"`): each tree surname is matched against every token |
| `events.birth`, `events.death` | The candidate's own birth and death, each optional. `year` and `year_to` give a range (equal for a plain year), `place` the place as written. A normalizer picks the stand-in itself: baptism or christening for birth, burial or cremation for death |
| `relatives` | People named in the record with the candidate's relation to them: `spouse`, `father`, `mother`, `parent`, `child`, `other`. Only `spouse` is scored today |

A record must carry only the candidate's own facts. A child's baptism year in a record where the candidate is the father belongs to the child, never to the candidate. A record that breaks this shape (no `name`, `surnames` as a string instead of a list, a year that is not a number, a relative that is a bare string) makes the run exit 2, and so does any other malformed input file. Exit 1 always means rows were read.

## The person

- `--given` is the given name as in the tree. Any part counts: `Maria del Carmen` matches a row indexed as `Carmen`. When a row has only a full name (page text, or a record with `given` null), only the words before the first surname are read as the given name, so the tree's `Martin Garcia` never matches the row `Juan Garcia Martin` on its second surname. The first word always counts as given, so `Martin Martin Lopez` still matches a Martin whose first surname is Martin. A row written surname first with a comma (`Garcia Martin, Juan`) reads the part after the comma as the given name. A close spelling (Antonin and Antoine) is a variant: it is listed but never strong.
- `--surname` is the first surname, and given again for the second. A match on the first surname counts as a surname match, so `Zorvalo Brineda` matches a row indexed as `Zorvalo`. A match on the second surname alone is noted and caps the row at `possible`. Surnames match as whole tokens or an exact split (`Le Chat` = `Lechat`), never as a substring.
- `--facts` holds what the tree knows: `by` and `dy` years, `bp` and `dp` places, with `_` for spaces. A value with no plain year (`by=?`) is ignored, and `abt1880` reads as 1880. A Baptism or Christening stands in for a missing Birth, and a Burial or Cremation for a missing Death.
- `--spouse` is the spouse's given name and surname. The given name and a surname must both appear in one spouse entry of the row. A given name alone never counts.

## Tiers

Each agreement beyond the name counts once: birth year within 3 years, death year within 3 years, birth place, death place, spouse. A place agrees when the most specific part of the tree's place, the text before the first comma, is in the row's place. A row that only says `Spain` never agrees with `Villazul, Spain`. Each place is checked against its own event: the birth place against the birth, never the death.

| Tier | Means |
|---|---|
| `strong` | The principal of the record, first surname and given name match, and two or more agreements |
| `possible` | A surname and a given name or variant match, and at least one agreement. A relative who would otherwise be strong lands here |
| `weak` | Everything else. Still a row worth reading |
| `conflict` | A birth or death year more than 3 years from the tree's. Listed last, never hidden |

Within a tier, rows go by points, then by the search's own order. Tabs and line breaks inside a field print as a space, so every row is one TSV line. TSV columns are tier, points, record id, name, role, birth year, birth place, death year, death place and the reasons. `--format json` prints one object per row with the spouses too. A summary of the counts per tier goes to stderr.

## Exit codes and the ledger

- `0`: at least one row is strong. Each strong row is a lead for the verifier, not an identification.
- `1`: rows exist and none is strong. Record the search in [gwt-ledger](ledger.md) as `partial`, never `none`. The rows were read, and a person indexed under a variant or with thin facts may still be among them.
- `2`: nothing to rank: the search returned 0 rows, the file is not a results page, or the page did not load. Never read this as a negative. Whether the search itself found nothing is the caller's call, from the search, not from this tool.
