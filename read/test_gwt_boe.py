"""Tests for `gwt-read boe`. Invented acts in fixtures/, a fake transport, a fake gate: no request ever leaves
this machine. Run: cd read && python3 -m unittest -v test_gwt_boe"""
import contextlib, io, json, os, unittest

from test_gwt_read import Base, FakeGate, FakeNet, CAPTCHA
import gwt_boe  # noqa: E402
import gwt_read  # noqa: E402
import gwt_rank  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')
RESULTS = open(os.path.join(FIX, 'boe-results.html'), 'rb').read()
EMPTY = open(os.path.join(FIX, 'boe-empty.html'), 'rb').read()
SEARCH_FORM = b'<html><body><form method="GET" action="gazeta.php"><input name="dato[4]"></form></body></html>'

NEXT = '<a href="gazeta.php?accion=Mas&amp;id_busqueda=ABC,,-50-50"><span class="pagSig">Pág. siguiente</span></a>'
PAGE1 = RESULTS.replace(b'Resultados 1 a 2 de 2', b'Resultados 1 a 2 de 1.003').replace(b'<div class="listadoResult">', NEXT.encode() + b'<div class="listadoResult">')
PAGE2 = RESULTS.replace(b'Resultados 1 a 2 de 2', b'Resultados 3 a 3 de 1.003').replace(b'BOE-A-1902-0001', b'BOE-A-1902-0003').replace(b'BOE-A-1949-0002', b'BOE-A-1949-0004')


class Boe(Base):
    def run_boe(self, args, net, gate):
        o, e = io.StringIO(), io.StringIO()
        argv = ['boe', '--cache-dir', self.cache, '--lane', 't', 'search'] + args + ['--out', self.out]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        lines = dict(l.split('\t', 1) for l in o.getvalue().splitlines() if '\t' in l)
        return code, lines, o.getvalue() + e.getvalue()

    def search(self, net, gate, extra=()):
        return self.run_boe(['--name', 'Ana Zorvalo Quemada'] + list(extra), net, gate)


class Parse(unittest.TestCase):
    def test_the_phrase_is_always_quoted_in_the_text_of_the_acts(self):
        u = gwt_boe.search_url('Ana "Zorvalo"')
        self.assertIn('dato%5B4%5D=%22Ana%20Zorvalo%22', u)
        self.assertIn('campo%5B4%5D=DOC', u)

    def test_rows_carry_title_reference_date_paper_department_and_pdf(self):
        r = gwt_boe.rows(RESULTS.decode())
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0]['ref'], 'BOE-A-1902-0001')
        self.assertEqual((r[0]['date'], r[0]['year']), ('1902-04-30', 1902))
        self.assertEqual(r[1]['paper'], 'Boletín Oficial del Estado')
        self.assertEqual(r[1]['dept'], 'Ministerio de Educación Nacional')
        # the same file as the link's /datos/pdfs/ (disallowed in robots.txt), at the allowed /gazeta/dias/ path
        self.assertEqual(r[0]['pdf'], 'https://www.boe.es/gazeta/dias/1902/04/30/pdfs/A00012-00012.pdf')
        self.assertEqual(r[1]['pdf'], 'https://www.boe.es/gazeta/dias/1949/07/26/pdfs/A00012-00012.pdf')
        self.assertEqual(gwt_boe.total(RESULTS.decode()), 2)

    def test_a_row_without_a_date_keeps_the_sites_own_pdf_link(self):
        page = RESULTS.decode().replace('de 30/04/1902,', 'de ,')
        self.assertEqual(gwt_boe.rows(page)[0]['pdf'], 'https://www.boe.es/datos/pdfs/BOE//1902/120/A00012-00012.pdf')

    def test_a_hit_is_a_mention_never_a_principal(self):
        c = gwt_boe.candidates(RESULTS.decode(), 'Ana Zorvalo Quemada')
        self.assertTrue(all(x['role'] == 'mention' and x['principal'] is False for x in c))
        self.assertEqual(c[0]['events'], {'mention': {'year': 1902, 'year_to': 1902, 'place': ''}})
        self.assertIn('pensión', c[0]['note'])

    def test_only_a_results_page_is_an_answer(self):
        self.assertTrue(gwt_boe.is_results(RESULTS.decode()))
        self.assertTrue(gwt_boe.is_results(EMPTY.decode()))
        self.assertFalse(gwt_boe.is_results(SEARCH_FORM.decode()))
        self.assertFalse(gwt_boe.is_results(CAPTCHA.decode('utf-8', 'replace')))


class Search(Boe):
    def test_search_writes_candidates(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, RESULTS))
        code, lines, text = self.search(net, gate)
        self.assertEqual(code, 0, text)
        self.assertEqual(net.requests[0].full_url, gwt_boe.search_url('Ana Zorvalo Quemada'))
        self.assertIsNone(net.requests[0].get_header('Authorization'))
        self.assertEqual(lines['source'], 'boe')
        self.assertEqual(lines['results'], '2 of 2')
        self.assertEqual(len(json.load(open(lines['candidates'], encoding='utf-8'))['candidates']), 2)
        with contextlib.redirect_stdout(io.StringIO()) as ranked, contextlib.redirect_stderr(io.StringIO()) as said:
            rc = gwt_rank.main([lines['candidates'], '--given', 'Ana', '--surname', 'Zorvalo', '--surname', 'Quemada',
                                '--facts', 'by=1880'])
        self.assertEqual(rc, 1, ranked.getvalue() + said.getvalue())   # gwt-rank reads the file: rows, none strong
        self.assertIn('2 rows, 0 strong, 0 possible, 2 weak', ranked.getvalue() + said.getvalue())  # a mention stays weak

    def test_a_repeat_costs_no_request(self):
        gate = FakeGate()
        self.search(FakeNet(gate, (200, RESULTS)), gate)
        gate2 = FakeGate()
        net2 = FakeNet(gate2)
        code, lines, text = self.search(net2, gate2)
        self.assertEqual(code, 0, text)
        self.assertEqual(net2.requests, [])

    def test_another_birth_year_parents_or_near_word_is_another_file(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, RESULTS))
        paths = set()
        for extra in ([], ['--born', '1903'], ['--parents', 'Juan y Antonia'], ['--near', 'Guerra']):
            code, lines, text = self.search(net, gate, extra)
            self.assertEqual(code, 0, text)
            paths.add(lines['candidates'])
        self.assertEqual(len(paths), 4)

    def test_a_long_name_with_other_parents_is_another_file(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, RESULTS))
        paths = set()
        for parents in ('Juan y Antonia', 'Juan y Ana'):    # the readable part of the name is cut at 60 characters
            code, lines, text = self.run_boe(['--name', 'Maria de la Concepcion Zorvalo de la Quemada', '--born', '1880',
                                              '--parents', parents], net, gate)
            self.assertEqual(code, 0, text)
            paths.add(lines['candidates'])
        self.assertEqual(len(paths), 2)

    def test_pages_never_go_past_5(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, PAGE1), (200, PAGE1))
        code, lines, text = self.search(net, gate, ['--pages', '9'])
        self.assertEqual(code, 0, text)
        self.assertEqual(lines['results'], '10 of 1003')    # 5 pages of 2 rows, the next pages from the cache

    def test_no_documents_is_exit_2(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, EMPTY)), gate)
        self.assertEqual(code, 2, text)

    def test_the_search_form_is_a_stop_not_zero_results(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, SEARCH_FORM)), gate)
        self.assertEqual(code, 3)
        self.assertFalse(os.path.exists(self.out) and os.listdir(self.out))

    def test_a_captcha_stops_the_run(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, CAPTCHA)), gate)
        self.assertEqual(code, 3)

    def test_a_name_needs_a_given_name_and_a_surname(self):
        gate = FakeGate()
        code, lines, text = self.run_boe(['--name', 'Zorvalo'], FakeNet(gate), gate)
        self.assertEqual(code, 64)

    def test_pages_follow_the_next_link_and_stop_at_the_count_given(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, PAGE1), (200, PAGE2))
        code, lines, text = self.search(net, gate, ['--pages', '2'])
        self.assertEqual(code, 0, text)
        self.assertEqual(len(net.requests), 2)
        self.assertEqual(net.requests[1].full_url, 'https://www.boe.es/buscar/gazeta.php?accion=Mas&id_busqueda=ABC,,-50-50')
        self.assertEqual(lines['results'], '4 of 1003')

    def test_one_page_is_the_default(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, PAGE1))
        code, lines, text = self.search(net, gate)
        self.assertEqual(len(net.requests), 1)
        self.assertEqual(lines['results'], '2 of 1003')

    def test_years_go_to_the_sites_own_date_field(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, RESULTS))
        self.search(net, gate, ['--from', '1930', '--to', '1959'])
        u = net.requests[0].full_url
        self.assertIn('dato%5B8%5D%5B0%5D=1930-01-01', u)
        self.assertIn('dato%5B8%5D%5B1%5D=1959-12-31', u)

    def test_near_marks_a_row_and_never_hides_one(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, RESULTS))
        code, lines, text = self.search(net, gate, ['--near', 'Guerra'])
        c = json.load(open(lines['candidates'], encoding='utf-8'))['candidates']
        self.assertEqual(len(c), 2)
        self.assertEqual([x['near'] for x in c], [['Guerra'], []])
        self.assertIn('[near: Guerra]', c[0]['note'])

    def test_pdf_context_reads_the_first_acts_and_adds_the_words_around_the_name(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, RESULTS), (200, b'%PDF fake one'))
        real = (gwt_boe.pdf_text, gwt_boe.shutil.which)
        gwt_boe.pdf_text = lambda data: 'En la prisión de Villa: Luis Gil, Ana Zorvalo\nQuemada. Eugenio Roa.'
        gwt_boe.shutil.which = lambda n: '/usr/bin/pdftotext'
        try:
            code, lines, text = self.search(net, gate, ['--pdf-context', '1'])
        finally:
            gwt_boe.pdf_text, gwt_boe.shutil.which = real
        self.assertEqual(code, 0, text)
        self.assertEqual(len(net.requests), 2)
        self.assertTrue(net.requests[1].full_url.endswith('.pdf'))
        c = json.load(open(lines['candidates'], encoding='utf-8'))['candidates']
        self.assertIn('Ana Zorvalo Quemada. Eugenio Roa', c[0]['context'])
        self.assertIsNone(c[1].get('context'))

    def test_a_name_split_in_the_text_layer_points_to_the_pdf(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, RESULTS), (200, b'%PDF fake one'))
        real = (gwt_boe.pdf_text, gwt_boe.shutil.which)
        gwt_boe.pdf_text = lambda data: 'Luis Gil, Ana Zorvalo y otros. Quemada.'
        gwt_boe.shutil.which = lambda n: '/usr/bin/pdftotext'
        try:
            code, lines, text = self.search(net, gate, ['--pdf-context', '1'])
        finally:
            gwt_boe.pdf_text, gwt_boe.shutil.which = real
        c = json.load(open(lines['candidates'], encoding='utf-8'))['candidates']
        self.assertIsNone(c[0]['context'])
        self.assertIn('name not whole in the text layer: open the PDF', c[0]['note'])

    def _with_pdftotext(self, net, gate, extra, txt='Ana Zorvalo Quemada.'):
        real = (gwt_boe.pdf_text, gwt_boe.shutil.which)
        gwt_boe.pdf_text = lambda data: txt
        gwt_boe.shutil.which = lambda n: '/usr/bin/pdftotext'
        try:
            return self.search(net, gate, extra)
        finally:
            gwt_boe.pdf_text, gwt_boe.shutil.which = real

    def test_an_html_page_at_a_pdf_url_is_a_stop_and_never_cached(self):
        gate = FakeGate()
        html_ = b'<html><body>Documento no disponible temporalmente</body></html>'
        code, lines, text = self._with_pdftotext(FakeNet(gate, (200, RESULTS), (200, html_)), gate, ['--pdf-context', '1'])
        self.assertEqual(code, 3, text)
        gate2 = FakeGate()
        net2 = FakeNet(gate2, (200, b'%PDF-1.4 real'))
        code, lines, text = self._with_pdftotext(net2, gate2, ['--pdf-context', '1'])
        self.assertEqual(code, 0, text)
        self.assertEqual(len(net2.requests), 1)   # the search came from the cache, the PDF did not
        self.assertTrue(net2.requests[0].full_url.endswith('.pdf'))

    def test_pdf_context_skips_acts_born_already_ruled_out(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, RESULTS), (200, b'%PDF-1.4 x'))
        code, lines, text = self._with_pdftotext(net, gate, ['--pdf-context', '1', '--born', '1903'])
        self.assertEqual(code, 0, text)
        self.assertEqual(len(net.requests), 2)
        self.assertIn('/1949/', net.requests[1].full_url)

    def test_pdf_context_without_pdftotext_fetches_nothing(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, RESULTS))
        real = gwt_boe.shutil.which
        gwt_boe.shutil.which = lambda n: None
        try:
            code, lines, text = self.search(net, gate, ['--pdf-context', '2'])
        finally:
            gwt_boe.shutil.which = real
        self.assertEqual(len(net.requests), 1)
        self.assertIn('needs pdftotext', text)

    def test_pdf_context_never_fetches_more_than_10_pdfs(self):
        got = []

        class R:
            def get(self, url, h, refresh=False, page=None):
                got.append(url)
                return 200, {}, b'%PDF-1.4 x', False
        cands = [{'url': 'https://www.boe.es/gazeta/dias/1902/04/30/pdfs/A%05d.pdf' % i, 'note': ''} for i in range(12)]
        a = gwt_read.argparse.Namespace(pdf_context=50, refresh=False, parents=None)
        real = (gwt_boe.pdf_text, gwt_boe.shutil.which)
        gwt_boe.pdf_text = lambda data: 'Ana Zorvalo Quemada.'
        gwt_boe.shutil.which = lambda n: '/usr/bin/pdftotext'
        try:
            gwt_read._boe_pdf_context(a, R(), cands, 'Ana Zorvalo Quemada')
        finally:
            gwt_boe.pdf_text, gwt_boe.shutil.which = real
        self.assertEqual(len(got), 10)

    def test_pdf_text_runs_nothing_without_pdftotext(self):
        real = (gwt_boe.shutil.which, gwt_boe.subprocess.run)
        ran = []
        gwt_boe.shutil.which = lambda n: None
        gwt_boe.subprocess.run = lambda *x, **k: ran.append(x)
        try:
            self.assertIsNone(gwt_boe.pdf_text(b'%PDF-1.4 x'))
        finally:
            gwt_boe.shutil.which, gwt_boe.subprocess.run = real
        self.assertEqual(ran, [])

    def test_born_marks_an_act_before_the_birth_and_hides_nothing(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, RESULTS))
        code, lines, text = self.search(net, gate, ['--born', '1903'])
        c = json.load(open(lines['candidates'], encoding='utf-8'))['candidates']
        self.assertEqual(len(c), 2)
        self.assertEqual([x['implausible'] for x in c], [True, False])
        self.assertIn('IMPLAUSIBLE', c[0]['note'])
        self.assertNotIn('IMPLAUSIBLE', c[1]['note'])

    def test_parents_in_the_act_are_compared_with_the_trees(self):
        for wanted, expect, word in (('Juan y Antonia', True, 'as in the tree'), ('José y Josefa', False, 'NOT the tree')):
            gate = FakeGate()
            net = FakeNet(gate, (200, RESULTS), (200, b'%PDF x'))
            real = (gwt_boe.pdf_text, gwt_boe.shutil.which)
            gwt_boe.pdf_text = lambda data: 'Luis Gil, de Pedro y Ana. Ana Zorvalo Quemada, de Juan y Antonia. Jenaro Ruiz'
            gwt_boe.shutil.which = lambda n: '/usr/bin/pdftotext'
            try:
                code, lines, text = self.search(net, gate, ['--pdf-context', '1', '--parents', wanted])
            finally:
                gwt_boe.pdf_text, gwt_boe.shutil.which = real
            c = json.load(open(lines['candidates'], encoding='utf-8'))['candidates']
            self.assertEqual(c[0]['parents_in_act'], [['Juan', 'Antonia']])
            self.assertIs(c[0]['same_parents'], expect)
            self.assertIn(word, c[0]['note'])


