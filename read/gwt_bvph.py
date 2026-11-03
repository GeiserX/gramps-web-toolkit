#!/usr/bin/env python3
"""gwt_bvph: the Biblioteca Virtual de Prensa Histórica (prensahistorica.mcu.es), Spanish newspapers searched inside
their OCR text, for gwt-read.

The BVPH holds digitised Spanish newspapers and provincial bulletins from the 17th century to the late 20th, with the
text of every page. Its full-text search is a public HTML page, no account:

    https://prensahistorica.mcu.es/es/consulta/resultados_ocr.do?busq_general="..."&tipoResultados=PAG&posicion=N

It lists 50 issues a page, each with the pages that hold the words and the words around them. A row here is one page
hit: a page of one issue, never a person.

The site is strict and has failed in ways that read as "no results" more than once:

- It answers HTTP 429 after a few quick requests, from some networks on the second one. A 429 is a wall (exit 3).
- It also soft-blocks: HTTP 200 with an empty page skeleton that still says `resultados_ocr` in its scripts. A page
  counts only when it holds a page hit (`class="unidad_textual"`) or the site's own "No hay resultados" box; any other
  page is a STOP (exit 3), never zero results. A later page of a search that says "No hay resultados" is a STOP too.
- An unquoted query matches the words anywhere and walks thousands of rows, which is what trips the block. The
  phrase must come in double quotes, and a search reads at most MAX_PAGES pages: past that it prints a `capped` line
  and the result is partial, never complete.

Every results page is walked while the pager links the next one. Standard library only.
"""
import html, re, sys, urllib.parse

import gwt_reader

HOST = 'https://prensahistorica.mcu.es'
SOURCE = 'bvph'
PER_PAGE = 50            # issues on one results page; the pager steps posicion by this
MAX_PAGES = 6            # a search never reads more pages than this: past it the result is CAPPED, partial
# The site answers 429 to quick requests: one every 10 s at best, never under 6 s, one every 5 minutes after a hit.
PACE = {'start': 10.0, 'floor': 6.0, 'ceil': 300.0}
DAY = 86400
SPEC = {
    'id': SOURCE,
    'help': 'BVPH, Spanish historical newspapers searched inside their text; public, slow, quoted phrases only',
    'pace': PACE,
    'cache_rules': ((r'/consulta/resultados_ocr\.do\?', 7 * DAY),),
    # Práxedes Mateo Sagasta, prime minister of Spain in the 1870s to 1900s: the site gives its 10,000-issue maximum
    # (2026-11-03), so two pages always hold 100 page hits
    'controls': [{'argv': ['--phrase', '"Mateo Sagasta"', '--pages', '2'], 'min_count': 80}],
    'result_markers': [r'class="unidad_textual"'],
    'negative_markers': [r'<div class="nav_registros">\s*<p>\s*No hay resultados'],
    'search_help': 'a quoted phrase searched in the text of every page; one row per page that holds it',
}
ACCEPT = 'text/html'
EXIT_USAGE = 64

_TOTAL = re.compile(r'class="nav_descrip">\s*[\d.]+\s+al\s+[\d.]+\s+de\s+([\d.]+)')
_DATE = re.compile(r'class="datos_publicacion"><bdi>[^<]*?(\d\d)/(\d\d)/(\d{4})')
_PAGE_LABEL = re.compile(r'>\s*(P[áa]gina\s+[^<]+?)\s*</a>')
_FRAGMENTS = re.compile(r'<ul class="texto_ocurrencias">(.*?)</ul>', re.S)


def text(fragment):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', fragment or ''))).strip()


def quoted(phrase):
    """True for a phrase wholly in double quotes, with no other quote inside: "Ana Zorvalo"."""
    return bool(re.fullmatch(r'"[^"]+"', (phrase or '').strip()))


def search_url(phrase, pos=1):
    return HOST + '/es/consulta/resultados_ocr.do?' + urllib.parse.urlencode([
        ('general_ocr', 'on'), ('busq_general', phrase.strip()), ('tipoResultados', 'PAG'),
        ('formato_fechapublicacion', 'dd/MM/yyyy'), ('posicion', pos)])


def total_of(page):
    """The issues the site counts for the search ("1 al 50 de 139"), or None."""
    m = _TOTAL.search(page)
    return int(m.group(1).replace('.', '')) if m else None


def has_next(page, pos):
    """The pager links the next results page while there is one."""
    return 'posicion=%d' % (pos + PER_PAGE) in page


def later_page(page):
    """The page check of a results page after the first: only a page with hits. The search said it had more rows,
    so "No hay resultados" there is the site failing, a STOP, never the end."""
    return gwt_reader.classify(page, SPEC) == 'hits'


def candidates(page):
    """One candidate per page hit: the issue's title and date, the page, and the words around the phrase."""
    out = []
    for chunk in page.split('<span class="titulo">')[1:]:
        t = re.search(r'<bdi>(.*?)</bdi>', chunk, re.S)
        title = text(t.group(1)) if t else ''
        d = _DATE.search(chunk)
        date = '%s-%s-%s' % (d.group(3), d.group(2), d.group(1)) if d else ''
        y = re.search(r'(?<!\d)(1[5-9]\d\d)(?!\d)', date or title)     # an issue with no date may name its year
        for unit in chunk.split('class="unidad_textual"')[1:]:
            im = re.search(r'idImagen=(\d+)', unit)
            path = re.search(r'grupo\.do\?path=(\d+)', unit)
            if not im:
                continue
            label = _PAGE_LABEL.search(html.unescape(unit))
            frags = _FRAGMENTS.search(unit)
            words = [text(li) for li in re.findall(r'<li>(.*?)</li>', frags.group(1), re.S)] if frags else []
            url = '%s/es/catalogo_imagenes/grupo.do?path=%s&idImagen=%s' % (HOST, path.group(1) if path else '',
                                                                             im.group(1))
            out.append({'source': SOURCE, 'id': im.group(1), 'name': title, 'role': 'mention', 'principal': False,
                        'given': None, 'surnames': None, 'events': {}, 'relatives': [], 'url': url, 'date': date,
                        'year': int(y.group(1)) if y else None, 'page': label.group(1) if label else '',
                        'text': ' / '.join(w for w in words if w)})
    return out


def add_search_args(sp):
    sp.add_argument('--phrase', required=True, help='the words in double quotes, as one argument: \'"Ana Zorvalo"\'')
    sp.add_argument('--pages', type=int, default=MAX_PAGES, choices=range(1, MAX_PAGES + 1),
                    metavar='1-%d' % MAX_PAGES, help='results pages to read at most, 50 issues each (default %d); '
                    'a search that has more prints a capped line' % MAX_PAGES)


def search(a, R):
    phrase = a.phrase.strip()
    if not quoted(phrase):
        print('gwt-read: bvph: %r is not in double quotes; pass it as \'"%s"\' (an unquoted search matches the '
              'words anywhere and walks thousands of rows)' % (a.phrase, phrase.strip('"')), file=sys.stderr, flush=True)
        return EXIT_USAGE
    pages = max(1, min(int(a.pages), MAX_PAGES))
    cands, urls, total, capped, cached_all, pos = [], [], None, False, True, 1
    for n in range(pages):
        url = search_url(phrase, pos)
        check = gwt_reader.page_check(SPEC) if pos == 1 else later_page
        status, rh, body, cached = R.get(url, {'accept': ACCEPT}, refresh=a.refresh, page=check)
        page = body.decode('utf-8', 'replace')
        if pos == 1:
            total, verdict = total_of(page), gwt_reader.classify(page, SPEC)
        cands += candidates(page)
        urls.append(url)
        cached_all = cached_all and cached
        if not has_next(page, pos):
            break
        if n == pages - 1:
            capped = True
            break
        pos += PER_PAGE
    env = gwt_reader.envelope(urls[0], None, text='\n'.join(c['text'] for c in cands if c['text']), verdict=verdict,
                              phrase=phrase, pages=urls, total=total, capped=capped, from_cache=cached_all)
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(urls[0], [SOURCE, phrase, pages]), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    if capped:
        print('capped\t%s: stopped after %d pages with %d page hits; partial, never complete: narrow the phrase'
              % (phrase, len(urls), len(cands)), flush=True)
    print('results\t%s issues, %d page hits read%s' % ('?' if total is None else total, len(cands),
                                                       ' (cache)' if cached_all else ''), flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY
