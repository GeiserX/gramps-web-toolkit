"""Tests for `gwt-read tna` (The National Archives Discovery). Answers in fixtures/tna/ keep the API's shape with
invented people, references and ids; a fake transport and a fake gate: no request leaves this machine.
Run: cd read && python3 -m pytest -q test_gwt_tna.py"""
import argparse, contextlib, io, json, os, unittest, urllib.parse

from test_gwt_read import Base, FakeGate, FakeNet
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402
import gwt_tna  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'tna')


def fx(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


def query(url):
    return urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)


class Run(Base):
    def run_tna(self, args, net, gate):
        o, e = io.StringIO(), io.StringIO()
        argv = ['tna', '--cache-dir', self.cache, '--lane', 't'] + args + ['--out', self.out]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        out = o.getvalue()
        lines = dict(l.split('\t', 1) for l in out.splitlines() if l.count('\t') == 1)
        archives = [l.split('\t')[1:] for l in out.splitlines() if l.startswith('archive\t')]
        return code, lines, archives, out + e.getvalue()

    def search(self, net, gate, *extra):
        return self.run_tna(['search', '--query', 'Will of Ana Zorvalo', *extra], net, gate)


class Classify(unittest.TestCase):
    def test_hits_none_and_a_changed_container_is_unknown(self):
        v = gwt_tna.verdict
        self.assertEqual(v(json.loads(fx('search.json'))), 'hits')
        self.assertEqual(v(json.loads(fx('none.json'))), 'none')
        self.assertEqual(v(json.loads(fx('changed.json'))), 'unknown', 'a renamed result container read as "none"')
        self.assertEqual(v({'records': []}), 'unknown', 'no count is not a zero count')
        self.assertEqual(v({'count': '0', 'records': []}), 'unknown', 'a count that is not a whole number')
        self.assertEqual(v({'count': 4, 'records': []}), 'unknown', 'a count above zero with no rows')
        self.assertEqual(v({'count': 1, 'records': [{'id': 'D1', 'heldBy': 'Kew'}]}), 'unknown',
                         'a row whose heldBy is no longer a list')

    def test_a_page_past_the_last_row_is_the_end_never_a_stop(self):
        past = {'count': 2, 'records': []}
        self.assertEqual(gwt_tna.verdict(past, 20), 'none')
        self.assertEqual(gwt_tna.verdict(past, 0), 'unknown')

    def test_a_details_answer_must_be_the_entry_asked_for(self):
        self.assertTrue(gwt_tna.is_details(json.loads(fx('record.json')), 'D900001'))
        self.assertFalse(gwt_tna.is_details(json.loads(fx('record.json')), 'D900002'))
        self.assertFalse(gwt_tna.is_details([], 'D900001'))

    def test_spec(self):
        self.assertEqual(gwt_reader.spec_problems(gwt_tna.SPEC), [])

    def test_the_search_url(self):
        q = query(gwt_tna.search_url('Will of Ana Zorvalo', ['PROB 11', 'PROB 10'], 'other', 1830, 1835, 500, 2))
        self.assertEqual(q, [('sps.searchQuery', 'Will of Ana Zorvalo'), ('sps.recordSeries', 'PROB 11'),
                             ('sps.recordSeries', 'PROB 10'), ('sps.heldByCode', 'OTH'),
                             ('sps.dateFrom', '1830-01-01'), ('sps.dateTo', '1835-12-31'),
                             ('sps.resultsPageSize', '100'), ('sps.page', '2')])
        self.assertNotIn('sps.heldByCode', dict(query(gwt_tna.search_url('x'))))


class Gate(Base):
    def test_the_gate_keeps_under_the_apis_daily_limit(self):
        # about 3,000 calls a day: the default gate allows 120 an hour, 2,880 a day
        src = gwt_read.sources()['tna']
        a = argparse.Namespace(gate_dir=os.path.join(self.dir, 'g'), cache_dir=self.cache, lane='t',
                               user_agent='test')
        R = src['build'](src, a, None, None)
        self.assertEqual(R.gate.per_hour, 120)
        self.assertLessEqual(R.gate.per_hour * 24, 3000)


