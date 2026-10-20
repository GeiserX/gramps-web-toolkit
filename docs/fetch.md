# gwt-fetch: archive images and text without a browser

A research agent that opens a browser to save a register page is slow and leaves no trace of where the file came from. `gwt-fetch` downloads the file straight from the portal and writes one `manifest.tsv` row per file, so every image in your tree can be traced back to its source URL and checksum.

Python 3.11 or newer with `requests`. Run it as [`fetch/gwt-fetch`](../fetch/gwt-fetch), symlinked onto your `PATH` if you like, or `pip install ./fetch` for a `gwt-fetch` command. The code is in [`fetch/gwt_fetch/`](../fetch/gwt_fetch/). Tests: `cd fetch && python3 -m pytest`.

`iiif` works with any IIIF archive in any country. `digibib`, `gazeta` and `geo` cover Spanish sources only, and `europeana` filters to the Spanish aggregator unless you pass `--provider any`.

```
gwt-fetch iiif URL [--pages 1-5,9] [--labels REGEX] [--out DIR] [--list]
gwt-fetch digibib URL [--pages ...] [--out DIR] [--list]
gwt-fetch gazeta search (--text WORDS | --title WORDS) [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--limit N]
gwt-fetch gazeta fetch BOE-A-YYYY-N [...] [--out DIR]
gwt-fetch europeana QUERY [--provider Hispana|any] [--data-provider NAME] [--year-from Y] [--year-to Y] [--iiif-only] [--rows N]
gwt-fetch geo PLACE [...] [--types Municipio,poblacion] [--province NAME] [--limit N]
gwt-fetch --delay 2 ...   # seconds between requests to one host, default 1
```

## What every download does

- **One row per file** in `DIR/manifest.tsv`: `file`, `label` (the canvas label or document title), `image_url` (the exact URL that served the bytes), `manifest_url` (the manifest or document page), `sha256`, `fetched_at` (UTC).
- **It never overwrites.** Files are written as `*.part` and renamed when complete, so a finished file on disk is always whole.
- **Re-running is resuming.** The same command skips finished files. A finished file without a row (the run died between rename and append) gets its row on the next run.
- **Polite by default.** One request per second per host, retries on 429 and 5xx with backoff, honours `Retry-After`. The user agent is `Mozilla/5.0 (compatible; gwt-fetch/0.1; +https://github.com/GeiserX/gramps-web-toolkit)`; Antenati refuses agents that don't start with `Mozilla/5.0`.
- **Exit status.** 0 when everything selected was fetched, 1 when some item failed (each failure is printed with the URLs tried and the HTTP status), 2 on a network or parse error.

Keep downloads out of the repo. Point `--out` at your research folder or `/tmp`.

## `iiif`: any IIIF manifest, or a viewer URL it knows

Give it a IIIF Presentation manifest URL (v2 or v3), or a viewer URL from a portal below. It downloads each selected canvas through the Image API at `full/max` (the largest size the server allows), falling back to `full/full` on v2 servers and then to the static image named in the manifest.

`--pages` takes 1-based canvas positions (`1-5,9,12-`); `--labels` takes a regular expression matched against the canvas label (`--labels '^pag\. (1|2)$'`). `--list` prints position, label and URL and downloads nothing.

| Portal | Works | Viewer URL to manifest |
|---|---|---|
| Portale Antenati (Italy) | Yes, tested | The unit page `https://antenati.cultura.gov.it/ark:/12657/an_ua4076426/` holds the link `https://dam-antenati.cultura.gov.it/antenati/containers/5vj8KVP/manifest`; `gwt-fetch` reads it from the page. Image requests need `Referer: https://antenati.cultura.gov.it/`, which `gwt-fetch` sends. `full/full` returns 403 and `full/max` works. The server caps output at 10 megapixels (`maxArea` in `info.json`), so a 4929×3781 scan arrives at 3610×2769. |
| Memòria Digital de Catalunya (and any CONTENTdm site) | Yes, tested | `https://mdc.csuc.cat/digital/collection/{alias}/id/{id}` becomes `https://mdc.csuc.cat/iiif/2/{alias}:{id}/manifest.json`. Images arrive at native size. |
| Europeana | Yes, tested | `https://www.europeana.eu/{lang}/item/{collection}/{id}` becomes `https://iiif.europeana.eu/presentation/{collection}/{id}/manifest`. When the provider has no Image API, Europeana's manifest points at its own thumbnail proxy (`api.europeana.eu/thumbnail/v2/url.json?uri=...`); `gwt-fetch` takes the original file from `uri` instead. The original can be a PDF. |
| Any Mirador or Universal Viewer link | Yes | A `?manifest=` or `?iiif-content=` parameter is used as the manifest. |
| Biblioteca Virtual de Prensa Histórica (BVPH) | No IIIF; use `digibib` | DIGIBIB viewer, no IIIF endpoint. |
| Biblioteca Virtual de Defensa | No IIIF; use `digibib` | DIGIBIB viewer, no IIIF endpoint. |
| Galiciana, Biblioteca Dixital de Galicia | Needs a browser | `biblioteca.galiciana.gal` answers record pages, viewer pages and image URLs with a JavaScript challenge page, also via Europeana's original-file links. `gwt-fetch` stops with a clear message; it does not solve the challenge. |
| Galiciana, Arquivo Dixital de Galicia | Yes, through `digibib` or Europeana | `arquivo.galiciana.gal/arpadweb` has no challenge; its single-file objects come back as PDF. |
| Biblioteca Digital Hispánica (BNE) | Needs a browser | `bdh-rd.bne.es` returns a 403 block page to scripted clients for viewer pages and for `pdf.raw` image URLs, from a datacenter address and from a home connection alike. No IIIF endpoint was found. |

Antenati traps from real downloads (two Italian birth registers of the 1850s and 1860s):

- The image server is slow: 10 to 60 seconds per image, about 20 on average. Five pages take close to two minutes, so run big selections in the background and let the resume logic carry a run that gets cut off.
- A plain `curl` gets 403 from both `antenati.cultura.gov.it` (search and unit pages) and the image server. The user agent has to start with `Mozilla/5.0`, and image requests need the portal as `Referer`.
- The web viewer shows about 1600 px and `full/full` returns a Cloudflare 403, which led one researcher to settle for 1600 px. `full/max` returns the largest size allowed: 3530×2716 for one page, 2122×1684 for another, depending on the scan.
- A canvas label is only `pag. N`. To find an act, list the canvases (`--list`), fetch a few pages around the estimate into `/tmp`, read the «Num. d'ordine» on each, then fetch just the right page into your research folder.

## `digibib`: DIGIBIB viewers without IIIF

DIGIBÍS's DIGIBIB runs BVPH, the Biblioteca Virtual de Defensa, Galiciana and many regional digital libraries. None of these installs serve IIIF. The viewer URL is `.../catalogo_imagenes/grupo.do?path=N`. Each page view (`&presentacion=pagina&posicion=K`) names one image as `imagen_id.do?idImagen=M`, and that URL with `&formato=jpg` returns the full JPEG. `gwt-fetch digibib` walks those page views. When the object is a single file, `grupo.do` serves the file itself (often a PDF); it is saved as `0001_document.pdf`.

Tested: Biblioteca Virtual de Defensa (`grupo.do?path=359360`, 17 pages, 3764×2443 JPEGs), BVPH (`grupo.do?path=1000178404`, 72 pages), Galiciana Arquivo Dixital (`grupo.do?path=264703`, one PDF).

`prensahistorica.mcu.es` and the other `*.mcu.es` sites return HTTP 429 to some egress addresses (seen from a corporate VPN exit) on every request, while the same request from a home connection works. If every request gets 429, change network rather than raising `--delay`.

## `gazeta`: the historical Gaceta de Madrid

The BOE open data API (`/datosabiertos/api/`) covers consolidated legislation and the modern BOE and BORME summaries only, with no endpoint for the «Gazeta» colección histórica (1661-1959). `gwt-fetch` therefore uses the public pages, whose URLs are stable:

- Search: `GET https://www.boe.es/buscar/gazeta.php` with the form's own fields as query parameters. Each field is a `campo[i]`/`dato[i]` pair, joined by `operador[i]` (`and`, `or`, `not`):

  | Field | `campo[i]` | `dato[i]` | gwt-fetch flag |
  |---|---|---|---|
  | Full text | `campo[4]=DOC` | words; wrap a phrase in double quotes | `--text` |
  | Title | `campo[1]=TITULOS` | words | `--title` |
  | Gazette title | `campo[6]=GAZ.ID` | 7 = Gaceta de Madrid, 4 = Gaceta de la República, 8 = BOE, and 12 more in the form | `--gazette` |
  | Publication date | `campo[8]=FPU` | `dato[8][0]` and `dato[8][1]`, `YYYY-MM-DD`, 1661-01-01 to 1959-12-31 | `--from`, `--to` |
  | Also | `campo[0]=ID_HIST`, `campo[2]=RNG.ID`, `campo[3]=DEM.ID`, `campo[7]=NBO` | rank, department, issue number (`n/aaaa`) | not exposed |

  Plus `page_hits` (only 50, 200, 500, 1000 or 2000), `sort_field[0]=FPU`, `sort_order[0]=asc` and `accion=Buscar`. Results are `<li class="resultado-busqueda">` blocks with the title, «núm. N, de DD/MM/AAAA, página(s) P», the department and the item PDF with its reference. The output is TSV: `ref`, `date`, `gazette`, `number`, `page`, `department`, `title`, `pdf_url`, `issue`, `issue_pdf`. The site returns at most 2000 hits per search; narrow the dates for more.
- Whole issue: for the Gaceta de Madrid, `issue` is `GMD-{year}-{number}` and `issue_pdf` is `https://www.boe.es/gazeta/dias/{YYYY}/{MM}/{DD}/pdfs/GMD-{year}-{number}.pdf`, with the number unpadded (`GMD-1870-40`, not `-040`).
- Document by reference: `https://www.boe.es/buscar/doc.php?id=BOE-A-1870-1111` (title, «Publicado en», a link to the PDF).
- PDF: `https://www.boe.es/datos/pdfs/BOE//{year}/{issue:03}/A{first:05}-{last:05}.pdf`, the scanned page or pages of the original issue.

Traps we hit with real searches:

- `--text Surname1 Surname2` matches the two words anywhere in the item, so a two-surname search returns several times more hits than it should, mostly noise. `--text '"Surname1 Surname2"'` is the phrase, and returns the same hits the BOE search form returns in a browser.
- References are not all `BOE-A-`: `BOE-I-1892-51060` and `BOE-B-1957-12194` came back from the same search. `gazeta fetch` takes any `BOE-<letter>-<year>-<n>`.
- An item's title can hide what matters. `BOE-A-1864-10018` is titled «Gobierno de la provincia de Zaragoza. Registro de la Propiedad de Yeste.», yet its two pages are a long list of farms in Moratalla and Nerpio with every owner's name. The full-text search finds such names; the title never would. Read the PDF before you call a hit a homonym.
- The search does not index the annexes «Anuncios, avisos y Administración de Justicia» of many years. For those, download the whole issue (`issue_pdf`) and search its text.

`gazeta fetch REF` saves `REF.pdf` and `REF_text.txt`. The text is the HTML text when the BOE has transcribed it. Otherwise it is the PDF's own OCR layer through `pdftotext` (poppler), when that is installed. That OCR is rough on 19th-century type but good enough to search.

```
$ gwt-fetch gazeta search --text Betanzos --from 1890-01-01 --to 1890-03-31 --limit 3
ref	date	gazette	number	page	department	title	pdf_url
BOE-A-1890-676	1890-02-02	Gaceta de Madrid	33	325	Ministerio de la Gobernación	Estación Central de Telégrafos.- Telegramas recibidos ...	https://www.boe.es/datos/pdfs/BOE//1890/033/A00325-00325.pdf
$ gwt-fetch gazeta fetch BOE-A-1890-676 --out research/raw/fuentes/gaceta
```

## `europeana`: Spanish records with a manifest

The Europeana Search API needs a free key: get one at https://pro.europeana.eu/pages/get-api and `export EUROPEANA_KEY=...`. Without a key, the command prints a message and exits 0, so pipelines carry on.

By default it filters to the Spanish aggregator `PROVIDER:"Hispana"` (4.5 million records: BVPH, Galiciana, BNE, the regional digital libraries and more). Use `--data-provider` to narrow to one institution as Europeana names it, for example `"Galiciana: Digital Library of Galicia"` or `"Virtual Library of Historical Press"`. Output is TSV with a `manifest` column for each record, which `gwt-fetch iiif` can fetch.

`--iiif-only` adds `sv_dcterms_conformsTo:*iiif*`, keeping only records whose provider serves a real Image API. In October 2026 that returns zero Hispana records: no Spanish provider exposes IIIF through Europeana, while France, Austria and the UK expose millions. For Spanish records, use the Europeana manifest without `--iiif-only`; `iiif` swaps each thumbnail for the provider's original file. Where that provider blocks scripted clients (Galiciana's library, BNE), you still need the browser.

## `geo`: Spanish places to coordinates and INE code

CartoCiudad's geocoder (IGN/CNIG, `https://www.cartociudad.es/geocoder/api/geocoder/candidates` then `/find`) turns a place name into coordinates, and every result carries the INE municipality code (`muniCode`, five digits: province plus municipality). No key needed.

```
$ gwt-fetch geo Betanzos Ortigueira --province "A Coruña" --limit 1
query	type	name	municipality	ine_code	province	lat	lng	cartociudad_id
Betanzos	poblacion	Betanzos	Betanzos	15009	A Coruña	43.278535	-8.213380	1200023058
Ortigueira	poblacion	Ortigueira	Ortigueira	15061	A Coruña	43.684475	-7.850976	1200024424
```

`--types` picks CartoCiudad result types: `Municipio` (the municipality, centroid), `poblacion` (a settlement), `toponimo` (named places such as churches and parish cemeteries, useful for parishes) and `callejero` (streets). Historical names that no longer exist won't match; geocode the modern municipality and keep the historical name in your own record.

## Spanish open-data projects we looked at

From the awesome-spain list (https://github.com/GeiserX/awesome-spain):

- **CartoCiudad / pycartociudad / caRtociudad**: used. `geo` calls the same public API directly, so it needs no extra dependency.
- **INE municipality codes** (municipios-provincias-comunidades-INE-spain, ccaa-provincias-municipios): not needed, because CartoCiudad already returns the INE code with each match.
- **spanish-names-surnames** (INE name and surname frequencies): not a fetch source. It may help a name-normalising step later.
- **boe-mcp, MCP-BOE, rOpenSpain/BOE, Datania BOE**: they wrap the modern BOE open data API, which does not cover the Gazeta collection. `gazeta` is the part that does.
- **API-CNIG, mapSpain, es-atlas**: map display and boundaries, outside what fetch does.
