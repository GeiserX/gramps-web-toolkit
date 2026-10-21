"""Read-only Gramps Web client: one login, then every object type page by page."""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .tree import TYPES


class ApiError(RuntimeError):
    pass


def jwt_claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


class Client:
    """GET only. The password stays in this object and never reaches the output."""

    def __init__(self, url: str, username: str, password: str, *, tree: str | None = None,
                 opener=None, sleep=time.sleep):
        self.base = url.rstrip("/") + "/api"
        self.username = username
        self._password = password
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
        body = json.dumps({"username": self.username, "password": self._password}).encode()
        for attempt in range(1, 8):
            req = urllib.request.Request(self.base + "/token/", body, {"Content-Type": "application/json"})
            status, headers, data = self._call(req)
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
        for attempt in range(1, 7):
            with self._lock:
                if self.token is None:
                    self.login()
                token = self.token
            status, headers, data = self._call(
                urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}))
            if status == 200:
                return headers, json.loads(data)
            if status == 401:
                with self._lock:
                    if self.token == token:  # one relogin for every thread that saw the 401
                        self.token = None
                continue
            if status == 429 or status >= 500:
                self.sleep(float(headers.get("Retry-After") or 5 * attempt))
                continue
            raise ApiError(f"GET {path}: HTTP {status}")
        raise ApiError(f"GET {path}: gave up after {attempt} tries")

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
