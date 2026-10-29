#!/usr/bin/env python3
"""gwt-fscache: a content-addressed HTTP response cache for rate-limited genealogy APIs.

A request already answered is served from disk with zero network calls. Each entry is keyed by
method + URL (+ request body for POST + the headers named in `vary`), and keeps status, headers,
body and fetch date. Only 2xx answers are stored: a 403, 429, 5xx or a failed connection never is,
so a block can never be replayed as if it were the answer. TTLs go by URL pattern; records and
images never expire.

As a module:

    from gwt_fscache import FSCache
    cache = FSCache('~/.cache/gwt-fscache')
    status, headers, body = cache.fetch('GET', url, {'accept': 'application/json'}, None, my_fetch)
    cached_fetch = cache.wrap(my_fetch)   # same signature: (method, url, headers, data) -> (status, headers, body)

As a CLI:

    gwt-fscache get <url> [-H 'Name: value'] [-o FILE] [--refresh] [--offline]
    gwt-fscache stats

The CLI reads the bearer token from $GWT_FSCACHE_TOKEN or the file in $GWT_FSCACHE_TOKEN_FILE and the
cache dir from $GWT_FSCACHE_DIR (default ~/.cache/gwt-fscache). Standard library only.
"""
import argparse, hashlib, json, os, re, sys, tempfile, time, urllib.error, urllib.request

DAY = 86400
# First match wins; None = never expires. Defaults fit FamilySearch: records, persons-in-records and
# images are fixed once published; searches, trees and catalog listings change.
FAMILYSEARCH_RULES = (
    (r'/ark:/61903/1:', None),                 # indexed records
    (r'/ark:/61903/3:', None),                 # images
    (r'/service/records/storage/', None),      # image storage (das, rms)
    (r'/sls/image/records/', None),            # machine transcription of an image
    (r'\.(jpe?g|png|tiff?|gif|pdf)(\?|$)', None),
    (r'/search/filmdatainfo/', 30 * DAY),      # list of images in a film
    (r'/platform/tree/|/tree/', 1 * DAY),      # the shared tree changes every day
    (r'/search/|/service/search/', 7 * DAY),
)
# PARES (Spanish state archives, pares.cultura.gob.es): catalogue descriptions change rarely, searches weekly.
PARES_RULES = (
    (r'/catalogo/search(\?|$)', 0),            # the search form: fetched only for the session cookie, never served
    (r'/catalogo/description/', 90 * DAY),     # one archival description
    (r'/catalogo/find\?', 7 * DAY),            # a search
)
# Genae (genae.org, a volunteer index of parish records): the index grows by transcription, so a search is kept a week.
GENAE_RULES = (
    (r'/buscar/(resultados|bloque)\?', 7 * DAY),
)
DEFAULT_TTL = 7 * DAY
STORE_STATUSES = range(200, 300)
PRIVATE_HEADERS = ('set-cookie', 'authorization', 'www-authenticate')  # never written to disk


