#!/usr/bin/env python3
"""gwt-gate: one shared request pace for every process on a machine that calls a rate-limited API.

Each lane (an agent, a script) asks the gate before every request. The gate hands out one slot at a time,
for all lanes together, and looks for the limit on its own: it starts at one request every START seconds,
shortens the gap by a fifth after every STEP_AFTER seconds without a hit (never below FLOOR), and on a hit
(a 429, a captcha, a block page) pauses every lane for PAUSE seconds and triples the gap (never above CEIL).
Priority lanes go first: a priority lane claims the next slot by waiting, and other lanes still get one
slot in SHARE+1 during a long priority run. Every call, hit and pace change goes to calls.log.

As a CLI (the state lives in --dir, so every process pointed at the same dir shares the pace):

    gwt-gate wait <lane>            block until this lane may make ONE request; exit 3 at once when the
                                    pause has more than --max-wait seconds left (do other work instead)
    gwt-gate hit <lane> <what>      a 429, a captcha, a block notice: every lane pauses, the gap triples
    gwt-gate denied <lane> <what>   a 403 permission refusal: logged, no pause, the gap stays
    gwt-gate dead <lane> <what>     a 401, the session is dead: every lane pauses, the gap stays, exit 4
    gwt-gate block <hours> <why>    stop every lane for that long (an IP or account block)
    gwt-gate clear <reason>         end a pause or a block now
    gwt-gate status                 the pace, the pause and its reason, the last hit and the last calls
    gwt-gate human <lane> <what> --url U   a captcha only a person can solve: every lane parks until it is
                                    cleared, the gap triples, and the --notify command runs once
    gwt-gate wait-clear --probe CMD poll a control command until it passes, then end the parking; one
                                    reminder after --timeout seconds, exit 5 after a second timeout
    gwt-gate auth-expired <lane> --account A --session FILE   a 401 for one account: only lanes that wait
                                    with --account A park, the gap stays; they resume when FILE changes
    gwt-gate eta <n>                seconds n more requests take at the current pace, caps and pause
    gwt-gate challenge [FILE]       name the bot wall (perfdrive, imperva, cloudflare, hcaptcha, recaptcha)
                                    in a saved answer; exit 0 when one is found, 1 when none, 2 when
                                    the file cannot be read

As a module:

    from gwt_gate import Gate, Paused, reread_token
    gate = Gate('~/.cache/gwt-gate', priority={'urgent'})
    gate.wait('my-lane')                       # raises Paused instead of exiting 3
    token = reread_token(token_file, token)    # after a 401: the new token if the file changed, else None
    challenge(body, url)                       # 'imperva', 'cloudflare', ... or None

Per-host limits a site publishes (robots.txt Crawl-delay, an API's stated rate) live in profiles.tsv:
--profile HOST applies one, so no lane goes faster than the site asks.

Settings come from flags, then GWT_GATE_* variables, then the defaults below. Standard library only.
"""
import argparse, fcntl, json, os, shlex, subprocess, sys, time, urllib.parse

# Defaults that kept one FamilySearch IP unblocked in 2026: a block came after a few hundred calls at one
# every 5-6 s, so the start and the floor sit well below that pace.
DEFAULTS = {'start': 15.0, 'floor': 8.0, 'ceil': 60.0, 'step_after': 3600.0, 'pause': 1200.0,
            'share': 3, 'max_wait': 120.0}
EXIT_PAUSED, EXIT_DEAD, EXIT_STILL_PARKED = 3, 4, 5
PARKED = float('inf')  # Paused.left while a person or a new session is needed: no timer ends it


class Paused(Exception):
    """The pause has more than max_wait seconds left."""

    def __init__(self, left, why):
        when = 'until it is cleared' if left == PARKED else f'for {round(left / 60)} more minutes'
        super().__init__(f'paused {when}: {why}')
        self.left, self.why = left, why


class Gate:
    def __init__(self, root, *, priority=(), start=DEFAULTS['start'], floor=DEFAULTS['floor'],
                 ceil=DEFAULTS['ceil'], step_after=DEFAULTS['step_after'], pause=DEFAULTS['pause'],
                 share=DEFAULTS['share'], max_wait=DEFAULTS['max_wait'], per_hour=None, burst=None,
                 cooldown=None, notify=None, clock=time.time, sleep=time.sleep):
        self.root = os.path.expanduser(root)
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        self.state, self.log_path, self.lock = (os.path.join(self.root, n) for n in ('state.json', 'calls.log', 'lock'))
        self.priority, self.share = set(priority), share
        self.start, self.floor, self.ceil, self.step_after, self.pause, self.max_wait = \
            start, floor, ceil, step_after, pause, max_wait
        # A site's own limits: at most per_hour calls in any hour, at most burst calls in any cooldown seconds.
        self.per_hour, self.burst, self.cooldown = per_hour or None, burst or None, cooldown or 0
        self.notify_cmd = notify  # a command template run once per parking: {site} {lane} {url} {what}
        self.notice = None  # what the last notice returned: 0, the command's exit code or the error
        self.site = os.path.basename(self.root.rstrip(os.sep))
        self.clock, self.sleep = clock, sleep

    def _locked(self, fn):
        """Run fn(state) under the lock and save what it left in state."""
        with open(self.lock, 'w') as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                with open(self.state) as f:
                    s = json.load(f)
            except (OSError, ValueError):
                s = {}
            s.setdefault('gap', self.start); s.setdefault('last', 0.0); s.setdefault('pause_until', 0.0)
            s.setdefault('last_hit', 0.0); s.setdefault('last_step', self.clock())
            out = fn(s)
            with open(self.state + '.tmp', 'w') as f:
                json.dump(s, f)
            os.replace(self.state + '.tmp', self.state)
            return out

    def log(self, line):
        with open(self.log_path, 'a') as f:
            f.write(time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(self.clock())) + ' ' + line + '\n')

    def _limit_ready(self, s, now):
        """When the site's own limits (per_hour, burst) next allow a call; 0 when they do not apply."""
        if not (self.per_hour or self.burst):
            return 0, ''
        keep = max(3600.0, self.cooldown) if self.per_hour else self.cooldown  # a burst needs its window only
        s['recent'] = recent = [t for t in s.get('recent', []) if now - t < keep]
        ready, why = 0, ''
        if self.per_hour and len(recent) >= self.per_hour:
            ready, why = recent[-self.per_hour] + 3600, f'the site allows {self.per_hour} requests an hour'
        if self.burst and len(recent) >= self.burst and recent[-self.burst] + self.cooldown > ready:
            ready, why = recent[-self.burst] + self.cooldown, \
                f'the site allows {self.burst} requests every {round(self.cooldown)} s'
        return ready, why

    def wait(self, lane, account=None):
        """Return when this lane may make one request. Raises Paused when the pause has long to run, while a
        person must solve a captcha (left is PARKED), or while `account`'s session has expired."""
        prio = lane in self.priority
        while True:
            def take(s):
                now = self.clock()
                parked = s.get('human')
                if parked:
                    return ('paused', PARKED, f'a person must solve {parked["what"]} at {parked["url"] or "?"} '
                                              f'(met by {parked["lane"]}); then run wait-clear or clear')
                auth = s.get('auth', {}).get(account) if account else None
                if auth:
                    if _mtime(auth['session']) != auth['mtime']:  # someone signed in again: resume
                        del s['auth'][account]
                        self.log(f'AUTH {account} session renewed ({auth["session"]})')
                    else:
                        return ('paused', PARKED, f'the session of account {account} expired: {auth["why"]}; '
                                                  f'sign in again so {auth["session"]} changes')
                if prio:  # the claim: other lanes hold back while a priority lane waits or parses an answer
                    s['prio_until'] = now + 1.5 * s['gap']
                if s.get('why') and s['pause_until'] <= now:  # the pause is over, so is its reason
                    s['why'] = ''
                if now - max(s['last_hit'], s['last_step']) >= self.step_after and s['gap'] > self.floor:
                    s['gap'] = max(self.floor, round(s['gap'] * 0.8, 2)); s['last_step'] = now
                    self.log(f'pace gap={s["gap"]}s ({round(self.step_after / 60)} min without a hit)')
                if s['pause_until'] - now > self.max_wait:
                    return ('paused', s['pause_until'] - now, s.get('why', ''))
                cap, cap_why = self._limit_ready(s, now)
                if cap - now > self.max_wait:
                    return ('paused', cap - now, cap_why)
                ready = max(s['pause_until'], s['last'] + s['gap'], cap)
                if not prio and s.get('prio_until', 0) > now and s.get('prio_run', 0) < self.share:
                    ready = max(ready, s['prio_until'])
                elif now >= ready:
                    s['last'] = now
                    if self.per_hour or self.burst:
                        s['recent'].append(now)
                    self.log(f'call {lane} gap={round(s["gap"], 2)}')
                    if prio:
                        s['prio_until'] = now + 1.5 * s['gap']; s['prio_run'] = s.get('prio_run', 0) + 1
                    else:
                        s['prio_run'] = 0
                    return 0
                return ready - now
            left = self._locked(take)
            if isinstance(left, tuple):
                raise Paused(left[1], left[2])
            if not left:
                return
            self.sleep(min(left, 30))

    def hit(self, lane, what):
        """A rate answer (a 429, a captcha, a block page): every lane pauses and the gap triples."""
        def h(s):
            now = self.clock()
            s['gap'] = min(self.ceil, s['gap'] * 3); s['pause_until'] = now + self.pause; s['last_hit'] = now
            s['why'] = what
            s['hit'] = time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(now)) + f' {lane} {what}'
            self.log(f'HIT {lane} {what} -> pause {round(self.pause / 60)} min, gap={round(s["gap"], 2)}s')
        self._locked(h)

    def denied(self, lane, what):
        """A permission refusal (a 403 on one record): says nothing about the rate, so only log it."""
        self._locked(lambda s: self.log(f'DENIED {lane} {what}'))

    def dead(self, lane, what):
        """A dead session (a 401): every lane pauses, but the gap stays, since it says nothing about the rate."""
        def h(s):
            now = self.clock()
            if now + self.pause > s['pause_until']:  # never shorten, or relabel, a longer pause or block
                s['pause_until'] = now + self.pause; s['why'] = what
                self.log(f'DEAD {lane} {what} -> pause {round(self.pause / 60)} min, gap={round(s["gap"], 2)}s')
            else:
                self.log(f'DEAD {lane} {what} -> pause kept ({s.get("why") or "?"}), gap={round(s["gap"], 2)}s')
        self._locked(h)

    def clear(self, reason, human_only=False):
        """End a pause, a block, a parking for a person and every expired account now. human_only ends the parking alone: a probe that
        passes shows the site answers, not that a block (an account suspended) was lifted."""
        def h(s):
            was = s.get('why') or ('a person: ' + s['human']['what'] if s.get('human') else '?')
            if human_only:
                was = 'a person: ' + s['human']['what'] if s.get('human') else '?'
            else:
                s['pause_until'] = min(s['pause_until'], self.clock()); s['why'] = ''
                if s.get('auth'):  # a sign-in that never rewrites the watched file has no other way out
                    was = '; '.join([was] * (was != '?') + [f'session of {a}' for a in sorted(s['auth'])])
                    s['auth'] = {}
            s['human'] = None
            self.log(f'CLEAR {reason} (was: {was})')
        self._locked(h)

    def human(self, lane, what, url=''):
        """A captcha or check only a person can pass: every lane parks until it is cleared, the gap triples as on
        a hit, and the notify command runs. Once per parking: a second report while parked only logs a line.
        Returns True when this call started the parking."""
        def h(s):
            now = self.clock()
            already = s.get('human')
            if already:
                self.log(f'HUMAN {lane} {what} -> already parked since {already["at"]}, no new notice')
                return False
            s['gap'] = min(self.ceil, s['gap'] * 3); s['last_hit'] = now
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(now))
            s['human'] = {'lane': lane, 'what': what, 'url': url, 'at': stamp}
            s['hit'] = f'{stamp} {lane} {what} (needs a person)'
            self.log(f'HUMAN {lane} {what} {url} -> parked until cleared, gap={round(s["gap"], 2)}s')
            return True
        first = self._locked(h)
        if first:
            self.notice = self.notify(lane, what, url)
        return first

    def notify(self, lane, what, url, again=False):
        """Run the notify command template. Each word is filled in on its own and the command runs without a
        shell, so a {what} or {url} the site wrote can never become a second command."""
        if not self.notify_cmd:
            return None
        fields = {'site': self.site, 'lane': lane, 'what': ('still waiting: ' if again else '') + what, 'url': url}
        try:
            argv = [w.format(**fields) for w in shlex.split(self.notify_cmd)]
            rc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, timeout=120).returncode
        except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as exc:
            rc = f'{type(exc).__name__}: {exc}'
        self._locked(lambda s: self.log(f'NOTIFY {"again " if again else ""}{lane} exit={rc}'))
        return rc

    def wait_clear(self, probe, every=60.0, timeout=3600.0):
        """Run the probe command (no shell) every `every` seconds while the gate is parked for a person; when it
        exits 0, end the parking. After `timeout` seconds the notify command runs once more; after a second
        `timeout`, give up with the gate still parked. Returns True when the gate is no longer parked. Raises
        OSError at once when the probe cannot run at all (not found, not executable)."""
        argv, start, reminded = shlex.split(probe), self.clock(), False
        while True:
            h = self._locked(lambda s: s.get('human'))
            if not h:
                return True
            try:
                rc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=max(every, 30)).returncode
            except OSError as exc:
                self._locked(lambda s: self.log(f'WAIT-CLEAR the probe cannot run: {exc}, still parked'))
                raise  # no retry can fix a probe that does not exist
            except subprocess.SubprocessError:
                rc = None
            if rc == 0:
                self.clear(f'probe passed: {probe}', human_only=True)
                return True
            if self.clock() - start >= timeout:
                if reminded:
                    self._locked(lambda s: self.log(f'WAIT-CLEAR gave up after {round(2 * timeout)} s, still parked'))
                    return False
                reminded, start = True, self.clock()
                self.notify(h['lane'], h['what'], h['url'], again=True)
            self.sleep(every)

    def auth_expired(self, lane, account, session, what):
        """A 401 for one account: only lanes that wait with that account park, the gap and the pause stay. They
        resume on their own when the session file changes (someone signed in again)."""
        def h(s):
            path = os.path.abspath(os.path.expanduser(session))  # a lane in another directory sees the same file
            s.setdefault('auth', {})[account] = {'session': path, 'mtime': _mtime(path), 'why': what}
            self.log(f'AUTH-EXPIRED {lane} {account} {what} -> account parked until {path} changes, '
                     f'gap={round(s["gap"], 2)}s')
        self._locked(h)

    def eta(self, n):
        """Seconds n more requests take at the current pace, the site's limits and the pause; None while
        parked for a person."""
        s = self._locked(dict)
        if s.get('human'):
            return None
        now = self.clock()
        cap, _ = self._limit_ready(s, now)  # s is a copy: pruning its history saves nothing
        pace = max([s['gap']] + ([3600.0 / self.per_hour] if self.per_hour else [])
                   + ([self.cooldown / self.burst] if self.burst else []))
        first = max(s['pause_until'], s['last'] + s['gap'], cap, now) - now
        return round(first + max(0, n - 1) * pace) if n > 0 else 0

    def block(self, hours, why):
        def h(s):
            s['pause_until'] = self.clock() + hours * 3600; s['why'] = why; s['last_hit'] = self.clock()
            self.log(f'BLOCK {hours} h: {why}')
        self._locked(h)

    def status(self):
        s = self._locked(dict)
        left = max(0, round(s['pause_until'] - self.clock()))
        out = {'gap_s': s['gap'], 'paused_for_s': left, 'why': s.get('why', '') if left else '',
               'last_hit': s.get('hit', '')}
        if s.get('human'):
            out['human'] = s['human']
        if s.get('auth'):
            out['auth_expired'] = sorted(s['auth'])
        if self.per_hour or self.burst:
            out['limits'] = {'per_hour': self.per_hour, 'burst': self.burst, 'cooldown_s': self.cooldown}
        return out

    def last_calls(self, n=10):
        try:
            with open(self.log_path) as f:
                return f.readlines()[-n:]
        except OSError:
            return []


def reread_token(path, used):
    """After a 401: the token now in `path` if it differs from the one the request used, else None.

    A long-running worker reads its token once; when the session file is refreshed under it, it should
    retry once with the new token. None means the session is dead: report a hit and stop (exit 4),
    never hand the caller an empty answer as if it were the result."""
    try:
        with open(os.path.expanduser(path)) as f:
            fresh = f.read().strip()
    except OSError:
        return None
    return fresh if fresh and fresh != used else None


def _mtime(path):
    try:
        return os.stat(os.path.expanduser(path)).st_mtime_ns
    except OSError:
        return None


# The bot walls a reader meets, by vendor. Matched case-insensitively on the answer's first 64 KB and its URL.
# The first vendor that matches wins, so a wall that embeds another vendor's captcha keeps its own name.
CHALLENGES = (
    ('perfdrive', ('validate.perfdrive.com', 'perfdrive.com/', 'shieldsquare')),
    ('imperva', ('_incapsula_resource', 'incapsula incident')),
    # not /cdn-cgi/challenge-platform: Cloudflare loads that script on ordinary pages too
    ('cloudflare', ('cf-chl', 'cf_chl_opt', '<title>just a moment...</title>')),
    ('hcaptcha', ('hcaptcha.com', 'class="h-captcha"')),
    ('recaptcha', ('google.com/recaptcha', 'class="g-recaptcha"')),
)


def challenge(body, url=''):
    """The vendor of the bot wall in an answer ('perfdrive', 'imperva', 'cloudflare', 'hcaptcha', 'recaptcha'),
    or None. A page that only mentions the word captcha is not a wall."""
    if isinstance(body, bytes):
        body = body[:65536].decode('utf-8', 'replace')
    text = ((url or '') + '\n' + (body or '')[:65536]).lower()
    for vendor, marks in CHALLENGES:
        if any(m in text for m in marks):
            return vendor
    return None


PROFILE_COLUMNS = ('host', 'per_hour', 'burst', 'cooldown_s', 'fresh_profile', 'notes')


def default_profiles():
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ('profiles.tsv', 'gwt_gate_profiles.tsv'):  # the source tree, then the installed wheel
        if os.path.exists(os.path.join(here, name)):
            return os.path.join(here, name)
    return os.path.join(here, 'profiles.tsv')


def load_profiles(path=None):
    """host -> {per_hour, burst, cooldown_s, fresh_profile, notes} from a tab-separated file. Lines starting
    with # are comments; the first other line is the header. An empty number means no limit."""
    rows, header = {}, None
    with open(os.path.expanduser(path or default_profiles()), encoding='utf-8') as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip('\n')
            if not line.strip() or line.startswith('#'):
                continue
            cells = line.split('\t')
            if header is None:
                if tuple(cells) != PROFILE_COLUMNS:
                    raise ValueError(f'{path}:{n}: the header must be {" ".join(PROFILE_COLUMNS)}')
                header = cells
                continue
            if len(cells) != len(header):
                raise ValueError(f'{path}:{n}: {len(cells)} columns, the header has {len(header)}')
            r = dict(zip(header, cells))
            try:
                rows[r['host'].lower()] = {
                    'per_hour': int(r['per_hour']) if r['per_hour'] else None,
                    'burst': int(r['burst']) if r['burst'] else None,
                    'cooldown_s': float(r['cooldown_s']) if r['cooldown_s'] else 0.0,
                    'fresh_profile': r['fresh_profile'].strip().lower() in ('yes', 'true', '1'),
                    'notes': r['notes']}
            except ValueError:
                raise ValueError(f'{path}:{n}: per_hour and burst must be whole numbers, cooldown_s a number')
            if any(v is not None and v < 1 for v in (rows[r['host'].lower()]['per_hour'],
                                                      rows[r['host'].lower()]['burst'])):
                raise ValueError(f'{path}:{n}: per_hour and burst must be 1 or more; leave the cell empty for none')
            if rows[r['host'].lower()]['burst'] and not rows[r['host'].lower()]['cooldown_s']:
                raise ValueError(f'{path}:{n}: a burst needs a cooldown_s')
    return rows


