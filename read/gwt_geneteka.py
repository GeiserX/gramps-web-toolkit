#!/usr/bin/env python3
"""gwt_geneteka: Geneteka (geneteka.genealodzy.pl), the Polish Genealogical Society's index of parish and civil
registers, for gwt-read.

Geneteka indexes births, marriages and deaths from Polish parishes and civil registers (and some in Ukraine,
Belarus and Lithuania), region by region. Its search page is a shell: the table fills from a JSON endpoint,
api/getAct.php, with the page's own query fields. This reader asks that endpoint directly, once per search.

    search   api/getAct.php?op=gt&lang=eng&w=<region>&bdm=<B|S|D>&rid=<parish or B|S|D>&search_lastname=...

Geneteka's robots.txt asks for Crawl-delay: 120. That is one request every two minutes, so this reader is for a
person's own searches, a handful at a time. Bulk use (looping over surnames or regions) is what that delay forbids:
never do it. Answers are kept 30 days so a repeated search costs nothing.

A row is an index entry, a lead: open the scan before citing it. `link` is the first link the row carries: a scan on
metryki.genealodzy.pl, or the site of the archive or diocese that holds the book. Geneteka has no page per entry, so
a candidate's `url` is the search page that shows it, and `id` is the entry's own number (the gid of its "report a
bug" link).

"none" is the answer's own count: recordsFiltered 0 with no row. Any other answer is a STOP. Standard library only.
"""
import html, json, re, sys, urllib.parse

import gwt_reader

HOST = 'https://geneteka.genealodzy.pl'
SOURCE = 'geneteka'
# robots.txt: Crawl-delay: 120. Never faster, whatever a lane asks for.
PACE = {'start': 120.0, 'floor': 120.0, 'ceil': 900.0}
DAY = 86400
EXIT_USAGE = 64
SPEC = {
    'id': SOURCE,
    'help': 'Geneteka, the Polish index of parish and civil births, marriages and deaths; one search every 2 minutes',
    'pace': PACE,
    'cache_rules': ((r'/api/getAct\.php\?', 30 * DAY),),
    'controls': [{'argv': ['--region', '07mz', '--surname', 'Kowalski'], 'min_count': 50}],
    'result_markers': [], 'negative_markers': [],     # a JSON answer: "none" is its own count
}
KINDS = {'births': 'B', 'marriages': 'S', 'deaths': 'D'}
# The site's regions (its w= codes): Polish voivodeships, Warsaw, and the neighbouring countries.
REGIONS = ('01ds', '02kp', '03lb', '04ls', '05ld', '06mp', '07mz', '71wa', '08op', '09pk', '10pl', '11pm', '12sl',
           '13sk', '14wm', '15wp', '16zp', '21uk', '22br', '23lt', '25po')
# The births table's columns, as its header names them. The marriages and deaths tables have their own; their rows
# keep their cells in order (`cells`) and are not split into names here.
BIRTH_COLUMNS = ('year', 'record', 'given', 'surname', 'father', 'mother', 'mother_surname', 'parish', 'place')
_GID = re.compile(r'fix\.php\?gid=(\d+)')
_HREF = re.compile(r'href="([^"]+)"')
_TITLE = re.compile(r'title="(Remarks:[^"]*)"')


def text(fragment):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', str(fragment or '')))).strip()


def query(region, kind, surname, given='', surname2='', given2='', year_from='', year_to='', parish='', exact=False):
    """The page's own query fields, in the page's order."""
    bdm = KINDS[kind]
    q = [('op', 'gt'), ('lang', 'eng'), ('bdm', bdm), ('w', region), ('rid', parish or bdm),
         ('search_lastname', surname), ('search_name', given), ('search_lastname2', surname2),
         ('search_name2', given2), ('from_date', year_from), ('to_date', year_to)]
    if exact:
        q.append(('exac', '1'))
    return q


def search_url(q, count=50, start=0):
    dt = [('draw', '1'), ('start', str(start)), ('length', str(count)), ('order[0][column]', '0'),
          ('order[0][dir]', 'asc'), ('search[value]', '')]
    return HOST + '/api/getAct.php?' + urllib.parse.urlencode(dt + q)


def page_url(q):
    return HOST + '/index.php?' + urllib.parse.urlencode(q)