class FSCache:
    def __init__(self, root, rules=FAMILYSEARCH_RULES, default_ttl=DEFAULT_TTL, vary=('accept',),
                 methods=('GET',), clock=time.time):
        self.root = os.path.expanduser(root)
        self.rules = [(re.compile(p), ttl) for p, ttl in rules]
        self.default_ttl, self.vary, self.methods, self.clock = default_ttl, tuple(h.lower() for h in vary), \
            tuple(m.upper() for m in methods), clock
        os.makedirs(self.root, mode=0o700, exist_ok=True)

    def key(self, method, url, headers=None, data=None):
        h = {k.lower(): v for k, v in (headers or {}).items()}
        parts = [method.upper(), url] + ['%s: %s' % (n, h.get(n, '')) for n in self.vary]
        if data is not None:
            parts.append(hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest())
        return hashlib.sha256('\n'.join(parts).encode()).hexdigest()

    def ttl(self, url):
        for pattern, ttl in self.rules:
            if pattern.search(url):
                return ttl
        return self.default_ttl

    def _paths(self, k):
        d = os.path.join(self.root, k[:2])
        return d, os.path.join(d, k + '.json'), os.path.join(d, k + '.body')

    def lookup(self, method, url, headers=None, data=None):
        """(status, headers, body) if a fresh entry exists, else None. Never touches the network."""
        _, meta_path, body_path = self._paths(self.key(method, url, headers, data))
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            with open(body_path, 'rb') as f:
                body = f.read()
        except (OSError, ValueError):
            return None
        ttl = self.ttl(url)
        if ttl is not None and self.clock() - meta['fetched'] > ttl:
            return None
        return meta['status'], meta['headers'], body

    def store(self, method, url, headers, data, status, resp_headers, body):
        if status not in STORE_STATUSES:
            return False
        d, meta_path, body_path = self._paths(self.key(method, url, headers, data))
        os.makedirs(d, mode=0o700, exist_ok=True)
        now = self.clock()
        kept = {k: v for k, v in (resp_headers or {}).items() if k.lower() not in PRIVATE_HEADERS}
        meta = {'method': method.upper(), 'url': url, 'status': status, 'headers': kept,
                'fetched': now, 'date': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
                'vary': {n: v for n, v in ((k.lower(), v) for k, v in (headers or {}).items()) if n in self.vary}}
        # Body first, then metadata: a reader that finds the metadata always finds a complete body.
        for path, payload in ((body_path, body), (meta_path, json.dumps(meta).encode())):
            fd, tmp = tempfile.mkstemp(dir=d)
            with os.fdopen(fd, 'wb') as f:
                f.write(payload)
            os.replace(tmp, path)
        return True

    def fetch(self, method, url, headers, data, fetcher, refresh=False):
        """Serve from cache if fresh, else call fetcher(method, url, headers, data) and store a 2xx answer."""
        cacheable = method.upper() in self.methods
        if cacheable and not refresh:
            hit = self.lookup(method, url, headers, data)
            if hit is not None:
                return hit
        status, resp_headers, body = fetcher(method, url, headers, data)
        if cacheable:
            self.store(method, url, headers, data, status, resp_headers, body)
        return status, resp_headers, body

    def wrap(self, fetcher):
        def cached(method, url, headers=None, data=None):
            return self.fetch(method, url, headers, data, fetcher)
        return cached

    def stats(self):
        n = size = 0
        for d, _, files in os.walk(self.root):
            for name in files:
                if name.endswith('.body'):
                    n += 1
                    size += os.path.getsize(os.path.join(d, name))
        return {'entries': n, 'bytes': size}


def urllib_fetch(method, url, headers, data):
    """Plain stdlib fetcher. HTTP errors come back as their status, never raised."""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()
    except (urllib.error.URLError, OSError) as e:
        return 0, {}, str(e).encode()


def token_from_env():
    tok = os.environ.get('GWT_FSCACHE_TOKEN')
    path = os.environ.get('GWT_FSCACHE_TOKEN_FILE')
    if not tok and path:
        with open(os.path.expanduser(path)) as f:
            tok = f.read().strip()
    return tok


def main(argv=None, fetcher=urllib_fetch):
    p = argparse.ArgumentParser(prog='gwt-fscache', description=__doc__.split('\n')[0])
    p.add_argument('--dir', default=os.environ.get('GWT_FSCACHE_DIR', '~/.cache/gwt-fscache'))
    sub = p.add_subparsers(dest='cmd', required=True)
    g = sub.add_parser('get', help='print the body of a GET, from cache when fresh')
    g.add_argument('url')
    g.add_argument('-H', '--header', action='append', default=[], help="extra header, 'Name: value'")
    g.add_argument('-o', '--output', help='write the body here instead of stdout')
    g.add_argument('--refresh', action='store_true', help='skip the cache and fetch again')
    g.add_argument('--offline', action='store_true', help='cache only; exit 4 on a miss')
    sub.add_parser('stats', help='entries and bytes on disk')
    args = p.parse_args(argv)
    cache = FSCache(args.dir)
    if args.cmd == 'stats':
        print(json.dumps(cache.stats()))
        return 0

    headers = {'accept': 'application/json'}
    for h in args.header:
        name, _, value = h.partition(':')
        headers[name.strip().lower()] = value.strip()
    hit = None if args.refresh else cache.lookup('GET', args.url, headers)
    if hit is None and args.offline:
        print('MISS (offline)', file=sys.stderr)
        return 4
    if hit is None:
        tok = token_from_env()
        if tok and 'authorization' not in headers:
            headers['authorization'] = 'Bearer ' + tok
        status, _, body = cache.fetch('GET', args.url, headers, None, fetcher, refresh=True)
    else:
        status, _, body = hit
    print('HTTP %d %s' % (status, 'HIT' if hit is not None else 'MISS'), file=sys.stderr)
    if args.output:
        with open(args.output, 'wb') as f:
            f.write(body)
    else:
        sys.stdout.buffer.write(body)
    return 0 if 200 <= status < 300 else 1


if __name__ == '__main__':
    sys.exit(main())
