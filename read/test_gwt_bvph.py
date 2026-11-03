"""Tests for `gwt-read bvph`. The pages in fixtures/bvph/ keep the site's markup with invented newspapers and people; a
fake transport and a fake gate: no request leaves this machine. Run: cd read && python3 -m pytest -q test_gwt_bvph.py"""
import contextlib, io, json, os, unittest, urllib.parse

from test_gwt_read import Base, FakeGate, FakeNet
import gwt_bvph  # noqa: E402
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'bvph')


def fx(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


P1, P2, NONE, SKELETON, TOO_MANY = (fx('hits-page1.html'), fx('hits-page2.html'), fx('none.html'), fx('skeleton.html'),
                                    fx('too-many-requests.html'))


class Classify(unittest.TestCase):
    def test_hits_none_and_a_skeleton_is_unknown(self):
        c = lambda b: gwt_reader.classify(b.decode('utf-8'), gwt_bvph.SPEC)
        self.assertEqual((c(P1), c(P2), c(NONE)), ('hits', 'hits', 'none'))
        self.assertEqual(c(SKELETON), 'unknown', 'the soft-block skeleton (it still says resultados_ocr) read as "none"')
        self.assertEqual(c(b''), 'unknown')

    def test_a_missing_result_container_is_never_none(self):
        # the result list gone and only an empty navigation frame left: neither a hit nor the site's own words
        frame = P1.decode().split("<ol class=\"nav_registros\"")[0] + '</div></div></body></html>'
        self.assertEqual(gwt_reader.classify(frame, gwt_bvph.SPEC), 'unknown')

    def test_rows_are_page_hits_with_title_date_page_and_words(self):
        a, b, c = gwt_bvph.candidates(P1.decode())
        self.assertEqual((a['id'], a['name'], a['date'], a['year'], a['page'], a['role'], a['principal']),
                         ('70000001', 'El Eco de Villazul: diario de la mañana Año III Número 512 - 1898 marzo 4',
                          '1898-03-04', 1898, 'Página 2', 'mention', False))
        self.assertEqual(a['url'], 'https://prensahistorica.mcu.es/es/catalogo_imagenes/grupo.do?path=5000001'
                                   '&idImagen=70000001')
        self.assertIn('doña Ana Zorvalo , viuda', a['text'])
        self.assertEqual(b['text'].count('Ana Zorvalo'), 2, 'the second fragment of a page was lost')
        self.assertEqual((c['id'], c['date'], c['page']), ('70000003', '1901-01-12', 'Página 1'))
        undated = P1.decode().replace('[s.n, 12/01/1901]', '[s.n.')
        self.assertEqual([(x['date'], x['year']) for x in gwt_bvph.candidates(undated)][2], ('1901-01-12', 1901),
                         'an issue with no date lost the date its title gives')

    def test_an_issue_dated_only_in_its_title(self):
        # the live markup often has no date in datos_publicacion, only in the title
        self.assertEqual(gwt_bvph.title_date('Boletín de Villazul: Número 91 _ 12/11/1840'), '1840-11-12')
        self.assertEqual(gwt_bvph.title_date('La Gaceta de Villazul: Número 1612 - 1889 abril 3'), '1889-04-03')
        self.assertEqual(gwt_bvph.title_date('La Gaceta: Número 14 - 1842 Setiembre 17'), '1842-09-17')
        self.assertEqual(gwt_bvph.title_date('La Gaceta de Villazul: Año II'), '')
        self.assertEqual(gwt_bvph.title_year('La Gaceta de Villazul: Número 1612 - Año 1889'), 1889,
                         'the issue number was read as the year')
        page = P1.decode().replace('[s.n, 12/01/1901]', '[S.l. : s.n.').replace(
            'La Voz de Nortia A&ntilde;o I N&uacute;mero 20 - 1901 enero 12', 'La Voz de Nortia N&uacute;mero 1612 _ 05/02/1889')
        self.assertEqual([(x['date'], x['year']) for x in gwt_bvph.candidates(page)][2], ('1889-02-05', 1889))
        yearly = page.replace('N&uacute;mero 1612 _ 05/02/1889', 'N&uacute;mero 1612 - A&ntilde;o 1889')
        self.assertEqual([(x['date'], x['year']) for x in gwt_bvph.candidates(yearly)][2], ('', 1889),
                         'an issue with only a year in its title lost it')

    def test_the_total_and_the_pager(self):
        self.assertEqual(gwt_bvph.total_of(P1.decode()), 1203, 'a total with a thousands dot')
        self.assertTrue(gwt_bvph.has_next(P1.decode(), 1))
        self.assertFalse(gwt_bvph.has_next(P2.decode(), 51))

    def test_only_a_phrase_in_double_quotes_is_searched(self):
        self.assertTrue(gwt_bvph.quoted('"Ana Zorvalo"'))
        for bad in ('Ana Zorvalo', '"Ana Zorvalo', 'Ana "Zorvalo"', '"Ana" "Zorvalo"', '""'):
            self.assertFalse(gwt_bvph.quoted(bad), bad)

    def test_the_url_keeps_the_quotes_and_the_position(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(gwt_bvph.search_url('"Ana Zorvalo"', 51)).query))
        self.assertEqual((q['busq_general'], q['posicion'], q['tipoResultados']), ('"Ana Zorvalo"', '51', 'PAG'))

    def test_the_pace_is_slow_and_the_spec_is_whole(self):
        self.assertGreaterEqual(gwt_bvph.SPEC['pace']['floor'], 6.0)
        self.assertEqual(gwt_reader.spec_problems(gwt_bvph.SPEC), [])
        for c in gwt_bvph.SPEC['controls']:
            phrase = c['argv'][c['argv'].index('--phrase') + 1]
            self.assertTrue(gwt_bvph.quoted(phrase), 'a canary must pass the quoting rule itself')


