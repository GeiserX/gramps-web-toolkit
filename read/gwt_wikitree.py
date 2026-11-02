#!/usr/bin/env python3
"""gwt_wikitree: WikiTree (wikitree.com), the free shared one-world tree, read through its API for gwt-read.

WikiTree's API (https://github.com/wikitree/wikitree-api) answers without a login for public profiles. It asks every
client to name itself with `appId`; this reader sends appId=gramps-web-toolkit on every call and never logs in, so
private profiles stay private: the API gives only their id, and this reader keeps them out of the candidates and
counts them as `withheld`.

    search   action=searchPerson   by given name, surname, birth and death year, birth place
    record   action=getProfile     one profile by its WikiTree ID (Smith-1234)
    record --ancestors N           action=getPeople&ancestors=N: the profile and N generations of its ancestors

A tree profile is what another researcher believes, never a record: a candidate here is a lead to a person and to
the sources the profile cites, never evidence on its own. "none" is the API's own count: `total` 0 with no match.
Any other answer shape, or a non-zero `status`, is a STOP. Standard library only.
"""
import json, re, sys, urllib.parse

import gwt_reader

API = 'https://api.wikitree.com/api.php'
WEB = 'https://www.wikitree.com/wiki/'
APP_ID = 'gramps-web-toolkit'
SOURCE = 'wikitree'
PACE = {'start': 3.0, 'floor': 2.0, 'ceil': 120.0}
DAY = 86400
FIELDS = ('Id,Name,FirstName,MiddleName,LastNameAtBirth,LastNameCurrent,BirthDate,DeathDate,BirthLocation,'
          'DeathLocation,Gender,Privacy,IsLiving,Father,Mother')
SPEC = {
    'id': SOURCE,
    'help': 'WikiTree, the shared one-world tree; public profiles only, no account',
    'pace': PACE,
    # profiles are edited every day: a search is kept a day, a profile a week
    'cache_rules': ((r'action=searchPerson', 1 * DAY), (r'action=get(Profile|People)', 7 * DAY)),
    'controls': [{'argv': ['--surname', 'Smith'], 'min_count': 10}],
    'result_markers': [], 'negative_markers': [],      # a JSON API: verdict() reads the answer's own count
    'search_help': 'a person search over public profiles',
    'record_help': 'one profile by its WikiTree ID; --ancestors N adds N generations of ancestors',
}
ACCEPT = 'application/json'
EXIT_USAGE = 64
_KEY = re.compile(r'(?:wikitree\.com/wiki/)?([^\s/?#:]+-\d+)(?:[/?#]|$)')


def api_url(params):
    return API + '?' + urllib.parse.urlencode(list(params) + [('appId', APP_ID), ('fields', FIELDS)])


def search_url(given='', surname='', born=None, died=None, birth_place='', count=25, start=0):
    q = [('action', 'searchPerson')]
    for k, v in (('FirstName', given), ('LastName', surname), ('BirthDate', born), ('DeathDate', died),
                 ('BirthLocation', birth_place)):
        if v:
            q.append((k, v))
    if born or died:
        q.append(('dateInclude', 'both'))   # else WikiTree also returns every profile with no date at all
    q += [('limit', max(1, min(int(count), 100))), ('start', max(0, int(start)))]
    return api_url(q)


def profile_url(key, ancestors=0):
    if ancestors:
        return api_url([('action', 'getPeople'), ('keys', key), ('ancestors', max(1, min(int(ancestors), 10)))])
    return api_url([('action', 'getProfile'), ('key', key)])


def profile_key(arg):
    m = _KEY.search((arg or '').strip())
    return m.group(1) if m else None


def _first(doc):
    return doc[0] if isinstance(doc, list) and len(doc) == 1 and isinstance(doc[0], dict) else None


def verdict(doc):
    """'hits', 'none' or 'unknown' for a searchPerson answer: [{status: 0, matches: [...], total: N}]."""
    d = _first(doc)
    if d is None or d.get('status') != 0 or not isinstance(d.get('matches'), list):
        return 'unknown'    # an error status, or the result container renamed: a STOP
    n = d.get('total')
    if not isinstance(n, int) or isinstance(n, bool):
        return 'unknown'
    if n == 0 and not d['matches']:
        return 'none'
    if d['matches'] and all(isinstance(m, dict) and m.get('Name') for m in d['matches']):
        return 'hits'
    return 'unknown'


def record_verdict(doc):
    """'hits', 'none' (the site's own "Invalid WikiTree ID", or no person) or 'unknown' for getProfile and getPeople."""
    d = _first(doc)
    if d is None:
        return 'unknown'
    if 'people' in d:                                   # getPeople: people is {} or {id: profile}
        if not isinstance(d['people'], (dict, list)):
            return 'unknown'
        return 'hits' if d['people'] else 'none'
    if d.get('status') == 0 and isinstance(d.get('profile'), dict):
        return 'hits'
    if isinstance(d.get('status'), str) and 'invalid' in d['status'].lower():
        return 'none'
    return 'unknown'


def is_public(p):
    return isinstance(p, dict) and p.get('Privacy_IsAtLeastPublic') is True


def _year(date):
    m = re.match(r'(\d{4})', str(date or ''))
    y = int(m.group(1)) if m else 0
    return y or None


def candidate(p, names=None):
    names = names or {}
    given = ' '.join(x for x in (p.get('FirstName'), p.get('MiddleName')) if x).strip()
    sur = [s for s in dict.fromkeys((p.get('LastNameAtBirth'), p.get('LastNameCurrent'))) if s]
    events = {}
    for kind, date, place in (('birth', 'BirthDate', 'BirthLocation'), ('death', 'DeathDate', 'DeathLocation')):
        y = _year(p.get(date))
        if y or p.get(place):
            events[kind] = {'year': y, 'year_to': y, 'place': p.get(place) or ''}
    rels = []
    for role in ('Father', 'Mother'):
        other = names.get(p.get(role))
        if other:
            rels.append({'role': role.lower(), 'name': other})
    c = {'source': SOURCE, 'id': p['Name'], 'name': ' '.join(x for x in [given] + sur[:1] if x),
         'role': 'profile', 'principal': True, 'given': given or None, 'surnames': sur or None, 'events': events,
         'relatives': rels, 'url': WEB + urllib.parse.quote(p['Name']), 'sex': p.get('Gender') or '',
         'birth_date': p.get('BirthDate') or '', 'death_date': p.get('DeathDate') or ''}
    if p.get('IsLiving'):
        c['living'] = True
    return c


def candidates(people):
    """(candidates, withheld): public profiles as candidates; private ones only counted."""
    people = [p for p in people if isinstance(p, dict)]
    names = {}
    for p in people:
        if is_public(p):
            names[p.get('Id')] = ' '.join(x for x in (p.get('FirstName'), p.get('LastNameAtBirth')) if x)
    pub = [candidate(p, names) for p in people if is_public(p) and p.get('Name')]
    return pub, len(people) - len(pub)


def add_search_args(sp):
    sp.add_argument('--given', default='')
    sp.add_argument('--surname', default='', help='surname at birth or current')
    sp.add_argument('--born', help='birth year or yyyy-mm-dd')
    sp.add_argument('--died', help='death year or yyyy-mm-dd')
    sp.add_argument('--birth-place', default='', help='words of the birth place')
    sp.add_argument('--count', type=int, default=25, choices=range(1, 101), metavar='1-100')
    sp.add_argument('--start', type=int, default=0)


def add_record_args(sp):
    sp.add_argument('key', help='a WikiTree ID (Smith-1234) or its profile URL')
    sp.add_argument('--ancestors', type=int, default=0, choices=range(0, 11), metavar='0-10',
                    help='generations of ancestors to read with it (one request)')


def _write(a, url, doc, v, cands, withheld, cached, status, words):
    env = gwt_reader.envelope(url, None, json_body=doc, verdict=v, withheld=withheld, status=status, from_cache=cached)
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(url, words), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    return base


def search(a, R):
    if not (a.given or a.surname):
        print('gwt-read: wikitree: give --surname or --given', file=sys.stderr, flush=True)
        return EXIT_USAGE
    url = search_url(a.given, a.surname, a.born, a.died, a.birth_place, a.count, a.start)
    status, rh, body, cached = gwt_reader.get_api(R, url, {'accept': ACCEPT}, expect=lambda d: verdict(d) != 'unknown',
                                                  refresh=a.refresh)
    doc = json.loads(body)
    cands, withheld = candidates(doc[0]['matches'])
    _write(a, url, doc, verdict(doc), cands, withheld, cached, status,
           [SOURCE, a.given, a.surname, a.born, a.died, a.birth_place, a.start])
    print('results\t%s total, %d public read, %d private withheld%s'
          % (doc[0]['total'], len(cands), withheld, ' (cache)' if cached else ''), flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY


def record(a, R):
    key = profile_key(a.key)
    if not key:
        print('gwt-read: wikitree: %r is not a WikiTree ID (Surname-123)' % a.key, file=sys.stderr, flush=True)
        return EXIT_USAGE
    url = profile_url(key, a.ancestors)
    status, rh, body, cached = gwt_reader.get_api(R, url, {'accept': ACCEPT},
                                                  expect=lambda d: record_verdict(d) != 'unknown', refresh=a.refresh)
    doc = json.loads(body)
    v, d = record_verdict(doc), doc[0]
    people = (list(d['people'].values()) if isinstance(d.get('people'), dict) else d.get('people') or []) \
        if 'people' in d else [d['profile']] if v == 'hits' else []
    cands, withheld = candidates(people)
    _write(a, url, doc, v, cands, withheld, cached, status, [SOURCE, key, a.ancestors])
    print('results\t%d public profiles, %d private withheld%s' % (len(cands), withheld, ' (cache)' if cached else ''),
          flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY
