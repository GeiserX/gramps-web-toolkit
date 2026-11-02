"""Tests for canary controls: `gwt-read <source> control` and `search --controls before|after|both`. Invented pages,
a fake transport, a fake gate: no request leaves this machine. Run: cd read && python3 -m pytest -q test_controls.py"""
import contextlib, io, json, os

from test_gwt_read import Base, FakeGate, FakeNet, CAPTCHA, persona
from test_registry import fixture, with_template
import gwt_read  # noqa: E402
import gwt_reader  # noqa: E402

RESULTS = fixture('template', 'results.html')      # 3 rows: the canary's minimum
ONE = fixture('template', 'one.html')              # 1 row: short
NONE = fixture('template', 'none.html')


class Controls(Base):
    def run_src(self, args, net, gate):
        o, e = io.StringIO(), io.StringIO()
        argv = ['example', '--cache-dir', self.cache, '--lane', 't'] + args
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = gwt_read.main(argv, urlopen=net, gate=gate, srcs=with_template())
        rows = [l.split('\t') for l in o.getvalue().splitlines()]
        return code, rows, o.getvalue() + e.getvalue()

    def search(self, net, gate, *extra):
        return self.run_src(['search', '--given', 'Ana', '--surname', 'Zorvalo', *extra, '--out', self.out], net, gate)

    def test_control_passes_when_the_canary_reads_its_minimum(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, RESULTS))
        code, rows, text = self.run_src(['control'], net, gate)
        self.assertEqual(code, 0, text)
        self.assertEqual(rows, [['control', 'control', '--surname Smith', '3 read, want at least 3', 'ok']])
        self.assertIn('surname=Smith', net.requests[0].full_url)
        self.assertEqual(self.out_files(), [], 'a canary wrote into --out')

    def test_a_short_canary_makes_the_run_void(self):
        gate = FakeGate()
        code, rows, _ = self.run_src(['control'], FakeNet(gate, (200, ONE)), gate)
        self.assertEqual(code, gwt_reader.EXIT_WALL)
        self.assertEqual(rows[0][-2:], ['1 read, want at least 3', 'SHORT'])
        self.assertEqual(rows[1][0], 'void')

    def test_a_canary_with_no_results_is_void_not_ok(self):
        gate = FakeGate()
        code, rows, _ = self.run_src(['control'], FakeNet(gate, (200, NONE)), gate)
        self.assertEqual((code, rows[0][-1]), (gwt_reader.EXIT_WALL, 'SHORT'))

    def test_a_canary_never_reads_the_cache(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, RESULTS), (200, RESULTS))
        self.run_src(['search', '--surname', 'Smith', '--out', self.out], net, gate)
        code, _, _ = self.run_src(['control'], net, gate)
        self.assertEqual((code, len(net.requests)), (0, 2), 'the canary was served from the cache')

    def test_a_blocked_canary_stops_like_any_request(self):
        gate = FakeGate()
        code, _, _ = self.run_src(['control'], FakeNet(gate, (200, CAPTCHA)), gate)
        self.assertEqual((code, len(gate.hits)), (gwt_reader.EXIT_WALL, 1))

    def test_controls_both_brackets_the_search(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, RESULTS), (200, NONE), (200, RESULTS))
        code, rows, text = self.search(net, gate, '--controls', 'both')
        self.assertEqual(code, gwt_reader.EXIT_EMPTY, text)
        self.assertEqual([r[1] for r in rows if r[0] == 'control'], ['before', 'after'])
        self.assertEqual(['Smith' in r.full_url for r in net.requests], [True, False, True])

    def test_a_short_canary_before_means_no_search(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, ONE))
        code, rows, _ = self.search(net, gate, '--controls', 'before')
        self.assertEqual((code, len(net.requests), self.out_files()), (gwt_reader.EXIT_WALL, 1, []))

    def test_a_short_canary_after_voids_the_none(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, NONE), (200, ONE))
        code, rows, _ = self.search(net, gate, '--controls', 'after')
        self.assertEqual(code, gwt_reader.EXIT_WALL)
        self.assertEqual(rows[-1][0], 'void')

    def test_a_void_run_leaves_no_files_and_no_cached_none(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, NONE), (200, ONE), (200, RESULTS))
        code, rows, _ = self.search(net, gate, '--controls', 'after')
        self.assertEqual(code, gwt_reader.EXIT_WALL)
        self.assertEqual(self.out_files(), [], 'a voided search left files that read as "none"')
        self.assertEqual([r[0] for r in rows].count('removed'), 2)
        code, _, _ = self.search(net, gate)      # the same search again, without controls
        self.assertEqual((code, len(net.requests)), (0, 3), 'the voided "none" was served from the cache')

    def test_a_void_run_forgets_only_what_the_search_cached(self):
        gate = FakeGate()
        code, _, _ = self.search(FakeNet(gate, (200, NONE), (200, ONE)), gate, '--controls', 'after')
        self.assertEqual(code, gwt_reader.EXIT_WALL)
        kept = [n for _, _, names in os.walk(self.cache) for n in names if n.endswith('.json')]
        self.assertEqual(len(kept), 1, 'the void also forgot the canary\'s own answer, or kept the search\'s')

    def test_a_stopped_canary_after_also_leaves_no_files_and_no_cached_none(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, NONE), (200, CAPTCHA), (200, RESULTS))
        code, rows, _ = self.search(net, gate, '--controls', 'after')
        self.assertEqual((code, len(gate.hits)), (gwt_reader.EXIT_WALL, 1))
        self.assertEqual(self.out_files(), [], 'a search whose after-canary was stopped left files that read as "none"')
        code, _, _ = self.search(net, gate)      # the same search again, without controls
        self.assertEqual((code, len(net.requests)), (0, 3), 'the unproven "none" was served from the cache')

    def test_a_void_run_keeps_other_files_in_the_folder(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, RESULTS), (200, NONE), (200, ONE))
        self.run_src(['search', '--surname', 'Other', '--out', self.out], net, gate)
        kept = self.out_files()
        code, _, _ = self.search(net, gate, '--controls', 'after')
        self.assertEqual((code, self.out_files()), (gwt_reader.EXIT_WALL, kept))

    def test_controls_read_the_search_fresh_not_from_the_cache(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, NONE), (200, NONE), (200, RESULTS))
        self.search(net, gate)
        code, rows, text = self.search(net, gate, '--controls', 'after')
        self.assertEqual((code, len(net.requests)), (gwt_reader.EXIT_EMPTY, 3), text)
        self.assertNotIn('(cache)', text)


    def test_an_offline_source_has_no_control(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            gwt_read.parser().parse_args(['insee', 'control'])

    def test_genae_adds_no_canary_searches(self):
        # genae.org's robots.txt disallows its search pages: no automated canary there
        for argv in (['genae', 'control'], ['genae', 'search', '--surname', 'X', '--controls', 'both', '--out', 'o']):
            with self.subTest(argv[1]), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                gwt_read.parser().parse_args(argv)


class Legacy(Base):
    def test_familysearch_control_counts_rows_of_its_public_canary(self):
        entries = [{'id': 'QZZ8-%03d' % i, 'content': {'gedcomx': {'persons': [
            persona('p%d' % i, 'QZZ8-%03d' % i, 'John', 'Smith', 'Male', principal=True)]}}} for i in range(12)]
        gate = FakeGate()
        net = FakeNet(gate, (200, json.dumps({'results': 12, 'entries': entries}).encode()))
        o = io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(io.StringIO()):
            code = gwt_read.main(['familysearch', '--cache-dir', self.cache, '--lane', 't', 'control'], urlopen=net, gate=gate)
        self.assertEqual(code, 0, o.getvalue())
        self.assertIn('q.surname=Smith', net.requests[0].full_url)
        self.assertIn('12 read, want at least 10\tok', o.getvalue())

    def test_after_canaries_skip_a_search_that_did_not_run(self):
        gate = FakeGate()
        net = FakeNet(gate)
        o = io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(io.StringIO()):
            code = gwt_read.main(['pares', '--cache-dir', self.cache, '--lane', 't', 'search', '--controls', 'after',
                                  '--out', self.out], urlopen=net, gate=gate)
        self.assertEqual((code, len(net.requests)), (gwt_read.EXIT_USAGE, 0), o.getvalue())
        self.assertNotIn('control', o.getvalue())

    def test_boe_control_with_two_rows_is_void(self):
        gate = FakeGate()
        net = FakeNet(gate, (200, fixture('boe-results.html')))
        o = io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(io.StringIO()):
            code = gwt_read.main(['boe', '--cache-dir', self.cache, '--lane', 't', 'control'], urlopen=net, gate=gate)
        self.assertEqual(code, gwt_reader.EXIT_WALL)
        self.assertIn('%22Manuel%20Rodr%C3%ADguez%22', net.requests[0].full_url)
        self.assertIn('void\tboe', o.getvalue())
