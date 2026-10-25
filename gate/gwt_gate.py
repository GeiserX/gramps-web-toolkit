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

As a module:

    from gwt_gate import Gate, Paused, reread_token
    gate = Gate('~/.cache/gwt-gate', priority={'urgent'})
    gate.wait('my-lane')                       # raises Paused instead of exiting 3
    token = reread_token(token_file, token)    # after a 401: the new token if the file changed, else None

Settings come from flags, then GWT_GATE_* variables, then the defaults below. Standard library only.
"""
import argparse, fcntl, json, os, sys, time

# Defaults that kept one FamilySearch IP unblocked in 2026: a block came after a few hundred calls at one
# every 5-6 s, so the start and the floor sit well below that pace.
DEFAULTS = {'start': 15.0, 'floor': 8.0, 'ceil': 60.0, 'step_after': 3600.0, 'pause': 1200.0,
            'share': 3, 'max_wait': 120.0}
EXIT_PAUSED, EXIT_DEAD = 3, 4


class Paused(Exception):
    """The pause has more than max_wait seconds left."""

    def __init__(self, left, why):
        super().__init__(f'paused for {round(left / 60)} more minutes: {why}')
        self.left, self.why = left, why


class Gate:
    def __init__(self, root, *, priority=(), start=DEFAULTS['start'], floor=DEFAULTS['floor'],
                 ceil=DEFAULTS['ceil'], step_after=DEFAULTS['step_after'], pause=DEFAULTS['pause'],
                 share=DEFAULTS['share'], max_wait=DEFAULTS['max_wait'], clock=time.time, sleep=time.sleep):
        self.root = os.path.expanduser(root)
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        self.state, self.log_path, self.lock = (os.path.join(self.root, n) for n in ('state.json', 'calls.log', 'lock'))
        self.priority, self.share = set(priority), share
        self.start, self.floor, self.ceil, self.step_after, self.pause, self.max_wait = \
            start, floor, ceil, step_after, pause, max_wait
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

    def wait(self, lane):
        """Return when this lane may make one request. Raises Paused when the pause has long to run."""
        prio = lane in self.priority
        while True:
            def take(s):
                now = self.clock()
                if prio:  # the claim: other lanes hold back while a priority lane waits or parses an answer
                    s['prio_until'] = now + 1.5 * s['gap']
                if s.get('why') and s['pause_until'] <= now:  # the pause is over, so is its reason
                    s['why'] = ''
                if now - max(s['last_hit'], s['last_step']) >= self.step_after and s['gap'] > self.floor:
                    s['gap'] = max(self.floor, round(s['gap'] * 0.8, 2)); s['last_step'] = now
                    self.log(f'pace gap={s["gap"]}s ({round(self.step_after / 60)} min without a hit)')
                if s['pause_until'] - now > self.max_wait:
                    return -1
                ready = max(s['pause_until'], s['last'] + s['gap'])
                if not prio and s.get('prio_until', 0) > now and s.get('prio_run', 0) < self.share:
                    ready = max(ready, s['prio_until'])
                elif now >= ready:
                    s['last'] = now
                    self.log(f'call {lane} gap={round(s["gap"], 2)}')
                    if prio:
                        s['prio_until'] = now + 1.5 * s['gap']; s['prio_run'] = s.get('prio_run', 0) + 1
                    else:
                        s['prio_run'] = 0
                    return 0
                return ready - now
            left = self._locked(take)
            if left == -1:
                s = self._locked(dict)
                raise Paused(s['pause_until'] - self.clock(), s.get('why', ''))
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
            s['pause_until'] = max(s['pause_until'], self.clock() + self.pause); s['why'] = what
            self.log(f'DEAD {lane} {what} -> pause {round(self.pause / 60)} min, gap={round(s["gap"], 2)}s')
        self._locked(h)

    def clear(self, reason):
        """End a pause or a block now."""
        def h(s):
            was = s.get('why') or '?'
            s['pause_until'] = min(s['pause_until'], self.clock()); s['why'] = ''
            self.log(f'CLEAR {reason} (was: {was})')
        self._locked(h)

    def block(self, hours, why):
        def h(s):
            s['pause_until'] = self.clock() + hours * 3600; s['why'] = why; s['last_hit'] = self.clock()
            self.log(f'BLOCK {hours} h: {why}')
        self._locked(h)

    def status(self):
        s = self._locked(dict)
        left = max(0, round(s['pause_until'] - self.clock()))
        return {'gap_s': s['gap'], 'paused_for_s': left, 'why': s.get('why', '') if left else '',
                'last_hit': s.get('hit', '')}

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
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('wait').add_argument('lane')
    for verb in ('hit', 'denied', 'dead'):
        sp = sub.add_parser(verb)
        sp.add_argument('lane'); sp.add_argument('what', nargs='*')
    sp = sub.add_parser('block')
    sp.add_argument('hours', type=float); sp.add_argument('why', nargs='*')
    sub.add_parser('clear').add_argument('reason', nargs='+')
    sub.add_parser('status')
    a = p.parse_args(argv)
    gate = Gate(a.dir, priority={x.strip() for x in a.priority.split(',') if x.strip()}, start=a.start,
                floor=a.floor, ceil=a.ceil, step_after=a.step_after, pause=a.pause, share=a.share,
                max_wait=a.max_wait)
    if a.cmd == 'wait':
        try:
            gate.wait(a.lane)
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
    else:
        print(json.dumps(gate.status()))
        print(''.join(gate.last_calls()), end='')
    return 0


if __name__ == '__main__':
    sys.exit(main())
