"""A snapshot of a whole tree, so a dry run needs no server and an apply reads nothing it already has.

    gwt-apply --save-snapshot tree.json.gz              # read every object once, a few minutes
    gwt-apply changes.jsonl --snapshot tree.json.gz     # dry run, offline, seconds
    gwt-apply changes.jsonl --snapshot tree.json.gz --apply
    gwt-apply --save-snapshot after.json.gz --since tree.json.gz   # only what changed since, seconds

Before an apply on a snapshot writes anything, it plans the whole list offline and asks the server
for the `change` stamp of every object of every class the plan looked at. It refuses when an object
the plan read or would write changed, appeared or went away since the snapshot, or when a class the
plan searched (names, places, sources, citations, media) has objects added or removed since then.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone

from .client import NAMESPACE, ApiError, Client
from .lock import LOCK_HANDLE

FORMAT = "gwt-apply-snapshot/1"
PAGE = 500
CHUNK = 100  # handles per read of changed objects


def save(client: Client, path: str) -> dict[str, int]:
    """Read every object of the tree, page by page, into a new gzip file. Returns counts per class."""
    objects: dict[str, list[dict]] = {}
    for cls, ns in NAMESPACE.items():
        rows: list[dict] = []
        page = 1
        while True:
            r = client._request("GET", f"/{ns}/", params={"page": page, "pagesize": PAGE})
            if r.status_code != 200:
                raise ApiError(f"GET {cls} page {page}: HTTP {r.status_code}", r.status_code)
            batch = r.json()
            rows += batch
            if len(batch) < PAGE:
                break
            page += 1
        objects[cls] = rows
    _write(path, client.tree, datetime.now(timezone.utc), objects)
    return {cls: len(rows) for cls, rows in objects.items()}


def refresh(client: Client, old: dict, path: str) -> dict[str, tuple[int, int, int]]:
    """A new snapshot from an older one of the same tree, reading only what changed since: one list of
    `change` stamps per class, then every object whose stamp moved or that is new. The result holds what a
    full `save` would. Returns, per class, (objects, objects read, objects deleted since)."""
    if old.get("tree") != client.tree:
        raise ValueError(f"the snapshot is of tree {old.get('tree')!r}, the login is in {client.tree!r}")
    taken = datetime.now(timezone.utc)  # before the first read: anything changed later is read or caught later
    objects: dict[str, list[dict]] = {}
    counts = {}
    for cls, ns in NAMESPACE.items():
        r = client._request("GET", f"/{ns}/", params={"keys": "handle,change"})
        if r.status_code != 200:
            raise ApiError(f"GET {cls} list: HTTP {r.status_code}", r.status_code)
        now = {row["handle"]: row.get("change") for row in r.json()}
        then = {o["handle"]: o for o in old["objects"].get(cls, [])}
        stale = [h for h, change in now.items() if h not in then or then[h].get("change") != change]
        fresh: dict[str, dict] = {}
        for i in range(0, len(stale), CHUNK):
            r = client._request("GET", f"/{ns}/", params={"handles": ",".join(stale[i:i + CHUNK])})
            if r.status_code != 200:
                raise ApiError(f"GET {cls} by handles: HTTP {r.status_code}", r.status_code)
            fresh.update((o["handle"], o) for o in r.json())
        # a stale object the second read did not return was deleted in between
        objects[cls] = [fresh[h] if h in fresh else then[h] for h in now if h in fresh or h not in stale]
        counts[cls] = (len(objects[cls]), len(fresh), len(set(then) - set(now)))
    _write(path, client.tree, taken, objects)
    return counts


def _write(path: str, tree: str | None, taken: datetime, objects: dict[str, list[dict]]) -> None:
    data = {"format": FORMAT, "tree": tree, "taken": taken.isoformat(timespec="seconds"), "objects": objects}
    with gzip.open(path, "xt", encoding="utf-8") as fh:  # never over another snapshot
        json.dump(data, fh, ensure_ascii=False)


def load(path: str) -> dict:
    """A snapshot, or a `gwt-check` dump (`gwt-check dump`, `check --save-dump`), gzip or plain JSON.

    A dump holds the same objects, as the API returns them, under the API's type names; it becomes a snapshot
    taken when the dump was written."""
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        data = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
    except (OSError, EOFError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path} is neither a gwt-apply snapshot nor a gwt-check dump ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} is neither a gwt-apply snapshot nor a gwt-check dump")
    if data.get("format") == FORMAT:
        return data
    if "format" not in data and isinstance(data.get("objects"), dict):
        return _from_dump(path, data)
    raise ValueError(f"{path} is neither a gwt-apply snapshot nor a gwt-check dump")


def _from_dump(path: str, dump: dict) -> dict:
    objects = dump["objects"]
    missing = [ns for ns in NAMESPACE.values() if ns != "tags" and not isinstance(objects.get(ns), list)]
    if missing:
        raise ValueError(f"{path}: a gwt-check dump without {', '.join(missing)}; take a new one")
    if not dump.get("tree"):
        raise ValueError(f"{path}: a gwt-check dump that records no tree; take a new one")
    return {"format": FORMAT, "tree": dump["tree"], "taken": _dump_time(dump),
            "objects": {cls: objects.get(ns) or [] for cls, ns in NAMESPACE.items()}}


def _dump_time(dump: dict) -> str:
    """When the dump was read, for the header line only: read_started, the moment gwt-check began reading, as
    gwt-check itself takes it; else dumped_at; else unknown."""
    started = dump.get("read_started")
    if isinstance(started, (int, float)) and not isinstance(started, bool):
        return datetime.fromtimestamp(started, timezone.utc).isoformat(timespec="seconds")
    try:
        return datetime.fromisoformat(dump["dumped_at"]).isoformat(timespec="seconds")
    except (KeyError, TypeError, ValueError):
        return "unknown"


def check(client: Client, snap: dict, planner: Client) -> list[str]:
    """What changed on the server since the snapshot, among what the plan made by `planner` relies on."""
    objects = snap["objects"]
    touched: dict[str, set[str]] = {}
    for cls, h in planner.reads | set(planner.pending):
        touched.setdefault(cls, set()).add(h)
    problems = []
    for cls in sorted(set(touched) | planner.listed):
        r = client._request("GET", f"/{NAMESPACE[cls]}/", params={"keys": "handle,change,gramps_id"})
        if r.status_code != 200:
            raise ApiError(f"GET {cls} list: HTTP {r.status_code}", r.status_code)
        # the apply's own lock note is not a change to the tree
        now = {row["handle"]: row for row in r.json() if row["handle"] != LOCK_HANDLE}
        then = {o["handle"]: o for o in objects.get(cls, []) if o["handle"] != LOCK_HANDLE}
        for h in sorted(touched.get(cls, ())):
            a, b = then.get(h), now.get(h)
            label = f"{cls} {(b or a or {}).get('gramps_id') or h}"
            if a is None and b is not None:
                problems.append(f"{label} was made after the snapshot")
            elif a is not None and b is None:
                problems.append(f"{label} was deleted after the snapshot")
            elif a is not None and a.get("change") != b.get("change"):
                problems.append(f"{label} changed after the snapshot")
        if cls in planner.listed:
            added = set(now) - set(then)
            gone = set(then) - set(now)
            if added or gone:
                problems.append(
                    f"{len(added)} {cls} object(s) added and {len(gone)} deleted after the snapshot, "
                    f"and the plan searched every {cls}"
                )
    return problems