def _env(name, cast):
    v = os.environ.get('GWT_GATE_' + name.upper())
    return cast(v) if v not in (None, '') else DEFAULTS.get(name)


def main(argv=None):
    p = argparse.ArgumentParser(prog='gwt-gate', description=__doc__.split('\n')[0])
    p.add_argument('--dir', default=os.environ.get('GWT_GATE_DIR', '~/.cache/gwt-gate'), help='shared state dir')
    p.add_argument('--priority', default=os.environ.get('GWT_GATE_PRIORITY', ''),
                   help='comma-separated lanes that go first')
    for name, cast in (('start', float), ('floor', float), ('ceil', float), ('step_after', float),
                       ('pause', float), ('share', int), ('max_wait', float)):
        p.add_argument('--' + name.replace('_', '-'), type=cast, default=_env(name, cast))
    p.add_argument('--profile', default=os.environ.get('GWT_GATE_PROFILE', ''),
                   help='apply the limits of this host from the profiles file')
    p.add_argument('--profiles', default=os.environ.get('GWT_GATE_PROFILES', ''),
                   help='the profiles file (default: profiles.tsv shipped with the gate)')
    for name, cast in (('per_hour', int), ('burst', int), ('cooldown', float)):
        p.add_argument('--' + name.replace('_', '-'), type=cast, default=_env(name, cast),
                       help='beats the profile')
    p.add_argument('--notify', default=os.environ.get('GWT_GATE_NOTIFY', ''),
                   help='command run once per parking for a person; {site} {lane} {url} {what} are filled in')
    sub = p.add_subparsers(dest='cmd', required=True)
    sp = sub.add_parser('wait')
    sp.add_argument('lane'); sp.add_argument('--account', help='park while this account\'s session is expired')
    for verb in ('hit', 'denied', 'dead'):
        sp = sub.add_parser(verb)
        sp.add_argument('lane'); sp.add_argument('what', nargs='*')
    sp = sub.add_parser('block')
    sp.add_argument('hours', type=float); sp.add_argument('why', nargs='*')
    sub.add_parser('clear').add_argument('reason', nargs='+')
    sub.add_parser('status')
    sp = sub.add_parser('human')
    sp.add_argument('lane'); sp.add_argument('what', nargs='*'); sp.add_argument('--url', default='')
    sp = sub.add_parser('wait-clear')
    sp.add_argument('--probe', required=True, help='a command that exits 0 once the site answers normally')
    sp.add_argument('--every', type=float, default=60.0); sp.add_argument('--timeout', type=float, default=3600.0)
    sp = sub.add_parser('auth-expired')
    sp.add_argument('lane'); sp.add_argument('what', nargs='*')
    sp.add_argument('--account', required=True); sp.add_argument('--session', required=True,
                                                                 help='the session or token file a sign-in rewrites')
    sub.add_parser('eta').add_argument('n', type=int, help='how many requests are still to make')
    sub.add_parser('challenge').add_argument('file', nargs='?', default='-', help='a saved answer; - for stdin')
    a = p.parse_args(argv)
    fields = dict.fromkeys(('site', 'lane', 'url', 'what'), '')
    try:  # a template that cannot be filled fails now, not silently at the first captcha
        if a.cmd in ('human', 'wait-clear') and a.notify:
            [w.format(**fields) for w in shlex.split(a.notify)]
        if a.cmd == 'wait-clear':
            shlex.split(a.probe)
    except (ValueError, KeyError, IndexError) as exc:
        print(f'gwt-gate: cannot use the --notify or --probe command ({type(exc).__name__}: {exc}); '
              'close every quote, and write a literal brace as {{ or }}', file=sys.stderr)
        return 2
    if a.cmd == 'challenge':
        try:
            body = sys.stdin.buffer.read() if a.file == '-' else open(a.file, 'rb').read()
        except OSError as exc:  # exit 1 means "no wall", so a page that cannot be read must not end that way
            print(f'gwt-gate: {exc}', file=sys.stderr)
            return 2
        vendor = challenge(body)
        print(vendor or 'none')
        return 0 if vendor else 1
    limits = {'per_hour': None, 'burst': None, 'cooldown_s': 0.0}
    if a.profile:
        host = urllib.parse.urlsplit(a.profile if '://' in a.profile else '//' + a.profile).hostname or ''
        try:
            profiles = load_profiles(a.profiles or None)
        except (OSError, ValueError) as exc:
            print(f'gwt-gate: {exc}', file=sys.stderr)
            return 2
        if host not in profiles:
            print(f'gwt-gate: no profile for {host} in {a.profiles or default_profiles()}; '
                  'add a row, or drop --profile and pass --per-hour/--burst/--cooldown', file=sys.stderr)
            return 2
        limits.update(profiles[host])
    per_hour = a.per_hour if a.per_hour is not None else limits['per_hour']
    burst = a.burst if a.burst is not None else limits['burst']
    cooldown = a.cooldown if a.cooldown is not None else limits['cooldown_s']
    if any(v is not None and v < 1 for v in (per_hour, burst)) or cooldown < 0:
        print('gwt-gate: --per-hour and --burst must be 1 or more, --cooldown 0 or more', file=sys.stderr)
        return 2
    gate = Gate(a.dir, priority={x.strip() for x in a.priority.split(',') if x.strip()}, start=a.start,
                floor=a.floor, ceil=a.ceil, step_after=a.step_after, pause=a.pause, share=a.share,
                max_wait=a.max_wait, per_hour=per_hour, burst=burst, cooldown=cooldown, notify=a.notify or None)
    if a.cmd == 'wait':
        try:
            gate.wait(a.lane, account=a.account)
        except Paused as exc:
            print(f'{exc}; do work that does not need this API', file=sys.stderr)
            return EXIT_PAUSED
    elif a.cmd == 'hit':
        gate.hit(a.lane, ' '.join(a.what) or '?')
    elif a.cmd == 'denied':
        gate.denied(a.lane, 'HTTP 403 ' + ' '.join(a.what))
    elif a.cmd == 'dead':
        gate.dead(a.lane, 'HTTP 401 session dead ' + ' '.join(a.what))
        print('session dead; log in again', file=sys.stderr)
        return EXIT_DEAD
    elif a.cmd == 'block':
        gate.block(a.hours, ' '.join(a.why) or '?')
    elif a.cmd == 'clear':
        gate.clear(' '.join(a.reason))
    elif a.cmd == 'human':
        if not gate.human(a.lane, ' '.join(a.what) or 'a captcha', a.url):
            print('already parked for a person; no new notice', file=sys.stderr)
        elif not gate.notify_cmd:
            print('parked for a person; no --notify command is set, so nobody was told', file=sys.stderr)
        elif gate.notice != 0:
            print(f'parked for a person, but the notice failed ({gate.notice}); nobody was told', file=sys.stderr)
            return 1
    elif a.cmd == 'wait-clear':
        try:
            passed = gate.wait_clear(a.probe, every=a.every, timeout=a.timeout)
        except OSError as exc:
            print(f'gwt-gate: the probe cannot run: {exc}; the gate is still parked', file=sys.stderr)
            return 2
        if not passed:
            print('the probe never passed; the gate is still parked for a person', file=sys.stderr)
            return EXIT_STILL_PARKED
    elif a.cmd == 'auth-expired':
        gate.auth_expired(a.lane, a.account, a.session, 'HTTP 401 ' + ' '.join(a.what))
        print(f'session of {a.account} expired; sign in again', file=sys.stderr)
        return EXIT_DEAD
    elif a.cmd == 'eta':
        e = gate.eta(a.n)
        print(json.dumps({'requests': a.n, 'eta_s': e, 'parked': e is None}))
    else:
        print(json.dumps(gate.status()))
        print(''.join(gate.last_calls()), end='')
    return 0

if __name__ == '__main__':
    sys.exit(main())
