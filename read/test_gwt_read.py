"""Tests for gwt-read and the shared reader shape. Invented people and ids, a fake transport, a fake gate:
no request ever leaves this machine. Run: cd read && python3 -m unittest -v test_gwt_read"""
import contextlib, io, json, os, sys, tempfile, unittest, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
for sub in ('rank', 'fscache', 'gate'):
    sys.path.insert(0, os.path.join(HERE, '..', sub))
sys.path.insert(0, HERE)

import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402
import gwt_rank  # noqa: E402
from gwt_gate import Paused  # noqa: E402

GX = 'http://gedcomx.org/'
TOKEN = 'tok-ZZ9-invented-session-0123456789abcdef'


def persona(pid, ark, given, surname, gender, facts=(), principal=False):
    return {'id': pid, 'principal': principal, 'gender': {'type': GX + gender},
            'identifiers': {GX + 'Persistent': ['https://www.familysearch.org/ark:/61903/1:1:' + ark]},
            'names': [{'nameForms': [{'fullText': '%s %s' % (given, surname),
                                      'parts': [{'type': GX + 'Given', 'value': given},
                                                {'type': GX + 'Surname', 'value': surname}]}]}],
            'facts': [{'type': GX + t, 'date': {'original': d, 'formal': '+' + d}, 'place': {'original': pl}}
                      for t, d, pl in facts]}


def entry(ark, child, father, mother):
    return {'id': ark, 'content': {'gedcomx': {
        'persons': [child, father, mother],
        'relationships': [{'type': GX + 'ParentChild', 'person1': {'resource': '#' + father['id']},
                           'person2': {'resource': '#' + child['id']}},
                          {'type': GX + 'ParentChild', 'person1': {'resource': '#' + mother['id']},
                           'person2': {'resource': '#' + child['id']}}]}}}


SEARCH = {'results': 2, 'entries': [
    entry('QZZ1-AAA',
          persona('p1', 'QZZ1-AAA', 'Ana', 'Zorvalo Quirell', 'Female', [('Christening', '1851', 'Villazul, Nortia')], True),
          persona('p2', 'QZZ1-AAB', 'Luis', 'Zorvalo', 'Male'),
          persona('p3', 'QZZ1-AAC', 'Rosa', 'Quirell', 'Female')),
    entry('QZZ2-BBB',
          persona('q1', 'QZZ2-BBB', 'Ana', 'Zorvalo Brin', 'Female', [('Christening', '1872', 'Montefrío, Sur')], True),
          persona('q2', 'QZZ2-BBC', 'Pedro', 'Zorvalo', 'Male'),
          persona('q3', 'QZZ2-BBD', 'Inés', 'Brin', 'Female')),
]}
RECORD = SEARCH['entries'][0]['content']['gedcomx']
CAPTCHA = (b'<html><head><title>Security check</title></head><body><div class="g-recaptcha"></div>'
           b'Please complete the captcha to continue</body></html>')
LOGIN = b'<html><body><form action="/auth/login"><input type="password" name="p">Sign in</form></body></html>'


class Resp:
    def __init__(self, status, body, ctype='application/json'):
        self.status, self.body, self.headers = status, body, {'content-type': ctype}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeGate:
    def __init__(self, paused=False):
        self.waits, self.hits, self.deads, self.blocks, self.paused = [], [], [], [], paused

    def wait(self, lane):
        if self.paused:
            raise Paused(600, 'a hit 1 minute ago')
        self.waits.append(lane)

    def hit(self, lane, what):
        self.hits.append(what)

    def dead(self, lane, what):
        self.deads.append(what)

    def block(self, hours, why):
        self.blocks.append(hours)

    def denied(self, lane, what):
        raise AssertionError('a 403 is a hit here, never only a denial')


class FakeNet:
    """urlopen stand-in: answers from a queue, and checks the gate was asked before this call."""

    def __init__(self, gate, *answers):
        self.gate, self.answers, self.requests = gate, list(answers), []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        assert len(self.gate.waits) == len(self.requests), 'network call without a gate slot'
        status, body = self.answers.pop(0)
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, 'x', {'content-type': 'text/html'}, io.BytesIO(body))
        return Resp(status, body, 'application/json' if body[:1] in (b'{', b'[') else 'text/html')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.out = os.path.join(self.dir, 'out')
        self.cache = os.path.join(self.dir, 'cache')
        self.gatedir = os.path.join(self.dir, 'gate')
        self.saved_env = dict(os.environ)
        os.environ['GWT_FS_TOKEN'] = TOKEN
        os.environ.pop('GWT_FS_TOKEN_FILE', None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved_env)
        self.tmp.cleanup()

    def run_fs(self, args, net, gate):
        o, e = io.StringIO(), io.StringIO()
        argv = ['familysearch', '--cache-dir', self.cache, '--gate-dir', self.gatedir, '--lane', 't'] + args + \
            ['--out', self.out]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        lines = dict(l.split('\t', 1) for l in o.getvalue().splitlines() if '\t' in l)
        return code, lines, o.getvalue() + e.getvalue()

    def search(self, net, gate, *extra):
        return self.run_fs(['search', '--given', 'Ana', '--surname', 'Zorvalo', '--by', '1851', *extra], net, gate)

    def out_files(self):
        return sorted(os.listdir(self.out)) if os.path.isdir(self.out) else []


class Search(Base):
    def test_search_writes_envelope_and_candidates_gwt_rank_ranks(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, json.dumps(SEARCH).encode()))
        code, lines, _ = self.search(net, gate)
        self.assertEqual(code, 0)
        self.assertEqual(lines['source'], 'familysearch')
        env = json.load(open(lines['evidence']))
        self.assertEqual(env['url'], net.requests[0].full_url)
        self.assertEqual(env['json'], SEARCH)
        self.assertIn('captured_at', env)
        cands = json.load(open(lines['candidates']))['candidates']
        self.assertEqual([c['id'] for c in cands], ['QZZ1-AAA', 'QZZ2-BBB'])
        self.assertIn({'role': 'father', 'name': 'Luis Zorvalo'}, cands[0]['relatives'])
        self.assertTrue(lines['results'].startswith('2 total, 2 read'))
        # gwt-rank reads the candidates file as written, and puts the fitting baptism first
        rows = gwt_rank.load(lines['candidates'])
        ranked = gwt_rank.rank(rows, 'Ana', ['Zorvalo', 'Quirell'], gwt_rank.parse_facts('by=1851 bp=Villazul'))
        self.assertEqual((ranked[0]['id'], ranked[0]['tier']), ('QZZ1-AAA', 'strong'))
        self.assertEqual(ranked[1]['tier'], 'conflict')

    def test_search_url_carries_the_query(self):
        url = gwt_read.search_url('Ana', ['Zorvalo', 'Quirell'], by=1851, years=2, place='Villazul',
                                  father='Luis Zorvalo', mother='Rosa Quirell', collection='9999999')
        self.assertTrue(url.startswith('https://www.familysearch.org/service/search/hr/v2/personas?'))
        for part in ('q.givenName=Ana', 'q.surname=Zorvalo%20Quirell', 'q.birthLikeDate.from=1849',
                     'q.birthLikeDate.to=1853', 'q.anyPlace=Villazul', 'q.fatherGivenName=Luis',
                     'q.fatherSurname=Zorvalo', 'q.motherGivenName=Rosa', 'f.collectionId=9999999', 'count=20'):
            self.assertIn(part, url)

    def test_second_identical_search_is_served_from_cache(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, json.dumps(SEARCH).encode()))
        self.assertEqual(self.search(net, gate)[0], 0)
        code, lines, _ = self.search(net, gate)
        self.assertEqual(code, 0)
        self.assertEqual(len(net.requests), 1, 'the second search went to the network')
        self.assertEqual(len(gate.waits), 1, 'a cached answer still asked the gate')
        self.assertIn('(cache)', lines['results'])

    def test_gate_is_asked_before_every_network_call(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, json.dumps(SEARCH).encode()), (200, json.dumps(RECORD).encode()))
        self.search(net, gate)
        self.run_fs(['record', '1:1:QZZ1-AAA'], net, gate)
        self.assertEqual((len(gate.waits), len(net.requests)), (2, 2))

    def test_paused_gate_makes_no_request(self):
        gate = FakeGate(paused=True)
        net = FakeNet(gate)
        code, _, text = self.search(net, gate)
        self.assertEqual(code, gwt_reader.EXIT_PAUSED)
        self.assertEqual(net.requests, [])
        self.assertIn('do other work', text)

    def test_no_results_exits_2(self):
        gate = FakeGate()
        code, lines, _ = self.search(FakeNet(gate, (200, b'{"results": 0, "entries": []}')), gate)
        self.assertEqual(code, 2)
        self.assertEqual(json.load(open(lines['candidates'])), {'candidates': []})


