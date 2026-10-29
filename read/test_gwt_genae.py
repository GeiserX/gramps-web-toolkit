"""Tests for `gwt-read genae`. Invented rows in fixtures/, a fake transport, a fake gate: no request ever leaves
this machine. Run: cd read && python3 -m unittest -v test_gwt_genae"""
import contextlib, io, json, os, unittest

from test_gwt_read import Base, FakeGate, FakeNet, CAPTCHA
import gwt_genae  # noqa: E402
import gwt_read  # noqa: E402
import gwt_rank  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')
PAGE = {b: open(os.path.join(FIX, 'genae-%s.html' % b), 'rb').read() for b in gwt_genae.BLOCKS}
ANSWERS = [(200, PAGE[b]) for b in gwt_genae.BLOCKS]
EMPTY_FRAME = b'<html><body><div id="resultados"></div></body></html>'
NO_BOOKS = (b'<aside id="resultados-cobertura"><strong>Sin registros indexados disponibles</strong></aside>'
            b'<section id="bloque-casadas" class="bloque-categoria" data-aviso-cobertura="No hay registros indexados '
            b'de Casadas para los a\xc3\xb1os pedidos en esta localidad."></section>')
SEARCH_FORM = b'<html><body><form id="form-busqueda" action="buscar"><section id="bloque-otro"><table></table></section></form></body></html>'


class Genae(Base):
    def run_genae(self, args, net, gate):
        o, e = io.StringIO(), io.StringIO()
        argv = ['genae', '--cache-dir', self.cache, '--lane', 't', 'search'] + args + ['--out', self.out]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        lines = dict(l.split('\t', 1) for l in o.getvalue().splitlines() if '\t' in l)
        return code, lines, o.getvalue() + e.getvalue()

    def search(self, net, gate, extra=()):
        return self.run_genae(['--given', 'Ana', '--surname', 'Zorvalo'] + list(extra), net, gate)


class Parse(unittest.TestCase):
    def test_params_use_the_sites_own_names_and_skip_empty_ones(self):
        self.assertEqual(gwt_genae.params('Ana', 'Zorvalo', '', 'mu', '', 1850, 1900),
                         'n=Ana&a1=Zorvalo&r=mu&d=1850&h=1900')
        self.assertIn('a2=Quirell&r=mu&l=Villazul', gwt_genae.params('', '', 'Quirell', 'mu', 'Villazul'))

    def test_block_urls(self):
        self.assertEqual(gwt_genae.block_url('bautismos', 'n=Ana'), 'https://genae.org/buscar/resultados?n=Ana')
        self.assertEqual(gwt_genae.block_url('defunciones', 'n=Ana'),
                         'https://genae.org/buscar/bloque?categoria=defunciones&n=Ana')

    def test_highlight_marks_inside_a_name_add_no_spaces(self):
        c = gwt_genae.candidates(PAGE['casados'].decode(), 'casados')
        self.assertEqual(c[0]['name'], 'Pedro Zorvalo Brin')

    def test_baptism_row_is_a_principal_with_parents_by_first_name(self):
        c = gwt_genae.candidates(PAGE['bautismos'].decode(), 'bautismos')
        self.assertEqual(c[0]['id'], 'bautismos-9000101')
        self.assertEqual((c[0]['given'], c[0]['surnames']), ('Ana', ['Zorvalo', 'Quirell']))
        self.assertEqual(c[0]['events'], {'birth': {'year': 1851, 'year_to': 1851, 'place': 'Villazul'}})
        self.assertEqual(c[0]['relatives'], [{'role': 'father', 'name': 'Luis'}, {'role': 'mother', 'name': 'Rosa'}])
        self.assertTrue(c[0]['principal'])

    def test_phonetic_suggestion_is_kept_and_marked(self):
        c = gwt_genae.candidates(PAGE['bautismos'].decode(), 'bautismos')
        self.assertEqual([x['phonetic'] for x in c], [False, True])

    def test_a_marriage_gives_one_candidate_per_spouse_and_never_splits_the_name(self):
        c = gwt_genae.candidates(PAGE['casados'].decode(), 'casados')
        self.assertEqual([x['id'] for x in c], ['matrimonios-9000201-esposo', 'matrimonios-9000201-esposa'])
        self.assertIsNone(c[1]['given'])
        self.assertIsNone(c[1]['surnames'])
        self.assertEqual(c[0]['relatives'][0], {'role': 'spouse', 'name': 'Maria Antonia Lucero Vega'})
        self.assertEqual(c[0]['events']['marriage']['year'], 1875)

    def test_burial_row_is_a_death_event(self):
        c = gwt_genae.candidates(PAGE['defunciones'].decode(), 'defunciones')
        self.assertEqual(c[0]['events'], {'death': {'year': 1899, 'year_to': 1899, 'place': 'Villazul'}})

    def test_a_candidate_points_at_the_search_that_found_it(self):
        c = gwt_genae.candidates(PAGE['defunciones'].decode(), 'defunciones', 'https://genae.org/buscar/bloque?x=1')
        self.assertEqual(c[0]['url'], 'https://genae.org/buscar/bloque?x=1')

    def test_a_block_with_no_indexed_books_is_an_answer_with_no_rows(self):
        self.assertTrue(gwt_genae.is_block(NO_BOOKS.decode(), 'casadas'))
        self.assertEqual(gwt_genae.rows(NO_BOOKS.decode()), [])
        self.assertFalse(gwt_genae.is_block(NO_BOOKS.decode(), 'casados'))

    def test_a_full_page_is_flagged_as_cut(self):
        self.assertFalse(gwt_genae.is_cut(14))
        self.assertTrue(gwt_genae.is_cut(gwt_genae.PAGE_SIZE))

    def test_only_the_block_asked_for_is_an_answer(self):
        self.assertTrue(gwt_genae.is_block(PAGE['casados'].decode(), 'casados'))
        self.assertFalse(gwt_genae.is_block(PAGE['casados'].decode(), 'defunciones'))   # another block
        self.assertFalse(gwt_genae.is_block(EMPTY_FRAME.decode(), 'bautismos'))         # an empty frame
        self.assertFalse(gwt_genae.is_block(SEARCH_FORM.decode(), 'bautismos'))         # the search form


