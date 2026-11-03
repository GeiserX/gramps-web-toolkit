# gwt-lint evidence rules

Four [gwt-lint](lint.md) rules compare what a change list says with the records you saved:

- **quote**: a quotation that drifted from the record (one accent changed, a word added, a phrase from another record).
- **principal**: a citation that points at the father's, mother's or godparent's persona instead of the record's principal, or at a record of another event.
- **role**: a godparent written down as a parent, and a record whose index gives the principal two parents of the same sex.
- **placement**: a baptism placed on the town, the province or the country instead of the church, a birth placed on a church, and a church that is not the one the record names.

They read files only. Nothing is fetched, and there is no separate index to rebuild: the rules read the records the way `gwt-read` saved them.

```sh
gwt-lint changes.jsonl --snapshot tree.json.gz --records saved/ --rules quote,principal,role,placement
gwt-lint changes.jsonl --snapshot tree.json.gz --records saved/ --readings readings.tsv --rules quote
gwt-lint --prove changes.jsonl --snapshot tree.json.gz --config lint.toml --rules quote,principal,role,placement
```

## Saved records

`--records DIR` (or `records = "saved"` in lint.toml, read relative to the file) names a folder. Everything below it is read:

| What | How it is read |
|---|---|
| `<key>.json` that `gwt-read` writes | the GEDCOM X records inside its `json` answer (a search or one record), and its `text` |
| `<key>.candidates.json` | each FamilySearch candidate row: one persona, its role, its principal flag, its typed place |
| a raw GEDCOM X record (`{"persons": [...]}`) | every persona, relationship and field |
| a `gwt-fscache` folder | each entry's `.body`, the answer as the site sent it |
| `*.txt` | a transcription or OCR text. It belongs to the ark in its file name or on its first line (`ark:/61903/3:1:...` for an image); with none, to every op that cites no ark |

Save a record before you cite it: `gwt-read familysearch record 1:1:XXXX-XXX --out saved/`.

**NOREC.** An op that cites a FamilySearch record ark with no saved record makes the rule exit 2, with the command that saves it. A rule that could not look at the record is not a clean rule. A missing `--records`, or a folder with no records in it, exits 2 the same way.

What an op cites: every FamilySearch ark anywhere in it (citation page, note text, event description, `probable_reason`, any reason), plus the arks of what it attaches to in the same list (a note on `key:c1` cites what `c1` cites) or in the snapshot (a citation's page), plus, for an event the list adds, the arks of the citations the list puts on it.

## quote

Every quotation in new text (`«…»`, `“…”`, `"…"`, four words or more, the same spans [living](lint.md#living) reads) must occur, inside one field, in what the op cites: the cited record's fields and the transcriptions saved for its arks. An op that cites no ark is checked against every saved text. The only change allowed is Unicode NFC and runs of spaces or line breaks: case and accents must match. `…`, `...`, `[…]` or `[...]` inside a quotation skip words; the parts must appear in order.

A quotation whose only source is an image (it cites a `3:1:` image ark or a media object, and no record ark) and that no saved transcription holds needs a **signed reading**: a row in the `--readings` TSV (or `[quote] readings` in lint.toml) with the quotation, the image's sha (hex, 8 characters or more) and who read it. A row without a reader or a sha does not count.

```text
quote	image sha	reader
nota marginal de difícil lectura aquí	3f9a0c1b2d4e	A. Reader
```

| Code | When |
|---|---|
| `quote-notfound` | the quotation is in none of the cited record's fields or transcriptions |
| `quote-image` | a STOP: an image-only quotation without a signed reading. Read the image, add the row, or drop the quote marks |

## principal

Every FamilySearch record ark (`1:1:`) in an op must be a persona the record marks as principal. The fix printed is the principal's ark. A spouse's persona is fine for a marriage event or a family. When the op speaks about an event (an `add_event`, or a citation or note on an event), the principal must have a fact of that type: a baptism record (Christening or Baptism) backs a Baptism and, when it gives one, a Birth; it does not back a Death.

| Code | When |
|---|---|
| `principal-nonprincipal` | the ark is the persona of someone who is not the principal (a parent, a godparent, a witness) |
| `principal-event` | the principal has no fact of the event type the op speaks about |

A candidate row from a search knows its persona's role and principal flag, not the principal's ark. Save the record itself to get the ark in the fix.

## role

For each persona the op cites that is not the principal, the role the text gives it is compared with the record. The text gives a role when a role word from the [locale tables](rules.md) (padre, madrina, godfather, testis, ...) stands within three words of the persona's name; a `link_child` or `add_family` that cites the persona gives it a parent's role. The record's role comes from its relationships (father, mother, spouse, child of the principal), then the role the index gives the persona.

| Code | When |
|---|---|
| `role-mismatch` | the list calls a persona father, mother or another role, and the record gives it another one (a godparent, a witness) |
| `role-samesex` | a cited record gives its principal two parents of the same sex: the index likely read a godparent as a parent. Check the image |

## placement

Each event the list places (`add_event` with a place, `update_event` setting one) is checked by its type:

| Event types | Must sit on |
|---|---|
| Baptism, Christening, Confirmation, First Communion (`[placement] church`) | a place whose type is Church, Parish or Chapel (`[placement] church_types`) |
| Birth, Death (`[placement] civil`) | a place that is not a church |

When a sacrament sits on a church and the event cites a record that keeps the place as the index typed it (`place_orig`, the `EVENT_PLACE_ORIG` and parish fields), the church must match it. Both names are folded and the locale's abbreviations written out (`Sta.` becomes `Santa`, see `dedication()` in the [shared rules](rules.md)); church words (iglesia, parroquia, ermita, church, ...) and particles at either end are dropped; what is left must appear whole in the typed place. Titles count, so «Santo Cristo del Valle» is not «Santísimo Cristo del Valle». A generic dedication, one with only titles left («Iglesia de Santa María»), never matches by itself: the town the church sits under must also be in the typed place.

When the index typed the place wrongly, give an override for that op, with the page's own words:

```toml
[placement.overrides.e-bap-pedro]          # the op's key
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

Each rule's mutant is built from your saved records, so `--prove` shows the rule can still go red on them: quote quotes a saved field with one accent changed, principal cites a parent's or godparent's persona, role calls a persona by a role the record does not give it, and placement puts a baptism on a new town. A folder with no persona that is not a principal cannot prove principal or role.

The tests use invented records in [lint/fixtures/evidence](https://github.com/GeiserX/gramps-web-toolkit/tree/main/lint/fixtures/evidence), written by `make_fixtures.py` there. Every name, place and ark in them is made up.
