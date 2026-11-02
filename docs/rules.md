# Shared rules and locales

Several tools answer the same questions: may this person still be alive, are these two names the same name, what date does this text give. Each tool used to keep its own copy of the answer, and the copies disagreed (one counted a person born 105 years ago as living, another did not). `gwt_core.rules` holds one answer for each, and `gwt_core/locales/` holds the words each language needs, so a new country is a table, not a code change in five tools.

It is a Python module, not a command. New tools import it; existing tools move to it one pull request at a time, so until a tool's page says it uses these rules, it still uses its own copy.

```python
from gwt_core.rules import possibly_living, fold, read_date, dedication, find_ids, Index

ix = Index(snapshot)                    # a `gwt-check dump` or a gwt-apply snapshot, loaded as JSON
possibly_living(person, ix)             # (True, "child-born-1990", 1976)
fold("Bermúdez de Castro", "es")        # "vermudez castro"
read_date("12 de marzo de 1890", "es")  # a Gramps Date dict for 1890-03-12
dedication("N.ª S.ª del Rosario", "es") # "nuestra senora del rosario"
find_ids("DNI 12345678Z")               # [("dni", "12345678Z")]
```

## Who may be living

`possibly_living(person, snapshot, years=110, gap=20, this_year=None, undated=True)` returns `(living, reason, bound_year)`. `reason` says why, so a tool can print it next to the person; `bound_year` is the latest year the person can have been born, from that evidence, or `None`. The rules run in this order and the first that applies decides:

| Rule | Result | Reason |
|---|---|---|
| A death, burial or cremation of their own | not living | `death-recorded`, `burial-recorded`, `cremation-recorded` |
| A dated birth, baptism or christening of their own | living if less than `years` ago | `born-1950`, `baptised-1950` |
| A birth «after Y» with Y less than `years` ago | living | `born-after-1950` |
| One end of a DNA association (an association whose relation is `DNA`) | living | `dna-match` |
| Other dated evidence (below) | living if the tightest bound is less than `years` ago | `census-1851`, `child-born-1990` |
| Nothing dated | `undated` | `no-dates` |

Every date is read by the latest year it allows, so an uncertain date can only make a person more likely living: `bet 1900 and 1930` and `from 1900 to 1930` count as 1930, `abt` and `bef` as the year written, and `aft 1900` gives no upper bound at all (it is living only when 1900 itself is inside the window). A date in another calendar (Julian, Hebrew, Islamic, Persian, French Republican) is read through its `sortval`, so an Islamic 1400 counts as 1979.

Other evidence, each the latest year the person can have been born. A relative's year is the earliest dated event of theirs, of any type: a child who died in 1750 was born by 1750.

| Evidence | Bound | Reason |
|---|---|---|
| Any other dated event of their own in Y (census, residence, occupation, will) | Y | `census-1851` |
| A child's event in Y | Y - 14 | `child-born-1990`, `child-death-1750` |
| A grandchild's event in Y | Y - 28 | `grandchild-born-1990` |
| A marriage of their own in Y | Y - 14 | `married-1905` |
| A spouse's event in Y | Y + `gap` (20) | `spouse-born-1800` |
| A sibling's event in Y | Y + `gap` (20) | `sibling-born-1880` |
| A parent's event in Y | Y + 60 | `parent-born-1700` |

Only the person's own events count for the first two rules (role `Primary`, or no role): a godparent at a 2001 baptism was not born in 2001. A DNA match is a person who took a test, so a match with no dates counts as living even with `undated=False`; a recorded date still wins over the association.

`snapshot` is a `gwt-check dump` (`{"people": [...], "families": [...], "events": [...]}`), a gwt-apply snapshot (`{"objects": {"Person": [...], ...}}`), or the same maps keyed by handle. Asking about many people, build `Index(snapshot)` once and pass it instead.

## Folding names

`fold(text)` lowercases, removes accents and turns punctuation into spaces. `fold(text, locale)` also drops the locale's particles as whole words (`de`, `van`, `von`) and then applies its spelling pairs, so old and new spellings compare equal: `b`/`v` and `y`/`i` in Spanish, `ij`/`y` in Dutch, `j`/`i` and `v`/`u` in Latin, `tx`/`ch` in Basque, `x`/`j` in Galician. A folded name is for comparing, never for showing.

## Reading dates

`read_date(text)` reads gwt-apply's date grammar and returns the same Gramps Date dict as gwt-apply: `1890`, `1890-03-12`, `abt 1890`, `bef 1890-03`, `est 1890`, `bet 1880 and 1890`, `from 1880 to 1890`. Anything else raises `DateError`, so a date is never stored as text.

With a locale, the date may also be written out: `12 de marzo de 1890` (es), `March 12th, 1890` (en), `le 1er août 1801` (fr), `12. März 1890` (de), `die 3 mensis Ianuarii anno Domini 1701` (la), `7bre 1750` (es, the old abbreviation for September). An `abt`, `bef` or `aft` in front still works. A written date must have exactly one month word, a year of three or four digits and at most one day; anything more is refused.

## Locale tables

`gwt_core/locales/<code>.toml`, one per language: `es`, `en`, `fr`, `it`, `pt`, `de`, `nl`, `ca`, `eu`, `gl`, `la`. Every file has the same sections, and a test fails when one does not:

| Section | Holds |
|---|---|
| `fold` | `pairs` (spelling swaps) and `particles` (words dropped from names) |
| `months` | `"1"` to `"12"`, each with the names and abbreviations a record uses |
| `dates` | `filler`, the words a written date has around its numbers (`de`, `le`, `anno`) |
| `gender` | `male`, `female` |
| `roles` | the words for `godparent`, `witness`, `father`, `mother`, `child`, `spouse`, `priest` |
| `events` | the words for `birth`, `baptism`, `marriage`, `death`, `burial` |
| `dedication` | `pairs` of a church-name abbreviation and its full words (`N.ª S.ª` and `Nuestra Señora`, `St.` and `Saint`) |

Words are written as the records write them, with accents; the code folds both sides before comparing. A month word must name one month only within its file.

`dedication(text, locale)` folds a church name and writes out its abbreviations, whole words only, so `Stoke` stays `stoke`.

`find_ids(text, kinds=None)` returns each identity number whose check digit or issuing rules hold: the Spanish DNI and NIE, the French NIR, the Italian codice fiscale and the United States SSN. An identity number belongs to a country, not to the language of the text, so every kind is looked for in every text (a DNI in an English note is found); `kinds=("dni", "nie")` limits the search. A number with a wrong check digit is not reported, so a dated file name such as `20230115B` is not taken for an ID. Only ASCII digits count.

To add a language, copy a file, translate every section, add its code to `LOCALES` in `rules.py`, and run `cd core && python3 -m pytest -q`. To add an identity number, add its pattern and its check to `ID_KINDS` in `rules.py`.