class Search(Run):
    def test_rows_are_catalogue_entries_and_the_archives_that_hold_them(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('search.json')))
        code, lines, archives, text = self.search(net, gate, '--series', 'PROB 11')
        self.assertEqual(code, 0, text)
        self.assertIn(('sps.recordSeries', 'PROB 11'), query(net.requests[0].full_url))
        a, b = json.load(open(lines['candidates']))['candidates']
        self.assertEqual((a['name'], a['role'], a['principal'], a['reference'], a['held_by']),
                         ('Will of Ana Zorvalo, Widow of Westbury', 'item', False, 'PROB 11/9001/17',
                          ['The National Archives, Kew']))
        self.assertEqual((a['year'], a['year_to'], a['dates']), (1833, 1833, '12 March 1833'))
        self.assertEqual(a['url'], 'https://discovery.nationalarchives.gov.uk/details/r/D900001')
        self.assertEqual((b['held_by'], b['year'], b['year_to']), (['Westbury Record Office'], 1790, 1850))
        self.assertEqual(archives, [['1', 'The National Archives, Kew'], ['1', 'Westbury Record Office']])
        self.assertTrue(lines['results'].startswith('2 total'))

    def test_the_apis_own_zero_is_exit_2(self):
        gate = FakeGate()
        code, _, archives, _ = self.search(FakeNet(gate, (200, fx('none.json'))), gate)
        self.assertEqual((code, gate.hits, archives), (gwt_reader.EXIT_EMPTY, [], []))

    def test_paging_past_the_last_row_is_exit_2(self):
        gate = FakeGate()
        past = json.dumps({'records': [], 'count': 2, 'repositories': [], 'heldByReps': []}).encode()
        code, lines, _, text = self.search(FakeNet(gate, (200, past)), gate, '--page', '2')
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []), text)
        self.assertIn('past the end', lines['results'])

    def test_a_changed_answer_is_a_stop_never_zero_results(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('changed.json')), (200, fx('changed.json')))
        code, _, _, text = self.search(net, gate)
        self.assertEqual((code, self.out_files()), (gwt_reader.EXIT_ERROR, []), text)
        self.search(net, gate)
        self.assertEqual(len(net.requests), 2, 'a changed answer was served from the cache')

    def test_rows_past_what_discovery_serves_ask_nothing(self):
        gate = FakeGate()
        net = FakeNet(gate)
        code, _, _, text = self.search(net, gate, '--count', '100', '--page', '101')
        self.assertEqual((code, net.requests), (gwt_tna.EXIT_USAGE, []), text)
        self.assertIn('10000 rows', text)
        code, _, _, text = self.search(FakeNet(gate, (200, fx('search.json'))), gate, '--count', '100', '--page', '100')
        self.assertNotEqual(code, gwt_tna.EXIT_USAGE, text)

    def test_an_outage_is_a_block_never_zero_results(self):
        for status in (500, 503):
            with self.subTest(status):
                gate = FakeGate()
                code, _, _, text = self.search(FakeNet(gate, (status, b'<html>Service Unavailable</html>')), gate)
                self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)


class Record(Run):
    def test_one_entry_in_full(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('record.json')))
        code, lines, _, text = self.run_tna(['record', 'D900001'], net, gate)
        self.assertEqual(code, 0, text)
        self.assertTrue(net.requests[0].full_url.endswith('/API/records/v1/details/D900001'))
        (c,) = json.load(open(lines['candidates']))['candidates']
        self.assertEqual((c['name'], c['reference'], c['held_by'], c['digitised'], c['year']),
                         ('Ana Zorvalo', 'PROB 11/9001/17', ['The National Archives, Kew'], True, 1833))

    def test_a_discovery_url_reads_as_its_id(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('record.json')))
        code, _, _, text = self.run_tna(['record', 'https://discovery.nationalarchives.gov.uk/details/r/D900001'],
                                        net, gate)
        self.assertEqual(code, 0, text)

    def test_a_short_id_in_lower_case_asks_for_the_id_in_capitals(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('record.json')))
        code, _, _, text = self.run_tna(['record', 'd900001'], net, gate)
        self.assertEqual(code, 0, text)
        self.assertTrue(net.requests[0].full_url.endswith('/details/D900001'))

    def test_an_unknown_id_is_an_error_never_a_block(self):
        # Discovery answers an id it does not have with HTTP 204 and no body
        gate = FakeGate()
        net = FakeNet(gate, (204, b''), (204, b''))
        code, _, _, text = self.run_tna(['record', 'D999999'], net, gate)
        self.assertEqual((code, gate.hits, gate.blocks, self.out_files()), (gwt_reader.EXIT_ERROR, [], [], []), text)
        self.assertIn('no record with this id', text)
        self.run_tna(['record', 'D999999'], net, gate)
        self.assertEqual(len(net.requests), 2, 'a missing record was served from the cache')

    def test_an_empty_search_answer_is_still_a_block(self):
        # only the details API's 204 means "no such record"; an empty search answer is an empty frame, a STOP
        gate = FakeGate()
        code, _, _, text = self.search(FakeNet(gate, (204, b'')), gate)
        self.assertEqual((code, len(gate.hits)), (gwt_reader.EXIT_WALL, 1), text)

    def test_the_wrong_entry_is_a_stop(self):
        gate = FakeGate()
        code, _, _, text = self.run_tna(['record', 'D900002'], FakeNet(gate, (200, fx('record.json'))), gate)
        self.assertEqual((code, self.out_files()), (gwt_reader.EXIT_ERROR, []), text)


if __name__ == '__main__':
    unittest.main()
