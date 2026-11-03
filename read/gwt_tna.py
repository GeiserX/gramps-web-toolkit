#!/usr/bin/env python3
"""gwt_tna: The National Archives (UK) Discovery catalogue, for gwt-read.

Discovery describes the records of The National Archives at Kew and of more than 2,500 other UK archives: wills
proved in the Prerogative Court of Canterbury (series PROB 11, one entry per will with the testator's name), army
and navy service records (WO 97, WO 363, ADM 188 ...), naturalisation papers (HO 334, HO 405), and the parish,
estate and family papers each local record office holds. Its API is open, no key and no login, under the Open
Government Licence, and allows about 3,000 calls a day: the gate keeps this reader to 120 an hour.

    search   https://discovery.nationalarchives.gov.uk/API/search/records?sps.searchQuery=...   catalogue entries;
             --series "PROB 11" for wills, --held-by other for local archives; the `archive` lines say which
             archives hold matching entries ("which archive holds X")
    record   https://discovery.nationalarchives.gov.uk/API/records/v1/details/{id}    one entry in full

A row is a catalogue entry, never a person: role `item`, never principal. "none" is the API's own count, `count`
0. The details API answers an unknown id with HTTP 204 and no body; that is read as "no such record", exit 1,
never as a block. Standard library only.
"""
import json, re, sys, urllib.parse

import gwt_reader

HOST = 'https://discovery.nationalarchives.gov.uk'
API = HOST + '/API'
SOURCE = 'tna'
PACE = {'start': 3.0, 'floor': 2.0, 'ceil': 300.0}
PER_HOUR = 120          # the API allows about 3,000 calls a day; 120 an hour stays under it all day
DAY = 86400
HELD = {'all': 'ALL', 'tna': 'TNA', 'other': 'OTH'}
ACCEPT = 'application/json'
EXIT_USAGE = 64
MAX_ROWS = 10000        # Discovery answers HTTP 400 when page times page size passes 10,000


def no_record_fetcher(fetch):
    """The details API answers an unknown id with HTTP 204 and no body, which Reader.get would read as an empty frame
    (a block). Here it becomes a 404 with the reason, so it is an error for this one id (exit 1), never a gate hit."""
    def f(method, url, headers, data):
        status, rh, body = fetch(method, url, headers, data)
        if status == 204 and '/records/v1/details/' in url:
            return 404, rh, b'{"message": "no record with this id"}'
        return status, rh, body
    return f


def build(src, a, urlopen, gate):
    """The default public Reader, with the API's daily limit in the gate and the 204 of an unknown id made a 404."""
    import gwt_read
    gate = gate or gwt_read.Gate(a.gate_dir or '~/.cache/gwt-gate-%s' % SOURCE, per_hour=PER_HOUR, **PACE)
    R = gwt_read._default_build(src, a, urlopen, gate)
    R.fetcher = no_record_fetcher(R.fetcher)
    return R


SPEC = {
    'id': SOURCE,
    'help': 'The National Archives (UK) Discovery: wills, service records and 2,500 UK archives; open API',
    'pace': PACE,
    # the catalogue changes slowly: a search is kept 30 days, an entry 90
    'cache_rules': ((r'/API/search/records\?', 30 * DAY), (r'/API/records/v1/details/', 90 * DAY)),
    # thousands of PCC wills of a John Smith for years
    'controls': [{'argv': ['--query', 'Will of John Smith', '--series', 'PROB 11'], 'min_count': 10}],
    'result_markers': [], 'negative_markers': [],      # a JSON API: verdict() reads the answer's own count
    'search_help': 'a catalogue search; each row is one catalogue entry, with the archive that holds it',
    'record_help': 'one catalogue entry in full, by its id (C1234, D280526 or a long id from a search row)',
    'build': build,
}


def search_url(query, series=(), held='all', year_from='', year_to='', count=20, page=1):
    p = [('sps.searchQuery', query)]
    p += [('sps.recordSeries', s) for s in series]
    if held != 'all':
        p.append(('sps.heldByCode', HELD[held]))
    if year_from:
        p.append(('sps.dateFrom', '%04d-01-01' % int(year_from)))
    if year_to:
        p.append(('sps.dateTo', '%04d-12-31' % int(year_to)))
    p += [('sps.resultsPageSize', max(1, min(int(count), 100))), ('sps.page', max(1, int(page)))]
    return API + '/search/records?' + urllib.parse.urlencode(p)


def details_url(rid):
    return API + '/records/v1/details/' + urllib.parse.quote(rid, safe='')


def web_url(rid):
    return HOST + '/details/r/' + urllib.parse.quote(rid, safe='')


def _count(n):
    return isinstance(n, int) and not isinstance(n, bool)


def verdict(doc, offset=0):
    """'hits', 'none' or 'unknown': {count: N, records: [...]}. A page that starts at or past the count (`offset`, the
    rows before it) is the end of the results."""
    if not isinstance(doc, dict) or not _count(doc.get('count')) or not isinstance(doc.get('records'), list):
        return 'unknown'    # the result container renamed: a STOP
    n, recs = doc['count'], doc['records']
    if n == 0 and not recs:
        return 'none'
    # paged past the last row: the end of the results, not a changed answer
    if not recs and offset >= n:
        return 'none'
    if recs and all(isinstance(r, dict) and r.get('id') and isinstance(r.get('heldBy'), list) for r in recs):
        return 'hits'
    return 'unknown'


def is_details(doc, rid):
    """A details answer is the entry asked for: an object whose id is the one in the URL."""
    return isinstance(doc, dict) and doc.get('id') == rid


def _year(n):
    s = str(n or '')
    return int(s[:4]) if len(s) >= 4 and s[:4].isdigit() and int(s[:4]) else None


