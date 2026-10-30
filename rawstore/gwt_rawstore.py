#!/usr/bin/env python3
"""gwt-rawstore: keep source images out of git, in S3-compatible storage, with a manifest in git.

Each file is uploaded under a content-addressed key, `<prefix><sha[:2]>/<sha256>/<name>`, and gets a
row in `rawstore.tsv` beside it (sha256, size, key, path relative to that folder). The manifest is
the record; the bytes live in the bucket and can be fetched back and checked at any time.

    gwt-rawstore put <files or dirs> [--remove]       upload, write the manifest, optionally delete local copies
    gwt-rawstore get <path|sha> [...] [-o TARGET]      fetch into the cache (or TARGET) and verify the sha256
    gwt-rawstore verify [dirs] [--deep]                every manifest row is in the bucket with the right size and hash
    gwt-rawstore migrate <dir> [--remove]              upload files already committed; --remove untracks and ignores them
    gwt-rawstore check-staged                          pre-commit check: refuse big binaries outside allowed paths

Configuration comes from the environment, then from the file in $GWT_RAWSTORE_CONFIG (default
~/.config/gwt-rawstore/config, KEY=value lines): GWT_RAWSTORE_ENDPOINT, GWT_RAWSTORE_BUCKET,
GWT_RAWSTORE_REGION (default us-east-1), GWT_RAWSTORE_PREFIX (default empty), GWT_RAWSTORE_ACCESS_KEY,
GWT_RAWSTORE_SECRET_KEY, GWT_RAWSTORE_CACHE (default ~/.cache/gwt-rawstore). Requests are signed with
AWS SigV4 and use path-style addressing, which Garage and MinIO need. Standard library only.
"""
import argparse, concurrent.futures, datetime, fnmatch, hashlib, hmac, http.client, os, re, shutil, subprocess, \
    sys, tempfile, time, urllib.error, urllib.parse, urllib.request, xml.etree.ElementTree as ET

MANIFEST = 'rawstore.tsv'
COLUMNS = ('sha256', 'size', 'key', 'path')
BINARY_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.tif', '.tiff', '.webp', '.bmp', '.heic', '.jp2', '.pdf',
              '.zip', '.tar', '.gz', '.tgz', '.7z', '.mp3', '.m4a', '.ogg', '.wav', '.mp4', '.mov', '.djvu'}
EMPTY_SHA = hashlib.sha256(b'').hexdigest()
CHUNK = 1 << 20
TRIES, WAIT = 3, 2.0   # put: attempts per file, and seconds before the first retry (doubled each time)


def sha256_file(path):
    h, n = hashlib.sha256(), 0
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(CHUNK), b''):
            h.update(b)
            n += len(b)
    return h.hexdigest(), n


def object_key(sha, name, prefix=''):
    return '%s%s/%s/%s' % (prefix, sha[:2], sha, name)


def is_binary_name(path):
    return os.path.splitext(path)[1].lower() in BINARY_EXT


# ---------------------------------------------------------------- config


