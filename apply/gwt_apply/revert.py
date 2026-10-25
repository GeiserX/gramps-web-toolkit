"""Undo an apply run from its log and the tree as it was before.

    gwt-apply --revert gwt-apply-<run id>.jsonl --pre before.json.gz            # plan, writes nothing
    gwt-apply --revert gwt-apply-<run id>.jsonl --pre before.json.gz --apply    # do it, then read back

`--pre` is a snapshot (`--save-snapshot`) or a `gwt-check dump` of the same tree, taken before the run; one
taken after the run's first write, or of another tree, is refused. The log says what the run wrote, including
writes whose answer was lost or came back 5xx (the server may still have done them); the pre-run copy says
what each object was. Newest write first:

- an object the run changed (PUT) goes back to its pre-run copy, once, whatever the number of writes;
- a note, citation, event, place, source, repository or tag the run made (POST) is deleted, after every
  restore, and only while nothing points at it any more (the server's backlinks: a later run that reused the
  tag or attached the note keeps it);
- nothing is touched that is no longer what the run left: a person someone edited since the run is reported
  and skipped, never overwritten. With --apply the plan is made under the tree lock, and each object is read
  again right before it is written.

Not undone, reported for a backup restore: people and families the run made (the server also edits the
objects around them), merges, deletes and media uploads.
"""

from __future__ import annotations

import gzip
import json
import time
from datetime import datetime, timezone

from .client import NAMESPACE, Client, Retryable
from .lock import LOCK_HANDLE

IGNORE = {"change", "extended", "profile", "backlinks", "_read", "_snap", "_class", "gramps_id"}
DELETABLE = {"Note", "Citation", "Event", "Place", "Source", "Repository", "Tag"}
CLS_OF = {ns: cls for cls, ns in NAMESPACE.items()}
CLS_OF_LINK = {cls.lower(): cls for cls in NAMESPACE}  # the server groups backlinks by lower-case class


def read_log(path: str) -> list[dict]:
    """The writes of a real run, in order: PUT, POST and DELETE lines that succeeded, and those whose answer was
    lost (status null) or came back 5xx, because the server may have done them anyway."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            if e.get("dry_run") or e.get("method") not in ("PUT", "POST", "DELETE"):
                continue
            if e["path"].startswith("/token/") or LOCK_HANDLE in e["path"] or LOCK_HANDLE in json.dumps(e.get("body") or ""):
                continue  # the login and the tree lock's own note are not changes to the tree
            status = e.get("status")
            if status in (200, 201, 204) or status is None or status >= 500:
                out.append(e)
    return out


def read_pre(path: str) -> dict:
    """The pre-run copy as it is on disk: a snapshot or a gwt-check dump, gzip or plain JSON."""
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("objects"), dict):
        raise ValueError(f"{path} is neither a snapshot nor a gwt-check dump")
    return data


def load_pre(path: str) -> dict[tuple[str, str], dict]:
    """The pre-run objects from a snapshot or a gwt-check dump, gzip or plain JSON."""
    return pre_objects(read_pre(path))


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


def _when(stamp: str) -> datetime:
    t = datetime.fromisoformat(stamp)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _created(entries: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """(class, handle) -> every body the run POSTed for it."""
    out: dict[tuple[str, str], list[dict]] = {}
    for e in entries:
        if e["method"] == "POST" and e["path"] == "/objects/":
            for o in e.get("body") or []:
                out.setdefault((o.get("_class"), o["handle"]), []).append(o)
    return out


def pre_problems(snap: dict, tree: str | None, entries: list[dict]) -> list[str]:
    """Why `snap` cannot be the tree as it was right before the run that wrote `entries`, if it cannot."""
    problems = []
    if snap.get("tree") != tree:
        problems.append(f"--pre is of tree {snap.get('tree')!r}, the login is in {tree!r}")
    stamp = snap.get("taken") or snap.get("dumped_at")
    first = next((e.get("ts") for e in entries if e.get("ts")), None)
    if not stamp:
        problems.append("--pre says nowhere when it was taken (no 'taken' or 'dumped_at')")
    elif entries and first is None:
        problems.append("the log has no time stamps, so --pre cannot be checked against it")
    elif first is not None:
        try:
            late = _when(stamp) > _when(first)
        except ValueError:
            late = None
        if late is None:
            problems.append(f"cannot read the time --pre was taken ({stamp!r}) or the log's ({first!r})")
        elif late:
            problems.append(f"--pre was taken at {stamp}, after the run's first write at {first}: "
                            "it is not the tree as it was before the run")
    pre = pre_objects(snap)
    made = [k for k in _created(entries) if k in pre]
    if made:
        cls, h = made[0]
        problems.append(f"--pre already holds {len(made)} object(s) the run made (e.g. {cls} {h}): "
                        "it was taken after the run")
    return problems


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


def _any_same(live: dict, bodies: list[dict]) -> bool:
    return any(_same(live, b) for b in bodies)


def _cls_of_path(path: str) -> tuple[str | None, str | None]:
    parts = [p for p in path.split("/") if p]
    if len(parts) == 2 and parts[0] in CLS_OF:
        return CLS_OF[parts[0]], parts[1]
    return None, None


def _referrers(links: dict | None) -> set[tuple[str, str]] | None:
    """The (class, handle) of every object that points at one, from the server's backlinks; None if unknown."""
    if not isinstance(links, dict):
        return None
    return {(CLS_OF_LINK.get(k, k), h) for k, hs in links.items() for h in hs or []}


def _held_by(refs: set[tuple[str, str]]) -> str:
    shown = ", ".join(f"{c} {h}" for c, h in sorted(refs)[:3])
    return f"still referenced by {shown}" + (f" and {len(refs) - 3} more" if len(refs) > 3 else "") + "; not deleted"


def plan(entries: list[dict], pre: dict, live, links=None) -> list[dict]:
    """Steps, newest write first. Each is {action: restore|delete|skip|manual, cls, handle, why, body?}.
    `live(cls, handle)` reads the object from the server (None if gone). `links(cls, handle)` gives its
    backlinks ({class: [handles]}, None if the server does not say): a delete is planned only once nothing
    but objects this revert restores or deletes points at it."""
    created = _created(entries)
    puts: dict[tuple[str, str], list[dict]] = {}
    blind: set[tuple[str, str]] = set()  # PUTs whose answer was lost and whose log line has no body
    manual: list[dict] = []
    order: list[tuple[str, str, str]] = []  # (kind, cls, handle) in log order, first sight of each
    seen: set[tuple[str, str, str]] = set()
    for e in entries:
        m, path = e["method"], e["path"]
        if m == "POST" and path == "/objects/":
            if not e.get("body"):
                manual.append({"action": "manual", "cls": "?", "handle": path,
                               "why": "a create whose answer was lost, with no body in the log; look for it by hand"})
            for o in e.get("body") or []:
                key = (o.get("_class"), o["handle"])
                if ("create", *key) not in seen:
                    seen.add(("create", *key))
                    order.append(("create", *key))
        elif m == "PUT" and "/file" not in path:
            cls, h = _cls_of_path(path)
            if cls is None:
                manual.append({"action": "manual", "cls": "?", "handle": path, "why": f"PUT {path} is not an object write"})
                continue
            if (cls, h) not in puts:
                order.append(("put", cls, h))
                puts[(cls, h)] = []
            if "body" in e:
                puts[(cls, h)].append(e["body"])
            else:
                blind.add((cls, h))
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
            written = puts[(cls, h)]
            if now is None:
                steps.append({"action": "skip", "cls": cls, "handle": h, "why": "gone from the tree"})
            elif _same(now, before):
                steps.append({"action": "skip", "cls": cls, "handle": h, "why": "already as before the run"})
            elif not written:
                steps.append({"action": "manual", "cls": cls, "handle": h,
                              "why": "a write whose answer was lost and whose log line has no body; compare by hand"})
            elif "change" in written[0] and "change" in before and written[0]["change"] != before["change"]:
                # the run's first PUT carries the stamp of what it read: the pre-run copy is older than that
                steps.append({"action": "manual", "cls": cls, "handle": h,
                              "why": "the pre-run copy is older than what the run read; restoring it would also undo "
                                     "edits made in between, compare by hand"})
            elif not _any_same(now, written):
                steps.append({"action": "skip", "cls": cls, "handle": h, "changed": True,
                              "why": "changed since the run; not overwritten, look at it by hand"})
            else:
                body = {k: v for k, v in before.items() if k not in ("extended", "profile", "backlinks")}
                body.setdefault("_class", cls)
                steps.append({"action": "restore", "cls": cls, "handle": h, "body": body, "written": written,
                              "read": now.get("_read"), "why": "back to its pre-run copy"})
        else:
            if cls not in DELETABLE:
                steps.append({"action": "manual", "cls": cls, "handle": h,
                              "why": f"a {cls} made by the run; the server also edits what it links to, restore the backup"})
                continue
            now = live(cls, h)
            written = created[(cls, h)] + puts.get((cls, h), [])  # a later op of the run may have updated it
            if now is None:
                steps.append({"action": "skip", "cls": cls, "handle": h, "why": "already gone"})
            elif not _any_same(now, written):
                steps.append({"action": "skip", "cls": cls, "handle": h, "changed": True,
                              "why": "changed since the run; not deleted"})
            else:
                steps.append({"action": "delete", "cls": cls, "handle": h, "written": written,
                              "why": "made by the run"})
    # an object left alone because it changed since the run may still point at something the run made: keep that too
    held = [json.dumps(puts[(s["cls"], s["handle"])]) for s in steps
            if s["action"] == "skip" and s.get("changed") and (s["cls"], s["handle"]) in puts]
    for s in steps:
        if s["action"] == "delete" and any(s["handle"] in blob for blob in held):
            s["action"], s["why"] = "skip", "an object that changed since the run still points at it; not deleted"
    if links is not None:
        _gate_deletes(steps, links)
    return steps + manual