def _plain(s):
    return ' '.join(re.sub(r'<[^>]+>', ' ', str(s or '')).split())


def candidate(r):
    return {'source': SOURCE, 'id': r['id'], 'name': _plain(r.get('title') or r.get('description')) or r['id'],
            'role': 'item', 'principal': False, 'given': None, 'surnames': None, 'events': {}, 'relatives': [],
            'url': web_url(r['id']), 'reference': r.get('reference') or '', 'held_by': r['heldBy'],
            'dates': r.get('coveringDates') or '', 'year': _year(r.get('numStartDate')),
            'year_to': _year(r.get('numEndDate')), 'description': _plain(r.get('description')),
            'context': _plain(r.get('context'))}


def record_candidate(d):
    sc = dict(d.get('scopeContent') or {})
    # the description opens with a document-type code (W for a will) inside its own tag: not part of the words
    sc['description'] = re.sub(r'<emph altrender="doctype">[^<]*</emph>', ' ', str(sc.get('description') or ''))
    held = [h.get('xReferenceName') for h in d.get('heldBy') or [] if isinstance(h, dict) and h.get('xReferenceName')]
    return {'source': SOURCE, 'id': d['id'], 'name': _plain(d.get('title') or sc.get('description')) or d['id'],
            'role': 'item', 'principal': False, 'given': None, 'surnames': None, 'events': {}, 'relatives': [],
            'url': web_url(d['id']), 'reference': d.get('citableReference') or '', 'held_by': held,
            'dates': d.get('coveringDates') or '', 'year': _year(d.get('coveringFromDate')),
            'year_to': _year(d.get('coveringToDate')), 'description': _plain(sc.get('description')),
            'digitised': bool(d.get('digitised')), 'parent_id': d.get('parentId') or '',
            'legal_status': d.get('legalStatus') or ''}


def archives(doc):
    """[(count, name)] of the archives holding matching entries, from the `repositories` facet (other archives) and
    `heldByReps` (TNA itself)."""
    out = []
    for f in doc.get('heldByReps') or []:
        if isinstance(f, dict) and f.get('code') == 'TNA' and _count(f.get('count')):
            out.append((f['count'], 'The National Archives, Kew'))
    for f in doc.get('repositories') or []:
        if isinstance(f, dict) and _count(f.get('count')) and f.get('code'):
            out.append((f['count'], re.sub(r'^\S+\s+', '', str(f['code']))))     # "201 West Yorkshire ..."
    return out


def add_search_args(sp):
    sp.add_argument('--query', required=True, help='catalogue words: "Will of John Smith", "Halifax parish registers"')
    sp.add_argument('--series', action='append', default=[], help='a record series: "PROB 11" (wills), "WO 97", '
                    '"HO 334"; repeat for more')
    sp.add_argument('--held-by', default='all', choices=tuple(HELD), help='tna (Kew only), other (the local '
                    'archives) or all (default)')
    sp.add_argument('--from', dest='year_from', type=int, help='first year of the record')
    sp.add_argument('--to', dest='year_to', type=int, help='last year of the record')
    sp.add_argument('--count', type=int, default=20, choices=range(1, 101), metavar='1-100')
    sp.add_argument('--page', type=int, default=1)


def search(a, R):
    if not a.query.strip():
        print('gwt-read: tna: --query is empty', file=sys.stderr, flush=True)
        return EXIT_USAGE
    if a.page * a.count > MAX_ROWS:
        print('gwt-read: tna: Discovery serves the first %d rows of a search; narrow with --series, --from/--to or '
              '--held-by' % MAX_ROWS, file=sys.stderr, flush=True)
        return EXIT_USAGE
    url = search_url(a.query, a.series, a.held_by, a.year_from or '', a.year_to or '', a.count, a.page)
    off = (max(1, int(a.page)) - 1) * max(1, min(int(a.count), 100))
    status, rh, body, cached = gwt_reader.get_api(R, url, {'accept': ACCEPT},
                                                  expect=lambda d: verdict(d, off) != 'unknown', refresh=a.refresh)
    doc = json.loads(body)
    cands = [candidate(r) for r in doc['records']]
    env = gwt_reader.envelope(url, None, json_body=doc, verdict=verdict(doc, off), status=status, from_cache=cached)
    words = [SOURCE, a.query, *a.series, a.held_by, a.year_from, a.year_to, a.count, a.page]
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(url, words), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    for n, name in archives(doc):
        print('archive\t%d\t%s' % (n, name), flush=True)
    print('results\t%s total, %d read%s%s' % (doc['count'], len(cands), gwt_reader.past_end(cands, off),
                                            ' (cache)' if cached else ''), flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY


def add_record_args(sp):
    sp.add_argument('id', help='the entry id: C1234, D280526, or the long id of a search row')


def record(a, R):
    rid = a.id.strip().rsplit('/', 1)[-1]
    if re.fullmatch(r'[cd]\d+', rid):
        rid = rid.upper()       # ids are case-sensitive: d682312 answers 204, D682312 the entry
    if not re.fullmatch(r'[A-Za-z0-9-]+', rid):
        print('gwt-read: tna: not a Discovery id: %r' % a.id, file=sys.stderr, flush=True)
        return EXIT_USAGE
    url = details_url(rid)
    status, rh, body, cached = gwt_reader.get_api(R, url, {'accept': ACCEPT}, expect=lambda d: is_details(d, rid),
                                                  refresh=a.refresh)
    doc = json.loads(body)
    cands = [record_candidate(doc)]
    env = gwt_reader.envelope(url, None, json_body=doc, status=status, from_cache=cached)
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(url, [SOURCE, 'record', rid]), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    print('results\t1 record%s' % (' (cache)' if cached else ''), flush=True)
    return gwt_reader.EXIT_OK
