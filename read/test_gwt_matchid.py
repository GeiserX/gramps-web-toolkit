"""Tests for `gwt-read matchid`. Answers in fixtures/matchid/ keep the API's shape with invented people and ids; a
fake transport and a fake gate: no request leaves this machine. Run: cd read && python3 -m pytest -q test_gwt_matchid.py"""
import contextlib, io, json, os, unittest, urllib.parse

from test_gwt_read import Base, FakeGate, FakeNet
import gwt_matchid  # noqa: E402
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'matchid')
TOKEN = 'mid-ZZ9-invented-token-0123456789abcdef'


def fx(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


class Run(Base):
    def setUp(self):
        super().setUp()
        os.environ.pop(gwt_matchid.TOKEN_ENV, None)

    def search(self, net, gate, *extra):
        o, e = io.StringIO(), io.StringIO()
        argv = ['matchid', '--cache-dir', self.cache, '--lane', 't', 'search', '--given', 'Ana', '--surname', 'Zorvalo',
                *extra, '--out', self.out]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        lines = dict(l.split('\t', 1) for l in o.getvalue().splitlines() if l.count('\t') == 1)
        return code, lines, o.getvalue() + e.getvalue()


class Classify(unittest.TestCase):
    def test_hits_none_and_a_changed_container_is_unknown(self):
        v = gwt_matchid.verdict
        self.assertEqual(v(json.loads(fx('search.json'))), 'hits')
        self.assertEqual(v(json.loads(fx('none.json'))), 'none')
        self.assertEqual(v(json.loads(fx('changed.json'))), 'unknown', 'a renamed result container read as "none"')
        self.assertEqual(v({'response': {'persons': []}}), 'unknown', 'no count is not a zero count')
        self.assertEqual(v({'response': {'total': 4, 'persons': []}}), 'unknown')
        self.assertEqual(v({'response': {'total': 0.0, 'persons': []}}), 'unknown', 'a count that is not a whole number')
        self.assertEqual(v({'response': {'total': 1, 'persons': [{'id': 'a1', 'name': 'Zorvalo'}]}}), 'unknown',
                         'a row whose name is no longer an object')

    def test_only_the_spent_quota_422_is_a_wall(self):
        self.assertTrue(gwt_matchid.quota_spent(422, b'{"msg":"temporary anonymous usage expired"}'))
        self.assertFalse(gwt_matchid.quota_spent(400, b'{"msg":"temporary anonymous usage expired"}'))
        self.assertFalse(gwt_matchid.quota_spent(422, b'{"msg":"invalid birthDate value"}'))

    def test_a_date_range_is_sent_without_spaces(self):
        # matchID answers HTTP 400 "invalid birthDate value" for "01/01/1900 - 31/12/1910", 200 without the spaces
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(
            gwt_matchid.search_url(surname='Zorvalo', born='01/01/1900 - 31/12/1910', died='1950 -1960')).query))
        self.assertEqual((q['birthDate'], q['deathDate']), ('01/01/1900-31/12/1910', '1950-1960'))

    def test_a_page_past_the_last_row_is_the_end_never_a_stop(self):
        past = {'response': {'total': 2, 'persons': []}}
        self.assertEqual(gwt_matchid.verdict(past, 20), 'none', 'page 2 of a 2-row search read as a changed answer')
        self.assertEqual(gwt_matchid.verdict(past, 0), 'unknown')

    def test_get_api_leaves_a_stop_reader_get_already_judged_alone(self):
        # a 503 bot page is a wall Reader.get already told the gate about: get_api must not count it twice
        class R:
            lane, gate = 't', FakeGate()

            def get(self, *a, **k):
                raise gwt_reader.Stop('a bot wall', gwt_reader.EXIT_WALL, 'wall', 503, b'{"error": "busy"}')
        r = R()
        with self.assertRaises(gwt_reader.Stop) as e:
            gwt_reader.get_api(r, 'https://deces.matchid.io/deces/api/v1/search?q=x')
        self.assertEqual((str(e.exception), r.gate.hits), ('a bot wall', []))

    def test_the_pace_keeps_to_one_request_a_second_at_most(self):
        self.assertGreaterEqual(gwt_matchid.SPEC['pace']['floor'], 1.0)
        self.assertEqual(gwt_reader.spec_problems(gwt_matchid.SPEC), [])


class Search(Run):
    def test_rows_are_principals_of_a_death_record(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('search.json')))
        code, lines, text = self.search(net, gate, '--born', '1902')
        self.assertEqual(code, 0, text)
        self.assertIn('firstName=Ana&lastName=Zorvalo&birthDate=1902', net.requests[0].full_url)
        a, b = json.load(open(lines['candidates']))['candidates']
        self.assertEqual((a['name'], a['given'], a['surnames'], a['principal']),
                         ('Ana Rosa Zorvalo', 'Ana Rosa', ['Zorvalo'], True))
        self.assertEqual(a['events'], {'birth': {'year': 1902, 'year_to': 1902, 'place': 'Villazul'},
                                       'death': {'year': 1985, 'year_to': 1985, 'place': 'Montefrío-sur-Mer'}})
        self.assertEqual((a['birth_date'], a['act'], a['insee_file'], a['line']), ('19020311', '42', '1985', 1201))
        self.assertEqual(a['url'], 'https://deces.matchid.io/id/zzAAAA000001')
        self.assertEqual(b['events']['birth']['place'], 'Espagne', 'a birth abroad keeps its country')
        self.assertTrue(lines['results'].startswith('2 total'))

    def test_the_apis_own_zero_is_exit_2(self):
        gate = FakeGate()
        code, lines, _ = self.search(FakeNet(gate, (200, fx('none.json'))), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []))

    def test_paging_past_the_last_row_is_exit_2(self):
        gate = FakeGate()
        past = json.dumps({'response': {'total': 2, 'size': 20, 'page': 2, 'persons': []}}).encode()
        code, lines, text = self.search(FakeNet(gate, (200, past)), gate, '--page', '2')
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []), text)
        self.assertIn('past the end', lines['results'])

    def test_a_changed_answer_is_a_stop_never_zero_results(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('changed.json')), (200, fx('changed.json')))
        code, _, text = self.search(net, gate)
        self.assertEqual((code, self.out_files()), (gwt_reader.EXIT_ERROR, []), text)
        self.search(net, gate)
        self.assertEqual(len(net.requests), 2, 'a changed answer was served from the cache')

    def test_an_outage_is_a_block_never_zero_results(self):
        for status in (500, 502, 503, 504):
            with self.subTest(status):
                gate = FakeGate()
                code, _, text = self.search(FakeNet(gate, (status, b'<html>Bad Gateway</html>')), gate)
                self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)

    def test_a_400_is_an_error_not_a_block(self):
        gate = FakeGate()
        code, _, _ = self.search(FakeNet(gate, (400, b'{"msg":"bad date"}')), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_ERROR, []))

    def test_a_4xx_says_the_apis_own_reason(self):
        gate = FakeGate()
        code, _, text = self.search(FakeNet(gate, (400, b'{"msg":["invalid birthDate value: 31/31/1900"]}')), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_ERROR, []))
        self.assertIn('invalid birthDate value: 31/31/1900', text)

    def test_the_anonymous_quota_is_a_block_never_an_error_to_move_past(self):
        # what matchID answers once the anonymous calls are spent: a lane must pause, not log ERR and send the next one
        body = (b'{"msg":"No token provided and temporary anonymous usage expired, please register with email or '
                b'wait 2 hours"}')
        gate = FakeGate()
        code, _, text = self.search(FakeNet(gate, (422, body)), gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)
        self.assertIn('anonymous usage expired', text)

    def test_the_token_goes_in_a_header_and_never_reaches_a_file(self):
        os.environ[gwt_matchid.TOKEN_ENV] = TOKEN
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('search.json')))
        code, lines, text = self.search(net, gate)
        self.assertEqual(code, 0, text)
        self.assertEqual(net.requests[0].get_header('Authorization'), 'Bearer ' + TOKEN)
        self.assertNotIn(TOKEN, net.requests[0].full_url)
        self.assertNotIn(TOKEN, text)
        for root, _, files in os.walk(self.dir):
            for name in files:
                with open(os.path.join(root, name), 'rb') as f:
                    self.assertNotIn(TOKEN.encode(), f.read(), name)

    def test_without_a_token_no_authorization_header(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('search.json')))
        self.search(net, gate)
        self.assertIsNone(net.requests[0].get_header('Authorization'))


if __name__ == '__main__':
    unittest.main()
