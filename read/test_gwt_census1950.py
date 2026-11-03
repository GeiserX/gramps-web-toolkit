"""Tests for `gwt-read census1950` (the 1950 US census name index). Answers in fixtures/census1950/ keep the API's
shape with invented people, places, schedule ids and image keys; a fake transport and a fake gate: no request leaves
this machine. Run: cd read && python3 -m pytest -q test_gwt_census1950.py"""
import contextlib, io, json, os, unittest, urllib.parse

from test_gwt_read import Base, FakeGate, FakeNet
import gwt_census1950  # noqa: E402
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'census1950')
KEY = '1950census/99999999-Puerto_Rico/99999999-Puerto_Rico-000001/99999999-Puerto_Rico-000001-0007.jpg'


def fx(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


class Run(Base):
    def search(self, net, gate, *extra, name='Zorvalo', state='pr'):
        o, e = io.StringIO(), io.StringIO()
        argv = ['census1950', '--cache-dir', self.cache, '--lane', 't', 'search', '--name', name, '--state', state,
                *extra, '--out', self.out]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        out = o.getvalue()
        lines = dict(l.split('\t', 1) for l in out.splitlines() if l.count('\t') == 1)
        items = [l.split('\t')[1:] for l in out.splitlines() if l.startswith('item\t')]
        return code, lines, items, out + e.getvalue()


class Classify(unittest.TestCase):
    def test_hits_none_and_a_changed_container_is_unknown(self):
        v = gwt_census1950.verdict
        self.assertEqual(v(json.loads(fx('search.json'))), 'hits')
        self.assertEqual(v(json.loads(fx('none.json'))), 'none')
        self.assertEqual(v(json.loads(fx('changed.json'))), 'unknown', 'a renamed result container read as "none"')
        self.assertEqual(v({'results': []}), 'unknown', 'no count is not a zero count')
        self.assertEqual(v({'total': 1.0, 'results': []}), 'unknown', 'a count that is not a whole number')
        self.assertEqual(v({'total': 3, 'results': []}), 'unknown', 'a count above zero with no rows')
        self.assertEqual(v({'total': 1, 'results': [{'scheduleId': '1', 'image': None, 'names': []}]}), 'unknown',
                         'a page with no image key')
        self.assertEqual(v({'total': 1, 'results': [{'scheduleId': '1', 'image': 'k.jpg', 'names': {}}]}),
                         'unknown', 'a page whose names are no longer a list')
        self.assertEqual(v({'total': 1, 'results': [{'scheduleId': '1', 'image': 'k.jpg', 'names': None,
                                                     'transcribe': [{'name': 'Ana Zorvalo', 'row': 3}]}]}), 'hits',
                         'a page only the volunteers have read has names null (seen live)')
        self.assertEqual(v({'total': 1, 'results': [{'scheduleId': '1', 'image': 'k.jpg', 'names': None}]}),
                         'unknown', 'a page with no lines at all')
        self.assertEqual(v({'total': 1, 'results': [{'scheduleId': '1', 'image': 'k.jpg', 'names': [],
                                                     'transcribe': 'Ana'}]}), 'unknown',
                         'a page whose volunteer lines are no longer a list')

    def test_a_page_past_the_last_row_is_the_end_never_a_stop(self):
        past = {'total': 2, 'size': 25, 'page': 2, 'results': []}
        self.assertEqual(gwt_census1950.verdict(past, 25), 'none')
        self.assertEqual(gwt_census1950.verdict(past, 0), 'unknown')

    def test_spec_and_pace(self):
        self.assertEqual(gwt_reader.spec_problems(gwt_census1950.SPEC), [])
        self.assertGreaterEqual(gwt_census1950.SPEC['pace']['floor'], 1.0, 'robots.txt asks for 1 s at least')

    def test_the_image_is_the_key_in_naras_public_bucket(self):
        self.assertEqual(gwt_census1950.image_url(KEY), 'https://nara-1950-census.s3.amazonaws.com/' + KEY)

    def test_the_search_url(self):
        q = urllib.parse.parse_qsl(urllib.parse.urlsplit(gwt_census1950.search_url('garcia', 'pr', 'San Juan', 3)).query)
        self.assertEqual(q, [('name', 'garcia'), ('state', 'PR'), ('county', 'San Juan'), ('page', '3')])


class Search(Run):
    def test_rows_are_matching_lines_of_a_page(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('search.json')))
        code, lines, items, text = self.search(net, gate, '--county', 'Villazul')
        self.assertEqual(code, 0, text)
        self.assertIn('name=Zorvalo&state=PR&county=Villazul&page=1', net.requests[0].full_url)
        cands = json.load(open(lines['candidates']))['candidates']
        got = [(c['id'], c['name'], c['row'], c['machine']) for c in cands]
        # the volunteers' lines 3 and 4 win over the machine's misread "Zorvalo Pedra"; the machine's line with no
        # number is kept, and a page with only the machine index gives machine rows
        self.assertEqual(got, [('9900001:3', 'Ana Zorvalo', 3, False), ('9900001:4', 'Pedro Zorvalo', 4, False),
                               ('9900001:m4', 'Zorvalo', None, True), ('9900002:12', 'Zorvalo Rosa', 12, True)])
        a = cands[0]
        self.assertEqual((a['ed'], a['county'], a['state_abbr'], a['sheet'], a['principal']),
                         ('99-12', 'Villazul', 'PR', 7, True))
        self.assertEqual(a['image'], 'https://nara-1950-census.s3.amazonaws.com/' + KEY)
        self.assertEqual(a['url'], a['image'])
        self.assertTrue(a['iiif_info'].endswith('/info.json'))
        self.assertEqual(a['events'], {'residence': {'year': 1950, 'year_to': 1950, 'place': 'Villazul, Puerto Rico'}})
        self.assertEqual([i[0] for i in items], ['9900001', '9900002'])
        self.assertTrue(lines['results'].startswith('2 pages, 4 lines read'))

    def test_a_page_the_api_matched_with_no_marked_line_is_kept_as_the_page(self):
        r = json.loads(fx('search.json'))['results'][1]
        r['highlight'] = {}
        (c,) = gwt_census1950.candidates(r)
        self.assertEqual((c['id'], c['row'], c['machine'], c['name']), ('9900002', None, True, ''))

    def test_every_row_has_its_own_id(self):
        r = json.loads(fx('search.json'))['results'][1]
        r['names'] = [{'row': 1, 'name': 'Zorvalo Ana'}, {'row': 1, 'name': 'Zorvalo Anna'},
                      {'row': None, 'name': 'Zorvalo'}, {'row': None, 'name': 'Zorvalo R'}]
        r['highlight'] = {'names.name': ['Zorvalo Ana', 'Zorvalo Anna', 'Zorvalo', 'Zorvalo R']}
        self.assertEqual([c['id'] for c in gwt_census1950.candidates(r)],
                         ['9900002:1', '9900002:1.2', '9900002:m3', '9900002:m4'])
        ids = [c['id'] for x in json.loads(fx('search.json'))['results'] for c in gwt_census1950.candidates(x)]
        self.assertEqual(len(ids), len(set(ids)))

    def test_a_line_the_volunteers_read_drops_the_machines_reading_of_it(self):
        # the machine read line 5 as "Zorvalo Luis"; the volunteers read a Quintel there, which the search did not mark
        r = json.loads(fx('search.json'))['results'][0]
        r['names'][2]['name'] = 'Zorvalo Luis'
        r['highlight']['names.name'].append('Zorvalo Luis')
        self.assertNotIn(5, [c['row'] for c in gwt_census1950.candidates(r)])

    def test_a_line_whose_name_is_not_text_is_skipped(self):
        r = json.loads(fx('search.json'))['results'][1]
        r['names'].append({'row': 14, 'name': ['Zorvalo']})
        self.assertEqual([c['row'] for c in gwt_census1950.candidates(r)], [12])

    def test_a_page_only_the_volunteers_have_read(self):
        r = json.loads(fx('search.json'))['results'][0]
        r['names'] = None
        r['highlight'] = {'transcribe.name': ['Ana Zorvalo']}
        self.assertEqual([(c['name'], c['row'], c['machine']) for c in gwt_census1950.candidates(r)],
                         [('Ana Zorvalo', 3, False)])

    def test_the_apis_own_zero_is_exit_2(self):
        gate = FakeGate()
        code, _, _, _ = self.search(FakeNet(gate, (200, fx('none.json'))), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []))

    def test_paging_past_the_last_row_is_exit_2(self):
        gate = FakeGate()
        past = json.dumps({'total': 2, 'size': 25, 'page': 2, 'results': []}).encode()
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

    def test_an_outage_is_a_block_never_zero_results(self):
        gate = FakeGate()
        code, _, _, text = self.search(FakeNet(gate, (502, b'<html>Bad Gateway</html>')), gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)

    def test_a_page_past_what_the_api_serves_asks_nothing(self):
        gate = FakeGate()
        net = FakeNet(gate)
        code, _, _, text = self.search(net, gate, '--page', '401')
        self.assertEqual((code, net.requests), (gwt_census1950.EXIT_USAGE, []), text)
        self.assertIn('400 pages', text)
        code, _, _, text = self.search(FakeNet(gate, (200, fx('search.json'))), gate, '--page', '400')
        self.assertNotEqual(code, gwt_census1950.EXIT_USAGE, text)

    def test_a_state_that_is_not_two_letters_asks_nothing(self):
        gate = FakeGate()
        net = FakeNet(gate)
        code, _, _, _ = self.search(net, gate, state='Puerto Rico')
        self.assertEqual((code, net.requests), (gwt_census1950.EXIT_USAGE, []))


if __name__ == '__main__':
    unittest.main()
