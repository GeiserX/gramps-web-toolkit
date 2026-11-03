#!/usr/bin/env python3
"""gwt_delpher: Delpher, the Dutch national library's (KB) digitised newspapers, searched inside their text through
the KB's open SRU API, for gwt-read.

Delpher holds Dutch newspapers from 1618 to 1995, cut into articles with their OCR text: family announcements
(births, marriages, deaths), notices and news. The KB's SRU API is open, no key and no login:

    search  https://jsru.kb.nl/sru/sru?operation=searchRetrieve&x-collection=DDD_artikel&recordSchema=ddd&query=...
    text    https://resolver.kb.nl/resolve?urn=<article>:ocr   the article's OCR text, the URL the search returns

This reader never touches the delpher.nl website, whose robots.txt closes it to robots: only the API and the
resolver URLs the API itself returns. The query is CQL: words, "a phrase" and `and`/`or`; --from/--to add the
API's own date filter. A row is one article, never a person. "None" is the API's own count, numberOfRecords 0, in a
well-shaped answer; an SRU diagnostic (the API's error, which comes with HTTP 200) or any other shape is a STOP.
Standard library only.
"""
import re, sys, urllib.parse
import xml.etree.ElementTree as ET

import gwt_reader

API = 'https://jsru.kb.nl/sru/sru'
RESOLVER = 'https://resolver.kb.nl/resolve?urn='
SOURCE = 'delpher'
COLLECTION = 'DDD_artikel'
PACE = {'start': 3.0, 'floor': 2.0, 'ceil': 120.0}      # the KB publishes no limit: one request every 3 s
DAY = 86400
MAX_TEXT = 20
SPEC = {
    'id': SOURCE,
    'help': 'Delpher, Dutch newspapers 1618-1995 searched inside their text; open KB API, no account',
    'pace': PACE,
    'cache_rules': ((r'jsru\.kb\.nl/sru/sru\?', 7 * DAY), (r'resolver\.kb\.nl/resolve\?urn=.*:ocr$', 365 * DAY)),
    # Jansen, the most common Dutch surname: 2,162,854 articles on 2026-11-03
    'controls': [{'argv': ['--query', 'Jansen', '--text', '0'], 'min_count': 10}],
    'result_markers': [], 'negative_markers': [],      # an XML API: verdict() reads the answer's own count
    'search_help': 'a CQL query over the articles\' text; one row per article, with its OCR text',
}
ACCEPT = 'application/xml'
EXIT_USAGE = 64
NS = {'srw': 'http://www.loc.gov/zing/srw/', 'dc': 'http://purl.org/dc/elements/1.1/', 'ddd': 'http://www.kb.nl/ddd'}


def search_url(query, year_from='', year_to='', count=20, start=1):
    cql = query.strip()
    if year_from or year_to:
        # the brackets keep the filter on the whole query: the KB parser reads '"a b" and date within ...' as 0 rows
        cql = '(%s) and date within "01-01-%s 31-12-%s"' % (cql, year_from or '1600', year_to or '2000')
    return API + '?' + urllib.parse.urlencode([
        ('version', '1.2'), ('operation', 'searchRetrieve'), ('x-collection', COLLECTION), ('recordSchema', 'ddd'),
        ('maximumRecords', max(1, min(int(count), 100))), ('startRecord', max(1, int(start))), ('query', cql)])


def parse(page):
    """The answer as an XML tree, or None when it is not XML."""
    try:
        return ET.fromstring(page.encode('utf-8') if isinstance(page, str) else page)
    except ET.ParseError:
        return None


def _records(root):
    return root.findall('srw:records/srw:record/srw:recordData', NS)


def verdict(root, start=1):
    """'hits', 'none' or 'unknown'. Only the API's own count says none; a diagnostic, a missing or non-numeric count,
    or a count with no rows is unknown, a STOP, unless the rows asked for start past that count: the end."""
    if root is None or root.tag != '{%s}searchRetrieveResponse' % NS['srw']:
        return 'unknown'
    if root.find('srw:diagnostics', NS) is not None:
        return 'unknown'      # the SRU error answer: a bad query or a failing index, never "none"
    n = (root.findtext('srw:numberOfRecords', None, NS) or '').strip()
    if not n.isdigit():
        return 'unknown'
    recs = _records(root)
    if int(n) == 0 and not recs:
        return 'none'
    if not recs and start > int(n):
        return 'none'         # paged past the last row: the end of the results
    if recs and all(r.findtext('ddd:metadataKey', '', NS) for r in recs):
        return 'hits'
    return 'unknown'


def diagnostic(body):
    """The text of an SRU diagnostic in an answer ("General system error: ..."), or ''."""
    root = parse(body or b'')
    if root is None:
        return ''
    out = []
    for d in root.iter('{http://www.loc.gov/zing/srw/diagnostic/}diagnostic'):
        words = [(d.findtext('{http://www.loc.gov/zing/srw/diagnostic/}%s' % k) or '').strip() for k in ('message', 'details')]
        out.append(': '.join(w for w in words if w))
    return '; '.join(w for w in out if w)[:300]


