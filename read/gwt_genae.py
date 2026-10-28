#!/usr/bin/env python3
"""gwt_genae: Genae (genae.org), the volunteer index of parish baptisms, marriages and burials of south-east Spain,
for gwt-read.

Genae is an index, not the record: a row says who was baptised, married or buried, when, where and the first
names of the parents. A candidate is one row, so gwt-rank can weigh it; it proves nothing until the parish book
or the civil act is seen. The site's search answers three HTML fragments, read here without a browser:

    buscar/resultados   the baptism block (the first of four tabs)
    buscar/bloque       one block, with categoria=casados|casadas|defunciones

Rows the site adds as phonetic suggestions (class `fila-fonetica`) are kept but marked `phonetic`, so nobody takes
a spelling variant for an exact hit. Only the first page of each block is read: a search that matches more rows
than one page says so in `total`, and the lane narrows the search instead of paging. Standard library only.
"""
import html, re, urllib.parse

HOST = 'https://genae.org'
SOURCE = 'genae'
# A volunteer project: one request every 20 s at best, never faster than one every 15 s, after a hit one every
# 180 s. A search over the four blocks costs four requests.
PACE = {'start': 20.0, 'floor': 15.0, 'ceil': 180.0}
ACCEPT = 'text/html'
LANGUAGE = 'es-ES,es;q=0.9'
BLOCKS = ('bautismos', 'casados', 'casadas', 'defunciones')
PROVINCES = ('mu', 'ab', 'al', 'am', 'gr')

_ROW = re.compile(r'<tr class="([^"]*)" data-registro-tipo="([^"]*)" data-registro-id="(\d+)" data-registro-ano="(\d*)"[^>]*>(.*?)</tr>', re.S)
_CELL = re.compile(r'<td data-col="([^"]+)"[^>]*>(.*?)</td>', re.S)
_SECTION = re.compile(r'<section id="bloque-(\w+)"')


def text(fragment):
    t = re.sub(r'<br\s*/?>', ' ', fragment or '')
    t = html.unescape(re.sub(r'<[^>]+>', '', t))   # inline marks (the highlight of a matched name) add no space
    return re.sub(r'\s+', ' ', t).strip()


def params(given='', surname1='', surname2='', province='mu', locality='', year_from=1503, year_to=1986):
    """The site's own query: n, a1, a2 (each 'starts with'), r province, l locality, d and h year range."""
    q = [('n', given), ('a1', surname1), ('a2', surname2), ('r', province)]
    if locality:
        q.append(('l', locality))
    q += [('d', str(int(year_from))), ('h', str(int(year_to)))]
    return urllib.parse.urlencode([(k, v) for k, v in q if v != ''], quote_via=urllib.parse.quote)


def block_url(block, query):
    """The URL of one block for one query."""
    if block == 'bautismos':
        return '%s/buscar/resultados?%s' % (HOST, query)
    return '%s/buscar/bloque?categoria=%s&%s' % (HOST, block, query)


def is_block(page, block):
    """True only for the fragment that holds the block asked for, with its table: an empty frame, a block
    page or the site's search form is not an answer."""
    m = _SECTION.search(page or '')
    return bool(m and m.group(1) == block and '<table' in page) or (
        block == 'bautismos' and 'id="filas-bautismos"' in (page or ''))


def rows(page):
    """[(kind, record id, year, phonetic, {column: text})] for every row of a block."""
    out = []
    for cls, kind, rid, year, body in _ROW.findall(page or ''):
        cells = {c: text(v) for c, v in _CELL.findall(body)}
        out.append((kind, rid, int(year) if year else None, 'fonetica' in cls, cells))
    return out


def _year(date):
    m = re.search(r'(\d{4})\s*$', date or '')
    return int(m.group(1)) if m else None


def _name(*parts):
    return ' '.join(p for p in parts if p)


def _rel(role, value):
    return [{'role': role, 'name': v} for v in re.split(r'\s*/\s*', value or '') if v.strip()]


def candidates(page, block):
    """Each row as a gwt-rank candidate. A baptism or a burial is about one person, who is the principal; a
    marriage names two, so both are principals of their own candidate, with the other as spouse."""
    out = []
    for kind, rid, year, phonetic, c in rows(page):
        y = _year(c.get('Fecha')) or year
        place = c.get('Localidad', '')
        base = {'source': SOURCE, 'id': '%s-%s' % (kind, rid), 'role': kind, 'principal': True, 'phonetic': phonetic,
                'url': HOST + '/buscar', 'date': c.get('Fecha', ''), 'note': c.get('Observaciones', ''),
                'locality': place}
        if block in ('bautismos', 'defunciones'):
            sur = [s for s in (c.get('1º Apellido'), c.get('2º Apellido')) if s]
            ev = 'birth' if block == 'bautismos' else 'death'
            rel = []
            if c.get('Padre'):
                rel.append({'role': 'father', 'name': c['Padre']})
            if c.get('Madre'):
                rel.append({'role': 'mother', 'name': c['Madre']})
            out.append(dict(base, name=_name(c.get('Nombre'), *sur), given=c.get('Nombre') or None,
                            surnames=' '.join(sur) or None, events={ev: {'year': y, 'year_to': y, 'place': place}},
                            relatives=rel))
        else:
            for who, other, parents in (('Esposo', 'Esposa', 'Padres esposo'), ('Esposa', 'Esposo', 'Padres esposa')):
                full = c.get(who, '')
                if not full:
                    continue
                rel = [{'role': 'spouse', 'name': c.get(other, '')}] if c.get(other) else []
                rel += _rel('parent', c.get(parents, ''))
                out.append(dict(base, id='%s-%s-%s' % (kind, rid, who.lower()), name=full, given=None, surnames=None,
                                events={'marriage': {'year': y, 'year_to': y, 'place': place}}, relatives=rel))
    return out
