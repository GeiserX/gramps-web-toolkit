#!/usr/bin/env python3
"""gwt_freeukgen: FreeREG and FreeCEN (Free UK Genealogy), volunteer transcriptions of English, Welsh and Scottish
parish registers and censuses, for gwt-read.

FreeREG (freereg.org.uk) holds parish baptisms, marriages and burials from 1538; FreeCEN (freecen.org.uk) the
censuses of 1841 to 1911, strongest for Scotland and Cornwall. Both run the same open-source Rails application
(https://github.com/FreeUKGen/MyopicVicar), so one reader serves both: `--site freereg` or `--site freecen`.

    search   GET  /search_queries/new        the search form, for its session cookie and form token
             POST /search_queries            the form, as a person fills it in
             ->   /search_queries/<id>       the site keeps the search and shows its results there

A row is a transcription, a lead: open the record page and the register image or the census page it names before
citing it. A candidate's `url` is the record's own page, /search_records/<id>/<name>.

"none" is the site's own heading, "No results found". "Too many search results" (500 rows on FreeREG, 1000 on
FreeCEN) is neither: nothing is written and the search exits 1, so narrow it. A search the site's own rules refuse
is refused here before any request (exit 64); if the site still refuses one, it shows its form again with its
reasons, and that is exit 64 too, never a block. Any other page (a maintenance page, a block page, a new layout) is
a STOP. The cookie and the form token live in memory for the run only. Standard
library only.
"""
import html, re, sys, urllib.parse

import gwt_reader

HOSTS = {'freereg': 'https://www.freereg.org.uk', 'freecen': 'https://www.freecen.org.uk'}
SOURCE = 'freeukgen'
# Volunteer sites. Their robots.txt limits only two advertising crawlers, and states no delay: one search (two
# requests: the form, then the search) every 10 s at most, never faster than one request every 5 s.
PACE = {'start': 10.0, 'floor': 5.0, 'ceil': 600.0}
DAY = 86400
EXIT_USAGE = 64
SPEC = {
    'id': SOURCE,
    'help': 'FreeREG and FreeCEN, UK volunteer transcriptions of parish registers and censuses; public',
    'pace': PACE,
    # the form carries a fresh token every time: never served from the cache. A search result lives on the site
    # under its own id and is not asked for again; a record page is kept 30 days.
    'cache_rules': ((r'/search_queries/new', 0), (r'/search_records/', 30 * DAY)),
    'controls': [{'argv': ['--surname', 'Smith', '--given', 'John', '--from', '1800', '--to', '1801',
                           '--type', 'baptisms'], 'min_count': 5}],
    # a result row: <tr id="<24 hex>">, the record's own id
    'result_markers': [r'<tr id="[0-9a-f]{24}">'],
    'negative_markers': [r'>\s*No results found\s*<'],
}
TOO_MANY = re.compile(r'>\s*Too many search results\s*<')
# the form shown again with the site's reasons, when it refuses a search
REFUSED = re.compile(r'prohibited this search from being processed')
_REASON = re.compile(r'<li class="validation-list__error[^"]*"\s*>(.*?)</li>', re.S)
CHANNEL_ISLANDS = ['ALD', 'GSY', 'JSY', 'SRK']     # the one set of four counties FreeREG takes together
TYPES = {'baptisms': 'ba', 'marriages': 'ma', 'burials': 'bu'}
CENSUS = ('1841', '1851', '1861', '1871', '1881', '1891', '1901', '1911')
FREEREG_COLUMNS = ('persons', 'record_type', 'date', 'county', 'place')
FREECEN_COLUMNS = ('persons', 'birth_county', 'birth_place', 'birth_year', 'census', 'census_county', 'district')

_TOKEN = re.compile(r'name="authenticity_token"[^>]*value="([^"]+)"|<meta name="csrf-token" content="([^"]+)"')
_ROW = re.compile(r'<tr id="([0-9a-f]{24})">(.*?)</tr>', re.S)
_CELL = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
_LINK = re.compile(r'href="(/search_records/[0-9a-f]{24}[^"]*)"')


def text(fragment):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', fragment or ''))).strip()


def form_token(page):
    m = _TOKEN.search(page)
    return html.unescape(m.group(1) or m.group(2)) if m else None


def search_fields(site, surname='', given='', year_from='', year_to='', kind='', counties=()):
    """The form's fields, in the form's names. `region` is the form's empty trap field: it must stay empty."""
    f = [('search_query[last_name]', surname), ('search_query[first_name]', given),
         ('search_query[start_year]', year_from), ('search_query[end_year]', year_to)]
    f += [('search_query[chapman_codes][]', c) for c in counties]
    if kind:
        f.append(('search_query[record_type]', TYPES.get(kind, kind)))
    f.append(('search_query[region]', ''))
    return [(k, str(v)) for k, v in f if v or k.endswith('[region]')]


def candidates(page, host, site):
    out = []
    names = FREEREG_COLUMNS if site == 'freereg' else FREECEN_COLUMNS
    for rid, row in _ROW.findall(page):
        cells = _CELL.findall(row)
        link = _LINK.search(cells[0]) if cells else None
        vals = dict(zip(names, (text(c) for c in cells[1:])))
        people = [text(p) for p in re.split(r'<br\s*/?>', cells[1])] if len(cells) > 1 else []
        people = [p for p in people if p]
        c = {'source': SOURCE, 'site': site, 'id': rid, 'name': people[0] if people else '', 'role': 'principal',
             'principal': True, 'given': None, 'surnames': None, 'events': {},
             'relatives': [{'role': 'also named', 'name': p} for p in people[1:]],
             'url': host + html.unescape(link.group(1)) if link else '%s/search_records/%s' % (host, rid)}
        if site == 'freereg':
            c['events'] = {vals.get('record_type') or 'event': {'date': vals.get('date') or None,
                                                                'place': vals.get('place') or None,
                                                                'county': vals.get('county') or None}}
        else:
            c['events'] = {'census': {'year': vals.get('census') or None, 'county': vals.get('census_county') or None,
                                      'district': vals.get('district') or None},
                           'birth': {'year': vals.get('birth_year') or None, 'place': vals.get('birth_place') or None,
                                     'county': vals.get('birth_county') or None}}
        out.append(c)
    return out


