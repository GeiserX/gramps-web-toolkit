# gwt-lint evidence rules

Four [gwt-lint](lint.md) rules compare what a change list says with the records you saved:

- **quote**: a quotation that drifted from the record (one accent changed, a word added, a phrase from another record).
- **principal**: a citation that points at the father's, mother's or godparent's persona instead of the record's principal, or at a record of another event, or that hangs on someone the record does not name.
- **role**: a godparent written down as a parent, and a record whose index gives the principal two parents of the same sex.
- **placement**: a baptism placed on the town, the province or the country instead of the church, a birth placed on a church, and a church that is not the one the record names.

They read files only. Nothing is fetched, and there is no separate index to rebuild: the rules read the records the way `gwt-read` saved them.

```sh
gwt-lint changes.jsonl --snapshot tree.json.gz --records saved/ --rules quote,principal,role,placement
gwt-lint changes.jsonl --snapshot tree.json.gz --records saved/ --readings readings.tsv --rules quote
gwt-lint --prove changes.jsonl --snapshot tree.json.gz --config lint.toml --rules quote,principal,role,placement
```

## Saved records

`--records DIR` (or `records = "saved"` in lint.toml, read relative to the file) names a folder. Everything below it is read, through symlinked folders too, so one folder of links can gather several:

| What | How it is read |
|---|---|
| `<key>.json` that `gwt-read` writes | the GEDCOM X records inside its `json` answer (a search or one record), and its `text` |
| `<key>.candidates.json` | each FamilySearch candidate row: one persona, its role, its principal flag, its typed place. A row is used only for an ark no full record holds: it has no facts and no relationships |
| a raw GEDCOM X record (`{"persons": [...]}`) | every persona, relationship and field. A fact on a relationship (FamilySearch puts a marriage on the Couple) counts for both people in it |
| a `gwt-fscache` folder | each entry's `.body`, the answer as the site sent it |
| `*.txt` | a transcription or OCR text. It belongs to the ark in its file name or on its first line (`ark:/61903/3:1:...` for an image), else to the web address on its first line (a bulletin's PDF, an archive's image viewer); with neither, to ops that cite nothing |

Save a record before you cite it: `gwt-read familysearch record 1:1:XXXX-XXX --out saved/`. A JSON file of another shape is skipped; when an ark is then missing, the NOREC message names the first file that could not be read.

Every file is read on each run, so point `--records` at the records a list cites, not at a whole `gwt-fscache` of tens of thousands of answers.

**NOREC.** An op that cites a FamilySearch record ark with no saved record makes the rule exit 2, with the command that saves it. A rule that could not look at the record is not a clean rule. A missing `--records`, or a folder with no records in it, exits 2 the same way, but only when the list cites a record or quotes one. A list that does neither (a tree-only list, a DNA import, a geocoding list) needs no saved records, and `placement-town` and `placement-civil` read only the snapshot.

