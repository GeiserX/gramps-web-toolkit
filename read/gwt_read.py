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
  gwt-read SOURCE control                             the source's canary searches, read without the cache
  gwt-read SOURCE search ... --controls before|after|both

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

Exit codes: 0 ok, 1 an error answer (nothing saved; insee: a file that cannot be read or holds no line), 2 no results
(insee: also a line out of INSEE's layout, with nothing written: no candidates file means it was not "none"), 3 rate limit, block, captcha or empty page
(HTTP 429: every lane on the account pauses; HTTP 403: the account's gate is blocked for 24 h; do other
work; also a canary that read less than its minimum: the run is void), 4 signed out or the token
expired (get a fresh token; never retry in a loop), 5 restricted (a FamilySearch record refused to this access
level, not a block; the gate is not told), 6 the account's gate is paused (nothing was requested), 64 usage (no token).
"""
import argparse, contextlib, io, json, os, re, shutil, sys, tempfile, urllib.error, urllib.parse, urllib.request

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

from gwt_reader import EXIT_OK, EXIT_ERROR, EXIT_EMPTY, EXIT_PAUSED  # noqa: E402

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


# Fields that hold a JSON object in their text, and the key in it that holds the event place as typed: some
# collections keep the typed place only there.
_PLACE_IN_JSON = {'EXT_DATA': 'EVENT_PLACE_ALT_ORIG', 'EXT_DATA_ORIG': 'EVENT_PLACE_ALT_ORIG',
                  'FS_UNSUPPORTED': 'EVENT_PLACE_SUP'}


def _typed_place(lab, t):
    """The typed place one field holds, or None."""
    if not isinstance(t, str) or not t:
        return None
    if lab in ('EVENT_PARISH_ORIG', 'CP_MAR_PLACE_ORIG') or (lab.startswith('EVENT_PLACE') and lab.endswith('_ORIG')):
        return t
    if lab in _PLACE_IN_JSON and t.lstrip().startswith('{'):
        try:
            v = json.loads(t).get(_PLACE_IN_JSON[lab])
        except (ValueError, AttributeError):
            return None
        return v if isinstance(v, str) and v else None
    return None


def place_orig(gedcomx):
    """The place of the record's event as the index typed it, in the order found: every EVENT_PLACE..._ORIG field
    (the parish and town as written, which the standard place often drops), EVENT_PARISH_ORIG and CP_MAR_PLACE_ORIG,
    and the typed place some collections keep inside the JSON text of EXT_DATA, EXT_DATA_ORIG or FS_UNSUPPORTED."""
    found = []

    def walk(o):
        if isinstance(o, dict):
            t = _typed_place(str(o.get('labelId', '')), o.get('text') or o.get('value'))
            if t and t not in found:
                found.append(t)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(gedcomx)
    return found


def _with_place_orig(rows, doc):
    """Adds `place_orig` to each candidate whose record has one; rows without it are written as before."""
    by_id = {e.get('id', ''): place_orig((e.get('content') or {}).get('gedcomx') or {}) for e in doc.get('entries') or []}
    for c in rows:
        if by_id.get(c['id']):
            c['place_orig'] = by_id[c['id']]
    return rows


def search_candidates(doc):
    """The search JSON as gwt-rank candidate records, through gwt-rank's own GEDCOM X reader. Each keeps its role,
    its principal flag and the event place as typed (`place_orig`), which the evidence checks read."""
    return _with_place_orig(gwt_rank.gedcomx_rows(doc), doc)


def record_candidates(doc, ark):
    """One record's GEDCOM X as the candidate records gwt-rank reads: the record's principal person."""
    wrapped = {'entries': [{'id': ark.split(':')[-1], 'content': {'gedcomx': doc}}]}
    return _with_place_orig(gwt_rank.gedcomx_rows(wrapped), wrapped)


# A 403 for one record is not always a block: a record the custodian restricts answers 403 to every account of
# this access level. FamilySearch says so for one artifact in the 403 body ("Access to artifact denied"); when a
# body names Themis permissions instead, a set without ThemisPrmAnyone means the same. Both are best effort:
# --control-ark is the check that holds whatever the body says.
_THEMIS = re.compile(r'ThemisPrm[A-Za-z]+')
_ARTIFACT_DENIED = 'access to artifact denied'


def access_of(body):
    """'restricted' when a 403 body refuses this one artifact, or names its permissions and none is
    ThemisPrmAnyone; else None."""
    text = (body or b'').decode('utf-8', 'replace')
    if _ARTIFACT_DENIED in text.lower():
        return 'restricted'
    perms = set(_THEMIS.findall(text))
    return 'restricted' if perms and 'ThemisPrmAnyone' not in perms else None


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


def _save(a, R, url, accept, expect, key, to_cands, total_of, on_403=None):
    status, rh, body, cached = R.get(url, {'accept': accept}, expect=expect, refresh=a.refresh, on_403=on_403)
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


def _fs_on_403(a, R, ark):
    """Is this record's 403 its own access? Its permissions first (no request); then, with --control-ark, the
    control record read again through the gate: only when it answers is the account fine and the record restricted.
    If the control read fails in any way (refused, a 5xx, the wrong shape, a paused gate), the first 403 stays a
    block."""
    def on_403(body):
        if access_of(body):
            return 'restricted'
        control = ark_id(getattr(a, 'control_ark', ''))
        if control:          # cmd_record has refused a control equal to the record
            try:
                R.get(record_url(control), {'accept': ACCEPT_RECORD}, expect=is_record, refresh=True)
            except gwt_reader.Stop as e:
                if e.verdict == 'blocked':
                    raise           # the control's own 403 has blocked the gate already: that is the block
                return None
            except Paused:
                return None
            return 'restricted'
        return None
    return on_403


def cmd_record(a, R):
    ark = ark_id(a.ark)
    if not ark:
        err('%r is not a FamilySearch record ark (1:1:XXXX-XXX)' % a.ark)
        return EXIT_USAGE
    control = getattr(a, 'control_ark', '')
    if control and ark_id(control) in (None, ark):
        err('--control-ark %r must be a FamilySearch record ark (1:1:XXXX-XXX) other than the one you read' % control)
        return EXIT_USAGE
    key = 'fs-record-' + ark.replace(':', '-')
    try:
        return _save(a, R, record_url(ark), ACCEPT_RECORD, is_record, key, lambda d: record_candidates(d, ark),
                     lambda d: None, on_403=_fs_on_403(a, R, ark))
    except gwt_reader.Restricted as e:
        err(str(e))
        perms = sorted(set(_THEMIS.findall(e.body.decode('utf-8', 'replace'))))
        env = gwt_reader.envelope(record_url(ark), None, status=e.status, access='restricted', permissions=perms)
        base = gwt_reader.write_search(a.out, key, env, [])
        gwt_reader.print_paths(SOURCE, base, out)
        out('results\trestricted (HTTP 403 for this record only; no gate hit)')
        return gwt_reader.EXIT_RESTRICTED


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
    """The candidates of one phrase over up to `pages` pages of results, how many rows the site found, and whether
    every page read came from the cache rather than the site."""
    h = {'accept': gwt_boe.ACCEPT, 'accept-language': gwt_boe.LANGUAGE}
    url = gwt_boe.search_url(phrase, a.year_from, a.year_to)
    cands, found, first, cached_all = [], 0, url, True
    for n in range(pages):
        status, rh, body, cached = R.get(url, h, refresh=a.refresh, page=gwt_boe.is_results)
        cached_all = cached_all and cached
        page = body.decode('utf-8', 'replace')
        if n == 0:
            found = gwt_boe.total(page)
        cands += gwt_boe.candidates(page, phrase, a.near, born)
        url = gwt_boe.next_url(page)
        if not url or len(cands) >= found:
            break
    return first, cands, found, page, cached_all


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
    first, cands, found, page, cached = _boe_read(a, R, phrase, max(1, min(a.pages, 5)), a.born)
    _boe_pdf_context(a, R, cands, phrase)
    _boe_verdicts(cands)
    words = ['boe', phrase, str(a.pages), str(a.pdf_context), str(a.born), str(a.parents)] + sorted(a.near)
    # the readable part is cut at 60 characters, so the options go into the hash too: a long name rerun with other
    # --born, --parents or --near still gets its own file
    key = gwt_reader.search_key('\n'.join([first] + words), words)
    env = gwt_reader.envelope(first, None, text=gwt_boe.text(page), total=found, from_cache=cached)
    base = gwt_reader.write_search(a.out, key, env, cands)
    gwt_reader.print_paths(gwt_boe.SOURCE, base, out)
    out('results\t%d of %d%s' % (len(cands), found, ' (cache)' if cached else ''))
    for v in ('consistent', 'open', 'excluded'):
        out('%s\t%d' % (v, sum(1 for c in cands if c['verdict'] == v)))
    return EXIT_OK if cands else EXIT_EMPTY


def cmd_insee_search(a, R=None):
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
    except gwt_insee.EmptyFile as e:
        err('%s; nothing was written' % e)
        return EXIT_ERROR
    except OSError as e:
        err('cannot read %s: %s; nothing was written' % (e.filename or 'a file', e.strerror or e))
        return EXIT_ERROR      # a file that cannot be read is an error, never "no results"
    for f in files:
        if f['malformed']:
            err('%s: %d line%s %s a value out of INSEE\'s own rules (month over 12, day over 31, or a name with no '
                '"*"), kept as written and marked "malformed"' % (f['file'], f['malformed'],
                                                                  '' if f['malformed'] == 1 else 's',
                                                                  'keeps' if f['malformed'] == 1 else 'keep'))
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


# ---------------------------------------------------------------- the sources gwt-read knows
# Each source is a SPEC (see gwt_reader.spec_problems and read/_template.py) plus the functions that add its
# options and run its commands. The older sources are declared here; a new one lives in its own module, which
# gwt_reader.SOURCE_MODULES names, with the same keys at module level.


def _site_args(ua_env, gate_dir):
    def add(sp):
        _common(sp, ua_env)
        sp.add_argument('--gate-dir', help='default ' + gate_dir)
    return add


def _fs_site_args(sp):
    sp.add_argument('--account', default=os.environ.get('GWT_FS_ACCOUNT', 'default'),
                    help='names the account; each account gets its own gate (pace)')
    _common(sp, 'GWT_FS_USER_AGENT')
    sp.add_argument('--gate-dir', help='default ~/.cache/gwt-gate-fs-<account>')
    sp.add_argument('--control-ark', default=os.environ.get('GWT_FS_CONTROL_ARK', ''),
                    help='a record your account can read; on a 403 for a record, it is read again (no cache) and, '
                    'when it answers, the 403 is that record\'s access ("restricted", exit 5), not a block')


def _pares_search_args(s):
    s.add_argument('--given', default='')
    s.add_argument('--surname', action='append', default=[], help='first surname, then again for the second')
    s.add_argument('--words', default='', help='more words to find anywhere in a description')


def _pares_record_args(r):
    r.add_argument('id')


def _genae_search_args(s):
    s.add_argument('--given', default='')
    s.add_argument('--surname', action='append', default=[], help='first surname, then again for the second')
    s.add_argument('--province', default='mu', choices=gwt_genae.PROVINCES)
    s.add_argument('--locality', default='', help="the site's numeric locality id (its own l= field)")
    s.add_argument('--from', dest='year_from', type=int, default=1503)
    s.add_argument('--to', dest='year_to', type=int, default=1986)


def _boe_search_args(s):
    s.add_argument('--name', required=True, help='given name and surname(s), read as one phrase')
    s.add_argument('--parents', help='the parents\' first names the tree holds, "Father y Mother": with --pdf-context, an act that writes other parents is marked')
    s.add_argument('--born', type=int, help='birth year of the person: an act published before it or over 100 years after it is marked IMPLAUSIBLE, never hidden')
    s.add_argument('--pages', type=int, default=1, help='pages of 50 results to read, 1 to 5 (default 1)')
    _boe_filters(s)


def _insee_search_args(s):
    s.add_argument('--files', nargs='+', required=True, help="INSEE deaths files (deces-YYYY.txt), downloaded once")
    s.add_argument('--surname', action='append', default=[], help='a surname to keep; repeat for more, a line '
                   'matching any of them is kept, so one pass serves a branch')


def _fs_search_args(s):
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


def _fs_record_args(r):
    r.add_argument('ark')


def _default_build(src, a, urlopen, gate):
    """The Reader of a public source: its own gate and cache rules, no token."""
    urlopen = urlopen or urllib.request.urlopen
    gate = gate or Gate(a.gate_dir or '~/.cache/gwt-gate-%s' % src['id'], **src['pace'])
    return gwt_reader.Reader(FSCache(a.cache_dir, rules=src['cache_rules']), gate, a.lane,
                             urllib_fetcher(None, a.user_agent, urlopen), log=err)


def _pares_build(src, a, urlopen, gate):
    # cookies live in memory for this run only: the session cookie never reaches the cache or a file
    urlopen = urlopen or urllib.request.build_opener(urllib.request.HTTPCookieProcessor()).open
    return _default_build(src, a, urlopen, gate)


def _fs_build(src, a, urlopen, gate):
    urlopen = urlopen or urllib.request.urlopen
    token = token_from_env()
    if not token:
        err('no session token: set GWT_FS_TOKEN, or GWT_FS_TOKEN_FILE to a file holding it (mode 600)')
        return EXIT_USAGE
    acct = gwt_reader.slug(a.account)
    gate = gate or Gate(a.gate_dir or '~/.cache/gwt-gate-fs-%s' % acct, **FS_PACE)
    cache = FSCache(a.cache_dir, rules=FAMILYSEARCH_RULES)
    return gwt_reader.Reader(cache, gate, a.lane, urllib_fetcher(token, a.user_agent, urlopen), log=err)


# Canaries: a search any working copy of the site answers with at least min_count rows, read without the cache.
# Public and neutral on purpose: very common names, never a family's own surname. The older sources leave the page
# markers empty: they check their pages with their own functions (gwt_pares.is_results, gwt_genae.is_block,
# gwt_boe.is_results), so the markers would be a second copy of the same rule.
LEGACY = (
    {'id': 'pares', 'help': "PARES, the Spanish state archives' catalogue; public, no account",
     'pace': gwt_pares.PACE, 'cache_rules': PARES_RULES,
     # one surname alone is too broad: the site answers "too many results" (a page with no rows) instead
     'controls': [{'argv': ['--given', 'Cristóbal', '--surname', 'Colón'], 'min_count': 10}],
     'result_markers': [], 'negative_markers': [],
     'site_args': _site_args('GWT_PARES_USER_AGENT', '~/.cache/gwt-gate-pares'), 'build': _pares_build,
     'search_help': 'a catalogue search; writes the page and its candidates', 'add_search_args': _pares_search_args,
     'search': cmd_pares_search,
     'record_help': 'one archival description by its id', 'add_record_args': _pares_record_args,
     'record': cmd_pares_record},
    {'id': 'genae', 'help': 'Genae, a volunteer index of parish baptisms, marriages and burials; public',
     'pace': gwt_genae.PACE, 'cache_rules': GENAE_RULES,
     # the site's robots.txt disallows its search pages for every robot: no automated searches beyond the user's own
     'controls': [],      # no canary on genae
     'result_markers': [], 'negative_markers': [],
     'site_args': _site_args('GWT_GENAE_USER_AGENT', '~/.cache/gwt-gate-genae'), 'build': _default_build,
     'search_help': 'a search over baptisms, marriages and burials; first page of each',
     'add_search_args': _genae_search_args, 'search': cmd_genae_search},
    {'id': 'boe', 'help': 'BOE and Gaceta de Madrid, a name in the text of official acts; public',
     'pace': gwt_boe.PACE, 'cache_rules': BOE_RULES,
     'controls': [{'argv': ['--name', 'Manuel Rodríguez'], 'min_count': 10}],
     'result_markers': [], 'negative_markers': [],
     'site_args': _site_args('GWT_BOE_USER_AGENT', '~/.cache/gwt-gate-boe'), 'build': _default_build,
     'search_help': 'one phrase in quotes, the first page of results', 'add_search_args': _boe_search_args,
     'search': cmd_boe_search},
    {'id': 'insee', 'help': "INSEE's French deaths files from data.gouv.fr, read offline; no request",
     'pace': {'start': 0.0, 'floor': 0.0, 'ceil': 0.0}, 'cache_rules': (), 'controls': [],
     'result_markers': [], 'negative_markers': [], 'offline': True,
     'search_help': 'every line of the files whose surname matches, as candidates',
     'add_search_args': _insee_search_args, 'search': cmd_insee_search},
    {'id': 'familysearch', 'help': 'FamilySearch historical records, with your own session token',
     'pace': FS_PACE, 'cache_rules': FAMILYSEARCH_RULES,
     'controls': [{'argv': ['--surname', 'Smith'], 'min_count': 10}],
     'result_markers': [], 'negative_markers': [],
     'site_args': _fs_site_args, 'build': _fs_build,
     'search_help': 'a records search; writes the answer and its candidates', 'add_search_args': _fs_search_args,
     'search': cmd_search,
     'record_help': 'one indexed record by its ark', 'add_record_args': _fs_record_args, 'record': cmd_record},
)
_MODULE_KEYS = ('add_search_args', 'search', 'add_record_args', 'record')


def module_source(mod):
    """A source module as a registry entry: its SPEC plus its command functions."""
    src = dict(mod.SPEC)
    for k in _MODULE_KEYS:
        if hasattr(mod, k):
            src[k] = getattr(mod, k)
    src.setdefault('site_args', _site_args('GWT_%s_USER_AGENT' % src['id'].upper().replace('-', '_'),
                                           '~/.cache/gwt-gate-%s' % src['id']))
    src.setdefault('build', _default_build)
    src.setdefault('search_help', 'a search; writes the answer and its candidates')
    src.setdefault('record_help', 'one record by its id')
    return src


def sources(modules=None):
    """{id: entry} of every source, in the order --help lists them: the older ones, then each module in
    gwt_reader.SOURCE_MODULES that has a SPEC."""
    out = {s['id']: s for s in LEGACY}
    for name in gwt_reader.SOURCE_MODULES if modules is None else modules:
        mod = name if not isinstance(name, str) else __import__(name)
        if hasattr(mod, 'SPEC'):
            src = module_source(mod)
            bad = gwt_reader.spec_problems(src)
            if bad or src['id'] in out:
                raise ValueError('%s: %s' % (getattr(mod, '__name__', mod), '; '.join(bad) or 'id already taken'))
            out[src['id']] = src
    return out


# ---------------------------------------------------------------- canary controls


def _canary(src, sp, ctl, a, R):
    """(rows read, exit code) of one canary search, read without the cache into a scratch folder."""
    with tempfile.TemporaryDirectory() as tmp:
        ns = sp.parse_args(list(ctl['argv']) + ['--out', tmp])
        for k, v in vars(a).items():
            if not hasattr(ns, k):
                setattr(ns, k, v)
        ns.refresh, ns.out = True, tmp
        with contextlib.redirect_stdout(io.StringIO()):
            code = src['search'](ns, R)
        n = 0
        for name in os.listdir(tmp):
            if name.endswith('.candidates.json'):
                with open(os.path.join(tmp, name), encoding='utf-8') as f:
                    n += len(json.load(f)['candidates'])
    return n, code


def run_controls(src, sp, a, R, when='control', undo=None):
    """Run every canary of the source. Prints one `control` line each; EXIT_OK when every one reads at least its
    minimum, else EXIT_WALL with a `void` line (after `undo()`, which removes what the bracketed search left): a run
    bracketed by a short canary proves nothing."""
    short = 0
    for ctl in src['controls']:
        n, code = _canary(src, sp, ctl, a, R)
        ok = code in (EXIT_OK, EXIT_EMPTY) and n >= ctl['min_count']
        short += not ok
        out('control\t%s\t%s\t%d read, want at least %d\t%s' % (when, ' '.join(ctl['argv']), n, ctl['min_count'],
                                                               'ok' if ok else 'SHORT'))
    if short:
        if undo:
            undo()
        out('void\t%s: %d of %d canaries read less than their minimum; read nothing here as "no results"'
            % (src['id'], short, len(src['controls'])))
        return gwt_reader.EXIT_WALL
    return EXIT_OK


def parser(srcs=None):
    srcs = sources() if srcs is None else srcs
    p = argparse.ArgumentParser(prog='gwt-read', description=__doc__.split('\n')[0],
                                epilog=__doc__[__doc__.index('Exit codes:'):],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sites = p.add_subparsers(dest='site', required=True)
    for src in srcs.values():
        sp = sites.add_parser(src['id'], help=src['help'])
        if src.get('site_args'):
            src['site_args'](sp)
        sub = sp.add_subparsers(dest='cmd', required=True)
        s = sub.add_parser('search', help=src['search_help'])
        src['add_search_args'](s)
        if src['controls']:
            s.add_argument('--controls', choices=('before', 'after', 'both'),
                           help='run the source\'s canary searches (no cache) before, after or around this one; '
                           'a short canary makes the run void (exit 3)')
        s.add_argument('--out', required=True)
        if src.get('record'):
            r = sub.add_parser('record', help=src['record_help'])
            src['add_record_args'](r)
            r.add_argument('--out', required=True)
        if src['controls']:
            sub.add_parser('control', help='run the canary searches (no cache): exit 0 when each reads at least its '
                           'minimum, 3 (void) otherwise')
        src['_search_parser'] = s
    return p


def main(argv=None, urlopen=None, gate=None, srcs=None):
    srcs = sources() if srcs is None else srcs
    a = parser(srcs).parse_args(argv)
    src = srcs[a.site]
    if src.get('offline'):
        return src['search'](a, None)       # offline: no gate, no cache, no fetcher
    R = src['build'](src, a, urlopen, gate)
    if isinstance(R, int):
        return R
    if a.cmd == 'control':
        return _run(R, lambda a, R: run_controls(src, src['_search_parser'], a, R), a)
    cmd = src[a.cmd]
    when = getattr(a, 'controls', None)
    if not when:
        return _run(R, cmd, a)

    def bracketed(a, R):
        a.refresh = True    # the canaries prove the site works now, so the search they bracket is read now too
        if when in ('before', 'both'):
            c = run_controls(src, src['_search_parser'], a, R, 'before')
            if c:
                return c
        files, stored = _listing(a.out), []
        store = R.cache.store
        R.cache.store = lambda *s: stored.append(s[:4]) or store(*s)
        try:
            code = cmd(a, R)
        finally:
            R.cache.store = store
        if when in ('after', 'both') and code in (EXIT_OK, EXIT_EMPTY):
            undo = lambda: _undo(a.out, files, R.cache, stored)
            try:
                c = run_controls(src, src['_search_parser'], a, R, 'after', undo)
            except (gwt_reader.Stop, Paused):
                undo()      # a canary that was stopped proved nothing either: the run is void all the same
                raise
            if c:
                return c
        return code
    return _run(R, bracketed, a)


def _listing(folder):
    """{file name: mtime} of a folder, empty when it does not exist yet."""
    try:
        return {e.name: e.stat().st_mtime_ns for e in os.scandir(folder) if e.is_file()}
    except OSError:
        return {}


def _undo(folder, before, cache, stored):
    """A void run leaves nothing that reads as "none": the files the search wrote and the answers it cached go."""
    for name, mtime in _listing(folder).items():
        if before.get(name) != mtime:
            os.remove(os.path.join(folder, name))
            out('removed\t%s' % os.path.join(folder, name))
    for method, url, headers, data in stored:
        cache.forget(method, url, headers, data)


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
