"""Tests for `gwt-read geneteka`. Answers in fixtures/geneteka/ keep the shape of the site's api/getAct.php with
invented people and ids; a fake transport and a fake gate: no request leaves this machine. Run: cd read && python3 -m
pytest -q test_gwt_geneteka.py"""
import contextlib, io, json, os, unittest, urllib.parse

from test_gwt_read import Base, FakeGate, FakeNet
import gwt_geneteka  # noqa: E402
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'geneteka')


def fx(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


class Run(Base):
    def search(self, net, gate, *extra):
        o, e = io.StringIO(), io.StringIO()
        argv = ['geneteka', '--cache-dir', self.cache, '--lane', 't', 'search', '--region', '07mz', '--surname',
                'Zbrowik', *extra, '--out', self.out]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        lines = dict(l.split('\t', 1) for l in o.getvalue().splitlines() if l.count('\t') == 1)
        return code, lines, o.getvalue() + e.getvalue()


class Shape(unittest.TestCase):
    def test_the_pace_keeps_to_the_robots_crawl_delay(self):
        # robots.txt: Crawl-delay: 120. The registry builds the gate from the module's pace, so read it there too.
        for pace in (gwt_geneteka.SPEC['pace'], gwt_read.sources()['geneteka']['pace']):
            self.assertGreaterEqual(pace['floor'], 120.0)
            self.assertGreaterEqual(pace['start'], 120.0)
        self.assertEqual(gwt_reader.spec_problems(gwt_geneteka.SPEC), [])

    def test_verdict(self):
        v = gwt_geneteka.verdict
        self.assertEqual(v(json.loads(fx('births.json'))), 'hits')
        self.assertEqual(v(json.loads(fx('none.json'))), 'none')
        self.assertEqual(v(json.loads(fx('changed.json'))), 'unknown', 'rows that are no longer cell lists')
        self.assertEqual(v({'data': []}), 'unknown', 'no count is not a zero count')
        self.assertEqual(v({'recordsFiltered': 0, 'rows': []}), 'unknown', 'a renamed row list read as "none"')
        self.assertEqual(v({'recordsFiltered': 3, 'data': []}), 'unknown')
        self.assertEqual(v({'recordsFiltered': 3, 'data': []}, 50), 'none')
        rows = json.loads(fx('births.json'))['data']
        self.assertEqual(v({'recordsFiltered': 330, 'data': rows}, 0, 10), 'unknown', 'a cut page read as the whole page')
        self.assertEqual(v({'recordsFiltered': 2, 'data': rows}, 0, 50), 'hits', 'the last rows of a search')
        self.assertEqual(v({'recordsFiltered': 12, 'data': rows}, 10, 10), 'hits', 'the last page')


class Search(Run):
    def test_rows_are_births_with_parents_and_the_entrys_number(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('births.json')))
        code, lines, text = self.search(net, gate, '--given', 'Jan', '--from', '1840')
        self.assertEqual(code, 0, text)
        req = net.requests[0]
        # without the headers of the page's own table, the site answers only the first few rows
        self.assertEqual(req.get_header('X-requested-with'), 'XMLHttpRequest')
        self.assertTrue(req.get_header('Referer').startswith('https://geneteka.genealodzy.pl/index.php?op=gt'))
        u = urllib.parse.urlsplit(req.full_url)
        q = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
        self.assertEqual(u.path, '/api/getAct.php')
        self.assertEqual((q['op'], q['lang'], q['bdm'], q['w'], q['rid'], q['search_lastname'], q['search_name'],
                          q['from_date'], q['length'], q['start']),
                         ('gt', 'eng', 'B', '07mz', 'B', 'Zbrowik', 'Jan', '1840', '50', '0'))
        a, b = json.load(open(lines['candidates']))['candidates']
        self.assertEqual((a['id'], a['name'], a['act']), ('B-91000001', 'Jan Zbrowik', '12'))
        self.assertEqual(a['relatives'], [{'role': 'father', 'name': 'Piotr'}, {'role': 'mother', 'name': 'Anna Wielosz'}])
        self.assertEqual(a['events'], {'birth': {'year': '1843', 'place': 'Nowa Wola', 'parish': 'Nowa Wola'}})
        self.assertEqual(a['link'], 'https://metryki.genealodzy.pl/metryki.php?op=kt&ar=99&zs=0001a&sy=1843&kt=1')
        self.assertEqual(a['remarks'], 'Remarks: invented remark')
        self.assertTrue(a['url'].startswith('https://geneteka.genealodzy.pl/index.php?op=gt&lang=eng&bdm=B&w=07mz'))
        self.assertIsNone(b['link'])
        self.assertTrue(lines['results'].startswith('2 total, 2 read'))

    def test_a_parish_and_another_kind(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('none.json')))
        self.search(net, gate, '--kind', 'deaths', '--parish', '1234')
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(net.requests[0].full_url).query))
        self.assertEqual((q['bdm'], q['rid']), ('D', '1234'))

    def test_the_sites_own_zero_is_exit_2(self):
        gate = FakeGate()
        code, lines, _ = self.search(FakeNet(gate, (200, fx('none.json'))), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []))

    def test_a_changed_answer_is_a_stop_never_zero_results(self):
        gate = FakeGate()
        code, _, text = self.search(FakeNet(gate, (200, fx('changed.json'))), gate)
        self.assertEqual((code, self.out_files()), (gwt_reader.EXIT_ERROR, []), text)

    def test_a_cut_answer_is_a_stop_never_a_short_list(self):
        # seen live: 6 rows of 330 for a request without the page's headers
        doc = json.loads(fx('births.json'))
        doc['recordsFiltered'] = 330
        gate = FakeGate()
        code, _, text = self.search(FakeNet(gate, (200, json.dumps(doc).encode())), gate, '--count', '10')
        self.assertEqual((code, self.out_files()), (gwt_reader.EXIT_ERROR, []), text)

    def test_an_html_page_instead_of_the_answer_is_a_stop(self):
        gate = FakeGate()
        code, _, text = self.search(FakeNet(gate, (200, b'<html><body>Geneteka</body></html>')), gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)

    def test_an_outage_is_a_block(self):
        gate = FakeGate()
        code, _, _ = self.search(FakeNet(gate, (502, b'Bad Gateway')), gate)
        self.assertEqual((code, len(gate.hits)), (gwt_reader.EXIT_WALL, 1))

    def test_a_repeated_search_costs_no_request(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('births.json')))
        self.search(net, gate)
        code, _, _ = self.search(net, gate)
        self.assertEqual((code, len(net.requests)), (0, 1))

    def test_a_parish_that_is_not_a_number_is_refused_before_any_request(self):
        gate = FakeGate()
        net = FakeNet(gate)
        code, _, _ = self.search(net, gate, '--parish', 'Nowa Wola')
        self.assertEqual((code, net.requests), (64, []))

    def test_an_empty_surname_is_refused_before_any_request(self):
        for surname in ('', '  '):
            gate = FakeGate()
            net = FakeNet(gate)
            code, _, text = self.search(net, gate, '--surname', surname)
            self.assertEqual((code, net.requests), (64, []), text)


if __name__ == '__main__':
    unittest.main()
