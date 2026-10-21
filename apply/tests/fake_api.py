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
    "media": ("Media", "O"),
    "notes": ("Note", "N"),
}
CLASS_NS = {cls: ns for ns, (cls, _) in NS.items()}


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
        self.busy = 0  # answer the next N writes with "database is locked"
        self.edit_after_read: tuple | None = None  # (handle, fn): another writer edits it once, right after a read
        self.drop_after: tuple | None = None  # (method, path prefix[, n to let through]): fail once, after doing it

    # -- helpers for tests --------------------------------------------------

    def add(self, ns: str, obj: dict) -> dict:
        obj = copy.deepcopy(obj)
        cls, prefix = NS[ns]
        if not obj.get("gramps_id"):
            obj["gramps_id"] = f"{prefix}{len(self.store[ns]) + 1:04d}"
        obj.setdefault("handle", hashlib.md5(json.dumps(obj, sort_keys=True).encode()).hexdigest())
        for lst in ("citation_list", "media_list", "note_list", "attribute_list", "event_ref_list"):
            if cls not in ("Note",):
                obj.setdefault(lst, [])
        if cls == "Person":
            obj.setdefault("family_list", [])
            obj.setdefault("parent_family_list", [])
            obj.setdefault("birth_ref_index", -1)
            obj.setdefault("death_ref_index", -1)
        if cls == "Family":
            obj.setdefault("child_ref_list", [])
            self._family_refs(None, obj)
        obj.pop("_class", None)
        self.store[ns][obj["handle"]] = obj
        return obj

    def by_id(self, ns: str, gramps_id: str) -> dict:
        return next(o for o in self.store[ns].values() if o["gramps_id"] == gramps_id)

    def writes(self) -> list[tuple[str, str]]:
        return [c for c in self.calls if c[0] in ("POST", "PUT", "DELETE") and c[1] != "/api/token/"]

    def _family_refs(self, old: dict | None, new: dict):
        """What the real server does on a family write: keep the persons' family links in step."""
        people = self.store["people"]
        h = new["handle"]
        for role in ("father_handle", "mother_handle"):
            p = new.get(role)
            if p and h not in people[p]["family_list"]:
                people[p]["family_list"].append(h)
        for ref in new.get("child_ref_list", []):
            child = people[ref["ref"]]
            if h not in child["parent_family_list"]:
                child["parent_family_list"].append(h)

    # -- the HTTP surface ---------------------------------------------------

    def request(self, method, url, headers=None, params=None, json=None, data=None, timeout=None):
        path = urlparse(url).path
        self.calls.append((method, path))
        params = params or {}
        if path == "/api/token/":
            return Resp(200, {"access_token": make_jwt(self.tree), "refresh_token": "r"})
        if method in ("POST", "PUT") and self.busy:
            self.busy -= 1
            return Resp(500, "sqlite3.OperationalError: database is locked")
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
            for obj in body:
                ns = CLASS_NS[obj["_class"]]
                if obj["handle"] in self.store[ns]:
                    return Resp(400, "Handle already exists.")
                self.add(ns, obj)
            return Resp(201, [{"type": "add", "handle": o["handle"]} for o in body])
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
            if "keys" in params:
                keys = params["keys"].split(",")
                rows = [{k: r.get(k) for k in keys} for r in rows]
            return Resp(200, copy.deepcopy(rows))
        if len(parts) == 2:
            obj = store.get(parts[1])
            if obj is None:
                return Resp(404, {"error": "not found"})
            if method == "GET":
                resp = Resp(200, copy.deepcopy(obj), {"ETag": self._etag(obj)})
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
                new = copy.deepcopy(body)
                new.pop("_class", None)
                if ns == "families":
                    self._family_refs(obj, new)
                store[parts[1]] = new
                return Resp(200, [{"type": "update", "handle": parts[1]}])
        return Resp(405, f"fake does not serve {method} {path}")
