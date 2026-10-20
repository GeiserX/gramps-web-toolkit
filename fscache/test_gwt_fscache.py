import io, json, os, sys, tempfile, unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_fscache as C

RECORD = 'https://api.example.org/ark:/61903/1:1:ABCD-123'
IMAGE = 'https://api.example.org/ark:/61903/3:1:XYZ9-77'
SEARCH = 'https://api.example.org/service/search/hr/v2/personas?q.surname=Peiro'


class Fake:
    """Counting fetcher: answers from a script of (status, body) per URL and records every call."""
    def __init__(self, answers=None, default=(200, b'{"ok":1}')):
        self.answers, self.default, self.calls = dict(answers or {}), default, []

    def __call__(self, method, url, headers, data):
        self.calls.append((method, url, dict(headers or {}), data))
        status, body = self.answers.get(url, self.default)
        return status, {'Content-Type': 'application/json', 'Set-Cookie': 'session=secret'}, body


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FSCacheTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.cache = C.FSCache(self.dir.name, clock=self.clock)

    def tearDown(self):
        self.dir.cleanup()

    def test_hit_costs_zero_network_requests(self):
        fake = Fake()
        get = self.cache.wrap(fake)
        first = get('GET', RECORD, {'accept': 'application/json'})
        for _ in range(50):
            again = get('GET', RECORD, {'accept': 'application/json'})
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(again[0], 200)
        self.assertEqual(again[2], first[2])

    def test_hit_does_not_even_construct_a_request(self):
        get = self.cache.wrap(Fake())
        get('GET', RECORD)
        boom = mock.Mock(side_effect=AssertionError('network touched'))
        with mock.patch.object(C.urllib.request, 'urlopen', boom):
            self.assertEqual(self.cache.fetch('GET', RECORD, None, None, boom)[0], 200)
        boom.assert_not_called()

    def test_errors_are_never_cached(self):
        for status in (403, 429, 500, 502, 503, 0):
            fake = Fake(default=(status, b'blocked'))
            get = self.cache.wrap(fake)
            get('GET', RECORD + str(status))
            get('GET', RECORD + str(status))
            self.assertEqual(len(fake.calls), 2, status)
        self.assertEqual(self.cache.stats()['entries'], 0)

    def test_a_block_does_not_poison_a_later_success(self):
        fake = Fake(default=(429, b'slow down'))
        get = self.cache.wrap(fake)
        self.assertEqual(get('GET', RECORD)[0], 429)
        fake.default = (200, b'record')
        self.assertEqual(get('GET', RECORD)[2], b'record')
        self.assertEqual(get('GET', RECORD)[2], b'record')
        self.assertEqual(len(fake.calls), 2)

    def test_records_and_images_never_expire_searches_do(self):
        fake = Fake()
        get = self.cache.wrap(fake)
        for url in (RECORD, IMAGE, SEARCH):
            get('GET', url)
        self.clock.t += 8 * C.DAY  # past the 7-day search TTL
        for url in (RECORD, IMAGE, SEARCH):
            get('GET', url)
        self.assertEqual([u for _, u, _, _ in fake.calls], [RECORD, IMAGE, SEARCH, SEARCH])
        self.clock.t += 3650 * C.DAY
        get('GET', RECORD); get('GET', IMAGE)
        self.assertEqual(len(fake.calls), 4)

    def test_custom_ttl_rules(self):
        cache = C.FSCache(self.dir.name, rules=[(r'/fast/', 10)], default_ttl=None, clock=self.clock)
        fake = Fake()
        get = cache.wrap(fake)
        get('GET', 'https://x.test/fast/1'); get('GET', 'https://x.test/slow/1')
        self.clock.t += 11
        get('GET', 'https://x.test/fast/1'); get('GET', 'https://x.test/slow/1')
        self.assertEqual(len(fake.calls), 3)

    def test_key_uses_method_url_vary_headers_and_body_but_not_auth(self):
        fake = Fake()
        cache = C.FSCache(self.dir.name, methods=('GET', 'POST'), clock=self.clock)
        get = cache.wrap(fake)
        get('GET', RECORD, {'accept': 'application/json', 'authorization': 'Bearer one'})
        get('GET', RECORD, {'accept': 'application/json', 'authorization': 'Bearer two'})  # token rotated: hit
        get('GET', RECORD, {'accept': 'application/x-gedcomx-v1+json'})                    # other accept: miss
        get('POST', RECORD, {}, b'{"a":1}')
        get('POST', RECORD, {}, b'{"a":1}')                                                 # same body: hit
        get('POST', RECORD, {}, b'{"a":2}')                                                 # other body: miss
        self.assertEqual(len(fake.calls), 4)

    def test_post_is_not_cached_unless_asked(self):
        fake = Fake()
        get = self.cache.wrap(fake)
        get('POST', SEARCH, {}, b'q'); get('POST', SEARCH, {}, b'q')
        self.assertEqual(len(fake.calls), 2)

    def test_secrets_never_reach_disk(self):
        self.cache.wrap(Fake())('GET', RECORD, {'authorization': 'Bearer TOPSECRET', 'accept': 'a'})
        blob = b''
        for d, _, files in os.walk(self.dir.name):
            for name in files:
                with open(os.path.join(d, name), 'rb') as f:
                    blob += f.read()
        self.assertNotIn(b'TOPSECRET', blob)
        self.assertNotIn(b'session=secret', blob)
        meta = [f for d, _, fs in os.walk(self.dir.name) for f in fs if f.endswith('.json')]
        self.assertEqual(len(meta), 1)

    def test_refresh_bypasses_and_rewrites(self):
        fake = Fake()
        self.cache.fetch('GET', RECORD, None, None, fake)
        fake.default = (200, b'new')
        self.assertEqual(self.cache.fetch('GET', RECORD, None, None, fake, refresh=True)[2], b'new')
        self.assertEqual(self.cache.fetch('GET', RECORD, None, None, fake)[2], b'new')
        self.assertEqual(len(fake.calls), 2)

    def test_binary_bodies_round_trip(self):
        png = bytes(range(256)) * 40
        fake = Fake({IMAGE: (200, png)})
        get = self.cache.wrap(fake)
        get('GET', IMAGE)
        self.assertEqual(get('GET', IMAGE)[2], png)
        self.assertEqual(len(fake.calls), 1)

    def test_cli_get_hit_offline_and_token(self):
        fake = Fake({RECORD: (200, b'body-1')})
        env = {'GWT_FSCACHE_TOKEN': 'tok-123'}

        def cli(*argv):
            out, err = io.BytesIO(), io.StringIO()
            stdout = io.TextIOWrapper(out)
            with mock.patch.dict(os.environ, env), redirect_stdout(stdout), redirect_stderr(err):
                code = C.main(['--dir', self.dir.name] + list(argv), fetcher=fake)
                stdout.flush()
            return code, out.getvalue(), err.getvalue()

        self.assertEqual(cli('get', '--offline', RECORD)[0], 4)
        self.assertEqual(len(fake.calls), 0)
        self.assertEqual(cli('get', RECORD), (0, b'body-1', 'HTTP 200 MISS\n'))
        self.assertEqual(fake.calls[0][2]['authorization'], 'Bearer tok-123')
        self.assertEqual(cli('get', RECORD), (0, b'body-1', 'HTTP 200 HIT\n'))
        self.assertEqual(cli('get', '--offline', RECORD)[0], 0)
        self.assertEqual(len(fake.calls), 1)
        code, out, _ = cli('stats')
        self.assertEqual(json.loads(out)['entries'], 1)

    def test_cli_token_file(self):
        path = os.path.join(self.dir.name, 'tok')
        with open(path, 'w') as f:
            f.write('from-file\n')
        with mock.patch.dict(os.environ, {'GWT_FSCACHE_TOKEN_FILE': path}, clear=False):
            os.environ.pop('GWT_FSCACHE_TOKEN', None)
            self.assertEqual(C.token_from_env(), 'from-file')


if __name__ == '__main__':
    unittest.main()
