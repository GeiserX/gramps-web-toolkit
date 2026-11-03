#!/usr/bin/env python3
"""gwt_kramerius: Kramerius, the digital library software of Czech libraries, searched inside the text of digitised
newspapers and books, for gwt-read.

Kramerius runs at the Czech National Library (NKP), the Moravian Library (MZK) and about ten other Czech libraries,
each with an open Solr search over the OCR text of every page: newspapers, address books, school reports, printed
parish and civil lists. No key, no login. A search here reads one library:

    mzk   https://api.kramerius.mzk.cz/search/api/client/v7.0/search?q=text_ocr:(...)   (Kramerius 7)
    nkp   https://kramerius5.nkp.cz/search/api/v5.0/search?q=text_ocr:(...)             (Kramerius 5)

Only public pages are asked for: pages under copyright (`dnnt`, `private`) are readable only inside the library or
with a Czech library login. A row is one page that holds the words, never a person, with the words around them, the
page's citation link and its full image URL for `gwt fetch` or any downloader. "None" is Solr's own count,
`numFound` 0, in a well-shaped answer; any other shape is a STOP. Standard library only.
"""
import html, json, re, urllib.parse

import gwt_reader

SOURCE = 'kramerius'
# host, API version, citation (viewer) base. The v5 and v7 APIs name the same fields differently.
LIBRARIES = {
    'mzk': {'host': 'https://api.kramerius.mzk.cz', 'api': 7, 'web': 'https://www.digitalniknihovna.cz/mzk/uuid/'},
    'nkp': {'host': 'https://kramerius5.nkp.cz', 'api': 5, 'web': 'https://www.digitalniknihovna.cz/nkp/uuid/'},
}
FIELDS = {
    7: {'path': '/search/api/client/v7.0/search', 'pid': 'pid', 'title': 'root.title', 'date': 'date.str',
        'page': 'page.number', 'model': 'own_model_path', 'model_fq': 'model:page',
        'public_fq': 'accessibility:public', 'begin': 'date_range_start.year', 'end': 'date_range_end.year',
        'image': '/search/api/client/v7.0/items/%s/image'},
    5: {'path': '/search/api/v5.0/search', 'pid': 'PID', 'title': 'root_title', 'date': 'datum_str',
        'page': 'details', 'model': 'model_path', 'model_fq': 'fedora.model:page', 'public_fq': 'dostupnost:public',
        'begin': 'datum_begin', 'end': 'datum_end',
        'image': '/search/img?pid=%s&stream=IMG_FULL&action=GETRAW'},
}
PACE = {'start': 3.0, 'floor': 2.0, 'ceil': 120.0}      # the libraries publish no limit; Solr answers take seconds
DAY = 86400
SPEC = {
    'id': SOURCE,
    'help': 'Kramerius, Czech digital libraries (MZK, NKP): newspapers and books searched inside their text; open',
    'pace': PACE,
    'cache_rules': ((r'/search/api/(?:client/v7\.0|v5\.0)/search\?', 7 * DAY),),
    # Novák, the most common Czech surname: 141,461 public pages at the MZK on 2026-11-03
    'controls': [{'argv': ['--query', 'Novák'], 'min_count': 10}],
    'result_markers': [], 'negative_markers': [],      # a JSON API: verdict() reads Solr's own count
    'search_help': 'words searched in the text of every public page; one row per page, with its image URL',
}
ACCEPT = 'application/json'


def search_url(query, library='mzk', year_from='', year_to='', count=20, start=0):
    lib = LIBRARIES[library]
    f = FIELDS[lib['api']]
    q = [('q', 'text_ocr:(%s)' % query.strip()), ('fq', f['model_fq']), ('fq', f['public_fq'])]
    if year_to:
        q.append(('fq', '%s:[* TO %s]' % (f['begin'], int(year_to))))
    if year_from:
        q.append(('fq', '%s:[%s TO *]' % (f['end'], int(year_from))))
    q += [('fl', ','.join((f['pid'], f['title'], f['date'], f['page'], f['model'], 'root.pid' if lib['api'] == 7
                           else 'root_pid'))),
          ('hl', 'true'), ('hl.fl', 'text_ocr'), ('hl.snippets', '3'), ('rows', max(1, min(int(count), 100))),
          ('start', max(0, int(start))), ('wt', 'json')]
    return lib['host'] + f['path'] + '?' + urllib.parse.urlencode(q)


