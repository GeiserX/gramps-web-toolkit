# gwt-read bvph: Biblioteca Virtual de Prensa Histórica

The [Biblioteca Virtual de Prensa Histórica](https://prensahistorica.mcu.es/) (BVPH) of the Spanish Ministry of Culture holds digitised Spanish newspapers and provincial bulletins from the 17th century to the late 20th, with the OCR text of every page. Its full-text search is public: no account, no key. Death notices, local news, lists of conscripts, emigrants and prize-winners name people that no register indexes.

## Commands

```sh
gwt read bvph search --phrase '"Ana Zorvalo Quirell"' [--pages 1-6] --out DIR
gwt read bvph search --phrase '"Ana Zorvalo Quirell"' --controls both --out DIR
gwt read bvph control
```

- `--phrase` must be wholly in double quotes, passed as one argument (`'"..."'`). An unquoted search matches the words anywhere, walks thousands of rows and trips the site's block; it is refused with exit 64 before any request.
- Every results page is read while the pager links the next one, 50 issues a page, up to `--pages` (default and maximum 6).
- Past that cap the search prints `capped<TAB><phrase>: stopped after N pages with M page hits; partial, never complete` and the envelope says `"capped": true`. The rows are real but the list is not complete: narrow the phrase (add a second surname, a place) and search again. A capped search still exits 0.

## What a candidate is

A row is one page that holds the phrase, never a person. Its candidate has the newspaper issue's title as `name`, role `mention`, and is never `principal`, so gwt-rank ranks it weak. It carries `url` (the page viewer, `https://prensahistorica.mcu.es/es/catalogo_imagenes/grupo.do?path=<issue>&idImagen=<page>`), `date` (`YYYY-MM-DD`, empty for an undated issue), `year` (from the date, or from the issue's title when the issue has no date), `page` ("Página 3") and `text`, the words the site shows around the phrase. The envelope's `text` holds every row's words, for a corpus; `total` is the number of issues the site counts, `pages` the URLs read. `gwt fetch digibib` downloads the page images.

## When it stops

The site has failed in ways that read as "no results" more than once, so every page is checked:

| The answer | What happens |
|---|---|
| a page with at least one page hit (`class="unidad_textual"`) | rows, exit 0 |
| the first page with the site's own "No hay resultados" box | exit 2, an empty candidates file |
| HTTP 429 | a wall: exit 3, the gate pauses every lane |
| HTTP 200 with neither hits nor "No hay resultados" (the soft-block skeleton, which still says `resultados_ocr` in its scripts, or a new layout) | a STOP: exit 3, nothing written or cached |
| "No hay resultados" on a later page, after the first said there was more | a STOP, exit 3 |

A STOP anywhere in the walk writes nothing, so a half-read search never looks complete.

## Pace, cache and terms

- The gate (`~/.cache/gwt-gate-bvph`) waits 10 s between requests, never less than 6 s, and up to 5 minutes after a hit. The site answers 429 after a few quick requests, from some networks on the second one; a home connection does better than a datacentre or a corporate one.
- A results page is cached 7 days.
- The site publishes no robots.txt. The Ministry of Culture's content is open to reuse with attribution (Real Decreto 1495/2011).

## Canary

`--phrase '"Mateo Sagasta"' --pages 2`, at least 80 page hits. Práxedes Mateo Sagasta led Spain's government in the 1870s to 1900s; the site gives its 10,000-issue maximum for him, so two pages always hold 100. Run `gwt read bvph control` before a batch and `--controls both` around a search you will log as "none".