What an op cites: every FamilySearch ark anywhere in it (citation page, note text, event description, `probable_reason`, any reason; not `expect`, the old value an update replaces), plus the arks of what it attaches to in the same list (a note on `key:c1` cites what `c1` cites) or in the snapshot (a citation's page), plus, for an event the list adds, the arks of the citations the list puts on it.

## quote

Every quotation in new text (`«…»`, `“…”`, `"…"`, four words or more, the same spans [living](lint.md#living) reads) must occur, inside one field, in what the op cites: the cited record's fields, the transcriptions saved for its arks, and the transcriptions saved for the other web addresses it cites. A quotation from a source with no saved text (a bulletin, an archive's image) makes the rule exit 2 (NOREC) with the address: save a `.txt` whose first line is that address. A quotation in an op that cites nothing at all is `quote-notfound` unless a transcription tied to nothing holds it: it is never matched against unrelated records. Shorter spans (one to three words, and an empty `«»`, the usual index-field drift such as a surname, a date or `None`) are checked only when the op cites a record ark, as `quote-short`; elsewhere `«…»` also marks titles and names. The only change allowed is Unicode NFC and runs of spaces or line breaks: case and accents must match. `…`, `...`, `[…]` or `[...]` inside a quotation skip words; the parts must appear in order.

A quotation whose only source is an image (it cites a `3:1:` image ark or a media object, or a media sits on the citation it hangs on, and it cites no record ark) and that no saved transcription holds needs a **signed reading**: a row in the `--readings` TSV (or `[quote] readings` in lint.toml) with the quotation, the image's sha (hex, 8 characters or more) and who read it. A row without a reader or a sha does not count. A file saved with a byte-order mark (Excel) reads the same.

```text
quote	image sha	reader
nota marginal de difícil lectura aquí	3f9a0c1b2d4e	A. Reader
```

| Code | When |
|---|---|
| `quote-notfound` | the quotation is in none of the cited record's fields or transcriptions, or the op cites no source at all |
| `quote-short` | a short or empty quoted span next to a cited record is not in it |
| `quote-image` | a STOP: an image-only quotation without a signed reading. Read the image, add the row, or drop the quote marks |

## principal

Every FamilySearch record ark (`1:1:`) in an op must be a persona the record marks as principal. The fix printed is the principal's ark. A spouse's persona is fine for a marriage event or a family, one the list adds included. `attach_existing` of a citation checks that citation's page. In free text, a persona named with a role word next to its name («padrino Tomás Brin <ark>») is a statement about that persona: [role](#role) judges it, and principal leaves it alone. A citation's page or a reason (a link's `probable_reason`, `new_couple_reason`) is never excused that way: there the persona is the evidence, so it must be the principal. When the op speaks about an event (an `add_event`, or a citation or note on an event), the principal must have a fact of that type: a baptism record (Christening or Baptism) backs a Baptism and, when it gives one, a Birth; it does not back a Death.

| Code | When |
|---|---|
| `principal-nonprincipal` | the ark is the persona of someone who is not the principal (a parent, a godparent, a witness) |
| `principal-event` | the principal has no fact of the event type the op speaks about |
| `principal-unnamed` | a citation of the record hangs on a person the record does not name |

Every person a citation of a record hangs on (an `add_citation`'s targets, or the targets `attach_existing` hangs a citation on) must be one of the record's personas: two words of a name in common, or every word of a one-word name, against the person's primary name and alternate names. A citation on an event speaks of the event's primary people, or of the parents of the family whose event it is; on a family, of its parents; on a child_ref, of the child. Each person is checked on their own, so one person the record names does not cover another it does not, and each person the record does not name is one `principal-unnamed`.

A candidate row from a search knows its persona's role and principal flag, not the principal's ark. Save the record itself to get the ark in the fix. A candidate row holds one persona only, so it never makes anyone `principal-unnamed`: save the record to have the people beside the principal checked.

## role

For every record an op cites, each persona that is not the principal is compared with what the op says about it, whichever persona's ark the op carries. So a note that cites the principal's ark and says «padre: Tomás Brin», when the record gives Tomás Brin as the godfather, is a finding. The text gives a persona a role when a role word from the [locale tables](rules.md) (padre, madrina, godfather, testis, ...) stands within three words of where the text names it: a word of its name next to another (a namesake sharing only the surname is not the persona). A role word followed by «de», «di», «of» (the `of` words of the locale tables; one word may sit between, as in «hija legítima de») reads one way, and both sides are taken as said of the record's principal: «X, hija de Y» calls X a child, and the person named right after «de» a parent. «Ana, hija de Luis Zorvalo» is no finding when the record gives Luis Zorvalo as the father, and «Ana, hija de Tomás Brin» is one when it gives Tomás Brin as the godfather. When the other side names another persona of the record («Luis Zorvalo, marido de Rosa Quirell»), the words say how those two stand to each other, not to the principal, so neither is judged. A word that means several roles across locales means any of them («padre» is a father in Spanish and Italian, a priest in Portuguese). A `link_child` or `add_family` whose father or mother has the name of the record's godparent, witness or priest makes that persona a parent. The record's role comes from its relationships (father, mother, spouse, child of the principal), then the role the index gives the persona; a persona the record gives no role is not judged.

| Code | When |
|---|---|
| `role-mismatch` | the list calls a persona father, mother or another role, or makes it a parent, and the record gives it another one (a godparent, a witness) |
| `role-samesex` | a cited record gives its principal two parents of the same sex: the index likely read a godparent as a parent. Check the image |

## placement

Each event the list places (`add_event` with a place, `update_event` setting one) is checked by its type:

| Event types | Must sit on |
|---|---|
| Baptism, Christening, Confirmation, First Communion (`[placement] church`) | a place whose type is Church, Parish or Chapel (`[placement] church_types`) |
| Birth, Death (`[placement] civil`) | a place that is not a church |

When a sacrament sits on a church and the event cites a record that keeps the place as the index typed it (`place_orig`, the `EVENT_PLACE_ORIG` and parish fields), the church must match it. Both names are folded and the locale's abbreviations written out (`Sta.` becomes `Santa`, see `dedication()` in the [shared rules](rules.md)); church words (iglesia, parroquia, ermita, church, ...) and particles at either end are dropped; what is left must appear whole in the typed place. Titles count, so «Santo Cristo del Valle» is not «Santísimo Cristo del Valle». A generic dedication, one with only titles left («Iglesia de Santa María»), never matches by itself: the town the church sits under must also be in the typed place.

When the index typed the place wrongly, give an override for that op, with the page's own words. It is keyed by the list's file name and the op's key, because op keys repeat across lists and lint.toml serves them all (an op cannot carry it: gwt-apply refuses fields it does not know):

```toml
[placement.overrides."changes.jsonl".e-bap-pedro]   # the list's file name, then the op's key
place = "Ermita del Santo Cristo del Valle"
quote = "en la ermita del Santo Cristo del Valle"   # must be in the cited record or the citation's page
```

| Code | When |
|---|---|
| `placement-town` | a sacrament on a town, municipality, region or country. The fix names the church the record typed |
| `placement-civil` | a civil event on a church |
| `placement-parish` | a sacrament on a church the record's typed place does not name |
| `placement-override` | an override with no quote, or a quote that is on neither the record nor the citation's page |

## Settings

```toml
records = "saved"                    # --records

[quote]
readings = "readings.tsv"            # --readings

[placement]
church = ["Baptism", "Christening", "Confirmation", "First Communion"]
civil = ["Birth", "Death"]
church_types = ["Church", "Parish", "Chapel"]
locales = ["es", "en"]               # dedication abbreviations from these tables; default: every locale
```

## Proving the rules

Each rule's mutant is built from your saved records, so `--prove` shows the rule can still go red on them: quote quotes a saved field with one accent changed, principal cites a parent's or godparent's persona, role calls a persona by a role the record does not give it, and placement puts a baptism on a new town. A folder with no persona that is not a principal cannot prove principal or role, and with no saved records only placement can be proved.

The tests use invented records in [lint/fixtures/evidence](https://github.com/GeiserX/gramps-web-toolkit/tree/main/lint/fixtures/evidence), written by `make_fixtures.py` there. Every name, place and ark in them is made up.
