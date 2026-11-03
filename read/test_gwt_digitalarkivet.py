"""Tests for `gwt-read digitalarkivet`. Pages in fixtures/digitalarkivet/ keep the site's markup with invented people
and ids; a fake transport and a fake gate: no request leaves this machine. Run: cd read && python3 -m pytest -q
test_gwt_digitalarkivet.py"""
import contextlib, io, json, os, unittest, urllib.parse

from test_gwt_read import Base, FakeGate, FakeNet
import gwt_digitalarkivet  # noqa: E402
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'digitalarkivet')


def fx(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


class Run(Base):
    def search(self, net, gate, *extra):
        o, e = io.StringIO(), io.StringIO()
        argv = ['digitalarkivet', '--cache-dir', self.cache, '--lane', 't', 'search', '--name', 'Fjellmo', *extra,
                '--out', self.out]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        lines = dict(l.split('\t', 1) for l in o.getvalue().splitlines() if l.count('\t') == 1)
        return code, lines, o.getvalue() + e.getvalue()


class Shape(unittest.TestCase):
    def test_three_states(self):
        spec = gwt_digitalarkivet.SPEC
        self.assertEqual(gwt_reader.classify(fx('results.html').decode(), spec), 'hits')
        self.assertEqual(gwt_reader.classify(fx('none.html').decode(), spec), 'none')
        self.assertEqual(gwt_reader.classify(fx('changed.html').decode(), spec), 'unknown',
                         'an empty result list with a count above zero read as rows')

    def test_the_pace_keeps_to_the_robots_crawl_delay(self):
        self.assertGreaterEqual(gwt_digitalarkivet.SPEC['pace']['floor'], 5.0)
        self.assertEqual(gwt_reader.spec_problems(gwt_digitalarkivet.SPEC), [])


class Search(Run):
    def test_rows_are_persons_with_their_own_page(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('results.html')))
        code, lines, text = self.search(net, gate, '--from', '1890', '--page', '2')
        self.assertEqual(code, 0, text)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(net.requests[0].full_url).query))
        self.assertEqual(q, {'s': 'Fjellmo', 'event_year_from': '1890', 'page': '2'})
        a, b = json.load(open(lines['candidates']))['candidates']
        self.assertEqual((a['id'], a['name'], a['given'], a['surnames'], a['sex'], a['born']),
                         ('pf09999999000001', 'Ola Kristian Fjellmo', 'Ola Kristian', ['Fjellmo'], 'male',
                          '1871-04-02, Nordvik'))
        self.assertEqual(a['url'], 'https://www.digitalarkivet.no/en/census/person/pf09999999000001')
        self.assertEqual((a['record_source'], a['source_url']),
                         ('1900 census for Nordvik', 'https://www.digitalarkivet.no/en/source/99001'))
        self.assertEqual(a['events'], {'event': {'label': 'H', 'date': '1900-12-03'}})
        self.assertTrue(a['principal'])
        self.assertEqual((b['role'], b['principal']), ('mor', False), 'a mother in an entry is not its principal')
        self.assertTrue(lines['results'].startswith('2 total, 2 read'))

    def test_the_sites_zero_count_is_exit_2(self):
        gate = FakeGate()
        code, lines, _ = self.search(FakeNet(gate, (200, fx('none.html'))), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []))

    def test_a_changed_layout_is_a_stop_never_zero_results(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('changed.html')), (200, fx('changed.html')))
        code, lines, text = self.search(net, gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)
        self.search(net, gate)
        self.assertEqual(len(net.requests), 2, 'a STOP page was served from the cache')

    def test_a_relative_link_and_any_id_form_are_read(self):
        page = fx('results.html').replace(b'href="https://www.digitalarkivet.no/en/view/255/pd00000099999002"',
                                          b'href="/en/view/255/A0099"')
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, page)), gate)
        self.assertEqual(code, 0, text)
        b = json.load(open(lines['candidates']))['candidates'][1]
        self.assertEqual((b['id'], b['url']), ('A0099', 'https://www.digitalarkivet.no/en/view/255/A0099'))

    def test_a_person_row_the_reader_cannot_read_is_a_stop_never_fewer_rows(self):
        page = fx('results.html').replace(b'<div class="name">', b'<div class="person-name">', 1)
        gate = FakeGate()
        net = FakeNet(gate, (200, page), (200, page))
        code, _, text = self.search(net, gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)
        self.search(net, gate)
        self.assertEqual(len(net.requests), 2, 'a page with an unread row was served from the cache')

    def past_end_page(self):
        page = fx('results.html').decode()
        head, rest = page.split('<div id="searchResultList" class="result-list clearfix">', 1)
        return (head.replace('data-hits="2">Your search resulted in 2 hits', 'data-hits="101">Your search resulted in '
                             '101 hits') + '<div id="searchResultList" class="result-list clearfix">\n\t</div>'
                '<ul class="pagination"></ul></section></body></html>').encode()

    def test_a_page_past_the_last_is_exit_2_past_the_end(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, self.past_end_page())), gate, '--page', '5')
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []), text)
        self.assertIn('past the end', lines['results'])

    def test_an_empty_page_before_the_last_is_still_a_stop(self):
        gate = FakeGate()
        code, _, text = self.search(FakeNet(gate, (200, self.past_end_page())), gate, '--page', '3')
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)

    def test_an_empty_page_with_no_count_is_a_stop_never_past_the_end(self):
        page = self.past_end_page().replace(b'data-hits="101"', b'')
        gate = FakeGate()
        code, _, text = self.search(FakeNet(gate, (200, page)), gate, '--page', '5')
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)

    def test_a_row_the_reader_cannot_read_past_the_count_is_a_stop_never_past_the_end(self):
        page = self.past_end_page().replace(b'clearfix">\n\t</div>',
                                            b'clearfix">\n<div class="unit"><div class="person-name">Ola</div></div></div>')
        gate = FakeGate()
        code, _, text = self.search(FakeNet(gate, (200, page)), gate, '--page', '5')
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)

    def test_a_page_the_site_never_shows_is_refused_before_any_request(self):
        for page in ('201', '0'):
            gate = FakeGate()
            net = FakeNet(gate)
            code, _, text = self.search(net, gate, '--page', page)
            self.assertEqual((code, net.requests), (64, []), (page, text))

    def test_an_empty_name_is_refused_before_any_request(self):
        for name in ('', '   '):
            gate = FakeGate()
            net = FakeNet(gate)
            o = io.StringIO()
            with contextlib.redirect_stdout(o), contextlib.redirect_stderr(o):
                code = gwt_read.main(['digitalarkivet', '--cache-dir', self.cache, 'search', '--name', name, '--out',
                                      self.out], urlopen=net, gate=gate)
            self.assertEqual((code, net.requests), (64, []), o.getvalue())


if __name__ == '__main__':
    unittest.main()
