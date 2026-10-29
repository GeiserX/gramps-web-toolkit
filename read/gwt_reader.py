#!/usr/bin/env python3
"""gwt_reader: the shape every record-site reader in the toolkit writes, and the one path its requests take.

A reader (gwt-mh for MyHeritage, gwt-read for FamilySearch, PARES and Genae) turns one search or one record into files a
lane and the other tools can read without knowing the site:

    <out>/<key>.json             the envelope: what was asked and what came back
                                 {"url", "final_url", "captured_at", "text" and/or "json", site extras...}
    <out>/<key>.candidates.json  {"candidates": [...]}, the normalized records gwt-rank reads
    manifest.tsv                 one row per file saved beside a record (an original image, a page), in the
                                 columns of MANIFEST_COLUMNS, so gwt-rawstore can keep it

and prints, one per line on stdout, `source<TAB><ledger slug>`, `evidence<TAB><path>` and
`candidates<TAB><path>`. The lane writes the ledger row from those lines; a reader never writes the ledger.

Every network request goes through Reader.get: the answer is served from gwt-fscache when fresh, and only on
a miss does it ask gwt-gate for a slot and call the fetcher. An answer that is a block, a captcha, a login
wall or an empty frame is a STOP: it is never stored in the cache, the gate hears about it, and Stop is
raised with a non-zero exit code, so nothing is ever saved as if it were a result.

Session secrets never reach disk: write_search() scrubs every envelope, and the fetcher keeps the token out
of the headers the cache sees. Standard library only.
"""
import datetime, hashlib, json, os, re, struct, unicodedata

EXIT_OK, EXIT_ERROR, EXIT_EMPTY, EXIT_WALL, EXIT_DEAD, EXIT_PAUSED = 0, 1, 2, 3, 4, 6

# ---------------------------------------------------------------- small helpers


def norm_words(text):
    t = unicodedata.normalize('NFKD', text or '')
    t = ''.join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r'[^a-z0-9 ]+', ' ', t).split()


