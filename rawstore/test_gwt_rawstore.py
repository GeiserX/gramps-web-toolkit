import datetime, hashlib, io, os, subprocess, sys, tempfile, threading, unittest, urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_rawstore as R

BUCKET = 'raw-test'


class FakeS3(BaseHTTPRequestHandler):
    """Path-style S3 stub: PUT/GET/HEAD/ListObjectsV2. Like Garage, it rejects a PUT whose body does not
    hash to x-amz-content-sha256, and it records every request."""
    objects, calls = {}, []

    def log_message(self, *a):
        pass

    def _split(self):
        u = urllib.parse.urlsplit(self.path)
        parts = u.path.split('/', 2)
        return parts[1], urllib.parse.unquote(parts[2]) if len(parts) > 2 else '', urllib.parse.parse_qs(u.query)

    def _reply(self, status, body=b'', headers=None):
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)) if self.command != 'HEAD' else headers.get('X-Len', '0'))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _auth_ok(self, bucket):
        self.calls.append((self.command, self.path))
        if bucket != BUCKET or not self.headers.get('Authorization', '').startswith('AWS4-HMAC-SHA256 Credential=AK/'):
            self._reply(403, b'<Error>AccessDenied</Error>', {})
            return False
        return True

    def do_PUT(self):
        bucket, key, _ = self._split()
        if not self._auth_ok(bucket):
            return
        body = self.rfile.read(int(self.headers['Content-Length']))
        if hashlib.sha256(body).hexdigest() != self.headers['x-amz-content-sha256']:
            return self._reply(400, b'<Error>XAmzContentSHA256Mismatch</Error>', {})
        self.objects[key] = (body, self.headers.get('x-amz-meta-sha256'))
        self._reply(200, b'', {})

    def do_HEAD(self):
        bucket, key, _ = self._split()
        if not self._auth_ok(bucket):
            return
        if key not in self.objects:
            return self._reply(404, b'', {})
        body, meta = self.objects[key]
        self._reply(200, b'', {'X-Len': str(len(body)), **({'x-amz-meta-sha256': meta} if meta else {})})

    def do_GET(self):
        bucket, key, q = self._split()
        if not self._auth_ok(bucket):
            return
        if not key:
            prefix = q.get('prefix', [''])[0]
            keys = sorted(k for k in self.objects if k.startswith(prefix))
            xml = ('<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated>'
                   + ''.join('<Contents><Key>%s</Key></Contents>' % escape(k) for k in keys) + '</ListBucketResult>')
            return self._reply(200, xml.encode(), {})
        if key not in self.objects:
            return self._reply(404, b'<Error>NoSuchKey</Error>', {})
        self._reply(200, self.objects[key][0], {})


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), FakeS3)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.endpoint = 'http://127.0.0.1:%d' % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeS3.objects.clear()
        FakeS3.calls.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.store = R.S3(self.endpoint, BUCKET, 'AK', 'SK')
        self.out = []

    def tearDown(self):
        self.tmp.cleanup()

    def log(self, *a, **k):
        self.out.append(' '.join(map(str, a)))

    def write(self, rel, data):
        p = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f:
            f.write(data)
        return p


class SigV4Test(unittest.TestCase):
    def test_matches_the_aws_documented_example(self):
        # "GET Object" example from the AWS SigV4 for S3 documentation.
        s = R.S3('https://examplebucket.s3.amazonaws.com', 'x', 'AKIAIOSFODNN7EXAMPLE',
                 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
                 clock=lambda: datetime.datetime(2013, 5, 24, tzinfo=datetime.timezone.utc))
        h = s._sign('GET', '/test.txt', {}, {'Range': 'bytes=0-9'}, R.EMPTY_SHA)
        self.assertTrue(h['Authorization'].endswith(
            'Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41'), h['Authorization'])


class PutGetTest(Base):
    def test_put_writes_manifest_and_content_addressed_key(self):
        img = self.write('r1/scan one.jpg', b'JPEGDATA')
        self.write('r1/notes.txt', b'text stays in git')
        self.assertEqual(R.put(self.store, R.expand([os.path.join(self.dir, 'r1')]), log=self.log), 0)
        sha = hashlib.sha256(b'JPEGDATA').hexdigest()
        key = '%s/%s/scan one.jpg' % (sha[:2], sha)
        self.assertEqual(FakeS3.objects[key], (b'JPEGDATA', sha))
        self.assertEqual(R.read_manifest(os.path.join(self.dir, 'r1')), {'scan one.jpg': (sha, 8, key)})
        self.assertTrue(os.path.exists(img))
        self.assertEqual(len(FakeS3.objects), 1)  # the .txt was not picked up from the directory

    def test_second_put_skips_upload_and_remove_deletes_local(self):
        img = self.write('r1/a.png', b'PNG')
        R.put(self.store, [img], log=self.log)
        puts = [c for c in FakeS3.calls if c[0] == 'PUT']
        R.put(self.store, [img], remove=True, log=self.log)
        self.assertEqual(puts, [c for c in FakeS3.calls if c[0] == 'PUT'])
        self.assertFalse(os.path.exists(img))
        self.assertIn('a.png', R.read_manifest(os.path.join(self.dir, 'r1')))

    def test_put_extends_existing_manifest(self):
        a, b = self.write('r1/a.png', b'A'), self.write('r1/b.png', b'B')
        R.put(self.store, [a], log=self.log)
        R.put(self.store, [b], log=self.log)
        self.assertEqual(set(R.read_manifest(os.path.join(self.dir, 'r1'))), {'a.png', 'b.png'})

    def test_failed_upload_is_not_recorded_or_removed(self):
        img = self.write('r1/a.png', b'A')
        bad = R.S3(self.endpoint, 'other-bucket', 'AK', 'SK')
        with redirect_stderr(io.StringIO()):
            self.assertEqual(R.put(bad, [img], remove=True, log=self.log), 1)
        self.assertTrue(os.path.exists(img))
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'r1', R.MANIFEST)))

    def test_get_by_path_and_by_sha_verifies(self):
        img = self.write('r1/a.tif', b'TIFF' * 1000)
        R.put(self.store, [img], remove=True, log=self.log)
        cache = os.path.join(self.dir, 'cache')
        got = R.get(self.store, img, cache)
        self.assertEqual(open(got, 'rb').read(), b'TIFF' * 1000)
        sha = hashlib.sha256(b'TIFF' * 1000).hexdigest()
        target = os.path.join(self.dir, 'back.tif')
        self.assertEqual(R.get(self.store, sha[:10], cache, target), target)
        self.assertEqual(open(target, 'rb').read(), b'TIFF' * 1000)

    def test_get_refuses_corrupt_object(self):
        img = self.write('r1/a.jpg', b'GOOD')
        R.put(self.store, [img], log=self.log)
        key = next(iter(FakeS3.objects))
        FakeS3.objects[key] = (b'EVIL', None)
        with self.assertRaises(SystemExit) as e:
            R.get(self.store, img, os.path.join(self.dir, 'cache'))
        self.assertIn('corrupt', str(e.exception))


class VerifyTest(Base):
    def test_verify_reports_missing_and_wrong_size(self):
        a, b, c = (self.write('r1/%s.jpg' % n, n.encode() * 3) for n in 'abc')
        R.put(self.store, [a, b, c], log=self.log)
        self.assertEqual(R.verify(self.store, [self.dir], deep=True, log=self.log), 0)
        keys = sorted(FakeS3.objects)
        del FakeS3.objects[keys[0]]
        FakeS3.objects[keys[1]] = (b'x', None)
        self.assertEqual(R.verify(self.store, [self.dir], log=self.log), 2)
        self.assertIn('missing from the store', '\n'.join(self.out))

    def test_deep_verify_catches_same_size_corruption(self):
        a = self.write('r1/a.jpg', b'AAAA')
        R.put(self.store, [a], log=self.log)
        key = next(iter(FakeS3.objects))
        FakeS3.objects[key] = (b'BBBB', None)
        self.assertEqual(R.verify(self.store, [self.dir], log=self.log), 0)
        self.assertEqual(R.verify(self.store, [self.dir], deep=True, log=self.log), 1)