def load_config(env=None):
    env = dict(os.environ if env is None else env)
    path = os.path.expanduser(env.get('GWT_RAWSTORE_CONFIG', '~/.config/gwt-rawstore/config'))
    cfg = {}
    if os.path.isfile(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    cfg[k.strip().removeprefix('export ')] = v.strip().strip('"\'')
    cfg.update({k: v for k, v in env.items() if k.startswith('GWT_RAWSTORE_')})
    return cfg


def store_from_config(cfg):
    missing = [k for k in ('ENDPOINT', 'BUCKET', 'ACCESS_KEY', 'SECRET_KEY') if not cfg.get('GWT_RAWSTORE_' + k)]
    if missing:
        raise SystemExit('gwt-rawstore: missing config: ' + ', '.join('GWT_RAWSTORE_' + k for k in missing))
    return S3(cfg['GWT_RAWSTORE_ENDPOINT'], cfg['GWT_RAWSTORE_BUCKET'], cfg['GWT_RAWSTORE_ACCESS_KEY'],
              cfg['GWT_RAWSTORE_SECRET_KEY'], cfg.get('GWT_RAWSTORE_REGION', 'us-east-1'))


# ---------------------------------------------------------------- S3 with SigV4, path-style


class S3Error(Exception):
    def __init__(self, status, body=b''):
        super().__init__('S3 answered %s: %s' % (status, body[:300].decode('utf-8', 'replace')))
        self.status = status


class S3:
    def __init__(self, endpoint, bucket, access_key, secret_key, region='us-east-1', clock=None):
        self.endpoint, self.bucket = endpoint.rstrip('/'), bucket
        self.access_key, self.secret_key, self.region = access_key, secret_key, region
        self.host = urllib.parse.urlsplit(self.endpoint).netloc
        self.clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc))

    def _sign(self, method, path, query, headers, payload_sha):
        now = self.clock()
        amz_date, day = now.strftime('%Y%m%dT%H%M%SZ'), now.strftime('%Y%m%d')
        headers = dict(headers, host=self.host, **{'x-amz-date': amz_date, 'x-amz-content-sha256': payload_sha})
        signed = sorted(k.lower() for k in headers)
        lower = {k.lower(): str(v).strip() for k, v in headers.items()}
        canonical_query = '&'.join('%s=%s' % (urllib.parse.quote(k, safe='-_.~'), urllib.parse.quote(v, safe='-_.~'))
                                   for k, v in sorted(query.items()))
        canonical = '\n'.join([method, path, canonical_query, ''.join('%s:%s\n' % (k, lower[k]) for k in signed),
                               ';'.join(signed), payload_sha])
        scope = '%s/%s/s3/aws4_request' % (day, self.region)
        to_sign = '\n'.join(['AWS4-HMAC-SHA256', amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        k = ('AWS4' + self.secret_key).encode()
        for part in (day, self.region, 's3', 'aws4_request'):
            k = hmac.new(k, part.encode(), hashlib.sha256).digest()
        sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
        headers['Authorization'] = 'AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s' % (
            self.access_key, scope, ';'.join(signed), sig)
        return headers

    def request(self, method, key='', query=None, headers=None, body=None, payload_sha=EMPTY_SHA):
        path = '/' + urllib.parse.quote(self.bucket, safe='')
        if key:
            path += '/' + urllib.parse.quote(key, safe='/-_.~')
        query = query or {}
        h = self._sign(method, path, query, headers or {}, payload_sha)
        url = self.endpoint + path + ('?' + urllib.parse.urlencode(sorted(query.items()), quote_via=urllib.parse.quote)
                                      if query else '')
        req = urllib.request.Request(url, data=body, method=method, headers=h)
        try:
            return urllib.request.urlopen(req, timeout=300)
        except urllib.error.HTTPError as e:
            raise S3Error(e.code, e.read() if method != 'HEAD' else b'') from None

    def head(self, key):
        """(size, metadata sha256 or None), or None when the object does not exist."""
        try:
            with self.request('HEAD', key) as r:
                return int(r.headers['Content-Length']), r.headers.get('x-amz-meta-sha256')
        except S3Error as e:
            if e.status == 404:
                return None
            raise

    def put_file(self, key, path, sha, size):
        with open(path, 'rb') as f:
            self.request('PUT', key, body=f, payload_sha=sha, headers={
                'Content-Length': str(size), 'Content-Type': 'application/octet-stream', 'x-amz-meta-sha256': sha}).close()

    def get_to(self, key, dest):
        """Stream the object into dest (atomic rename); return (sha256, size) of what arrived."""
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(dest)), prefix='.rawstore-')
        h, n = hashlib.sha256(), 0
        try:
            with os.fdopen(fd, 'wb') as out, self.request('GET', key) as r:
                for b in iter(lambda: r.read(CHUNK), b''):
                    h.update(b)
                    n += len(b)
                    out.write(b)
            os.replace(tmp, dest)
        except BaseException:
            os.unlink(tmp)
            raise
        return h.hexdigest(), n

    def list_keys(self, prefix):
        keys, token = [], None
        while True:
            q = {'list-type': '2', 'prefix': prefix}
            if token:
                q['continuation-token'] = token
            with self.request('GET', query=q) as r:
                root = ET.fromstring(r.read())
            ns = root.tag[:root.tag.index('}') + 1] if root.tag.startswith('{') else ''
            keys += [c.findtext(ns + 'Key') for c in root.iter(ns + 'Contents')]
            token = root.findtext(ns + 'NextContinuationToken')
            if root.findtext(ns + 'IsTruncated') != 'true' or not token:
                return keys


# ---------------------------------------------------------------- manifest


def read_manifest(folder):
    """{relative path: (sha256, size, key)} for the folder's rawstore.tsv, empty when there is none."""
    path = os.path.join(folder, MANIFEST)
    rows = {}
    if os.path.isfile(path):
        with open(path, encoding='utf-8') as f:
            for i, line in enumerate(f):
                parts = line.rstrip('\n').split('\t')
                if i == 0 and tuple(parts) == COLUMNS:
                    continue
                if len(parts) == 4:
                    rows[parts[3]] = (parts[0], int(parts[1]), parts[2])
    return rows


def write_manifest(folder, rows):
    path = os.path.join(folder, MANIFEST)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix='.rawstore-')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write('\t'.join(COLUMNS) + '\n')
        for rel in sorted(rows):
            sha, size, key = rows[rel]
            f.write('%s\t%d\t%s\t%s\n' % (sha, size, key, rel))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def manifests_under(roots):
    for root in roots:
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if x != '.git']
            if MANIFEST in files:
                yield d


def expand(paths, everything=False):
    """Files named directly, plus binary files (by extension) found under named directories."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            for d, dirs, files in os.walk(p):
                dirs[:] = sorted(x for x in dirs if x != '.git')
                out += [os.path.join(d, f) for f in sorted(files) if everything or is_binary_name(f)]
        elif os.path.isfile(p):
            out.append(p)
        else:
            raise SystemExit('gwt-rawstore: no such file: ' + p)
    return out


# ---------------------------------------------------------------- operations


def transient(e):
    """An upload error worth another try: the network, a busy or failing store (5xx, 429), a body that no longer
    hashes to what was sent (the file changed while it was read), or a store that did not confirm the object."""
    if isinstance(e, S3Error):
        return e.status >= 500 or e.status == 429 or (e.status == 400 and 'XAmzContentSHA256Mismatch' in str(e))
    if isinstance(e, (FileNotFoundError, PermissionError, IsADirectoryError)):
        return False
    return isinstance(e, (urllib.error.URLError, http.client.HTTPException, OSError, RuntimeError))


def describe(e):
    """An exception as one line that is never empty: a bare TimeoutError() prints as ''."""
    return '%s: %s' % (type(e).__name__, e) if str(e) else type(e).__name__


def put(store, files, prefix='', remove=False, jobs=4, log=print):
    """Upload files and record them in their folder's manifest. Returns the number of failures. A transient
    error is retried (TRIES attempts per file, the file hashed again each time); a file that still fails is named on
    a FAIL line, and a last line on stderr and stdout counts and names every one of them: a run that stored 3,681 of
    3,682 files exited 1 with no path, and a log of stdout alone showed nothing (30-10-2026)."""
    def attempt(path):
        sha, size = sha256_file(path)
        key = object_key(sha, os.path.basename(path), prefix)
        have = store.head(key)
        if have is None or have[0] != size:
            store.put_file(key, path, sha, size)
            have, uploaded = store.head(key), True
        else:
            uploaded = False
        if have is None or have[0] != size or (have[1] and have[1] != sha):
            raise RuntimeError('upload of %s not confirmed by the store (%r)' % (path, have))
        return path, sha, size, key, uploaded

    def one(path):
        for n in range(TRIES):
            try:
                return attempt(path)
            except Exception as e:
                if n + 1 == TRIES or not transient(e):
                    raise
                log('retry %s after %s' % (path, describe(e)), file=sys.stderr)
                time.sleep(WAIT * 2 ** n)

    by_folder, failed = {}, []
    with concurrent.futures.ThreadPoolExecutor(max(1, jobs)) as ex:
        futs = {ex.submit(one, f): f for f in files}
        for fut in concurrent.futures.as_completed(futs):
            try:
                path, sha, size, key, uploaded = fut.result()
            except Exception as e:
                failed.append(futs[fut])
                log('FAIL %s: %s' % (futs[fut], describe(e)), file=sys.stderr)
                continue
            folder = os.path.dirname(path) or '.'
            by_folder.setdefault(folder, []).append((os.path.basename(path), sha, size, key, path))
            log('%s %s %s' % ('put ' if uploaded else 'have', sha[:12], path))
    for folder, items in by_folder.items():
        rows = read_manifest(folder)
        for rel, sha, size, key, _ in items:
            rows[rel] = (sha, size, key)
        write_manifest(folder, rows)
        if remove:
            for *_, path in items:
                os.unlink(path)
    if failed:
        line = 'put: %d of %d file(s) not stored: %s' % (len(failed), len(files), ' '.join(sorted(failed)))
        log(line, file=sys.stderr)
        log(line)
    return len(failed)


def resolve(store, ref, prefix=''):
    """A path listed in a manifest, or a sha256 (full, or a prefix of 8+ hex) -> (sha, size or None, key, name)."""
    folder, name = os.path.split(ref)
    rows = read_manifest(folder or '.')
    if name in rows:
        sha, size, key = rows[name]
        return sha, size, key, name
    if re.fullmatch(r'[0-9a-f]{8,64}', ref):
        keys = store.list_keys('%s%s/%s' % (prefix, ref[:2], ref))
        shas = {k[len(prefix):].split('/')[1] for k in keys}
        if len(shas) == 1:
            key = keys[0]
            return key[len(prefix):].split('/')[1], None, key, key.rsplit('/', 1)[1]
        if len(shas) > 1:
            raise SystemExit('gwt-rawstore: %s is ambiguous (%d objects)' % (ref, len(shas)))
    raise SystemExit('gwt-rawstore: %s is not in a manifest or the store' % ref)


def get(store, ref, cache, target=None, prefix=''):
    sha, size, key, name = resolve(store, ref, prefix)
    cached = os.path.join(os.path.expanduser(cache), sha[:2], sha)
    if not (os.path.isfile(cached) and sha256_file(cached)[0] == sha):
        got_sha, got_size = store.get_to(key, cached)
        if got_sha != sha or (size is not None and got_size != size):
            os.unlink(cached)
            raise SystemExit('gwt-rawstore: %s arrived corrupt (sha256 %s, expected %s)' % (key, got_sha, sha))
    if target:
        if os.path.isdir(target):
            target = os.path.join(target, name)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(target)), prefix='.rawstore-')
        os.close(fd)
        shutil.copyfile(cached, tmp)
        os.replace(tmp, target)
        return target
    return cached


def verify(store, roots, deep=False, log=print):
    bad = total = 0
    for folder in manifests_under(roots):
        for rel, (sha, size, key) in sorted(read_manifest(folder).items()):
            total += 1
            where = os.path.join(folder, rel)
            problem = None
            if sha not in key.split('/'):
                problem = 'key %s does not carry the sha256' % key
            else:
                have = store.head(key)
                if have is None:
                    problem = 'missing from the store'
                elif have[0] != size:
                    problem = 'size %d in the store, %d in the manifest' % (have[0], size)
                elif have[1] and have[1] != sha:
                    problem = 'store metadata says sha256 %s' % have[1]
                elif deep:
                    with tempfile.TemporaryDirectory() as t:
                        got, _ = store.get_to(key, os.path.join(t, 'x'))
                    if got != sha:
                        problem = 'content hashes to %s' % got
            if problem:
                bad += 1
                log('BAD  %s: %s' % (where, problem))
    log('%d rows checked, %d bad' % (total, bad))
    return bad


def git(*args, cwd=None):
    return subprocess.run(('git',) + args, cwd=cwd, check=True, capture_output=True, text=True).stdout


def gitignore_escape(name):
    return '/' + re.sub(r'([\\*?\[\]!# ])', r'\\\1', name)


def migrate(store, folder, prefix='', remove=False, jobs=4, log=print):
    """Upload the binaries git tracks under folder. --remove untracks them and ignores them; history stays."""
    tracked = [p for p in git('ls-files', '-z', '--', '.', cwd=folder).split('\0') if p and is_binary_name(p)]
    files = [os.path.join(folder, p) for p in tracked if os.path.isfile(os.path.join(folder, p))]
    if len(files) != len(tracked):
        raise SystemExit('gwt-rawstore: %d tracked files are not in the working tree (sparse checkout?); '
                         'check them out first' % (len(tracked) - len(files)))
    failures = put(store, files, prefix=prefix, jobs=jobs, log=log)
    if failures:
        log('%d uploads failed; nothing untracked' % failures, file=sys.stderr)
        return failures
    if remove and files:
        by_folder = {}
        for f in files:
            by_folder.setdefault(os.path.dirname(f), []).append(os.path.basename(f))
        for d, names in by_folder.items():
            git('rm', '--cached', '--quiet', '--', *names, cwd=d)
            ig = os.path.join(d, '.gitignore')
            have = set(open(ig, encoding='utf-8').read().splitlines()) if os.path.isfile(ig) else set()
            new = [gitignore_escape(n) for n in sorted(names) if gitignore_escape(n) not in have]
            if new:
                with open(ig, 'a', encoding='utf-8') as f:
                    f.write('\n'.join(new) + '\n')
            git('add', '--sparse', '--', MANIFEST, '.gitignore', cwd=d)
        log('untracked %d files in %d folders; review with git status, then commit' % (len(files), len(by_folder)))
    return 0


def check_staged(max_kb, allow, log=print):
    """Pre-commit: refuse staged binaries over max_kb outside the allowed globs. Returns the number refused."""
    out = git('diff', '--cached', '--numstat', '--diff-filter=AM', '-z')
    refused = []
    for entry in out.split('\0'):
        parts = entry.split('\t')
        if len(parts) != 3 or not parts[2]:
            continue
        added, _, path = parts
        if added != '-' and not is_binary_name(path):
            continue
        if any(fnmatch.fnmatch(path, g) for g in allow):
            continue
        size = int(git('cat-file', '-s', ':' + path))
        if size > max_kb * 1024:
            refused.append((path, size))
    for path, size in refused:
        log('gwt-rawstore: %s is a %d KB binary; binaries over %d KB go to the raw store' % (path, size // 1024, max_kb),
            file=sys.stderr)
    if refused:
        log('Run: git restore --staged -- <file> && gwt-rawstore put <file>, then commit the rawstore.tsv beside it.',
            file=sys.stderr)
    return len(refused)


def allow_globs(cfg):
    globs = [g for g in cfg.get('GWT_RAWSTORE_ALLOW', '').split(':') if g]
    try:
        top = git('rev-parse', '--show-toplevel').strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return globs
    path = os.path.join(top, '.rawstore-allow')
    if os.path.isfile(path):
        globs += [l.strip() for l in open(path, encoding='utf-8') if l.strip() and not l.startswith('#')]
    return globs


def main(argv=None, store=None):
    p = argparse.ArgumentParser(prog='gwt-rawstore', description=__doc__.split('\n')[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    g = sub.add_parser('put', help='upload files and record them in rawstore.tsv')
    g.add_argument('paths', nargs='+')
    g.add_argument('--remove', action='store_true', help='delete each local file once the store confirms it')
    g.add_argument('--all', action='store_true', help='in directories, take every file, not only binary extensions')
    g.add_argument('-j', '--jobs', type=int, default=4)
    g = sub.add_parser('get', help='fetch by manifest path or sha256 and verify')
    g.add_argument('refs', nargs='+')
    g.add_argument('-o', '--output', help='copy here (a directory when several refs are given)')
    g = sub.add_parser('verify', help='check every manifest row against the store')
    g.add_argument('roots', nargs='*', default=['.'])
    g.add_argument('--deep', action='store_true', help='download and hash every object too')
    g = sub.add_parser('migrate', help='move committed binaries under a folder to the store')
    g.add_argument('folder')
    g.add_argument('--remove', action='store_true', help='git rm --cached them and add them to .gitignore')
    g.add_argument('-j', '--jobs', type=int, default=4)
    g = sub.add_parser('check-staged', help='pre-commit hook: refuse big staged binaries')
    g.add_argument('--max-kb', type=int, default=None)
    a = p.parse_args(argv)

    cfg = load_config()
    if a.cmd == 'check-staged':
        max_kb = a.max_kb if a.max_kb is not None else int(cfg.get('GWT_RAWSTORE_MAX_KB', '256'))
        return 1 if check_staged(max_kb, allow_globs(cfg)) else 0
    store = store or store_from_config(cfg)
    prefix, cache = cfg.get('GWT_RAWSTORE_PREFIX', ''), cfg.get('GWT_RAWSTORE_CACHE', '~/.cache/gwt-rawstore')
    if a.cmd == 'put':
        return 1 if put(store, expand(a.paths, a.all), prefix, a.remove, a.jobs) else 0
    if a.cmd == 'get':
        if len(a.refs) > 1 and a.output and not os.path.isdir(a.output):
            raise SystemExit('gwt-rawstore: -o must be an existing directory when several refs are given')
        for ref in a.refs:
            print(get(store, ref, cache, a.output, prefix))
        return 0
    if a.cmd == 'verify':
        return 1 if verify(store, a.roots, a.deep) else 0
    if a.cmd == 'migrate':
        return 1 if migrate(store, a.folder, prefix, a.remove, a.jobs) else 0


if __name__ == '__main__':
    sys.exit(main())
