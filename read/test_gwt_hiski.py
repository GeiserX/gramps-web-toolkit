"""Tests for `gwt-read hiski`. Pages in fixtures/hiski/ keep the site's markup and encoding (ISO-8859-1) with invented
people; a fake transport and a fake gate: no request leaves this machine. Run: cd read && python3 -m pytest -q
test_gwt_hiski.py"""
import contextlib, io, json, os, unittest, urllib.parse

from test_gwt_read import Base, FakeGate, FakeNet
import gwt_hiski  # noqa: E402
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'hiski')
# A real Cloudflare challenge loads its script from /cdn-cgi/challenge-platform/h/... with an inline script, the way
# the beacon does: that is a wall.
CHALLENGE = (b'<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>'
             b'<script>(function(){window._cf_chl_opt={cType:\'managed\'};var a=document.createElement(\'script\');'
             b'a.src=\'/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1?ray=0\';document.head.appendChild(a);'
             b'}());</script></body></html>')


def fx(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


class Run(Base):
    def search(self, net, gate, *extra, book='marriages'):
        o, e = io.StringIO(), io.StringIO()
        argv = ['hiski', '--cache-dir', self.cache, '--lane', 't', 'search', '--parish', '0999', '--book', book,
                *extra, '--out', self.out]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        lines = dict(l.split('\t', 1) for l in o.getvalue().splitlines() if l.count('\t') == 1)
        return code, lines, o.getvalue() + e.getvalue()


class Shape(unittest.TestCase):
    def test_three_states(self):
        page = lambda n: gwt_hiski.decode(fx(n))
        self.assertEqual(gwt_reader.classify(page('results.html'), gwt_hiski.SPEC), 'hits')
        self.assertEqual(gwt_reader.classify(page('none.html'), gwt_hiski.SPEC), 'none')
        self.assertEqual(gwt_reader.classify(page('changed.html'), gwt_hiski.SPEC), 'unknown')

    def test_cloudflares_beacon_is_not_a_wall_and_its_challenge_is(self):
        ok = gwt_reader.page_check(gwt_hiski.SPEC)
        self.assertIn(b'/cdn-cgi/challenge-platform/scripts/jsd/main.js', fx('results.html'))
        self.assertEqual(gwt_reader.classify_answer(200, {}, fx('results.html'), page=ok), 'ok')
        self.assertEqual(gwt_reader.classify_answer(200, {}, CHALLENGE, page=ok), 'wall')
        both = fx('results.html').replace(b'</BODY>', CHALLENGE + b'</BODY>')
        self.assertEqual(gwt_reader.classify_answer(200, {}, both, page=ok), 'wall', 'a challenge beside a beacon')

    def test_the_pace(self):
        self.assertGreaterEqual(gwt_hiski.SPEC['pace']['floor'], 5.0)
        self.assertEqual(gwt_reader.spec_problems(gwt_hiski.SPEC), [])

    def test_many_unclosed_scripts_are_read_in_linear_time(self):
        import time
        hostile = (b'<script>' * 8000)[:65536]
        t = time.monotonic()
        self.assertEqual(gwt_reader.classify_answer(200, {}, hostile), 'empty')
        self.assertLess(time.monotonic() - t, 0.5, 'the beacon check is quadratic on unclosed <script> tags')
        beacon = b'<script>x="/cdn-cgi/challenge-platform/scripts/jsd/main.js"</script>'
        self.assertEqual(gwt_reader.classify_answer(200, {}, b'<script>' * 3000 + beacon + b'<p>ok</p>',
                                                    page=lambda p: 'ok' in p), 'ok')
        outside = b'<script>a</script><p>/cdn-cgi/challenge-platform/scripts/jsd/main.js</p><script>b</script>'
        self.assertEqual(gwt_reader.classify_answer(200, {}, outside), 'wall',
                         'the beacon path outside a <script> element is not left out')


class Search(Run):
    def test_rows_are_entries_with_their_own_page(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('results.html')))
        code, lines, text = self.search(net, gate, '--given', 'Matti', '--village', 'Järvelä', '--from', '1790')
        self.assertEqual(code, 0, text)
        u = net.requests[0].full_url
        self.assertIn('ikyla=J%E4rvel%E4', u, 'the CGI reads ISO-8859-1')
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(u).query, encoding='iso-8859-1'))
        self.assertEqual((q['komento'], q['srk'], q['kirja'], q['kieli'], q['ietunimi'], q['alkuvuosi'], q['maxkpl']),
                         ('haku', '0999', 'vihityt', 'en', 'Matti', '1790', '50'))
        a, b = json.load(open(lines['candidates']))['candidates']
        self.assertEqual((a['id'], a['name'], a['role'], a['url']),
                         ('0999-vihityt-3', 'Torp. Matti Virtakorpi', 'Man', 'https://hiski.genealogia.fi/hiski?en+0999+vihityt+3'))
        self.assertEqual(a['relatives'], [{'role': 'Wife', 'name': 'p. Liisa Heikintytär'}])
        self.assertEqual(a['events'], {'vihityt': {'date': '12.2.1790'}})
        self.assertEqual(b['columns'][-1], ['Farm', 'Mäkelä.'])
        self.assertTrue(lines['results'].startswith('2 total, 2 read'))

    def test_any_form_field_by_its_own_name(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('none.html')))
        self.search(net, gate, '--field', 'etunimi=Anna', '--field', 'aetunimi=Maria', book='baptisms')
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(net.requests[0].full_url).query))
        self.assertEqual((q['kirja'], q['etunimi'], q['aetunimi']), ('kastetut', 'Anna', 'Maria'))

    def test_the_sites_own_no_events_sentence_is_exit_2(self):
        gate = FakeGate()
        code, lines, _ = self.search(FakeNet(gate, (200, fx('none.html'))), gate, '--surname', 'Qzxwort')
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []))
        self.assertEqual(json.load(open(lines['evidence']))['verdict'], 'none')

    def test_a_parish_list_instead_of_results_is_a_stop(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('changed.html')), (200, fx('changed.html')))
        code, lines, text = self.search(net, gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)
        self.search(net, gate)
        self.assertEqual(len(net.requests), 2, 'a STOP page was served from the cache')

    def test_a_challenge_is_a_wall(self):
        gate = FakeGate()
        code, _, _ = self.search(FakeNet(gate, (200, CHALLENGE)), gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []))

    def test_a_parish_code_that_is_not_four_digits_is_refused(self):
        gate = FakeGate()
        net = FakeNet(gate)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            gwt_read.main(['hiski', 'search', '--parish', 'Espoo', '--book', 'marriages', '--out', self.out],
                          urlopen=net, gate=gate)
        self.assertEqual(net.requests, [])

    def test_rows_with_attributes_are_read(self):
        page = fx('results.html').replace(b'<TR><TD><a', b'<TR BGCOLOR="#eeeeee"><TD ALIGN=left><a')
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, page)), gate)
        self.assertEqual(code, 0, text)
        self.assertEqual(len(json.load(open(lines['candidates']))['candidates']), 2)
        self.assertTrue(lines['results'].startswith('2 total, 2 read'))

    def test_an_entry_row_the_reader_cannot_read_is_a_stop_never_none(self):
        page = fx('results.html').replace(b'<TR><TD><a', b'<TR><TD><span>*</span><a')
        gate = FakeGate()
        net = FakeNet(gate, (200, page), (200, page))
        code, _, text = self.search(net, gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)
        self.search(net, gate)
        self.assertEqual(len(net.requests), 2, 'a page with an unread row was served from the cache')

    def test_a_book_the_parish_does_not_keep_is_a_usage_error_not_a_block(self):
        gate = FakeGate()
        code, _, text = self.search(FakeNet(gate, (200, fx('no-book.html'))), gate, book='baptisms')
        self.assertEqual((code, gate.hits, self.out_files()), (64, [], []), text)
        self.assertIn('no baptisms book', text)
        self.assertIn('https://hiski.genealogia.fi/hiski?en+0999', text)

    def test_a_parish_code_the_site_does_not_have_is_a_usage_error_not_a_block(self):
        page = fx('no-book.html').replace('Kylämaa - christened'.encode('iso-8859-1'), b'unknown parish - married')
        gate = FakeGate()
        code, _, text = self.search(FakeNet(gate, (200, page)), gate)
        self.assertEqual((code, gate.hits, self.out_files()), (64, [], []), text)
        self.assertIn('no parish 0999', text)

    def test_a_field_without_a_name_or_outside_iso_8859_1_is_refused(self):
        for extra in (('--field', 'aetunimi'), ('--field', '=Maria'), ('--given', 'Jōhan'),
                      ('--field', 'aetunimi=Ŝara')):
            gate = FakeGate()
            net = FakeNet(gate)
            code, _, text = self.search(net, gate, *extra)
            self.assertEqual((code, net.requests), (64, []), (extra, text))


if __name__ == '__main__':
    unittest.main()
