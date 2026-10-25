"""Tests for `gwt-read pares`. Invented descriptions in fixtures/, a fake transport, a fake gate: no request ever
leaves this machine. Run: cd read && python3 -m unittest -v test_gwt_pares"""
import contextlib, io, json, os, unittest

from test_gwt_read import Base, FakeGate, FakeNet, CAPTCHA
import gwt_pares  # noqa: E402
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402
import gwt_rank  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')
SEARCH = open(os.path.join(FIX, 'pares-search.html'), 'rb').read()
RECORD = open(os.path.join(FIX, 'pares-record.html'), 'rb').read()
FORM = b'<html><body><form id="formBusqueda" name="formBusqueda" action="find" method="get"></form></body></html>'
ERROR_PAGE = (b'<html><body><div id="error_mensaje"><div class="error">Ha ocurrido un error en la aplicaci\xc3\xb3n. '
              b'Por favor, vuelva a intentarlo de nuevo.</div></div></body></html>')
EMPTY_FRAME = b'<html><body><iframe src="/x" style="width:100%"></iframe></body></html>'


class Pares(Base):
    def run_pares(self, args, net, gate):
        o, e = io.StringIO(), io.StringIO()
        argv = ['pares', '--cache-dir', self.cache, '--lane', 't'] + args + ['--out', self.out]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        lines = dict(l.split('\t', 1) for l in o.getvalue().splitlines() if '\t' in l)
        return code, lines, o.getvalue() + e.getvalue()

    def search(self, net, gate):
        return self.run_pares(['search', '--given', 'Ana', '--surname', 'Zorvalo'], net, gate)


class Search(Pares):
    def test_search_writes_envelope_and_candidates_gwt_rank_ranks(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, FORM), (200, SEARCH))
        code, lines, text = self.search(net, gate)
        self.assertEqual(code, 0, text)
        self.assertEqual([r.full_url for r in net.requests],
                         [gwt_pares.FORM_URL, gwt_pares.BASE + '/find?nm=&texto=Ana%20Zorvalo'])
        self.assertEqual(net.requests[1].get_header('Accept-language'), gwt_pares.LANGUAGE)
        self.assertIsNone(net.requests[1].get_header('Authorization'))
        self.assertEqual(lines['source'], 'pares')
        env = json.load(open(lines['evidence']))
        self.assertEqual(env['url'], net.requests[1].full_url)
        self.assertIn('Pleito de Ana Zorvalo Quirell', env['text'])
        self.assertIn('id="texto_resultados"', env['html'])
        cands = json.load(open(lines['candidates']))['candidates']
        self.assertEqual([c['id'] for c in cands], ['9000001', '9000002', '9000003'])
        self.assertEqual(cands[1]['name'], 'Expediente de limpieza de sangre de Pedro Zorvalo Brin, natural de '
                         'Montefrío, para el ingreso en el colegio de Sur, con las pruebas de sus abuelos')
        self.assertEqual((cands[0]['archive'], cands[0]['dates'], cands[0]['reference']),
                         ('Archivo Histórico de Nortia', '1871 / 1873', 'PLEITOS,101,Exp.7'))
        self.assertEqual(cands[2]['reference'], '')
        self.assertEqual((cands[0]['principal'], cands[0]['events']), (False, {}))
        self.assertEqual(lines['results'], '1203 total, 3 read')
        # gwt-rank reads the file as written: the description naming Ana Zorvalo comes first, and no
        # description is ever strong (the file's dates are not the person's)
        ranked = gwt_rank.rank(gwt_rank.load(lines['candidates']), 'Ana', ['Zorvalo', 'Quirell'],
                               gwt_rank.parse_facts('by=1851'))
        self.assertEqual(ranked[0]['id'], '9000001')
        self.assertGreater(ranked[0]['score'], ranked[1]['score'])
        self.assertNotIn('strong', [r['tier'] for r in ranked])

    def test_second_identical_search_is_served_from_cache_with_no_fetcher_call(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, FORM), (200, SEARCH))
        self.assertEqual(self.search(net, gate)[0], 0)
        code, lines, _ = self.search(net, gate)
        self.assertEqual(code, 0)
        self.assertEqual(len(net.requests), 2, 'the repeat went to the network (form or results)')
        self.assertEqual(len(gate.waits), 2, 'a cached answer still asked the gate')
        self.assertIn('(cache)', lines['results'])

    def test_gate_is_asked_before_every_network_call(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, FORM), (200, SEARCH), (200, RECORD))
        self.search(net, gate)
        self.run_pares(['record', '9000001'], net, gate)
        self.assertEqual((len(gate.waits), len(net.requests)), (3, 3))

    def test_paused_gate_makes_no_request(self):
        gate = FakeGate(paused=True)
        net = FakeNet(gate)
        code, _, text = self.search(net, gate)
        self.assertEqual((code, net.requests), (gwt_reader.EXIT_PAUSED, []))
        self.assertIn('do other work', text)

    def test_no_words_no_request(self):
        gate = FakeGate()
        net = FakeNet(gate)
        code, _, _ = self.run_pares(['search'], net, gate)
        self.assertEqual((code, net.requests), (64, []))

    def test_page_with_no_rows_exits_2(self):
        gate = FakeGate()
        head = SEARCH.split(b'<div id="caja_resultados">')[0] + b'</div></body></html>'
        code, lines, _ = self.search(FakeNet(gate, (200, FORM), (200, head)), gate)
        self.assertEqual(code, 2)
        self.assertEqual(json.load(open(lines['candidates'])), {'candidates': []})


class Stops(Pares):
    def assert_stop(self, answers, code, gate_list, requests_before_stop):
        gate = FakeGate()
        net = FakeNet(gate, *(answers * 2))
        got, lines, text = self.search(net, gate)
        self.assertEqual(got, code, text)
        self.assertNotIn('candidates', lines)
        self.assertEqual(self.out_files(), [], 'a STOP wrote files')
        self.assertEqual(len(getattr(gate, gate_list)), 1, 'the gate did not hear about it')
        # never cached: the next run asks the site again instead of replaying the block
        self.search(net, gate)
        self.assertEqual(len(net.requests), 2 * requests_before_stop, 'a STOP answer was served from the cache')
        return text

    def test_captcha_on_the_results_is_a_stop(self):
        self.assert_stop([(200, FORM), (200, CAPTCHA)], gwt_reader.EXIT_WALL, 'hits', 2)

    def test_captcha_on_the_form_is_a_stop_before_any_search(self):
        self.assert_stop([(200, CAPTCHA)], gwt_reader.EXIT_WALL, 'hits', 1)

    def test_error_page_instead_of_the_form_is_a_stop_before_any_search(self):
        self.assert_stop([(200, ERROR_PAGE)], gwt_reader.EXIT_WALL, 'hits', 1)

    def test_incapsula_block_is_a_stop(self):
        self.assert_stop([(200, FORM), (200, b'<html><body><div id="_Incapsula_Resource">x</div></body></html>')],
                         gwt_reader.EXIT_WALL, 'hits', 2)

    def test_empty_frame_is_a_stop(self):
        self.assert_stop([(200, FORM), (200, EMPTY_FRAME)], gwt_reader.EXIT_WALL, 'hits', 2)

    def test_site_error_page_is_a_stop(self):
        self.assert_stop([(200, FORM), (200, ERROR_PAGE)], gwt_reader.EXIT_WALL, 'hits', 2)

    def test_403_blocks_the_gate_for_a_day(self):
        gate = FakeGate()
        code, _, _ = self.search(FakeNet(gate, (200, FORM), (403, b'forbidden')), gate)
        self.assertEqual((code, gate.blocks, self.out_files()), (gwt_reader.EXIT_WALL, [24], []))

    def test_429_stops_and_pauses(self):
        self.assert_stop([(200, FORM), (429, b'slow down')], gwt_reader.EXIT_WALL, 'hits', 2)

    def test_record_page_without_a_title_is_a_stop(self):
        gate = FakeGate()
        page = RECORD.replace(b'Pleito de Ana Zorvalo Quirell, vecina de Villazul, con Luis Brin sobre una herencia\n'
                              b'\t\t\t\t</div>', b'</div>', 1)
        self.assertNotEqual(page, RECORD)
        code, _, _ = self.run_pares(['record', '9000001'], FakeNet(gate, (200, page)), gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []))


class Record(Pares):
    def test_record_by_id_or_url(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, RECORD))
        url = 'https://pares.cultura.gob.es/ParesBusquedas20/catalogo/description/9000001?nm'
        code, lines, text = self.run_pares(['record', url], net, gate)
        self.assertEqual(code, 0, text)
        self.assertEqual(net.requests[0].full_url, gwt_pares.BASE + '/description/9000001')
        c, = json.load(open(lines['candidates']))['candidates']
        self.assertEqual((c['id'], c['archive']), ('9000001', 'Archivo Histórico de Nortia'))
        self.assertTrue(c['name'].startswith('Pleito de Ana Zorvalo Quirell'))
        self.assertEqual(c['fields']['Date of creation'], '1871 - 1873')
        self.assertEqual(c['fields']['Reference code'], 'ES.99999.AHN/PLEITOS,101,Exp.7')
        self.assertEqual(lines['results'], '? total, 1 read')
        # served from the cache the second time, with no form fetch: a record needs no session
        self.assertEqual(self.run_pares(['record', '9000001'], net, gate)[0], 0)
        self.assertEqual(len(net.requests), 1)

    def test_bad_id_is_refused_without_a_request(self):
        gate = FakeGate()
        net = FakeNet(gate)
        self.assertEqual(self.run_pares(['record', 'not-an-id'], net, gate)[0], 64)
        self.assertEqual(net.requests, [])


if __name__ == '__main__':
    unittest.main()