class Old(unittest.TestCase):
    def test_a_child_is_not_implausible_orphans_are_named_in_pension_lists(self):
        page = RESULTS.decode()
        self.assertFalse(gwt_boe.candidates(page, 'x y', born=1895)[0]['implausible'])   # 1902, aged 7
        self.assertTrue(gwt_boe.candidates(page, 'x y', born=1903)[0]['implausible'])    # 1902, not yet born
        self.assertFalse(gwt_boe.candidates(page, 'x y', born=1902)[0]['implausible'])   # the year of the birth

    def test_over_100_is_implausible(self):
        page = open(os.path.join(os.path.dirname(__file__), 'fixtures', 'boe-results.html'), encoding='utf-8').read()
        rs = gwt_boe.rows(page)
        i = next(n for n, r in enumerate(rs) if r['year'])
        y = rs[i]['year']
        self.assertTrue(gwt_boe.candidates(page, 'x y', born=y - 101)[i]['implausible'])
        self.assertFalse(gwt_boe.candidates(page, 'x y', born=y - 50)[i]['implausible'])


class Pace(unittest.TestCase):
    def test_the_gate_never_goes_faster_than_one_request_every_6_s(self):
        # gwt_gate shortens the gap toward `floor` after every quiet hour: floor is the real pace of a long run
        self.assertGreaterEqual(gwt_boe.PACE['floor'], 6.0)
        self.assertGreaterEqual(gwt_boe.PACE['start'], gwt_boe.PACE['floor'])