def add_search_args(sp):
    sp.add_argument('--site', dest='ukgen', default='freereg', choices=sorted(HOSTS),
                    help='freereg (parish registers, default) or freecen (censuses)')
    sp.add_argument('--surname', default='')
    sp.add_argument('--given', default='')
    sp.add_argument('--from', dest='year_from', default='')
    sp.add_argument('--to', dest='year_to', default='')
    sp.add_argument('--type', dest='kind', default='', help='FreeREG: baptisms, marriages or burials; FreeCEN: a '
                    'census year (1841 to 1911); default all')
    sp.add_argument('--county', action='append', default=[], help='a county\'s Chapman code (DEV, CON, LAN, ...); '
                    'repeat for more')


def refusal(site, surname, year_from, year_to, kind, counties):
    """The sentence the site would refuse this search with, or None. The rules are the sites' own (MyopicVicar
    app/models/search_query.rb): https://github.com/FreeUKGen/MyopicVicar/blob/51d429fecf01daa7d626e555922602f306645699/app/models/search_query.rb#L839-L842
    and #L1367-L1380 for the county rules, #L410-L422 for the years."""
    if not surname.strip():
        # without a surname the site needs a forename, a county and a place; a place is not a search option here
        return 'give --surname: without one the site needs a forename, a county and a place'
    for y in (year_from, year_to):
        if y and not str(y).isdigit():
            return 'a year is a number (%r)' % y
    if year_from and not year_to:
        return 'You have specified a start year but no end year'
    if year_to and not year_from:
        return 'You have specified an end year but no start year'
    if year_from and int(year_from) > int(year_to):
        return 'First year must precede last year.'
    if site == 'freereg':
        if not counties and not (kind and year_from and year_to):
            return ('A date range and record type must be part of your search if you do not select a county '
                    '(--type, --from and --to, or --county)')
        if len(counties) > 3 and list(counties) != CHANNEL_ISLANDS:
            return 'You cannot select more than 3 counties.'
    return None


def search(a, R):
    ok = sorted(TYPES) if a.ukgen == 'freereg' else CENSUS
    if a.kind and a.kind not in ok:
        print('gwt-read: freeukgen: --type for %s is one of %s' % (a.ukgen, ', '.join(ok)), file=sys.stderr, flush=True)
        return EXIT_USAGE
    why = refusal(a.ukgen, a.surname, a.year_from, a.year_to, a.kind, a.county)
    if why:
        print('gwt-read: freeukgen: %s: %s' % (a.ukgen, why), file=sys.stderr, flush=True)
        return EXIT_USAGE
    host = HOSTS[a.ukgen]
    _, _, body, _ = gwt_reader.get_api(R, host + '/search_queries/new', {'accept': 'text/html'}, refresh=True,
                                       page=lambda p: form_token(p) is not None)
    token = form_token(body.decode('utf-8', 'replace'))
    fields = search_fields(a.ukgen, a.surname, a.given, a.year_from, a.year_to, a.kind, a.county)
    url = host + '/search_queries'
    data = urllib.parse.urlencode([('utf8', '✓'), ('authenticity_token', token)] + fields
                                  + [('commit', 'Search')]).encode()
    check = gwt_reader.rows_check(SPEC, r'<tr id="', lambda p: candidates(p, host, a.ukgen))
    status, rh, body, cached = gwt_reader.get_api(
        R, url, {'accept': 'text/html', 'content-type': 'application/x-www-form-urlencoded'}, refresh=True,
        page=lambda p: check(p) or bool(TOO_MANY.search(p) or REFUSED.search(p)), data=data)
    page = body.decode('utf-8', 'replace')
    final = (rh or {}).get('x-gwt-final-url')
    if REFUSED.search(page) and gwt_reader.classify(page, SPEC) == 'unknown':
        why = '; '.join(text(r) for r in _REASON.findall(page)) or 'no reason given'
        print('gwt-read: freeukgen: the site refused the search: %s; nothing written' % why, file=sys.stderr, flush=True)
        return EXIT_USAGE
    if TOO_MANY.search(page) and not re.search(SPEC['result_markers'][0], page):
        print('gwt-read: freeukgen: too many results (the site shows none past its limit); add --given, --from/--to, '
              '--type or --county; nothing written (%s)' % (final or url), file=sys.stderr, flush=True)
        return gwt_reader.EXIT_ERROR
    cands = candidates(page, host, a.ukgen)
    env = gwt_reader.envelope(url, final, text=text(page), request=fields, verdict=gwt_reader.classify(page, SPEC),
                              from_cache=cached)
    key_url = url + '?' + urllib.parse.urlencode(fields)
    words = [SOURCE, a.ukgen, a.surname, a.given, a.year_from, a.year_to, a.kind] + a.county
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(key_url, words), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    print('results\t%d read' % len(cands), flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY


def build(src, a, urlopen, gate):
    """PARES's reader: a cookie jar in memory for this run only. The form token is tied to the session cookie, which
    never reaches the cache or a file."""
    import gwt_read
    return gwt_read._pares_build(src, a, urlopen, gate)


SPEC['build'] = build
