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
    "Repository": "repositories",
    "Media": "media",
    "Note": "notes",
    "Tag": "tags",
}


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Retryable(ApiError):
    """The server was busy ("database is locked", 5xx): rerun the whole op."""


class Stale(ApiError):
    """An object read from the snapshot changed on the server since: take a new snapshot."""


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
        # "METHOD path" of each write with no clear answer (timeout, dropped connection, 5xx, "database is
        # locked"): the server may have done it. The applier empties it before every try of an op.
        self.unknown_write: list[str] = []
        # With a snapshot, reads come from it, except for objects this run wrote (or made the server change)
        self.snapshot: dict[str, dict[str, dict]] | None = None
        self.offline = False  # a dry run on a snapshot: no server at all
        self.stale: set[tuple[str, str]] = set()
        self.reads: set[tuple[str, str]] = set()  # every object the run read, for the snapshot check
        self.listed: set[str] = set()  # every class the run searched as a whole
        self.deleted: set[tuple[str, str]] = set()  # objects a merge removed
        self.unsnapped: set[str] = set()  # classes a merge changed all over: read from the server from then on
        # "x": a log file is never appended to or overwritten; each run writes its own
        self._log = open(log_path, "x", encoding="utf-8") if log_path else None
        self.context: dict = {}  # op key and ruling, stamped on every log line
        # called with (class, handle) before an object is read, written or merged, and with (class, None) when a
        # whole class may change: the applier writes what it holds for that object first
        self.touch = None
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
            try:
                r = self.session.request(
                    "POST",
                    self.base + "/token/",
                    json={"username": self.username, "password": self._password},
                    timeout=60,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                self.log(method="POST", path="/token/", status=None, error=type(exc).__name__)
                raise Retryable(f"login: {type(exc).__name__}") from exc
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

    def use_snapshot(self, data: dict, *, offline: bool = False) -> None:
        self.snapshot = {cls: {o["handle"]: o for o in rows} for cls, rows in data["objects"].items()}
        self.offline = offline
        if offline:
            self.tree = data.get("tree")

    def _snapped(self, cls: str, handle: str) -> bool:
        return self.snapshot is not None and cls not in self.unsnapped and (cls, handle) not in self.stale

    def _request(self, method: str, path: str, *, write: bool = False, **kw):
        if self.offline:
            raise ApiError(f"{method} {path}: a dry run on a snapshot does not reach the server")
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
                # the write may or may not have happened: the op starts over from fresh reads. The body goes in the
                # log line, so a revert can tell whether the server holds it.
                entry = {"method": method, "path": path, "status": None, "error": f"{type(exc).__name__}: {exc}"[:500]}
                if write and "json" in kw:
                    entry["body"] = kw["json"]
                self.log(**entry)
                if write:
                    self.unknown_write.append(f"{method} {path}")
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
            if write:
                self.unknown_write.append(f"{method} {path}")
            raise Retryable(f"{method} {path}: HTTP {r.status_code}", r.status_code)
        return r

    # -- reads ------------------------------------------------------------

    def _touched(self, cls: str | None, handle: str | None) -> None:
        if self.touch is not None:
            self.touch(cls, handle)

    def get(self, cls: str, handle: str) -> dict | None:
        """The object, or None. `_read` keeps what the server sent, for the check in `update`."""
        self._touched(cls, handle)
        if (cls, handle) in self.deleted:
            return None
        if (cls, handle) in self.pending:
            return copy.deepcopy(self.pending[(cls, handle)])
        self.reads.add((cls, handle))
        if self._snapped(cls, handle):
            obj = self.snapshot.get(cls, {}).get(handle)
            if obj is None:
                return None
            obj = copy.deepcopy(obj)
            obj["_snap"] = obj.get("change")  # what `update` compares with the server
            return obj
        return self.get_live(cls, handle)

    def get_live(self, cls: str, handle: str, *, backlinks: bool = False) -> dict | None:
        """The object as the server has it now. With `backlinks`, also what points at it ({class: [handles]})."""
        self._touched(cls, handle)
        if backlinks:
            r = self._request("GET", f"/{NAMESPACE[cls]}/{handle}", params={"backlinks": "true"})
        else:
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
            if self.snapshot is not None:
                rows = list(self.snapshot.get(cls, {}).values())
            else:
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
        handle = self._index(cls).get(gramps_id)
        if handle is None or (cls, handle) in self.deleted:
            return None
        if (cls, handle) in self.unsure and not self.exists(cls, handle):  # merged away by a lost request
            return None
        return handle

    def exists(self, cls: str, handle: str) -> bool:
        """Whether the object is there, without reading it."""
        if (cls, handle) in self.pending:
            return True
        if (cls, handle) in self.deleted:
            return False
        self.reads.add((cls, handle))
        if (cls, handle) in self.unsure:
            return self.get(cls, handle) is not None
        self._index(cls)
        return handle in self._handles[cls]

    def get_many(self, cls: str, handles: list[str]) -> list[dict]:
        for h in handles:
            self._touched(cls, h)
        handles = [h for h in handles if (cls, h) not in self.deleted]
        out = [copy.deepcopy(self.pending[(cls, h)]) for h in handles if (cls, h) in self.pending]
        rest = [h for h in handles if (cls, h) not in self.pending]
        self.reads |= {(cls, h) for h in rest}
        if self.snapshot is not None:
            out += [copy.deepcopy(self.snapshot.get(cls, {})[h]) for h in rest
                    if self._snapped(cls, h) and h in self.snapshot.get(cls, {})]
            rest = [h for h in rest if not self._snapped(cls, h)]
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
        self.listed.add(cls)
        if "person_ref_list" in keys:
            self._touched(cls, None)
        if cache not in self._lists and self.snapshot is not None:
            self._lists[cache] = [{k: o.get(k) for k in ["handle", *keys]} for o in self.snapshot.get(cls, {}).values()]
        if cache not in self._lists:
            r = self._request(
                "GET", f"/{NAMESPACE[cls]}/", params={"keys": ",".join(["handle", *keys])}
            )
            if r.status_code != 200:
                raise ApiError(f"GET {cls} list: HTTP {r.status_code}", r.status_code)
            self._lists[cache] = r.json()
        mine = {h: obj for (c, h), obj in {**self.written, **self.pending}.items() if c == cls}
        rows = [row for row in self._lists[cache] if row["handle"] not in mine and (cls, row["handle"]) not in self.deleted]
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
        self._wrote(cls, obj)  # before the request: if its answer is lost, the retry reads the server
        r = self._request("POST", "/objects/", write=True, json=[obj])
        if r.status_code != 201:
            raise ApiError(f"create {cls}: HTTP {r.status_code} {r.text[:300]}", r.status_code)
        self.unsure.discard((cls, obj["handle"]))
        self.written[(cls, obj["handle"])] = copy.deepcopy(obj)
        self._remember(cls, obj["handle"], obj)

    def _wrote(self, cls: str, obj: dict) -> None:
        """Objects this write changed on the server are read from it from now on, not from the snapshot."""
        self.stale.add((cls, obj["handle"]))
        if cls == "Family":  # the server keeps the members' family lists in step
            members = [obj.get("father_handle"), obj.get("mother_handle")]
            members += [r.get("ref") for r in obj.get("child_ref_list") or []]
            self.stale |= {("Person", h) for h in members if h}

    def update(self, obj: dict) -> None:
        """PUT the whole object back, if it still is what the op read.

        The ETag a GET returns cannot be used as If-Match: the server hashes the
        request cache key for one and the object for the other, so If-Match always
        answers 412. Instead the object is read again right before the PUT.
        """
        cls = obj["_class"]
        self._touched(cls, obj["handle"])
        body = {k: v for k, v in obj.items() if k not in ("_read", "_snap", "extended", "profile")}
        if self.dry_run:
            self.pending[(cls, obj["handle"])] = copy.deepcopy(body)
            self.log(method="PUT", path=f"/{NAMESPACE[cls]}/{obj['handle']}", body=body, status=None)
            return
        if "_snap" in obj:
            live = self.get_live(cls, obj["handle"])
            if live is None or live.get("change") != obj["_snap"]:
                raise Stale(f"{cls} {obj.get('gramps_id') or obj['handle']} changed since the snapshot; take a new one")
        elif "_read" in obj:
            fresh = self.get_live(cls, obj["handle"])
            if fresh is None or fresh["_read"] != obj["_read"]:
                raise Retryable(f"update {cls} {obj['handle']}: changed since read")
        self._wrote(cls, body)
        r = self._request("PUT", f"/{NAMESPACE[cls]}/{obj['handle']}", write=True, json=body)
        if r.status_code != 200:
            raise ApiError(f"update {cls}: HTTP {r.status_code} {r.text[:300]}", r.status_code)
        self.written[(cls, obj["handle"])] = copy.deepcopy(body)

    def delete(self, cls: str, handle: str) -> None:
        """DELETE one object: the lock note, and what `remove` deletes."""
        if self.dry_run:
            raise ApiError("delete in a dry run")
        r = self._request("DELETE", f"/{NAMESPACE[cls]}/{handle}", write=True)
        if r.status_code not in (200, 404):
            raise ApiError(f"delete {cls} {handle}: HTTP {r.status_code} {r.text[:300]}", r.status_code)
        self.written.pop((cls, handle), None)
        self._handles.get(cls, set()).discard(handle)

    def remove(self, cls: str, handle: str) -> None:
        """DELETE an object an op has emptied of references. A dry run only marks it as gone."""
        self.deleted.add((cls, handle))
        if self.dry_run:
            self.log(method="DELETE", path=f"/{NAMESPACE[cls]}/{handle}", status=None)
            return
        try:
            self.delete(cls, handle)
        except Retryable:
            # like a lost merge: the object may still be there, so the retry asks the server
            self.deleted.discard((cls, handle))
            self.unsure.add((cls, handle))
            raise
        except ApiError:
            self.deleted.discard((cls, handle))
            raise
        self.unsure.discard((cls, handle))

    def merge(self, cls: str, winner: str, loser: str, body: dict | None = None) -> None:
        """Gramps' own merge: every reference to the loser moves to the winner, then the loser is deleted.

        A dry run only marks the loser as gone; the op shows what would move.
        """
        self._touched(None, None)  # a merge rewrites whatever pointed at the loser
        path = f"/{NAMESPACE[cls]}/{winner}/merge/{loser}"
        self.deleted.add((cls, loser))
        if self.dry_run:
            self.log(method="POST", path=path, body=body or {}, status=None)
            return
        # a merge rewrites people, families and notes that pointed at the loser: stop trusting the snapshot for them
        self.unsnapped |= {"Person", "Family", "Note"}
        self.stale |= {(cls, winner), (cls, loser)}
        try:
            r = self._request("POST", path, write=True, json=body or {})
        except Retryable:
            # no answer, or a busy one: the merge may have happened; the retry asks the server
            self.deleted.discard((cls, loser))
            self.unsure.add((cls, loser))
            raise
        if r.status_code != 200:
            self.deleted.discard((cls, loser))
            raise ApiError(f"merge {cls} {loser} into {winner}: HTTP {r.status_code} {r.text[:300]}", r.status_code)
        self.unsure.discard((cls, loser))
        if self._ids.get(cls):
            self._handles[cls].discard(loser)

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
