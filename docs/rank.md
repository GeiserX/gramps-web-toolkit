# gwt-rank: order search results by how well they fit

A search by name returns thousands of rows, and a lane that stops at "N results" has found nothing. `gwt-rank` reads the rows and puts first the ones that agree with what the tree already knows. It only orders. Every row is printed, so a page with no strong row is still a list of leads to read.

```sh
gwt-fscache get --offline "$SEARCH_URL" | gwt-rank - --given "Maria del Carmen" --surname Perez --surname Lopez \
    --facts "by=1901 bp=Villazul,_Murcia dy=1970" --spouse "Juan Ruiz"
gwt-rank page1.json page2.json --given Ana --surname Perez     # several pages of one search
gwt-rank results.txt --given Ana --surname Perez               # page text, for sites with no JSON
```

## Input

- **FamilySearch search JSON**, the GEDCOM X body of a `/service/search/hr/` request, as [gwt-fscache](fscache.md) stores it. It has no language, so the web UI can stay in any language. Each entry gives the persona the search matched: the principal of the record, or a relative (Father, Mother, Spouse, Other). Only that persona's own facts are scored, never another person's in the same record. Spouses come from the record's Couple relationships.
- **Page text**, the fallback for pages with no JSON (MyHeritage, other sites): the visible text of a results page, with Spanish or English labels (`Principal` or `Persona principal`, `Birth` or `Nacimiento`, `Baptism` or `Bautismo`, `Spouse` or `Cónyuge`). A row starts at a name line followed by a role line. When the page puts a blank line between them, only that shape starts a row, so a `Cónyuge` label inside a row is read as a field. The first line of a row is the collection title and is never read as an event. Text layouts vary, so a JSON source is always better.

## The person

- `--given` is the given name as in the tree. Any part counts: `Maria del Carmen` matches a row indexed as `Carmen`. A close spelling (Antonin and Antoine) is a variant: it is listed but never strong.
- `--surname` is the first surname, and given again for the second. A match on the first surname counts as a surname match, so `Fernandez Rubio` matches a row indexed as `Fernandez`. A match on the second surname alone is noted and caps the row at `possible`. Surnames match as whole tokens or an exact split (`Le Chat` = `Lechat`), never as a substring.
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

Within a tier, rows go by points, then by the search's own order. TSV columns are tier, points, record id, name, role, birth year, birth place, death year, death place and the reasons. `--format json` prints one object per row with the spouses too. A summary of the counts per tier goes to stderr.

## Exit codes and the ledger

- `0`: at least one row is strong. Each strong row is a lead for the verifier, not an identification.
- `1`: rows exist and none is strong. Record the search in [gwt-ledger](ledger.md) as `partial`, never `none`. The rows were read, and a person indexed under a variant or with thin facts may still be among them.
- `2`: nothing to rank: the search returned 0 rows, the file is not a results page, or the page did not load. Never read this as a negative. Whether the search itself found nothing is the caller's call, from the search, not from this tool.
