"""Time a gwt-apply command and count its requests by kind. With --apply it writes: use a throwaway tree.

    GWT_URL=... GWT_USERNAME=... GWT_PASSWORD=... GWT_TREE=... \\
        python3 bench/run.py <gwt-apply arguments> > plan.txt

Runs the gwt-apply command line in-process with a timing HTTP session, prints its output to stdout
and, to stderr, the wall time and, per request kind (method and path with handles folded), how many
calls there were and how long they took.
"""

from __future__ import annotations

import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gwt_apply.client as clientmod  # noqa: E402
from gwt_apply.cli import main  # noqa: E402


class Timed(requests.Session):
    stats: dict = defaultdict(lambda: [0, 0.0])

    def request(self, method, url, *a, **kw):
        t = time.monotonic()
        try:
            return super().request(method, url, *a, **kw)
        finally:
            path = re.sub(r"(/api/\w+/)[^/?]+", r"\1<h>", "/api" + url.split("/api", 1)[1])
            params = kw.get("params") or {}
            kind = f"{method} {path}" + (" ?" + ",".join(sorted(params)) if params else "")
            s = self.stats[kind]
            s[0] += 1
            s[1] += time.monotonic() - t


if __name__ == "__main__":
    clientmod.requests.Session = Timed
    t = time.monotonic()
    code = main(sys.argv[1:])
    took = time.monotonic() - t
    calls = sum(n for n, _ in Timed.stats.values())
    print(f"exit {code}, {took:.1f} s, {calls} requests", file=sys.stderr)
    for kind, (n, secs) in sorted(Timed.stats.items(), key=lambda kv: -kv[1][1]):
        print(f"  {secs:8.1f} s  {n:6d}  {secs / n * 1000:7.1f} ms  {kind}", file=sys.stderr)
