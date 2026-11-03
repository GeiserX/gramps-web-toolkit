#!/usr/bin/env python3
"""gwt_digitalarkivet: the Norwegian Digitalarkivet's person search (digitalarkivet.no), for gwt-read.

The Arkivverket's Digitalarkivet has transcribed the censuses of 1801 to 1920, many church books and emigrant lists,
and searches every person in them from one page. It has no public API: this reader reads that page, 50 persons a
page, in English.

    search   /en/search/persons?s=<words>&event_year_from=&event_year_to=&page=N

A row is a transcription, a lead: open the scanned page (the record page links it) before citing it. A candidate's
`url` is the person's own page (/en/census/person/pf..., /en/view/255/pd...).

"none" is the site's own count, "Your search resulted in 0 hits". Any page with no person row and no zero count (an
empty result list, a block page, a new layout) is a STOP. Standard library only.
"""
import html, re, urllib.parse

import gwt_reader

HOST = 'https://www.digitalarkivet.no'
SOURCE = 'digitalarkivet'
# robots.txt asks every robot for Crawl-delay: 5
PACE = {'start': 6.0, 'floor': 5.0, 'ceil': 300.0}
DAY = 86400
SPEC = {
    'id': SOURCE,
    'help': 'Digitalarkivet, Norway\'s transcribed censuses, church books and emigrant lists; public',
    'pace': PACE,
    'cache_rules': ((r'/search/persons\?', 30 * DAY),),
    'controls': [{'argv': ['--name', 'Hansen'], 'min_count': 50}],
    # a person row inside the result list; the empty list itself is on every search page
    'result_markers': [r'<div id="searchResultList"[^>]*>\s*<div class="unit"'],
    'negative_markers': [r'data-hits="0"'],
}

_UNIT = re.compile(r'<div class="unit"[^>]*>(.*?)(?=<div class="unit"|<ul class="pagination"|</section>|\Z)', re.S)
# the person's own page, absolute or not; its id is the last part of the path, whatever its form (pf..., pd..., ...)
_LINK = re.compile(r'<div class="name">\s*<a href="((?:https://www\.digitalarkivet\.no)?/[^"]+/([^/"?#]+))"[^>]*>(.*?)'
                   r'<div class="born">(.*?)</div>', re.S)
_FIELD = re.compile(r'<div class="([\w-]+)">\s*<span>([^<]*)</span>(.*?)</div>', re.S)
_SOURCE = re.compile(r'<a href="(https://www\.digitalarkivet\.no/[^"]*source/\d+)">(.*?)</a>', re.S)
_HITS = re.compile(r'data-hits="(\d+)"')


def text(fragment):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', fragment or ''))).strip()


def search_url(words, year_from='', year_to='', page=1):
    q = [('s', words)]
    q += [(k, v) for k, v in (('event_year_from', year_from), ('event_year_to', year_to)) if v]
    if page and int(page) > 1:
        q.append(('page', str(page)))
    return HOST + '/en/search/persons?' + urllib.parse.urlencode(q)


def candidates(page):
    out = []
    for unit in _UNIT.findall(page):
        m = _LINK.search(unit)
        if not m:
            continue
        url, rid, raw_name, born = m.groups()
        url = urllib.parse.urljoin(HOST, url)
        # the site writes the given names and the surname in two runs of text, separated by tabs
        parts = [text(p) for p in re.split(r'\t{2,}|\n', html.unescape(raw_name)) if text(p)]
        fields = {cls: (label.rstrip(':'), text(val)) for cls, label, val in _FIELD.findall(unit) if cls != 'name'}
        src = _SOURCE.search(unit)
        sex = 'male' if 'fa-mars' in unit else 'female' if 'fa-venus' in unit else None
        ev = fields.get('action')
        out.append({'source': SOURCE, 'id': rid, 'name': ' '.join(parts), 'role': (fields.get('role') or ('', ''))[1],
                    'principal': not (fields.get('role') or ('', ''))[1], 'given': parts[0] if len(parts) > 1 else None,
                    'surnames': parts[1:] if len(parts) > 1 else None, 'sex': sex,
                    'born': text(born).strip('()').replace('b. ', '', 1) or None,
                    'events': {'event': {'label': ev[0], 'date': ev[1]}} if ev and ev[1] else {},
                    'domicile': (fields.get('place') or ('', ''))[1] or None,
                    'position': (fields.get('stilling-stand') or ('', ''))[1] or None,
                    'relatives': [], 'record_source': text(src.group(2)) if src else None,
                    'source_url': src.group(1) if src else None, 'url': url})
    return out


def total_of(page):
    m = _HITS.search(page)
    return int(m.group(1)) if m else None


def add_search_args(sp):
    sp.add_argument('--name', required=True, help='the site\'s own search words: names, and places or years if '
                    'you like (it reads them all)')
    sp.add_argument('--from', dest='year_from', default='', help='first year of the event')
    sp.add_argument('--to', dest='year_to', default='', help='last year of the event')
    sp.add_argument('--page', type=int, default=1, help='page of 50 persons (default 1)')


def search(a, R):
    url = search_url(a.name, a.year_from, a.year_to, a.page)
    status, rh, body, cached = gwt_reader.get_api(R, url, {'accept': 'text/html'}, refresh=a.refresh,
                                                  page=gwt_reader.rows_check(SPEC, r'<div class="unit"', candidates))
    page = body.decode('utf-8', 'replace')
    cands = candidates(page)
    env = gwt_reader.envelope(url, (rh or {}).get('x-gwt-final-url'), text=text(page),
                              verdict=gwt_reader.classify(page, SPEC), from_cache=cached)
    words = [SOURCE, a.name, a.year_from, a.year_to, a.page if a.page > 1 else '']
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(url, words), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    total = total_of(page)
    print('results\t%s total, %d read%s' % ('?' if total is None else total, len(cands), ' (cache)' if cached else ''),
          flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY
