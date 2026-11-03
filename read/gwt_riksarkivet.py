#!/usr/bin/env python3
"""gwt_riksarkivet: Riksarkivet, the Swedish National Archives, its catalogue of scanned volumes, for gwt-read.

Riksarkivet has scanned the church books of every Swedish parish (births, marriages, deaths, household examination
rolls), estate inventories, court and military rolls, and shows them free. Its search API is open, no key and no
login: https://data.riksarkivet.se/api/records . A row here is one volume: its reference code, the archive and series
it belongs to, its years, and the IIIF manifest of its scans (https://lbiiif.riksarkivet.se/arkis!<id>/manifest),
which `gwt fetch iiif` reads to save the pages.

    search   --query "Uppsala domkyrkoförsamling"   volumes whose catalogue entry holds the words, scanned ones only
             unless --all

The API sends its facets with every answer, so an answer is large; a page is 20 rows by default (`limit`, at most
100) and every answer is cached. "none" is the API's own count, `totalHits` 0. Standard library only.
"""
import json, re, sys, urllib.parse

import gwt_reader

API = 'https://data.riksarkivet.se/api/records'
SOURCE = 'riksarkivet'
PACE = {'start': 3.0, 'floor': 2.0, 'ceil': 120.0}
DAY = 86400
SPEC = {
    'id': SOURCE,
    'help': 'Riksarkivet (Sweden): scanned church books and other volumes, with their IIIF manifests; open API',
    'pace': PACE,
    # the catalogue changes slowly and the answers are large: a search is kept 30 days
    'cache_rules': ((r'data\.riksarkivet\.se/api/records\?', 30 * DAY),),
    # Uppsala Cathedral parish: several hundred scanned volumes for years
    'controls': [{'argv': ['--query', 'Uppsala domkyrkoförsamling'], 'min_count': 10}],
    'result_markers': [], 'negative_markers': [],      # a JSON API: verdict() reads the answer's own count
    'search_help': 'a catalogue search; each row is one volume with its reference code and IIIF manifest',
}
ACCEPT = 'application/json'
EXIT_USAGE = 64


def search_url(query, kind='Volume', scanned=True, count=20, page=1):
    n = max(1, min(int(count), 100))
    p = [('text', query)]
    if kind:
        p.append(('facet', 'ObjectType:Record;Type:%s' % kind))
    if scanned:
        p.append(('only_digitised_materials', 'true'))
    p += [('offset', (max(1, int(page)) - 1) * n), ('limit', n)]
    return API + '?' + urllib.parse.urlencode(p)


def _count(n):
    return isinstance(n, int) and not isinstance(n, bool)


def verdict(doc, offset=0):
    """'hits', 'none' or 'unknown': {totalHits: N, items: [...]}. A page that starts at or past the total (`offset`,
    the rows before it) is the end of the results."""
    if not isinstance(doc, dict) or not _count(doc.get('totalHits')) or not isinstance(doc.get('items'), list):
        return 'unknown'    # the result container renamed: a STOP
    n, items = doc['totalHits'], doc['items']
    if n == 0 and not items:
        return 'none'
    # paged past the last row: the end of the results, not a changed answer
    if not items and offset >= n:
        return 'none'
    if items and all(isinstance(i, dict) and i.get('id') and isinstance(i.get('metadata'), dict) for i in items):
        return 'hits'
    return 'unknown'


def _years(date):
    """'1877 - 1883' -> (1877, 1883); '1829 - ' -> (1829, None); 'odat' (undated) -> (None, None)."""
    ys = [int(y) for y in re.findall(r'(?<!\d)(\d{4})(?!\d)', str(date or ''))]
    return (ys[0], ys[-1] if len(ys) > 1 else None) if ys else (None, None)


def _caption(x):
    return ' '.join(str((x or {}).get('caption') or '').split())


def candidate(item):
    md, links = item['metadata'], item.get('_links') or {}
    path = [_caption(h) for h in md.get('hierarchy') or [] if _caption(h)]
    ref = md.get('referenceCode') or ''
    y0, y1 = _years(md.get('date'))
    title = path[-1] if path else _caption(item) or ref or item['id']
    images = links.get('image') or []
    images = [images] if isinstance(images, str) else [u for u in images if isinstance(u, str)]
    return {'source': SOURCE, 'id': item['id'], 'name': '%s (%s)' % (title, ' '.join(str(md.get('date') or '').split()))
            if md.get('date') else title, 'role': (item.get('type') or 'item').lower(), 'principal': False,
            'given': None, 'surnames': None, 'events': {}, 'relatives': [],
            'url': links.get('html') or 'https://sok.riksarkivet.se/arkiv/%s' % urllib.parse.quote(item['id']),
            'reference_code': ref, 'archive': path[0] if path else '', 'series': path[1:],
            'date': md.get('date') or '', 'year': y0, 'year_to': y1,
            'held_by': [_caption(a) for a in md.get('archivalInstitution') or [] if _caption(a)],
            'scanned': bool(md.get('onlyDigitisedMaterials')), 'iiif_manifests': images}


def add_search_args(sp):
    sp.add_argument('--query', required=True, help='words of the catalogue entry: a parish, "Uppsala '
                    'domkyrkoförsamling", or a series, "husförhörslängder Uppsala"')
    sp.add_argument('--type', dest='kind', default='Volume', help='Volume (default), or "" for every kind of entry '
                    '(archives, series, maps)')
    sp.add_argument('--all', action='store_true', help='also volumes that are not scanned (no manifest)')
    sp.add_argument('--count', type=int, default=20, choices=range(1, 101), metavar='1-100')
    sp.add_argument('--page', type=int, default=1)


def search(a, R):
    if not a.query.strip():
        print('gwt-read: riksarkivet: --query is empty', file=sys.stderr, flush=True)
        return EXIT_USAGE
    url = search_url(a.query, a.kind, not a.all, a.count, a.page)
    off = (max(1, int(a.page)) - 1) * max(1, min(int(a.count), 100))
    status, rh, body, cached = gwt_reader.get_api(R, url, {'accept': ACCEPT},
                                                  expect=lambda d: verdict(d, off) != 'unknown', refresh=a.refresh)
    doc = json.loads(body)
    cands = [candidate(i) for i in doc['items']]
    kept = {k: doc[k] for k in ('totalHits', 'hits', 'offset', 'items') if k in doc}   # the facets are most of it
    env = gwt_reader.envelope(url, None, json_body=kept, verdict=verdict(doc, off), status=status, from_cache=cached)
    words = [SOURCE, a.query, a.kind, 'all' if a.all else 'scanned', a.count, a.page]
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(url, words), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    for c in cands:
        for m in c['iiif_manifests'] or ['']:
            print('item\t%s\t%s' % (c['reference_code'] or c['id'], m), flush=True)
    print('results\t%s total, %d read%s%s' % (doc['totalHits'], len(cands), gwt_reader.past_end(cands, off),
                                            ' (cache)' if cached else ''), flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY
