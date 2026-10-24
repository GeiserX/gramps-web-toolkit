"""Undo an apply run from its log and the tree as it was before.

    gwt-apply --revert gwt-apply-<run id>.jsonl --pre before.json.gz            # plan, writes nothing
    gwt-apply --revert gwt-apply-<run id>.jsonl --pre before.json.gz --apply    # do it, then read back

`--pre` is a snapshot (`--save-snapshot`) or a `gwt-check dump` taken before the run. The log says what the
run wrote; the pre-run copy says what each object was. Newest write first:

- an object the run changed (PUT) goes back to its pre-run copy, once, whatever the number of writes;
- a note, citation, event, place, source, repository or tag the run made (POST) is deleted;
- nothing is touched that is no longer what the run left: a person someone edited since the run is reported
  and skipped, never overwritten.

Not undone, reported for a backup restore: people and families the run made (the server also edits the
objects around them), merges, deletes and media uploads.
"""

from __future__ import annotations

import gzip
import json

from .client import NAMESPACE, ApiError, Client
from .lock import LOCK_HANDLE

IGNORE = {"change", "extended", "profile", "_read", "_snap", "_class", "gramps_id"}
DELETABLE = {"Note", "Citation", "Event", "Place", "Source", "Repository", "Tag"}
CLS_OF = {ns: cls for cls, ns in NAMESPACE.items()}


def read_log(path: str) -> list[dict]:
    """The writes of a real run, in order: successful PUT, POST and DELETE lines."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            if e.get("dry_run") or e.get("method") not in ("PUT", "POST", "DELETE"):
                continue
            if e["path"] == "/token/" or LOCK_HANDLE in e["path"] or LOCK_HANDLE in json.dumps(e.get("body") or ""):
                continue  # the login and the tree lock's own note are not changes to the tree
            if e.get("status") in (200, 201, 204):
                out.append(e)
    return out


def load_pre(path: str) -> dict[tuple[str, str], dict]:
    """The pre-run objects from a snapshot or a gwt-check dump, gzip or plain JSON."""
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("objects"), dict):
        raise ValueError(f"{path} is neither a snapshot nor a gwt-check dump")
    return pre_objects(data)


def pre_objects(snap: dict) -> dict[tuple[str, str], dict]:
    """(class, handle) -> object, from a snapshot or a gwt-check dump."""
    out = {}
    for name, rows in (snap.get("objects") or {}).items():
        cls = name if name in NAMESPACE else CLS_OF.get(name)
        if cls is None:
            continue
        for o in rows if isinstance(rows, list) else rows.values():
            out[(cls, o["handle"])] = o
    return out


def _covers(live, written) -> bool:
    """Does `live` hold everything in `written`? The server drops `_class` inside nested objects and fills in
    defaults, so dicts are compared by the keys that were sent; lists must have the same length and order."""
    if isinstance(written, dict):
        return isinstance(live, dict) and all(
            _covers(live.get(k), v) for k, v in written.items() if k != "_class")
    if isinstance(written, list):
        return isinstance(live, list) and len(live) == len(written) and all(_covers(a, b) for a, b in zip(live, written))
    return live == written


def _same(live: dict, written: dict) -> bool:
    """Does the live object still hold everything the run wrote? Top-level server fields (change, gramps_id
    on a create) are ignored."""
    return all(_covers(live.get(k), v) for k, v in written.items() if k not in IGNORE)


def _cls_of_path(path: str) -> tuple[str | None, str | None]:
    parts = [p for p in path.split("/") if p]
    if len(parts) == 2 and parts[0] in CLS_OF:
        return CLS_OF[parts[0]], parts[1]
    return None, None


def plan(entries: list[dict], pre: dict, live) -> list[dict]:
    """Steps, newest write first. Each is {action: restore|delete|skip|manual, cls, handle, why, body?}.
    `live(cls, handle)` reads the object from the server (None if gone)."""
    created: dict[tuple[str, str], dict] = {}
    last_put: dict[tuple[str, str], dict] = {}
    manual: list[dict] = []
    order: list[tuple[str, str, str]] = []  # (kind, cls, handle) in log order, first sight of each
    for e in entries:
        m, path = e["method"], e["path"]
        if m == "POST" and path == "/objects/":
            for o in e.get("body") or []:
                cls = o.get("_class")
                key = (cls, o["handle"])
                created[key] = o
                order.append(("create", *key))
        elif m == "PUT" and "/file" not in path:
            cls, h = _cls_of_path(path)
            if cls is None:
                manual.append({"action": "manual", "cls": "?", "handle": path, "why": f"PUT {path} is not an object write"})
                continue
            if (cls, h) not in last_put:
                order.append(("put", cls, h))
            last_put[(cls, h)] = e["body"]
        else:
            manual.append({"action": "manual", "cls": "?", "handle": path,
                           "why": f"{m} {path}: a merge, delete or upload is not undone from the log; restore the backup"})
    steps = []
    for kind, cls, h in reversed(order):
        if kind == "put":
            if (cls, h) in created:
                steps.append({"action": "skip", "cls": cls, "handle": h, "why": "made by this run; its delete covers it"})
                continue
            before = pre.get((cls, h))
            if before is None:
                steps.append({"action": "manual", "cls": cls, "handle": h, "why": "not in the pre-run copy; cannot restore"})
                continue
            now = live(cls, h)
            if now is None:
                steps.append({"action": "skip", "cls": cls, "handle": h, "why": "gone from the tree"})
            elif _same(now, before):
                steps.append({"action": "skip", "cls": cls, "handle": h, "why": "already as before the run"})
            elif not _same(now, last_put[(cls, h)]):
                steps.append({"action": "skip", "cls": cls, "handle": h,
                              "why": "changed since the run; not overwritten, look at it by hand"})
            else:
                body = {k: v for k, v in before.items() if k not in ("extended", "profile")}
                body.setdefault("_class", cls)
                steps.append({"action": "restore", "cls": cls, "handle": h, "body": body,
                              "why": "back to its pre-run copy"})
        else:
            if cls not in DELETABLE:
                steps.append({"action": "manual", "cls": cls, "handle": h,
                              "why": f"a {cls} made by the run; the server also edits what it links to, restore the backup"})
                continue
            now = live(cls, h)
            if now is None:
                steps.append({"action": "skip", "cls": cls, "handle": h, "why": "already gone"})
            elif not _same(now, created[(cls, h)]):
                steps.append({"action": "skip", "cls": cls, "handle": h, "why": "changed since the run; not deleted"})
            else:
                steps.append({"action": "delete", "cls": cls, "handle": h, "why": "made by the run"})
    # an object left alone because it changed since the run may still point at something the run made: keep that too
    held = [json.dumps(last_put[(s["cls"], s["handle"])]) for s in steps
            if s["action"] == "skip" and "changed since" in s["why"] and (s["cls"], s["handle"]) in last_put]
    for s in steps:
        if s["action"] == "delete" and any(s["handle"] in blob for blob in held):
            s["action"], s["why"] = "skip", "an object that changed since the run still points at it; not deleted"
    return steps + manual


def execute(client: Client, steps: list[dict], pre: dict) -> list[str]:
    """Do the restore and delete steps, then read each restored object back. Returns problems."""
    problems = []
    for s in steps:
        if s["action"] == "restore":
            r = client._request("PUT", f"/{NAMESPACE[s['cls']]}/{s['handle']}", write=True, json=s["body"])
            if r.status_code != 200:
                raise ApiError(f"restore {s['cls']} {s['handle']}: HTTP {r.status_code} {r.text[:200]}", r.status_code)
        elif s["action"] == "delete":
            client.delete(s["cls"], s["handle"])
    for s in steps:
        if s["action"] == "restore":
            now = client.get_live(s["cls"], s["handle"])
            if now is None or not _same(now, pre[(s["cls"], s["handle"])]):
                problems.append(f"{s['cls']} {s['handle']} does not read back as before the run")
        elif s["action"] == "delete" and client.get_live(s["cls"], s["handle"]) is not None:
            problems.append(f"{s['cls']} {s['handle']} is still there after the delete")
    return problems
