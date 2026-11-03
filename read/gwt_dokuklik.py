#!/usr/bin/env python3
"""gwt_dokuklik: the Basque historical archive's index of parish registers (Dokuklik), for gwt-read.

Euskadiko Artxibo Historikoa indexes about 5.6 million baptisms, marriages and burials of the parishes of Araba,
Bizkaia and Gipuzkoa, roughly 1490 to 1900, from the books of the three diocesan archives (Bilbao, San Sebastián,
Vitoria). The old dokuklik.euskadi.eus host is gone; the index now lives in the archive's site. Its search page fills
its result table from a JSON endpoint, one per kind of record; this reader posts the same body the page posts.

    search   POST .../sacramentales/busqueda{Bautismo,Matrimonio,Defuncion}   one page of up to --count rows

A row is an index entry, a lead, not the record: it names the book, folio and the archive's call number, so ask the
diocesan archive for the page before citing it. A candidate's `url` is the entry's record page
(.../bautismo/getFicha?bauid=N).

"none" is the answer's own count: recordsFiltered 0 with no row. Any other answer (a renamed field, a count with no
rows, the site's HTML error page) is a STOP. Standard library only.
"""
import json, re, sys, urllib.request

import gwt_reader

HOST = 'https://www.artxibo.euskadi.eus'
BASE = HOST + '/webartxi00-container/es/ad53aArchivoHistoricoWar'
SOURCE = 'dokuklik'
# A government archive's site with no stated limit (its robots.txt does not cover this application): one request every
# 5 s, never faster than one every 3 s.
PACE = {'start': 5.0, 'floor': 3.0, 'ceil': 300.0}
DAY = 86400
EXIT_USAGE = 64
SPEC = {
    'id': SOURCE,
    'help': 'Dokuklik, the Basque archive\'s index of parish baptisms, marriages and burials; public',
    'pace': PACE,
    # the index is finished work: a search is kept 30 days, a record page for good
    'cache_rules': ((r'/sacramentales/busqueda', 30 * DAY), (r'/getFicha\?', None)),
    'controls': [{'argv': ['--surname', 'Garcia'], 'min_count': 50}],
    'result_markers': [], 'negative_markers': [],     # a JSON answer: "none" is its own count
}
# kind: (the endpoint's name, the field prefix, the record page, the columns the page's table sends)
KINDS = {
    'baptism': ('Bautismo', 'bau', 'bautismo/getFicha?bauid=',
                ('baudfecsacra', 'baubautizadonom', 'bupadrenom', 'baumadrenom', 'baumunicipio', 'baulocalidad',
                 'bauparroquia', 'baubautizadonom')),
    'marriage': ('Matrimonio', 'mat', 'matrimonio/getFicha?matid=',
                 ('matfecsacra', 'matesposonom', 'matesposanom', 'matmunicipio', 'matlocalidad', 'matparroquia',
                  'matesposanom')),
    'death': ('Defuncion', 'def', 'defuncion/getFicha?defid=',
              ('deffecsacra', 'defdifuntonom', 'defmunicipio', 'deflocalidad', 'defparroquia', 'defdifuntonom')),
}
ARCHIVES = {'bilbao': '3', 'san-sebastian': '2', 'vitoria': '1'}


def search_url(kind):
    return '%s/sacramentales/busqueda%s' % (BASE, KINDS[kind][0])


def search_body(kind, given='', surname='', surname2='', year_from='', year_to='', archive='', count=100, start=0):
    """The JSON the page's table posts. The site reads the same three name fields for every kind (the first person:
    the child, the groom or the deceased), and refuses a body without its table's columns."""
    cols = [{'data': c, 'name': '', 'searchable': True, 'orderable': True, 'search': {'value': '', 'regex': False}}
            for c in KINDS[kind][3]]
    body = {'draw': 1, 'columns': cols, 'order': [{'column': 0, 'dir': 'asc'}], 'start': int(start),
            'length': int(count), 'search': {'value': '', 'regex': False},
            'archivosDiocesanos': [ARCHIVES[archive]] if archive else [], 'municipio': [], 'localidad': [],
            'parroquia': [], 'anioInicial': str(year_from or ''), 'anioFinal': str(year_to or ''),
            'bautismoHijoNombre': given, 'bautismoHijoApellido1': surname, 'bautismoHijoApellido2': surname2}
    return json.dumps(body, ensure_ascii=False, sort_keys=True).encode('utf-8')


def verdict(doc, kind, start=0):
    """'hits', 'none' or 'unknown' for {recordsFiltered: N, data: [...]}. A page that starts at or past the count is
    the end of the results."""
    if not isinstance(doc, dict) or not isinstance(doc.get('data'), list):
        return 'unknown'    # the result container renamed: a STOP
    n = doc.get('recordsFiltered')
    if not isinstance(n, int) or isinstance(n, bool):
        return 'unknown'
    rows = doc['data']
    if not rows and (n == 0 or start >= n):
        return 'none'
    pid = KINDS[kind][1] + 'id'
    if rows and all(isinstance(r, dict) and isinstance(r.get(pid), int) for r in rows):
        return 'hits'
    return 'unknown'


def _s(v):
    return re.sub(r'\s+', ' ', str(v)).strip() if v not in (None, '') and not re.fullmatch(r'-+', str(v)) else ''


def _name(*parts):
    return ' '.join(p for p in (_s(x) for x in parts) if p)


def candidate(r, kind):
    p = KINDS[kind][1]
    rel = []
    if kind == 'baptism':
        given, sur = _s(r.get('baubautizadonom')), [_s(r.get('baubautizadoape1')), _s(r.get('baubautizadoape2'))]
        rel = [{'role': 'father', 'name': _name(r.get('baupadrenom'), r.get('baupadreape1'), r.get('baupadreape2'))},
               {'role': 'mother', 'name': _name(r.get('baumadrenom'), r.get('baumadreape1'), r.get('baumadreape2'))}]
        date = r.get('baudfecsacra')
    elif kind == 'marriage':
        # the site's own field for the groom's second surname is spelled mathesposope2
        given, sur = _s(r.get('matesposonom')), [_s(r.get('matesposoape1')), _s(r.get('mathesposope2'))]
        rel = [{'role': 'bride', 'name': _name(r.get('matesposanom'), r.get('matesposaape1'), r.get('matesposaape2'))}]
        date = r.get('matfecsacra')
    else:
        given, sur = _s(r.get('defdifuntonom')), [_s(r.get('defdifuntoape1')), _s(r.get('defdifuntoape2'))]
        date = r.get('deffecsacra')
    sur = [s for s in sur if s]
    place = ', '.join(x for x in (_s(r.get(p + 'parroquia')), _s(r.get(p + 'localidad')), _s(r.get(p + 'municipio')),
                                  _s(r.get(p + 'territorio'))) if x)
    rid = r[p + 'id']
    return {'source': SOURCE, 'id': '%s-%s' % (p, rid), 'name': _name(given, *sur), 'role': 'principal',
            'principal': True, 'given': given or None, 'surnames': sur or None,
            'events': {kind: {'date': _s(date) or None, 'place': place or None}},
            'relatives': [x for x in rel if x['name']],
            'book': _s(r.get(p + 'titulo')) or None, 'folio': _s(r.get(p + 'foliorv')) or None,
            'call_number': _s(r.get(p + 'signa')) or None, 'reference': _s(r.get(p + 'codref')) or None,
            'diocese': _s(r.get(p + 'diocesis')) or None, 'note': _s(r.get(p + 'observa')) or None,
            'url': '%s/%s%s' % (BASE, KINDS[kind][2], rid)}


def add_search_args(sp):
    sp.add_argument('--kind', default='baptism', choices=sorted(KINDS), help='default baptism')
    sp.add_argument('--given', default='', help='a first name; the site matches it, like the surnames, in the '
                    'record\'s people (a marriage search on a surname also finds brides)')
    sp.add_argument('--surname', default='', help='a first surname')
    sp.add_argument('--surname2', default='', help='a second surname')
    sp.add_argument('--from', dest='year_from', default='')
    sp.add_argument('--to', dest='year_to', default='')
    sp.add_argument('--archive', default='', choices=sorted(ARCHIVES), help='one diocesan archive (default all)')
    sp.add_argument('--count', type=int, default=100, choices=range(1, 101), metavar='1-100')
    sp.add_argument('--page', type=int, default=1)


def search(a, R):
    if not (a.given or a.surname or a.surname2):
        print('gwt-read: dokuklik: give --given, --surname or --surname2', file=sys.stderr, flush=True)
        return EXIT_USAGE
    start = (max(1, a.page) - 1) * a.count
    url = search_url(a.kind)
    data = search_body(a.kind, a.given, a.surname, a.surname2, a.year_from, a.year_to, a.archive, a.count, start)
    status, rh, body, cached = gwt_reader.get_api(
        R, url, {'accept': 'application/json', 'content-type': 'application/json'},
        expect=lambda d: verdict(d, a.kind, start) != 'unknown', refresh=a.refresh, data=data)
    doc = json.loads(body)
    cands = [candidate(r, a.kind) for r in doc['data']]
    env = gwt_reader.envelope(url, None, json_body=doc, request=json.loads(data), verdict=verdict(doc, a.kind, start),
                              status=status, from_cache=cached)
    words = [SOURCE, a.kind, a.given, a.surname, a.surname2, a.year_from, a.year_to, a.archive,
             a.page if a.page > 1 else '']
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(url + '\n' + data.decode(), words), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    print('results\t%s total, %d read%s%s' % (doc['recordsFiltered'], len(cands), gwt_reader.past_end(cands, start),
                                             ' (cache)' if cached else ''), flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY


def build(src, a, urlopen, gate):
    """The default reader, with a cache that keeps POST answers too: every Dokuklik search is a POST."""
    import gwt_read
    gate = gate or gwt_read.Gate(a.gate_dir or '~/.cache/gwt-gate-%s' % src['id'], **src['pace'])
    cache = gwt_read.FSCache(a.cache_dir, rules=src['cache_rules'], methods=('GET', 'POST'))
    return gwt_reader.Reader(cache, gate, a.lane, gwt_read.urllib_fetcher(None, a.user_agent,
                                                                         urlopen or urllib.request.urlopen),
                             log=gwt_read.err)


SPEC['build'] = build