class GitTest(Base):
    def setUp(self):
        super().setUp()
        self.git('init', '-q')
        self.git('config', 'user.email', 't@example.org')
        self.git('config', 'user.name', 't')
        self.git('config', 'commit.gpgsign', 'false')
        self.git('config', 'core.hooksPath', '/dev/null')

    def git(self, *a):
        return subprocess.run(('git',) + a, cwd=self.dir, check=True, capture_output=True, text=True).stdout

    def test_migrate_remove_untracks_ignores_and_keeps_history(self):
        self.write('raw/r1/a.jpg', b'A' * 10)
        self.write('raw/r1/sub/b.pdf', b'B' * 10)
        self.write('raw/r1/notes.md', b'# notes')
        self.git('add', '.')
        self.git('commit', '-qm', 'raw')
        self.assertEqual(R.migrate(self.store, os.path.join(self.dir, 'raw/r1'), remove=True, log=self.log), 0)
        self.assertEqual(len(FakeS3.objects), 2)
        status = self.git('status', '--porcelain')
        self.assertIn('D  raw/r1/a.jpg', status)
        self.assertIn('A  raw/r1/rawstore.tsv', status)
        self.assertIn('A  raw/r1/sub/.gitignore', status)
        self.assertNotIn('??', status)  # the files stay on disk, ignored
        self.assertEqual(open(os.path.join(self.dir, 'raw/r1/.gitignore')).read(), '/a.jpg\n')
        self.git('commit', '-qm', 'migrate')
        self.assertEqual(self.git('show', 'HEAD~1:raw/r1/a.jpg'), 'A' * 10)
        self.assertEqual(R.verify(self.store, [os.path.join(self.dir, 'raw')], log=self.log), 0)

    def test_migrate_without_remove_touches_no_index(self):
        self.write('r1/a.jpg', b'A')
        self.git('add', '.')
        self.git('commit', '-qm', 'raw')
        R.migrate(self.store, os.path.join(self.dir, 'r1'), log=self.log)
        self.assertEqual(self.git('status', '--porcelain'), '?? r1/rawstore.tsv\n')

    def test_check_staged_refuses_big_binary_outside_allowed(self):
        self.write('raw/big.jpg', b'\xff\xd8' + os.urandom(300 * 1024))
        self.write('assets/logo.png', b'\x89PNG' + os.urandom(300 * 1024))
        self.write('raw/small.jpg', b'\xff\xd8' + os.urandom(10))
        self.write('raw/long.md', b'text\n' * 100000)
        self.git('add', '.')
        cwd = os.getcwd()
        os.chdir(self.dir)
        try:
            with redirect_stderr(io.StringIO()):
                refused = R.check_staged(256, ['assets/*'], log=self.log)
        finally:
            os.chdir(cwd)
        self.assertEqual(refused, 1)
        self.assertIn('raw/big.jpg', self.out[0])
        self.assertIn('gwt-rawstore put', self.out[-1])

    def test_check_staged_passes_clean_index(self):
        self.write('a.md', b'hi')
        self.git('add', '.')
        cwd = os.getcwd()
        os.chdir(self.dir)
        try:
            self.assertEqual(R.check_staged(256, [], log=self.log), 0)
        finally:
            os.chdir(cwd)


class ConfigTest(unittest.TestCase):
    def test_env_beats_file_and_missing_keys_are_named(self):
        with tempfile.NamedTemporaryFile('w', delete=False) as f:
            f.write('# comment\nGWT_RAWSTORE_BUCKET=from-file\nexport GWT_RAWSTORE_ENDPOINT="http://x:9000"\n')
        try:
            cfg = R.load_config({'GWT_RAWSTORE_CONFIG': f.name, 'GWT_RAWSTORE_BUCKET': 'from-env'})
        finally:
            os.unlink(f.name)
        self.assertEqual((cfg['GWT_RAWSTORE_BUCKET'], cfg['GWT_RAWSTORE_ENDPOINT']), ('from-env', 'http://x:9000'))
        with self.assertRaises(SystemExit) as e:
            R.store_from_config(cfg)
        self.assertIn('GWT_RAWSTORE_ACCESS_KEY', str(e.exception))


if __name__ == '__main__':
    unittest.main()
