#!/usr/bin/env python3
"""gwt_archiveorg: the Internet Archive (archive.org), scanned books and registers, searched for gwt-read.

The Internet Archive holds millions of scanned books with their OCR: printed parish register transcriptions, county
and family histories, city directories, nobility books, official gazettes. Its search is open, no key and no login.

    search          https://archive.org/advancedsearch.php?q=...&output=json    the catalogue (title, creator,
                                                                                 subject, year ...)
    search --fulltext  https://archive.org/services/search/beta/page_production/?service_backend=fts
                                                                                 the words inside the scanned text

A row is a book or an item, never a person: its candidate carries the item's title as its name, role `item`, never
principal, so gwt-rank ranks it weak. Each row also gives the item's IIIF manifest
(https://iiif.archive.org/iiif/{id}/manifest.json) for `gwt fetch iiif`, and, for a text, its OCR
(https://archive.org/download/{id}/{id}_djvu.txt); the `item` lines print both. A full-text row adds the snippets
around the words. "none" is the API's own count: `numFound` (catalogue) or `hits.total` (full text) 0.

The full-text answer echoes the caller's IP address and User-Agent in a `session_context` block; the envelope keeps
only the answer's `response` part, so that never reaches the evidence files. Standard library only.
"""
import json, re, sys, urllib.parse

import gwt_reader

HOST = 'https://archive.org'
SOURCE = 'archiveorg'
PACE = {'start': 3.0, 'floor': 2.0, 'ceil': 120.0}
DAY = 86400
FIELDS = ('identifier', 'title', 'creator', 'date', 'year', 'mediatype', 'language', 'collection', 'subject')
SPEC = {
    'id': SOURCE,
    'help': 'Internet Archive, scanned books and registers: catalogue or full text; open API, no account',
    'pace': PACE,
    'cache_rules': ((r'/advancedsearch\.php\?', 7 * DAY), (r'/services/search/', 7 * DAY)),
    # a printed-register title search: several hundred items for years
    'controls': [{'argv': ['--query', 'title:(parish register)'], 'min_count': 10}],
    'result_markers': [], 'negative_markers': [],      # a JSON API: verdict() reads the answer's own count
    'search_help': 'a catalogue search (--query), or a search of the scanned text (--fulltext)',
}
ACCEPT = 'application/json'
EXIT_USAGE = 64


def search_url(query, mediatype='texts', count=50, page=1):
    q = query if not mediatype else '(%s) AND mediatype:(%s)' % (query, mediatype)
    return HOST + '/advancedsearch.php?' + urllib.parse.urlencode(
        [('q', q)] + [('fl[]', f) for f in FIELDS] + [('rows', max(1, min(int(count), 100))),
                                                      ('page', max(1, int(page))), ('output', 'json')])


def fulltext_url(query, count=50, page=1):
    return HOST + '/services/search/beta/page_production/?' + urllib.parse.urlencode(
        [('user_query', query), ('service_backend', 'fts'), ('hits_per_page', max(1, min(int(count), 100))),
         ('page', max(1, int(page)))])


def manifest_url(ident):
    return 'https://iiif.archive.org/iiif/%s/manifest.json' % urllib.parse.quote(ident)


def ocr_url(ident):
    i = urllib.parse.quote(ident)
    return '%s/download/%s/%s_djvu.txt' % (HOST, i, i)


def _count(n):
    return isinstance(n, int) and not isinstance(n, bool)


def verdict(doc, offset=0):
    """'hits', 'none' or 'unknown' for a catalogue answer: {responseHeader: {status: 0}, response: {numFound, docs}}.
    A page that starts at or past numFound (`offset`, the rows before it) is the end of the results."""
    if not isinstance(doc, dict) or (doc.get('responseHeader') or {}).get('status') != 0:
        return 'unknown'
    r = doc.get('response')
    if not isinstance(r, dict) or not _count(r.get('numFound')) or not isinstance(r.get('docs'), list):
        return 'unknown'    # the result container renamed: a STOP
    if r['numFound'] == 0 and not r['docs']:
        return 'none'
    # paged past the last row: the end of the results, not a changed answer
    if not r['docs'] and offset >= r['numFound']:
        return 'none'
    if r['docs'] and all(isinstance(d, dict) and d.get('identifier') for d in r['docs']):
        return 'hits'
    return 'unknown'


