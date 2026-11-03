"""Tests for `gwt-read riksarkivet` (Swedish National Archives). Answers in fixtures/riksarkivet/ keep the API's shape
with an invented parish, volumes and ids; a fake transport and a fake gate: no request leaves this machine.
Run: cd read && python3 -m pytest -q test_gwt_riksarkivet.py"""
import contextlib, io, json, os, unittest, urllib.parse

from test_gwt_read import Base, FakeGate, FakeNet
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402
import gwt_riksarkivet  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'riksarkivet')


def fx(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


def query(url):
    return urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)


class Run(Base):
    def search(self, net, gate, *extra):
        o, e = io.StringIO(), io.StringIO()
        argv = ['riksarkivet', '--cache-dir', self.cache, '--lane', 't', 'search', '--query', 'Granbacka', *extra,
                '--out', self.out]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        out = o.getvalue()
        lines = dict(l.split('\t', 1) for l in out.splitlines() if l.count('\t') == 1)
        items = [l.split('\t')[1:] for l in out.splitlines() if l.startswith('item\t')]
        return code, lines, items, out + e.getvalue()


class Classify(unittest.TestCase):
    def test_hits_none_and_a_changed_container_is_unknown(self):
        v = gwt_riksarkivet.verdict
        self.assertEqual(v(json.loads(fx('search.json'))), 'hits')
        self.assertEqual(v(json.loads(fx('none.json'))), 'none')
        self.assertEqual(v(json.loads(fx('changed.json'))), 'unknown', 'a renamed result container read as "none"')
        self.assertEqual(v({'items': []}), 'unknown', 'no count is not a zero count')
        self.assertEqual(v({'totalHits': False, 'items': []}), 'unknown', 'false is not a count of zero')
        self.assertEqual(v({'totalHits': 3, 'items': []}), 'unknown', 'a count above zero with no rows')
        self.assertEqual(v({'totalHits': 1, 'items': [{'id': 'x', 'metadata': 'Volume'}]}), 'unknown',
                         'a row whose metadata is no longer an object')

    def test_a_page_past_the_last_row_is_the_end_never_a_stop(self):
        past = {'totalHits': 2, 'items': []}
        self.assertEqual(gwt_riksarkivet.verdict(past, 20), 'none')
        self.assertEqual(gwt_riksarkivet.verdict(past, 0), 'unknown')

    def test_spec_and_pace(self):
        self.assertEqual(gwt_reader.spec_problems(gwt_riksarkivet.SPEC), [])
        self.assertGreaterEqual(gwt_riksarkivet.SPEC['pace']['floor'], 2.0)

    def test_small_pages_of_scanned_volumes_by_default(self):
        q = dict(query(gwt_riksarkivet.search_url('Granbacka')))
        self.assertEqual((q['facet'], q['only_digitised_materials'], q['limit'], q['offset']),
                         ('ObjectType:Record;Type:Volume', 'true', '20', '0'))
        q = dict(query(gwt_riksarkivet.search_url('Granbacka', '', False, 500, 3)))
        self.assertNotIn('facet', q)
        self.assertNotIn('only_digitised_materials', q)
        self.assertEqual((q['limit'], q['offset']), ('100', '200'), 'a page is never more than 100 rows')


class Search(Run):
    def test_rows_are_volumes_with_their_manifests(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('search.json')))
        code, lines, items, text = self.search(net, gate)
        self.assertEqual(code, 0, text)
        a, b = json.load(open(lines['candidates']))['candidates']
        self.assertEqual((a['name'], a['role'], a['principal'], a['reference_code']),
                         ('Husförhörslängder (1877 - 1883)', 'volume', False, 'SE/ZZA/90001/A I/3'))
        self.assertEqual((a['archive'], a['series'], a['year'], a['year_to']),
                         ('Granbacka församlings kyrkoarkiv', ['Husförhörslängder'], 1877, 1883))
        self.assertEqual(a['iiif_manifests'], ['https://lbiiif.riksarkivet.se/arkis!Z0000001/manifest',
                                               'https://lbiiif.riksarkivet.se/arkis!Z0000002/manifest'])
        self.assertEqual(a['url'], 'https://sok.riksarkivet.se/arkiv/ZZvolume0001')
        self.assertEqual((b['year'], b['year_to'], b['iiif_manifests']), (1829, None, []))
        self.assertEqual(items, [['SE/ZZA/90001/A I/3', 'https://lbiiif.riksarkivet.se/arkis!Z0000001/manifest'],
                                 ['SE/ZZA/90001/A I/3', 'https://lbiiif.riksarkivet.se/arkis!Z0000002/manifest'],
                                 ['SE/ZZA/90001/C/1', '']])
        self.assertTrue(lines['results'].startswith('2 total'))
        env = json.load(open(lines['evidence']))
        self.assertNotIn('facets', env['json'], 'the facets, most of every answer, are not kept in the evidence')

    def test_the_apis_own_zero_is_exit_2(self):
        gate = FakeGate()
        code, lines, _, _ = self.search(FakeNet(gate, (200, fx('none.json'))), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []))

    def test_paging_past_the_last_row_is_exit_2(self):
        gate = FakeGate()
        past = json.dumps({'totalHits': 2, 'hits': 0, 'offset': 20, 'facets': [], 'items': []}).encode()
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

    def test_an_empty_query_asks_nothing(self):
        gate = FakeGate()
        net = FakeNet(gate)
        code, _, _, _ = self.search(net, gate, '--query', '  ')
        self.assertEqual((code, net.requests), (gwt_riksarkivet.EXIT_USAGE, []))

    def test_a_single_manifest_link_is_one_manifest(self):
        c = gwt_riksarkivet.candidate({'id': 'ZZvolume0003', 'metadata': {},
                                       '_links': {'image': 'https://lbiiif.riksarkivet.se/arkis!Z0000003/manifest'}})
        self.assertEqual(c['iiif_manifests'], ['https://lbiiif.riksarkivet.se/arkis!Z0000003/manifest'])

    def test_an_outage_is_a_block_never_zero_results(self):
        for status in (500, 503):
            with self.subTest(status):
                gate = FakeGate()
                code, _, _, text = self.search(FakeNet(gate, (status, b'<html>Bad Gateway</html>')), gate)
                self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)

    def test_an_answer_is_cached(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('search.json')))
        self.search(net, gate)
        code, lines, _, _ = self.search(net, gate)
        self.assertEqual((code, len(net.requests)), (0, 1))
        self.assertIn('(cache)', lines['results'])


if __name__ == '__main__':
    unittest.main()
