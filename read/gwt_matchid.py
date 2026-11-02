#!/usr/bin/env python3
"""gwt_matchid: matchID (deces.matchid.io), France's deaths since 1970 searchable online, for gwt-read.

matchID serves INSEE's deaths files (the same data `gwt-read insee` reads offline) through an open search API,
https://deces.matchid.io/deces/api/v1/search , with the birth and death dates and places of each person. Anonymous
use is limited (about 1 request a second, and a capped number of calls); a free token from https://deces.matchid.io/
raises it. Put it in $MATCHID_TOKEN: it is sent as a Bearer header by the fetcher, so it never reaches the cache key,
the envelope or a log line.

    search   by --given, --surname, --born, --died, --birth-city, --death-city, --sex; or --q for the site's free text

The service has had outages: any 5xx, or no answer, is read as a block (exit 3, the gate pauses), never as "no
results", and any answer of another shape is a STOP. "none" is the API's own count, `total` 0. Each row is the
principal of one INSEE death record, as in `gwt-read insee`. Standard library only.
"""
import json, os, re, sys, urllib.parse, urllib.request

import gwt_reader

API = 'https://deces.matchid.io/deces/api/v1'
SOURCE = 'matchid'
PACE = {'start': 2.0, 'floor': 1.0, 'ceil': 300.0}
DAY = 86400
TOKEN_ENV = 'MATCHID_TOKEN'
ACCEPT = 'application/json'
EXIT_USAGE = 64


def build(src, a, urlopen, gate):
    """The default public Reader; with $MATCHID_TOKEN, its fetcher adds the token itself (never a cached header)."""
    import gwt_read
    R = gwt_read._default_build(src, a, urlopen, gate)
    token = os.environ.get(TOKEN_ENV, '').strip()
    if token:
        R.fetcher = gwt_read.urllib_fetcher(token, a.user_agent, urlopen or urllib.request.urlopen)
    return R


SPEC = {
    'id': SOURCE,
    'help': "matchID, France's deaths since 1970 (INSEE) searched online; open API, optional $MATCHID_TOKEN",
    'pace': PACE,
    # INSEE adds a month of deaths at a time: a search is kept 30 days
    'cache_rules': ((r'/deces/api/v1/search\?', 30 * DAY),),
    'controls': [{'argv': ['--given', 'Jean', '--surname', 'Dupont'], 'min_count': 10}],
    'result_markers': [], 'negative_markers': [],      # a JSON API: verdict() reads the answer's own count
    'search_help': 'a death search; each row is the principal of one INSEE death record',
    'build': build,
}


def quota_spent(status, body):
    """matchID's answer once the anonymous calls are spent: HTTP 422 "No token provided and temporary anonymous usage
    expired, please register with email or wait N hours". A wall for the gate, never an error a lane moves past."""
    return status == 422 and b'usage expired' in (body or b'').lower()


def _date(v):
    """matchID dates: JJ/MM/AAAA, a year AAAA, or a range JJ/MM/AAAA-JJ/MM/AAAA. The API refuses spaces around the
    range's "-" (HTTP 400), so they are dropped."""
    return re.sub(r'\s*-\s*', '-', str(v).strip()) if v else ''


def search_url(q='', given='', surname='', born='', died='', birth_city='', death_city='', sex='', count=20, page=1):
    p = []
    for k, v in (('q', q), ('firstName', given), ('lastName', surname), ('birthDate', _date(born)),
                 ('deathDate', _date(died)), ('birthCity', birth_city), ('deathCity', death_city), ('sex', sex)):
        if v:
            p.append((k, v))
    p += [('size', max(1, min(int(count), 100))), ('page', max(1, int(page)))]
    return API + '/search?' + urllib.parse.urlencode(p)


def verdict(doc):
    """'hits', 'none' or 'unknown': {response: {total: N, persons: [...]}}."""
    r = doc.get('response') if isinstance(doc, dict) else None
    if not isinstance(r, dict) or not isinstance(r.get('persons'), list):
        return 'unknown'    # the result container renamed: a STOP
    n = r.get('total')
    if not isinstance(n, int) or isinstance(n, bool):
        return 'unknown'
    if n == 0 and not r['persons']:
        return 'none'
    if r['persons'] and all(isinstance(x, dict) and x.get('id') and isinstance(x.get('name'), dict)
                            for x in r['persons']):
        return 'hits'
    return 'unknown'


def _year(d):
    y = str(d or '')[:4]
    return int(y) if y.isdigit() and int(y) else None


def _place(loc):
    loc = loc or {}
    city = loc.get('city')
    city = city[0] if isinstance(city, list) and city else city or ''
    country = loc.get('country') or ''
    return ', '.join(x for x in (city, country if country != 'France' or not city else '') if x)


def candidate(p):
    first = p['name'].get('first') or []
    given = ' '.join(first) if isinstance(first, list) else str(first)
    last = p['name'].get('last') or ''
    last = ' '.join(last) if isinstance(last, list) else str(last)
    b, d = p.get('birth') or {}, p.get('death') or {}
    by, dy = _year(b.get('date')), _year(d.get('date'))
    return {'source': SOURCE, 'id': p['id'], 'name': ' '.join(x for x in (given, last) if x), 'role': 'Principal',
            'principal': True, 'given': given or None, 'surnames': [last] if last else None, 'sex': p.get('sex') or '',
            'events': {'birth': {'year': by, 'year_to': by, 'place': _place(b.get('location'))},
                       'death': {'year': dy, 'year_to': dy, 'place': _place(d.get('location'))}},
            'relatives': [], 'url': 'https://deces.matchid.io/id/%s' % urllib.parse.quote(p['id']),
            'birth_date': b.get('date') or '', 'death_date': d.get('date') or '',
            'birth_place_code': (b.get('location') or {}).get('code') or '',
            'death_place_code': (d.get('location') or {}).get('code') or '',
            'act': d.get('certificateId') or '', 'insee_file': p.get('source') or '', 'line': p.get('sourceLine')}


def add_search_args(sp):
    sp.add_argument('--q', default='', help='the site\'s free text: "Dupont Jean 1950"')
    sp.add_argument('--given', default='')
    sp.add_argument('--surname', default='')
    sp.add_argument('--born', default='', help='birth date: a year, JJ/MM/AAAA, or a range "01/01/1900-31/12/1910" or "1900-1910"')
    sp.add_argument('--died', default='', help='death date, as --born')
    sp.add_argument('--birth-city', default='')
    sp.add_argument('--death-city', default='')
    sp.add_argument('--sex', default='', choices=('', 'M', 'F'))
    sp.add_argument('--count', type=int, default=20, choices=range(1, 101), metavar='1-100')
    sp.add_argument('--page', type=int, default=1)


def search(a, R):
    if not (a.q or a.surname or a.given):
        print('gwt-read: matchid: give --surname, --given or --q', file=sys.stderr, flush=True)
        return EXIT_USAGE
    url = search_url(a.q, a.given, a.surname, a.born, a.died, a.birth_city, a.death_city, a.sex, a.count, a.page)
    status, rh, body, cached = gwt_reader.get_api(R, url, {'accept': ACCEPT}, expect=lambda d: verdict(d) != 'unknown',
                                                  refresh=a.refresh, wall=quota_spent)
    doc = json.loads(body)
    cands = [candidate(p) for p in doc['response']['persons']]
    env = gwt_reader.envelope(url, None, json_body=doc, verdict=verdict(doc), status=status, from_cache=cached)
    words = [SOURCE, a.q, a.given, a.surname, a.born, a.died, a.birth_city, a.death_city, a.sex, a.page]
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(url, words), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    print('results\t%s total, %d read%s' % (doc['response']['total'], len(cands), ' (cache)' if cached else ''),
          flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY
