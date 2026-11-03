"""Tests for `gwt-read digitalnz` (Papers Past through the DigitalNZ API). Answers in fixtures/digitalnz/ keep the
API's shape with invented articles, people and papers; a fake transport and a fake gate: no request leaves this
machine. Run: cd read && python3 -m pytest -q test_gwt_digitalnz.py"""
import contextlib, io, json, os, unittest, urllib.parse

from test_gwt_read import Base, FakeGate, FakeNet
import gwt_digitalnz  # noqa: E402
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'digitalnz')


def fx(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


def query(url):
    return urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)


class Run(Base):
    def search(self, net, gate, *extra):
        o, e = io.StringIO(), io.StringIO()
        argv = ['digitalnz', '--cache-dir', self.cache, '--lane', 't', 'search', '--query', 'Pedro Zorvalo', *extra,
                '--out', self.out]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        out = o.getvalue()
        lines = dict(l.split('\t', 1) for l in out.splitlines() if l.count('\t') == 1)
        items = [l.split('\t')[1:] for l in out.splitlines() if l.startswith('item\t')]
        return code, lines, items, out + e.getvalue()


class Classify(unittest.TestCase):
    def test_hits_none_and_a_changed_container_is_unknown(self):
        v = gwt_digitalnz.verdict
        self.assertEqual(v(json.loads(fx('search.json'))), 'hits')
        self.assertEqual(v(json.loads(fx('none.json'))), 'none')
        self.assertEqual(v(json.loads(fx('changed.json'))), 'unknown', 'a renamed result container read as "none"')
        self.assertEqual(v({'search': {'results': []}}), 'unknown', 'no count is not a zero count')
        self.assertEqual(v({'search': {'result_count': '0', 'results': []}}), 'unknown', 'a count that is a string')
        self.assertEqual(v({'search': {'result_count': 5, 'results': []}}), 'unknown', 'a count above zero, no rows')
        self.assertEqual(v({'search': {'result_count': 1, 'results': [{'id': 1}]}}), 'unknown',
                         'an article with no link')

    def test_a_page_past_the_last_row_is_the_end_never_a_stop(self):
        past = {'search': {'result_count': 2, 'results': []}}
        self.assertEqual(gwt_digitalnz.verdict(past, 20), 'none')
        self.assertEqual(gwt_digitalnz.verdict(past, 0), 'unknown')

    def test_spec_and_pace(self):
        self.assertEqual(gwt_reader.spec_problems(gwt_digitalnz.SPEC), [])
        self.assertGreaterEqual(gwt_digitalnz.SPEC['pace']['floor'], 2.0)

    def test_every_search_is_kept_to_papers_past(self):
        q = query(gwt_digitalnz.search_url('Zorvalo', 1901, 500, 2))
        self.assertIn(('and[primary_collection][]', 'Papers Past'), q)
        self.assertIn(('and[year][]', '1901'), q)
        self.assertEqual((dict(q)['per_page'], dict(q)['page']), ('100', '2'))
        self.assertIn('fulltext', dict(q)['fields'].split(','))

    def test_the_snippet_is_the_words_around_the_query(self):
        text = 'a ' * 300 + 'ana daughter of mr pedro zorvalo of villazul' + ' b' * 300
        s = gwt_digitalnz.snippet(text, 'Pedro Zorvalo')
        self.assertIn('pedro zorvalo of villazul', s)
        self.assertTrue(s.startswith('...') and s.endswith('...'))
        self.assertLess(len(s), 2 * gwt_digitalnz.AROUND + 30)
        self.assertEqual(gwt_digitalnz.snippet(None, 'Zorvalo'), '')
        self.assertEqual(gwt_digitalnz.snippet('nothing here', 'Zorvalo'), '')


class Search(Run):
    def test_rows_are_articles_with_their_links(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('search.json')))
        code, lines, items, text = self.search(net, gate, '--year', '1901')
        self.assertEqual(code, 0, text)
        self.assertIn(('and[primary_collection][]', 'Papers Past'), query(net.requests[0].full_url))
        a, b = json.load(open(lines['candidates']))['candidates']
        self.assertEqual((a['id'], a['name'], a['role'], a['principal']),
                         ('99000001', 'MARRIAGE. (Kowhai Courier, 3 May 1901)', 'article', False))
        self.assertEqual((a['date'], a['year'], a['publisher']), ('1901-05-03', 1901, 'Kowhai Courier'))
        self.assertEqual(a['url'], 'https://paperspast.natlib.govt.nz/newspapers/ZZ19010503.2.1')
        self.assertEqual(a['text_url'], 'https://api.digitalnz.org/records/99000001.json?fields=fulltext')
        self.assertIn('pedro zorvalo of villazul', a['snippet'])
        self.assertEqual(b['snippet'], '', 'an article with no text has no snippet')
        self.assertEqual(items[0], ['99000001', a['url'], a['text_url']])
        self.assertTrue(lines['results'].startswith('2 total'))

    def test_the_apis_own_zero_is_exit_2(self):
        gate = FakeGate()
        code, _, _, _ = self.search(FakeNet(gate, (200, fx('none.json'))), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []))

    def test_paging_past_the_last_row_is_exit_2(self):
        gate = FakeGate()
        past = json.dumps({'search': {'page': 2, 'per_page': 20, 'result_count': 2, 'results': []}}).encode()
        code, lines, _, text = self.search(FakeNet(gate, (200, past)), gate, '--page', '2')
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []), text)
        self.assertIn('past the end', lines['results'])

    def test_a_page_past_what_anonymous_use_may_read_asks_nothing(self):
        gate = FakeGate()
        net = FakeNet(gate)
        code, _, _, text = self.search(net, gate, '--page', '101')
        self.assertEqual((code, net.requests), (gwt_digitalnz.EXIT_USAGE, []), text)
        self.assertIn('pages 1 to 100', text)
        code, _, _, text = self.search(FakeNet(gate, (200, fx('search.json'))), gate, '--page', '100')
        self.assertNotEqual(code, gwt_digitalnz.EXIT_USAGE, text)

    def test_a_changed_answer_is_a_stop_never_zero_results(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('changed.json')), (200, fx('changed.json')))
        code, _, _, text = self.search(net, gate)
        self.assertEqual((code, self.out_files()), (gwt_reader.EXIT_ERROR, []), text)
        self.search(net, gate)
        self.assertEqual(len(net.requests), 2, 'a changed answer was served from the cache')

    def test_an_empty_query_asks_nothing(self):
        gate = FakeGate()
        net = FakeNet(gate)
        code, _, _, _ = self.search(net, gate, '--query', '  ')
        self.assertEqual((code, net.requests), (gwt_digitalnz.EXIT_USAGE, []))

    def test_an_outage_is_a_block_never_zero_results(self):
        gate = FakeGate()
        code, _, _, text = self.search(FakeNet(gate, (503, b'<html>Service Unavailable</html>')), gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)


if __name__ == '__main__':
    unittest.main()
