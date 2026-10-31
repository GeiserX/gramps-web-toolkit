#!/usr/bin/env python3
"""gwt-read: run a record search or read one record on a record site, slowly, cached, and stopping on any block.

  gwt-read pares search [--given G] [--surname S [--surname S2]] [--words "W ..."] --out DIR
  gwt-read pares record ID --out DIR                  ID: a PARES description id or its URL
  gwt-read genae search [--given G] [--surname S [--surname S2]] [--province mu] [--locality L] [--from Y] [--to Y]
                        --out DIR
  gwt-read boe search --name "Given Surname1 [Surname2]" [--from Y] [--to Y] [--born Y] [--parents "F y M"]
                      [--pdf-context N] [--near W] [--pages N] --out DIR
  gwt-read insee search --files FILE [FILE ...] --surname S [--surname S2 ...] --out DIR
  gwt-read familysearch search [--given G] [--surname S [--surname S2]] [--by Y] [--dy Y] [--years N]
                               [--place P] [--father "G S"] [--mother "G S"] [--collection ID]
                               [--count N] --out DIR
  gwt-read familysearch record ARK --out DIR          ARK: 1:1:XXXX-XXX or its https://.../ark:/61903/ URL

PARES (the Spanish state archives' catalogue), Genae (a volunteer index of parish baptisms, marriages and
burials) and the BOE and Gaceta de Madrid search are public: no account and no token. Genae rows are an index, never
the record; a BOE hit is a mention of a name in an official act, never a record of a person. `insee` reads INSEE's
French deaths files the user downloaded from data.gouv.fr, offline: no request, no gate, no cache.

The FamilySearch session token is the one the signed-in browser already holds (its `fssessionid` cookie), read from
$GWT_FS_TOKEN or the file named in $GWT_FS_TOKEN_FILE. The tool never asks for a password and never logs in.

Every request is served from gwt-fscache when it can be, and otherwise waits for its slot in the site's or
account's gwt-gate (FamilySearch one request every 30 s at best, PARES one every 15 s, see docs/read.md), so a
search already run costs nothing.

Writes <out>/<key>.json (the answer) and <out>/<key>.candidates.json (what gwt-rank reads), and prints
`source`, `evidence`, `candidates` and `results` lines. It never writes the ledger.

Exit codes: 0 ok, 1 an error answer (nothing saved), 2 no results (insee: also a line out of INSEE's layout or a
file that cannot be read, nothing written), 3 rate limit, block, captcha or empty page
(HTTP 429: every lane on the account pauses; HTTP 403: the account's gate is blocked for 24 h; do other
work), 4 signed out or the token
expired (get a fresh token; never retry in a loop), 6 the account's gate is paused (nothing was requested),
64 usage (no token).
"""
import argparse, json, os, re, shutil, sys, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
try:                     # installed: the other tools are modules beside this one
    import gwt_reader
    import gwt_pares
    import gwt_genae
    import gwt_boe
    import gwt_insee
    from gwt_fscache import FSCache, FAMILYSEARCH_RULES, PARES_RULES, GENAE_RULES, BOE_RULES
    from gwt_gate import Gate, Paused
    import gwt_rank