class Stops(Base):
    def assert_stop(self, status, body, code, gate_list):
        gate = FakeGate()
        net = FakeNet(gate, (status, body), (status, body))
        got, lines, text = self.search(net, gate)
        self.assertEqual(got, code, text)
        self.assertNotIn('candidates', lines)
        self.assertEqual(self.out_files(), [], 'a STOP wrote files')
        self.assertEqual(len(getattr(gate, gate_list)), 1, 'the gate did not hear about it')
        # never cached: the next run asks the site again instead of replaying the block
        self.search(net, gate)
        self.assertEqual(len(net.requests), 2, 'a STOP answer was served from the cache')
        return text

    def test_429_stops_and_pauses(self):
        self.assert_stop(429, b'slow down', gwt_reader.EXIT_WALL, 'hits')

    def test_403_stops_and_blocks_the_account_for_a_day(self):
        gate = FakeGate()
        code, _, text = self.search(FakeNet(gate, (403, b'forbidden')), gate)
        self.assertEqual((code, gate.blocks, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, [24], 1, []))
        self.assertIn('24 h', text)
        self.assert_stop(403, b'forbidden', gwt_reader.EXIT_WALL, 'hits')

    def test_200_captcha_page_is_a_stop(self):
        self.assert_stop(200, CAPTCHA, gwt_reader.EXIT_WALL, 'hits')

    def test_200_login_page_is_a_stop(self):
        self.assert_stop(200, LOGIN, gwt_reader.EXIT_DEAD, 'deads')

    def test_200_empty_frame_is_a_stop(self):
        self.assert_stop(200, b'<html><body><iframe src="/x"></iframe></body></html>', gwt_reader.EXIT_WALL, 'hits')

    def test_401_is_dead(self):
        self.assert_stop(401, b'{"error":"unauthorized"}', gwt_reader.EXIT_DEAD, 'deads')

    def test_unexpected_json_is_not_saved_or_cached(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, b'{"error": "odd"}'), (200, b'{"error": "odd"}'))
        self.assertEqual(self.search(net, gate)[0], 1)
        self.assertEqual(self.search(net, gate)[0], 1)
        self.assertEqual((len(net.requests), self.out_files(), gate.hits), (2, [], []))


class Token(Base):
    def test_token_is_sent_but_never_written_or_logged(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, json.dumps(SEARCH).encode()), (429, b'no'), (200, json.dumps(RECORD).encode()))
        _, _, text1 = self.search(net, gate)
        _, _, text2 = self.run_fs(['search', '--given', 'Otra', '--surname', 'Persona'], net, gate)
        _, _, text3 = self.run_fs(['record', 'https://www.familysearch.org/ark:/61903/1:1:QZZ1-AAA'], net, gate)
        self.assertEqual(net.requests[0].get_header('Authorization'), 'Bearer ' + TOKEN)
        self.assertNotIn(TOKEN, text1 + text2 + text3)
        written = 0
        for d in (self.out, self.cache, self.gatedir):
            for root, _, files in os.walk(d):
                for name in files:
                    with open(os.path.join(root, name), 'rb') as f:
                        self.assertNotIn(TOKEN.encode(), f.read(), name)
                    written += 1
        self.assertGreaterEqual(written, 4, 'the scan read nothing, so it proves nothing')

    def test_no_token_no_request(self):
        os.environ.pop('GWT_FS_TOKEN')
        gate = FakeGate()
        net = FakeNet(gate)
        code, _, text = self.search(net, gate)
        self.assertEqual((code, net.requests, gate.waits), (64, [], []))
        self.assertIn('GWT_FS_TOKEN', text)

    def test_token_file(self):
        os.environ.pop('GWT_FS_TOKEN')
        path = os.path.join(self.dir, 'tok')
        with open(path, 'w') as f:
            f.write(TOKEN + '\n')
        os.environ['GWT_FS_TOKEN_FILE'] = path
        self.assertEqual(gwt_read.token_from_env(), TOKEN)


class Record(Base):
    def test_record_by_ark(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, json.dumps(RECORD).encode()))
        code, lines, _ = self.run_fs(['record', '1:1:QZZ1-AAA'], net, gate)
        self.assertEqual(code, 0)
        self.assertEqual(net.requests[0].full_url, 'https://www.familysearch.org/ark:/61903/1:1:QZZ1-AAA')
        self.assertEqual(net.requests[0].get_header('Accept'), 'application/x-gedcomx-v1+json')
        cands = json.load(open(lines['candidates']))['candidates']
        self.assertEqual([(c['id'], c['name']) for c in cands], [('QZZ1-AAA', 'Ana Zorvalo Quirell')])

    def test_bad_ark_is_refused_without_a_request(self):
        gate = FakeGate()
        net = FakeNet(gate)
        self.assertEqual(self.run_fs(['record', '3:1:NOT-ARECORD'], net, gate)[0], 64)
        self.assertEqual(net.requests, [])
        self.assertEqual(gwt_read.ark_id('https://www.familysearch.org/ark:/61903/1:1:QZZ1-AAA?lang=es'), '1:1:QZZ1-AAA')


class ReaderShape(unittest.TestCase):
    def test_classify_answer(self):
        c = gwt_reader.classify_answer
        self.assertEqual(c(200, {}, b'{"a": 1}'), 'ok')
        self.assertEqual(c(200, {}, b''), 'empty')
        self.assertEqual(c(200, {}, b'  \n'), 'empty')
        self.assertEqual(c(429, {}, b'{}'), 'wall')
        self.assertEqual(c(403, {}, b'{}'), 'blocked')
        self.assertEqual(c(500, {}, b'{}'), 'http')
        self.assertEqual(c(200, {}, b'<div id="_Incapsula_Resource"></div>'), 'wall')
        self.assertEqual(c(200, {}, b'{"x": 1}', expect=lambda d: 'entries' in d), 'unexpected')

    def test_write_search_scrubs_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            env = gwt_reader.envelope('https://example.org/s?q=1', text='Authorization: Bearer abcdefghijklmnopqrstuvwxyz')
            base = gwt_reader.write_search(d, 'k', env, [])
            self.assertNotIn('abcdefghijklmnop', open(base + '.json').read())

    def test_manifest_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            png = os.path.join(d, 'image.png')
            with open(png, 'wb') as f:
                f.write(b'\x89PNG\r\n\x1a\n' + b'\x00' * 8 + (640).to_bytes(4, 'big') + (480).to_bytes(4, 'big'))
            row = gwt_reader.manifest_row(png, 'image', 'https://example.org/i.png', 'free', 'c1', 'i1', image=True)
            self.assertEqual((row['width'], row['height'], row['original']), (640, 480, 'yes'))
            m = os.path.join(d, 'manifest.tsv')
            gwt_reader.write_manifest(m, [row])
            self.assertEqual(gwt_reader.read_manifest(m)[0]['sha256'], row['sha256'])


if __name__ == '__main__':
    unittest.main()
