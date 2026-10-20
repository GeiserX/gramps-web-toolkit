"""One polite HTTP client: a fixed identity, a per-host delay, and retries on 429/5xx."""
from __future__ import annotations

import sys
import time
from urllib.parse import urlsplit

import requests

# Some portals (Antenati) refuse a user agent that does not start with "Mozilla/5.0";
# the "compatible" form still names the tool honestly.
USER_AGENT = "Mozilla/5.0 (compatible; gwt-fetch/0.1; +https://github.com/GeiserX/gramps-web-toolkit)"
RETRY_STATUS = {429, 500, 502, 503, 504}


class Client:
    def __init__(self, delay: float = 1.0, retries: int = 4, timeout: float = 60.0, session=None):
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._last: dict[str, float] = {}

    def _wait(self, url: str) -> None:
        host = urlsplit(url).netloc
        gap = time.monotonic() - self._last.get(host, 0.0)
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last[host] = time.monotonic()

    def get(self, url: str, *, referer: str | None = None, stream: bool = False, **kw) -> requests.Response:
        headers = dict(kw.pop("headers", {}) or {})
        if referer:
            headers.setdefault("Referer", referer)
        backoff = max(self.delay, 2.0)
        for attempt in range(self.retries + 1):
            self._wait(url)
            try:
                r = self.session.get(url, headers=headers, timeout=self.timeout, stream=stream, **kw)
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt == self.retries:
                    raise
                print(f"gwt-fetch: {e.__class__.__name__} on {url}, retrying in {backoff:.0f}s", file=sys.stderr)
                time.sleep(backoff)
                backoff *= 2
                continue
            if r.status_code in RETRY_STATUS and attempt < self.retries:
                wait = _retry_after(r) or backoff
                print(f"gwt-fetch: HTTP {r.status_code} on {url}, retrying in {wait:.0f}s", file=sys.stderr)
                r.close()
                time.sleep(wait)
                backoff *= 2
                continue
            return r
        return r  # pragma: no cover

    def json(self, url: str, **kw):
        r = self.get(url, headers={"Accept": "application/json, application/ld+json"}, **kw)
        r.raise_for_status()
        return r.json()

    def text(self, url: str, **kw) -> str:
        r = self.get(url, **kw)
        r.raise_for_status()
        return r.text


def _retry_after(r) -> float | None:
    v = r.headers.get("Retry-After", "")
    return min(float(v), 300.0) if v.isdigit() else None
