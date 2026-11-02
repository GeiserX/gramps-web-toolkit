#!/usr/bin/env python3
"""gwt_openarch: Open Archives (openarchieven.nl), person records from Dutch, Belgian and other archives, for gwt-read.

Open Archives indexes civil registration, church books, population registers, notarial and court records of
hundreds of archives, strongest in the Netherlands and Flanders, with some French, German, Surinamese and South
African series. Its REST API is open: no key, no login. The API documents a limit of 4 requests a second per IP
(https://api.openarchieven.nl/openapi.yaml); this reader keeps to one every 2 s at best.

    search   https://api.openarch.nl/1.1/records/search.json?name=...   one row per person mention
    record   https://api.openarch.nl/1.1/records/show.json?archive=A&identifier=UUID   the whole record (A2A)
    --gedcom https://www.openarchieven.nl/A:UUID/gedcom   the same record as GEDCOM 7.0, saved beside it

The citation URL of a record is https://www.openarchieven.nl/{archive}:{uuid}. A search row is one person in one
record (the bride of a marriage, the father at a birth), with its role; `record` reads every person of the record.
"none" is the API's own count: `number_found` 0 in a well-shaped answer. The API answers some errors (a missing
archive code) with HTTP 200 and an `error_code`; that answer, or any other shape, is a STOP, never zero results.
Standard library only.
"""
import json, os, re, sys, urllib.parse

import gwt_reader

API = 'https://api.openarch.nl/1.1'
WEB = 'https://www.openarchieven.nl'
SOURCE = 'openarch'
PACE = {'start': 2.0, 'floor': 2.0, 'ceil': 120.0}
DAY = 86400
SPEC = {
    'id': SOURCE,
    'help': 'Open Archives (openarchieven.nl), Dutch, Belgian and other archives; open API, no account',
    'pace': PACE,
    'cache_rules': ((r'/records/search\.json\?', 7 * DAY), (r'/records/show\.json\?', 90 * DAY),
                    (r'/gedcom$', 90 * DAY)),
    # Jansen is the most common Dutch surname: well over a million rows for years
    'controls': [{'argv': ['--name', 'Jansen'], 'min_count': 10}],
    'result_markers': [], 'negative_markers': [],      # a JSON API: verdict() reads the answer's own count
    'search_help': 'a person search; each row is one person in one record, with its role',
    'record_help': 'one record by ARCHIVE:UUID or its openarchieven.nl URL; --gedcom saves its GEDCOM too',
}
ACCEPT = 'application/json'
EXIT_USAGE = 64
_REC = re.compile(r'(?:openarchieven\.nl/|openarchives\.nl/)?([a-z0-9_-]+):([0-9A-Fa-f-]{32,36})(?:[/?#]|$)')

# A2A role words (Dutch as the archives type them, and the API's English labels), folded, and who is the principal
CHILD = {'kind', 'dopeling', 'child', 'baptized', 'baptised', 'gedoopte'}
PRINCIPAL = {'bruidegom', 'bruid', 'groom', 'bride', 'overledene', 'deceased', 'begravene', 'buried', 'geregistreerde',
             'registered', 'hoofdpersoon', 'main person', 'ondertrouwde', 'echtgenoot', 'echtgenote'}
BIRTH = {'geboorte', 'doop', 'birth', 'baptism', 'christening', 'bs geboorte', 'dtb dopen'}
DEATH = {'overlijden', 'begraven', 'begrafenis', 'death', 'burial', 'bs overlijden', 'dtb begraven'}
MARRIAGE = {'huwelijk', 'ondertrouw', 'marriage', 'marriage banns', 'bs huwelijk', 'dtb trouwen'}
ROLE_EN = {'kind': 'child', 'dopeling': 'child', 'bruidegom': 'groom', 'bruid': 'bride', 'vader': 'father',
           'moeder': 'mother', 'overledene': 'deceased', 'getuige': 'witness', 'geregistreerde': 'registered',
           'vader van de bruidegom': 'groom father', 'moeder van de bruidegom': 'groom mother',
           'vader van de bruid': 'bride father', 'moeder van de bruid': 'bride mother',
           'echtgenoot': 'spouse', 'echtgenote': 'spouse', 'aangever': 'informant'}


def _fold(s):
    s = re.sub(r'^other:', '', str(s or '').strip(), flags=re.I)
    return ' '.join(gwt_reader.norm_words(s))


def _list(x):
    """A2A gives one item as an object and several as a list."""
    return x if isinstance(x, list) else [] if x is None else [x]


def _year(v):
    m = re.search(r'(?<!\d)(\d{4})(?!\d)', str(v or ''))
    return int(m.group(1)) if m else None


def event_kind(word):
    w = _fold(word)
    return 'birth' if w in BIRTH else 'death' if w in DEATH else 'marriage' if w in MARRIAGE else 'event'


def is_principal(role, kind):
    r = _fold(role)
    return r in PRINCIPAL or (r in CHILD and kind == 'birth')


def citation_url(archive, uuid):
    return '%s/%s:%s' % (WEB, archive, uuid)


def search_url(name, period='', eventplace='', archive='', sourcetype='', relationtype='', country='', count=25,
               start=0):
    q = [('name', ' '.join(x for x in (name.strip(), str(period or '').strip()) if x)), ('lang', 'en'),
         ('number_show', max(1, min(int(count), 100))), ('start', max(0, min(int(start), 10000)))]
    for k, v in (('eventplace', eventplace), ('archive_code', archive), ('sourcetype', sourcetype),
                 ('relationtype', relationtype), ('country_code', country)):
        if v:
            q.append((k, v))
    return API + '/records/search.json?' + urllib.parse.urlencode(q)


def record_url(archive, uuid):
    return API + '/records/show.json?' + urllib.parse.urlencode([('archive', archive), ('identifier', uuid),
                                                                  ('lang', 'en')])


def gedcom_url(archive, uuid):
    return citation_url(archive, uuid) + '/gedcom'


def record_id(arg):
    """(archive, uuid) from ARCHIVE:UUID or a record's openarchieven.nl URL, else None."""
    m = _REC.search((arg or '').strip())
    return (m.group(1), m.group(2)) if m else None


def verdict(doc, offset=0):
    """'hits', 'none' or 'unknown' for a search answer. Only the API's own count says none; an error answer
    (error_code), a missing count or a count with no rows is unknown, a STOP, unless the rows asked for start at or
    past that count (`offset`): then it is the end of the results."""
    if not isinstance(doc, dict) or not isinstance(doc.get('response'), dict):
        return 'unknown'    # an error answer, or the result container renamed: a STOP
    n, docs = doc['response'].get('number_found'), doc['response'].get('docs', [])
    if not isinstance(n, int) or isinstance(n, bool) or not isinstance(docs, list):
        return 'unknown'
    if n == 0 and not docs:
        return 'none'
    # paged past the last row: the end of the results, not a changed answer
    if not docs and offset >= n:
        return 'none'
    if docs and all(isinstance(d, dict) and d.get('identifier') and d.get('archive_code') for d in docs):
        return 'hits'
    return 'unknown'


def is_record(doc):
    return isinstance(doc, dict) and bool(_list(doc.get('Person'))) and \
        all(isinstance(p, dict) and p.get('@pid') for p in _list(doc.get('Person')))


def is_gedcom(page):
    return page.lstrip('\ufeff').startswith('0 HEAD')


def search_candidates(doc):
    out = []
    for d in doc['response'].get('docs') or []:
        kind = event_kind(d.get('eventtype'))
        date = d.get('eventdate') or {}
        y = _year(date.get('year')) if isinstance(date, dict) else None
        place = ', '.join(_list(d.get('eventplace')))
        role = d.get('relationtype') or ''
        out.append({'source': SOURCE, 'id': '%s:%s' % (d['archive_code'], d['identifier']),
                    'name': (d.get('personname') or '').strip(), 'role': role, 'principal': is_principal(role, kind),
                    'given': None, 'surnames': None,
                    'events': {kind: {'year': y, 'year_to': y, 'place': place}} if y or place else {},
                    'relatives': [], 'url': citation_url(d['archive_code'], d['identifier']),
                    'archive': d.get('archive_org') or d.get('archive') or '', 'sourcetype': d.get('sourcetype') or '',
                    'eventtype': d.get('eventtype') or ''})
    return out


def _person_name(p):
    n = p.get('PersonName') or {}
    given = (n.get('PersonNameFirstName') or '').strip()
    sur = ' '.join(x for x in (n.get('PersonNamePrefixLastName'), n.get('PersonNameLastName')) if x).strip()
    return ' '.join(x for x in (given, sur) if x), given or None, [sur] if sur else None


def _ev_date(d):
    d = d or {}
    return _year(d.get('Year')) or _year(d.get('LiteralDate'))


def record_candidates(doc, archive, uuid):
    """Every person of the record, each with its role, the record's event and the others as relatives."""
    events = {e.get('@eid'): e for e in _list(doc.get('Event')) if isinstance(e, dict)}
    roles = {}
    for r in _list(doc.get('RelationEP')):
        if isinstance(r, dict):
            roles.setdefault(r.get('PersonKeyRef'), (r.get('RelationType') or '', r.get('EventKeyRef')))
    people = []
    for p in _list(doc.get('Person')):
        name, given, sur = _person_name(p)
        role, eid = roles.get(p['@pid'], ('', None))
        ev = events.get(eid) or (next(iter(events.values())) if events else {})
        people.append((p, name, given, sur, role, ev))
    out = []
    for p, name, given, sur, role, ev in people:
        kind = event_kind(ev.get('EventType'))
        y = _ev_date(ev.get('EventDate'))
        evs = {}
        if y or ev.get('EventPlace'):
            evs[kind] = {'year': y, 'year_to': y, 'place': ((ev.get('EventPlace') or {}).get('Place') or '')}
        by = _ev_date(p.get('BirthDate'))
        if (by or p.get('BirthPlace')) and 'birth' not in evs:
            evs['birth'] = {'year': by, 'year_to': by, 'place': (p.get('BirthPlace') or {}).get('Place') or ''}
        rels = [{'role': ROLE_EN.get(_fold(r2), _fold(r2) or 'other'), 'name': n2}
                for q, n2, _, _, r2, _ in people if q is not p and n2]
        c = {'source': SOURCE, 'id': '%s:%s:%s' % (archive, uuid, p['@pid']), 'name': name,
             'role': ROLE_EN.get(_fold(role), _fold(role)), 'principal': is_principal(role, kind),
             'given': given, 'surnames': sur, 'events': evs, 'relatives': rels, 'url': citation_url(archive, uuid)}
        if p.get('Profession'):
            c['occupation'] = p['Profession'] if isinstance(p['Profession'], str) else str(p['Profession'])
        out.append(c)
    return out


def add_search_args(sp):
    sp.add_argument('--name', required=True, help='the name as the site reads it: "Jan Jansen", "Jansen", or two '
                    'people joined by "&" ("Jan Jansen & Maria Bakker")')
    sp.add_argument('--period', default='', help='a year or a range, "1850" or "1850-1860"')
    sp.add_argument('--eventplace', default='', help='the place of the event, as the archive typed it')
    sp.add_argument('--archive', default='', help='an archive code (the part before ":" in a record id)')
    sp.add_argument('--sourcetype', default='', help='a source type as the site lists it')
    sp.add_argument('--relationtype', default='', help='a role, as the site lists it (Bruidegom, Bruid, Kind ...)')
    sp.add_argument('--country', default='', help='a country code: nl, be, fr, sr ...')
    sp.add_argument('--count', type=int, default=25, choices=range(1, 101), metavar='1-100')
    sp.add_argument('--start', type=int, default=0, help='row offset, at most 10000 (the API refuses deeper)')


def add_record_args(sp):
    sp.add_argument('id', help='ARCHIVE:UUID, or the record\'s https://www.openarchieven.nl/ URL')
    sp.add_argument('--gedcom', action='store_true', help='also save the record as GEDCOM 7.0 (<key>.ged)')


def search(a, R):
    url = search_url(a.name, a.period, a.eventplace, a.archive, a.sourcetype, a.relationtype, a.country, a.count,
                     a.start)
    off = max(0, min(int(a.start), 10000))
    status, rh, body, cached = gwt_reader.get_api(R, url, {'accept': ACCEPT},
                                                  expect=lambda d: verdict(d, off) != 'unknown',
                                                  refresh=a.refresh)
    doc = json.loads(body)
    cands = search_candidates(doc)
    env = gwt_reader.envelope(url, None, json_body=doc, verdict=verdict(doc, off), status=status, from_cache=cached)
    words = [SOURCE, a.name, a.period, a.eventplace, a.archive, a.sourcetype, a.relationtype, a.country, a.start]
    base = gwt_reader.write_search(a.out, gwt_reader.search_key(url, words), env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    print('results\t%s total, %d read%s%s' % (doc['response']['number_found'], len(cands), gwt_reader.past_end(cands, off),
                                            ' (cache)' if cached else ''), flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY


def record(a, R):
    rid = record_id(a.id)
    if not rid:
        print('gwt-read: openarch: %r is not ARCHIVE:UUID or a record URL' % a.id, file=sys.stderr, flush=True)
        return EXIT_USAGE
    archive, uuid = rid
    url = record_url(archive, uuid)
    status, rh, body, cached = gwt_reader.get_api(R, url, {'accept': ACCEPT}, expect=is_record, refresh=a.refresh)
    doc = json.loads(body)
    cands = record_candidates(doc, archive, uuid)
    env = gwt_reader.envelope(url, None, json_body=doc, citation=citation_url(archive, uuid), status=status,
                              from_cache=cached)
    key = gwt_reader.search_key(url, [SOURCE, archive, uuid])
    if a.gedcom:
        gurl = gedcom_url(archive, uuid)
        _, _, gbody, _ = gwt_reader.get_api(R, gurl, {'accept': 'text/plain'}, refresh=a.refresh, page=is_gedcom)
        gwt_reader.write_atomic(os.path.join(a.out, key + '.ged'), gbody)
        env['gedcom'] = gurl
    base = gwt_reader.write_search(a.out, key, env, cands)
    gwt_reader.print_paths(SOURCE, base, print)
    if a.gedcom:
        print('gedcom\t%s.ged' % base, flush=True)
    print('results\t%d persons in %s%s' % (len(cands), citation_url(archive, uuid), ' (cache)' if cached else ''),
          flush=True)
    return gwt_reader.EXIT_OK if cands else gwt_reader.EXIT_EMPTY
