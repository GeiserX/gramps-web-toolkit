#!/usr/bin/env python3
"""gwt_trove: Trove, the National Library of Australia's digitised newspapers and gazettes, searched through its v3
API with your own free API key, for gwt-read.

Trove holds Australian newspapers from 1803 into the 1950s and later, cut into articles with their OCR text: family
notices (births, marriages, deaths), funerals, probate and court news, shipping lists. The API needs a personal key,
free for non-commercial use (apply in your Trove account); the tool never creates one and never logs in.

    https://api.trove.nla.gov.au/v3/result?category=newspaper&q=...&encoding=json   with the header X-API-KEY

The key is read from $GWT_TROVE_KEY and sent only as that header, inside the fetcher, so it never reaches the cache
or the evidence files. Without it the command exits 4 with a message; a 401 (a wrong or revoked key) exits 4 too.
A row is one article, never a person. "None" is the API's own count, `total` 0, in a well-shaped answer; any other
shape is a STOP. Standard library only.
"""
import html, json, os, re, sys, urllib.parse

import gwt_reader

API = 'https://api.trove.nla.gov.au/v3/result'
SOURCE = 'trove'
KEY_ENV = 'GWT_TROVE_KEY'
KEY_HEADER = 'x-api-key'
PACE = {'start': 2.0, 'floor': 1.0, 'ceil': 120.0}    # the API allows about 200 requests a minute per key
DAY = 86400


def build(src, a, urlopen, gate):
    """The source's Reader, with the key added inside the fetcher; exit 4 when there is no key."""
    import gwt_read
    key = os.environ.get(KEY_ENV, '').strip()
    if not key:
        print('gwt-read: trove: no API key: set %s to your own Trove API key (free for non-commercial use, from your '
              'Trove account under API keys); the tool never makes one' % KEY_ENV, file=sys.stderr, flush=True)
        return gwt_reader.EXIT_DEAD
    R = gwt_read._default_build(src, a, urlopen, gate)
    inner = R.fetcher
    R.fetcher = lambda method, url, headers, data: inner(method, url, dict(headers or {}, **{KEY_HEADER: key}), data)
    return R


SPEC = {
    'id': SOURCE,
    'help': 'Trove, Australian newspapers searched inside their text; your own free API key in $%s' % KEY_ENV,
    'pace': PACE,
    'cache_rules': ((r'/v3/result\?', 7 * DAY),),
    # Smith, the most common surname in Australia: millions of newspaper articles
    'controls': [{'argv': ['--query', 'Smith'], 'min_count': 10}],
    'result_markers': [], 'negative_markers': [],      # a JSON API: verdict() reads the answer's own count
    'build': build,
    'search_help': 'a newspaper and gazette article search; one row per article, with its text',
}
ACCEPT = 'application/json'


def search_url(query, year_from='', year_to='', category='', state='', count=20, start='*', text=True):
    q = query.strip()
    if year_from or year_to:      # brackets, so an OR in the query never escapes the date range
        q = '(%s) date:[%s-01-01T00:00:00Z TO %s-12-31T23:59:59Z]' % (q, year_from or '1800', year_to or '2030')
    params = [('category', 'newspaper'), ('q', q), ('encoding', 'json'), ('n', max(1, min(int(count), 100))),
              ('s', start or '*'), ('sortby', 'relevance')]
    if text:
        params += [('reclevel', 'full'), ('include', 'articletext')]
    for k, v in (('l-category', category), ('l-state', state)):
        if v:
            params.append((k, v))
    return API + '?' + urllib.parse.urlencode(params)


def _number(x):
    return isinstance(x, int) and not isinstance(x, bool)


def records(doc):
    """The newspaper category's `records`, or None when the answer has no such part."""
    if not isinstance(doc, dict) or not isinstance(doc.get('category'), list):
        return None
    for c in doc['category']:
        if isinstance(c, dict) and c.get('code') == 'newspaper' and isinstance(c.get('records'), dict):
            return c['records']
    return None


def verdict(doc, start='*'):
    """'hits', 'none' or 'unknown'. Only the API's own `total` says none; a missing category, a missing or
    non-numeric total, or a total with no articles is unknown, a STOP, unless this is a later page (a cursor): then no
    article is the end of the results."""
    rec = records(doc)
    if rec is None or not _number(rec.get('total')):
        return 'unknown'
    arts = rec.get('article', [])
    if not isinstance(arts, list):
        return 'unknown'
    if rec['total'] == 0 and not arts:
        return 'none'
    if not arts and start not in ('', '*'):
        return 'none'         # a cursor past the last article: the end
    if arts and all(isinstance(x, dict) and x.get('id') for x in arts):
        return 'hits'
    return 'unknown'


def plain(fragment):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', fragment or ''))).strip()


def candidates(doc):
    out = []
    for x in records(doc).get('article') or []:
        title = x.get('title') if isinstance(x.get('title'), dict) else {}
        date = str(x.get('date') or '')
        y = re.match(r'(\d{4})', date)
        out.append({'source': SOURCE, 'id': str(x['id']), 'name': title.get('title') or '', 'role': 'mention',
                    'principal': False, 'given': None, 'surnames': None, 'events': {}, 'relatives': [],
                    'url': x.get('troveUrl') or 'https://nla.gov.au/nla.news-article%s' % x['id'], 'date': date,
                    'year': int(y.group(1)) if y else None, 'title': plain(x.get('heading')),
                    'type': x.get('category') or '', 'page': str(x.get('page') or ''),
                    'text': plain(x.get('articleText')) or plain(x.get('snippet'))})
    return out


def add_search_args(sp):
    sp.add_argument('--query', required=True, help='Trove query: words, "a phrase", AND/OR: \'"Ann Smith" AND funeral\'')
    sp.add_argument('--from', dest='year_from', type=int, default=None, help='first publication year')
    sp.add_argument('--to', dest='year_to', type=int, default=None, help='last publication year')
    sp.add_argument('--category', default='', help='an article category, as Trove names it: "Family Notices" ...')
    sp.add_argument('--state', default='', help='a state of publication: "New South Wales", "Victoria" ...')
    sp.add_argument('--count', type=int, default=20, choices=range(1, 101), metavar='1-100')
    sp.add_argument('--start', default='*', help='the `next` cursor a previous search printed')
    sp.add_argument('--no-text', dest='text', action='store_false', help='leave the article text out (only snippets)')


def search(a, R):
    url = search_url(a.query, a.year_from or '', a.year_to or '', a.category, a.state, a.count, a.start, a.text)
    try:
        status, rh, body, cached = gwt_reader.get_api(R, url, {'accept': ACCEPT}, refresh=a.refresh,
                                                      expect=lambda d: verdict(d, a.start) != 'unknown')
    except gwt_reader.Stop as e:
        if e.verdict != 'dead':
            raise
        raise gwt_reader.Stop('Trove refused the API key in $%s (HTTP 401): check or renew it, then end the pause with '
                              '`gwt gate clear --dir ~/.cache/gwt-gate-trove "new key"`; never retry in a loop'
                              % KEY_ENV, e.exit_code, e.verdict, e.status, e.body) from None
    doc = json.loads(body)
    rec = records(doc)
    cands = candidates(doc)
    env = gwt_reader.envelope(url, None, text='\n\n'.join(c['text'] for c in cands if c['text']), json_body=doc,
                              verdict=verdict(doc, a.start), total=rec['total'], status=status, from_cache=cached)
    words = [SOURCE, a.query, a.year_from or '', a.year_to or '', a.category, a.state, a.count, a.start, a.text]
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(url, words), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    if cands and rec.get('nextStart'):
        print('next\t%s' % rec['nextStart'], flush=True)
    print('results\t%s total, %d read%s' % (rec['total'], len(cands), ' (cache)' if cached else ''), flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY
