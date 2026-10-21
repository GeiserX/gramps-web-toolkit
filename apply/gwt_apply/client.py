"""Gramps Web REST client: login, reads with an overlay for dry-run, logged writes."""

from __future__ import annotations

import base64
import copy
import json
import time
from datetime import datetime, timezone
from typing import Any

import requests

# Gramps class -> REST namespace
NAMESPACE = {
    "Person": "people",
    "Family": "families",
    "Event": "events",
    "Place": "places",
    "Citation": "citations",
    "Source": "sources",
    "Media": "media",
    "Note": "notes",
}


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Retryable(ApiError):
    """The server was busy ("database is locked", 5xx): rerun the whole op."""


def jwt_claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


class Client:
    """One tree, one login. In dry-run every write lands in `self.pending` instead."""

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        *,
        tree: str | None = None,
        dry_run: bool = True,
        log_path: str | None = None,
        session: Any = None,
    ):
        self.base = url.rstrip("/") + "/api"
        self.username = username
        self._password = password
        self.expected_tree = tree
        self.dry_run = dry_run
        self.session = session or requests.Session()
        self.token: str | None = None
        self.tree: str | None = None
        self.pending: dict[tuple[str, str], dict] = {}
        # Run-wide indexes, each read once. They stay true for the run because the tree's lock keeps other
        # appliers out and this client adds its own writes to them; a GET still reads every object it changes.
        self._ids: dict[str, dict[str, str]] = {}  # class -> Gramps ID -> handle
        self._handles: dict[str, set[str]] = {}  # class -> every handle
        self._lists: dict[tuple[str, tuple], list[dict]] = {}  # (class, keys) -> rows from the server
        self.written: dict[tuple[str, str], dict] = {}  # what this run created or changed, for the indexes
        self.unsure: set[tuple[str, str]] = set()  # creates with no answer: the server may have them
        # "x": a log file is never appended to or overwritten; each run writes its own
        self._log = open(log_path, "x", encoding="utf-8") if log_path else None
        self.context: dict = {}  # op key and ruling, stamped on every log line
        self._last_login = 0.0

    # -- plumbing ---------------------------------------------------------

    def log(self, **entry):
        if self._log is None:
            return
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dry_run": self.dry_run,
            **self.context,
            **entry,
        }
        self._log.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._log.flush()

    def login(self):
        # the server rate-limits logins (429): space them out and wait when told to
        for attempt in range(1, 7):
            wait = 1.1 - (time.monotonic() - self._last_login)
            if wait > 0:
                time.sleep(wait)
            self._last_login = time.monotonic()
            r = self.session.request(
                "POST",
                self.base + "/token/",
                json={"username": self.username, "password": self._password},
                timeout=60,
            )
            self.log(method="POST", path="/token/", status=r.status_code)
            if r.status_code != 429:
                break
            time.sleep(float(r.headers.get("Retry-After") or 5 * attempt))
        if r.status_code != 200:
            raise ApiError(f"login failed: HTTP {r.status_code}", r.status_code)
        self.token = r.json()["access_token"]
        self.tree = jwt_claims(self.token).get("tree")
        if self.expected_tree and self.tree != self.expected_tree:
            raise ApiError(
                f"login landed in tree {self.tree!r}, expected {self.expected_tree!r}"
            )

    def _request(self, method: str, path: str, *, write: bool = False, **kw):
        if self.token is None:
            self.login()
        for attempt in (1, 2):
            headers = dict(kw.pop("headers", {}) or {})
            headers["Authorization"] = f"Bearer {self.token}"
            try:
                r = self.session.request(
                    method, self.base + path, headers=headers, timeout=120, **kw
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                # the write may or may not have happened: the op starts over from fresh reads
                self.log(method=method, path=path, status=None, error=f"{type(exc).__name__}: {exc}"[:500])
                raise Retryable(f"{method} {path}: {type(exc).__name__}") from exc
            kw["headers"] = {k: v for k, v in headers.items() if k != "Authorization"}
            if r.status_code == 401 and attempt == 1:
                self.login()
                continue
            break
        entry = {"method": method, "path": path, "status": r.status_code}
        if write and "json" in kw:
            entry["body"] = kw["json"]
        if r.status_code >= 400:
            entry["error"] = r.text[:500]
        self.log(**entry)
        if r.status_code >= 500 or "database is locked" in r.text[:2000].lower():
            raise Retryable(f"{method} {path}: HTTP {r.status_code}", r.status_code)
        return r

    # -- reads ------------------------------------------------------------

    def get(self, cls: str, handle: str) -> dict | None:
        """The object, or None. `_read` keeps what the server sent, for the check in `update`."""
        if (cls, handle) in self.pending:
            return copy.deepcopy(self.pending[(cls, handle)])
        r = self._request("GET", f"/{NAMESPACE[cls]}/{handle}")
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise ApiError(f"GET {cls} {handle}: HTTP {r.status_code}", r.status_code)
        obj = r.json()
        obj["_read"] = json.dumps(r.json(), sort_keys=True)
        return obj

    def _index(self, cls: str) -> dict[str, str]:
        """Gramps ID -> handle for a whole class, from one read the first time it is needed."""
        if cls not in self._ids:
            r = self._request("GET", f"/{NAMESPACE[cls]}/", params={"keys": "handle,gramps_id"})
            if r.status_code != 200:
                raise ApiError(f"GET {cls} list: HTTP {r.status_code}", r.status_code)
            rows = r.json()
            self._ids[cls] = {row["gramps_id"]: row["handle"] for row in rows if row.get("gramps_id")}
            self._handles[cls] = {row["handle"] for row in rows}
            for (c, h), obj in self.written.items():
                if c == cls:
                    self._remember(cls, h, obj)
        return self._ids[cls]

    def _remember(self, cls: str, handle: str, obj: dict) -> None:
        if cls in self._ids:
            self._handles[cls].add(handle)
            if obj.get("gramps_id"):
                self._ids[cls][obj["gramps_id"]] = handle

    def handle_for_gramps_id(self, cls: str, gramps_id: str) -> str | None:
        for (c, h), obj in self.pending.items():
            if c == cls and obj.get("gramps_id") == gramps_id:
                return h
        return self._index(cls).get(gramps_id)

    def exists(self, cls: str, handle: str) -> bool:
        """Whether the object is there, without reading it."""
        if (cls, handle) in self.pending:
            return True
        if (cls, handle) in self.unsure:
            return self.get(cls, handle) is not None
        self._index(cls)
        return handle in self._handles[cls]

    def get_many(self, cls: str, handles: list[str]) -> list[dict]:
        out = [copy.deepcopy(self.pending[(cls, h)]) for h in handles if (cls, h) in self.pending]
        rest = [h for h in handles if (cls, h) not in self.pending]
        if rest:
            r = self._request(
                "GET", f"/{NAMESPACE[cls]}/", params={"handles": ",".join(rest)}
            )
            if r.status_code != 200:
                raise ApiError(f"GET {cls} list: HTTP {r.status_code}", r.status_code)
            out += r.json()
        return out

    def list_all(self, cls: str, keys: list[str]) -> list[dict]:
        """Every object of a class with only `keys`, read once per run, with this run's writes on top."""
        cache = (cls, tuple(keys))
        if cache not in self._lists:
            r = self._request(
                "GET", f"/{NAMESPACE[cls]}/", params={"keys": ",".join(["handle", *keys])}
            )
            if r.status_code != 200:
                raise ApiError(f"GET {cls} list: HTTP {r.status_code}", r.status_code)
            self._lists[cache] = r.json()
        mine = {h: obj for (c, h), obj in {**self.written, **self.pending}.items() if c == cls}
        rows = [row for row in self._lists[cache] if row["handle"] not in mine]
        rows += [{k: obj.get(k) for k in ["handle", *keys]} for h, obj in mine.items()]
        return rows

    # -- writes -----------------------------------------------------------

    def create(self, obj: dict) -> None:
        """POST /api/objects/ with one new object (always a deterministic handle)."""
        cls = obj["_class"]
        if self.dry_run:
            self.pending[(cls, obj["handle"])] = copy.deepcopy(obj)
            self.log(method="POST", path="/objects/", body=[obj], status=None)
            return
        self.unsure.add((cls, obj["handle"]))
        r = self._request("POST", "/objects/", write=True, json=[obj])
        if r.status_code != 201:
            raise ApiError(f"create {cls}: HTTP {r.status_code} {r.text[:300]}", r.status_code)
        self.unsure.discard((cls, obj["handle"]))
        self.written[(cls, obj["handle"])] = copy.deepcopy(obj)
        self._remember(cls, obj["handle"], obj)

    def update(self, obj: dict) -> None:
        """PUT the whole object back, if it still is what the op read.

        The ETag a GET returns cannot be used as If-Match: the server hashes the
        request cache key for one and the object for the other, so If-Match always
        answers 412. Instead the object is read again right before the PUT.
        """
        cls = obj["_class"]
        body = {k: v for k, v in obj.items() if k not in ("_read", "extended", "profile")}
        if self.dry_run:
            self.pending[(cls, obj["handle"])] = copy.deepcopy(body)
            self.log(method="PUT", path=f"/{NAMESPACE[cls]}/{obj['handle']}", body=body, status=None)
            return
        if "_read" in obj:
            fresh = self.get(cls, obj["handle"])
            if fresh is None or fresh["_read"] != obj["_read"]:
                raise Retryable(f"update {cls} {obj['handle']}: changed since read")
        r = self._request("PUT", f"/{NAMESPACE[cls]}/{obj['handle']}", write=True, json=body)
        if r.status_code != 200:
            raise ApiError(f"update {cls}: HTTP {r.status_code} {r.text[:300]}", r.status_code)
        self.written[(cls, obj["handle"])] = copy.deepcopy(body)

    def delete(self, cls: str, handle: str) -> None:
        """DELETE one object. Used only for the lock note."""
        if self.dry_run:
            raise ApiError("delete in a dry run")
        r = self._request("DELETE", f"/{NAMESPACE[cls]}/{handle}", write=True)
        if r.status_code not in (200, 404):
            raise ApiError(f"delete {cls} {handle}: HTTP {r.status_code} {r.text[:300]}", r.status_code)
        self.written.pop((cls, handle), None)
        self._handles.get(cls, set()).discard(handle)

    def upload_missing_file(self, handle: str, data: bytes, mime: str) -> str:
        """Store the file of a media object created by `create`. Returns what happened."""
        if self.dry_run:
            self.log(method="PUT", path=f"/media/{handle}/file?uploadmissing=1", status=None)
            return "would upload"
        r = self._request(
            "PUT",
            f"/media/{handle}/file",
            params={"uploadmissing": "true"},
            data=data,
            headers={"Content-Type": mime},
        )
        if r.status_code == 200:
            return "uploaded"
        if r.status_code == 409:
            return "file already on server"
        raise ApiError(f"upload {handle}: HTTP {r.status_code} {r.text[:300]}", r.status_code)
