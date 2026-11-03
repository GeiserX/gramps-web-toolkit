"""Tests for `gwt-read delpher`. Answers in fixtures/delpher/ keep the SRU API's shape with invented newspapers and
people; a fake transport and a fake gate: no request leaves this machine. Run: cd read && python3 -m pytest -q
test_gwt_delpher.py"""
import contextlib, io, json, os, shutil, unittest, urllib.parse

from test_gwt_read import Base, FakeGate, FakeNet
import gwt_delpher  # noqa: E402
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'delpher')


def fx(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


SEARCH, NONE, DIAG, OCR = fx('search.xml'), fx('none.xml'), fx('diagnostic.xml'), fx('ocr.xml')


class Classify(unittest.TestCase):
    def v(self, body, start=1):
        return gwt_delpher.verdict(gwt_delpher.parse(body), start)

    def test_hits_none_and_anything_else_is_unknown(self):
        self.assertEqual((self.v(SEARCH), self.v(NONE)), ('hits', 'none'))
        self.assertEqual(self.v(DIAG), 'unknown', 'an SRU error (HTTP 200 with a diagnostic) read as "none"')
        self.assertEqual(self.v(b'<html><body>Service unavailable</body></html>'), 'unknown')
        self.assertEqual(self.v(b''), 'unknown')
        self.assertEqual(self.v(SEARCH.replace(b'<srw:numberOfRecords>2</srw:numberOfRecords>', b'')), 'unknown',
                         'no count is not a zero count')
        self.assertEqual(self.v(SEARCH.replace(b'ddd:metadataKey', b'ddd:otherKey')), 'unknown',
                         'a row with no metadataKey is a changed answer')

    def test_a_missing_result_container_is_never_none(self):
        gone = SEARCH.replace(b'<srw:records>', b'').replace(b'</srw:records>', b'')
        self.assertEqual(self.v(gone), 'unknown', 'a count of 2 with the records gone read as "none"')

    def test_past_the_last_row_is_the_end(self):
        past = NONE.replace(b'<srw:numberOfRecords>0', b'<srw:numberOfRecords>2')
        self.assertEqual((self.v(past, 21), self.v(past, 1)), ('none', 'unknown'))

    def test_rows_are_articles_with_their_ocr_link(self):
        a, b = gwt_delpher.candidates(gwt_delpher.parse(SEARCH))
        self.assertEqual((a['id'], a['name'], a['date'], a['year'], a['title'], a['page'], a['role'], a['principal']),
                         ('ddd:099900001:mpeg21:a0007', 'Nieuwe Villazulsche Courant', '1902-05-14', 1902,
                          'Familiebericht', '3', 'mention', False))
        self.assertEqual(a['ocr'], 'https://resolver.kb.nl/resolve?urn=ddd:099900001:mpeg21:a0007:ocr')
        self.assertEqual(a['url'], 'https://resolver.kb.nl/resolve?urn=ddd:099900001:mpeg21:a0007')
        self.assertEqual(b['place'], 'Montefrio')

    def test_the_query_and_date_filter(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(
            gwt_delpher.search_url('"Anna Zorvaal"', '1900', '1910', 500, 0)).query))
        self.assertEqual(q['query'], '("Anna Zorvaal") and date within "01-01-1900 31-12-1910"',
                         'without the brackets the KB parser reads a quoted phrase plus a date filter as 0 rows')
        q2 = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(gwt_delpher.search_url('Zorvaal or Quirel')).query))
        self.assertEqual(q2['query'], 'Zorvaal or Quirel', 'a query with no date filter goes as typed')
        self.assertEqual((q['x-collection'], q['maximumRecords'], q['startRecord']), ('DDD_artikel', '100', '1'))
        self.assertTrue(gwt_delpher.search_url('x').startswith('https://jsru.kb.nl/sru/sru?'))

    def test_a_query_the_api_cannot_parse_is_caught_here(self):
        for bad in ('Zorvaal and (', '"Anna Zorvaal', 'Zorvaal) or (Quirel'):
            self.assertTrue(gwt_delpher.query_problem(bad), bad)
        for good in ('"Anna Zorvaal" and overleden', '(Zorvaal or Quirel) and "a (b"', 'Zorvaal'):
            self.assertEqual(gwt_delpher.query_problem(good), '', good)

    def test_ocr_text(self):
        self.assertTrue(gwt_delpher.is_ocr(OCR.decode()))
        self.assertFalse(gwt_delpher.is_ocr('<html><body>Not found</body></html>'))
        self.assertIn('ANNA ZORVAAL, weduwe', gwt_delpher.ocr_text(OCR))

    def test_spec(self):
        self.assertGreaterEqual(gwt_delpher.SPEC['pace']['floor'], 2.0)
        self.assertEqual(gwt_reader.spec_problems(gwt_delpher.SPEC), [])


