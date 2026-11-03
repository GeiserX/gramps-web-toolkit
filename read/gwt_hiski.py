#!/usr/bin/env python3
"""gwt_hiski: HisKi (hiski.genealogia.fi), the Finnish Genealogical Society's index of parish registers, for gwt-read.

HisKi indexes baptisms, marriages, burials and moves parish by parish, from the 1600s to about 1900. It has no API:
its search is an old CGI form that also answers a plain GET, so this reader sends the form's own fields as a query
string, in English (kieli=en), one parish and one book at a time.

    search   /hiski?komento=haku&srk=<parish>&kirja=<book>&...   one page of up to --count events

A row is an index entry, a lead, not the record: read the parish register (the scans on the Finnish archives' sites)
before citing it. A candidate's `url` is the entry's own page, /hiski?en+<parish>+<book>+<n>.

"none" is the site's own sentence, "No events found with this search criteria". Any other page with no row (a
parish list, because the parish code is wrong; a block page; a new layout) is a STOP. Standard library only.
"""
import html, re, urllib.parse

import gwt_reader

HOST = 'https://hiski.genealogia.fi'
SOURCE = 'hiski'
# An old CGI program on a volunteer society's server; robots.txt names no crawl delay, so one search every 10 s at
# most, and never faster than one every 5 s.
PACE = {'start': 10.0, 'floor': 5.0, 'ceil': 300.0}
DAY = 86400
SPEC = {
    'id': SOURCE,
    'help': 'HisKi, the Finnish parish register index (baptisms, marriages, burials, moves); public',
    'pace': PACE,
    # the index grows slowly, parish by parish: a search is kept 30 days
    'cache_rules': ((r'/hiski\?', 30 * DAY),),
    'controls': [{'argv': ['--parish', '0035', '--book', 'marriages', '--given', 'Johan'], 'min_count': 15}],
    # a row starts with the link to its entry: /hiski?en+0035+vihityt+12
    'result_markers': [r'<a href="/hiski\?[a-z]+\+\d{4}\+[a-z]+\+\d+"'],
    'negative_markers': [r'No events found with this search criteria'],
}
# The book names the site uses, and the English words a user types.
BOOKS = {'baptisms': 'kastetut', 'marriages': 'vihityt', 'burials': 'haudatut', 'moves': 'muuttaneet'}
COUNTS = (15, 30, 50, 100, 250, 500, 1000)
# The search form's own fields for the first person of a book (the man of a marriage, the father of a baptism).
FIELDS = {'given': 'ietunimi', 'patronymic': 'ipatronyymi', 'surname': 'isukunimi', 'village': 'ikyla'}
# Column headers that hold a person, the first one found is the row's principal.
PERSON_COLUMNS = ('Man', 'Child', 'Name', 'Buried', 'Deceased', 'Person', 'Father', 'Wife', 'Mother')

_ROW = re.compile(r'<TR\b[^>]*>\s*(<TD\b[^>]*>\s*<a href="/hiski\?[a-z]+\+(\d{4})\+([a-z]+)\+(\d+)".*?)(?=<TR\b|</TABLE>)',
                  re.S | re.I)
_HEAD = re.compile(r'<TR\b[^>]*>\s*(<TH\b.*?)(?=<TR\b|</TABLE>)', re.S | re.I)
_CELL = re.compile(r'<TD\b[^>]*>', re.I)
_TOTAL = re.compile(r'(?:About\s+)?(\d+)\s+events?\s+found', re.I)
_DATE = re.compile(r'\b\d{1,2}\.\d{1,2}\.\d{3,4}\b')


def text(fragment):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', fragment or ''))).strip()


def search_url(parish, book, fields, count=50, start=0):
    """The GET form of the site's search. `fields` are the form's own names (ietunimi, isukunimi, ...)."""
    q = [('komento', 'haku'), ('srk', parish), ('kirja', book), ('kieli', 'en'), ('maxkpl', str(count))]
    q += [(k, v) for k, v in fields if v]
    if start:
        q.append(('hakupos', str(start)))
    return HOST + '/hiski?' + urllib.parse.urlencode(q, encoding='iso-8859-1', errors='replace')