class Search(Base):
    def run_bvph(self, net, gate, *args):
        o, e = io.StringIO(), io.StringIO()
        argv = ['bvph', '--cache-dir', self.cache, '--lane', 't', *args]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        lines = dict(l.split('\t', 1) for l in o.getvalue().splitlines() if l.count('\t') == 1)
        return code, lines, o.getvalue() + e.getvalue()

    def search(self, net, gate, *extra):
        return self.run_bvph(net, gate, 'search', '--phrase', '"Ana Zorvalo"', *extra, '--out', self.out)

    def test_every_results_page_is_walked(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, P1), (200, P2))
        code, lines, text = self.search(net, gate)
        self.assertEqual(code, 0, text)
        self.assertEqual(len(net.requests), 2, 'stopped after the first page')
        self.assertIn('posicion=51', net.requests[1].full_url)
        cands = json.load(open(lines['candidates']))['candidates']
        self.assertEqual([c['id'] for c in cands], ['70000001', '70000002', '70000003', '70000004'])
        env = json.load(open(lines['evidence']))
        self.assertEqual((env['verdict'], env['total'], env['capped'], len(env['pages'])), ('hits', 1203, False, 2))
        self.assertIn('embarcó Ana Zorvalo', env['text'])
        self.assertNotIn('capped', lines)
        self.assertEqual(lines['results'], '1203 issues, 4 page hits read')

    def test_the_page_cap_says_capped_never_complete(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, P1))
        code, lines, text = self.search(net, gate, '--pages', '1')
        self.assertEqual(code, 0, text)
        self.assertEqual(len(net.requests), 1)
        self.assertIn('stopped after 1 pages with 3 page hits; partial', lines['capped'])
        self.assertTrue(json.load(open(lines['evidence']))['capped'])

    def test_the_site_own_no_results_words_are_exit_2(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, NONE)), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []), text)
        self.assertEqual(json.load(open(lines['candidates']))['candidates'], [])
        self.assertEqual(json.load(open(lines['evidence']))['verdict'], 'none')

    def test_a_skeleton_is_a_stop_never_zero_results(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, SKELETON), (200, SKELETON))
        code, lines, text = self.search(net, gate)
        self.assertEqual(code, gwt_reader.EXIT_WALL, text)
        self.assertEqual((self.out_files(), len(gate.hits)), ([], 1))
        self.search(net, gate)
        self.assertEqual(len(net.requests), 2, 'a skeleton was served from the cache')

    def test_a_429_is_a_wall(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (429, TOO_MANY)), gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)

    def test_no_results_on_a_later_page_is_a_stop(self):
        # the first page said there were more rows; "No hay resultados" on the next one is the site failing
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, P1), (200, NONE)), gate)
        self.assertEqual(code, gwt_reader.EXIT_WALL, text)
        self.assertNotIn('candidates', lines)
        self.assertEqual(len(gate.hits), 1)

    def test_hits_with_no_readable_row_are_a_stop_never_zero(self):
        # the result marker is there but the row markup changed: no row parses, which must never read as none
        for old, new in (('<span class="titulo">', '<span class="titulo-x">'), ('idImagen=', 'imagenId=')):
            gate = FakeGate()
            changed = P1.decode().replace(old, new).encode()
            code, lines, text = self.search(FakeNet(gate, (200, changed)), gate)
            self.assertEqual((code, self.out_files()), (gwt_reader.EXIT_ERROR, []), text)
            self.assertIn('no row could be read', text)

    def test_a_server_error_is_an_outage(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (503, b'<html>Service Unavailable</html>')), gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)

    def test_a_429_on_a_later_page_writes_nothing(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, P1), (429, TOO_MANY)), gate)
        self.assertEqual((code, self.out_files()), (gwt_reader.EXIT_WALL, []), text)

    def test_an_unquoted_phrase_is_refused_before_any_request(self):
        gate = FakeGate()
        net = FakeNet(gate)
        code, lines, text = self.run_bvph(net, gate, 'search', '--phrase', 'Ana Zorvalo', '--out', self.out)
        self.assertEqual((code, net.requests), (gwt_bvph.EXIT_USAGE, []))
        self.assertIn('not in double quotes', text)

    def test_the_control_reads_two_pages_and_is_void_when_short(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, P1), (200, P2))
        code, lines, text = self.run_bvph(net, gate, 'control')
        self.assertEqual(code, gwt_reader.EXIT_WALL, text)      # 4 page hits, far under the minimum
        self.assertIn('4 read, want at least 80\tSHORT', text)
        self.assertIn('%22Mateo+Sagasta%22', net.requests[0].full_url)


if __name__ == '__main__':
    unittest.main()
