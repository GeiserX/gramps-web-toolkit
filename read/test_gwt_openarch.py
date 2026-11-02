"""Tests for `gwt-read openarch` (Open Archives). Answers in fixtures/openarch/ keep the API's shape with invented
people and ids; a fake transport and a fake gate: no request leaves this machine.
Run: cd read && python3 -m pytest -q test_gwt_openarch.py"""
import contextlib, io, json, os, unittest, urllib.error

from test_gwt_read import Base, FakeGate, FakeNet
import gwt_openarch  # noqa: E402
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'openarch')


def fx(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


UUID = '0f1e2d3c-0000-4000-8000-00000000a001'


class Run(Base):
    def run_oa(self, args, net, gate):
        o, e = io.StringIO(), io.StringIO()
        argv = ['openarch', '--cache-dir', self.cache, '--lane', 't'] + args + ['--out', self.out]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        lines = dict(l.split('\t', 1) for l in o.getvalue().splitlines() if l.count('\t') == 1)
        return code, lines, o.getvalue() + e.getvalue()

    def search(self, net, gate, *extra):
        return self.run_oa(['search', '--name', 'Velderhoek', '--period', '1850-1860', *extra], net, gate)


class Classify(unittest.TestCase):
    def test_hits_none_and_a_changed_container_is_unknown(self):
        v = gwt_openarch.verdict
        self.assertEqual(v(json.loads(fx('search.json'))), 'hits')
        self.assertEqual(v(json.loads(fx('none.json'))), 'none')
        self.assertEqual(v(json.loads(fx('changed.json'))), 'unknown', 'a renamed result container read as "none"')
        self.assertEqual(v(json.loads(fx('error200.json'))), 'unknown', 'an error answered with HTTP 200 read as "none"')
        self.assertEqual(v({'response': {'number_found': 3}}), 'unknown', 'a count with no rows read as "none"')
        self.assertEqual(v({'response': {'number_found': '0'}}), 'unknown')

    def test_a_page_past_the_last_row_is_the_end_never_a_stop(self):
        past = {'response': {'number_found': 409, 'docs': []}}
        self.assertEqual(gwt_openarch.verdict(past, 410), 'none', 'paging past the end read as a changed answer')
        self.assertEqual(gwt_openarch.verdict(past, 400), 'unknown', 'rows missing inside the total read as the end')

    def test_record_ids_and_urls(self):
        self.assertEqual(gwt_openarch.record_id('zzarch:' + UUID), ('zzarch', UUID))
        self.assertEqual(gwt_openarch.record_id('https://www.openarchieven.nl/zzarch:%s/en' % UUID), ('zzarch', UUID))
        self.assertIsNone(gwt_openarch.record_id('not a record'))
        self.assertEqual(gwt_openarch.citation_url('zzarch', UUID), 'https://www.openarchieven.nl/zzarch:' + UUID)

    def test_search_url_keeps_the_apis_limits(self):
        u = gwt_openarch.search_url('Velderhoek', '1850-1860', count=500, start=99999)
        self.assertIn('name=Velderhoek+1850-1860', u)
        self.assertIn('number_show=100', u, 'the API returns at most 100 rows')
        self.assertIn('start=10000', u, 'the API refuses a start beyond 10000')

    def test_the_pace_is_never_faster_than_one_request_every_2_s(self):
        self.assertGreaterEqual(gwt_openarch.SPEC['pace']['floor'], 2.0)
        self.assertEqual(gwt_reader.spec_problems(gwt_openarch.SPEC), [])


class Search(Run):
    def test_rows_are_candidates_with_role_and_citation_url(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, fx('search.json'))), gate)
        self.assertEqual(code, 0, text)
        groom, father = json.load(open(lines['candidates']))['candidates']
        self.assertEqual(groom['id'], 'zzarch:' + UUID)
        self.assertEqual(groom['url'], 'https://www.openarchieven.nl/zzarch:' + UUID, 'the citation URL, not the /en page')
        self.assertEqual((groom['role'], groom['principal']), ('Groom', True))
        self.assertEqual(groom['events'], {'marriage': {'year': 1856, 'year_to': 1856, 'place': 'Nortdam'}})
        self.assertEqual((father['role'], father['principal']), ('Father', False), 'a father at a birth is not its principal')
        self.assertEqual(json.load(open(lines['evidence']))['verdict'], 'hits')
        self.assertTrue(lines['results'].startswith('2 total'))

    def test_the_apis_own_zero_is_exit_2(self):
        gate = FakeGate()
        code, lines, _ = self.search(FakeNet(gate, (200, fx('none.json'))), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []))
        self.assertEqual(json.load(open(lines['candidates'])), {'candidates': []})

    def test_paging_past_the_last_row_is_exit_2(self):
        gate = FakeGate()
        past = json.dumps({'query': {'start': 410}, 'response': {'number_found': 409, 'docs': []}}).encode()
        code, lines, text = self.search(FakeNet(gate, (200, past)), gate, '--start', '410')
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []), text)
        self.assertIn('past the end', lines['results'])

    def test_a_changed_answer_is_a_stop_never_zero_results_and_never_cached(self):
        for name in ('changed.json', 'error200.json'):
            with self.subTest(name):
                gate = FakeGate()
                net = FakeNet(gate, (200, fx(name)), (200, fx(name)))
                code, lines, text = self.search(net, gate)
                self.assertEqual(code, gwt_reader.EXIT_ERROR, text)
                self.assertEqual((self.out_files(), lines.get('candidates')), ([], None))
                self.search(net, gate)
                self.assertEqual(len(net.requests), 2, 'a changed answer was served from the cache')

    def test_a_503_is_a_block_the_gate_hears_never_zero_results(self):
        gate = FakeGate()
        code, _, text = self.search(FakeNet(gate, (503, b'<html>Service Unavailable</html>')), gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)
        self.assertIn('service is down', text)

    def test_no_answer_at_all_is_a_block_too(self):
        gate = FakeGate()

        def down(req, timeout=None):
            gate_ok = len(gate.waits)
            assert gate_ok == 1
            raise urllib.error.URLError('timed out')
        code, _, text = self.search(down, gate)
        self.assertEqual((code, len(gate.hits)), (gwt_reader.EXIT_WALL, 1), text)

    def test_a_429_stays_a_wall(self):
        gate = FakeGate()
        code, _, _ = self.search(FakeNet(gate, (429, b'')), gate)
        self.assertEqual((code, len(gate.hits)), (gwt_reader.EXIT_WALL, 1))


