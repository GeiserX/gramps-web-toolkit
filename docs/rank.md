# gwt-rank: strong matches from a page of search results

A search by name returns thousands of rows, and a lane that stops at "N results" has found nothing. `gwt-rank` reads the visible text of a FamilySearch-style results page and keeps the rows that agree with what the tree already knows.

```sh
gwt-rank page.txt --name "Antonin Lechat" --facts "by=1860 bp=Tours dy=1933" [--spouse "Rose Bellanger"]
```

`--facts` is the `facts:` prefix `gwt-leads` puts in a task note (`by`/`dy` year, `bp`/`dp` place, spaces as `_`). Only the first `--top` rows (20) are scored. A row counts only when it is the principal of its record, and its surname and given name match (accents and case ignored). A birth or death year within 3 years adds a point and an exact year two, a year further away VETOES the row (it is never strong, however much else matches), a place match adds one (the tree's birth place against the row's birth place, its death place against the death place, never across events), a spouse match adds two (only from a line that says Spouse or Spouses, never from Parents or other text). Surnames match as a whole token or an exact split (Le Chat = Lechat), never as a substring. A page whose rows carry no readable event is treated as not loaded (exit 2). Rows at or above `--min` (3) print best first with the reasons.

Exit codes: `0` with strong rows, `1` when the page parsed and no row is strong, `2` when the page holds no records. A `2` means not loaded or not a results page and is never a negative; a `1` means only "no strong lead in the first rows", not that the person is absent. A strong row is a lead for the verifier and the judge, not an identification.
