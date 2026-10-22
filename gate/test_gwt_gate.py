import io, json, os, subprocess, sys, tempfile, unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gwt_gate as G


class Clock:
    """Fake time: sleeping moves it forward, and every sleep is recorded."""
    def __init__(self, t=1_000_000.0):
        self.t, self.slept = t, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()

    def tearDown(self):
        self.tmp.cleanup()

    def gate(self, **kw):
        return G.Gate(self.tmp.name, clock=self.clock, sleep=self.clock.sleep, **kw)

    def calls(self):
        return [line.split(' ', 1)[1].strip() for line in self.gate().last_calls(100)]

    def test_lanes_share_one_pace(self):
        g = self.gate(start=10)
        g.wait('a')
        g.wait('b')  # in another process this would be the same: the state is on disk
        self.assertEqual(self.clock.t, 1_000_010.0)
        self.assertEqual(self.calls(), ['call a gap=10', 'call b gap=10'])

    def test_a_hit_pauses_everyone_and_triples_the_gap(self):
        g = self.gate(start=10, pause=1200)
        g.wait('a')
        g.hit('a', 'HTTP 429')
        with self.assertRaises(G.Paused):
            g.wait('b')
        self.assertEqual(g.status(), {'gap_s': 30, 'paused_for_s': 1200})
        self.clock.t += 1200 - 60  # one minute left: short enough to wait for
        g.wait('b')
        self.assertEqual(g.status()['paused_for_s'], 0)
        for _ in range(3):
            g.hit('a', 'again')
        self.assertEqual(g.status()['gap_s'], 60)  # never above the ceiling

    def test_the_gap_shrinks_after_a_quiet_spell_down_to_the_floor(self):
        g = self.gate(start=10.0, floor=8.0, step_after=3600)
        g.wait('a')
        self.clock.t += 3600
        g.wait('a')
        self.assertEqual(g.status()['gap_s'], 8.0)
        self.clock.t += 7200
        g.wait('a')
        self.assertEqual(g.status()['gap_s'], 8.0)
        self.assertIn('pace gap=8.0s (60 min without a hit)', self.calls())

    def test_after_share_priority_calls_in_a_row_the_next_slot_is_open_to_any_lane(self):
        g = self.gate(start=10, priority={'urgent'}, share=2)
        g.wait('urgent'); g.wait('urgent')
        t = self.clock.t
        g.wait('other')  # the claim still holds, but two priority calls in a row used up the share
        self.assertEqual(self.clock.t, t + 10)
        g.wait('urgent')
        t = self.clock.t
        g.wait('other')  # one priority call since: the claim holds it back again
        self.assertEqual(self.clock.t, t + 15)

    def test_a_waiting_non_priority_lane_holds_back_during_the_claim(self):
        g = self.gate(start=10, priority={'urgent'}, share=3)
        g.wait('urgent')
        t = self.clock.t
        g.wait('other')
        self.assertEqual(self.clock.t, t + 15)  # 1.5 gaps of claim, not the plain 10 s gap

    def test_block_stops_everyone_with_its_reason(self):
        g = self.gate()
        g.block(6, 'account suspended')
        with self.assertRaises(G.Paused) as cm:
            g.wait('a')
        self.assertIn('account suspended', str(cm.exception))
        self.assertEqual(round(cm.exception.left), 6 * 3600)

    def test_an_existing_state_file_is_kept(self):
        with open(os.path.join(self.tmp.name, 'state.json'), 'w') as f:
            json.dump({'gap': 22.5, 'last': 0.0, 'pause_until': self.clock.t + 3000, 'last_hit': self.clock.t,
                       'last_step': self.clock.t, 'why': 'from before'}, f)
        with self.assertRaises(G.Paused):
            self.gate().wait('a')
        self.assertEqual(self.gate().status()['gap_s'], 22.5)

    def test_reread_token(self):
        path = os.path.join(self.tmp.name, 'tok')
        self.assertIsNone(G.reread_token(path, 'old'))  # no file: dead
        with open(path, 'w') as f:
            f.write('old\n')
        self.assertIsNone(G.reread_token(path, 'old'))  # unchanged: dead
        with open(path, 'w') as f:
            f.write('new\n')
        self.assertEqual(G.reread_token(path, 'old'), 'new')  # refreshed: retry once with it


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}), redirect_stdout(out), redirect_stderr(err):
            code = G.main(['--dir', self.tmp.name, *argv])
        return code, out.getvalue(), err.getvalue()

    def test_wait_exits_3_while_paused_and_dead_exits_4(self):
        self.assertEqual(self.cli('wait', 'a')[0], 0)
        code, _, err = self.cli('dead', 'a', 'records')
        self.assertEqual(code, 4)
        code, _, err = self.cli('wait', 'b')
        self.assertEqual(code, 3)
        self.assertIn('paused for 20 more minutes', err)
        code, out, _ = self.cli('status')
        self.assertEqual(json.loads(out.splitlines()[0])['gap_s'], 45.0)
        self.assertIn('HIT a HTTP 401 session dead records', out)

    def test_settings_from_the_environment_and_flags(self):
        self.cli('hit', 'a', 'x', env={'GWT_GATE_PAUSE': '600', 'GWT_GATE_START': '2'})
        code, out, _ = self.cli('status')
        self.assertEqual(json.loads(out.splitlines()[0]), {'gap_s': 6.0, 'paused_for_s': 600})
        self.assertEqual(self.cli('wait', 'a')[0], 3)
        self.cli('--pause', '300', 'hit', 'a', 'y', env={'GWT_GATE_PAUSE': '600'})  # a flag beats the environment
        self.assertEqual(json.loads(self.cli('status')[1].splitlines()[0])['paused_for_s'], 300)

    def test_two_processes_share_the_state(self):
        exe = [sys.executable, '-I', os.path.join(HERE, 'gwt-gate'), '--dir', self.tmp.name]
        subprocess.run(exe + ['hit', 'a', 'HTTP 403'], check=True)
        r = subprocess.run(exe + ['wait', 'b'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 3, r.stderr)


if __name__ == '__main__':
    unittest.main()
