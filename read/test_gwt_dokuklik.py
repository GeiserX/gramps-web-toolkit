"""Tests for `gwt-read dokuklik`. Answers in fixtures/dokuklik/ keep the site's JSON shape with invented people and
ids; a fake transport and a fake gate: no request leaves this machine. Run: cd read && python3 -m pytest -q
test_gwt_dokuklik.py"""
import contextlib, io, json, os, unittest

from test_gwt_read import Base, FakeGate, FakeNet
import gwt_dokuklik  # noqa: E402
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'dokuklik')


def fx(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


class Run(Base):
    def search(self, net, gate, *extra):
        o, e = io.StringIO(), io.StringIO()
        argv = ['dokuklik', '--cache-dir', self.cache, '--lane', 't', 'search', '--surname', 'Ostrabide', *extra,
                '--out', self.out]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        lines = dict(l.split('\t', 1) for l in o.getvalue().splitlines() if l.count('\t') == 1)
        return code, lines, o.getvalue() + e.getvalue()


class Shape(unittest.TestCase):
    def test_verdict(self):
        v = gwt_dokuklik.verdict
        self.assertEqual(v(json.loads(fx('baptisms.json')), 'baptism'), 'hits')
        self.assertEqual(v(json.loads(fx('none.json')), 'baptism'), 'none')
        self.assertEqual(v(json.loads(fx('changed.json')), 'baptism'), 'unknown', 'a renamed row list read as "none"')
        self.assertEqual(v({'data': []}, 'baptism'), 'unknown', 'no count is not a zero count')
        self.assertEqual(v({'recordsFiltered': 0, 'rows': []}, 'baptism'), 'unknown', 'a renamed row list, zero count')
        self.assertEqual(v({'recordsFiltered': 4, 'data': []}, 'baptism'), 'unknown')
        self.assertEqual(v({'recordsFiltered': 4, 'data': []}, 'baptism', 100), 'none', 'past the end is the end')
        self.assertEqual(v({'recordsFiltered': 1, 'data': [{'matid': 1}]}, 'baptism'), 'unknown',
                         'a row without its own kind\'s id')

    def test_the_body_is_the_one_the_page_posts(self):
        b = json.loads(gwt_dokuklik.search_body('death', 'Martin', 'Ostrabide', year_from=1800, archive='bilbao',
                                                count=50, start=100))
        self.assertEqual([c['data'] for c in b['columns']], list(gwt_dokuklik.KINDS['death'][3]),
                         'the site refuses a body without its table\'s columns')
        self.assertEqual((b['bautismoHijoNombre'], b['bautismoHijoApellido1'], b['anioInicial'], b['start'],
                          b['length'], b['archivosDiocesanos']), ('Martin', 'Ostrabide', '1800', 100, 50, ['3']))

    def test_the_pace(self):
        self.assertGreaterEqual(gwt_dokuklik.SPEC['pace']['floor'], 3.0)
        self.assertEqual(gwt_reader.spec_problems(gwt_dokuklik.SPEC), [])

    def test_the_archive_choices_show_no_empty_one(self):
        o = io.StringIO()
        with contextlib.redirect_stdout(o), self.assertRaises(SystemExit):
            gwt_read.main(['dokuklik', 'search', '--help'])
        self.assertIn('{bilbao,san-sebastian,vitoria}', o.getvalue())


class Search(Run):
    def test_rows_are_baptisms_with_parents_and_a_record_page(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('baptisms.json')))
        code, lines, text = self.search(net, gate, '--from', '1800')
        self.assertEqual(code, 0, text)
        req = net.requests[0]
        self.assertEqual((req.get_method(), req.full_url), ('POST', gwt_dokuklik.BASE + '/sacramentales/busquedaBautismo'))
        self.assertEqual(req.get_header('Content-type'), 'application/json')
        sent = json.loads(req.data)
        self.assertEqual((sent['bautismoHijoApellido1'], sent['anioInicial']), ('Ostrabide', '1800'))
        a, b = json.load(open(lines['candidates']))['candidates']
        self.assertEqual((a['id'], a['name'], a['given'], a['surnames']),
                         ('bau-9000001', 'Ana Josefa Ostrabide Zorvalo', 'Ana Josefa', ['Ostrabide', 'Zorvalo']))
        self.assertEqual(a['relatives'], [{'role': 'father', 'name': 'Juan Ostrabide Lurzubi'},
                                          {'role': 'mother', 'name': 'Maria Zorvalo'}])
        self.assertEqual(a['events'], {'baptism': {'date': '1810-03-04', 'place': 'San Bartolo, Ibarra, Villazul, BIZKAIA'}})
        self.assertEqual((a['book'], a['folio'], a['call_number']), ('Libro 2 de bautizados', 'f.12r', '0001/001-00'))
        self.assertEqual(a['url'], gwt_dokuklik.BASE + '/bautismo/getFicha?bauid=9000001')
        self.assertEqual(b['surnames'], ['Ostrabide'], 'an empty second surname is left out')
        self.assertTrue(lines['results'].startswith('2 total, 2 read'))

    def test_a_marriage_names_the_groom_and_the_bride(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('marriages.json')))
        code, lines, text = self.search(net, gate, '--kind', 'marriage')
        self.assertEqual(code, 0, text)
        self.assertTrue(net.requests[0].full_url.endswith('/busquedaMatrimonio'))
        (c,) = json.load(open(lines['candidates']))['candidates']
        self.assertEqual((c['name'], c['relatives']), ('Martin Lurzubi Ostrabide', [{'role': 'bride',
                                                                                    'name': 'Ana Zorvalo Ibarzuri'}]))
        self.assertEqual(c['note'], None, 'the site\'s dashes for "nothing" are not a note')
        self.assertTrue(c['url'].endswith('/matrimonio/getFicha?matid=9100001'))

    def test_the_sites_own_zero_is_exit_2(self):
        gate = FakeGate()
        code, lines, _ = self.search(FakeNet(gate, (200, fx('none.json'))), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []))
        self.assertEqual(json.load(open(lines['candidates'])), {'candidates': []})

    def test_a_changed_answer_is_a_stop_never_zero_results(self):
        gate = FakeGate()
        code, _, text = self.search(FakeNet(gate, (200, fx('changed.json'))), gate)
        self.assertEqual((code, self.out_files()), (gwt_reader.EXIT_ERROR, []), text)

    def test_the_sites_error_page_is_a_stop(self):
        gate = FakeGate()
        code, _, text = self.search(FakeNet(gate, (200, fx('error.html'))), gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)

    def test_an_outage_is_a_block(self):
        gate = FakeGate()
        code, _, text = self.search(FakeNet(gate, (503, b'<html>down</html>')), gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)

    def test_a_repeated_search_is_served_from_the_cache(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('baptisms.json')))
        self.search(net, gate)
        code, lines, _ = self.search(net, gate)
        self.assertEqual((code, len(net.requests)), (0, 1), 'a POST search was asked again')
        self.assertIn('(cache)', lines['results'])

    def test_another_search_is_not_served_the_first_ones_answer(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('baptisms.json')), (200, fx('none.json')))
        self.search(net, gate)
        code, _, _ = self.search(net, gate, '--given', 'Pedro')
        self.assertEqual((code, len(net.requests)), (gwt_reader.EXIT_EMPTY, 2))

    def test_paging_past_the_last_row_is_exit_2(self):
        gate = FakeGate()
        past = json.dumps({'data': [], 'recordsTotal': 2, 'recordsFiltered': 2}).encode()
        net = FakeNet(gate, (200, past))
        code, lines, text = self.search(net, gate, '--page', '2')
        self.assertEqual(code, gwt_reader.EXIT_EMPTY, text)
        self.assertIn('past the end', lines['results'])
        self.assertEqual(json.loads(net.requests[0].data)['start'], 100)

    def test_a_search_needs_a_name(self):
        gate = FakeGate()
        net = FakeNet(gate)
        o, e = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(['dokuklik', '--cache-dir', self.cache, 'search', '--from', '1800', '--out', self.out],
                                 urlopen=net, gate=gate)
        self.assertEqual((code, net.requests), (64, []))


if __name__ == '__main__':
    unittest.main()
