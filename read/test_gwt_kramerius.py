"""Tests for `gwt-read kramerius`. Answers in fixtures/kramerius/ keep the Kramerius 5 and 7 Solr shapes with invented
books, papers and people; a fake transport and a fake gate: no request leaves this machine. Run: cd read && python3 -m
pytest -q test_gwt_kramerius.py"""
import contextlib, io, json, os, unittest, urllib.parse

from test_gwt_read import Base, FakeGate, FakeNet
import gwt_kramerius  # noqa: E402
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'kramerius')
P1 = 'uuid:00000000-0000-4000-8000-000000000001'


def fx(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


V7, V5, NONE, CHANGED = fx('search-v7.json'), fx('search-v5.json'), fx('none.json'), fx('changed.json')


class Classify(unittest.TestCase):
    def test_hits_none_and_anything_else_is_unknown(self):
        v = gwt_kramerius.verdict
        self.assertEqual((v(json.loads(V7)), v(json.loads(V5), 'PID'), v(json.loads(NONE))), ('hits', 'hits', 'none'))
        self.assertEqual(v(json.loads(V5)), 'unknown', 'a v5 row read with the v7 field names')
        self.assertEqual(v({'message': 'status code: 400, reason phrase: Bad Request'}), 'unknown')
        self.assertEqual(v({'response': {'docs': []}}), 'unknown', 'no count is not a zero count')
        self.assertEqual(v({'response': {'numFound': 3, 'docs': []}}), 'unknown')
        self.assertEqual(v({'response': {'numFound': 0, 'docs': {}}}), 'unknown', 'rows of another type are not zero rows')

    def test_a_missing_result_container_is_never_none(self):
        self.assertEqual(gwt_kramerius.verdict(json.loads(CHANGED)), 'unknown', 'numFound 2 with no docs read as "none"')

    def test_past_the_last_row_is_the_end(self):
        past = {'response': {'numFound': 2, 'docs': []}}
        self.assertEqual((gwt_kramerius.verdict(past, 'pid', 20), gwt_kramerius.verdict(past, 'pid', 0)),
                         ('none', 'unknown'))

    def test_v7_rows_keep_the_words_the_citation_and_the_image(self):
        a, b = gwt_kramerius.candidates(json.loads(V7), 'mzk')
        self.assertEqual((a['id'], a['name'], a['page'], a['year'], a['role'], a['principal']),
                         ('mzk:' + P1, 'Adresář města Villazul 1912', '214', 1912, 'mention', False))
        self.assertEqual(a['url'], 'https://www.digitalniknihovna.cz/mzk/uuid/' + P1)
        self.assertEqual(a['image'], 'https://api.kramerius.mzk.cz/search/api/client/v7.0/items/%s/image' % P1)
        self.assertEqual(a['text'], 'Zorvalová Anna, vdova, Hlavní 12 / Zorvalová Marie, švadlena')
        self.assertEqual((b['year'], b['model']), (1908, 'periodical/periodicalvolume/periodicalitem/page'))
        self.assertEqual((a['date'], b['date']), ('1912', '1908-03-15'), 'a dd.mm.yyyy date is given as ISO')
        self.assertEqual(a['iiif'], 'https://api.kramerius.mzk.cz/search/iiif/%s/info.json' % P1,
                         'an MZK page names its IIIF service, which gwt fetch iiif reads')

    def test_v5_rows(self):
        (a,) = gwt_kramerius.candidates(json.loads(V5), 'nkp')
        self.assertEqual((a['name'], a['page'], a['year'], a['model']), ('Adresář Nortie', '201', 1927, 'monograph/page'))
        self.assertEqual(a['image'], 'https://kramerius5.nkp.cz/search/img?pid=%s&stream=IMG_FULL&action=GETRAW' % P1)
        self.assertIn('Zorvalová Anna, obch.', a['text'])
        self.assertEqual(a['iiif'], '', 'Kramerius 5 has no IIIF service')

    def test_the_url_asks_only_for_public_pages_in_the_years(self):
        for lib, public, begin, end in (
                ('mzk', 'accessibility:public', 'date_range_start.year:[* TO 1910]', 'date_range_end.year:[1900 TO *]'),
                ('nkp', 'dostupnost:public', 'datum_begin:[* TO 1910]', 'datum_end:[1900 TO *]')):
            u = gwt_kramerius.search_url('"Anna Zorvalová"', lib, '1900', '1910', 500)
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)
            self.assertIn(public, q['fq'])
            self.assertIn(begin, q['fq'])
            self.assertIn(end, q['fq'], '--from was dropped')
            self.assertEqual((q['q'], q['rows']), (['text_ocr:("Anna Zorvalová")'], ['100']))

    def test_spec(self):
        self.assertEqual(gwt_reader.spec_problems(gwt_kramerius.SPEC), [])
        self.assertGreaterEqual(gwt_kramerius.SPEC['pace']['floor'], 2.0)