def query_problem(query):
    """Why a CQL query cannot be sent (an odd number of double quotes, unbalanced brackets), or ''. The API answers
    such a query with a diagnostic that looks the same as a failing index, so it is caught here, before any request."""
    if query.count('"') % 2:
        return 'an odd number of double quotes'
    depth = 0
    for ch in re.sub(r'"[^"]*"', '', query):
        depth += {'(': 1, ')': -1}.get(ch, 0)
        if depth < 0:
            break
    return 'unbalanced brackets' if depth else ''


def is_ocr(page):
    """An OCR answer of the resolver: an XML <text> document."""
    root = parse(page)
    return root is not None and root.tag == 'text'


def ocr_text(page):
    root = parse(page)
    return re.sub(r'\s+', ' ', ' '.join(root.itertext())).strip() if root is not None else ''


def _https(url):
    return re.sub(r'^http://', 'https://', url or '')


def candidates(root):
    out = []
    for r in _records(root):
        key = r.findtext('ddd:metadataKey', '', NS)
        m = re.match(r'(\d{4})/(\d\d)/(\d\d)', r.findtext('dc:date', '', NS) or '')
        date = '%s-%s-%s' % m.groups() if m else ''
        ocr = next((_https(i.text) for i in r.findall('dc:identifier', NS) if (i.text or '').endswith(':ocr')), '')
        out.append({'source': SOURCE, 'id': key, 'name': r.findtext('ddd:papertitle', '', NS) or '', 'role': 'mention',
                    'principal': False, 'given': None, 'surnames': None, 'events': {}, 'relatives': [],
                    'url': RESOLVER + key, 'date': date, 'year': int(date[:4]) if date else None,
                    'title': r.findtext('dc:title', '', NS) or '', 'type': r.findtext('dc:type', '', NS) or '',
                    'page': r.findtext('ddd:page', '', NS) or '', 'place': r.findtext('ddd:spatialCreation', '', NS) or '',
                    'ocr': ocr, 'text': ''})
    return out


def add_search_args(sp):
    sp.add_argument('--query', required=True, help='CQL: words, "a phrase", and/or: \'"Jan Jansen" and overleden\'')
    sp.add_argument('--from', dest='year_from', type=int, default=None, help='first publication year')
    sp.add_argument('--to', dest='year_to', type=int, default=None, help='last publication year')
    sp.add_argument('--count', type=int, default=20, choices=range(1, 101), metavar='1-100')
    sp.add_argument('--start', type=int, default=1, help='first row, from 1')
    sp.add_argument('--text', type=int, default=5, help='read the OCR text of the first N articles (0 to %d, '
                    'default 5), one request each' % MAX_TEXT)


def search(a, R):
    why = query_problem(a.query)
    if why:
        print('gwt-read: delpher: the query has %s; fix it before searching: %s' % (why, a.query), file=sys.stderr,
              flush=True)
        return EXIT_USAGE
    start = max(1, int(a.start))
    url = search_url(a.query, a.year_from or '', a.year_to or '', a.count, start)
    try:
        status, rh, body, cached = gwt_reader.get_api(R, url, {'accept': ACCEPT}, refresh=a.refresh,
                                                      page=lambda p: verdict(parse(p), start) != 'unknown')
    except gwt_reader.Stop as e:
        diag = diagnostic(e.body)
        if diag:
            raise gwt_reader.Stop('%s; the API said: %s' % (e, diag), e.exit_code, e.verdict, e.status, e.body) from None
        raise
    root = parse(body)
    total = int(root.findtext('srw:numberOfRecords', '0', NS).strip())
    cands = candidates(root)
    for c in cands[:max(0, min(int(a.text), MAX_TEXT))]:
        if c['ocr']:
            try:
                _, _, ob, _ = gwt_reader.get_api(R, c['ocr'], {'accept': ACCEPT}, refresh=a.refresh, page=is_ocr)
            except gwt_reader.Stop as e:
                if e.verdict != 'http' or e.status not in (404, 410):
                    raise
                c['ocr_status'] = e.status      # this one article has no OCR text; the other rows stand
                continue
            c['text'] = ocr_text(ob)
    env = gwt_reader.envelope(url, None, text='\n\n'.join(c['text'] for c in cands if c['text']),
                              verdict=verdict(root, start), total=total, status=status, from_cache=cached)
    words = [SOURCE, a.query, a.year_from or '', a.year_to or '', a.count, start]
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(url, words), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    print('results\t%d total, %d read, %d with text%s%s' % (
        total, len(cands), sum(1 for c in cands if c['text']), gwt_reader.past_end(cands, start - 1),
        ' (cache)' if cached else ''), flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY
