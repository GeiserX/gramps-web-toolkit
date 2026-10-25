"""A whole tree in memory: from a JSON dump, or (handles and links only) from a .gramps backup."""

from __future__ import annotations

import gzip
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime

# REST namespaces, in dump order. Tags are optional: older dumps do not have them.
TYPES = (
    "people",
    "families",
    "events",
    "places",
    "citations",
    "sources",
    "media",
    "notes",
    "repositories",
    "tags",
)

# Gramps XML element -> REST namespace
XML_TYPES = {
    "person": "people",
    "family": "families",
    "event": "events",
    "placeobj": "places",
    "citation": "citations",
    "source": "sources",
    "object": "media",
    "note": "notes",
    "repository": "repositories",
    "tag": "tags",
}


# a gwt-apply snapshot keeps its objects by Gramps class
APPLY_SNAPSHOT = "gwt-apply-snapshot/1"
APPLY_CLASSES = {
    "Person": "people", "Family": "families", "Event": "events", "Place": "places", "Citation": "citations",
    "Source": "sources", "Media": "media", "Note": "notes", "Repository": "repositories", "Tag": "tags",
}


# the note a running gwt-apply holds the tree with (LOCK_HANDLE in apply/gwt_apply/lock.py): not part of the tree
APPLY_LOCK = "gwt8e1481beef5aa4a5c4de4424f"


# A file without `read_started` (written before it existed, or a gwt-apply snapshot) says only when it was written
# (`dumped_at`, after a read that can take many minutes) or taken (`taken`); its read is taken to have started this
# long before that, so `check --from` reads again whatever changed in that hour.
READ_MARGIN = 3600


class BadInput(Exception):
    """A file that cannot be read as a tree: exit code 2."""


class Tree:
    def __init__(self, data: dict[str, list[dict]], *, partial: bool = False, source: str = ""):
        self.objs: dict[str, dict[str, dict]] = {}
        for t in TYPES:
            rows = data.get(t)
            if rows is None and t != "tags":
                raise BadInput(f"{source}: no {t!r} list")
            self.objs[t] = {o["handle"]: o for o in rows or [] if o["handle"] != APPLY_LOCK}
        self.has_tags = data.get("tags") is not None
        self.partial = partial  # from a backup: handles, ids, change times and family links only
        self.source = source
        self.tree_id: str | None = None  # the tree the file says it was read from
        self.read_started: int | None = None  # Unix time the read began, to compare with change stamps
        self.read_started_guessed = False  # True when worked out from dumped_at or taken minus READ_MARGIN

    def gid(self, t: str, handle: str) -> str:
        o = self.objs[t].get(handle)
        return o.get("gramps_id") or handle if o else handle

    def counts(self) -> dict[str, int]:
        return {t: len(v) for t, v in self.objs.items()}

    def by_gid(self, t: str, gramps_id: str) -> dict | None:
        return next((o for o in self.objs[t].values() if o.get("gramps_id") == gramps_id), None)

    def tagged(self, name: str) -> set[tuple[str, str]]:
        """(type, handle) of every object carrying the tag called `name`."""
        tag = next((h for h, o in self.objs["tags"].items() if o.get("name") == name), None)
        if tag is None:
            return set()
        return {
            (t, h)
            for t, objs in self.objs.items()
            for h, o in objs.items()
            if tag in (o.get("tag_list") or [])
        }


def _open(path: str):
    with open(path, "rb") as fh:
        gz = fh.read(2) == b"\x1f\x8b"
    return gzip.open(path, "rb") if gz else open(path, "rb")


def load(path: str) -> Tree:
    """A JSON dump (`gwt-check dump`, gzipped or not) or a Gramps XML backup (.gramps)."""
    try:
        with _open(path) as fh:
            head = fh.read(512).lstrip()
    except OSError as exc:
        raise BadInput(f"{path}: {exc}") from exc
    if head.startswith(b"<"):
        return load_xml(path)
    try:
        with _open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError, EOFError) as exc:
        raise BadInput(f"{path}: not a JSON dump or a .gramps backup ({exc})") from exc
    if not isinstance(data, dict):
        raise BadInput(f"{path}: a dump is a JSON object with one list per type")
    meta = data
    if data.get("format") == APPLY_SNAPSHOT:
        if not isinstance(data.get("objects"), dict) or not all(isinstance(v, list) for v in data["objects"].values()):
            raise BadInput(f"{path}: a gwt-apply snapshot without an objects map of lists")
        missing = [cls for cls in APPLY_CLASSES if cls not in data["objects"] and cls != "Tag"]
        if missing:
            raise BadInput(f"{path}: a gwt-apply snapshot without {', '.join(missing)}; take a new one")
        data = {t: data["objects"].get(cls) for cls, t in APPLY_CLASSES.items()}
    data = data.get("objects", data)
    tree = Tree(data, source=path)
    tree.tree_id = meta.get("tree")
    tree.read_started, tree.read_started_guessed = _read_started(meta)
    return tree


def _read_started(meta: dict) -> tuple[int | None, bool]:
    if isinstance(meta.get("read_started"), (int, float)):
        return int(meta["read_started"]), False
    stamp = meta.get("taken") or meta.get("dumped_at")
    try:
        # Python before 3.11 rejects a zone written +0000 (no colon), which is how a dump writes its own dumped_at
        when = datetime.fromisoformat(stamp[:-2] + ":" + stamp[-2:] if re.search(r"[+-]\d{4}$", stamp) else stamp)
        return int(when.timestamp()) - READ_MARGIN, True
    except (TypeError, ValueError):
        return None, True


def save(data: dict, path: str) -> None:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "wt", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)


def load_xml(path: str) -> Tree:
    """Enough of a Gramps XML backup for the diff and the Ahnentafel; checks need a JSON dump."""
    data: dict[str, list[dict]] = {t: [] for t in TYPES}
    h = lambda el: (el.get("hlink") or el.get("handle") or "").lstrip("_")  # noqa: E731
    try:
        with _open(path) as fh:
            for _, el in ET.iterparse(fh):
                tag = el.tag.rsplit("}", 1)[-1]
                t = XML_TYPES.get(tag)
                if t is None or not el.get("handle"):
                    continue
                kids = [(c.tag.rsplit("}", 1)[-1], c) for c in el]
                o = {
                    "handle": h(el),
                    "gramps_id": el.get("id") or "",
                    "change": int(el.get("change") or 0),
                    "tag_list": [h(c) for k, c in kids if k == "tagref"],
                }
                if t == "people":
                    o["parent_family_list"] = [h(c) for k, c in kids if k == "childof"]
                    o["family_list"] = [h(c) for k, c in kids if k == "parentin"]
                elif t == "families":
                    o["father_handle"] = next((h(c) for k, c in kids if k == "father"), "")
                    o["mother_handle"] = next((h(c) for k, c in kids if k == "mother"), "")
                    o["child_ref_list"] = [{"ref": h(c)} for k, c in kids if k == "childref"]
                elif t == "tags":
                    o["name"] = el.get("name") or ""
                data[t].append(o)
                el.clear()
    except (OSError, ET.ParseError, EOFError) as exc:
        raise BadInput(f"{path}: not a readable Gramps XML backup ({exc})") from exc
    return Tree(data, partial=True, source=path)
