"""An in-memory stand-in for the parts of the Gramps Web API that gwt-apply uses."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from urllib.parse import urlparse

import requests

NS = {
    "people": ("Person", "I"),
    "families": ("Family", "F"),
    "events": ("Event", "E"),
    "places": ("Place", "P"),
    "citations": ("Citation", "C"),
    "sources": ("Source", "S"),
    "repositories": ("Repository", "R"),
    "media": ("Media", "O"),
    "notes": ("Note", "N"),
    "tags": ("Tag", "T"),
    "repositories": ("Repository", "R"),
}
CLASS_NS = {cls: ns for ns, (cls, _) in NS.items()}


def drop_class(value):
    """What the real server keeps of a written object: `_class` dropped at every depth, not just the top."""
    if isinstance(value, dict):
        return {k: drop_class(v) for k, v in value.items() if k != "_class"}
    if isinstance(value, list):
        return [drop_class(v) for v in value]
    return value


class Resp:
    def __init__(self, status: int, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return self._body


def make_jwt(tree: str) -> str:
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'none'})}.{b64({'tree': tree, 'type': 'access'})}.sig"


class FakeGrampsWeb:
    def __init__(self, tree: str = "tree-test"):
        self.tree = tree
        self.store: dict[str, dict[str, dict]] = {ns: {} for ns in NS}
        self.files: dict[str, bytes] = {}  # media handle -> stored bytes
        self.calls: list[tuple[str, str]] = []
        self.headers: list[dict] = []  # the headers of every request, in step with `calls`
        self.sync_tokens: dict[str, str] = {}  # persistent sync token -> the tree its JWT is for
        self.expire = 0  # answer the next N requests that carry a JWT with 401, as if it had expired
        self.busy = 0  # answer the next N writes with "database is locked"
        self.busy_status = 500  # the HTTP status of that answer; some servers send it as a 4xx
        self.edit_after_read: tuple | None = None  # (handle, fn): another writer edits it once, right after a read
        self.drop_after: tuple | None = None  # (method, path prefix[, n to let through]): fail once, after doing it
        self.clock = 1_700_000_000  # the server's `change` stamp, one tick per write

    def tick(self) -> int:
        self.clock += 1
        return self.clock

    # -- helpers for tests --------------------------------------------------

    def add(self, ns: str, obj: dict) -> dict:
        obj = copy.deepcopy(obj)
        cls, prefix = NS[ns]
        if not obj.get("gramps_id"):
            obj["gramps_id"] = f"{prefix}{len(self.store[ns]) + 1:04d}"
        obj.setdefault("handle", hashlib.md5(json.dumps(obj, sort_keys=True).encode()).hexdigest())
        for lst in ("citation_list", "media_list", "note_list", "attribute_list", "event_ref_list"):
            if cls not in ("Note", "Tag"):
                obj.setdefault(lst, [])
        if cls == "Person":
            obj.setdefault("family_list", [])
            obj.setdefault("parent_family_list", [])
            obj.setdefault("birth_ref_index", -1)
            obj.setdefault("death_ref_index", -1)
        if cls == "Family":
            obj.setdefault("child_ref_list", [])
            self._family_refs(None, obj)
        obj = drop_class(obj)
        obj["change"] = self.tick()
        self.store[ns][obj["handle"]] = obj
        return obj

    def by_id(self, ns: str, gramps_id: str) -> dict:
        return next(o for o in self.store[ns].values() if o["gramps_id"] == gramps_id)

    def backlinks(self, handle: str) -> dict[str, list[str]]:
        """Every object that holds `handle` anywhere in it, by lower-case class."""
        out: dict[str, list[str]] = {}
        for ns, (cls, _) in NS.items():
            for h, o in self.store[ns].items():
                if h != handle and handle in json.dumps(o):
                    out.setdefault(cls.lower(), []).append(h)
        return out

    def writes(self) -> list[tuple[str, str]]:
        return [c for c in self.calls if c[0] in ("POST", "PUT", "DELETE") and not c[1].startswith("/api/token/")]

    def _family_refs(self, old: dict | None, new: dict):
        """What the real server does on a family write: keep the persons' family links in step."""
        people = self.store["people"]
        h = new["handle"]
        for role in ("father_handle", "mother_handle"):
            p = new.get(role)
            if p and h not in people[p]["family_list"]:
                people[p]["family_list"].append(h)
                people[p]["change"] = self.tick()
        for ref in new.get("child_ref_list", []):
            child = people[ref["ref"]]
            if h not in child["parent_family_list"]:
                child["parent_family_list"].append(h)
                child["change"] = self.tick()

    # -- the HTTP surface ---------------------------------------------------

    def request(self, method, url, headers=None, params=None, json=None, data=None, timeout=None):
        path = urlparse(url).path
        self.calls.append((method, path))
        self.headers.append(dict(headers or {}))
        params = params or {}
        if path == "/api/token/":
            return Resp(200, {"access_token": make_jwt(self.tree), "refresh_token": "r"})
        if path == "/api/token/sync/":
            tree = self.sync_tokens.get((json or {}).get("token"))
            if tree is None:
                return Resp(403, {"error": {"message": "Forbidden"}})
            return Resp(200, {"access_token": make_jwt(tree)})  # never a refresh token
        if self.expire:
            self.expire -= 1
            return Resp(401, {"msg": "Token has expired"})
        if method in ("POST", "PUT") and self.busy:
            self.busy -= 1
            return Resp(self.busy_status, "sqlite3.OperationalError: database is locked")
        resp = self._route(method, path, headers or {}, params, json, data)
        if self.drop_after and method == self.drop_after[0] and path.startswith(self.drop_after[1]):
            method_, prefix, *skip = self.drop_after
            if skip and skip[0]:
                self.drop_after = (method_, prefix, skip[0] - 1)
            else:
                self.drop_after = None
                raise requests.ConnectionError("connection dropped after the server did the work")
        return resp

    def _etag(self, obj):
        return hashlib.md5(repr(sorted(obj.items())).encode()).hexdigest()

    def _route(self, method, path, headers, params, body, data):
        parts = [p for p in path.split("/") if p][1:]  # drop "api"
        if parts == ["objects"] and method == "POST":
            made = []
            for obj in body:
                ns = CLASS_NS[obj["_class"]]
                if obj["handle"] in self.store[ns]:
                    return Resp(400, "Handle already exists.")
                made.append((obj["_class"], self.add(ns, obj)))
            # like the server: the transaction, each added object as stored (with its new Gramps ID) under "new"
            return Resp(201, [{"type": "add", "handle": o["handle"], "_class": cls, "old": None, "new": copy.deepcopy(o)}
                              for cls, o in made])
        if parts[0] == "media" and len(parts) == 3 and parts[2] == "file" and method == "PUT":
            media = self.store["media"][parts[1]]
            if hashlib.md5(data).hexdigest() != media["checksum"]:
                return Resp(400, "checksum differs")
            if parts[1] in self.files:
                return Resp(409, "Uploaded file has the same checksum as the existing media object")
            assert params.get("uploadmissing") == "true"
            self.files[parts[1]] = data
            return Resp(200, "")
        ns = parts[0]
        store = self.store[ns]
        if len(parts) == 1 and method == "GET":
            if "gramps_id" in params:
                rows = [o for o in store.values() if o["gramps_id"] == params["gramps_id"]]
                if not rows:
                    return Resp(404, {"error": "not found"})
            elif "handles" in params:
                rows = [store[h] for h in params["handles"].split(",") if h in store]
            else:
                rows = list(store.values())
            if "page" in params:
                size = int(params["pagesize"])
                rows = rows[(int(params["page"]) - 1) * size: int(params["page"]) * size]
            if "keys" in params:
                keys = params["keys"].split(",")
                rows = [{k: r.get(k) for k in keys} for r in rows]
            return Resp(200, copy.deepcopy(rows))
        if len(parts) == 2:
            obj = store.get(parts[1])
            if obj is None:
                return Resp(404, {"error": "not found"})
            if method == "GET":
                body = copy.deepcopy(obj)
                if params.get("backlinks") in (True, "true", "1"):  # like the server: {class: [handles]}, lower case
                    body["backlinks"] = self.backlinks(parts[1])
                resp = Resp(200, body, {"ETag": self._etag(obj)})
                if self.edit_after_read and self.edit_after_read[0] == parts[1]:
                    self.edit_after_read, (_, fn) = None, self.edit_after_read
                    fn(obj)
                return resp
            if method == "DELETE":
                del store[parts[1]]
                return Resp(200, [{"type": "delete", "handle": parts[1]}])
            if method == "PUT":
                if headers.get("If-Match"):  # like the real server: a GET ETag never matches
                    return Resp(412, "Resource does not match provided ETag")
                new = drop_class(body)
                new["change"] = self.tick()
                if ns == "families":
                    self._family_refs(obj, new)
                store[parts[1]] = new
                return Resp(200, [{"type": "update", "handle": parts[1]}])
        return Resp(405, f"fake does not serve {method} {path}")
