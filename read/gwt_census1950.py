#!/usr/bin/env python3
"""gwt_census1950: the 1950 United States census name index (NARA), for gwt-read.

The National Archives (NARA) released the 1950 census in 2022 with a name index made by handwriting recognition,
for every state, Puerto Rico and the other territories. The search behind its viewer answers JSON, no key and no
login: https://1950census.archives.gov/api/search?name=...&state=PR&county=... . Each result is one schedule page
(an enumeration district's sheet) with the names on it that match.

    search   --name "garcia" --state PR [--county "San Juan"]   one row per matching name on a page

A row is one line of a schedule: the name as the index reads it, the line (`row`), the page's schedule id,
enumeration district (`ed`), county and state, and the page image twice: `image`, the scan in NARA's public bucket
on AWS (https://nara-1950-census.s3.amazonaws.com/<key>, anonymous, what `gwt fetch` saves), and `iiif_info`, the
page's IIIF Image API base for `gwt fetch iiif --image-base`. The machine index is noisy (misread letters, given
names split from surnames), so a row read from it carries `machine: true`; a row from the volunteers' corrected
transcription, when the page has one, carries `machine: false`. "none" is the API's own count, `total` 0.
Standard library only.
"""
import json, re, sys, urllib.parse

import gwt_reader

API = 'https://1950census.archives.gov/api/search'
S3 = 'https://nara-1950-census.s3.amazonaws.com/'
SOURCE = 'census1950'
PACE = {'start': 3.0, 'floor': 2.0, 'ceil': 120.0}    # robots.txt asks for 1 s between requests
DAY = 86400
SPEC = {
    'id': SOURCE,
    'help': 'the 1950 US census name index (NARA), every state and Puerto Rico, with the page scans; open API',
    'pace': PACE,
    # the index is fixed since the release, but volunteers still correct it: a search is kept 30 days
    'cache_rules': ((r'1950census\.archives\.gov/api/search\?', 30 * DAY),),
    # Garcia in Puerto Rico: thousands of pages
    'controls': [{'argv': ['--name', 'Garcia', '--state', 'PR'], 'min_count': 10}],
    'result_markers': [], 'negative_markers': [],      # a JSON API: verdict() reads the answer's own count
    'search_help': 'a name search in one state; each row is one matching line of a schedule page',
}
ACCEPT = 'application/json'
EXIT_USAGE = 64
PAGE_SIZE = 25          # the API's own page size; it takes no size parameter


def search_url(name, state, county='', page=1):
    p = [('name', name), ('state', state.upper())]
    if county:
        p.append(('county', county))
    p.append(('page', max(1, int(page))))
    return API + '?' + urllib.parse.urlencode(p)


def _count(n):
    return isinstance(n, int) and not isinstance(n, bool)


def verdict(doc, offset=0):
    """'hits', 'none' or 'unknown': {total: N, results: [...]}. A page that starts at or past the total (`offset`, the
    pages of results before it) is the end of the results."""
    if not isinstance(doc, dict) or not _count(doc.get('total')) or not isinstance(doc.get('results'), list):
        return 'unknown'    # the result container renamed: a STOP
    n, res = doc['total'], doc['results']
    if n == 0 and not res:
        return 'none'
    # paged past the last result: the end of the results, not a changed answer
    if not res and offset >= n:
        return 'none'
    if res and all(isinstance(r, dict) and r.get('scheduleId') and isinstance(r.get('image'), str) and _lines_ok(r)
                   for r in res):
        return 'hits'
    return 'unknown'


def _lines_ok(r):
    """A page has the machine's lines (`names`), the volunteers' (`transcribe`) or both: each a list or null (a page
    only the volunteers have read has `names` null), and at least one a list."""
    n, t = r.get('names'), r.get('transcribe')
    return all(x is None or isinstance(x, list) for x in (n, t)) and (isinstance(n, list) or isinstance(t, list))


