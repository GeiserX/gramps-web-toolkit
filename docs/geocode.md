# gwt-geocode

The Gramps Web map shows a marker only for places that have coordinates, and most trees have none, so the map stays empty. `gwt-geocode` finds coordinates for the places your events use, in any country, and writes them as a change list. `gwt-apply` then puts them in the tree, logged and revertible like any other change.

Standard library only. It reads a `gwt-check dump` or a `gwt-apply` snapshot, gzipped or not, and never touches the tree.

```sh
gwt geocode --snapshot DUMP --out changes.jsonl [--recheck] [--providers LIST] [--overrides FILE]
            [--geonames FILE ...] [--country NAME=CC ...] [--contact EMAIL] [--cache DIR | --no-cache]
            [--ruling ID] [--lang CODE] > report.tsv
```

A typical run:

```sh
gwt check dump tree.json
gwt geocode --snapshot tree.json --contact YOUR_EMAIL --out coords.jsonl > coords.tsv
gwt apply coords.jsonl            # dry run: read it, then add --apply
```

## Which places get a point

Only places that some event uses. A country, a province or a town that only holds other places gets none, because the map draws a marker for every place with coordinates: a birth in a village would otherwise show three markers, for the village, its province and its country. Parents are still looked up in memory, as the reference their children are checked against, but nothing is written for them.

A place no event uses that already has a point is listed in the report as `container`, so you can clear it by hand. `gwt-geocode` never clears coordinates itself.

A place that already has a point keeps it, and no provider is asked about it: a point placed by hand is usually better than a gazetteer's. `--recheck` looks those places up again. It moves a point only when the answer is more than 50 m away, and the note says how far and from where. It never replaces a church's or a street's own point with its town's point. `--only-missing` names the default and is kept for older scripts. A point is any place whose latitude and longitude are both filled in, as `gwt-check` counts them; one written in degrees and minutes (`40°10'00"N`) is kept as it is, even with `--recheck`, and the note says so.

## Providers

Each place is looked up by its own name and the names above it, through a chain of providers. The first one that gives an answer the rules below accept wins. `--providers` sets which ones run, in order; the default is `cartociudad,ban,geonames-dump,nominatim`.

| Provider | Covers | What it is |
|---|---|---|
| `cartociudad` | Spain | The IGN/CNIG geocoder that `gwt-fetch geo` also uses: towns, municipalities with their INE code, and named spots such as churches (`toponimo`). No key. |
| `ban` | France | The national address base, https://api-adresse.data.gouv.fr: one point per commune, with its INSEE code and a score. No key. |
| `geonames-dump` | any country | A GeoNames dump file you download yourself from https://download.geonames.org/export/dump/ (a country file such as `IT.zip`, or `cities500.zip` for the world's towns), passed with `--geonames FILE`, read offline. Without `--geonames` it is skipped. |
| `nominatim` | anywhere | OpenStreetMap's geocoder, https://nominatim.openstreetmap.org. At most one request a second, as its usage policy asks; `--contact` adds your e-mail address to the User-Agent. |

Every answer is cached on disk (`--cache`, default `~/.cache/gwt-geocode`), so a second run asks nothing again. `--no-cache` asks again. A dropped connection, a timeout, 429 and 5xx are tried three times. A provider that blocks the tool (HTTP 403, usually a missing or made-up `--contact` for Nominatim) or fails three questions in a row gets no more requests that run; stderr says so once, and each place's note names the error.

The country decides which providers apply. It is read from the top place of the hierarchy when its type is `Country`: a two-letter name is taken as the code, then the GeoNames dump and Nominatim are asked. When none of them knows the name, no place under that country is looked up, so a namesake in another country can never win; the report says so, and you give the code with `--country Ruritania=rr`.

## The rules

- **A town** must carry the place's name (once case, accents and punctuation are folded; `Villanueva (Vilanova)` matches either name). `Saint-Exemple` is not `Saint-Exemple-sur-Mer`. A town under another town (a hamlet in a municipality) must lie within 30 km of it.
- **Two towns of the same name** more than 10 km apart, with no town above to choose by, give no answer from that provider, never the first one. CartoCiudad's point for a town and its municipality's centroid share one INE code, so they count as one place, and the town's point wins.
- **A church, building, cemetery or any other spot** must lie within 5 km of its town; **a street** or house number within 2 km. Otherwise it gets its town's point, `match` says `town-centroid`, and the note says how far the nearest answer was. A spot with no town above it gets no point, since nothing could check the distance. A `Parish` or `Farm` under a town is read as a spot (a parish church, a farmstead); with no town above it, as in English, Scandinavian or Galician trees, it is the settlement itself, and a farm under such a parish is checked against the parish.
- **A spot's name** must end with the same words as the answer's, once church abbreviations are written out for the country's language (`N.ª S.ª` reads `Nuestra Señora`, `St` reads `Saint`; the tables are `gwt_core.rules.dedication`, and `--lang` picks one by hand), or differ only in the first word. So `Iglesia de San Ejemplo` matches `San Ejemplo` and `Parroquia de San Ejemplo`, but never `San Ejemplo Obrero`, a modern parish that only shares the words. A town is never the point of a church, even when the church is named after it.
- **A result in another country** than the hierarchy's is refused.
- **An override always wins.** Gazetteers know today's buildings, not the convent that closed in 1835. `--overrides FILE` is a TSV, one row per place: Gramps ID, latitude, longitude and an optional note. Keep one file per tree and add to it as you go. An override on a place no event uses writes nothing, but it is still the reference for the places under it.

```text
gramps_id	lat	long	note
P9042	40.0312	-3.0157	the old convent, from the 1860 town plan
```

## Output

`--out` gets one `update_place` op per place, for `gwt-apply`:

```json
{"op": "update_place", "key": "geocode-P9042", "place": "P9042", "set": {"lat": 40.0312, "long": -3.0157}, "ruling": "geocode"}
```

`--ruling` sets the ruling every op carries. A place with no Gramps ID gets no op, and the report says so.

The report on stdout has one row per event place, then the containers that have a point:

| Column | Meaning |
|---|---|
| `gramps_id`, `place`, `type` | The place, with its hierarchy (`Chapel > Village > Province > Country`) |
| `match` | `settlement`, `area` or `own-point` (found), `town-centroid` (the town's point, see the note), `override`, `kept` (already had a point), `unresolved` (no point; the note says why), `container` |
| `provider`, `confidence` | Who answered, and its own score when it gives one (Nominatim's importance, the BAN score) |
| `distance_km` | For a spot, how far its point is from its town |
| `lat`, `long`, `note` | The point, and what was refused on the way |

A line on stderr counts the ops and each kind of match. The exit code is 0; 2 for a snapshot or overrides file in the wrong shape, or an `--out` that cannot be written (checked before any request); 3 when no provider answered a single request.

## Related tools

- Gramps desktop has PlaceCleanup (GeoNames) and GetGOV, run one place at a time by hand; `gwt-geocode` runs over the whole tree and leaves the decision in a change list.
- `gwt-fetch geo` looks up one Spanish place and prints it. `gwt-geocode` uses the same CartoCiudad client.
- `gwt-check` reports `place-no-coords`; run it after the apply to see what is left.