class Search(Base):
    def run_k(self, net, gate, *args):
        o, e = io.StringIO(), io.StringIO()
        argv = ['kramerius', '--cache-dir', self.cache, '--lane', 't', *args]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        out = o.getvalue()
        lines = dict(l.split('\t', 1) for l in out.splitlines() if l.count('\t') == 1)
        return code, lines, out + e.getvalue()

    def search(self, net, gate, *extra):
        return self.run_k(net, gate, 'search', '--query', 'Zorvalová', *extra, '--out', self.out)

    def test_rows_and_page_lines(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, V7)), gate)
        self.assertEqual(code, 0, text)
        self.assertIn('page\thttps://www.digitalniknihovna.cz/mzk/uuid/%s\thttps://api.kramerius.mzk.cz/' % P1, text)
        self.assertIn('/image\thttps://api.kramerius.mzk.cz/search/iiif/%s/info.json\n' % P1, text)
        env = json.load(open(lines['evidence']))
        self.assertEqual((env['verdict'], env['library'], env['total']), ('hits', 'mzk', 2))
        self.assertIn('Zemřela paní Anna Zorvalová', env['text'])
        self.assertEqual(lines['results'], '2 total, 2 read')

    def test_the_nkp_library_reads_v5(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, V5))
        code, lines, text = self.search(net, gate, '--library', 'nkp')
        self.assertEqual(code, 0, text)
        self.assertTrue(net.requests[0].full_url.startswith('https://kramerius5.nkp.cz/search/api/v5.0/search?'))

    def test_a_year_that_is_not_a_number_is_refused_before_any_request(self):
        gate = FakeGate()
        net = FakeNet(gate)
        for flag in ('--from', '--to'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit, msg=flag):
                self.search(net, gate, flag, '19x')
        self.assertEqual(net.requests, [])

    def test_none_is_exit_2(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, NONE)), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []), text)

    def test_a_changed_answer_is_a_stop(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, CHANGED)), gate)
        self.assertEqual((code, self.out_files()), (gwt_reader.EXIT_ERROR, []), text)

    def test_a_query_solr_refuses_is_an_error_not_an_outage(self):
        # Kramerius answers a query it cannot parse with HTTP 500 {"message": "... Bad Request"}: this search's fault
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (500, b'{"message": "status code: 400, reason phrase: Bad Request"}')),
                                        gate)
        self.assertEqual((code, gate.hits, self.out_files()), (gwt_reader.EXIT_ERROR, [], []), text)
        self.assertIn('could not read the query', text)

    def test_a_429_is_a_wall_whatever_its_body_says(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (429, b'{"message": "Bad Request"}')), gate)
        self.assertEqual((code, len(gate.hits)), (gwt_reader.EXIT_WALL, 1), text)

    def test_any_other_5xx_is_an_outage(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (503, b'{"message": "Service Unavailable"}')), gate)
        self.assertEqual((code, len(gate.hits)), (gwt_reader.EXIT_WALL, 1), text)


if __name__ == '__main__':
    unittest.main()