def verdict(doc, start=0, count=None):
    """'hits', 'none' or 'unknown' for {recordsFiltered: N, data: [[cells], ...]}. With `count`, a page with fewer
    rows than it should hold is 'unknown': the site cuts its answer to a few rows for a request that does not look
    like its own page's, and that must never read as the whole page."""
    if not isinstance(doc, dict) or not isinstance(doc.get('data'), list):
        return 'unknown'    # the result container renamed: a STOP
    n = doc.get('recordsFiltered')
    if not isinstance(n, int) or isinstance(n, bool):
        return 'unknown'
    rows = doc['data']
    if not rows and (n == 0 or start >= n):
        return 'none'
    if count and rows and len(rows) < min(count, n - start):
        return 'unknown'    # a cut answer
    if rows and all(isinstance(r, list) and len(r) >= 10 for r in rows):
        return 'hits'
    return 'unknown'


def candidate(row, kind, url):
    cells = [text(c) for c in row[:-1]]
    remarks = str(row[-1] or '')
    gid = _GID.search(remarks)
    links = [html.unescape(h) for h in _HREF.findall(remarks)]
    link = next((h for h in links if 'fix.php' not in h and 'user.php' not in h), None)
    c = {'source': SOURCE, 'id': '%s-%s' % (KINDS[kind], gid.group(1)) if gid else None, 'kind': kind,
         'name': '', 'role': 'principal', 'principal': True, 'given': None, 'surnames': None, 'events': {},
         'relatives': [], 'cells': cells, 'remarks': '; '.join(text(t) for t in _TITLE.findall(remarks)) or None,
         'link': link, 'url': url}
    if kind == 'births':
        b = dict(zip(BIRTH_COLUMNS, cells))
        c.update(name=' '.join(x for x in (b.get('given'), b.get('surname')) if x), given=b.get('given') or None,
                 surnames=[b['surname']] if b.get('surname') else None, act=b.get('record') or None,
                 events={'birth': {'year': b.get('year') or None, 'place': b.get('place') or None,
                                   'parish': b.get('parish') or None}},
                 relatives=[r for r in ({'role': 'father', 'name': b.get('father', '')},
                                        {'role': 'mother', 'name': ' '.join(x for x in (b.get('mother'),
                                                                                       b.get('mother_surname')) if x)})
                            if r['name']])
    else:
        c.update(name=' '.join(cells[2:4]), events={kind[:-1]: {'year': cells[0] or None}})
    return c


def add_search_args(sp):
    sp.add_argument('--region', required=True, choices=REGIONS, help='the site\'s region code (07mz mazowieckie, '
                    '06mp małopolskie, 71wa Warszawa, ...)')
    sp.add_argument('--kind', default='births', choices=sorted(KINDS), help='default births')
    sp.add_argument('--surname', required=True, help='the site needs a surname for a search over a whole region')
    sp.add_argument('--given', default='')
    sp.add_argument('--surname2', default='', help='a second person (a spouse, or a parent with --parents on the site)')
    sp.add_argument('--given2', default='')
    sp.add_argument('--from', dest='year_from', default='')
    sp.add_argument('--to', dest='year_to', default='')
    sp.add_argument('--parish', default='', help='the site\'s numeric parish id (its rid); default every parish')
    sp.add_argument('--exact', action='store_true', help='exact spelling only')
    sp.add_argument('--count', type=int, default=50, choices=(10, 25, 50))
    sp.add_argument('--page', type=int, default=1)


def search(a, R):
    if a.parish and not a.parish.isdigit():
        print('gwt-read: geneteka: --parish is the site\'s numeric parish id', file=sys.stderr, flush=True)
        return EXIT_USAGE
    start = (max(1, a.page) - 1) * a.count
    q = query(a.region, a.kind, a.surname, a.given, a.surname2, a.given2, a.year_from, a.year_to, a.parish, a.exact)
    url = search_url(q, a.count, start)
    shown = page_url(q)
    # what the page's own table sends: without them the site answers only the first few rows
    headers = {'accept': 'application/json, text/javascript, */*; q=0.01', 'x-requested-with': 'XMLHttpRequest',
               'referer': shown}
    status, rh, body, cached = gwt_reader.get_api(R, url, headers, expect=lambda d: verdict(d, start, a.count) != 'unknown',
                                                  refresh=a.refresh)
    doc = json.loads(body)
    cands = [candidate(r, a.kind, shown) for r in doc['data']]
    env = gwt_reader.envelope(url, None, json_body=doc, page_url=shown, verdict=verdict(doc, start, a.count), status=status,
                              from_cache=cached)
    words = [SOURCE, a.region, a.kind, a.surname, a.given, a.surname2, a.given2, a.year_from, a.year_to, a.parish,
             a.page if a.page > 1 else '']
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(url, words), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    print('results\t%s total, %d read%s%s' % (doc['recordsFiltered'], len(cands), gwt_reader.past_end(cands, start),
                                             ' (cache)' if cached else ''), flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY
