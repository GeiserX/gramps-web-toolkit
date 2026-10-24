# gwt-import-excel

Turns rows of a research workbook (.xlsx) into a [gwt-apply](apply.md) change list. It never writes to Gramps. Python 3.8+, standard library only.

```sh
cd importxl
python3 gwt_import_excel.py people.xlsx --mapping map.json --tree tree-dump.json.gz --out changes.jsonl --report report.json
gwt-apply changes.jsonl              # dry-run first, as always
```

## Mapping file

Workbooks differ, so nothing is hard-coded. The mapping names the sheet, the header row and which column holds what:

```json
{"ruling": "R-1",
 "sheets": [{"sheet": "People", "header_row": 1,
   "columns": {"first_name": "Given", "surname": "Surname", "gender": "Sex",
               "birth_date": "Born", "birth_place": "Birth place",
               "death_date": "Died", "death_place": "Death place",
               "source": "Source", "page": "Page"}}]}
```

`first_name` and `surname` are required for a row to import. A mapped column that is not in the header stops the run and names the column. `ruling` goes on every op, as gwt-apply requires.

## What it does with each row

- **Dates.** Checked against the real calendar (leap years included): `31/02/1901`, `2026-02-29` or `99 March 1901` are refused. An explicit zero (`1901-02-00`, `00 March 1901`, `0000`) is refused too: only an absent part means unknown. Read: `1901`, `1901-03`, `1901-03-14`, `14/03/1901` (day first), `Mar 1901`, `14 March 1901`, and `c.`/`ca.`/`circa`/`abt`/`bef`/`aft` prefixes. Anything else is not stored as a date. The event keeps a description `Date as written: ...` and the row goes in the report under `date_problems`. Missing parts are never filled in.
- **Excel dates and formulas.** A numeric cell with a date style (built-in or custom, such as `d/m/yyyy`) is converted from its serial number to an ISO date (1900 and 1904 systems; serial 0 is valid only in the 1904 system, and the 1900 leap-year bug day is refused). A number whose format is time of day or elapsed time only (built-ins 18-21, 32, 33, 45-47, or a custom code with `h`/`s`/`[h]`, such as `[h]:mm:ss`) is not a date: it is kept as typed text and listed under `time_cells`. A plain number is kept as typed text, so `1901` is the year 1901, and a serial is never read as a year. A formula cell gives its cached value, which the tool does not recalculate; every such cell is listed under `formula_cells` so you can check it, also when it is a date or an error. An error cell (`#REF!`) is empty and listed under `error_cells`.
- **Duplicates.** With `--tree` (a dump with `objects.people` and `objects.events`), a row whose accent-folded name matches a person in the tree and whose birth year is within 1 year (or unknown on either side) is listed under `duplicates` and left out of the change list. It is never merged. The same person twice in the workbook is imported once.
- **Sources.** The source column becomes one source per distinct title and one citation per row, attached to the person and the events. A row with no source gets a `[no source]` research note instead and is listed under `no_source`.
- **Places.** One place per distinct text (exact text, so `A-B` and `A B` stay two places; same for sources), type `Unknown`. Fix the hierarchy afterwards.
- **Skipped rows.** Rows with an incomplete name are listed with the reason.

## Limits

People, births, deaths, places, sources and citations only. No families, no parents. Add families by hand or in a second pass with the reviewed rows. The tool does not know who is alive: keep living people out of the workbook, or out of the tree.
