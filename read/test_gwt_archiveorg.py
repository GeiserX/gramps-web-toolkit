"""Tests for `gwt-read archiveorg` (Internet Archive). Answers in fixtures/archiveorg/ keep the API's shape with
invented items; a fake transport and a fake gate: no request leaves this machine.
Run: cd read && python3 -m pytest -q test_gwt_archiveorg.py"""
import contextlib, io, json, os, unittest, urllib.parse

from test_gwt_read import Base, FakeGate, FakeNet
import gwt_archiveorg  # noqa: E402
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'archiveorg')


def fx(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


class Run(Base):
    def run_ia(self, args, net, gate):
        o, e = io.StringIO(), io.StringIO()
        argv = ['archiveorg', '--cache-dir', self.cache, '--lane', 't', 'search'] + args + ['--out', self.out]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate)
        out = o.getvalue()
        lines = dict(l.split('\t', 1) for l in out.splitlines() if l.count('\t') == 1)
        items = [l.split('\t')[1:] for l in out.splitlines() if l.startswith('item\t')]
        return code, lines, items, out + e.getvalue()


class Classify(unittest.TestCase):
    def test_catalogue(self):
        v = gwt_archiveorg.verdict
        self.assertEqual(v(json.loads(fx('search.json'))), 'hits')
        self.assertEqual(v(json.loads(fx('none.json'))), 'none')
        self.assertEqual(v(json.loads(fx('changed.json'))), 'unknown', 'a renamed result container read as "none"')
        self.assertEqual(v({'responseHeader': {'status': 400}, 'response': {'numFound': 0, 'docs': []}}), 'unknown')
        ok = {'responseHeader': {'status': 0}}
        self.assertEqual(v(dict(ok, response={'numFound': '1', 'docs': [{'identifier': 'x'}]})), 'unknown',
                         'a count that is not a whole number')
        self.assertEqual(v(dict(ok, response={'numFound': 1, 'docs': [{'title': 'x'}]})), 'unknown', 'no identifier')

    def test_fulltext(self):
        v = gwt_archiveorg.fulltext_verdict
        self.assertEqual(v(json.loads(fx('fulltext.json'))), 'hits')
        self.assertEqual(v(json.loads(fx('fulltext-none.json'))), 'none')
        failed = json.loads(fx('fulltext-none.json'))
        failed['response']['header']['succeeded'] = False
        self.assertEqual(v(failed), 'unknown', 'a search the backend says failed read as "none"')
        self.assertEqual(v(json.loads(fx('search.json'))), 'unknown')
        hits = lambda total, rows: {'response': {'header': {'succeeded': True}, 'body': {'hits': {'total': total,
                                                                                               'hits': rows}}}}
        self.assertEqual(v(hits('1', [{'fields': {'identifier': 'x'}}])), 'unknown', 'a count that is not a number')
        self.assertEqual(v(hits(1, [{'fields': {'title': 'x'}}])), 'unknown', 'a hit with no identifier')

    def test_a_page_past_the_last_row_is_the_end_never_a_stop(self):
        past = {'responseHeader': {'status': 0}, 'response': {'numFound': 109, 'docs': []}}
        self.assertEqual(gwt_archiveorg.verdict(past, 150), 'none', 'page 4 of 109 items read as a changed answer')
        self.assertEqual(gwt_archiveorg.verdict(past, 50), 'unknown')
        full = {'response': {'header': {'succeeded': True}, 'body': {'hits': {'total': 3, 'hits': []}}}}
        self.assertEqual(gwt_archiveorg.fulltext_verdict(full, 50), 'none')
        self.assertEqual(gwt_archiveorg.fulltext_verdict(full, 0), 'unknown')

    def test_the_catalogue_search_keeps_to_one_mediatype_unless_told(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(gwt_archiveorg.search_url('title:(x)')).query))
        self.assertEqual(q['q'], '(title:(x)) AND mediatype:(texts)')
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(gwt_archiveorg.search_url('title:(x)', '')).query))
        self.assertEqual(q['q'], 'title:(x)')
        self.assertIn('rows=100', gwt_archiveorg.search_url('x', count=999))


class Search(Run):
    def test_items_are_never_principals_and_carry_iiif_and_ocr_links(self):
        gate = FakeGate()
        code, lines, items, text = self.run_ia(['--query', 'title:(parish register)'],
                                               FakeNet(gate, (200, fx('search.json'))), gate)
        self.assertEqual(code, 0, text)
        book, film = json.load(open(lines['candidates']))['candidates']
        self.assertEqual((book['role'], book['principal'], book['events']), ('item', False, {}))
        self.assertFalse(film['principal'])
        self.assertEqual(book['iiif_manifest'], 'https://iiif.archive.org/iiif/registerofnortia00zzzz/manifest.json')
        self.assertEqual(book['ocr'], 'https://archive.org/download/registerofnortia00zzzz/registerofnortia00zzzz_djvu.txt')
        self.assertNotIn('ocr', film, 'a film has no OCR text')
        self.assertEqual((book['year'], film['year']), (1904, 1950))
        self.assertEqual(items[0], ['registerofnortia00zzzz', book['iiif_manifest'], book['ocr']])
        self.assertEqual(items[1], ['villazul-burials-film', film['iiif_manifest'], ''])

    def test_paging_past_the_last_row_is_exit_2(self):
        gate = FakeGate()
        past = json.dumps({'responseHeader': {'status': 0}, 'response': {'numFound': 109, 'docs': []}}).encode()
        code, lines, _, text = self.run_ia(['--query', 'title:(x)', '--page', '4', '--count', '50'],
                                           FakeNet(gate, (200, past)), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []), text)
        self.assertIn('past the end', lines['results'])

    def test_fulltext_keeps_the_snippets_and_never_the_callers_address(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('fulltext.json')))
        code, lines, items, text = self.run_ia(['--fulltext', '--query', '"Ana Zorvalo"'], net, gate)
        self.assertEqual(code, 0, text)
        self.assertIn('service_backend=fts', net.requests[0].full_url)
        (c,) = json.load(open(lines['candidates']))['candidates']
        self.assertEqual(c['snippets'], ['Ana, daughter of Ana Zorvalo, baptized 9 July 1611.'])
        with open(lines['evidence'], encoding='utf-8') as f:
            env = f.read()
        self.assertNotIn('203.0.113.7', env, 'the caller\'s IP address reached the evidence file')
        self.assertNotIn('session_context', env)
        self.assertIn('registerofnortia00zzzz', env)

    def test_the_apis_own_zero_is_exit_2(self):
        for args, name in ((['--query', 'title:(qzxvbrwlk)'], 'none.json'),
                           (['--fulltext', '--query', 'qzxvbrwlk'], 'fulltext-none.json')):
            with self.subTest(name):
                gate = FakeGate()
                code, lines, items, _ = self.run_ia(args, FakeNet(gate, (200, fx(name))), gate)
                self.assertEqual((code, gate.hits, items), (gwt_reader.EXIT_EMPTY, [], []))

    def test_a_changed_answer_is_a_stop_never_zero_results(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fx('changed.json')), (200, fx('changed.json')))
        code, _, _, text = self.run_ia(['--query', 'x'], net, gate)
        self.assertEqual((code, self.out_files()), (gwt_reader.EXIT_ERROR, []), text)
        self.run_ia(['--query', 'x'], net, gate)
        self.assertEqual(len(net.requests), 2, 'a changed answer was served from the cache')

    def test_a_503_is_a_block(self):
        gate = FakeGate()
        code, _, _, text = self.run_ia(['--query', 'x'], FakeNet(gate, (503, b'<html>Service unavailable</html>')), gate)
        self.assertEqual((code, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, 1, []), text)


if __name__ == '__main__':
    unittest.main()
