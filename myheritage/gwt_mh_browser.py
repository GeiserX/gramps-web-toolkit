#!/usr/bin/env python3
"""gwt_mh_browser: the only gwt-mh module that drives a browser.

It attaches to a Chrome over CDP (Playwright's connect_over_cdp, no browser download), always in a new
context of its own built from the account's saved login state, so it never touches a context another
tool or agent owns, and reads pages from inside that signed-in session. One session per account at a
time, whatever its gate dir: a second one on the same account stops with Busy (exit 7). Every page load waits for the account's gwt-gate slot plus a random
0-8 s, rests 2-4 min every --rest-every loads, and goes through gwt-fscache, so a page already read is
never loaded twice. It never clicks: it opens URLs and reads what they show.

On a bot wall or lockout, in a page or in an image download, it blocks the account's gate for 24 h and
raises Wall (exit 3); on a login wall, Dead (exit 4); on a paywall, Paywall (exit 5). None of the three is
ever stored in the cache.
"""
import base64, fcntl, json, os, random, sys, time, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
for sub in ('gate', 'fscache'):
    p = os.path.join(HERE, '..', sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from gwt_gate import Gate, Paused  # noqa: E402
from gwt_fscache import FSCache  # noqa: E402
import gwt_mh as mh  # noqa: E402

GRAPHQL = '/web-family-graphql/search_in_historical_records'
STATUS_FOR = {'wall': 403, 'dead': 401, 'paywall': 402}
WALL_HOURS = 24      # no request on the account after a wall; a new login during it can extend the lockout
LOCK_DIR = os.environ.get('GWT_MH_LOCK_DIR', '~/.cache/gwt-mh-locks')


class Stop(Exception):
    exit_code = 1

    def __init__(self, msg, envelope=None):
        super().__init__(msg)
        self.envelope = envelope


class Wall(Stop):
    exit_code = mh.EXIT_WALL


class Dead(Stop):
    exit_code = mh.EXIT_DEAD


class Paywall(Stop):
    exit_code = mh.EXIT_PAYWALL


class Busy(Stop):
    exit_code = mh.EXIT_BUSY


class Session:
    def __init__(self, cdp, account, cache_dir, gate_dir, lane, storage_state=None,
                 rest_every=45, jitter=8.0, refresh=False, log=None, lock_dir=None):
        self.cdp, self.account, self.lane = cdp, account, lane
        self.storage_state = os.path.expanduser(storage_state) if storage_state else None
        self.cache_dir = os.path.expanduser(cache_dir)
        self.gate = Gate(gate_dir, start=17, floor=10, ceil=60)
        self.gate_dir = os.path.expanduser(gate_dir)
        self.lock_path = os.path.join(os.path.expanduser(lock_dir or LOCK_DIR), mh.slug(account) + '.lock')
        self.wall_path = self.lock_path[:-len('.lock')] + '.wall'    # the 24 h block, whatever the gate dir
        self.clock = time.time
        self.cache = FSCache(self.cache_dir, rules=mh.MYHERITAGE_RULES, methods=('GET',), vary=(mh.PLAN_HEADER,))
        self.plan = mh.PlanState(self.cache_dir)
        self.rest_every, self.jitter, self.refresh = rest_every, jitter, refresh
        self.log = log or (lambda m: print(m, file=sys.stderr))
        self._pw = self._browser = self._ctx = self.page = self._lock = None
        self.images = []        # (url, bytes) of image responses during the last load

    # ------------------------------------------------------------ lifecycle

    def acquire(self):
        """Take the account's session lock (<lock dir>/<account>.lock), or raise Busy. It is keyed on the
        account, not the gate dir, so two runs with different gate dirs still cannot share an account. The
        kernel drops the lock when the process dies, so a crashed run never leaves the account locked.
        Raises Paused while the account's wall block (<lock dir>/<account>.wall) has time left."""
        self.check_wall()
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        f = open(self.lock_path, 'a+')
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            raise Busy('another gwt-mh session is open on account %s; one at a time, do other work' % self.account)
        f.seek(0)
        f.truncate()
        f.write('%d %s %s\n' % (os.getpid(), self.lane, mh.now_iso()))
        f.flush()
        self._lock = f

    def check_wall(self):
        try:
            with open(self.wall_path) as f:
                w = json.load(f)
        except (OSError, ValueError):
            return
        left = float(w.get('until', 0)) - self.clock()
        if left > 0:
            raise Paused(left, w.get('why', 'MyHeritage wall on account %s' % self.account))

    def block_account(self, why):
        os.makedirs(os.path.dirname(self.wall_path), exist_ok=True)
        mh.write_json(self.wall_path, {'until': self.clock() + WALL_HOURS * 3600, 'why': why, 'lane': self.lane,
                                       'at': mh.now_iso()})

    def release(self):
        if self._lock:
            fcntl.flock(self._lock, fcntl.LOCK_UN)
            self._lock.close()
            self._lock = None

    def __enter__(self):
        self.acquire()
        try:
            from playwright.sync_api import sync_playwright   # imported here so the pure module never needs it
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.connect_over_cdp(self.cdp)
            kw = {'locale': 'es-ES', 'color_scheme': 'dark', 'viewport': {'width': 1400, 'height': 900}}
            if self.storage_state:
                kw['storage_state'] = self.storage_state
            self._ctx = self._browser.new_context(**kw)
            self.page = self._ctx.new_page()
            self.page.on('response', self._on_response)
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *exc):
        try:
            if self.page:
                self.page.close()
            if self._ctx:
                if self.storage_state:      # keep the account's own refreshed cookies, mode 600, never shared
                    self._ctx.storage_state(path=self.storage_state)
                    os.chmod(self.storage_state, 0o600)
                self._ctx.close()
        finally:
            try:
                if self._pw:
                    self._pw.stop()    # disconnects; never closes the remote Chrome itself
            finally:
                self.release()

    # ------------------------------------------------------------ pace

    def _slot(self, what):
        self.gate.wait(self.lane)
        time.sleep(random.uniform(0, self.jitter))
        path = os.path.join(self.gate_dir, 'mh-loads.json')
        try:
            with open(path) as f:
                n = json.load(f).get('n', 0) + 1
        except (OSError, ValueError):
            n = 1
        mh.write_json(path, {'n': n})
        if self.rest_every and n % self.rest_every == 0:
            pause = random.uniform(120, 240)
            self.log('rest %d s after %d loads' % (pause, n))
            time.sleep(pause)
        self.log('load %s' % what)

    # ------------------------------------------------------------ capture

    def _on_response(self, resp):
        try:
            url = resp.url
            if GRAPHQL in url and resp.request.method == 'POST':
                self._graphql.append({'url': url.split('?')[0], 'body': resp.json()})
            elif resp.request.resource_type == 'image' and resp.ok:
                ctype = resp.headers.get('content-type', '')
                if ctype.startswith('image/'):
                    self.images.append((url, resp.body()))
        except Exception:      # a response that went away while being read is not an error of ours
            pass

    def _load(self, url, wait_graphql=False):
        self._graphql, self.images = [], []
        self._slot(url)
        page = self.page
        page.goto(url, wait_until='domcontentloaded', timeout=90000)
        deadline = time.time() + 25
        while time.time() < deadline:
            if wait_graphql and self._graphql and len(self._graphql) >= 2:
                break
            if not wait_graphql:
                try:
                    page.wait_for_load_state('networkidle', timeout=8000)
                except Exception:
                    pass
                break
            page.wait_for_timeout(1000)
        if wait_graphql:
            page.wait_for_timeout(1500)
        text = page.inner_text('body', timeout=20000) if page.query_selector('body') else ''
        env = {'url': url, 'final_url': page.url, 'text': text, 'html': page.content(),
               'graphql': list(self._graphql), 'captured_at': mh.now_iso(), 'plan': self.plan.label()}
        return mh.scrub(env)

    def capture(self, url, wait_graphql=False, refresh=None):
        """The envelope of one page, from the cache when fresh. Raises Wall, Dead or Paywall."""
        key_url = mh.normalize_url(url)
        headers = {mh.PLAN_HEADER: self.plan.label()}
        state = {}

        def fetcher(method, u, h, data):
            env = self._load(u, wait_graphql)
            st = mh.classify(env['final_url'], env['text'], env['html'])
            state['env'], state['st'] = env, st
            body = json.dumps(env, ensure_ascii=False).encode()
            return (200 if st == 'ok' else STATUS_FOR[st]), {'content-type': 'application/json'}, body

        status, _, body = self.cache.fetch('GET', key_url, headers, None, fetcher,
                                            refresh=self.refresh if refresh is None else refresh)
        env = json.loads(body)
        st = state.get('st', 'ok')
        self._stop(st, key_url, env)
        if st == 'paywall':
            self.plan.add(mh.record_observations(env['final_url'], url=key_url))
            raise Paywall('the plan does not open this page (%s)' % env['final_url'].split('?')[0], env)
        env['from_cache'] = 'env' not in state
        return env

    def _stop(self, st, url, env=None):
        """Raise Wall (after blocking the gate for 24 h) or Dead for a wall or dead answer; else return."""
        if st == 'wall':
            why = 'MyHeritage bot wall or lockout at %s; no request for %d h, never log in again' % (url, WALL_HOURS)
            self.gate.hit(self.lane, 'MyHeritage bot wall at ' + url)
            self.gate.block(WALL_HOURS, why)
            self.block_account(why)
            raise Wall('bot wall or account lockout (Imperva, hCaptcha, unusual or irregular activity); gate blocked '
                       'for %d h, nothing clicked; do NOT log in again and let a person look' % WALL_HOURS, env)
        if st == 'dead':
            self.gate.hit(self.lane, 'MyHeritage session dead at ' + url)
            raise Dead('session is logged out; log in again once with the account login tool, '
                       'and never after a wall (exit 3)', env)

    def fetch_bytes(self, url, final_ok=None):
        """(status, content type, bytes) of a URL, fetched from inside the page with the session's cookies.
        An image CDN on another origin refuses a credentialed in-page fetch (CORS), so that case goes
        through the context's own request client, which sends the same cookies; that retry is a request of
        its own and takes its own gate slot. Every answer is read like a page: a wall or lockout raises Wall
        and blocks the gate, a logged-out answer raises Dead. With final_ok, an answer whose final URL after
        redirects fails it is dropped. (0, '', b'') on failure."""
        self._slot(url)
        try:
            got = self._page_fetch(url)
        except Exception as exc:
            self.log('in-page fetch failed (%s); retrying with the context client' % str(exc).split('\n')[0][:80])
            self._slot(url + ' (context client)')
            try:
                r = self._ctx.request.get(url, headers={'referer': self.page.url}, timeout=60000)
                got = r.status, r.headers.get('content-type', ''), r.body(), r.url
            except Exception as exc:
                self.log('fetch failed: %s' % str(exc).split('\n')[0][:120])
                return 0, '', b''
        st, ctype, data, final = got
        self._stop(mh.classify_bytes(final or url, st, ctype, data), mh.normalize_url(url))
        if final_ok and not final_ok(final or url):
            self.log('dropped %s: redirected to host %s, not an allowed MyHeritage host'
                     % (url, urllib.parse.urlsplit(final or '').hostname or '?'))
            return 0, '', b''
        return st, ctype, data

    def _page_fetch(self, url):
        out = self.page.evaluate("""async (u) => {
            const r = await fetch(u, {credentials: 'include'});
            const buf = new Uint8Array(await r.arrayBuffer());
            let s = '';
            for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
            return {status: r.status, type: r.headers.get('content-type') || '', b64: btoa(s), url: r.url};
        }""", url)
        return out['status'], out['type'], base64.b64decode(out['b64']), out.get('url') or url

    def links(self, selector):
        """href attributes of matching elements; read only, never clicked."""
        return self.page.eval_on_selector_all(selector, 'els => els.map(e => e.href || e.getAttribute("href")).filter(Boolean)')

    def largest_image(self, exclude=('blur(',)):
        """The image response of the last load with the most pixels, ignoring blurred previews."""
        best, area = None, 0
        for url, data in self.images:
            if any(x in url for x in exclude):
                continue
            w, h = mh.image_size(data)
            if w and h and w * h > area:
                best, area = (url, data), w * h
        return best

    def screenshot(self, path):
        self.page.screenshot(path=path, full_page=True)