def _gate_deletes(steps: list[dict], links) -> None:
    """Keep every planned delete whose object something outside this revert still points at. Restored objects
    go back to a copy from before the object existed, so they stop pointing at it; deletes go in an order where
    each object has no referrer left when its turn comes (what `execute` does too)."""
    restored = {(s["cls"], s["handle"]) for s in steps if s["action"] == "restore"}
    todo = {(s["cls"], s["handle"]): s for s in steps if s["action"] == "delete"}
    refs = {}
    for key, s in todo.items():
        refs[key] = _referrers(links(*key))
        if refs[key] is None:
            s["action"], s["why"] = "skip", "the server did not say what points at it; not deleted"
    pending = {k for k in todo if refs[k] is not None}
    gone: set[tuple[str, str]] = set()
    moved = True
    while moved:
        moved = False
        for key in sorted(pending):
            if not refs[key] - restored - gone - {key}:
                gone.add(key)
                pending.discard(key)
                moved = True
    for key in pending:
        todo[key]["action"], todo[key]["why"] = "skip", _held_by(refs[key] - restored - gone - {key})


def _busy(fn, retries: int, wait: float):
    """Run fn, waiting and trying again while the server says it is busy, like the lock does."""
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Retryable:
            if attempt == retries:
                raise
            time.sleep(wait)


def execute(client: Client, steps: list[dict], pre: dict, *, lock=None, retries: int = 5,
            retry_wait: float = 30.0) -> list[str]:
    """Do the restore steps, then the deletes, then read each object back. Returns problems.

    Every step reads its object again right before it writes: a restore whose object changed since the plan,
    and a delete whose object changed or is still pointed at, become `skip` steps (the step dict is updated in
    place) and nothing is overwritten. A busy answer is tried again from that fresh read, so a write that
    landed despite its answer reads as done. A step that stops the run raises; rerunning is safe."""
    def refresh():
        if lock is not None:
            lock.refresh()

    def restore(s):
        key = (s["cls"], s["handle"])
        now = client.get_live(*key)
        if now is not None and _same(now, pre[key]):
            return  # done, by this step's own earlier try or by someone else
        if now is None:
            s["action"], s["why"] = "skip", "gone from the tree since the plan"
            return
        if (now.get("_read") != s["read"]) if s.get("read") is not None else not _any_same(now, s["written"]):
            s["action"], s["why"], s["changed"] = "skip", "changed since the plan; not overwritten", True
            return
        client.update(dict(s["body"]))

    for s in steps:
        if s["action"] == "restore":
            refresh()
            _busy(lambda: restore(s), retries, retry_wait)

    pending = [s for s in steps if s["action"] == "delete"]
    blocked: dict[int, set] = {}
    while pending:
        moved = False
        for s in list(pending):
            refresh()
            now = _busy(lambda: client.get_live(s["cls"], s["handle"], backlinks=True), retries, retry_wait)
            refs = None if now is None else _referrers(now.get("backlinks"))
            if now is None:
                pass  # already gone
            elif not _any_same(now, s["written"]):
                s["action"], s["why"], s["changed"] = "skip", "changed since the plan; not deleted", True
            elif refs is None:
                s["action"], s["why"] = "skip", "the server did not say what points at it; not deleted"
            elif refs - {(s["cls"], s["handle"])}:
                blocked[id(s)] = refs
                continue  # maybe a later delete frees it
            else:
                _busy(lambda: client.delete(s["cls"], s["handle"]), retries, retry_wait)
            pending.remove(s)
            moved = True
        if not moved:
            for s in pending:
                s["action"], s["why"] = "skip", _held_by(blocked[id(s)] - {(s["cls"], s["handle"])})
            break

    problems = []
    for s in steps:
        if s["action"] == "restore":
            now = client.get_live(s["cls"], s["handle"])
            if now is None or not _same(now, pre[(s["cls"], s["handle"])]):
                problems.append(f"{s['cls']} {s['handle']} does not read back as before the run")
        elif s["action"] == "delete" and client.get_live(s["cls"], s["handle"]) is not None:
            problems.append(f"{s['cls']} {s['handle']} is still there after the delete")
    return problems
