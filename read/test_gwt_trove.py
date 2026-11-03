"""Tests for `gwt-read trove`. Answers in fixtures/trove/ keep the v3 API's shape with invented newspapers and people;
a fake transport and a fake gate: no request leaves this machine. Run: cd read && python3 -m pytest -q test_gwt_trove.py"""
import contextlib, io, json, os, unittest, urllib.parse

from test_gwt_read import Base, FakeGate, FakeNet
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402
import gwt_trove  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'trove')
KEY = 'trove-ZZ9-invented-key-0123456789'


def fx(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


SEARCH, NONE, CHANGED, UNAUTH = fx('search.json'), fx('none.json'), fx('changed.json'), fx('unauthorized.json')


class Classify(unittest.TestCase):
    def test_hits_none_and_anything_else_is_unknown(self):
        v = gwt_trove.verdict
        self.assertEqual((v(json.loads(SEARCH)), v(json.loads(NONE))), ('hits', 'none'))
        self.assertEqual(v(json.loads(UNAUTH)), 'unknown')
        self.assertEqual(v({'category': []}), 'unknown', 'no newspaper category is not zero articles')
        self.assertEqual(v({'category': [{'code': 'newspaper', 'records': {'article': []}}]}), 'unknown',
                         'no total is not a zero total')
        self.assertEqual(v({'category': [{'code': 'newspaper', 'records': {'total': True}}]}), 'unknown')
        self.assertEqual(v({'category': [{'code': 'newspaper', 'records': {'total': 1, 'article': [{'heading': 'x'}]}}]}),
                         'unknown', 'an article with no id')

    def test_a_missing_result_container_is_never_none(self):
        self.assertEqual(gwt_trove.verdict(json.loads(CHANGED)), 'unknown',
                         'a total of 37 with the articles gone read as "none"')

    def test_a_cursor_past_the_end_is_none(self):
        last = {'category': [{'code': 'newspaper', 'records': {'total': 37, 'article': []}}]}
        self.assertEqual((gwt_trove.verdict(last, 'AoIIQ0ZZ'), gwt_trove.verdict(last, '*')), ('none', 'unknown'))

    def test_rows_are_articles_with_their_text(self):
        a, b = gwt_trove.candidates(json.loads(SEARCH))
        self.assertEqual((a['id'], a['name'], a['date'], a['year'], a['title'], a['type'], a['page']),
                         ('999000001', 'The Villazul Advertiser (Vic. : 1880 - 1920)', '1903-08-21', 1903, 'Family Notices',
                          'Family Notices', '2'))
        self.assertEqual(a['url'], 'https://trove.nla.gov.au/newspaper/article/999000001')
        self.assertIn('Ann Zorvalo , relict of the late Peter Quirell', a['text'])
        self.assertNotIn('<', a['text'])
        self.assertIn('first prize', b['text'], 'an article with no text keeps its snippet')

    def test_the_url(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(gwt_trove.search_url(
            '"Ann Zorvalo"', '1890', '1910', 'Family Notices', 'Victoria', 500)).query))
        self.assertEqual(q['q'], '"Ann Zorvalo" date:[1890-01-01T00:00:00Z TO 1910-12-31T23:59:59Z]')
        self.assertEqual((q['category'], q['n'], q['s'], q['include'], q['l-category'], q['l-state']),
                         ('newspaper', '100', '*', 'articletext', 'Family Notices', 'Victoria'))
        self.assertNotIn('key', q, 'the key must never be in the URL, which the cache and the envelope keep')

    def test_spec(self):
        self.assertEqual(gwt_reader.spec_problems(gwt_trove.SPEC), [])


class Search(Base):
    def setUp(self):
        super().setUp()
        os.environ[gwt_trove.KEY_ENV] = KEY

    def run_t(self, net, gate, *args):
        o, e = io.StringIO(), io.StringIO()
        argv = ['trove', '--cache-dir', self.cache, '--lane', 't', *args]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        lines = dict(l.split('\t', 1) for l in o.getvalue().splitlines() if l.count('\t') == 1)
        return code, lines, o.getvalue() + e.getvalue()

    def search(self, net, gate, *extra):
        return self.run_t(net, gate, 'search', '--query', '"Ann Zorvalo"', *extra, '--out', self.out)

    def test_rows_text_and_the_next_cursor(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, SEARCH))
        code, lines, text = self.search(net, gate)
        self.assertEqual(code, 0, text)
        self.assertEqual(net.requests[0].get_header('X-api-key'), KEY, 'the key was not sent')
        self.assertEqual(lines['next'], 'AoIIQ0ZZ')
        self.assertEqual(lines['results'], '37 total, 2 read')
        env = json.load(open(lines['evidence']))
        self.assertIn('relict of the late Peter Quirell', env['text'])
        for root, _, names in os.walk(self.dir):
            for n in names:
                with open(os.path.join(root, n), 'rb') as f:
                    self.assertNotIn(KEY.encode(), f.read(), 'the key reached %s' % n)

    def test_no_key_exits_4_before_any_request(self):
        del os.environ[gwt_trove.KEY_ENV]
        gate = FakeGate()
        net = FakeNet(gate)
        code, lines, text = self.search(net, gate)
        self.assertEqual((code, net.requests), (gwt_reader.EXIT_DEAD, []))
        self.assertIn('GWT_TROVE_KEY', text)
        code, _, _ = self.run_t(net, gate, 'control')
        self.assertEqual((code, net.requests), (gwt_reader.EXIT_DEAD, []), 'a control without a key ran')

    def test_a_401_is_a_dead_key(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (401, UNAUTH)), gate)
        self.assertEqual((code, len(gate.deads), self.out_files()), (gwt_reader.EXIT_DEAD, 1, []), text)

    def test_none_is_exit_2(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, NONE)), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []), text)
        self.assertNotIn('next', lines)

    def test_a_changed_answer_is_a_stop(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, CHANGED)), gate)
        self.assertEqual((code, self.out_files()), (gwt_reader.EXIT_ERROR, []), text)

    def test_a_429_is_a_wall(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (429, b'{"statusCode":429}')), gate)
        self.assertEqual((code, len(gate.hits)), (gwt_reader.EXIT_WALL, 1), text)


if __name__ == '__main__':
    unittest.main()
