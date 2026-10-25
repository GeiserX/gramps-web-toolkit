"""Read-only Gramps Web client: one login, then every object type page by page."""

from __future__ import annotations

import base64
import http.client
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import version
from .tree import TYPES

# the Gramps Web maintainer asks every API client to say who it is
USER_AGENT = f"gramps-web-toolkit/{version()} (gwt-check; +https://github.com/GeiserX/gramps-web-toolkit)"


class ApiError(RuntimeError):
    pass


# a timeout, a reset or an SSL read error on a loaded server; HTTPError is answered in _call first
NETWORK_ERRORS = (OSError, http.client.HTTPException)
TRIES = 6


def backoff(attempt: int) -> float:
    """5, 10, 20, 40, then 60 s at most between tries."""
    return min(60.0, 5.0 * 2 ** (attempt - 1))


def api_base(url: str) -> str:
    """GWT_URL -> the API root. The site address and the API address (ending in /api) both work."""
    url = url.strip().rstrip("/")
    return (url[: -len("/api")] if url.endswith("/api") else url) + "/api"


def url_problem(url: str) -> str | None:
    """Why GWT_URL cannot be used, or None."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return f"GWT_URL must be an http:// or https:// address, such as https://gramps.example.org; got {url!r}"
    return None


def jwt_claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


class Client:
    """GET only. The password or token stays in this object and never reaches the output."""

    def __init__(self, url: str, username: str, password: str, *, tree: str | None = None,
                 token: str | None = None, opener=None, sleep=time.sleep):
        self.base = api_base(url)
        self.username = username
        self._password = password
        # a persistent Gramps Web access token of scope "sync": exchanged for a JWT instead of the password
        self._sync_token = token
        self.expected_tree = tree
        self.open = opener or urllib.request.urlopen
        self.sleep = sleep
        self.token: str | None = None
        self.tree: str | None = None
        self.requests = 0
        self._lock = threading.RLock()

    def _call(self, req):
        with self._lock:
            self.requests += 1
        try:
            with self.open(req, timeout=180) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers or {}), e.read()

    def login(self) -> None:
        # the sync endpoint answers no refresh token: a 401 in get() exchanges the token again
        if self._sync_token:
            path, body = "/token/sync/", {"token": self._sync_token}
        else:
            path, body = "/token/", {"username": self.username, "password": self._password}
        data = json.dumps(body).encode()
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
        for attempt in range(1, 8):
            req = urllib.request.Request(self.base + path, data, headers)
            try:
                status, headers, data = self._call(req)
            except NETWORK_ERRORS as exc:
                if attempt >= TRIES:
                    raise ApiError(f"login failed: {type(exc).__name__}: {exc}") from exc
                self.sleep(backoff(attempt))
                continue
            if status != 429:
                break
            # logins are rate-limited: wait as told, never retry in a burst
            self.sleep(float(headers.get("Retry-After") or 10 * attempt))
        if status != 200:
            raise ApiError(f"login failed: HTTP {status}")
        self.token = json.loads(data)["access_token"]
        self.tree = jwt_claims(self.token).get("tree")
        if self.expected_tree and self.tree != self.expected_tree:
            raise ApiError(f"login landed in tree {self.tree!r}, expected {self.expected_tree!r}")

    def get(self, path: str, params: dict) -> tuple[dict, object]:
        url = f"{self.base}{path}?{urllib.parse.urlencode(params)}"
        last = ""
        for attempt in range(1, TRIES + 1):
            with self._lock:
                if self.token is None:
                    self.login()
                token = self.token
            try:
                status, headers, data = self._call(
                    urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}))
            except NETWORK_ERRORS as exc:
                # one slow page must not end a whole-tree read: retry it like a 5xx
                last = f"{type(exc).__name__}: {exc}"
                if attempt < TRIES:
                    self.sleep(backoff(attempt))
                continue
            if status == 200:
                return headers, json.loads(data)
            if status == 401:
                last = "HTTP 401"
                with self._lock:
                    if self.token == token:  # one relogin for every thread that saw the 401
                        self.token = None
                continue
            if status == 429 or status >= 500:
                last = f"HTTP {status}"
                if attempt < TRIES:
                    self.sleep(float(headers.get("Retry-After") or backoff(attempt)))
                continue
            raise ApiError(f"GET {path}: HTTP {status}")
        raise ApiError(f"GET {path}: gave up after {TRIES} tries, last {last}")

    def read_all(self, *, pagesize: int = 1000, workers: int = 4, progress=None) -> dict[str, dict[str, dict]]:
        """Every object of every type, by handle, page by page, without trusting the pages: a write during the
        read shifts them, so an object can come twice or not at all. `refresh` puts that right."""
        out: dict[str, dict[str, dict]] = {}
        for t in TYPES:
            headers, first = self.get(f"/{t}/", {"page": 1, "pagesize": pagesize})
            total = int({k.lower(): v for k, v in headers.items()}.get("x-total-count", len(first)))
            rows = list(first)
            with ThreadPoolExecutor(workers) as pool:
                for _, chunk in pool.map(lambda p: self.get(f"/{t}/", {"page": p, "pagesize": pagesize}),
                                         range(2, max(1, -(-total // pagesize)) + 1)):
                    rows += chunk
            out[t] = {o["handle"]: o for o in rows}
            if progress:
                progress(t, len(out[t]))
        return out

    def refresh(self, objs: dict[str, dict[str, dict]], chunk: int = 100) -> int:
        """One list of change stamps per type (a single request, so one consistent view of that type), then a
        reread of every object whose stamp moved or that is missing, and out with the ones that are gone.
        Returns how many objects that touched: 0 means nothing changed since the last read."""
        moved = 0
        for t in TYPES:
            _, rows = self.get(f"/{t}/", {"keys": "handle,change"})
            now = {r["handle"]: r.get("change") for r in rows}
            have = objs.setdefault(t, {})
            gone = [h for h in have if h not in now]
            stale = [h for h, c in now.items() if h not in have or have[h].get("change") != c]
            for h in gone:
                del have[h]
            for i in range(0, len(stale), chunk):
                _, fresh = self.get(f"/{t}/", {"handles": ",".join(stale[i:i + chunk])})
                have.update((o["handle"], o) for o in fresh)
            moved += len(gone) + len(stale)
        return moved

    def dump(self, *, pagesize: int = 1000, workers: int = 4, progress=None) -> dict:
        """Every object of every type. Raises if a type comes back short or with repeats."""
        out: dict[str, list] = {}
        for t in TYPES:
            headers, first = self.get(f"/{t}/", {"page": 1, "pagesize": pagesize})
            total = int({k.lower(): v for k, v in headers.items()}.get("x-total-count", len(first)))
            pages = max(1, -(-total // pagesize))
            rows = list(first)
            if pages > 1:
                with ThreadPoolExecutor(workers) as pool:
                    for _, chunk in pool.map(
                        lambda p: self.get(f"/{t}/", {"page": p, "pagesize": pagesize}),
                        range(2, pages + 1),
                    ):
                        rows += chunk
            handles = {o["handle"] for o in rows}
            if len(rows) != total or len(handles) != total:
                # pages shift when the tree changes mid-dump; a short dump gives false orphans
                raise ApiError(f"{t}: server says {total}, got {len(rows)} rows, {len(handles)} distinct")
            out[t] = rows
            if progress:
                progress(t, total)
        return out