class Search(Genae):
    def test_search_reads_the_four_blocks_and_writes_candidates_gwt_rank_ranks(self):
        gate = FakeGate()
        net = FakeNet(gate, *ANSWERS)
        code, lines, text = self.search(net, gate)
        self.assertEqual(code, 0, text)
        self.assertEqual([r.full_url for r in net.requests],
                         [gwt_genae.block_url(b, 'n=Ana&a1=Zorvalo&r=mu&d=1503&h=1986') for b in gwt_genae.BLOCKS])
        self.assertEqual(net.requests[0].get_header('Hx-request'), 'true')
        self.assertIsNone(net.requests[0].get_header('Authorization'))
        self.assertEqual(lines['source'], 'genae')
        self.assertEqual(lines['results'], 'bautismos 2, casados 1, casadas 0, defunciones 1')
        cands = json.load(open(lines['candidates']))['candidates']
        self.assertEqual(len(cands), 5)
        with contextlib.redirect_stdout(io.StringIO()) as ranked, contextlib.redirect_stderr(io.StringIO()):
            rc = gwt_rank.main([lines['candidates'], '--given', 'Ana', '--surname', 'Zorvalo', '--surname', 'Quirell',
                                '--facts', 'by=1851 bp=Villazul'])
        self.assertEqual(rc, 0, ranked.getvalue())   # gwt-rank reads the file and finds the exact baptism strong
        self.assertIn('bautismos-9000101', ranked.getvalue())

    def test_a_repeat_costs_no_request(self):
        gate = FakeGate()
        net = FakeNet(gate, *ANSWERS)
        self.search(net, gate)
        gate2 = FakeGate()
        net2 = FakeNet(gate2)
        code, lines, text = self.search(net2, gate2)
        self.assertEqual(code, 0, text)
        self.assertEqual(net2.requests, [])

    def test_no_rows_is_exit_2(self):
        gate = FakeGate()
        empty = open(os.path.join(FIX, 'genae-casadas.html'), 'rb').read()
        net = FakeNet(gate, *[(200, empty.replace(b'casadas', b)) for b in gwt_genae.BLOCKS.__iter__()
                              for b in [b.encode()]])
        code, lines, text = self.search(net, gate)
        self.assertEqual(code, 2, text)

    def test_a_block_with_no_indexed_books_does_not_stop_the_search(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, PAGE['bautismos']), (200, PAGE['casados']), (200, NO_BOOKS), (200, PAGE['defunciones']))
        code, lines, text = self.search(net, gate)
        self.assertEqual(code, 0, text)
        self.assertEqual(lines['results'], 'bautismos 2, casados 1, casadas 0, defunciones 1')

    def test_a_marriage_found_in_both_blocks_is_one_pair_of_candidates(self):
        gate = FakeGate()
        both = PAGE['casados'].replace(b'bloque-casados', b'bloque-casadas').replace(b'filas-casados', b'filas-casadas')
        net = FakeNet(gate, (200, PAGE['bautismos']), (200, PAGE['casados']), (200, both), (200, PAGE['defunciones']))
        code, lines, text = self.search(net, gate)
        ids = [c['id'] for c in json.load(open(lines['candidates']))['candidates']]
        self.assertEqual(len(ids), len(set(ids)), ids)

    def test_a_captcha_stops_the_run_and_saves_nothing(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, CAPTCHA))
        code, lines, text = self.search(net, gate)
        self.assertEqual(code, 3)
        self.assertEqual(len(net.requests), 1)
        self.assertFalse(os.path.exists(self.out) and os.listdir(self.out))

    def test_an_empty_frame_is_a_stop_not_zero_results(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, EMPTY_FRAME))
        code, lines, text = self.search(net, gate)
        self.assertEqual(code, 3)

    def test_a_search_needs_a_name(self):
        gate = FakeGate()
        code, lines, text = self.run_genae([], FakeNet(gate), gate)
        self.assertEqual(code, 64)


if __name__ == '__main__':
    unittest.main()