class Search(Base):
    def run_d(self, net, gate, *args):
        o, e = io.StringIO(), io.StringIO()
        argv = ['delpher', '--cache-dir', self.cache, '--lane', 't', *args]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        lines = dict(l.split('\t', 1) for l in o.getvalue().splitlines() if l.count('\t') == 1)
        return code, lines, o.getvalue() + e.getvalue()

    def search(self, net, gate, *extra):
        return self.run_d(net, gate, 'search', '--query', '"Anna Zorvaal"', *extra, '--out', self.out)

    def test_rows_and_their_text(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, SEARCH), (200, OCR), (200, OCR))
        code, lines, text = self.search(net, gate)
        self.assertEqual(code, 0, text)
        self.assertEqual(net.requests[1].full_url, 'https://resolver.kb.nl/resolve?urn=ddd:099900001:mpeg21:a0007:ocr')
        cands = json.load(open(lines['candidates']))['candidates']
        self.assertIn('ANNA ZORVAAL', cands[0]['text'])
        env = json.load(open(lines['evidence']))
        self.assertEqual((env['verdict'], env['total']), ('hits', 2))
        self.assertIn('weduwe van P. Quirel', env['text'])
        self.assertEqual(lines['results'], '2 total, 2 read, 2 with text')

    def test_text_0_reads_no_ocr(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, SEARCH))
        code, lines, text = self.search(net, gate, '--text', '0')
        self.assertEqual((code, len(net.requests)), (0, 1), text)

    def test_none_is_exit_2(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, NONE)), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []), text)
        self.assertEqual(json.load(open(lines['evidence']))['verdict'], 'none')

    def test_a_diagnostic_is_a_stop(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, DIAG), (200, DIAG))
        code, lines, text = self.search(net, gate)
        self.assertEqual((code, self.out_files()), (gwt_reader.EXIT_WALL, []), text)
        self.search(net, gate)
        self.assertEqual(len(net.requests), 2, 'an SRU error was served from the cache')

    def test_a_diagnostic_says_what_the_api_said(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (200, DIAG)), gate)
        self.assertIn('the API said: Query syntax error', text)

    def test_a_bad_query_or_year_is_refused_before_any_request(self):
        for extra in (('--query', 'Zorvaal and ('), ('--query', 'Zorvaal', '--from', 'abc'),
                      ('--query', 'Zorvaal', '--to', '19x')):
            gate = FakeGate()
            net = FakeNet(gate)
            with contextlib.redirect_stderr(io.StringIO()):
                try:
                    code, lines, text = self.run_d(net, gate, 'search', *extra, '--out', self.out)
                except SystemExit as e:      # argparse refuses a year that is not a number
                    code = e.code
            self.assertIn(code, (gwt_delpher.EXIT_USAGE, 2), extra)
            self.assertEqual((net.requests, gate.hits), ([], []), extra)

    def test_one_article_without_ocr_keeps_the_other_rows(self):
        for status in (404, 410):
            shutil.rmtree(self.cache, ignore_errors=True)
            gate = FakeGate()
            net = FakeNet(gate, (200, SEARCH), (status, b'not found'), (200, OCR))
            code, lines, text = self.search(net, gate)
            self.assertEqual(code, 0, text)
            a, b = json.load(open(lines['candidates']))['candidates']
            self.assertEqual((a['text'], a['ocr_status']), ('', status))
            self.assertIn('ANNA ZORVAAL', b['text'])
            self.assertEqual(lines['results'], '2 total, 2 read, 1 with text')

    def test_a_busy_ocr_request_stops_the_run(self):
        # only a 404 or 410 means one article has no text; a 429 or 5xx on the resolver is a block
        for status in (429, 503):
            gate = FakeGate()
            net = FakeNet(gate, (200, SEARCH), (status, b'busy'))
            code, lines, text = self.search(net, gate)
            self.assertEqual((code, self.out_files()), (gwt_reader.EXIT_WALL, []), (status, text))

    def test_an_ocr_page_that_is_not_ocr_stops_the_run(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, SEARCH), (200, b'<html><body>Even geduld</body></html>'))
        code, lines, text = self.search(net, gate)
        self.assertEqual((code, self.out_files()), (gwt_reader.EXIT_WALL, []), text)

    def test_a_5xx_is_an_outage(self):
        gate = FakeGate()
        code, lines, text = self.search(FakeNet(gate, (503, b'busy')), gate)
        self.assertEqual((code, len(gate.hits)), (gwt_reader.EXIT_WALL, 1), text)


if __name__ == '__main__':
    unittest.main()