def verdict(doc, pid='pid', offset=0):
    """'hits', 'none' or 'unknown'. Only Solr's own numFound says none; an error answer, a missing count or a count
    with no rows is unknown, a STOP, unless the rows asked for start at or past that count: the end."""
    if not isinstance(doc, dict) or not isinstance(doc.get('response'), dict):
        return 'unknown'
    n, docs = doc['response'].get('numFound'), doc['response'].get('docs')
    if not isinstance(n, int) or isinstance(n, bool) or not isinstance(docs, list):
        return 'unknown'
    if n == 0 and not docs:
        return 'none'
    if not docs and offset >= n:
        return 'none'
    if docs and all(isinstance(d, dict) and d.get(pid) for d in docs):
        return 'hits'
    return 'unknown'


def _one(v):
    return (v[0] if v else '') if isinstance(v, list) else (v or '')


def plain(fragment):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', fragment or ''))).strip()


def candidates(doc, library='mzk'):
    lib = LIBRARIES[library]
    f = FIELDS[lib['api']]
    hl = doc.get('highlighting') if isinstance(doc.get('highlighting'), dict) else {}
    out = []
    for d in doc['response']['docs']:
        pid = d[f['pid']]
        # v7 keys the highlights by "<root pid>!<pid>", v5 by the pid
        snip = hl.get(pid) or next((v for k, v in hl.items() if k.endswith('!' + pid)), {})
        page = _one(d.get(f['page'])).split('##')[0].strip()
        date = str(d.get(f['date']) or '')
        y = re.search(r'(?<!\d)(\d{4})(?!\d)', date)
        out.append({'source': SOURCE, 'id': '%s:%s' % (library, pid), 'name': d.get(f['title']) or '', 'role': 'mention',
                    'principal': False, 'given': None, 'surnames': None, 'events': {}, 'relatives': [],
                    'url': lib['web'] + pid, 'date': date, 'year': int(y.group(1)) if y else None, 'page': page,
                    'model': _one(d.get(f['model'])), 'image': lib['host'] + f['image'] % pid,
                    'text': ' / '.join(plain(s) for s in (snip.get('text_ocr') or []) if isinstance(snip, dict))})
    return out


def add_search_args(sp):
    sp.add_argument('--query', required=True, help='Solr words in the page text: Novák, "Jan Novák", Novák AND Brno')
    sp.add_argument('--library', default='mzk', choices=sorted(LIBRARIES), help='which library (default mzk)')
    sp.add_argument('--from', dest='year_from', default='', help='first year the page can be from')
    sp.add_argument('--to', dest='year_to', default='', help='last year')
    sp.add_argument('--count', type=int, default=20, choices=range(1, 101), metavar='1-100')
    sp.add_argument('--start', type=int, default=0, help='row offset')


class _QueryFirst:
    """R as get_api sees it, except that Solr's own refusal of the query (HTTP 500 whose reason says Bad Request) is
    an error in this one search (exit 1), never an outage that pauses every lane."""

    def __init__(self, R):
        self.R, self.lane, self.gate = R, R.lane, R.gate

    def get(self, *args, **kw):
        try:
            return self.R.get(*args, **kw)
        except gwt_reader.Stop as e:
            why = gwt_reader.api_reason(e.body)
            if e.verdict == 'http' and 'bad request' in why.lower():
                raise gwt_reader.Stop('the library could not read the query (%s); check its Solr syntax: quotes, '
                                      'brackets, AND/OR' % why, gwt_reader.EXIT_ERROR, 'query', e.status, e.body) from None
            raise


def search(a, R):
    pid = FIELDS[LIBRARIES[a.library]['api']]['pid']
    off = max(0, int(a.start))
    url = search_url(a.query, a.library, a.year_from, a.year_to, a.count, off)
    status, rh, body, cached = gwt_reader.get_api(_QueryFirst(R), url, {'accept': ACCEPT}, refresh=a.refresh,
                                                  expect=lambda d: verdict(d, pid, off) != 'unknown')
    doc = json.loads(body)
    cands = candidates(doc, a.library)
    env = gwt_reader.envelope(url, None, text='\n'.join(c['text'] for c in cands if c['text']), json_body=doc,
                              verdict=verdict(doc, pid, off), library=a.library, status=status, from_cache=cached)
    words = [SOURCE, a.library, a.query, a.year_from, a.year_to, a.count, off]
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(url, words), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    for c in cands:
        print('page\t%s\t%s' % (c['url'], c['image']), flush=True)
    print('results\t%s total, %d read%s%s' % (doc['response']['numFound'], len(cands), gwt_reader.past_end(cands, off),
                                            ' (cache)' if cached else ''), flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY
