#!/usr/bin/env python3
"""gwt_pares: PARES (Portal de Archivos Españoles, pares.cultura.gob.es) for gwt-read.

PARES is the catalogue of the Spanish state archives. A search returns archival descriptions (a lawsuit, a file
of proofs of nobility, a passenger licence), not indexed people, so a candidate is one description: its title
is the `name` gwt-rank matches the person's names against, and it is never the record's principal and carries
no birth or death, because the dates of a file are not a person's dates.

The site answers plain HTML. Its search only answers inside a session, so a search that is not in the cache
first fetches the search form for the session cookie (two requests through the gate), then the results.
Standard library only.
"""
import html, re, urllib.parse

HOST = 'https://pares.cultura.gob.es'
BASE = HOST + '/ParesBusquedas20/catalogo'
FORM_URL = BASE + '/search'
SOURCE = 'pares'
# A public archive with no account: one request every 15 s at best, never faster than one every 10 s, and
# after a hit one every 120 s. A search that misses the cache costs two requests (the form, then the results).
PACE = {'start': 15.0, 'floor': 10.0, 'ceil': 120.0}
ACCEPT = 'text/html'
LANGUAGE = 'es-ES,es;q=0.9'
DESCRIPTION = re.compile(r'(?:/catalogo/description/|^)(\d{1,10})(?:[/?#]|$)')

_ROW = re.compile(r'<p class="titulo">(.*?)</td>', re.S)
_LINK = re.compile(r'<a href="/ParesBusquedas20/catalogo/description/(\d+)[^"]*"([^>]*)>(.*?)</a>', re.S)
_TITLE_ATTR = re.compile(r"""title=(['"])(.*?)\1""", re.S)
_FIELD = re.compile(r'<p class="(tipo_archivo|fecha|signatura)"><em class="destacado">[^<]*</em>(.*?)</p>', re.S)
_TOTAL = re.compile(r'pagebanner nResul">.*?<span class="azul">[\d.]+</span>.*?<span class="azul">[\d.]+</span>.*?'
                    r'<span class="azul">([\d.]+)</span>', re.S)
_INFO = re.compile(r'<div class="info">\s*<h4[^>]*>(.*?)</h4>(.*?)</div>', re.S)


def text(fragment):
    """Visible text of an HTML fragment: comments and tags out, entities decoded, spaces folded."""
    t = re.sub(r'<!--.*?-->', ' ', fragment or '', flags=re.S)
    t = html.unescape(re.sub(r'<[^>]+>', ' ', t))
    return re.sub(r'\s+', ' ', t).strip()


def search_url(given='', surnames=(), words=''):
    """The site's own simple search: every word anywhere in a description (accents and case ignored)."""
    texto = ' '.join(w for w in [given] + list(surnames) + [words] if w)
    return BASE + '/find?' + urllib.parse.urlencode([('nm', ''), ('texto', texto)], quote_via=urllib.parse.quote)


def description_id(arg):
    """'1234567' from a description id or its URL, else None."""
    m = DESCRIPTION.search((arg or '').strip())
    return m.group(1) if m else None


def description_url(did):
    return '%s/description/%s' % (BASE, did)


# ---------------------------------------------------------------- is this the page we asked for?
# Anything else (an empty frame, a block page, the site's own error page) is a STOP in gwt_reader.


def is_form(page):
    return 'id="formBusqueda"' in page


def is_results(page):
    return 'id="texto_resultados"' in page


def is_description(page):
    m = re.search(r'<div id="tituloUD"\s*>(.*?)</div>', page, re.S)
    return bool(m and text(m.group(1)))


# ---------------------------------------------------------------- candidates


def _candidate(did, title, **extra):
    c = {'source': SOURCE, 'id': did, 'name': title, 'role': 'named in the description', 'principal': False,
         'given': None, 'surnames': None, 'events': {}, 'relatives': [], 'url': description_url(did)}
    c.update(extra)
    return c


def search_candidates(page):
    """Each result row as a candidate: the full title (the site cuts long ones and keeps the whole in the
    link's title attribute), the archive, the dates of the file and its reference number."""
    rows = []
    for row in _ROW.findall(page):
        link = _LINK.search(row)
        if not link:
            continue
        did, attrs, shown = link.groups()
        full = _TITLE_ATTR.search(attrs)
        title = text(full.group(2)) if full else text(shown)
        fields = {k: text(v) for k, v in _FIELD.findall(row)}
        rows.append(_candidate(did, title, archive=fields.get('tipo_archivo', ''), dates=fields.get('fecha', ''),
                               reference=fields.get('signatura', '')))
    return rows


def total(page):
    m = _TOTAL.search(page)
    return int(m.group(1).replace('.', '')) if m else (0 if not search_candidates(page) else None)


def description_fields(page):
    """{label: value} of every field on a description page, labels as the site words them (its language
    follows Accept-Language), so nothing is lost when the site adds or renames a field."""
    out = {}
    for label, body in _INFO.findall(page):
        key = text(label).rstrip(':').strip()
        if key and key not in out:
            out[key] = text(body)
    return out


def record_candidates(page, did):
    """One description as one candidate: its title, archive, and every field on the page."""
    m = re.search(r'<div id="tituloUD"\s*>(.*?)</div>', page, re.S)
    arch = re.search(r'<div id\s*="nombre_archivo">.*?<a [^>]*>(.*?)</a>', page, re.S)
    return [_candidate(did, text(m.group(1)) if m else '', archive=text(arch.group(1)) if arch else '',
                       fields=description_fields(page))]
