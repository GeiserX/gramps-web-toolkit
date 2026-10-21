"""One --apply per tree at a time: a lock note inside the tree itself, so it holds across machines.

The lock is a private note with a fixed handle. Creating it is the test-and-set: the server refuses
a second object with the same handle. It says who holds it and until when; the holder pushes that
time forward while it works and deletes the note when it stops. A holder that died leaves a note
that expires, and the next run takes it over then (or at once with --break-lock).
"""

from __future__ import annotations

import json
import os
import socket
import time
from datetime import datetime, timezone

from .client import ApiError, Client, Retryable
from .ops import stable_handle

LOCK_HANDLE = stable_handle("gwt-apply-lock")
LOCK_ID = "GWTLOCK"


class LockHeld(Exception):
    """Another apply holds the tree."""


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def read_lock(client: Client) -> dict | None:
    note = client.get("Note", LOCK_HANDLE)
    if note is None:
        return None
    try:
        info = json.loads((note.get("text") or {}).get("string") or "{}")
    except json.JSONDecodeError:
        info = {}
    info["_note"] = note
    return info


class TreeLock:
    def __init__(self, client: Client, run_id: str, *, ttl: float = 900.0, retries: int = 5, retry_wait: float = 30.0):
        self.c = client
        self.run_id = run_id
        self.ttl = ttl
        self.expires = 0.0
        self.retries = retries
        self.retry_wait = retry_wait

    def _busy(self, fn):
        """Run fn, waiting and trying again while the server says it is busy ("database is locked")."""
        for attempt in range(1, self.retries + 1):
            try:
                return fn()
            except Retryable:
                if attempt == self.retries:
                    raise
                time.sleep(self.retry_wait)

    def _note(self) -> dict:
        info = {
            "tool": "gwt-apply",
            "run": self.run_id,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "user": self.c.username,
            "expires": _iso(self.expires),
            "expires_ts": self.expires,
        }
        return {
            "_class": "Note",
            "handle": LOCK_HANDLE,
            "gramps_id": LOCK_ID,
            "type": "General",
            "private": True,
            "format": 0,
            "text": {"_class": "StyledText", "string": json.dumps(info), "tags": []},
        }

    def acquire(self, *, break_lock: bool = False) -> str | None:
        """Take the lock. Returns a line about a lock taken over, or None. Raises LockHeld."""
        return self._busy(lambda: self._acquire(break_lock))

    def _acquire(self, break_lock: bool) -> str | None:
        taken_over = None
        held = read_lock(self.c)
        if held is not None and held.get("run") != self.run_id:
            alive = float(held.get("expires_ts") or 0) > _now()
            who = f"run {held.get('run')} on {held.get('host')} (pid {held.get('pid')}), expires {held.get('expires')}"
            if alive and not break_lock:
                raise LockHeld(f"another apply holds this tree: {who}; wait, or pass --break-lock if it is dead")
            self.c.delete("Note", LOCK_HANDLE)
            taken_over = f"took over the lock of {who}"
        self.expires = _now() + self.ttl
        try:
            self.c.create(self._note())
        except Retryable:
            raise
        except ApiError as exc:
            held = read_lock(self.c)
            raise LockHeld(f"another apply took the lock first: run {held and held.get('run')}") from exc
        mine = read_lock(self.c)
        if mine is None or mine.get("run") != self.run_id:
            raise LockHeld(f"another apply took the lock first: run {mine and mine.get('run')}")
        return taken_over

    def refresh(self) -> None:
        """Push the expiry forward when half of it is gone. Raises LockHeld if the lock is no longer ours."""
        if self.expires - _now() > self.ttl / 2:
            return
        self._busy(self._refresh)

    def _refresh(self) -> None:
        held = read_lock(self.c)
        if held is None or held.get("run") != self.run_id:
            raise LockHeld(f"lost the lock (now held by run {held and held.get('run')}); stopping")
        self.expires = _now() + self.ttl
        note = held["_note"]
        note["text"] = self._note()["text"]
        note.setdefault("_class", "Note")
        self.c.update(note)

    def release(self) -> None:
        def go():
            held = read_lock(self.c)
            if held is not None and held.get("run") == self.run_id:
                self.c.delete("Note", LOCK_HANDLE)

        self._busy(go)