def slug(text, limit=60):
    return '-'.join(norm_words(text))[:limit].strip('-') or 'x'


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def write_atomic(path, data):
    """Write to path.part, then rename: a reader never sees half a file."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + '.part'
    with open(tmp, 'wb') as f:
        f.write(data if isinstance(data, bytes) else data.encode('utf-8'))
    os.replace(tmp, path)


def write_json(path, obj):
    write_atomic(path, json.dumps(obj, ensure_ascii=False, indent=1) + '\n')


# ---------------------------------------------------------------- secrets never reach disk

# Session secrets a page or a search carries: bearer tokens, the FamilyGraph tokens a MyHeritage page embeds
# (fgToken, webClientFgToken, ...), session ids, XSRF and CSRF tokens, and Authorization headers.
_SECRET_NAME = r'\w*(?:fgtoken|xsrftoken|csrftoken|csrf_token|bearer_token|bearertoken|access_token|accesstoken)'
_SECRET_KEY = re.compile(r'^%s$|^authorization$' % _SECRET_NAME, re.I)
_SECRET_RES = (
    re.compile(r'(%s(?:\\*["\'])?\s*[:=]\s*(?:\\*["\'])?)([^"\'&\s,;}\\<]+)' % _SECRET_NAME, re.I),
    re.compile(r'(name="bearer_token"\r?\n\r?\n)([^\r\n]+)'),
    re.compile(r'(\b[Bb]earer\s+)([A-Za-z0-9._~+/=-]{16,})'),
    re.compile(r'((?:[Aa]uthorization)["\']?\s*[:=]\s*["\']?)([^"\'\r\n,}]+)'),
)


def scrub(obj):
    """A copy with every session token (bearer, FamilyGraph, XSRF, CSRF) and Authorization value replaced by REDACTED."""
    if isinstance(obj, dict):
        return {k: ('REDACTED' if _SECRET_KEY.match(k) else scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    if isinstance(obj, str):
        for rx in _SECRET_RES:
            obj = rx.sub(lambda m: m.group(1) + 'REDACTED', obj)
        return obj
    return obj


# ---------------------------------------------------------------- the search files and the lines a lane reads


def envelope(url, final_url=None, text='', json_body=None, **extra):
    """The envelope of one answer. `text` is the visible page text, `json_body` a parsed JSON answer; a site
    adds its own keys (gwt-mh: html, graphql, plan)."""
    env = {'url': url, 'final_url': final_url or url, 'captured_at': now_iso()}
    if text:
        env['text'] = text
    if json_body is not None:
        env['json'] = json_body
    env.update(extra)
    return env


def search_key(normalized_url, words):
    """<slug of the words>-<10 hex of the URL>: readable, and two different searches never collide."""
    h = hashlib.sha256(normalized_url.encode()).hexdigest()[:10]
    return '%s-%s' % (slug(' '.join(str(w) for w in words if w)), h)


def write_search(out_dir, key, env, candidates):
    """Write <out>/<key>.json (the envelope, scrubbed) and <out>/<key>.candidates.json; returns the base path."""
    base = os.path.join(out_dir, key)
    write_json(base + '.json', scrub(env))
    write_json(base + '.candidates.json', {'candidates': candidates})
    return base


def print_paths(source, base, out=print):
    """The three lines a lane copies into its ledger row: the source slug, the evidence and the candidates."""
    out('source\t%s' % source)
    out('evidence\t%s.json' % base)
    out('candidates\t%s.candidates.json' % base)


# ---------------------------------------------------------------- manifest of saved files (gwt-rawstore)

MANIFEST_COLUMNS = ('file', 'kind', 'url', 'sha256', 'size', 'width', 'height', 'plan', 'original', 'fetched_at',
                    'collection_id', 'item_id')


def image_size(data):
    """(width, height) from a PNG, JPEG, GIF or WebP header, else (None, None)."""
    if data[:8] == b'\x89PNG\r\n\x1a\n' and len(data) >= 24:
        return struct.unpack('>II', data[16:24])
    if data[:6] in (b'GIF87a', b'GIF89a') and len(data) >= 10:
        return struct.unpack('<HH', data[6:10])
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP' and data[12:16] == b'VP8 ' and len(data) >= 30:
        w, h = struct.unpack('<HH', data[26:30])
        return w & 0x3fff, h & 0x3fff
    if data[:2] == b'\xff\xd8':
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xff:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xd8, 0x01) or 0xd0 <= marker <= 0xd7:
                i += 2
                continue
            seg = struct.unpack('>H', data[i + 2:i + 4])[0]
            if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
                h, w = struct.unpack('>HH', data[i + 5:i + 9])
                return w, h
            i += 2 + seg
    return None, None


def manifest_row(path, kind, url, plan, collection_id, item_id, fetched_at=None, original=True, image=False):
    """One manifest row for a saved file. `image` reads width and height from its header; `original` is False
    for a stand-in (a thumbnail, a screenshot) so nobody cites it as the source."""
    with open(path, 'rb') as f:
        data = f.read()
    w, h = image_size(data) if image else (None, None)
    return {'file': os.path.basename(path), 'kind': kind, 'url': url or '', 'sha256': hashlib.sha256(data).hexdigest(),
            'size': len(data), 'width': w or '', 'height': h or '', 'plan': plan, 'original': 'yes' if original else 'no',
            'fetched_at': fetched_at or now_iso(), 'collection_id': collection_id, 'item_id': item_id}


def read_manifest(path):
    try:
        with open(path, encoding='utf-8') as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    return [dict(zip(MANIFEST_COLUMNS, l.split('\t'))) for l in lines[1:] if l]


def write_manifest(path, rows):
    """Merge by file name (a re-fetch replaces its row) and write sorted, so the file diffs cleanly."""
    by_file = {r['file']: r for r in read_manifest(path)}
    for r in rows:
        by_file[r['file']] = r
    out = ['\t'.join(MANIFEST_COLUMNS)]
    for name in sorted(by_file):
        out.append('\t'.join(re.sub(r'[\t\r\n]+', ' ', str(by_file[name].get(c, ''))) for c in MANIFEST_COLUMNS))
    write_atomic(path, '\n'.join(out) + '\n')


# ---------------------------------------------------------------- STOP: blocks, captchas, login walls, empty frames


class Stop(Exception):
    """An answer that must end the run: nothing is saved as a result. exit_code is the process exit."""

    def __init__(self, msg, exit_code=EXIT_ERROR):
        super().__init__(msg)
        self.exit_code = exit_code


# Bot walls, rate pages and captchas (Imperva/Incapsula, Cloudflare, Akamai, hCaptcha, reCAPTCHA), and the
# signs of a sign-in page. Matched case-insensitively on the first 64 KB of an answer that is not JSON.
WALL_MARKERS = ('_incapsula_resource', 'incapsula incident', 'captcha', 'cf-chl', 'challenge-platform',
                'access denied', 'too many requests', 'unusual traffic', 'unusual activity', 'are you a robot',
                'request unsuccessful', 'security check')
LOGIN_MARKERS = ('type="password"', "type='password'", 'sign in to familysearch', 'iniciar sesión', 'log in',
                 'sign in', '/auth/', 'ident.familysearch.org')


def classify_answer(status, headers, body, expect=None, page=None):
    """'ok', 'blocked' (HTTP 403: the site refused the account or the IP), 'wall' (HTTP 429, a rate page or a
    captcha), 'dead' (signed out), 'empty' (an empty frame: no body or a page with no readable answer, read as
    a block), 'unexpected' (JSON of the wrong shape) or 'http' (any other non-2xx). A JSON reader passes
    `expect(parsed) -> bool`; a page reader passes `page(html) -> bool`, true only when the page carries the
    record text it asked for, so any other page (an empty frame, an error page) is a STOP."""
    if status == 403:
        return 'blocked'
    if status == 429:
        return 'wall'
    if status == 401:
        return 'dead'
    if not 200 <= status < 300:
        return 'http'
    data = body or b''
    if not data.strip():
        return 'empty'
    try:
        doc = json.loads(data)
    except ValueError:
        low = data[:65536].decode('utf-8', 'replace').lower()
        if any(m in low for m in WALL_MARKERS):
            return 'wall'
        if page is not None and page(data.decode('utf-8', 'replace')):
            return 'ok'
        if any(m in low for m in LOGIN_MARKERS):
            return 'dead'
        return 'empty'
    if expect is not None and not expect(doc):
        return 'unexpected'
    return 'ok'


STOP_FOR = {  # verdict: (exit code, what the lane reads, what the gate hears: block, hit, dead or nothing)
    'blocked': (EXIT_WALL, 'the site refused this account or IP (HTTP 403); every lane on this account stops for '
                '24 h; do not move to another account or IP to dodge it', 'block'),
    'wall': (EXIT_WALL, 'rate limit, block or captcha; every lane on this account pauses; do other work', 'hit'),
    'empty': (EXIT_WALL, 'empty or unreadable page, read as a block; every lane on this account pauses', 'hit'),
    'dead': (EXIT_DEAD, 'signed out or the token expired; get a fresh session token, never retry in a loop', 'dead'),
    'unexpected': (EXIT_ERROR, 'the answer is JSON of an unexpected shape; nothing saved', None),
    'http': (EXIT_ERROR, 'the site answered an error; nothing saved', None),
}
NOT_STORED = 599     # the status the cache sees for a 2xx that is a STOP, so it is never stored


class Reader:
    """GET through gwt-fscache and gwt-gate. fetcher(method, url, headers, data) -> (status, headers, body)
    is gwt-fscache's fetcher signature; the token belongs inside it, never in `headers`, which the cache sees."""

    BLOCK_HOURS = 24     # a refusal parks the account's gate this long (docs/hard-won.md, lesson 2)

    def __init__(self, cache, gate, lane, fetcher, log=None):
        self.cache, self.gate, self.lane, self.fetcher = cache, gate, lane, fetcher
        self.log = log or (lambda m: None)

    def get(self, url, headers=None, expect=None, refresh=False, page=None):
        """(status, headers, body, from_cache) for a 2xx answer the site really gave; raises Stop otherwise.
        `expect` checks a JSON answer, `page` an HTML one (see classify_answer)."""
        state = {}

        def gated(method, u, h, data):
            self.gate.wait(self.lane)       # raises gwt_gate.Paused: the caller exits without a request
            st, rh, body = self.fetcher(method, u, h, data)
            verdict = classify_answer(st, rh, body, expect, page)
            state.update(status=st, verdict=verdict)
            self.log('%s %s -> HTTP %s %s' % (method, u, st, verdict))
            return (st if verdict == 'ok' or not 200 <= st < 300 else NOT_STORED), rh, body

        status, rh, body = self.cache.fetch('GET', url, headers or {}, None, gated, refresh=refresh)
        verdict = state.get('verdict', 'ok')
        if verdict != 'ok':
            code, why, tell = STOP_FOR[verdict]
            what = 'HTTP %s %s' % (state['status'], url[:120])
            if tell == 'block':
                self.gate.hit(self.lane, what)
                self.gate.block(self.BLOCK_HOURS, what)
            elif tell == 'hit':
                self.gate.hit(self.lane, what)
            elif tell == 'dead':
                self.gate.dead(self.lane, what)
            raise Stop('%s (HTTP %s at %s)' % (why, state['status'], url), code)
        return status, rh, body, 'status' not in state