def image_url(key):
    """The page scan in NARA's public bucket: the API's image key is the bucket key, '1950census/<roll>/.../x.jpg'."""
    return S3 + urllib.parse.quote(key.lstrip('/'))


def _sheet(key):
    m = re.search(r'-(\d{4})\.jpg$', key or '')
    return int(m.group(1)) if m else None


def _rows(lines, wanted):
    """{name: [row, ...]} of the lines whose name is one the API marked as matching."""
    out = {}
    for x in lines or []:
        if isinstance(x, dict) and x.get('name') in wanted:
            out.setdefault(x['name'], []).append(x.get('row') if _count(x.get('row')) else None)
    return out


def candidates(r):
    """One row per matching line of the page. The volunteers' line wins over the machine's for the same row."""
    hl = r.get('highlight') if isinstance(r.get('highlight'), dict) else {}
    place = ', '.join(x for x in (r.get('county'), r.get('state')) if x)
    page = {'schedule_id': str(r['scheduleId']), 'state': r.get('state') or '', 'state_abbr': r.get('abbr') or '',
            'county': r.get('county') or '', 'ed': r.get('ed') or '', 'sheet': _sheet(r['image']),
            'image_key': r['image'], 'image': image_url(r['image']), 'iiif_info': r.get('iiifUrl') or ''}
    rows, read = [], set()
    for machine, lines, field in ((False, r.get('transcribe'), 'transcribe.name'), (True, r.get('names'), 'names.name')):
        for name, nums in _rows(lines, set(hl.get(field) or ())).items():
            for row in nums:
                if machine and row is not None and row in read:
                    continue        # the volunteers already read this line
                if (machine, name, row) not in rows:
                    rows.append((machine, name, row))
                if not machine and row is not None:
                    read.add(row)
    if not rows:
        rows = [(True, '', None)]   # the API matched the page but marked no line: the page itself, to read by eye
    return [dict({'source': SOURCE, 'id': '%s' % r['scheduleId'] if row is None else '%s:%d' % (r['scheduleId'], row),
                  'name': ' '.join(name.split()), 'role': 'census', 'principal': True, 'given': None,
                  'surnames': None, 'events': {'residence': {'year': 1950, 'year_to': 1950, 'place': place}},
                  'relatives': [], 'url': page['image'], 'row': row, 'machine': machine}, **page)
            for machine, name, row in rows]


def add_search_args(sp):
    sp.add_argument('--name', required=True, help='a name as written on the schedule, surname first or alone: '
                    '"garcia", "garcia juan"')
    sp.add_argument('--state', required=True, help='the two-letter code: PR, NY, FL ...')
    sp.add_argument('--county', default='', help='the county or, in Puerto Rico, the municipio: "San Juan"')
    sp.add_argument('--page', type=int, default=1, help='25 schedule pages a page')


def search(a, R):
    if not a.name.strip() or not re.fullmatch(r'[A-Za-z]{2}', a.state.strip()):
        print('gwt-read: census1950: give --name and a two-letter --state', file=sys.stderr, flush=True)
        return EXIT_USAGE
    url = search_url(a.name, a.state.strip(), a.county, a.page)
    off = (max(1, int(a.page)) - 1) * PAGE_SIZE
    status, rh, body, cached = gwt_reader.get_api(R, url, {'accept': ACCEPT},
                                                  expect=lambda d: verdict(d, off) != 'unknown', refresh=a.refresh)
    doc = json.loads(body)
    cands = [c for r in doc['results'] for c in candidates(r)]
    env = gwt_reader.envelope(url, None, json_body=doc, verdict=verdict(doc, off), status=status, from_cache=cached)
    words = [SOURCE, a.name, a.state.upper(), a.county, a.page]
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(url, words), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    for r in doc['results']:
        print('item\t%s\t%s\t%s' % (r['scheduleId'], image_url(r['image']), r.get('iiifUrl') or ''), flush=True)
    print('results\t%s pages, %d lines read%s%s' % (doc['total'], len(cands), gwt_reader.past_end(cands, off),
                                                  ' (cache)' if cached else ''), flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY
