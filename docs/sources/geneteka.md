# gwt-read geneteka: Polish parish and civil registers

Geneteka, run by the Polish Genealogical Society, indexes births, marriages and deaths from Polish parish and civil registers, and from some in Ukraine, Belarus and Lithuania, region by region. https://geneteka.genealodzy.pl/

## Commands

```sh
gwt read geneteka search --region 07mz --surname S [--kind births|marriages|deaths] [--given G] \
                         [--surname2 S2] [--given2 G2] [--from 1800] [--to 1850] [--parish N] [--exact] \
                         [--count 50] [--page 1] --out DIR
gwt read geneteka control
```

- `--region` is the site's region code: `01ds` dolnośląskie, `02kp` kujawsko-pomorskie, `03lb` lubelskie, `04ls` lubuskie, `05ld` łódzkie, `06mp` małopolskie, `07mz` mazowieckie, `71wa` Warszawa, `08op` opolskie, `09pk` podkarpackie, `10pl` podlaskie, `11pm` pomorskie, `12sl` śląskie, `13sk` świętokrzyskie, `14wm` warmińsko-mazurskie, `15wp` wielkopolskie, `16zp` zachodniopomorskie, `21uk` Ukraine, `22br` Belarus, `23lt` Lithuania, `25po` other.
- The site needs a surname to search a whole region.
- `--parish` is the site's numeric parish id (its `rid`, from the parish list of the search page); default every parish.
- `--count` is 10, 25 or 50 rows a page.

## Never in bulk

Geneteka's robots.txt asks for `Crawl-delay: 120`: one request every two minutes. This source keeps to it whatever a lane asks for, so it is for a person's own searches, a handful at a time. A loop over surnames or regions is what that delay forbids; do not build one. Answers are cached 30 days so a repeated search costs nothing.

## How it reads

The search page is a shell: its table fills from a JSON endpoint, `api/getAct.php`, with the page's own query fields. The reader asks that endpoint directly, once per search.

## What a candidate is

An index entry, a lead: open the scan before citing it. `id` is the kind's letter and the entry's own number (`B-12345678`, the number of its "report a bug" link). A birth has its name, `act` (the record number), the year, place and parish, and the father and mother as `relatives`. `cells` keeps every cell of the row in order; for marriages and deaths the reader does not split the cells into names yet. `remarks` is the site's own remark; `link` is the first link the row carries (a scan on metryki.genealodzy.pl, or the site of the archive or diocese that holds the book). Geneteka has no page per entry, so `url` is the search page that shows it.

## When it stops

"None" is the answer's own count: `recordsFiltered` 0 with no row. A page past the last row is the end: exit 2, with `past the end` on the results line. An answer of another shape is a STOP, exit 1; an HTML page instead of the answer is a STOP, exit 3 (the gate pauses every lane); a 5xx or no answer is an outage, exit 3.

## Canary

`--region 07mz --surname Kowalski`, at least 50 rows (the site gave about 79,700 births in November 2026). A control run takes two minutes per canary, at the site's pace.

The canary has already caught one trap: without the headers its own page sends (`X-Requested-With`, and the search page as `Referer`), the site answered 6 rows of a 50-row page. The reader sends them, and a page with fewer rows than it should hold is a STOP (exit 1), never a short list read as complete.