class Record(Run):
    def record(self, net, gate, *extra):
        return self.run_oa(['record', 'zzarch:' + UUID, *extra], net, gate)

    def test_every_person_of_the_record_with_role_and_relatives(self):
        gate = FakeGate()
        code, lines, text = self.record(FakeNet(gate, (200, fx('record.json'))), gate)
        self.assertEqual(code, 0, text)
        c = {x['name']: x for x in json.load(open(lines['candidates']))['candidates']}
        self.assertEqual(len(c), 5)
        groom, bride = c['Pieter Velderhoek'], c['Maaike van Brunsveld']
        self.assertEqual((groom['role'], groom['principal'], bride['role'], bride['principal']),
                         ('groom', True, 'bride', True))
        self.assertEqual((c['Jan Velderhoek']['role'], c['Jan Velderhoek']['principal']), ('groom father', False))
        self.assertEqual((c['Klaas Rietdijk']['role'], c['Klaas Rietdijk']['principal']), ('witness', False))
        self.assertEqual((bride['given'], bride['surnames']), ('Maaike', ['van Brunsveld']))
        self.assertEqual(groom['events']['marriage'], {'year': 1856, 'year_to': 1856, 'place': 'Nortdam'})
        self.assertEqual(groom['events']['birth']['place'], 'Zuidwold')
        self.assertIn({'role': 'bride', 'name': 'Maaike van Brunsveld'}, groom['relatives'])
        self.assertEqual(groom['occupation'], 'smid')
        self.assertEqual(groom['url'], 'https://www.openarchieven.nl/zzarch:' + UUID)
        self.assertNotIn('gedcom', lines)

    def test_a_record_with_one_person_is_read_too(self):
        gate = FakeGate()
        code, lines, text = self.record(FakeNet(gate, (200, fx('record-one.json'))), gate)
        self.assertEqual(code, 0, text)
        (c,) = json.load(open(lines['candidates']))['candidates']
        self.assertEqual((c['name'], c['role'], c['principal']), ('Hendrik Velderhoek', 'ondertekenaar', False))

    def test_a_birth_record_child_is_the_principal(self):
        self.assertTrue(gwt_openarch.is_principal('Kind', 'birth'))
        self.assertFalse(gwt_openarch.is_principal('Kind', 'marriage'), 'a child named in a marriage is not its principal')

    def test_gedcom_is_saved_beside_the_record(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('record.json')), (200, fx('record.ged')))
        code, lines, text = self.record(net, gate, '--gedcom')
        self.assertEqual(code, 0, text)
        self.assertEqual(net.requests[1].full_url, 'https://www.openarchieven.nl/zzarch:%s/gedcom' % UUID)
        with open(lines['gedcom'], 'rb') as f:
            self.assertEqual(f.read(), fx('record.ged'))
        self.assertEqual(json.load(open(lines['evidence']))['gedcom'], net.requests[1].full_url)

    def test_an_html_page_at_the_gedcom_url_is_a_stop(self):
        gate = FakeGate()
        page = b'<html><head><title>Pagina niet gevonden</title></head><body>Open Archieven</body></html>'
        code, lines, text = self.record(FakeNet(gate, (200, fx('record.json')), (200, page)), gate, '--gedcom')
        self.assertNotEqual(code, 0, text)
        self.assertEqual(self.out_files(), [], 'an HTML page was saved as the GEDCOM')

    def test_an_error_answered_with_http_200_is_a_stop(self):
        gate = FakeGate()
        code, _, text = self.record(FakeNet(gate, (200, fx('error200.json'))), gate)
        self.assertEqual((code, self.out_files()), (gwt_reader.EXIT_ERROR, []), text)

    def test_a_404_for_one_record_is_an_error_not_a_block(self):
        gate = FakeGate()
        code, _, text = self.record(FakeNet(gate, (404, b'<html>niet gevonden</html>')), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_ERROR, []), text)

    def test_a_bad_record_id_is_usage_and_asks_nothing(self):
        gate = FakeGate()
        net = FakeNet(gate)
        code, _, text = self.run_oa(['record', 'Velderhoek'], net, gate)
        self.assertEqual((code, len(net.requests)), (64, 0), text)


if __name__ == '__main__':
    unittest.main()
