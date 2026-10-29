import os
"""Tests for `gwt-read boe`. Invented acts in fixtures/, a fake transport, a fake gate: no request ever leaves
this machine. Run: cd read && python3 -m unittest -v test_gwt_boe"""
import contextlib, io, json, os, unittest

from test_gwt_read import Base, FakeGate, FakeNet, CAPTCHA
import gwt_boe  # noqa: E402
import gwt_read  # noqa: E402

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
        self.assertTrue(r[1]['pdf'].startswith('https://www.boe.es/datos/pdfs/'))
        self.assertEqual(gwt_boe.total(RESULTS.decode()), 2)

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

    def test_a_repeat_costs_no_request(self):
        gate = FakeGate()
        self.search(FakeNet(gate, (200, RESULTS)), gate)
        gate2 = FakeGate()
        net2 = FakeNet(gate2)
        code, lines, text = self.search(net2, gate2)
        self.assertEqual(code, 0, text)
        self.assertEqual(net2.requests, [])

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

    def test_born_marks_an_act_before_the_persons_fourteenth_year_and_hides_nothing(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, RESULTS))
        code, lines, text = self.search(net, gate, ['--born', '1895'])
        c = json.load(open(lines['candidates'], encoding='utf-8'))['candidates']
        self.assertEqual(len(c), 2)
        self.assertEqual([x['implausible'] for x in c], [True, False])
        self.assertIn('IMPLAUSIBLE', c[0]['note'])
        self.assertNotIn('IMPLAUSIBLE', c[1]['note'])

    def test_pdf_context_says_when_the_name_sits_inside_a_longer_one(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, RESULTS), (200, b'%PDF x'))
        real = (gwt_boe.pdf_text, gwt_boe.shutil.which)
        gwt_boe.pdf_text = lambda data: '1905 Juan Ana Zorvalo Quemada.... 1905 Jenaro'
        gwt_boe.shutil.which = lambda n: '/usr/bin/pdftotext'
        try:
            code, lines, text = self.search(net, gate, ['--pdf-context', '1'])
        finally:
            gwt_boe.pdf_text, gwt_boe.shutil.which = real
        c = json.load(open(lines['candidates'], encoding='utf-8'))['candidates']
        self.assertEqual(c[0]['preceded_by'], 'Juan')
        self.assertIn('a longer name', c[0]['note'])

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
            self.assertEqual(c[0]['parents_in_act'], ['Juan', 'Antonia'])
            self.assertIs(c[0]['same_parents'], expect)
            self.assertIn(word, c[0]['note'])


class Ocr(unittest.TestCase):
    def test_fuzzy_finds_a_name_the_ocr_misread_and_a_hyphen(self):
        t = '306, contra Ana Zorvalo Quemda, vecino\n 307, contra Luis Marti-\nnez'
        w, c = gwt_boe.fuzzy_find(t, 'Ana Zorvalo Quemada')
        self.assertEqual(w, 'Ana Zorvalo Quemda,')
        self.assertIn('vecino', c)
        self.assertEqual(gwt_boe.fuzzy_find(t, 'Luis Martinez')[0], 'Luis Martinez')

    def test_fuzzy_does_not_find_a_different_name(self):
        self.assertIsNone(gwt_boe.fuzzy_find('contra Pedro Gil Ortega, vecino de Roda', 'Ana Zorvalo Quemada'))

    def test_ocr_needs_both_programs_and_runs_nothing_without_them(self):
        calls = []
        orig = gwt_boe.shutil.which
        gwt_boe.shutil.which = lambda n: None
        try:
            self.assertIsNone(gwt_boe.ocr_text(b'%PDF', run=lambda *a, **k: calls.append(a)))
        finally:
            gwt_boe.shutil.which = orig
        self.assertEqual(calls, [])


class OcrLanguage(unittest.TestCase):
    def _run(self, langs):
        seen = []

        def run(cmd, **k):
            seen.append(cmd)

            class R:
                returncode = 0
                stdout = (langs if '--list-langs' in cmd else 'text').encode()
            if cmd[0].endswith('pdftoppm'):
                open(cmd[-1] + '-1.png', 'wb').close()
            return R()
        orig = gwt_boe.shutil.which
        gwt_boe.shutil.which = lambda n: '/usr/bin/' + n
        try:
            gwt_boe.ocr_text(b'%PDF', run=run)
        finally:
            gwt_boe.shutil.which = orig
        return [c for c in seen if '--psm' in c][0]

    def test_spanish_when_installed_else_english(self):
        c = self._run('List of available languages (2):\neng\nspa\n')
        self.assertEqual(c[c.index('-l') + 1], 'spa')
        c = self._run('List of available languages (1):\neng\n')
        self.assertEqual(c[c.index('-l') + 1], 'eng')


class Old(unittest.TestCase):
    def test_over_100_is_implausible(self):
        page = open(os.path.join(os.path.dirname(__file__), 'fixtures', 'boe-results.html'), encoding='utf-8').read()
        rs = gwt_boe.rows(page)
        i = next(n for n, r in enumerate(rs) if r['year'])
        y = rs[i]['year']
        self.assertTrue(gwt_boe.candidates(page, 'x y', born=y - 101)[i]['implausible'])
        self.assertFalse(gwt_boe.candidates(page, 'x y', born=y - 50)[i]['implausible'])


class Verdict(unittest.TestCase):
    def test_excluded_consistent_open(self):
        self.assertEqual(gwt_boe.verdict({'implausible': True}), 'excluded')
        self.assertEqual(gwt_boe.verdict({'same_parents': False}), 'excluded')
        self.assertEqual(gwt_boe.verdict({'preceded_by': 'Juan'}), 'excluded')
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

    def test_preceded_by_ignores_a_title_and_a_start_of_text(self):
        self.assertEqual(gwt_boe.preceded_by('de Juan Ana Zorvalo Quemada y', 'Ana Zorvalo Quemada'), 'Juan')
        self.assertIsNone(gwt_boe.preceded_by('a don Ana Zorvalo Quemada y', 'Ana Zorvalo Quemada'))
        self.assertIsNone(gwt_boe.preceded_by('Ana Zorvalo Quemada', 'Ana Zorvalo Quemada'))

    def test_parents_after_reads_de_x_y_y_and_the_comparison_ignores_accents(self):
        self.assertEqual(gwt_boe.parents_after('Ana Zorvalo, de José y Josefa. Luis', 'Ana Zorvalo'), ('José', 'Josefa'))
        self.assertIsNone(gwt_boe.parents_after('Ana Zorvalo Quemada.... 1905', 'Ana Zorvalo'))
        self.assertTrue(gwt_boe.same_parents(('José', 'Josefa'), 'jose y JOSEFA'))
        self.assertFalse(gwt_boe.same_parents(('Juan', 'Antonia'), 'José y Josefa'))
        self.assertIsNone(gwt_boe.same_parents(None, 'José y Josefa'))


if __name__ == '__main__':
    unittest.main()