def decode(body):
    """HisKi writes ISO-8859-1, and says so in its meta tag."""
    return body.decode('iso-8859-1') if b'iso-8859-1' in body[:2048].lower() else body.decode('utf-8', 'replace')


def headers_of(page):
    m = _HEAD.search(page)
    return [text(h) for h in re.split(r'<TH\b[^>]*>', m.group(1), flags=re.I)[1:]] if m else []


def candidates(page):
    heads = headers_of(page)
    out = []
    for row, parish, book, n in _ROW.findall(page):
        cells = [text(c) for c in _CELL.split(row)[1:]]
        cols = [[heads[i] if i < len(heads) else '', c] for i, c in enumerate(cells)]
        people = [(h, c) for h, c in cols if h in PERSON_COLUMNS and c]
        date = next((m.group(0) for _, c in cols for m in [_DATE.search(c)] if m), None)
        out.append({'source': SOURCE, 'id': '%s-%s-%s' % (parish, book, n),
                    'name': people[0][1] if people else '', 'role': people[0][0] if people else '', 'principal': True,
                    'given': None, 'surnames': None, 'events': {book: {'date': date}} if date else {},
                    'relatives': [{'role': h, 'name': c} for h, c in people[1:]], 'columns': cols,
                    'parish': parish, 'book': book, 'url': '%s/hiski?en+%s+%s+%s' % (HOST, parish, book, n)})
    return out


def total_of(page):
    m = _TOTAL.search(text(page))
    return int(m.group(1)) if m else None


def _parish(v):
    if not re.fullmatch(r'\d{4}', v or ''):
        raise ValueError(v)
    return v


_parish.__name__ = 'parish code'   # what argparse names in its error


def add_search_args(sp):
    sp.add_argument('--parish', required=True, type=_parish,
                    help='the parish\'s four-digit code, as in its HisKi address (/hiski?en+0035 is 0035)')
    sp.add_argument('--book', required=True, choices=sorted(BOOKS) + sorted(BOOKS.values()),
                    help='which book: baptisms, marriages, burials or moves')
    sp.add_argument('--given', default='', help='first name of the first person (the man of a marriage, the father '
                    'of a baptism)')
    sp.add_argument('--surname', default='', help='last name of the first person')
    sp.add_argument('--patronymic', default='')
    sp.add_argument('--village', default='')
    sp.add_argument('--field', action='append', default=[], metavar='NAME=VALUE',
                    help='any other field of the site\'s form by its own name, e.g. aetunimi=Maria (the wife or '
                    'the mother), etunimi=Anna (the child); repeat for more')
    sp.add_argument('--from', dest='year_from', default='')
    sp.add_argument('--to', dest='year_to', default='')
    sp.add_argument('--count', type=int, default=50, choices=COUNTS)
    sp.add_argument('--start', type=int, default=0, help='the site\'s position to continue from (its hakupos)')


def search(a, R):
    book = BOOKS.get(a.book, a.book)
    fields = [(FIELDS[k], getattr(a, k)) for k in ('given', 'patronymic', 'surname', 'village')]
    fields += [tuple(f.split('=', 1)) for f in a.field if '=' in f]
    fields += [('alkuvuosi', a.year_from), ('loppuvuosi', a.year_to)]
    url = search_url(a.parish, book, fields, a.count, a.start)
    status, rh, body, cached = gwt_reader.get_api(R, url, {'accept': 'text/html'}, refresh=a.refresh,
                                                  page=gwt_reader.rows_check(SPEC, SPEC['result_markers'][0], candidates))
    page = decode(body)
    cands = candidates(page)
    env = gwt_reader.envelope(url, None, text=text(page), verdict=gwt_reader.classify(page, SPEC), from_cache=cached)
    words = [SOURCE, a.parish, book] + [v for _, v in fields]
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(url, words), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    total = total_of(page) if cands else 0     # the page check made a page with rows and none read a STOP
    print('results\t%s total, %d read%s' % ('?' if total is None else total, len(cands), ' (cache)' if cached else ''),
          flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY
