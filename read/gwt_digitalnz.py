#!/usr/bin/env python3
"""gwt_digitalnz: Papers Past, New Zealand's historic newspapers, through the DigitalNZ API, for gwt-read.

Papers Past (National Library of New Zealand) holds the scanned text of New Zealand newspapers from 1839, with
births, deaths and marriages notices, shipping lists, court reports and obituaries. Its own site sits behind a bot
wall, so this reader never asks it: it searches the DigitalNZ API, which indexes the same articles with their text,
https://api.digitalnz.org/records.json , kept to the Papers Past collection. Anonymous use is allowed at a low
volume; this reader keeps to one request every 3 s.

    search   --query '"John Smith"' [--year 1896]   one row per article, with its Papers Past link and the words
             around the query in its text

A row is an article, never a person: role `article`, never principal. "none" is the API's own count,
`result_count` 0. Standard library only.
"""
import json, re, sys, urllib.parse

import gwt_reader

API = 'https://api.digitalnz.org/records.json'
SOURCE = 'digitalnz'
COLLECTION = 'Papers Past'
PACE = {'start': 3.0, 'floor': 2.0, 'ceil': 120.0}
DAY = 86400
FIELDS = ('id', 'title', 'date', 'year', 'publisher', 'landing_url', 'display_collection', 'fulltext')
SPEC = {
    'id': SOURCE,
    'help': 'Papers Past, New Zealand newspapers from 1839, searched through the DigitalNZ API; no key',
    'pace': PACE,
    'cache_rules': ((r'api\.digitalnz\.org/records\.json\?', 30 * DAY),),
    # Smith in Papers Past: over a million articles for years
    'controls': [{'argv': ['--query', 'Smith'], 'min_count': 10}],
    'result_markers': [], 'negative_markers': [],      # a JSON API: verdict() reads the answer's own count
    'search_help': 'a newspaper search; each row is one article with its link and the words around the query',
}
ACCEPT = 'application/json'
EXIT_USAGE = 64
AROUND = 200            # characters of article text kept on each side of the first match
MAX_PAGE = 100          # without a key the API refuses a page above 100 (HTTP 400)


def search_url(query, year='', count=20, page=1):
    p = [('text', query), ('and[primary_collection][]', COLLECTION)]
    if year:
        p.append(('and[year][]', str(int(year))))
    p += [('per_page', max(1, min(int(count), 100))), ('page', max(1, int(page))), ('fields', ','.join(FIELDS))]
    return API + '?' + urllib.parse.urlencode(p)


def _count(n):
    return isinstance(n, int) and not isinstance(n, bool)


def verdict(doc, offset=0):
    """'hits', 'none' or 'unknown': {search: {result_count: N, results: [...]}}. A page that starts at or past the
    count (`offset`, the rows before it) is the end of the results."""
    s = doc.get('search') if isinstance(doc, dict) else None
    if not isinstance(s, dict) or not _count(s.get('result_count')) or not isinstance(s.get('results'), list):
        return 'unknown'    # the result container renamed: a STOP
    n, res = s['result_count'], s['results']
    if n == 0 and not res:
        return 'none'
    # paged past the last row: the end of the results, not a changed answer
    if not res and offset >= n:
        return 'none'
    if res and all(isinstance(r, dict) and r.get('id') and r.get('landing_url') for r in res):
        return 'hits'
    return 'unknown'


def _first(v):
    return v[0] if isinstance(v, list) and v else v if not isinstance(v, list) else None


def _year(r):
    y = _first(r.get('year'))
    if isinstance(y, int) and not isinstance(y, bool):
        return y
    m = re.match(r'(\d{4})', str(y or _first(r.get('date')) or ''))
    return int(m.group(1)) if m else None


def snippet(text, query):
    """The words around the first query word found in the article's text; '' when none is (the API matches stems)."""
    text = ' '.join(str(text or '').split())
    low = text.lower()
    for w in sorted(re.findall(r'\w+', query.lower()), key=len, reverse=True):
        i = low.find(w)
        if i >= 0:
            a, b = max(0, i - AROUND), min(len(text), i + len(w) + AROUND)
            return ('...' if a else '') + text[a:b] + ('...' if b < len(text) else '')
    return ''


def candidate(r, query):
    return {'source': SOURCE, 'id': str(r['id']), 'name': ' '.join(str(r.get('title') or r['id']).split()),
            'role': 'article', 'principal': False, 'given': None, 'surnames': None, 'events': {}, 'relatives': [],
            'url': r['landing_url'], 'date': str(_first(r.get('date')) or '')[:10], 'year': _year(r),
            'publisher': _first(r.get('publisher')) or '', 'collection': r.get('display_collection') or '',
            'text_url': 'https://api.digitalnz.org/records/%s.json?fields=fulltext' % r['id'],
            'snippet': snippet(r.get('fulltext'), query)}


def add_search_args(sp):
    sp.add_argument('--query', required=True, help='words, a phrase in quotes: \'"John Smith" Dunedin\'')
    sp.add_argument('--year', type=int, help='the year of the issue')
    sp.add_argument('--count', type=int, default=20, choices=range(1, 101), metavar='1-100')
    sp.add_argument('--page', type=int, default=1)


def search(a, R):
    if not a.query.strip():
        print('gwt-read: digitalnz: --query is empty', file=sys.stderr, flush=True)
        return EXIT_USAGE
    if a.page > MAX_PAGE:
        print('gwt-read: digitalnz: without an API key DigitalNZ serves pages 1 to %d; narrow with --year or more '
              'words, or raise --count' % MAX_PAGE, file=sys.stderr, flush=True)
        return EXIT_USAGE
    url = search_url(a.query, a.year or '', a.count, a.page)
    off = (max(1, int(a.page)) - 1) * max(1, min(int(a.count), 100))
    status, rh, body, cached = gwt_reader.get_api(R, url, {'accept': ACCEPT},
                                                  expect=lambda d: verdict(d, off) != 'unknown', refresh=a.refresh)
    doc = json.loads(body)
    cands = [candidate(r, a.query) for r in doc['search']['results']]
    env = gwt_reader.envelope(url, None, json_body=doc, verdict=verdict(doc, off), status=status, from_cache=cached)
    words = [SOURCE, a.query, a.year, a.count, a.page]
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(url, words), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    for c in cands:
        print('item\t%s\t%s\t%s' % (c['id'], c['url'], c['text_url']), flush=True)
    print('results\t%s total, %d read%s%s' % (doc['search']['result_count'], len(cands),
                                            gwt_reader.past_end(cands, off), ' (cache)' if cached else ''), flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY
