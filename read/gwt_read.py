#!/usr/bin/env python3
"""gwt-read: run a record search or read one record on a record site, slowly, cached, and stopping on any block.

  gwt-read pares search [--given G] [--surname S [--surname S2]] [--words "W ..."] --out DIR
  gwt-read pares record ID --out DIR                  ID: a PARES description id or its URL
  gwt-read familysearch search [--given G] [--surname S [--surname S2]] [--by Y] [--dy Y] [--years N]
                               [--place P] [--father "G S"] [--mother "G S"] [--collection ID]
                               [--count N] --out DIR
  gwt-read familysearch record ARK --out DIR          ARK: 1:1:XXXX-XXX or its https://.../ark:/61903/ URL

PARES (the Spanish state archives' catalogue) is public: no account and no token.

The FamilySearch session token is the one the signed-in browser already holds (its `fssessionid` cookie), read from
$GWT_FS_TOKEN or the file named in $GWT_FS_TOKEN_FILE. The tool never asks for a password and never logs in.

Every request is served from gwt-fscache when it can be, and otherwise waits for its slot in the site's or
account's gwt-gate (FamilySearch one request every 30 s at best, PARES one every 15 s, see docs/read.md), so a
search already run costs nothing.

Writes <out>/<key>.json (the answer) and <out>/<key>.candidates.json (what gwt-rank reads), and prints
`source`, `evidence`, `candidates` and `results` lines. It never writes the ledger.

Exit codes: 0 ok, 1 an error answer (nothing saved), 2 no results, 3 rate limit, block, captcha or empty page
(HTTP 429: every lane on the account pauses; HTTP 403: the account's gate is blocked for 24 h; do other
work), 4 signed out or the token
expired (get a fresh token; never retry in a loop), 6 the account's gate is paused (nothing was requested),
64 usage (no token).
"""
import argparse, json, os, re, sys, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
try:                     # installed: the other tools are modules beside this one
    import gwt_reader
    import gwt_pares
    from gwt_fscache import FSCache, FAMILYSEARCH_RULES, PARES_RULES
    from gwt_gate import Gate, Paused
    import gwt_rank
except ImportError:      # a clone: they live in ../gate, ../fscache, ../rank
    for sub in ('read', 'gate', 'fscache', 'rank'):
        p = os.path.join(HERE, '..', sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    import gwt_reader
    import gwt_pares
    from gwt_fscache import FSCache, FAMILYSEARCH_RULES, PARES_RULES
    from gwt_gate import Gate, Paused
    import gwt_rank

from gwt_reader import EXIT_OK, EXIT_EMPTY, EXIT_PAUSED  # noqa: E402

FS_HOST = 'https://www.familysearch.org'
SEARCH_PATH = '/service/search/hr/v2/personas'
ARK = re.compile(r'(?:ark:/61903/)?(1:1:[A-Z0-9]+(?:-[A-Z0-9]+)+)(?:[/?#]|$)')
ACCEPT_SEARCH = 'application/json'
ACCEPT_RECORD = 'application/x-gedcomx-v1+json'
# A slow pace for one account: at best one request every 30 s (120 an hour), never faster than one every
# 20 s, and after a hit one every 120 s. The gate defaults (15 s, floor 8 s) are for a machine-wide pace.
FS_PACE = {'start': 30.0, 'floor': 20.0, 'ceil': 120.0}
SOURCE = 'familysearch'
EXIT_USAGE = 64


def _split(full):
    w = (full or '').split()
    return (w[0], ' '.join(w[1:])) if w else ('', '')


def search_url(given='', surnames=(), by=None, dy=None, years=2, place='', father='', mother='', collection='',
               count=20, offset=0, host=FS_HOST):
    """The search the site's own records search runs, as JSON. Years become a from/to range of +-years."""
    q = []
    if given:
        q.append(('q.givenName', given))
    sur = ' '.join(s for s in surnames if s)
    if sur:
        q.append(('q.surname', sur))
    for key, y in (('birthLikeDate', by), ('deathLikeDate', dy)):
        if y:
            q += [('q.%s.from' % key, str(int(y) - years)), ('q.%s.to' % key, str(int(y) + years))]
    if place:
        q.append(('q.anyPlace', place))
    for role, name in (('father', father), ('mother', mother)):
        g, s = _split(name)
        if g:
            q.append(('q.%sGivenName' % role, g))
        if s:
            q.append(('q.%sSurname' % role, s))
    if collection:
        q.append(('f.collectionId', str(collection)))
    q += [('count', str(count)), ('offset', str(offset))]
    return host + SEARCH_PATH + '?' + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)


def ark_id(arg):
    """'1:1:ABCD-123' from an ark id or URL, else None."""
    m = ARK.search((arg or '').strip())
    return m.group(1) if m else None


def record_url(ark, host=FS_HOST):
    return '%s/ark:/61903/%s' % (host, ark)


def is_search(doc):
    return isinstance(doc, dict) and isinstance(doc.get('entries', []), list) and ('entries' in doc or 'results' in doc)


def is_record(doc):
    return isinstance(doc, dict) and isinstance(doc.get('persons'), list)


def search_candidates(doc):
    """The search JSON as gwt-rank candidate records, through gwt-rank's own GEDCOM X reader."""
    return gwt_rank.gedcomx_rows(doc)


def record_candidates(doc, ark):
    """One record's GEDCOM X as the candidate records gwt-rank reads: the record's principal person."""
    return gwt_rank.gedcomx_rows({'entries': [{'id': ark.split(':')[-1], 'content': {'gedcomx': doc}}]})


# ---------------------------------------------------------------- network


FINAL_URL_HEADER = 'x-gwt-final-url'


def urllib_fetcher(token, user_agent, urlopen=urllib.request.urlopen):
    """A gwt-fscache fetcher that adds the token itself (when there is one), so the headers the cache sees
    never carry it. HTTP errors come back as their status, never raised."""
    def fetch(method, url, headers, data):
        h = dict(headers or {})
        if token:
            h['authorization'] = 'Bearer ' + token
        h['user-agent'] = user_agent
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with urlopen(req, timeout=60) as r:
                rh, body = dict(r.headers), r.read()
                final = r.geturl()
                if final and final != url:
                    rh[FINAL_URL_HEADER] = final    # a redirect; the cache keeps it with the answer
                return r.status, rh, body
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers or {}), e.read()
        except (urllib.error.URLError, OSError) as e:
            return 0, {}, str(e).encode()
    return fetch


def token_from_env():
    tok = os.environ.get('GWT_FS_TOKEN', '').strip()
    path = os.environ.get('GWT_FS_TOKEN_FILE')
    if not tok and path:
        try:
            with open(os.path.expanduser(path)) as f:
                tok = f.read().strip()
        except OSError:
            return ''
    return tok


# ---------------------------------------------------------------- commands


def out(msg):
    print(msg, flush=True)


def err(msg):
    print('gwt-read: ' + msg, file=sys.stderr, flush=True)


def _save(a, R, url, accept, expect, key, to_cands, total_of):
    status, rh, body, cached = R.get(url, {'accept': accept}, expect=expect, refresh=a.refresh)
    doc = json.loads(body)
    cands = to_cands(doc)
    env = gwt_reader.envelope(url, (rh or {}).get(FINAL_URL_HEADER), json_body=doc, status=status,
                              from_cache=cached)
    base = gwt_reader.write_search(a.out, key, env, cands)
    gwt_reader.print_paths(SOURCE, base, out)
    total = total_of(doc)
    out('results\t%s total, %d read%s' % ('?' if total is None else total, len(cands), ' (cache)' if cached else ''))
    return EXIT_OK if cands else EXIT_EMPTY


