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
        self.assertEqual({k: g.status()[k] for k in ('gap_s', 'paused_for_s')}, {'gap_s': 30, 'paused_for_s': 1200})
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

    def test_wait_reports_the_text_of_the_hit(self):
        g = self.gate()
        g.hit('lane-a', 'HTTP 429 /search')
        with self.assertRaises(G.Paused) as cm:
            g.wait('lane-b')
        self.assertEqual(cm.exception.why, 'HTTP 429 /search')
        self.assertEqual(g.status()['why'], 'HTTP 429 /search')
        self.assertTrue(g.status()['last_hit'].endswith(' lane-a HTTP 429 /search'))

    def test_a_reason_ends_with_its_pause(self):
        g = self.gate(start=10)
        g.block(1, 'old block')
        self.clock.t += 7200
        g.wait('a')
        self.assertEqual(g.status()['why'], '')
        self.assertEqual(G.json.load(open(os.path.join(self.tmp.name, 'state.json')))['why'], '')
        g.hit('a', 'HTTP 429 new')
        with self.assertRaises(G.Paused) as cm:
            g.wait('b')
        self.assertEqual(cm.exception.why, 'HTTP 429 new')

    def test_dead_pauses_without_touching_the_gap(self):
        g = self.gate(start=10, pause=1200)
        g.dead('a', 'HTTP 401 session dead')
        self.assertEqual(g.status()['gap_s'], 10)
        self.assertEqual(g.status()['paused_for_s'], 1200)
        self.assertEqual(g.status()['why'], 'HTTP 401 session dead')

    def test_dead_does_not_shorten_a_block(self):
        g = self.gate(pause=1200)
        g.block(6, 'account suspended')
        g.dead('a', 'HTTP 401')
        self.assertEqual(g.status()['paused_for_s'], 6 * 3600)
        self.assertEqual(g.status()['why'], 'account suspended')
        self.assertIn('DEAD a HTTP 401 -> pause kept (account suspended), gap=15.0s', self.calls())
        g.clear('relogged')
        self.assertIn('CLEAR relogged (was: account suspended)', self.calls())

    def test_status_drops_a_reason_once_its_pause_has_run_out(self):
        g = self.gate(start=10)
        g.block(1, 'old')
        self.clock.t += 7200  # no wait in between
        self.assertEqual(g.status()['why'], '')

    def test_denied_neither_pauses_nor_moves_the_gap(self):
        g = self.gate(start=10)
        g.wait('a')
        before = dict(G.json.load(open(os.path.join(self.tmp.name, 'state.json'))))
        g.denied('a', 'HTTP 403 /record/1')
        after = G.json.load(open(os.path.join(self.tmp.name, 'state.json')))
        self.assertEqual(after['pause_until'], before['pause_until'])
        self.assertEqual(after['gap'], 10)
        self.assertIn('DENIED a HTTP 403 /record/1', self.calls())

    def test_clear_ends_a_pause_and_logs_why(self):
        g = self.gate(start=10)
        g.block(24, 'ip blocked')
        g.clear('unblocked by support')
        self.assertEqual(g.status()['paused_for_s'], 0)
        self.assertEqual(g.status()['why'], '')
        self.assertEqual(G.json.load(open(os.path.join(self.tmp.name, 'state.json')))['why'], '')
        self.assertIn('CLEAR unblocked by support (was: ip blocked)', self.calls())
        g.wait('a')  # no exit 3, no long sleep
        self.assertLessEqual(sum(self.clock.slept), 10)

    def test_the_gap_is_rounded_in_the_log(self):
        g = self.gate(start=8.2)
        g.hit('a', 'x')
        self.assertIn('HIT a x -> pause 20 min, gap=24.6s', self.calls())
        self.clock.t += 1200  # wait out the pause
        g.wait('a')
        self.assertIn('call a gap=24.6', self.calls())

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
        self.assertEqual(json.loads(out.splitlines()[0])['gap_s'], 15.0)  # a 401 says nothing about the rate
        self.assertIn('DEAD a HTTP 401 session dead records', out)

    def test_settings_from_the_environment_and_flags(self):
        self.cli('hit', 'a', 'x', env={'GWT_GATE_PAUSE': '600', 'GWT_GATE_START': '2'})
        code, out, _ = self.cli('status')
        self.assertEqual(json.loads(out.splitlines()[0])['paused_for_s'], 600)
        self.assertEqual(json.loads(out.splitlines()[0])['gap_s'], 6.0)
        self.assertEqual(self.cli('wait', 'a')[0], 3)
        self.cli('--pause', '300', 'hit', 'a', 'y', env={'GWT_GATE_PAUSE': '600'})  # a flag beats the environment
        self.assertEqual(json.loads(self.cli('status')[1].splitlines()[0])['paused_for_s'], 300)

    def test_status_denied_and_clear(self):
        self.cli('hit', 'a', 'HTTP 429 /search')
        code, out, _ = self.cli('status')
        st = json.loads(out.splitlines()[0])
        self.assertEqual(st['why'], 'HTTP 429 /search')
        self.assertTrue(st['last_hit'].endswith('a HTTP 429 /search'))
        code, _, err = self.cli('wait', 'b')
        self.assertIn('HTTP 429 /search', err)
        self.assertEqual(self.cli('denied', 'b', '/record/1')[0], 0)
        self.assertEqual(json.loads(self.cli('status')[1].splitlines()[0])['gap_s'], st['gap_s'])
        self.assertEqual(self.cli('clear', 'false', 'alarm')[0], 0)
        self.assertEqual(self.cli('wait', 'b')[0], 0)
        out = self.cli('status')[1]
        self.assertIn('DENIED b HTTP 403 /record/1', out)
        self.assertIn('CLEAR false alarm (was: HTTP 429 /search)', out)

    def test_two_processes_share_the_state(self):
        exe = [sys.executable, '-I', os.path.join(HERE, 'gwt-gate'), '--dir', self.tmp.name]
        subprocess.run(exe + ['hit', 'a', 'HTTP 403'], check=True)
        r = subprocess.run(exe + ['wait', 'b'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 3, r.stderr)


NOTE = ("import sys\n"
        "with open(sys.argv[1], 'a') as f:\n"
        "    f.write('\\x1f'.join(sys.argv[2:]) + '\\n')\n")
FIXTURES = os.path.join(HERE, '..', 'myheritage', 'fixtures')


class HumanAuthProfileTest(unittest.TestCase):
    """Captchas park the gate for a person, a 401 parks one account, a site's limits cap the pace."""
    gate, calls, tearDown = GateTest.gate, GateTest.calls, GateTest.tearDown

    def setUp(self):
        GateTest.setUp(self)
        self.out = os.path.join(self.tmp.name, 'notices.txt')
        self.script = os.path.join(self.tmp.name, 'note.py')
        with open(self.script, 'w') as f:
            f.write(NOTE)
        self.notify = f'{sys.executable} -I {self.script} {self.out} {{site}} {{lane}} {{url}} {{what}}'
        self.sleeps = 0

    def notices(self):
        try:
            with open(self.out) as f:
                return [line.rstrip('\n').split('\x1f') for line in f]
        except OSError:
            return []

    def capped_sleep(self, s):  # a wait-clear that never stops fails the test instead of hanging it
        self.sleeps += 1
        if self.sleeps > 200:
            raise RuntimeError('wait-clear never stopped')
        self.clock.sleep(s)

    def test_human_parks_every_lane_and_notifies_once_per_parking(self):
        g = self.gate(start=10, notify=self.notify)
        self.assertTrue(g.human('lane-a', 'hCaptcha', 'https://example.org/search?q=1'))
        self.assertFalse(g.human('lane-b', 'hCaptcha again'))  # same parking: no second notice
        self.assertEqual(self.notices(), [[os.path.basename(self.tmp.name), 'lane-a',
                                           'https://example.org/search?q=1', 'hCaptcha']])
        for lane in ('lane-a', 'lane-c'):
            with self.assertRaises(G.Paused) as cm:
                g.wait(lane)
            self.assertEqual(cm.exception.left, G.PARKED)
        self.assertIn('until it is cleared', str(cm.exception))
        self.assertIn('hCaptcha', cm.exception.why)
        self.assertEqual(g.status()['gap_s'], 30)  # a captcha is a rate answer too
        self.assertEqual(g.status()['human']['lane'], 'lane-a')
        self.clock.t += 10 * 86400  # no timer ends the parking
        with self.assertRaises(G.Paused):
            g.wait('lane-a')
        g.clear('solved it by hand')
        self.assertIn('CLEAR solved it by hand (was: a person: hCaptcha)', self.calls())
        g.wait('lane-a')
        self.assertTrue(g.human('lane-a', 'second captcha'))  # a new parking notifies again
        self.assertEqual(len(self.notices()), 2)

    def test_notify_fills_each_word_alone_and_never_runs_a_shell(self):
        g = self.gate(notify=self.notify)
        marker = os.path.join(self.tmp.name, 'pwned')
        what = f'two words; touch {marker} $(touch {marker}) `touch {marker}`'
        g.human('a', what, 'https://example.org/a b')
        self.assertEqual(self.notices()[0][2:], ['https://example.org/a b', what])
        self.assertFalse(os.path.exists(marker))

    def test_a_failing_notify_command_is_logged_not_raised(self):
        g = self.gate(notify='/nonexistent/notify {what}')
        g.human('a', 'captcha')
        self.assertTrue(any(c.startswith('NOTIFY a exit=FileNotFoundError') for c in self.calls()))

    def test_wait_clear_releases_when_the_probe_passes(self):
        flag = os.path.join(self.tmp.name, 'solved')
        g = G.Gate(self.tmp.name, clock=self.clock, notify=self.notify,
                   sleep=lambda s: (self.capped_sleep(s), self.sleeps == 3 and open(flag, 'w').close()))
        g.human('a', 'captcha')
        probe = f'{sys.executable} -I -c "import os,sys; sys.exit(0 if os.path.exists(sys.argv[1]) else 1)" {flag}'
        self.assertTrue(g.wait_clear(probe, every=60, timeout=3600))
        self.assertEqual(self.sleeps, 3)
        self.assertEqual(len(self.notices()), 1)  # passed before the timeout: no reminder
        g.wait('a')
        self.assertTrue(any(c.startswith('CLEAR probe passed') for c in self.calls()))

    def test_a_passing_probe_ends_the_parking_but_not_a_block(self):
        g = G.Gate(self.tmp.name, clock=self.clock, sleep=self.capped_sleep)
        g.block(24, 'account suspended')
        g.human('a', 'captcha')
        ok = f'{sys.executable} -I -c "raise SystemExit(0)"'
        self.assertTrue(g.wait_clear(ok, every=60, timeout=3600))
        self.assertNotIn('human', g.status())
        with self.assertRaises(G.Paused) as cm:
            g.wait('a')
        self.assertEqual(cm.exception.why, 'account suspended')  # the block still runs
        g.clear('lifted')  # by hand, clear ends both
        g.wait('a')

    def test_wait_clear_reminds_once_after_the_timeout_then_gives_up_parked(self):
        g = G.Gate(self.tmp.name, clock=self.clock, sleep=self.capped_sleep, notify=self.notify)
        g.human('a', 'captcha', 'https://example.org/')
        fail = f'{sys.executable} -I -c "raise SystemExit(1)"'
        self.assertFalse(g.wait_clear(fail, every=600, timeout=1800))
        n = self.notices()
        self.assertEqual([x[3] for x in n], ['captcha', 'still waiting: captcha'])  # first notice + one reminder
        self.assertEqual(round(self.clock.t - 1_000_000), 3600)
        with self.assertRaises(G.Paused):
            g.wait('a')

    def test_wait_clear_stops_at_once_when_someone_cleared_by_hand(self):
        g = G.Gate(self.tmp.name, clock=self.clock, sleep=self.capped_sleep)
        self.assertTrue(g.wait_clear(f'{sys.executable} -I -c "raise SystemExit(1)"'))
        self.assertEqual(self.sleeps, 0)

    def test_auth_expired_parks_one_account_and_keeps_the_gap(self):
        session = os.path.join(self.tmp.name, 'session.txt')
        with open(session, 'w') as f:
            f.write('old')
        g = self.gate(start=10, pause=1200)
        g.auth_expired('a', 'acct-1', session, 'HTTP 401')
        st = g.status()
        self.assertEqual((st['gap_s'], st['paused_for_s'], st['auth_expired']), (10, 0, ['acct-1']))
        with self.assertRaises(G.Paused) as cm:
            g.wait('a', account='acct-1')
        self.assertIn('acct-1', str(cm.exception))
        g.wait('b', account='acct-2')  # another account goes on
        g.wait('c')                    # and so does a lane that names no account
        self.assertEqual(sum(self.clock.slept), 10)  # one plain gap, no pause
        os.utime(session, ns=(0, os.stat(session).st_mtime_ns + 10 ** 9))  # someone signed in again
        g.wait('a', account='acct-1')
        self.assertIn(f'AUTH acct-1 session renewed ({session})', self.calls())
        self.assertNotIn('auth_expired', g.status())

    def test_a_relative_session_path_holds_from_any_directory(self):
        os.makedirs(os.path.join(self.tmp.name, 'one', 'sub'))
        with open(os.path.join(self.tmp.name, 'one', 'sub', 'tok'), 'w') as f:
            f.write('old')
        here = os.getcwd()
        try:
            os.chdir(os.path.join(self.tmp.name, 'one'))
            self.gate().auth_expired('a', 'acct-1', 'sub/tok', 'HTTP 401')
            os.chdir('sub')  # a lane started elsewhere: 'sub/tok' no longer exists from here
            with self.assertRaises(G.Paused):
                self.gate().wait('b', account='acct-1')
        finally:
            os.chdir(here)

    def test_clear_releases_an_expired_account(self):
        session = os.path.join(self.tmp.name, 'never-rewritten.txt')
        g = self.gate()
        g.auth_expired('a', 'acct-1', session, 'HTTP 401')
        g.clear('signed in through the browser')
        g.wait('a', account='acct-1')
        self.assertNotIn('auth_expired', g.status())
        self.assertIn('CLEAR signed in through the browser (was: session of acct-1)', self.calls())

    def test_hit_triples_the_gap_where_auth_expired_does_not(self):  # the positive control
        g = self.gate(start=10)
        g.hit('a', 'HTTP 429')
        self.assertEqual(g.status()['gap_s'], 30)

    def test_a_per_hour_cap_refuses_the_next_request_in_the_hour(self):
        g = self.gate(start=1, floor=1, per_hour=3, max_wait=120)
        for _ in range(3):
            g.wait('a')
        with self.assertRaises(G.Paused) as cm:
            g.wait('a')
        self.assertIn('3 requests an hour', cm.exception.why)
        self.assertAlmostEqual(cm.exception.left, 3600 - 2, delta=1)
        self.clock.t += 3600
        g.wait('a')
        self.assertEqual(g.status()['limits'], {'per_hour': 3, 'burst': None, 'cooldown_s': 0})

    def test_a_burst_spaces_calls_by_the_cooldown(self):
        g = self.gate(start=1, floor=1, burst=1, cooldown=5)
        g.wait('a')
        t = self.clock.t
        g.wait('b')
        self.assertEqual(self.clock.t, t + 5)  # the Crawl-delay, not the 1 s gap
        g2 = self.gate(start=1, floor=1, burst=2, cooldown=60)
        self.clock.t += 120
        t = self.clock.t
        g2.wait('a'); g2.wait('a'); g2.wait('a')
        self.assertEqual(self.clock.t, t + 60)  # two in a row, the third waits out the window

    def test_eta(self):
        g = self.gate(start=10)
        self.assertEqual(g.eta(0), 0)
        self.assertEqual(g.eta(4), 30)
        self.assertEqual(self.gate(start=10, burst=1, cooldown=120).eta(4), 360)
        g.hit('a', 'x')
        self.assertEqual(g.eta(1), 1200)
        g.human('a', 'captcha')
        self.assertIsNone(g.eta(1))

    def test_eta_counts_a_spent_per_hour_cap(self):
        g = self.gate(start=1, floor=1, per_hour=3, max_wait=120)
        for _ in range(3):
            g.wait('a')
        self.assertAlmostEqual(g.eta(1), 3600 - 2, delta=1)  # the next call waits out the hour, not the gap
        self.assertEqual(len(g._locked(dict)['recent']), 3)  # eta reads the history, it never saves a pruned one

    def test_challenge_names_the_vendor(self):
        def page(name):
            with open(os.path.join(FIXTURES, name), 'rb') as f:
                return f.read()
        self.assertEqual(G.challenge(page('imperva.html')), 'imperva')
        self.assertEqual(G.challenge(page('imperva_es.html').decode()), 'hcaptcha')
        self.assertEqual(G.challenge('', 'https://validate.perfdrive.com/?ssa=1'), 'perfdrive')
        self.assertEqual(G.challenge('<script src="/cdn-cgi/challenge-platform/h/b/orchestrate"></script>'),
                         'cloudflare')
        self.assertEqual(G.challenge('<title>Just a moment...</title>'), 'cloudflare')
        self.assertEqual(G.challenge('<div class="g-recaptcha" data-sitekey="x"></div>'), 'recaptcha')
        self.assertIsNone(G.challenge('<p>Fill in the captcha field of our contact form.</p>'))
        self.assertIsNone(G.challenge(b''))

    def test_the_shipped_profiles_load_and_cite_a_source(self):
        rows = G.load_profiles()
        self.assertGreater(len(rows), 0)
        for host, r in rows.items():
            self.assertRegex(r['notes'], r'https://\S+', host)
            self.assertTrue(r['per_hour'] or r['burst'], host)

    def test_a_bad_profiles_file_is_refused(self):
        bad = os.path.join(self.tmp.name, 'p.tsv')
        for text in ('host\tper_hour\n', 'host\tper_hour\tburst\tcooldown_s\tfresh_profile\tnotes\nx\t\t2\t\tno\tn\n',
                     'host\tper_hour\tburst\tcooldown_s\tfresh_profile\tnotes\nx\tmany\t\t\tno\tn\n',
                     'host\tper_hour\tburst\tcooldown_s\tfresh_profile\tnotes\nx\t-3\t\t\tno\tn\n'):
            with open(bad, 'w') as f:
                f.write(text)
            with self.assertRaises(ValueError):
                G.load_profiles(bad)


class CliHumanProfileTest(unittest.TestCase):
    setUp, tearDown, cli = CliTest.setUp, CliTest.tearDown, CliTest.cli

    def test_profile_flags_and_errors(self):
        prof = os.path.join(self.tmp.name, 'p.tsv')
        with open(prof, 'w') as f:
            f.write('# invented\nhost\tper_hour\tburst\tcooldown_s\tfresh_profile\tnotes\n'
                    'records.example.org\t2\t1\t5\tyes\tinvented https://example.org/robots.txt\n')
        code, out, _ = self.cli('--profiles', prof, '--profile', 'https://records.example.org/search', 'status')
        self.assertEqual(json.loads(out.splitlines()[0])['limits'], {'per_hour': 2, 'burst': 1, 'cooldown_s': 5.0})
        code, out, _ = self.cli('--profiles', prof, '--profile', 'records.example.org', '--per-hour', '9', 'status')
        self.assertEqual(json.loads(out.splitlines()[0])['limits']['per_hour'], 9)  # a flag beats the profile
        code, _, err = self.cli('--profiles', prof, '--profile', 'other.example.org', 'status')
        self.assertEqual(code, 2)
        self.assertIn('no profile for other.example.org', err)
        self.assertEqual(self.cli('--profile', 'www.loc.gov', 'status')[0], 0)  # the shipped file
        for flags in (('--per-hour', '0'), ('--burst', '-1', '--cooldown', '5'), ('--cooldown', '-5')):
            code, _, err = self.cli(*flags, 'wait', 'a')
            self.assertEqual(code, 2, flags)  # never a traceback, never a silent "no limit"
            self.assertIn('must be', err)

    def test_auth_expired_and_eta(self):
        session = os.path.join(self.tmp.name, 'tok')
        open(session, 'w').close()
        self.assertEqual(self.cli('auth-expired', 'a', '--account', 'x', '--session', session)[0], 4)
        self.assertEqual(self.cli('wait', 'a', '--account', 'x')[0], 3)
        self.assertEqual(self.cli('wait', 'b')[0], 0)
        code, out, _ = self.cli('eta', '3')
        self.assertEqual(json.loads(out), {'requests': 3, 'eta_s': 45, 'parked': False})

    def test_smoke_a_fake_reader_meets_a_captcha(self):
        """A fake reader saves an answer, finds the wall, parks the gate; a person solves it; work resumes."""
        out = os.path.join(self.tmp.name, 'notices.txt')
        script = os.path.join(self.tmp.name, 'note.py')
        with open(script, 'w') as f:
            f.write(NOTE)
        page = os.path.join(FIXTURES, 'imperva.html')
        exe = [sys.executable, '-I', os.path.join(HERE, 'gwt-gate'), '--dir', self.tmp.name,
               '--notify', f'{sys.executable} -I {script} {out} {{site}} {{what}}']
        run = lambda *a: subprocess.run(exe + list(a), capture_output=True, text=True)
        self.assertEqual(run('wait', 'reader').returncode, 0)
        r = run('challenge', page)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, 'imperva'))
        self.assertEqual(run('human', 'reader', r.stdout.strip(), '--url', 'https://example.org/').returncode, 0)
        r = run('wait', 'other')
        self.assertEqual(r.returncode, 3)
        self.assertIn('until it is cleared', r.stderr)
        self.assertEqual(run('wait-clear', '--probe', f'{sys.executable} -I -c pass').returncode, 0)
        self.assertNotIn('human', json.loads(run('status').stdout.splitlines()[0]))
        self.assertIn('CLEAR probe passed', run('status').stdout)
        with open(out) as f:
            self.assertEqual(f.read().count('\n'), 1)
        self.assertEqual(run('challenge', os.path.join(FIXTURES, 'catalog.html')).returncode, 1)


if __name__ == '__main__':
    unittest.main()