except ImportError:      # a clone: they live in ../gate, ../fscache, ../rank
    for sub in ('read', 'gate', 'fscache', 'rank'):
        p = os.path.join(HERE, '..', sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    import gwt_reader
    import gwt_pares
    import gwt_genae
    import gwt_boe
    import gwt_insee
    from gwt_fscache import FSCache, FAMILYSEARCH_RULES, PARES_RULES, GENAE_RULES, BOE_RULES
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


def cmd_genae_search(a, R):
    if not (a.given or a.surname):
        err('give --given, --surname or both')
        return EXIT_USAGE
    sur = (list(a.surname) + ['', ''])[:2]
    query = gwt_genae.params(a.given, sur[0], sur[1], a.province, a.locality, a.year_from, a.year_to)
    h = {'accept': gwt_genae.ACCEPT, 'accept-language': gwt_genae.LANGUAGE, 'hx-request': 'true'}
    cands, pages, seen = [], {}, set()
    for block in gwt_genae.BLOCKS:
        url = gwt_genae.block_url(block, query)
        status, rh, body, cached = R.get(url, h, refresh=a.refresh, page=lambda p, b=block: gwt_genae.is_block(p, b))
        page = body.decode('utf-8', 'replace')
        n = len(gwt_genae.rows(page))
        pages[block] = {'url': url, 'html': page, 'rows': n, 'cut': gwt_genae.is_cut(n), 'from_cache': cached}
        for c in gwt_genae.candidates(page, block, url):
            if c['id'] not in seen:   # a marriage both spouses match is in the groom and the bride blocks
                seen.add(c['id'])
                cands.append(c)
    key = gwt_reader.search_key(gwt_genae.block_url('bautismos', query), ['genae', a.given] + list(a.surname))
    env = gwt_reader.envelope(gwt_genae.block_url('bautismos', query), None,
                              text=' '.join(gwt_genae.text(p['html']) for p in pages.values()), blocks=pages)
    base = gwt_reader.write_search(a.out, key, env, cands)
    gwt_reader.print_paths(gwt_genae.SOURCE, base, out)
    out('results\t%s' % ', '.join('%s %d%s' % (b, pages[b]['rows'], '+ (cut: narrow the search)' if pages[b]['cut'] else '')
                                   for b in gwt_genae.BLOCKS))
    return EXIT_OK if cands else EXIT_EMPTY


def _boe_phrase(name):
    return ' '.join((name or '').split())


def _boe_read(a, R, phrase, pages, born=None):
    """The candidates of one phrase over up to `pages` pages of results, and how many rows the site found."""
    h = {'accept': gwt_boe.ACCEPT, 'accept-language': gwt_boe.LANGUAGE}
    url = gwt_boe.search_url(phrase, a.year_from, a.year_to)
    cands, found, first = [], 0, url
    for n in range(pages):
        status, rh, body, cached = R.get(url, h, refresh=a.refresh, page=gwt_boe.is_results)
        page = body.decode('utf-8', 'replace')
        if n == 0:
            found = gwt_boe.total(page)
        cands += gwt_boe.candidates(page, phrase, a.near, born)
        url = gwt_boe.next_url(page)
        if not url or len(cands) >= found:
            break
    return first, cands, found, page


def _boe_pdf_context(a, R, cands, phrase):
    """The words around the name in the PDF of the first `--pdf-context` acts that `--born` has not ruled out, one
    request each at the gate's pace. Without `pdftotext` nothing is fetched."""
    n = max(0, min(getattr(a, 'pdf_context', 0), 10))
    if not n:
        return
    if not shutil.which('pdftotext'):
        err('--pdf-context needs pdftotext (poppler-utils); nothing was fetched')
        return
    h = {'accept': 'application/pdf', 'accept-language': gwt_boe.LANGUAGE}
    for c in [c for c in cands if c['url'].endswith('.pdf') and not c.get('implausible')][:n]:
        # an HTML page served with 200 at a PDF URL is not the act: a STOP, never cached for 90 days
        status, rh, body, cached = R.get(c['url'], h, refresh=a.refresh, page=lambda p: p.startswith('%PDF'))
        txt = gwt_boe.pdf_text(body)
        ctx = gwt_boe.context(txt, phrase)
        c['context'] = ctx
        if ctx:
            c['note'] += ' | in the act: ...%s...' % ctx
        else:
            c['note'] += ' [name not whole in the text layer: open the PDF]'
        every = gwt_boe.parents_after(txt, phrase)    # one (father, mother) or None per occurrence of the name
        found = [p for p in every if p]
        if found:
            c['parents_in_act'] = [list(p) for p in found]
            same = gwt_boe.act_parents(every, getattr(a, 'parents', None))
            c['same_parents'] = same
            c['note'] = '[parents in the act: %s%s] ' % ('; '.join('%s y %s' % p for p in found), '' if same is None else
                                                        ', as in the tree' if same else ', NOT the tree\'s: another person?') + c['note']


def _boe_verdicts(cands):
    for c in cands:
        c['verdict'] = gwt_boe.verdict(c)
        c['note'] = '[%s] %s' % (c['verdict'], c['note'])


def cmd_boe_search(a, R):
    phrase = _boe_phrase(a.name)
    if len(phrase.split()) < 2:
        err('give --name with at least a given name and a surname')
        return EXIT_USAGE
    first, cands, found, page = _boe_read(a, R, phrase, max(1, min(a.pages, 5)), a.born)
    _boe_pdf_context(a, R, cands, phrase)
    _boe_verdicts(cands)
    words = ['boe', phrase, str(a.pages), str(a.pdf_context), str(a.born), str(a.parents)] + sorted(a.near)
    # the readable part is cut at 60 characters, so the options go into the hash too: a long name rerun with other
    # --born, --parents or --near still gets its own file
    key = gwt_reader.search_key('\n'.join([first] + words), words)
    env = gwt_reader.envelope(first, None, text=gwt_boe.text(page), total=found)
    base = gwt_reader.write_search(a.out, key, env, cands)
    gwt_reader.print_paths(gwt_boe.SOURCE, base, out)
    out('results\t%d of %d' % (len(cands), found))
    for v in ('consistent', 'open', 'excluded'):
        out('%s\t%d' % (v, sum(1 for c in cands if c['verdict'] == v)))
    return EXIT_OK if cands else EXIT_EMPTY


def cmd_insee_search(a):
    """Every line of the deaths files whose surname matches one --surname, as candidates. Offline: the files are a
    download the user made; nothing here touches the network."""
    surnames = [s for s in a.surname if gwt_insee.fold(s)]
    if not surnames:
        err('give at least one --surname')
        return EXIT_USAGE
    try:
        cands, files = gwt_insee.read_files(a.files, surnames)
    except gwt_insee.LayoutError as e:
        err('%s; nothing was written' % e)
        return EXIT_EMPTY
    except OSError as e:
        err('cannot read %s: %s; nothing was written' % (e.filename or 'a file', e.strerror or e))
        return EXIT_EMPTY
    sums = sorted(f['sha256'] for f in files)
    words = ['insee'] + sorted(gwt_insee.fold(' '.join(surnames)))
    key = gwt_reader.search_key('\n'.join(['insee'] + sums + sorted(' '.join(gwt_insee.fold(s)) for s in surnames)),
                                words)
    env = gwt_reader.envelope('insee:' + ' '.join(f['file'] for f in files), None, files=files, surnames=surnames,
                              text='\n'.join('%s:%d %s' % (c['file'], c['line'], c['name']) for c in cands))
    base = gwt_reader.write_search(a.out, key, env, cands)
    gwt_reader.print_paths(gwt_insee.SOURCE, base, out)
    out('results\t%d of %d lines in %d file%s' % (len(cands), sum(f['lines'] for f in files), len(files),
                                                  '' if len(files) == 1 else 's'))
    return EXIT_OK if cands else EXIT_EMPTY


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


def _boe_filters(sp):
    sp.add_argument('--from', dest='year_from', help='first publication year or yyyy-mm-dd (text search covers 1836 to 1959)')
    sp.add_argument('--to', dest='year_to', help='last publication year or yyyy-mm-dd (text search ends in 1959)')
    sp.add_argument('--pdf-context', type=int, default=0, help='read the PDF of the first N acts (0 to 10, default 0) and add the words '
                    'around the name to the note; one request each, needs pdftotext')
    sp.add_argument('--near', action='append', default=[], help='a word to look for in the title and department, '
                    'marked on the row, never a filter (a place, a trade); repeat for more')


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
    ge = sites.add_parser('genae', help='Genae, a volunteer index of parish baptisms, marriages and burials; public')
    _common(ge, 'GWT_GENAE_USER_AGENT')
    ge.add_argument('--gate-dir', help='default ~/.cache/gwt-gate-genae')
    gsub = ge.add_subparsers(dest='cmd', required=True)
    s = gsub.add_parser('search', help='a search over baptisms, marriages and burials; first page of each')
    s.add_argument('--given', default='')
    s.add_argument('--surname', action='append', default=[], help='first surname, then again for the second')
    s.add_argument('--province', default='mu', choices=gwt_genae.PROVINCES)
    s.add_argument('--locality', default='', help="the site's numeric locality id (its own l= field)")
    s.add_argument('--from', dest='year_from', type=int, default=1503)
    s.add_argument('--to', dest='year_to', type=int, default=1986)
    s.add_argument('--out', required=True)
    bo = sites.add_parser('boe', help='BOE and Gaceta de Madrid, a name in the text of official acts; public')
    _common(bo, 'GWT_BOE_USER_AGENT')
    bo.add_argument('--gate-dir', help='default ~/.cache/gwt-gate-boe')
    bsub = bo.add_subparsers(dest='cmd', required=True)
    s = bsub.add_parser('search', help='one phrase in quotes, the first page of results')
    s.add_argument('--name', required=True, help='given name and surname(s), read as one phrase')
    s.add_argument('--parents', help='the parents\' first names the tree holds, "Father y Mother": with --pdf-context, an act that writes other parents is marked')
    s.add_argument('--born', type=int, help='birth year of the person: an act published before it or over 100 years after it is marked IMPLAUSIBLE, never hidden')
    s.add_argument('--pages', type=int, default=1, help='pages of 50 results to read, 1 to 5 (default 1)')
    _boe_filters(s)
    s.add_argument('--out', required=True)
    ins = sites.add_parser('insee', help="INSEE's French deaths files from data.gouv.fr, read offline; no request")
    isub = ins.add_subparsers(dest='cmd', required=True)
    s = isub.add_parser('search', help='every line of the files whose surname matches, as candidates')
    s.add_argument('--files', nargs='+', required=True, help="INSEE deaths files (deces-YYYY.txt), downloaded once")
    s.add_argument('--surname', action='append', default=[], help='a surname to keep; repeat for more, a line '
                   'matching any of them is kept, so one pass serves a branch')
    s.add_argument('--out', required=True)
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
    if a.site == 'insee':
        return cmd_insee_search(a)      # offline: no gate, no cache, no fetcher
    if a.site == 'pares':
        # cookies live in memory for this run only: the session cookie never reaches the cache or a file
        urlopen = urlopen or urllib.request.build_opener(urllib.request.HTTPCookieProcessor()).open
        gate = gate or Gate(a.gate_dir or '~/.cache/gwt-gate-pares', **gwt_pares.PACE)
        R = gwt_reader.Reader(FSCache(a.cache_dir, rules=PARES_RULES), gate, a.lane,
                              urllib_fetcher(None, a.user_agent, urlopen), log=err)
        return _run(R, {'search': cmd_pares_search, 'record': cmd_pares_record}[a.cmd], a)
    if a.site == 'genae':
        urlopen = urlopen or urllib.request.urlopen
        gate = gate or Gate(a.gate_dir or '~/.cache/gwt-gate-genae', **gwt_genae.PACE)
        R = gwt_reader.Reader(FSCache(a.cache_dir, rules=GENAE_RULES), gate, a.lane,
                              urllib_fetcher(None, a.user_agent, urlopen), log=err)
        return _run(R, cmd_genae_search, a)
    if a.site == 'boe':
        urlopen = urlopen or urllib.request.urlopen
        gate = gate or Gate(a.gate_dir or '~/.cache/gwt-gate-boe', **gwt_boe.PACE)
        R = gwt_reader.Reader(FSCache(a.cache_dir, rules=BOE_RULES), gate, a.lane,
                              urllib_fetcher(None, a.user_agent, urlopen), log=err)
        return _run(R, cmd_boe_search, a)
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