def cmd_search(a, R):
    if not (a.given or a.surname):
        err('give --given, --surname or both')
        return EXIT_USAGE
    url = search_url(a.given, a.surname, a.by, a.dy, a.years, a.place, a.father, a.mother, a.collection, a.count)
    key = gwt_reader.search_key(url, ['fs', a.given] + list(a.surname) + [a.by])
    return _save(a, R, url, ACCEPT_SEARCH, is_search, key, search_candidates, lambda d: d.get('results'))


def cmd_record(a, R):
    ark = ark_id(a.ark)
    if not ark:
        err('%r is not a FamilySearch record ark (1:1:XXXX-XXX)' % a.ark)
        return EXIT_USAGE
    key = 'fs-record-' + ark.replace(':', '-')
    return _save(a, R, record_url(ark), ACCEPT_RECORD, is_record, key, lambda d: record_candidates(d, ark),
                 lambda d: None)


def cmd_pares_search(a, R):
    if not (a.given or a.surname or a.words):
        err('give --given, --surname, --words or any of them')
        return EXIT_USAGE
    url = gwt_pares.search_url(a.given, a.surname, a.words)
    h = {'accept': gwt_pares.ACCEPT, 'accept-language': gwt_pares.LANGUAGE}
    # the site answers a search only inside a session: unless the answer is already cached, fetch the search
    # form first (through the gate like any request) so the cookie jar holds the session cookie
    if a.refresh or R.cache.lookup('GET', url, h) is None:
        R.get(gwt_pares.FORM_URL, h, refresh=True, page=gwt_pares.is_form)
    key = gwt_reader.search_key(url, ['pares', a.given] + list(a.surname) + [a.words])
    return _save_page(a, R, url, h, gwt_pares.is_results, key, gwt_pares.search_candidates, gwt_pares.total)


def cmd_pares_record(a, R):
    did = gwt_pares.description_id(a.id)
    if not did:
        err('%r is not a PARES description id (digits) or its URL' % a.id)
        return EXIT_USAGE
    h = {'accept': gwt_pares.ACCEPT, 'accept-language': gwt_pares.LANGUAGE}
    return _save_page(a, R, gwt_pares.description_url(did), h, gwt_pares.is_description, 'pares-record-' + did,
                      lambda p: gwt_pares.record_candidates(p, did), lambda p: None)


def _save_page(a, R, url, h, page_ok, key, to_cands, total_of):
    status, rh, body, cached = R.get(url, h, refresh=a.refresh, page=page_ok)
    page = body.decode('utf-8', 'replace')
    cands = to_cands(page)
    env = gwt_reader.envelope(url, (rh or {}).get(FINAL_URL_HEADER), text=gwt_pares.text(page), html=page,
                              status=status, from_cache=cached)
    base = gwt_reader.write_search(a.out, key, env, cands)
    gwt_reader.print_paths(gwt_pares.SOURCE, base, out)
    total = total_of(page)
    out('results\t%s total, %d read%s' % ('?' if total is None else total, len(cands), ' (cache)' if cached else ''))
    return EXIT_OK if cands else EXIT_EMPTY


def _common(sp, ua_env):
    sp.add_argument('--lane', default=os.environ.get('GWT_AGENT', 'gwt-read'), help='who is asking, for the gate log')
    sp.add_argument('--cache-dir', default=os.environ.get('GWT_FSCACHE_DIR', '~/.cache/gwt-fscache'))
    sp.add_argument('--user-agent', default=os.environ.get(ua_env, 'gramps-web-toolkit (+https://github.com/GeiserX/gramps-web-toolkit)'))
    sp.add_argument('--refresh', action='store_true', help='skip the cache (the request still waits for the gate)')


def parser():
    p = argparse.ArgumentParser(prog='gwt-read', description=__doc__.split('\n')[0],
                                epilog=__doc__[__doc__.index('Exit codes:'):],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sites = p.add_subparsers(dest='site', required=True)
    pa = sites.add_parser('pares', help="PARES, the Spanish state archives' catalogue; public, no account")
    _common(pa, 'GWT_PARES_USER_AGENT')
    pa.add_argument('--gate-dir', help='default ~/.cache/gwt-gate-pares')
    psub = pa.add_subparsers(dest='cmd', required=True)
    s = psub.add_parser('search', help='a catalogue search; writes the page and its candidates')
    s.add_argument('--given', default='')
    s.add_argument('--surname', action='append', default=[], help='first surname, then again for the second')
    s.add_argument('--words', default='', help='more words to find anywhere in a description')
    s.add_argument('--out', required=True)
    r = psub.add_parser('record', help='one archival description by its id')
    r.add_argument('id')
    r.add_argument('--out', required=True)
    fs = sites.add_parser('familysearch', help='FamilySearch historical records, with your own session token')
    fs.add_argument('--account', default=os.environ.get('GWT_FS_ACCOUNT', 'default'),
                    help='names the account; each account gets its own gate (pace)')
    _common(fs, 'GWT_FS_USER_AGENT')
    fs.add_argument('--gate-dir', help='default ~/.cache/gwt-gate-fs-<account>')
    sub = fs.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('search', help='a records search; writes the answer and its candidates')
    s.add_argument('--given', default='')
    s.add_argument('--surname', action='append', default=[], help='first surname, then again for the second')
    s.add_argument('--by', type=int, help='birth or baptism year')
    s.add_argument('--dy', type=int, help='death or burial year')
    s.add_argument('--years', type=int, default=2, help='search each year +-N (default 2)')
    s.add_argument('--place', default='', help='any place in the record')
    s.add_argument('--father', default='', help='"Given Surname"')
    s.add_argument('--mother', default='', help='"Given Surname"')
    s.add_argument('--collection', default='', help='a FamilySearch collection id')
    s.add_argument('--count', type=int, default=20, choices=range(1, 101), metavar='1-100')
    s.add_argument('--out', required=True)
    r = sub.add_parser('record', help='one indexed record by its ark')
    r.add_argument('ark')
    r.add_argument('--out', required=True)
    return p


def main(argv=None, urlopen=None, gate=None):
    a = parser().parse_args(argv)
    if a.site == 'pares':
        # cookies live in memory for this run only: the session cookie never reaches the cache or a file
        urlopen = urlopen or urllib.request.build_opener(urllib.request.HTTPCookieProcessor()).open
        gate = gate or Gate(a.gate_dir or '~/.cache/gwt-gate-pares', **gwt_pares.PACE)
        R = gwt_reader.Reader(FSCache(a.cache_dir, rules=PARES_RULES), gate, a.lane,
                              urllib_fetcher(None, a.user_agent, urlopen), log=err)
        return _run(R, {'search': cmd_pares_search, 'record': cmd_pares_record}[a.cmd], a)
    urlopen = urlopen or urllib.request.urlopen
    token = token_from_env()
    if not token:
        err('no session token: set GWT_FS_TOKEN, or GWT_FS_TOKEN_FILE to a file holding it (mode 600)')
        return EXIT_USAGE
    acct = gwt_reader.slug(a.account)
    gate = gate or Gate(a.gate_dir or '~/.cache/gwt-gate-fs-%s' % acct, **FS_PACE)
    cache = FSCache(a.cache_dir, rules=FAMILYSEARCH_RULES)
    R = gwt_reader.Reader(cache, gate, a.lane, urllib_fetcher(token, a.user_agent, urlopen), log=err)
    return _run(R, {'search': cmd_search, 'record': cmd_record}[a.cmd], a)


def _run(R, cmd, a):
    try:
        return cmd(a, R)
    except gwt_reader.Stop as e:
        err(str(e))
        return e.exit_code
    except Paused as e:
        err('%s; do other work' % e)
        return EXIT_PAUSED


if __name__ == '__main__':
    sys.exit(main())