class WholeName(unittest.TestCase):
    def test_a_longer_given_name_is_not_the_name(self):
        self.assertIsNone(gwt_boe.context('Juana Zorvalo cobra la pensión', 'Ana Zorvalo'))
        self.assertEqual(gwt_boe.context('a Ana Zorvalo, cobra', 'Ana Zorvalo', 5), 'a Ana Zorvalo, cob')
        page = RESULTS.decode().replace('doña Ana Zorvalo', 'doña Juana Zorvalo')
        self.assertFalse(gwt_boe.candidates(page, 'Ana Zorvalo Quemada')[0]['in_title'])

    def test_parents_are_compared_by_first_given_name(self):
        self.assertTrue(gwt_boe.same_parents(('José', 'Josefa'), 'José María y Josefa'))
        self.assertFalse(gwt_boe.same_parents(('Pedro', 'Josefa'), 'José María y Josefa'))


class ActVerdict(Boe):
    def _act(self, txt, parents):
        gate = FakeGate()
        net = FakeNet(gate, (200, RESULTS), (200, b'%PDF-1.4 x'))
        real = (gwt_boe.pdf_text, gwt_boe.shutil.which)
        gwt_boe.pdf_text = lambda data: txt
        gwt_boe.shutil.which = lambda n: '/usr/bin/pdftotext'
        try:
            code, lines, text = self.search(net, gate, ['--pdf-context', '1', '--parents', parents])
        finally:
            gwt_boe.pdf_text, gwt_boe.shutil.which = real
        self.assertEqual(code, 0, text)
        return json.load(open(lines['candidates'], encoding='utf-8'))['candidates'][0]

    def test_a_rank_before_the_name_does_not_exclude_the_act(self):
        c = self._act('Se declara retirado al Cabo Ana Zorvalo Quemada, de Juan y Antonia.', 'Juan y Antonia')
        self.assertEqual(c['verdict'], 'consistent')

    def test_the_last_surname_of_the_line_above_does_not_exclude_the_act(self):
        c = self._act('Luis Gil Pérez\nAna Zorvalo Quemada, de Juan y Antonia.\n', 'Juan y Antonia')
        self.assertEqual(c['verdict'], 'consistent')

    def test_every_occurrence_is_read_and_one_match_is_enough(self):
        c = self._act('Rosa Ana Zorvalo Quemada, de Pedro y Rosa. Más abajo: Ana Zorvalo Quemada, de Juan y Antonia.',
                      'Juan y Antonia')
        self.assertEqual(c['verdict'], 'consistent')

    def test_excluded_only_when_every_occurrence_names_other_parents(self):
        c = self._act('Ana Zorvalo Quemada, de Pedro y Rosa. Ana Zorvalo Quemada, de Pedro y Rosa.', 'Juan y Antonia')
        self.assertEqual(c['verdict'], 'excluded')
        c = self._act('Ana Zorvalo Quemada, de Pedro y Rosa. Ana Zorvalo Quemada, vecina de Roda.', 'Juan y Antonia')
        self.assertEqual(c['verdict'], 'open')


class Verdict(unittest.TestCase):
    def test_excluded_consistent_open(self):
        self.assertEqual(gwt_boe.verdict({'implausible': True}), 'excluded')
        self.assertEqual(gwt_boe.verdict({'same_parents': False}), 'excluded')
        self.assertEqual(gwt_boe.verdict({'preceded_by': 'Cabo', 'same_parents': True}), 'consistent')
        self.assertEqual(gwt_boe.verdict({'same_parents': True}), 'consistent')
        self.assertEqual(gwt_boe.verdict({'same_parents': True, 'implausible': True}), 'excluded')
        self.assertEqual(gwt_boe.verdict({'same_parents': None}), 'open')


class Units(unittest.TestCase):
    def test_a_total_with_a_thousands_dot(self):
        self.assertEqual(gwt_boe.total(PAGE1.decode()), 1003)

    def test_a_name_in_the_title_is_marked_and_the_accent_is_ignored(self):
        c = gwt_boe.candidates(RESULTS.decode(), 'ana zorvalo quemada')
        self.assertTrue(all(x['in_title'] for x in c))
        self.assertFalse(gwt_boe.candidates(RESULTS.decode(), 'Ana Zorvalo Otra')[0]['in_title'])

    def test_context_is_the_words_around_the_whole_name_and_none_when_it_is_split(self):
        txt = 'Se concede a don Luis Gil y a Ana Zor-\nvalo Quemada la gracia de que se trata.'
        self.assertEqual(gwt_boe.context(txt, 'ana zorvalo quemada', 10), 's Gil y a Ana Zorvalo Quemada la gracia')
        self.assertIsNone(gwt_boe.context('Ana Zorvalo y otros. Quemada.', 'Ana Zorvalo Quemada'))

    def test_parents_after_reads_de_x_y_y_and_the_comparison_ignores_accents(self):
        self.assertEqual(gwt_boe.parents_after('Ana Zorvalo, de José y Josefa. Luis', 'Ana Zorvalo'), [('José', 'Josefa')])
        self.assertEqual(gwt_boe.parents_after('Ana Zorvalo Quemada.... 1905', 'Ana Zorvalo'), [None])
        self.assertEqual(gwt_boe.parents_after('nadie', 'Ana Zorvalo'), [])
        self.assertTrue(gwt_boe.same_parents(('José', 'Josefa'), 'jose y JOSEFA'))
        self.assertFalse(gwt_boe.same_parents(('Juan', 'Antonia'), 'José y Josefa'))
        self.assertIsNone(gwt_boe.same_parents(None, 'José y Josefa'))


if __name__ == '__main__':
    unittest.main()
