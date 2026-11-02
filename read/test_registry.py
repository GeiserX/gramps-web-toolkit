"""Tests for the gwt-read source registry, the page classifier and FamilySearch's restricted-vs-blocked 403.
Invented pages and ids, a fake transport, a fake gate: no request leaves this machine. The template source
(_template.py) is tested here the way a new source tests itself: copy the Template class into test_gwt_<id>.py.
Run: cd read && python3 -m pytest -q test_registry.py"""
import contextlib, io, json, os, unittest

from test_gwt_read import Base, FakeGate, FakeNet, CAPTCHA, SEARCH, RECORD, persona
import _template
import gwt_boe  # noqa: E402
import gwt_genae  # noqa: E402
import gwt_pares  # noqa: E402
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')


def fixture(*name):
    with open(os.path.join(FIX, *name), 'rb') as f:
        return f.read()


SKELETON = fixture('template', 'skeleton.html')


def with_template():
    return gwt_read.sources(list(gwt_reader.SOURCE_MODULES) + [_template])


class Registry(unittest.TestCase):
    def test_every_source_has_the_shape_gwt_read_builds_from(self):
        srcs = gwt_read.sources()
        self.assertEqual(list(srcs), ['pares', 'genae', 'boe', 'insee', 'familysearch'])
        for src in srcs.values():
            self.assertEqual(gwt_reader.spec_problems(src), [], src['id'])

    def test_every_source_module_imports(self):
        for name in gwt_reader.SOURCE_MODULES:
            self.assertTrue(__import__(name).__name__ == name)

    def test_the_cache_rules_come_from_the_registry(self):
        srcs = gwt_read.sources()
        self.assertIs(srcs['pares']['cache_rules'], gwt_read.PARES_RULES)
        self.assertIs(srcs['boe']['cache_rules'], gwt_read.BOE_RULES)
        self.assertIs(srcs['familysearch']['cache_rules'], gwt_read.FAMILYSEARCH_RULES)

    def test_a_module_with_a_spec_becomes_a_subcommand(self):
        srcs = with_template()
        self.assertEqual(list(srcs)[-1], 'example')
        a = gwt_read.parser(srcs).parse_args(['example', 'search', '--surname', 'Smith', '--out', 'o'])
        self.assertEqual((a.site, a.cmd, a.surname, a.controls), ('example', 'search', 'Smith', None))
        self.assertEqual(gwt_read.parser(srcs).parse_args(['example', 'control']).cmd, 'control')

    def test_a_bad_spec_or_a_taken_id_is_refused(self):
        class Bad:
            __name__ = 'gwt_bad'
            SPEC = dict(_template.SPEC, pace={'start': 1.0})
        with self.assertRaisesRegex(ValueError, 'pace'):
            gwt_read.sources([Bad])

        class Taken:
            __name__ = 'gwt_taken'
            SPEC = dict(_template.SPEC, id='pares')
        with self.assertRaisesRegex(ValueError, 'id already taken'):
            gwt_read.sources([Taken])

    def test_spec_problems_names_each_gap(self):
        bad = gwt_reader.spec_problems({'id': 'Not A Slug', 'controls': [{'argv': [], 'min_count': 0}]})
        for word in ('help must', 'pace must', 'cache_rules must', 'id must be a lowercase slug', 'each control'):
            self.assertTrue(any(word in b for b in bad), (word, bad))


class Classify(unittest.TestCase):
    def test_three_states(self):
        spec = _template.SPEC
        self.assertEqual(gwt_reader.classify(fixture('template', 'results.html').decode(), spec), 'hits')
        self.assertEqual(gwt_reader.classify(fixture('template', 'none.html').decode(), spec), 'none')
        self.assertEqual(gwt_reader.classify(SKELETON.decode(), spec), 'unknown')
        self.assertEqual(gwt_reader.classify(CAPTCHA.decode(), spec), 'unknown')
        self.assertEqual(gwt_reader.classify('', spec), 'unknown')

    def test_the_older_sources_markers_agree_with_their_own_page_checks(self):
        srcs = gwt_read.sources()
        cases = [('boe', gwt_boe.is_results, ['boe-results.html', 'boe-empty.html']),
                 ('pares', gwt_pares.is_results, ['pares-search.html']),
                 ('genae', lambda p: any(gwt_genae.is_block(p, b) for b in gwt_genae.BLOCKS),
                  ['genae-%s.html' % b for b in gwt_genae.BLOCKS])]
        for sid, check, names in cases:
            for page in [fixture(n).decode() for n in names] + [SKELETON.decode(), CAPTCHA.decode()]:
                self.assertEqual(gwt_reader.classify(page, srcs[sid]) != 'unknown', check(page), (sid, page[:80]))
        self.assertEqual(gwt_reader.classify(fixture('boe-empty.html').decode(), srcs['boe']), 'none')
        self.assertEqual(gwt_reader.classify(fixture('genae-casadas.html').decode(), srcs['genae']), 'none')


class Template(Base):
    """How a source tests its search: fixture pages through the fake network and gate."""

    def run_src(self, args, net, gate):
        o, e = io.StringIO(), io.StringIO()
        argv = ['example', '--cache-dir', self.cache, '--lane', 't'] + args
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate, srcs=with_template())
        lines = dict(l.split('\t', 1) for l in o.getvalue().splitlines() if l.count('\t') == 1)
        return code, lines, o.getvalue() + e.getvalue()

    def search(self, net, gate, *extra):
        return self.run_src(['search', '--surname', 'Smith', *extra, '--out', self.out], net, gate)

    def test_rows_are_candidates(self):
        gate = FakeGate()
        code, lines, _ = self.search(FakeNet(gate, (200, fixture('template', 'results.html'))), gate)
        self.assertEqual(code, 0)
        cands = json.load(open(lines['candidates']))['candidates']
        self.assertEqual([c['id'] for c in cands], ['R1001', 'R1002', 'R1003'])
        self.assertEqual(json.load(open(lines['evidence']))['verdict'], 'hits')

    def test_the_sites_own_no_results_words_are_exit_2(self):
        gate = FakeGate()
        code, lines, _ = self.search(FakeNet(gate, (200, fixture('template', 'none.html'))), gate)
        self.assertEqual((code, gate.hits), (gwt_reader.EXIT_EMPTY, []))
        self.assertEqual(json.load(open(lines['evidence']))['verdict'], 'none')

    def test_a_skeleton_page_is_a_stop_never_zero_results(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, SKELETON), (200, SKELETON))
        code, lines, text = self.search(net, gate)
        self.assertEqual(code, gwt_reader.EXIT_WALL, text)
        self.assertNotIn('candidates', lines)
        self.assertEqual(self.out_files(), [], 'a skeleton wrote files')
        self.assertEqual(len(gate.hits), 1, 'the gate did not hear about it')
        self.search(net, gate)
        self.assertEqual(len(net.requests), 2, 'a skeleton was served from the cache')


def fs_entry(ark, given, surname, orig=None, principal=True):
    p = persona('p-' + ark, ark, given, surname, 'Female', [('Christening', '1851', 'Villazul, Nortia')], principal)
    g = {'persons': [p]}
    if orig:
        g['fields'] = [{'labelId': 'EVENT_PLACE_ORIG', 'text': orig}, {'labelId': 'EVENT_PLACE', 'text': 'Nortia'}]
    return {'id': ark, 'content': {'gedcomx': g}}


class DenyGate(FakeGate):
    """FakeGate that records a permission refusal instead of failing on it."""

    def __init__(self, paused=False):
        super().__init__(paused)
        self.denials = []

    def denied(self, lane, what):
        self.denials.append(what)


class FamilySearch(Base):
    def test_candidates_keep_place_orig_role_and_principal(self):
        doc = {'results': 2, 'entries': [fs_entry('QZZ7-AAA', 'Ana', 'Zorvalo', 'San Bartolo, Villazul'),
                                         fs_entry('QZZ7-BBB', 'Ana', 'Zorvalo')]}
        gate = FakeGate()
        code, lines, _ = self.search(FakeNet(gate, (200, json.dumps(doc).encode())), gate)
        self.assertEqual(code, 0)
        c1, c2 = json.load(open(lines['candidates']))['candidates']
        self.assertEqual(c1['place_orig'], ['San Bartolo, Villazul'])
        self.assertNotIn('place_orig', c2, 'a record without the field is written as before')
        self.assertEqual((c1['role'], c1['principal']), ('Principal', True))

    def test_a_record_keeps_its_place_orig(self):
        doc = dict(RECORD, fields=[{'labelId': 'EVENT_PLACE_ORIG', 'text': 'Villazul, parroquia de San Bartolo'}])
        gate = FakeGate()
        code, lines, _ = self.run_fs(['record', '1:1:QZZ1-AAA'], FakeNet(gate, (200, json.dumps(doc).encode())), gate)
        self.assertEqual(code, 0)
        self.assertEqual(json.load(open(lines['candidates']))['candidates'][0]['place_orig'],
                         ['Villazul, parroquia de San Bartolo'])

    def test_a_restricted_record_is_a_result_the_gate_only_logs(self):
        gate = DenyGate()
        body = b'{"errors":[{"message":"no access","permissions":"ThemisPrmAffiliate,ThemisPrmFhlc"}]}'
        code, lines, text = self.run_fs(['record', '1:1:QZZ9-RRR'], FakeNet(gate, (403, body)), gate)
        self.assertEqual(code, gwt_reader.EXIT_RESTRICTED, text)
        self.assertEqual((gate.hits, gate.blocks), ([], []))
        self.assertEqual(len(gate.denials), 1, 'the refusal left no trace in the gate log')
        self.assertIn('1:1:QZZ9-RRR', gate.denials[0])
        self.assertTrue(lines['results'].startswith('restricted'))
        env = json.load(open(lines['evidence']))
        self.assertEqual((env['access'], env['permissions']), ('restricted', ['ThemisPrmAffiliate', 'ThemisPrmFhlc']))
        self.assertEqual(json.load(open(lines['candidates'])), {'candidates': []})

    def test_artifact_denied_in_the_403_body_is_restricted(self):
        gate = DenyGate()
        body = b'{"errors":[{"code":403,"message":"Access to artifact denied."}]}'
        code, _, text = self.run_fs(['record', '1:1:QZZ9-RRR'], FakeNet(gate, (403, body)), gate)
        self.assertEqual((code, gate.hits, gate.blocks, len(gate.denials)), (gwt_reader.EXIT_RESTRICTED, [], [], 1), text)

    def test_a_403_open_to_anyone_is_still_a_block(self):
        gate = FakeGate()
        code, _, _ = self.run_fs(['record', '1:1:QZZ9-RRR'], FakeNet(gate, (403, b'ThemisPrmAnyone,ThemisPrmFhlc')), gate)
        self.assertEqual((code, gate.blocks, len(gate.hits)), (gwt_reader.EXIT_WALL, [24], 1))

    def test_a_plain_403_without_a_control_ark_is_a_block_as_before(self):
        gate = FakeGate()
        code, _, _ = self.run_fs(['record', '1:1:QZZ9-RRR'], FakeNet(gate, (403, b'forbidden')), gate)
        self.assertEqual((code, gate.blocks, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, [24], 1, []))

    def test_a_403_whose_control_record_answers_is_restricted(self):
        gate = DenyGate()
        net = FakeNet(gate, (403, b'forbidden'), (200, json.dumps(RECORD).encode()))
        code, _, text = self.run_fs(['--control-ark', '1:1:QZZ1-AAA', 'record', '1:1:QZZ9-RRR'], net, gate)
        self.assertEqual(code, gwt_reader.EXIT_RESTRICTED, text)
        self.assertEqual((gate.hits, gate.blocks), ([], []))
        self.assertEqual([r.full_url[-12:] for r in net.requests], ['1:1:QZZ9-RRR', '1:1:QZZ1-AAA'])

    def test_a_403_whose_control_is_refused_too_is_a_block(self):
        gate = FakeGate()
        net = FakeNet(gate, (403, b'forbidden'), (403, b'forbidden'))
        code, _, _ = self.run_fs(['--control-ark', '1:1:QZZ1-AAA', 'record', '1:1:QZZ9-RRR'], net, gate)
        self.assertEqual((code, gate.blocks, len(gate.hits), self.out_files()), (gwt_reader.EXIT_WALL, [24], 1, []))

    def test_a_cached_control_proves_nothing_so_it_is_read_again(self):
        gate = DenyGate()
        net = FakeNet(gate, (200, json.dumps(RECORD).encode()), (403, b'forbidden'), (200, json.dumps(RECORD).encode()))
        self.run_fs(['record', '1:1:QZZ1-AAA'], net, gate)
        code, _, _ = self.run_fs(['--control-ark', '1:1:QZZ1-AAA', 'record', '1:1:QZZ9-RRR'], net, gate)
        self.assertEqual((code, len(net.requests)), (gwt_reader.EXIT_RESTRICTED, 3))

    def test_a_search_403_is_never_read_as_restricted(self):
        gate = FakeGate()
        code, _, _ = self.search(FakeNet(gate, (403, b'ThemisPrmAffiliate')), gate)
        self.assertEqual((code, gate.blocks), (gwt_reader.EXIT_WALL, [24]))


if __name__ == '__main__':
    unittest.main()