def fulltext_verdict(doc, offset=0):
    """The same for a full-text answer: {response: {header: {succeeded: true}, body: {hits: {total, hits}}}}."""
    r = doc.get('response') if isinstance(doc, dict) else None
    if not isinstance(r, dict) or (r.get('header') or {}).get('succeeded') is not True:
        return 'unknown'
    h = (r.get('body') or {}).get('hits') if isinstance(r.get('body'), dict) else None
    if not isinstance(h, dict) or not _count(h.get('total')) or not isinstance(h.get('hits'), list):
        return 'unknown'
    if h['total'] == 0 and not h['hits']:
        return 'none'
    # paged past the last row: the end of the results, not a changed answer
    if not h['hits'] and offset >= h['total']:
        return 'none'
    if h['hits'] and all(isinstance(x, dict) and (x.get('fields') or {}).get('identifier') for x in h['hits']):
        return 'hits'
    return 'unknown'


def _text(v):
    return '; '.join(str(x) for x in v) if isinstance(v, list) else str(v or '')


def _snippet(s):
    return re.sub(r'\s+', ' ', s.replace('{{{', '').replace('}}}', '')).strip()


def candidate(f, snippets=()):
    ident = f['identifier']
    y = f.get('year')
    y = int(y) if isinstance(y, int) or (isinstance(y, str) and y.isdigit()) else None
    c = {'source': SOURCE, 'id': ident, 'name': _text(f.get('title')) or ident, 'role': 'item', 'principal': False,
         'given': None, 'surnames': None, 'events': {}, 'relatives': [], 'url': '%s/details/%s' % (HOST, ident),
         'year': y, 'creator': _text(f.get('creator')), 'mediatype': _text(f.get('mediatype')),
         'iiif_manifest': manifest_url(ident)}
    if c['mediatype'] == 'texts':
        c['ocr'] = ocr_url(ident)
    if snippets:
        c['snippets'] = [_snippet(s) for s in snippets]
    return c


def add_search_args(sp):
    sp.add_argument('--query', required=True, help='catalogue: the site\'s query syntax, "title:(parish register) '
                    'AND date:[1800 TO 1900]"; with --fulltext: words, "\\"John Smith\\" baptized"')
    sp.add_argument('--fulltext', action='store_true', help='search the words inside the scanned text, not the '
                    'catalogue; slower (the site takes tens of seconds)')
    sp.add_argument('--mediatype', default='texts', help='catalogue only: texts (default), or "" for any')
    sp.add_argument('--count', type=int, default=50, choices=range(1, 101), metavar='1-100')
    sp.add_argument('--page', type=int, default=1)


def search(a, R):
    if not a.query.strip():
        print('gwt-read: archiveorg: --query is empty', file=sys.stderr, flush=True)
        return EXIT_USAGE
    full = a.fulltext
    url = fulltext_url(a.query, a.count, a.page) if full else search_url(a.query, a.mediatype, a.count, a.page)
    off = (max(1, int(a.page)) - 1) * max(1, min(int(a.count), 100))
    judge = fulltext_verdict if full else verdict
    status, rh, body, cached = gwt_reader.get_api(R, url, {'accept': ACCEPT},
                                                  expect=lambda d: judge(d, off) != 'unknown',
                                                  refresh=a.refresh)
    doc = json.loads(body)
    if full:
        kept = {'response': doc['response']}         # never session_context: it holds the caller's IP address
        hits = doc['response']['body']['hits']
        total, cands = hits['total'], [candidate(h['fields'], (h.get('highlight') or {}).get('text') or ())
                                       for h in hits['hits']]
    else:
        kept = doc
        total, cands = doc['response']['numFound'], [candidate(d) for d in doc['response']['docs']]
    env = gwt_reader.envelope(url, None, json_body=kept, verdict=judge(doc, off), status=status, from_cache=cached)
    words = [SOURCE, 'fulltext' if full else 'catalogue', a.query, a.mediatype, a.page]
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(url, words), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    for c in cands:
        print('item\t%s\t%s\t%s' % (c['id'], c['iiif_manifest'], c.get('ocr', '')), flush=True)
    print('results\t%s total, %d read%s%s' % (total, len(cands), gwt_reader.past_end(cands, off),
                                            ' (cache)' if cached else ''), flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY
