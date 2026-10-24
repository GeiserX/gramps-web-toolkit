# gwt-import-excel

Turns rows of a research workbook (.xlsx), such as a cousin's spreadsheet or an archive's index, into a [gwt-apply](apply.md) change list for review. It never writes to Gramps and never decides who is a duplicate: [gwt-apply's preflight](apply.md) does. Python 3.8+, standard library only.

```sh
cd importxl
gwt-apply --save-snapshot tree.json.gz                   # or a gwt-check dump
python3 gwt_import_excel.py cousin.xlsx --mapping map.json --batch cousin-ana \
    --tree tree.json.gz --out changes.jsonl --report report.json
gwt-apply changes.jsonl --snapshot tree.json.gz          # dry run first, as always
```

`--batch` names the workbook. It goes into every key, so two workbooks never share a key, and it must stay the same when you rerun the same workbook. `--out` and `--report` are never overwritten without `--force`.

## Mapping file

Workbooks differ, so nothing is hard-coded. The mapping names the sheet, the header row and which column holds what:

```json
{"ruling": "R-1",
 "sheets": [{"sheet": "People", "header_row": 1,
   "columns": {"first_name": "Given", "surname": "Surname", "gender": "Sex",
               "birth_date": "Born", "birth_place": "Birth place", "birth_place_id": "Birth place ID",
               "death_date": "Died", "death_place": "Death place", "death_place_id": "Death place ID",
               "source": "Source", "page": "Page", "namesake_reason": "Why another person"}}]}
```

Only `first_name` and `surname` are needed for a row to import. A mapped column that is not in the header, or a header name that appears twice, stops the run and names the column. `ruling` goes on every op, as gwt-apply requires.

## What it does with each row

- **Keys.** Every op of a row gets the key `xl-<batch>-<sheet>-r<row>-<name>-<hash>`. The name is part of it, so a row that moves after a rerun gets a new key, and preflight then reports it as a namesake of the person the old row made. It is never silently merged into someone else.
- **Namesakes.** With `--tree`, the report lists under `namesakes` every row whose name matches a person in the tree, either the full name or the first given name and first surname as preflight compares them, and every row that matches an earlier row of the same import. The importer keeps them all in the change list. A row with nothing in the `namesake_reason` column goes in as a plain `add_person`, and gwt-apply refuses the whole list until someone decides: delete the row, or write in that column why it is another person and run again. A row with a reason gets `allow_namesake`, that reason, and `namesake_of` naming every match. A reason on a row with no match is listed under `namesake_reason_unused`.
- **People who may be alive.** A row born less than 100 years ago with nothing in the death columns is held out of the change list and listed under `living_held`. `--allow-living` imports them.
- **Places.** The importer never creates a place, because a place in the tree has a type and a hierarchy that a spreadsheet does not give. A `*_place_id` column holding a Gramps ID (`P0042`) is used as is. Otherwise the place text is matched by name, ignoring case and accents, against the tree's places. One match is used. Several matches go under `places_ambiguous` and none go under `places_unmatched`. In both cases the event keeps `Place as written: ...` in its description. An ID that is not a Gramps ID, or not in the tree, goes under `place_ids_unknown`.
- **Dates.** Checked against the real calendar, leap years included: `31/02/1901`, `2026-02-29` or `99 March 1901` are refused. An explicit zero (`1901-02-00`, `00 March 1901`, `0000`) is refused too, since only an absent part means unknown. It reads `1901`, `1901-03`, `1901-03-14`, `14/03/1901`, `14-03-1901` and `14.03.1901` (day first), month names in English, Spanish and French, whole words or the usual abbreviations (`Mar 1901`, `14 de marzo de 1901`, `1er janvier 1901`, `15 août 1901`), and the prefixes `c.`, `ca.`, `circa`, `abt`, `hacia`, `vers`, `bef`, `antes de`, `avant`, `aft`, `después de`, `après`. A day-first date that would also read month first (`03/04/1901`) is listed under `day_first_assumed`. Anything else is not stored as a date. The event keeps `Date as written: ...` in its description, and the row goes under `date_problems`. Missing parts are never filled in. A cell holding only `?` makes no event.
- **Excel dates and formulas.** A numeric cell with a date style (built-in or custom, such as `d/m/yyyy`) is converted from its serial number to an ISO date (1900 and 1904 systems; serial 0 is valid only in the 1904 system, and the 1900 leap-year bug day is refused). A number whose format shows only a time of day or elapsed time (built-ins 18-21, 32, 33, 45-47, or a custom code with `h`/`s`/`[h]`, such as `[h]:mm:ss`) is not a date. It is kept as typed text and listed under `time_cells`. A plain number is kept as typed, so `1901` is the year 1901, and a serial is never read as a year. A formula cell gives its cached value, which the tool does not recalculate. Every such cell is listed under `formula_cells`, also when it is a date or an error. An error cell (`#REF!`) is empty and listed under `error_cells`.
- **Gender.** `M`, `H`, `V`, `varón`, `hombre`, `masculino`, `homme` and `masculin` are male; `F`, `mujer`, `hembra`, `femenino`, `femme` and `féminin` are female. An empty cell or `?` is unknown, and anything else is unknown and listed under `gender_unread`.
- **Sources.** The source column becomes one source per distinct title and one citation per row, attached to the person and the events. gwt-apply reuses a source that already has that title. A row with no page gets the page `(no page in workbook, row <sheet>!<row>)`, so two rows never share a page-less citation. A row with no source gets a `[no source]` research note instead and is listed under `no_source`.
- **Skipped rows.** Rows with an incomplete name are listed with the reason.

## Limits

People, births, deaths and their citations only. No families, no parents: add them by hand or in a second pass with the reviewed rows. A workbook part that unpacks to more than 100 MB, a row past 1,048,576 or a column past 16,384 stops the run. A broken workbook ends with a message and exit 2, never a traceback.
